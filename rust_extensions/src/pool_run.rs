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

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    Ok(())
}
