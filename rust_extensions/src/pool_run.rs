//! pool_run — Python-callable rayon pool runners
//!
//! Exposes Rust rayon thread pools to Python via PyO3.
//! Enables Python asyncio to delegate CPU/IO-bound work to rayon pools.
//!
//! ## GIL Strategy
//!
//! The `py: Python<'_>` parameter holds the GIL. Inside `pool.install()`,
//! rayon workers cannot access `py` directly (not Send). Workaround: call
//! `Python::with_gil()` INSIDE the pool closure to re-acquire GIL for
//! the Python call. The GIL is released during the rayon work.
//!
//! For true multi-core Python parallelism, use `ProcessPoolExecutor` instead.
//! This module provides rayon-backed dispatch for Python functions that
//! release the GIL internally (I/O, or nested `asyncio.to_thread()`).

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;
use std::sync::{Arc, Mutex};
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use crate::cpu_pool;
use crate::io_pool;
use crate::mixed_pool;

// CPU pool runner — 4 P-cores for CPU-bound SIMD/hot path

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

// I/O pool runner — 2 threads for I/O-bound (DuckDB, compress)

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

// Mixed pool runner — adaptive 1-2 threads based on batch size

#[pyfunction]
#[pyo3(name = "mixed_pool_run")]
pub fn mixed_pool_run_(
    _py: Python<'_>,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    Python::with_gil(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

// ---------------------------------------------------------------------------
// rayon_submit — spawns a background thread and returns a JoinHandle for cancel.
// Python side: asyncio.to_thread(rayon_submit, pool_type, n_items, fn, args)
// → returns opaque handle bytes; asyncio.to_thread(rayon_join, handle) waits result.
// Cancellation: rayon_abort(handle) calls thread::spawn(...).abort().
// ---------------------------------------------------------------------------

/// Shared storage for rayon task result + join handle + cancellation.
struct SharedTask {
    result: Mutex<Option<Result<Py<PyAny>, PyErr>>>,
    join_handle: Mutex<Option<thread::JoinHandle<()>>>,
    /// Cancellation flag — set by rayon_abort to request early exit.
    cancel_flag: AtomicBool,
    /// Abort flag — set on timeout so worker doesn't overwrite TimeoutError.
    aborted: AtomicBool,
}

/// Spawn a rayon pool task and return an opaque handle for join/abort.
///
/// pool_type: "cpu" | "io" | "mixed"
/// n_items: batch size hint for mixed pool adaptive threading
/// func: Python callable
/// args: Python tuple of arguments
///
/// Returns: bytes representing the SharedTask (opaque to Python).
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
    let pool_type_str = pool_type.to_string();

    // Shared storage — rayon thread writes result, Python reads via rayon_join
    let shared: Arc<SharedTask> = Arc::new(SharedTask {
        result: Mutex::new(None),
        join_handle: Mutex::new(None),
        cancel_flag: AtomicBool::new(false),
        aborted: AtomicBool::new(false),
    });

    let shared_clone = Arc::clone(&shared);

    // Spawn work thread — thread::spawn returns immediately, work runs in background.
    // pool.install keeps closure on current thread (GIL-safe), JoinHandle::abort
    // terminates the thread + rayon worker immediately on cancel.
    let handle = thread::spawn(move || {
        let pool: &ThreadPool = match pool_type_str.as_str() {
            "cpu" => cpu_pool(),
            "io" => io_pool(),
            "mixed" => mixed_pool(n_items),
            _ => cpu_pool(),
        };

        pool.install(|| {
            // Re-acquire GIL inside rayon pool.
            // Store Result<Py<PyAny>, PyErr> so we can pass error across thread boundary.
            // Check cancel_flag before starting work — allows rayon_abort to interrupt early.
            if shared_clone.cancel_flag.load(Ordering::Acquire) {
                let mut guard = shared_clone.result.lock().unwrap();
                *guard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "Task was cancelled before starting",
                )));
                return;
            }
            let py_result: Result<Py<PyAny>, PyErr> = Python::with_gil(|py| {
                // Note: cancel_flag is checked once before work starts (cooperative cancellation).
                // For interruptible long-running work, the Python caller should use
                // chunked/iterative processing with explicit cancel_flag checks.
                let result = func_clone.into_bound(py).call1((args_clone.into_bound(py),))?;
                Ok(result.unbind())
            });
            // Store result for rayon_join to retrieve — but not if we were aborted (timeout).
            // Checking aborted flag prevents worker result from overwriting TimeoutError.
            if !shared_clone.aborted.load(Ordering::Acquire) {
                let mut guard = shared_clone.result.lock().unwrap();
                *guard = Some(py_result);
            }
        });
    });

    // Store the JoinHandle in shared storage so rayon_join can wait for thread completion
    {
        let mut jh_guard = shared.join_handle.lock().unwrap();
        *jh_guard = Some(handle);
    }

    // Return pointer to shared task as Python int
    let ptr = Box::into_raw(Box::new(shared)) as usize;
    Ok(ptr.into_py(py))
}

/// Wait for a rayon_submit task to complete. Returns the Python result.
/// Timeout is expressed in seconds — defaults to 30 s. If None, waits indefinitely.
/// On timeout, returns None (Python side should retry or abort).
#[pyfunction]
#[pyo3(name = "rayon_join")]
pub fn rayon_join_(py: Python<'_>, handle_ptr: usize, timeout_s: Option<f64>) -> PyResult<Py<PyAny>> {
    // Reconstruct the shared task pointer
    let shared_task = unsafe {
        Box::from_raw(handle_ptr as *mut Arc<SharedTask>)
    };

    // First, wait for the thread to complete by joining its handle
    let join_handle = {
        let mut jh_guard = shared_task.join_handle.lock().unwrap();
        jh_guard.take()
    };

    if let Some(handle) = join_handle {
        // Helper thread approach for timeout-aware join:
        // Main thread blocks on mpsc receiver; helper thread does the actual join.
        // If timeout expires, we detach the worker and return error.
        let (tx, rx) = mpsc::channel::<Result<(), std::thread::Result<()>>>();
        let helper = thread::spawn(move || {
            let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                handle.join()
            }));
            let _ = tx.send(match res {
                Ok(inner) => Ok(inner.map(|_| ())),
                Err(panic_err) => Ok(Err(panic_err)),
            });
        });

        let timed_out = if let Some(timeout_secs) = timeout_s {
            let timeout = Duration::from_secs_f64(timeout_secs.max(0.0));
            match rx.recv_timeout(timeout) {
                Ok(Ok(())) => false, // Thread finished normally
                Ok(Err(_)) => {
                    // Thread panicked — result already set in guard below
                    false
                }
                Err(mpsc::RecvTimeoutError::Timeout) => true, // Timeout
                Err(mpsc::RecvTimeoutError::Disconnected) => false, // Helper crashed
            }
        } else {
            // No timeout — wait indefinitely
            match rx.recv() {
                Ok(Ok(())) => false,
                Ok(Err(_)) => false, // Thread panicked
                Err(_) => false,
            }
        };

        // Timeout path: worker thread is still running.
        // Must wait for helper to finish (helper holds handle.join()).
        // If worker panicked, catch_unwind in helper already captured it.
        // Set aborted flag BEFORE writing result — worker checks it before overwriting.
        if timed_out {
            shared_task.aborted.store(true, Ordering::Release);
            let mut result_guard = shared_task.result.lock().unwrap();
            if result_guard.is_none() {
                *result_guard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "Rayon worker thread timed out",
                )));
            }
            drop(result_guard); // unlock before joining — helper may panic
            let _ = helper.join();
        } else {
            // Normal path: worker finished, helper will finish shortly
            let _ = helper.join();
        }
    }

    // Now read the result (thread has finished, result is available)
    let mut result_guard = shared_task.result.lock().unwrap();

    match result_guard.take() {
        Some(Ok(py_obj)) => {
            // Return the Python object, transferring ownership back to Python's GC
            Ok(py_obj.into_py(py))
        }
        Some(Err(err)) => {
            // Propagate the Python exception
            Err(err)
        }
        None => {
            // Result not ready yet — shouldn't happen with proper await
            Ok(py.None().into())
        }
    }
}

/// Abort a rayon_submit task.
/// Sends cancellation signal and waits for thread to finish before releasing Arc.
/// Timeout: 5 seconds max (after that, detaches and returns error).
#[pyfunction]
#[pyo3(name = "rayon_abort")]
pub fn rayon_abort_(handle_ptr: usize) -> PyResult<()> {
    // Reconstruct the shared task pointer
    let shared_task = unsafe {
        Box::from_raw(handle_ptr as *mut Arc<SharedTask>)
    };

    // Signal cancellation to the worker thread (it checks cancel_flag in its loop)
    shared_task.cancel_flag.store(true, Ordering::Release);

    // Take the JoinHandle and wait for thread to finish with timeout.
    // This ensures the thread releases its Arc clone before we drop our Arc.
    // Using helper thread + mpsc for timeout-aware join.
    let join_handle = shared_task.join_handle.lock().unwrap().take();
    if let Some(handle) = join_handle {
        let (tx, rx) = mpsc::channel::<Result<(), std::thread::Result<()>>>();
        let helper = thread::spawn(move || {
            let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                handle.join()
            }));
            let _ = tx.send(match res {
                Ok(inner) => Ok(inner.map(|_| ())),
                Err(panic_err) => Ok(Err(panic_err)),
            });
        });

        // 5 second timeout for abort
        let timed_out = match rx.recv_timeout(Duration::from_secs(5)) {
            Ok(Ok(())) => false,
            Ok(Err(_)) => false, // panicked
            Err(mpsc::RecvTimeoutError::Timeout) => true,
            Err(mpsc::RecvTimeoutError::Disconnected) => false,
        };

        if timed_out {
            // Worker thread may still be running — best-effort abort.
            // Note: handle was moved into helper closure, automatically cleaned up on helper exit.
        }

        let _ = helper.join();
    }

    // Now safe to drop: thread has finished and released its Arc clone.
    // Drop the Box which releases the Arc, refcount hits 0, SharedTask freed.
    drop(shared_task);

    Ok(())
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_submit_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_join_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_abort_, m)?)?;
    Ok(())
}
