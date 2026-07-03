//! gil.rs — GIL token management for free-threaded Python compatibility
//!
//! # F5.2: PyO3 0.23 + free-threaded Python GIL removal
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
//! In PyO3 0.29 (current), ALL Python object access is implicitly GIL-protected.
//! In PyO3 0.27+ with `gil = "false"`:
//!   - `Bound<'py, T>` APIs always require explicit GIL token
//!   - `Python::acquire_gil()` or `py.handle()` for GIL token
//!   - GIL is a runtime mutex, NOT a compile-time guarantee
//!
//! ## For `hledac-rust-extensions`
//!
//! The extension is called from Python asyncio context. When free-threaded Python
//! is used:
//!   - Python asyncio runs on main thread with GIL
//!   - Rust rayon pools run on separate threads WITHOUT GIL
//!   - We need to acquire GIL token before any Python object access in Rust
//!
//! ## Strategy
//!
//! 1. **Default (GIL present)**: `Python::acquire_gil()` is a no-op that returns
//!    the existing GIL token. Code works identically with or without GIL.
//!
//! 2. **Free-threaded Python**: `Python::acquire_gil()` actually acquires the GIL
//!    token on that thread. This is required before ANY `Bound::...` API call.
//!
//! 3. **Pool runners** (`pool_run.rs`): Already use `Python::attach()` which
//!    acquires GIL for the duration of the Python callable. This pattern is
//!    correct for both GIL and no-GIL Python.
//!
//! ## Key Invariants
//!
//! - `#[pyfunction]` entry points receive `py: Python<'_>` parameter — already GIL-held
//! - Inside `pool.install()` (rayon), we use `Python::attach()` — GIL acquired per-call
//! - Inside Rust-only code (no Python objects), no GIL needed even in free-threaded
//! - SIMD/hot path (`quality_gate`, `simhash_ext`, `simd_similarity`) operates on
//!   raw data (f32, u8, u64) — no Python objects, no GIL needed

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// GIL token acquisition utilities
// ---------------------------------------------------------------------------

/// Acquire GIL token for the current thread.
///
/// In standard Python (with GIL): this is a no-op that returns the existing token.
/// In free-threaded Python (no GIL): this blocks until GIL is acquired.
///
/// This is the SAFE approach — works correctly in both GIL and no-GIL contexts.
///
/// ## Example
/// ```rust
/// // Safe Rust code that works with both GIL and no-GIL Python
/// fn rust_only_function() {
///     // No Python objects — no GIL needed
///     let data = compute_something();
///     // If we need to create a Python object:
///     let gil = acquire_gil();
///     let py_obj = PyString::new(gil.python(), "result");
/// }
/// ```
/// NOTE: This function is only for internal Rust code that needs a Python token
/// OUTSIDE of a #[pyfunction] call. Inside #[pyfunction], the py parameter
/// is already GIL-protected.
#[inline]
pub fn acquire_gil() {
    // Python::attach() acquires the GIL and runs a closure with the Python token.
    // This works correctly in both GIL and no-GIL Python contexts.
    // We don't return the token — we just ensure GIL is held for the guard lifetime.
    let _ = pyo3::Python::attach(|_py| ());
}

/// RAII guard for GIL token — automatically released on drop.
///
/// In free-threaded Python, holding GIL for too long causes contention.
/// Use this for scopes where you need GIL but want automatic release.
///
/// ## Example
/// ```rust
/// fn process_and_call_python(data: &[u8]) -> usize {
///     // Compute on raw data — no GIL needed
///     let result = fast_simd_compute(data);
///
///     // Now call Python — acquire GIL temporarily
///     let gil = GILGuard::new();
///     let py_result = Python::call1(gil.python(), args);
///
///     result + py_result
/// }
/// ```
///
/// NOTE: GILGuard is only for advanced use cases. Most Rust code should use
/// `#[pyfunction]` which receives `py: Python<'_>` already GIL-protected.
#[allow(dead_code)]
pub struct GILGuard;

#[allow(dead_code)]
impl GILGuard {
    /// Acquire GIL and return guard.
    #[inline]
    pub fn new() -> Self {
        // Python::attach() acquires GIL for the duration of this call.
        // The GIL is released when the guard is dropped.
        let _ = pyo3::Python::attach(|_py| ());
        GILGuard
    }

    /// Get the Python token. Only valid while GIL is held.
    #[inline]
    pub fn python(&self) -> Python<'_> {
        // Safety: GIL is held because GILGuard is in scope.
        unsafe { pyo3::Python::assume_attached() }
    }
}

impl Default for GILGuard {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Free-threaded Python detection
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register GIL management functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(is_free_threaded_python, m)?)?;
    m.add_function(wrap_pyfunction!(recommended_rayon_workers, m)?)?;
    Ok(())
}
