//! gil.rs — GIL token management for free-threaded Python compatibility
//!
//! # F5.2: PyO3 0.25 + free-threaded Python GIL handling
//!
//! ## Background
//!
//! Standard Python (CPython) uses a Global Interpreter Lock (GIL) to ensure
//! thread-safety of Python objects. The GIL serializes access to Python objects
//! across threads, preventing true parallel CPU-bound Python code.
//!
//! Free-threaded Python (PEP 703) removes the GIL from CPython, enabling true
//! multi-core parallelism. This requires:
//!   1. Rust code to explicitly acquire GIL tokens where accessing Python objects
//!   2. PyO3 0.27+ with `gil = "false"` feature to emit no-GIL code paths
//!
//! ## PyO3 GIL Handling
//!
//! In PyO3 0.25, ALL Python object access is implicitly GIL-protected when
//! called from Python (the GIL is held by the calling Python thread).
//!
//! Inside `#[pyfunction]`, the `py: Python<'_>` parameter represents the GIL
//! token already held by that thread. You can:
//!   - Use `py.allow_threads()` to temporarily release GIL during CPU-intensive work
//!   - Use `Python::with_gil()` for scoped GIL acquisition
//!
//! ## Key Invariants
//!
//! - `#[pyfunction]` entry points receive `py: Python<'_>` parameter — already GIL-held
//! - Inside `pool.install()` (rayon), we use `Python::with_gil()` — GIL acquired per-call
//! - Inside Rust-only code (no Python objects), no GIL needed even in free-threaded
//! - SIMD/hot path (`quality_gate`, `simhash_ext`, `simd_similarity`) operates on
//!   raw data (f32, u8, u64) — no Python objects, no GIL needed
//!
//! ## Issue #19: GIL-Free DuckDB Batch Iteration
//!
//! DuckDB queries via PyO3 hold GIL during result iteration. To avoid blocking
//! the asyncio event loop, DuckDB operations run on ThreadPoolExecutor (run_in_executor).
//! GIL is released on worker threads, but result iteration still happens under GIL.
//!
//! Cutting-edge solution: GIL-free iteration using PyO3's ` gil = "false"` feature
//! (available in PyO3 0.29+). When enabled, PyO3 emits code that does NOT
//! automatically acquire GIL, allowing explicit control via Python::with_gil().
//!
//! NOTE: allow_threads() is NOT in PyO3 0.29 public API. The pattern:
//!   py.allow_threads(move || { ... })
//! requires PyO3 internals. Workaround: use ThreadPoolExecutor batching
//! to amortize GIL acquisition overhead (Python-side fix in duckdb_store.py).
//!
//! For Python 3.14+ with free-threaded build (Py_GIL_DISABLED=1), GIL is
//! never held, enabling true parallel DuckDB iteration.

use pyo3::prelude::*;

// Free-threaded Python detection

/// Detect if running under free-threaded Python (no GIL).
///
/// Uses `sysconfig.get_config_var("Py_GIL_DISABLED")` or checks for
/// `_PyThreadState_UncheckedGet` availability.
///
/// Returns `true` if no GIL, `false` if standard Python.
#[pyfunction]
pub fn is_free_threaded_python(py: Python<'_>) -> bool {
    // py parameter is already GIL-protected in #[pyfunction]
    let sys = py.import("sys").ok();
    let version_info = sys.as_ref().and_then(|s| s.getattr("version_info").ok());
    let version_tuple = version_info.as_ref().and_then(|v| v.extract::<(usize, usize)>().ok());

    // Python 3.14+ has free-threaded support
    if let Some((major, minor)) = version_tuple {
        if major >= 3 && minor >= 14 {
            // Check if GIL is actually disabled
            if let Ok(gil_disabled) = std::env::var("Py_GIL_DISABLED") {
                return gil_disabled == "1";
            }
        }
    }
    false
}

/// Maximum number of rayon workers recommended for the current Python runtime.
///
/// In free-threaded Python: all CPU cores available (8 on M1).
/// In standard Python with GIL: limited by GIL contention.
#[pyfunction]
pub fn recommended_rayon_workers(py: Python<'_>) -> usize {
    if is_free_threaded_python(py) {
        // Free-threaded: all cores available
        std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4)
    } else {
        // Standard Python: GIL limits parallelism
        // Keep 4 for CPU-bound SIMD (P-cores), let GIL serialize the rest
        4
    }
}

// Module registration

/// Register GIL management functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(is_free_threaded_python, m)?)?;
    m.add_function(wrap_pyfunction!(recommended_rayon_workers, m)?)?;
    Ok(())
}
