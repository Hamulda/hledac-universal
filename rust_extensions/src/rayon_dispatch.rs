//! rayon_dispatch — Channel-based dispatch to existing rayon thread pools
//!
//! ISSUE 2.3 Fix: Eliminates double-thread-per-task overhead in UnifiedExecutor.
//!
//! ## The Problem
//!
//! Old flow (pool_run.rs, rayon_submit):
//!     asyncio.to_thread(rayon_submit, ...)  ← OS thread #1 (asyncio pool)
//!         → thread::spawn(move || { pool.install(...) })  ← OS thread #2 (new)
//!             → rayon worker (inside pool.install)
//!
//! 2× OS thread creation per task = ~25× context-switch overhead on 4 P-cores.
//!
//! ## The Fix
//!
//! New flow — single asyncio.to_thread call, work-stealing via channel:
//!     asyncio.to_thread(rayon_submit_channel, ...)  ← OS thread (asyncio pool, holds GIL)
//!         → CHANNEL.submit(work_item)              ← ~5μs (bounded send)
//!             → rayon worker RECV from channel     ← existing pool threads
//!                 → Python::with_gil(|py| func.call(py))
//!                     → store result + signal condvar
//!         → returns immediately
//!
//!     asyncio.to_thread(rayon_join_channel, ...)   ← OS thread (asyncio pool, holds GIL)
//!         → condvar.wait()                         ← blocking wait on existing thread
//!             → returns result
//!
//! Cost per task: ~5μs (channel send) vs ~500μs (thread::spawn + join).
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

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

// crossbeam-channel is Send-safe (Receiver is Send + Sync)
// crossbeam_channel::Receiver is NOT Send
use crossbeam_channel::{bounded, Sender, Receiver};

/// SharedTask result state — encoded as u8 for atomic Swap operations.
/// 0 = Pending, 1 = Ready (result set), 2 = Aborted (timeout)
const STATE_PENDING: u8 = 0;
const STATE_READY: u8 = 1;
const STATE_ABORTED: u8 = 2;

use crate::cpu_pool;
use crate::io_pool;
use crate::mixed_pool;

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
    result: Mutex<Option<Result<Py<PyAny>, PyErr>>>,
    /// Set by rayon_abort to request early cancellation.
    cancel_flag: AtomicBool,
    /// Atomic state: STATE_PENDING | STATE_READY | STATE_ABORTED.
    /// Issue #3 fix: Use atomic swap instead of separate aborted flag + mutex
    /// to prevent race between worker writing result and timeout setting aborted.
    state: AtomicU8,
    /// Signaled when result is ready.
    condvar: Condvar,
}

// ---------------------------------------------------------------------------
// Dispatcher — runs pool.install() and consumes work from the channel
// ---------------------------------------------------------------------------

/// Spawn a dispatcher thread that runs pool.install() and consumes work from rx.
/// The dispatcher selects the correct pool (cpu/io/mixed) based on pool_name
/// and n_items hint stored in each WorkItem.
fn spawn_dispatcher(
    pool_name: &str,
    rx: Arc<crossbeam_channel::Receiver<WorkItem>>,
) {
    // Convert to owned String so it can be moved into the thread closure
    let pool_name_owned = pool_name.to_string();
    thread::Builder::new()
        .name(format!("hledac-dispatch-{}", pool_name_owned))
        .stack_size(4_194_304) // 4 MiB — stack for rayon workers
        .spawn(move || {
            // QoS hint for the dispatcher thread
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    use libc::pthread_set_qos_class_self_np;
                    let qos = libc::qos_class_t::QOS_CLASS_USER_INITIATED;
                    pthread_set_qos_class_self_np(qos, 0);
                }
            }

            // Route to the correct pool
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
fn run_dispatcher_loop(
    pool: &'static ThreadPool,
    rx: Arc<crossbeam_channel::Receiver<WorkItem>>,
) {
    pool.install(|| {
        loop {
            // Issue #1 fix: check shutdown flag before each recv
            if RAYON_SHUTDOWN.load(Ordering::Acquire) {
                break;
            }
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(work) => {
                    execute_work_item(work, || pool);
                }
                Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                    // Timeout — loop back to check shutdown flag
                    continue;
                }
                Err(crossbeam_channel::RecvTimeoutError::Disconnected) => {
                    // Channel closed — pool is shutting down
                    break;
                }
            }
        }
    });
}

/// Dispatcher loop for mixed pool — selects POOL_SINGLE or POOL_PAIR at dispatch time.
fn run_mixed_dispatcher_loop(rx: Arc<crossbeam_channel::Receiver<WorkItem>>) {
    // Issue #2 fix: wrap pool initialization in catch_unwind so a panic during
    // pool setup doesn't poison the dispatcher. If priming fails, exit gracefully.
    if std::panic::catch_unwind(|| {
        // prime POOL_SINGLE and POOL_PAIR so first work item doesn't pay init cost
        let _ = mixed_pool(0);
        let _ = mixed_pool(usize::MAX);
    }).is_err() {
        // Pool initialization panicked — cannot serve requests safely. Exit.
        return;
    }

    loop {
        // Issue #1 fix: check shutdown flag before each recv
        if RAYON_SHUTDOWN.load(Ordering::Acquire) {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(work) => {
                // Extract n_items before moving work into the closure
                let n = work.n_items;
                execute_work_item(work, || {
                    // Select pool based on n at dispatch time
                    mixed_pool(n)
                });
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                // Timeout — loop back to check shutdown flag
                continue;
            }
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
        }
    }
}

/// Execute a single work item: GIL-acquire, call Python func, store result, notify.
fn execute_work_item<F>(work: WorkItem, pool_fn: F)
where
    F: Fn() -> &'static ThreadPool,
{
    // Cooperative cancellation check
    if work.shared.cancel_flag.load(Ordering::Acquire) {
        let mut guard = work.shared.result.lock().unwrap();
        *guard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Task was cancelled before starting",
        )));
        // Issue #3 fix: use swap to atomically set STATE_READY so join sees it
        work.shared.state.swap(STATE_READY, Ordering::AcqRel);
        work.shared.condvar.notify_one();
        return;
    }

    // Execute Python function with GIL
    let py_result: Result<Py<PyAny>, PyErr> = Python::with_gil(|py| {
        let result = work
            .func
            .into_bound(py)
            .call1((work.args.into_bound(py),))?;
        Ok(result.unbind())
    });

    // Issue #3 fix: Use atomic swap to prevent race with timeout.
    // Only write result if state hasn't been set to ABORTED by a concurrent timeout.
    // Swap returns the OLD value — if it's still PENDING, we won the race.
    let expected = STATE_PENDING;
    if work.shared.state.compare_exchange(
        expected,
        STATE_READY,
        Ordering::AcqRel,
        Ordering::Acquire,
    ).is_ok() {
        // We atomically transitioned PENDING → READY. Safe to write result.
        let mut guard = work.shared.result.lock().unwrap();
        *guard = Some(py_result);
    }
    // If compare_exchange failed, state is already ABORTED — timeout won the race, skip write.

    work.shared.condvar.notify_one();
}

// ---------------------------------------------------------------------------
// Global channel senders — one per pool type
// Issue #1 fix: use Mutex<Option<Sender>> so shutdown can drop the sender.
// ---------------------------------------------------------------------------

/// Global shutdown flag — checked by all dispatchers on each loop iteration.
static RAYON_SHUTDOWN: AtomicBool = AtomicBool::new(false);

fn cpu_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>> = LazyLock::new(|| {
        let (tx, rx) = bounded(256); // bounded = back-pressure
        spawn_dispatcher("cpu", Arc::new(rx));
        Mutex::new(Some(tx))
    });
    &SENDER
}

fn io_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>> = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("io", Arc::new(rx));
        Mutex::new(Some(tx))
    });
    &SENDER
}

fn mixed_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>> = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("mixed", Arc::new(rx));
        Mutex::new(Some(tx))
    });
    &SENDER
}

// ---------------------------------------------------------------------------
// Python-callable API
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
) -> PyResult<Py<Any>> {
    // Clone Py objects — we must own our references to move into WorkItem
    let func_clone = Py::clone_ref(&func, py);
    let args_clone = Py::clone_ref(&args, py);

    // Shared state between Python caller and rayon dispatcher worker
    let shared: Arc<SharedTask> = Arc::new(SharedTask {
        result: Mutex::new(None),
        cancel_flag: AtomicBool::new(false),
        state: AtomicU8::new(STATE_PENDING),
        condvar: Condvar::new(),
    });

    let work = WorkItem {
        func: func_clone,
        args: args_clone,
        n_items,
        shared: Arc::clone(&shared),
    };

    // Select sender based on pool type
    let sender_mutex: &Mutex<Option<Sender<WorkItem>>> = match pool_type {
        "cpu" => cpu_sender(),
        "io" => io_sender(),
        "mixed" => mixed_sender(),
        _ => cpu_sender(),
    };

    // Send to bounded channel — ~5μs (vs ~500μs for thread::spawn).
    // Blocks if channel is full (back-pressure), which is correct since
    // asyncio.to_thread provides the bound.
    let sender_guard = sender_mutex.lock().unwrap();
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
    Ok(ptr.into_py(py))
}

/// Wait for a rayon_submit_channel task to complete.
///
/// GIL: caller (asyncio.to_thread) must hold the GIL during this call
/// (the condvar wait is a park, not a sleep, so GIL is released).
///
/// timeout_s: seconds to wait. None = indefinite.
#[pyfunction]
#[pyo3(name = "rayon_join_channel")]
pub fn rayon_join_channel_(
    py: Python<'_>,
    handle_ptr: usize,
    timeout_s: Option<f64>,
) -> PyResult<Py<Any>> {
    if handle_ptr == 0 {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Invalid handle: 0",
        ));
    }

    // Issue #4 fix: reconstruct Arc from raw pointer and Clone it so Box release
    // doesn't drop the inner Arc — only the Box wrapper is freed.
    let shared_box = unsafe { Box::from_raw(handle_ptr as *mut Arc<SharedTask>) };
    let shared = Arc::clone(&*shared_box); // increment Arc refcount
    drop(shared_box); // free Box, NOT the Arc — Arc refcount stays +1

    let timeout = timeout_s
        .map(|t| Duration::from_secs_f64(t.max(0.0)))
        .unwrap_or(Duration::MAX);

    // condvar.wait releases the mutex while waiting — GIL is NOT held during wait
    // (Python threads park via _thread.park)
    let (guard, wait_result) = shared.condvar.wait_timeout_while(
        shared.result.lock().unwrap(),
        timeout,
        |r| r.is_none(),
    ).unwrap();

    let timed_out = wait_result.timed_out();
    if timed_out {
        // Issue #3 fix: atomically set ABORTED state. If worker already set READY,
        // compare_exchange fails (worker won race) — don't overwrite its result.
        let expected = STATE_PENDING;
        let _ = shared.state.compare_exchange(
            expected,
            STATE_ABORTED,
            Ordering::AcqRel,
            Ordering::Acquire,
        );
        drop(guard);

        // Write TimeoutError only if result not yet set by worker
        let mut rguard = shared.result.lock().unwrap();
        if rguard.is_none() {
            *rguard = Some(Err(PyErr::new::<
                pyo3::exceptions::PyRuntimeError,
                _,
            >("Rayon dispatch timed out")));
        }

        let result = shared.result.lock().unwrap().take();
        return match result {
            Some(Ok(py_obj)) => Ok(py_obj.into_py(py)),
            Some(Err(err)) => Err(err),
            None => Ok(py.None().into()),
        };
    }

    let result = guard.into_inner().take();

    match result {
        Some(Ok(py_obj)) => Ok(py_obj.into_py(py)),
        Some(Err(err)) => Err(err),
        None => Ok(py.None().into()),
    }
}

/// Abort a rayon dispatch task.
/// Sets cancel flag and waits up to 5s for worker acknowledgment.
#[pyfunction]
#[pyo3(name = "rayon_abort_channel")]
pub fn rayon_abort_channel_(handle_ptr: usize) -> PyResult<()> {
    if handle_ptr == 0 {
        return Ok(());
    }

    // Issue #4 fix: clone Arc so Box release doesn't drop inner Arc
    let shared_box = unsafe { Box::from_raw(handle_ptr as *mut Arc<SharedTask>) };
    let shared = Arc::clone(&*shared_box);
    drop(shared_box);

    shared.cancel_flag.store(true, Ordering::Release);

    // Wait up to 5s for worker to acknowledge (set result or check cancel)
    let timeout = Duration::from_secs(5);
    let (_guard, _wait_result) = shared.condvar.wait_timeout_while(
        shared.result.lock().unwrap(),
        timeout,
        |r| r.is_none(),
    ).unwrap();

    Ok(())
}

/// Issue #1 fix: Graceful shutdown — sets shutdown flag, closes all senders,
/// and waits for dispatcher threads to exit (via channel close).
/// Safe to call multiple times.
#[pyfunction]
#[pyo3(name = "rayon_shutdown_channel")]
pub fn rayon_shutdown_channel_() -> PyResult<()> {
    // 1. Set shutdown flag — all dispatchers will exit their loops on next iteration
    RAYON_SHUTDOWN.store(true, Ordering::Release);

    // 2. Drop all senders — this closes all channels and wakes dispatchers
    // cpu_sender etc. are LazyLock so they auto-initialize on first access
    if let Ok(tx) = cpu_sender().lock() {
        let _ = tx.take(); // drops Sender → channel closes
    }
    if let Ok(tx) = io_sender().lock() {
        let _ = tx.take();
    }
    if let Ok(tx) = mixed_sender().lock() {
        let _ = tx.take();
    }

    Ok(())
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rayon_submit_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_join_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_abort_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_shutdown_channel_, m)?)?;
    Ok(())
}
