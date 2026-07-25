//! pool_run — Python-callable rayon pool runners (channel-based dispatch)
//!
//! ISSUE 5.2 FIX: Replaces thread::spawn-per-task with crossbeam-channel dispatch.
//!
//! ## The Problem (old implementation)
//!
//!     asyncio.to_thread(rayon_submit, ...)  ← OS thread #1 (asyncio pool)
//!         → thread::spawn(move || { pool.install(...) })  ← OS thread #2 (new)
//!             → rayon worker (inside pool.install)
//!
//! 2× OS thread creation per task = ~25× context-switch overhead on 4 P-cores.
//!
//! ## The Fix (new implementation)
//!
//! New flow — single asyncio.to_thread call, work-stealing via channel:
//!     asyncio.to_thread(rayon_submit, ...)  ← OS thread (asyncio pool, holds GIL)
//!         → CHANNEL.send(work_item)         ← ~5μs (bounded send)
//!             → rayon worker RECV from channel  ← existing pool threads
//!                 → Python::with_gil(|py| func.call(py))
//!                     → store result + signal condvar
//!         → returns immediately
//!
//!     asyncio.to_thread(rayon_join, ...)   ← OS thread (asyncio pool, holds GIL)
//!         → condvar.wait()                ← blocking wait on existing thread
//!             → returns result
//!
//! Cost per task: ~5μs (channel send) vs ~500μs (thread::spawn + join).

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Condvar, LazyLock, Mutex};
use std::thread;
use std::time::Duration;

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
    result: Mutex<Option<Result<Py<PyAny>, PyErr>>>,
    /// Set by rayon_abort to request early cancellation.
    cancel_flag: AtomicBool,
    /// Atomic state: STATE_PENDING | STATE_READY | STATE_ABORTED.
    /// Prevents race between worker writing result and timeout setting aborted.
    state: AtomicU8,
    /// Signaled when result is ready.
    condvar: Condvar,
}

// ---------------------------------------------------------------------------
// Global channel senders — one per pool type, one dispatcher thread each
// ---------------------------------------------------------------------------

static RAYON_SHUTDOWN: AtomicBool = AtomicBool::new(false);

fn cpu_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>, fn() -> Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("cpu", Arc::new(rx));
            Mutex::new(Some(tx))
        });
    &SENDER
}

fn io_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>, fn() -> Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("io", Arc::new(rx));
            Mutex::new(Some(tx))
        });
    &SENDER
}

fn mixed_sender() -> &'static Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<Mutex<Option<Sender<WorkItem>>>, fn() -> Mutex<Option<Sender<WorkItem>>>> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);
            spawn_dispatcher("mixed", Arc::new(rx));
            Mutex::new(Some(tx))
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
        let mut guard = work.shared.result.lock().unwrap();
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
// rayon_submit — channel-based dispatch, returns handle for join/abort
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "rayon_submit")]
pub fn rayon_submit_(
    py: Python<'_>,
    pool_type: &str,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    let func_clone = Py::clone_ref(&func, py);
    let args_clone = Py::clone_ref(&args, py);

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

    let sender_mutex: &Mutex<Option<Sender<WorkItem>>> = match pool_type {
        "cpu" => cpu_sender(),
        "io" => io_sender(),
        "mixed" => mixed_sender(),
        _ => cpu_sender(),
    };

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

    // Return pointer to SharedTask — Python passes this to rayon_join
    let ptr = Box::into_raw(Box::new(shared)) as usize;
    Ok(ptr.into_py(py))
}

// ---------------------------------------------------------------------------
// rayon_join — wait for rayon_submit task to complete
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "rayon_join")]
pub fn rayon_join_(py: Python<'_>, handle_ptr: usize, timeout_s: Option<f64>) -> PyResult<Py<PyAny>> {
    if handle_ptr == 0 {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Invalid handle: 0"));
    }

    // Reconstruct Arc from raw pointer and Clone so Box release doesn't drop inner Arc
    let shared_box = unsafe { Box::from_raw(handle_ptr as *mut Arc<SharedTask>) };
    let shared = Arc::clone(&*shared_box);
    drop(shared_box); // free Box, NOT the Arc

    let timeout = timeout_s
        .map(|t| Duration::from_secs_f64(t.max(0.0)))
        .unwrap_or(Duration::MAX);

    let (guard, wait_result) = shared.condvar.wait_timeout_while(
        shared.result.lock().unwrap(),
        timeout,
        |r| r.is_none(),
    ).unwrap();

    let timed_out = wait_result.timed_out();
    if timed_out {
        // Atomically set ABORTED if still PENDING
        let expected = STATE_PENDING;
        let _ = shared.state.compare_exchange(
            expected,
            STATE_ABORTED,
            Ordering::AcqRel,
            Ordering::Acquire,
        );
        drop(guard);

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

    let result = (*guard).take();

    match result {
        Some(Ok(py_obj)) => Ok(py_obj.into_py(py)),
        Some(Err(err)) => Err(err),
        None => Ok(py.None().into()),
    }
}

// ---------------------------------------------------------------------------
// rayon_abort — signal cancellation and wait for worker acknowledgment
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "rayon_abort")]
pub fn rayon_abort_(handle_ptr: usize) -> PyResult<()> {
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
    let (_guard, _timed_out) = shared.condvar.wait_timeout_while(
        shared.result.lock().unwrap(),
        timeout,
        |r| r.is_none(),
    ).unwrap();

    Ok(())
}

// ---------------------------------------------------------------------------
// rayon_shutdown — graceful shutdown of all dispatcher threads
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "rayon_shutdown")]
pub fn rayon_shutdown_() -> PyResult<()> {
    RAYON_SHUTDOWN.store(true, Ordering::Release);

    if let Ok(tx) = cpu_sender().lock() {
        let _ = tx.take();
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
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_submit_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_join_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_abort_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_shutdown_, m)?)?;
    Ok(())
}
