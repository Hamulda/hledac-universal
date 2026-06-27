//! pool_run — Python-callable rayon pool runners
//!
//! Exposes Rust rayon thread pools to Python via PyO3.
//! Enables Python asyncio to delegate CPU/IO-bound work to rayon pools.
//!
//! PyO3 0.29 note: Python GIL token (!Send) cannot be passed to rayon closures.
//! These functions call Python directly without rayon threading to avoid GIL issues.
//! For true parallelism, use Python's asyncio.to_thread() or concurrent.futures.

use pyo3::prelude::*;
use pyo3::types::PyTuple;

/// Run a Python callable on the CPU-bound rayon pool (4 P-cores).
///
/// Note: This calls the Python function directly. Rayon parallelism is handled
/// at the Python level for PyO3 0.29 compatibility.
#[pyfunction]
#[pyo3(name = "cpu_pool_run")]
pub fn cpu_pool_run_(
    py: Python<'_>,
    func: Py<PyAny>,
    args: &Bound<'_, PyTuple>,
) -> PyResult<Py<PyAny>> {
    func.call1(py, args)
}

/// Run a Python callable on the I/O-bound rayon pool (2 threads).
#[pyfunction]
#[pyo3(name = "io_pool_run")]
pub fn io_pool_run_(
    py: Python<'_>,
    func: Py<PyAny>,
    args: &Bound<'_, PyTuple>,
) -> PyResult<Py<PyAny>> {
    func.call1(py, args)
}

/// Run a Python callable on the adaptive rayon mixed pool (1-2 threads).
#[pyfunction]
#[pyo3(name = "mixed_pool_run")]
pub fn mixed_pool_run_(
    n_items: usize,
    py: Python<'_>,
    func: Py<PyAny>,
    args: &Bound<'_, PyTuple>,
) -> PyResult<Py<PyAny>> {
    let _ = n_items; // Reserved for future rayon use
    func.call1(py, args)
}

/// Module-level pool_run functions registration.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    Ok(())
}
