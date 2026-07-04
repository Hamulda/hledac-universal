//! pool_run — Python-callable rayon pool runners
//!
//! Exposes Rust rayon thread pools to Python via PyO3.
//! Enables Python asyncio to delegate CPU/IO-bound work to rayon pools.
//!
//! ## GIL Strategy (PyO3 0.29+)
//!
//! Python GIL token (!Send) cannot cross thread boundaries. In PyO3 0.29,
//! use `Python::with_gil()` inside rayon worker threads to acquire GIL.
//!
//! For true multi-core Python parallelism, use `ProcessPoolExecutor` instead.
//! This module provides rayon-backed dispatch for Python functions that
//! release the GIL internally (I/O, or nested `asyncio.to_thread()`).
//!
//! ## M1 8GB Considerations
//!
//! - cpu_pool: 4 threads (all P-cores) for CPU-bound SIMD/hot path
//! - io_pool: 2 threads for I/O-bound (DuckDB, compress)
//! - mixed_pool: 1-2 threads adaptive based on batch size
//! - Stack size: 1.5 MiB per thread = 6 MB total for cpu_pool

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;

use crate::cpu_pool;
use crate::io_pool;
use crate::mixed_pool;

// ---------------------------------------------------------------------------
// CPU pool runner — 4 P-cores for CPU-bound SIMD/hot path
// ---------------------------------------------------------------------------

/// Run a Python callable on the CPU-bound rayon pool (4 P-cores).
///
/// The callable is invoked ONCE inside the rayon pool. GIL is acquired
/// temporarily for the call and released immediately after.
///
/// For true multi-core Python parallelism, use `ProcessPoolExecutor`.
#[pyfunction]
#[pyo3(name = "cpu_pool_run")]
pub fn cpu_pool_run_(
    py: Python<'_>,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    let pool: &ThreadPool = cpu_pool();
    let mut result: PyResult<Py<PyAny>> = Ok(py.None());

    pool.install(|| {
        // PyO3 0.29+: Python::with_gil is safe inside rayon workers
        // GIL acquired for func.call1 duration
        result = Python::with_gil(|py| func.call1(py, args));
    });

    result
}

// ---------------------------------------------------------------------------
// I/O pool runner — 2 threads for I/O-bound (DuckDB, compress)
// ---------------------------------------------------------------------------

/// Run a Python callable on the I/O-bound rayon pool (2 threads).
///
/// See [`cpu_pool_run_`] for details.
#[pyfunction]
#[pyo3(name = "io_pool_run")]
pub fn io_pool_run_(
    py: Python<'_>,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    let pool: &ThreadPool = io_pool();
    let mut result: PyResult<Py<PyAny>> = Ok(py.None());

    pool.install(|| {
        result = Python::with_gil(|py| func.call1(py, args));
    });

    result
}

// ---------------------------------------------------------------------------
// Mixed pool runner — adaptive 1-2 threads based on batch size
// ---------------------------------------------------------------------------

/// Run a Python callable on the adaptive rayon mixed pool (1-2 threads).
///
/// Uses 1 thread for n_items < MIXED_THRESHOLD (32), 2 threads otherwise.
#[pyfunction]
#[pyo3(name = "mixed_pool_run")]
pub fn mixed_pool_run_(
    py: Python<'_>,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    let pool: &ThreadPool = mixed_pool(n_items);
    let mut result: PyResult<Py<PyAny>> = Ok(py.None());

    pool.install(|| {
        result = Python::with_gil(|py| func.call1(py, args));
    });

    result
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    Ok(())
}
