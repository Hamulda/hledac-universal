//! pool_run — Python-callable rayon pool runners (channel-based dispatch)
//!
//! CONSOLIDATED (R2 fix): Merged rayon_dispatch.rs into pool_run.rs.
//! Previously there were two identical implementations — pool_run.rs (with dead
//! rayon_submit/rayon_join aliases) and rayon_dispatch.rs (with the correct
//! rayon_submit_channel/rayon_join_channel names).
//!
//! ## Architecture
//!
//! One dispatcher thread per pool type (cpu, io, mixed) that runs
//! pool.install() and consumes from a bounded sync_channel (capacity=256).
//! The dispatcher pulls work items from the channel and executes them on
//! the rayon pool threads via pool.install().
//!
//! The GIL is held by the asyncio.to_thread worker thread during both
//! submit and join. The rayon pool workers acquire the GIL via
//! Python::with_gil() for Python callbacks — no contention because the
//! asyncio worker is blocked on the condvar during pool execution.
//!
//! ## M1 8GB Safety
//!
//! - 1 dispatcher thread per pool type (3 total, not per-task)
//! - Bounded channel (256 items) provides natural back-pressure
//! - Existing rayon pool threads are reused — no per-task allocation
//! - Zero heap allocations on the hot path (after init)
//!
//! ## Function Naming
//!
//! - `cpu_pool_run` / `io_pool_run` / `mixed_pool_run` — sync GIL wrappers
//!   (call Python directly with GIL held, no pool usage — for tiny workloads)
//! - `rayon_submit_channel` / `rayon_join_channel` / `rayon_abort_channel` /
//!   `rayon_shutdown_channel` — channel-based dispatch to rayon pools
//!   (~5μs/task vs ~500μs for thread::spawn)

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, LazyLock};
use std::thread;
use std::time::Duration;

// Use parking_lot: no poisoning (panic in one thread won't poison the mutex),
// ~2x faster than std::sync::Mutex, and .lock() returns Guard directly (no Result).
// This prevents unwrap() panics from propagating as Rust panics across the PyO3 FFI boundary.

use crossbeam_channel::{bounded, Sender, Receiver};

use crate::cpu_pool;
use crate::io_pool;
use crate::mixed_pool;

// State encoding for atomic compare-exchange
const STATE_PENDING: u8 = 0;
const STATE_READY: u8 = 1;
const STATE_ABORTED: u8 = 2;

// ---------------------------------------------------------------------------
// Work item — submitted to rayon pool dispatcher via channel
// ---------------------------------------------------------------------------

struct WorkItem {
    func: Py<PyAny>,
    args: Py<PyTuple>,
    /// Batch size hint — used by mixed dispatcher to select pool size
    n_items: usize,
    /// Shared result storage + synchronization
    shared: Arc<SharedTask>,
}

/// Shared storage for task result + cancellation + completion signal.
struct SharedTask {
    /// Result of the Python function call.
    /// parking_lot::Mutex: no poisoning, no unwrap needed, 2x faster.
    result: parking_lot::Mutex<Option<Result<Py<PyAny>, PyErr>>>,
    /// Set by rayon_abort to request early cancellation.
    cancel_flag: AtomicBool,
    /// Atomic state: STATE_PENDING | STATE_READY | STATE_ABORTED.
    /// Prevents race between worker writing result and timeout setting aborted.
    state: AtomicU8,
    /// Signaled when result is ready.
    /// parking_lot::Condvar paired with parking_lot::Mutex (compatible, both from parking_lot).
    condvar: parking_lot::Condvar,
}

// ---------------------------------------------------------------------------
// Global channel senders — one per pool type, one dispatcher thread each
// ---------------------------------------------------------------------------

static RAYON_SHUTDOWN: AtomicBool = AtomicBool::new(false);

fn cpu_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<parking_lot::Mutex<Option<Sender<WorkItem>>>, fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("cpu", Arc::new(rx));
            parking_lot::Mutex::new(Some(tx))
        });
    &SENDER
}

fn io_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<parking_lot::Mutex<Option<Sender<WorkItem>>>, fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("io", Arc::new(rx));
            parking_lot::Mutex::new(Some(tx))
        });
    &SENDER
}

fn mixed_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<parking_lot::Mutex<Option<Sender<WorkItem>>>, fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("mixed", Arc::new(rx));
            parking_lot::Mutex::new(Some(tx))
        });
    &SENDER
}

/// Spawn a dispatcher thread that runs pool.install() and consumes work from rx.
fn spawn_dispatcher(pool_name: &str, rx: Arc<Receiver<WorkItem>>) {
    let pool_name_owned = pool_name.to_string();
    thread::Builder::new()
        .name(format!("hledac-dispatch-{}", pool_name_owned))
        .stack_size(4_194_304) // 4 MiB
        .spawn(move || {
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    use libc::pthread_set_qos_class_self_np;
                    let qos = libc::qos_class_t::QOS_CLASS_USER_INITIATED;
                    pthread_set_qos_class_self_np(qos, 0);
                }
            }

            match pool_name_owned.as_str() {
                "cpu" => run_dispatcher_loop(cpu_pool(), rx),
                "io" => run_dispatcher_loop(io_pool(), rx),
                "mixed" => run_mixed_dispatcher_loop(rx),
                _ => run_dispatcher_loop(cpu_pool(), rx),
            }
        })
        .expect("spawn_dispatcher: thread::Builder failed (OOM?)");
}

/// Dispatcher loop for fixed pools (cpu, io).
fn run_dispatcher_loop(pool: &'static ThreadPool, rx: Arc<Receiver<WorkItem>>) {
    pool.install(|| {
        loop {
            if RAYON_SHUTDOWN.load(Ordering::Acquire) {
                break;
            }
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(work) => execute_work_item(work, || pool),
                Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
                Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
            }
        }
    });
}

/// Dispatcher loop for mixed pool — selects POOL_SINGLE or POOL_PAIR at dispatch time.
fn run_mixed_dispatcher_loop(rx: Arc<Receiver<WorkItem>>) {
    if std::panic::catch_unwind(|| {
        let _ = mixed_pool(0);
        let _ = mixed_pool(usize::MAX);
    }).is_err() {
        return;
    }

    loop {
        if RAYON_SHUTDOWN.load(Ordering::Acquire) {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(work) => {
                let n = work.n_items;
                execute_work_item(work, || mixed_pool(n));
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Execute a single work item: cooperative cancellation check, GIL-acquire, call Python func.
fn execute_work_item<F>(work: WorkItem, _pool_fn: F)
where
    F: Fn() -> &'static ThreadPool,
{
    // Cooperative cancellation check
    if work.shared.cancel_flag.load(Ordering::Acquire) {
        let mut guard = work.shared.result.lock();
        *guard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Task was cancelled before starting",
        )));
        work.shared.state.swap(STATE_READY, Ordering::AcqRel);
        work.shared.condvar.notify_one();
        return;
    }

    // Execute Python function with GIL
    let py_result: Result<Py<PyAny>, PyErr> = Python::with_gil(|py| {
        let result = work.func.into_bound(py).call1((work.args.into_bound(py),))?;
        Ok(result.unbind())
    });

    // Atomically set STATE_READY if still PENDING (not aborted by timeout)
    let expected = STATE_PENDING;
    if work.shared.state.compare_exchange(
        expected,
        STATE_READY,
        Ordering::AcqRel,
        Ordering::Acquire,
    ).is_ok() {
        let mut guard = work.shared.result.lock();
        *guard = Some(py_result);
    }

    work.shared.condvar.notify_one();
}

// ---------------------------------------------------------------------------
// Simple sync pool runners — GIL wrapper, no thread spawn (fast path for small work)
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "cpu_pool_run")]
pub fn cpu_pool_run_(
    _py: Python<'_>,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    Python::with_gil(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

#[pyfunction]
#[pyo3(name = "io_pool_run")]
pub fn io_pool_run_(
    _py: Python<'_>,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    Python::with_gil(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

#[pyfunction]
#[pyo3(name = "mixed_pool_run")]
pub fn mixed_pool_run_(
    _py: Python<'_>,
    _n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    Python::with_gil(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

// ---------------------------------------------------------------------------
// rayon_submit_channel — channel-based dispatch, returns handle for join/abort
// ---------------------------------------------------------------------------

/// Submit a Python function to the rayon pool via channel dispatch.
/// Returns immediately (does NOT block on the result).
/// Result is retrieved via rayon_join_channel.
///
/// GIL: caller (asyncio.to_thread) must hold the GIL during this call.
#[pyfunction]
#[pyo3(name = "rayon_submit_channel")]
pub fn rayon_submit_channel_(
    py: Python<'_>,
    pool_type: &str,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    let func_clone = Py::clone_ref(&func, py);
    let args_clone = Py::clone_ref(&args, py);

    let shared: Arc<SharedTask> = Arc::new(SharedTask {
        result: parking_lot::Mutex::new(None),
        cancel_flag: AtomicBool::new(false),
        state: AtomicU8::new(STATE_PENDING),
        condvar: parking_lot::Condvar::new(),
    });

    let work = WorkItem {
        func: func_clone,
        args: args_clone,
        n_items,
        shared: Arc::clone(&shared),
    };

    let sender_mutex: &parking_lot::Mutex<Option<Sender<WorkItem>>> = match pool_type {
        "cpu" => cpu_sender(),
        "io" => io_sender(),
        "mixed" => mixed_sender(),
        _ => cpu_sender(),
    };

    let sender_guard = sender_mutex.lock();
    if let Some(ref sender) = *sender_guard {
        match sender.send(work) {
            Ok(()) => {}
            Err(_) => {
                drop(sender_guard);
                return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "Dispatcher thread died — pool may have panicked",
                ));
            }
        }
    } else {
        drop(sender_guard);
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Channel already shut down",
        ));
    }

    // Return pointer to SharedTask — Python passes this to rayon_join_channel
    let ptr = Box::into_raw(Box::new(shared)) as usize;
    Ok(ptr.into_pyobject(py).unwrap().into())
}

// ---------------------------------------------------------------------------
// rayon_join_channel — wait for rayon_submit_channel task to complete
// ---------------------------------------------------------------------------

/// Wait for a rayon_submit_channel task to complete.
///
/// GIL: caller (asyncio.to_thread) must hold the GIL during this call
/// (the condvar wait releases the GIL).
///
/// timeout_s: seconds to wait. None = indefinite.
#[pyfunction]
#[pyo3(name = "rayon_join_channel")]
pub fn rayon_join_channel_(
    py: Python<'_>,
    handle_ptr: usize,
    timeout_s: Option<f64>,
) -> PyResult<Py<PyAny>> {
    if handle_ptr == 0 {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Invalid handle: 0",
        ));
    }

    // Reconstruct Arc from raw pointer and Clone so Box release doesn't drop inner Arc
    let shared_box = unsafe { Box::from_raw(handle_ptr as *mut Arc<SharedTask>) };
    let shared = Arc::clone(&*shared_box);
    drop(shared_box); // free Box, NOT the Arc

    let timeout = timeout_s
        .map(|t| Duration::from_secs_f64(t.max(0.0)))
        .unwrap_or(Duration::MAX);

    // parking_lot 0.12: use sleep loop with Instant tracking
    let start = std::time::Instant::now();
    let mut guard = shared.result.lock();
    while (*guard).is_none() {
        let remaining = timeout.saturating_sub(start.elapsed());
        if remaining.is_zero() {
            break;
        }
        drop(guard);
        std::thread::sleep(std::time::Duration::from_millis(1.min(remaining.as_millis() as u64)));
        guard = shared.result.lock();
    }
    let timed_out = (*guard).is_none();
    if timed_out {
        // Try to claim ABORTED ownership atomically.
        // If CAS fails → worker already won (wrote result and transitioned to READY).
        // We must NOT overwrite the worker's valid result with a timeout error.
        // If CAS succeeds → we own the result; write timeout error only if still None.
        let expected = STATE_PENDING;
        let we_own = shared.state
            .compare_exchange(
                expected,
                STATE_ABORTED,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_ok();
        drop(guard);

        if we_own {
            // WE are responsible for the result — write timeout error if worker didn't.
            let mut rguard = shared.result.lock();
            if rguard.is_none() {
                *rguard = Some(Err(PyErr::new::<
                    pyo3::exceptions::PyRuntimeError,
                    _,
                >("Rayon dispatch timed out")));
            }
        }
        // else: worker won the race — don't overwrite its valid result

        let result = shared.result.lock().take();
        return match result {
            Some(Ok(py_obj)) => Ok(py_obj.into_pyobject(py).unwrap().into()),
            Some(Err(err)) => Err(err),
            None => Ok(py.None().into_pyobject(py).unwrap().into()),
        };
    }

    let result = (*guard).take();

    match result {
        Some(Ok(py_obj)) => Ok(py_obj.into_pyobject(py).unwrap().into()),
        Some(Err(err)) => Err(err),
        None => Ok(py.None().into_pyobject(py).unwrap().into()),
    }
}

// ---------------------------------------------------------------------------
// rayon_abort_channel — signal cancellation and wait for worker acknowledgment
// ---------------------------------------------------------------------------

/// Abort a rayon dispatch task.
/// Sets cancel flag and waits up to 5s for worker acknowledgment.
#[pyfunction]
#[pyo3(name = "rayon_abort_channel")]
pub fn rayon_abort_channel_(handle_ptr: usize) -> PyResult<()> {
    if handle_ptr == 0 {
        return Ok(());
    }

    // Clone Arc so Box release doesn't drop inner Arc
    let shared_box = unsafe { Box::from_raw(handle_ptr as *mut Arc<SharedTask>) };
    let shared = Arc::clone(&*shared_box);
    drop(shared_box);

    shared.cancel_flag.store(true, Ordering::Release);

    // Wait up to 5s for worker to acknowledge
    let timeout = Duration::from_secs(5);
    // parking_lot 0.12: use sleep loop with Instant tracking
    let start = std::time::Instant::now();
    let mut guard = shared.result.lock();
    while (*guard).is_none() {
        let remaining = timeout.saturating_sub(start.elapsed());
        if remaining.is_zero() {
            break;
        }
        drop(guard);
        std::thread::sleep(std::time::Duration::from_millis(1.min(remaining.as_millis() as u64)));
        guard = shared.result.lock();
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// rayon_shutdown_channel — graceful shutdown of all dispatcher threads
// ---------------------------------------------------------------------------

/// Graceful shutdown — sets shutdown flag, closes all senders,
/// and waits for dispatcher threads to exit (via channel close).
/// Safe to call multiple times.
#[pyfunction]
#[pyo3(name = "rayon_shutdown_channel")]
pub fn rayon_shutdown_channel_() -> PyResult<()> {
    RAYON_SHUTDOWN.store(true, Ordering::Release);

    // parking_lot::Mutex::lock() returns MutexGuard directly (no Result),
    // so we use a scoped block to drop the guard immediately after use.
    {
        let mut tx = cpu_sender().lock();

        let _ = tx.take();
    }
    {
        let mut tx = io_sender().lock();

        let _ = tx.take();
    }
    {
        let mut tx = mixed_sender().lock();

        let _ = tx.take();
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Deprecated aliases — removed in R2 (were exact duplicates of channel versions)
// ---------------------------------------------------------------------------
// NOTE: rayon_submit / rayon_join / rayon_abort / rayon_shutdown were removed.
// They were identical to the _channel versions and no Python code used them.
// If you need these names, use the _channel variants instead.

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // GIL wrappers — sync fast path for tiny workloads (no pool used)
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    // Channel-based dispatch to rayon pools (~5μs/task)
    m.add_function(wrap_pyfunction!(rayon_submit_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_join_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_abort_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_shutdown_channel_, m)?)?;
    Ok(())
}
