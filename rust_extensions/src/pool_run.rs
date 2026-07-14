//! pool_run — Python-callable rayon pool runners
//!
//! Exposes Rust rayon thread pools to Python via PyO3.
//! Enables Python asyncio to delegate CPU/IO-bound work to rayon pools.
//!
//! ## GIL Strategy (PyO3 0.27+)
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
use std::sync::Arc;
use std::thread;

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
    let pool: &ThreadPool = cpu_pool();

    pool.install(|| {
        // Re-acquire GIL inside rayon worker (py is not Send)
        Python::attach(|py| {
            let func_ref = Py::clone_ref(&func, py);
            let args_ref = Py::clone_ref(&args, py);
            // args_ref is Py<PyTuple>, &Py<PyAny> implements AsPyPointer
            func_ref.call1(py, (args_ref.as_ref(),))
        })
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
    let pool: &ThreadPool = io_pool();

    pool.install(|| {
        Python::attach(|py| {
            let func_ref = Py::clone_ref(&func, py);
            let args_ref = Py::clone_ref(&args, py);
            func_ref.call1(py, (args_ref.as_ref(),))
        })
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
    let pool: &ThreadPool = mixed_pool(n_items);

    pool.install(|| {
        Python::attach(|py| {
            let func_ref = Py::clone_ref(&func, py);
            let args_ref = Py::clone_ref(&args, py);
            func_ref.call1(py, (args_ref.as_ref(),))
        })
    })
}

// ---------------------------------------------------------------------------
// rayon_submit — spawns a background thread and returns a JoinHandle for cancel.
// Python side: asyncio.to_thread(rayon_submit, pool_type, n_items, fn, args)
// → returns opaque handle bytes; asyncio.to_thread(rayon_join, handle) waits result.
// Cancellation: rayon_abort(handle) calls thread::spawn(...).abort().
// ---------------------------------------------------------------------------

/// Spawn a rayon pool task and return an opaque handle for join/abort.
///
/// pool_type: "cpu" | "io" | "mixed"
/// n_items: batch size hint for mixed pool adaptive threading
/// func: Python callable
/// args: Python tuple of arguments
///
/// Returns: bytes representing the JoinHandle (opaque to Python).
#[pyfunction]
#[pyo3(name = "rayon_submit")]
pub fn rayon_submit_(
    py: Python<'_>,
    pool_type: &str,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    // Spawn a background thread that installs the rayon pool and calls the Python fn.
    // The JoinHandle is returned to Python as bytes via Arc.
    let func_clone = Py::clone_ref(&func, py);
    let args_clone = Py::clone_ref(&args, py);

    let handle: Arc<thread::JoinHandle<PyResult<Py<PyAny>>>> = Arc::new(thread::spawn(move || {
        let pool: &ThreadPool = match pool_type {
            "cpu" => cpu_pool(),
            "io" => io_pool(),
            "mixed" => mixed_pool(n_items),
            _ => cpu_pool(), // fallback
        };
        pool.install(|| {
            Python::with_gil(|py| {
                let f = Py::clone_ref(&func_clone, py);
                let a = Py::clone_ref(&args_clone, py);
                f.call1(py, (a.as_ref(),))
            })
        })
    }));

    // Serialize the Arc pointer as usize bytes so Python can pass it back to rayon_join/rayon_abort.
    let handle_ptr = Arc::into_raw(handle) as usize;
    Ok(Py::new(py, handle_ptr).unwrap().into())
}

/// Wait for a rayon_submit task to complete. Returns the Python result.
///
/// handle: opaque handle from rayon_submit
#[pyfunction]
#[pyo3(name = "rayon_join")]
pub fn rayon_join_(py: Python<'_>, handle_ptr: usize) -> PyResult<Py<PyAny>> {
    let handle: Arc<thread::JoinHandle<PyResult<Py<PyAny>>>> =
        // SAFETY: handle_ptr was created by rayon_submit as Arc::into_raw.
        // We reconstruct the Arc and immediately forget it (so no double-free).
        unsafe { Arc::from_raw(handle_ptr as *const _) };
    let handle = Arc::try_unwrap(handle)
        .map_err(|_| pyo3::exceptions::PyRuntimeError::new_err("handle already joined or aborted"))?;
    let result = handle.join().map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err("rayon task panicked or was aborted")
    })??;
    Ok(result)
}

/// Abort a rayon_submit task (calls JoinHandle::abort on the background thread).
///
/// handle: opaque handle from rayon_submit
#[pyfunction]
#[pyo3(name = "rayon_abort")]
pub fn rayon_abort_(handle_ptr: usize) -> PyResult<()> {
    let handle: Arc<thread::JoinHandle<PyResult<Py<PyAny>>>> =
        // SAFETY: same as rayon_join_.
        unsafe { Arc::from_raw(handle_ptr as *const _) };
    handle.abort();
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
