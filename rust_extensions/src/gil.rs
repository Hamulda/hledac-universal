//! GIL management utilities.
//!
//! ## Architecture
//!
//! PyO3 0.29 changed the GIL API. The `with_gil` / `allow_threads` pattern
//! was removed. This module provides GIL release utilities that work with
//! the new PyO3 0.29 API.
//!
//! ## Modern PyO3 Strategy (ROADMAP-016)
//!
//! **Single-item functions**: Use `#[pyo3(gil = "release")]` attribute for automatic
//! GIL release. This is the PREFERRED approach for pure-Rust functions that don't
//! need Python object access. Example:
//!
//! ```ignore
//! #[pyfunction]
//! #[pyo3(gil = "release")]  // Automatic GIL release
//! pub fn sha256_hex(data: &[u8]) -> String {
//!     // GIL is released during this function's execution
//!     blake3::hash(data).to_hex().to_string()
//! }
//! ```
//!
//! **Batch functions with rayon**: Use `release_gil(py, || { ... })` for explicit
//! GIL release during parallel work. This allows asyncio event loop to run while
//! CPU-bound Rust work executes.
//!
//! ## Two-Tier Pattern (for advanced use cases)
//!
//! **1. Pure Rust Operations (CAN release GIL)**
//!    Use `release_gil(py, || { ... })` or `release_gil_py(py, || { ... })`
//!
//!    For `release_gil`: Closure must be `FnOnce() -> R + Send` (no panic handling)
//!    For `release_gil_py`: Closure must be `FnOnce() -> PyResult<R> + Send + UnwindSafe`
//!
//!    Examples: PBKDF2, AES-GCM, regex, xxhash, serde_json, Unicode normalization
//!    Benefit: Rayon parallel work can run without GIL contention
//!
//! **2. Mixed Rust/Python Operations (CANNOT release GIL)**
//!    Use `Python::attach(py, |py| { ... Python object access ... })`
//!    The GIL MUST be held during Python object access
//!    Examples: All LMDB operations (env.getattr, txn.call_method)
//!
//! ## Important: Closure Requirements
//!
//! The `py.detach()` API requires `Send` closures because the GIL is released
//! during execution. If your closure captures non-Send types (e.g., Mutex guards,
//! UnsafeCell), use `release_gil_py` with `AssertUnwindSafe` wrapper or restructure
//! the code to move non-Send acquisitions outside the closure.
//!
//! ## Python 3.14+ / M1/ARM64 Compatibility
//!
//! - GIL release is CRITICAL for 8GB M1 Air to avoid memory pressure from
//!   blocked Python threads during CPU-bound operations
//! - Allows asyncio event loop to run while Rust CPU-bound work executes
//! - Enables true CPU parallelism with rayon thread pool

use pyo3::prelude::*;

/// Thread-local flag for panic detection.
/// Set to true when a panic is caught in `release_gil_py`.
static RELEASE_GIL_PANICKED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Returns true if the previous release_gil_py call caught a panic.
/// Call this after `release_gil_py` to check for panics.
#[inline]
pub fn release_gil_caught_panic() -> bool {
    RELEASE_GIL_PANICKED.swap(false, std::sync::atomic::Ordering::SeqCst)
}

/// Execute a closure with the GIL released (GIL is RELEASED during closure).
///
/// MODERN-05 FIX: Uses `py.detach()` to actually release the GIL, enabling
/// other Python coroutines to run while CPU-bound Rust work executes.
///
/// ## How It Works
///
/// `py.detach(f)` executes closure `f` while the GIL is NOT held:
/// 1. Acquires GIL guard
/// 2. Drops guard (GIL released)  
/// 3. Executes `f()` WITHOUT the GIL
/// 4. Re-acquires GIL when returning
///
/// ## Requirements
///
/// The closure MUST be `Send` because `py.detach()` releases the GIL.
/// If the closure panicked and the GIL was released, we'd have undefined behavior.
///
/// NOTE: This function does NOT provide panic catching. Use `release_gil_py`
/// if you need panic handling.
///
/// ## Usage
///
/// ```ignore
/// // GOOD: Pure Rust work - GIL released during blake3 computation
/// let result = release_gil(py, move || {
///     let hash = blake3::hash(&data);
///     hash.as_bytes().to_vec()  // Vec<u8> is Send
/// });
/// ```
#[inline]
pub fn release_gil<F, R>(py: Python<'_>, f: F) -> R
where
    F: FnOnce() -> R + Send,  // Send required by py.detach() (Ungil on stable)
    R: Send,                   // Return type must be Send
{
    // MODERN-05 FIX: Actually release the GIL via py.detach().
    //
    // PyO3 0.29 detach() semantics:
    // - Releases GIL during closure execution (via GILGuard drop)
    // - Re-acquires GIL when returning
    // - Allows other Python threads to run during CPU-bound Rust work
    //
    // Bounds: F must be Send, R must be Send
    // This is the API contract - callers must ensure closures are safe
    // to run while GIL is released.
    py.detach(f)
}

/// Execute a closure returning PyResult with the GIL released.
///
/// MODERN-05 FIX: Uses `py.detach()` to actually release the GIL.
/// Panics are caught and converted to `PyErr::new::<PyRuntimeError, _>(...)`.
///
/// ## Requirements
///
/// The closure MUST be `Send + UnwindSafe` because:
/// - `Send`: Required by `py.detach()` for GIL release
/// - `UnwindSafe`: Required by `catch_unwind` for panic catching
///
/// If your closure captures non-UnwindSafe types (e.g., Mutex guards),
/// wrap it with `std::panic::AssertUnwindSafe`:
///
/// ```ignore
/// let result = release_gil_py(py, std::panic::AssertUnwindSafe(move || {
///     // closure that captures non-UnwindSafe types
///     Ok(())
/// }));
/// ```
#[inline]
pub fn release_gil_py<F, R>(py: Python<'_>, f: F) -> PyResult<R>
where
    F: FnOnce() -> PyResult<R> + Send + std::panic::UnwindSafe,
    R: Send,  // Required by py.detach() - return type must be Send (Ungil)
{
    // MODERN-05 FIX: Actually release the GIL via py.detach().
    // Panic handling: catch panics and convert to PyErr.
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        py.detach(|| {
            // This closure runs WITHOUT the GIL
            // If it panics, the outer catch_unwind catches it
            f()
        })
    }))
    .map_err(|_| {
        RELEASE_GIL_PANICKED.store(true, std::sync::atomic::Ordering::SeqCst);
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Rust panic in release_gil_py")
    })?
}
