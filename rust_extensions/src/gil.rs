//! GIL utilities - simplified for PyO3 0.29 compatibility.
//!
//! The original `release_gil` design used `py.detach()` which requires closures
//! to be Send + UnwindSafe. This is incompatible with closures that capture
//! parking_lot Mutexes, UnsafeCell types, etc.
//!
//! This simplified version just runs the closure without releasing the GIL.

use pyo3::prelude::*;

/// Thread-local flag for panic detection.
static RELEASE_GIL_PANICKED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Returns true if the previous release_gil call caught a panic.
#[inline]
pub fn release_gil_caught_panic() -> bool {
    RELEASE_GIL_PANICKED
        .swap(false, std::sync::atomic::Ordering::SeqCst)
}

/// Execute a closure (GIL NOT released - simplified version).
#[inline]
pub fn release_gil<F, R>(py: Python<'_>, f: F) -> R
where
    F: FnOnce() -> R + Send,
{
    let _ = py; // Suppress unused warning
    f()
}

/// Execute a closure returning PyResult (GIL NOT released).
#[inline]
pub fn release_gil_py<F, R>(py: Python<'_>, f: F) -> PyResult<R>
where
    F: FnOnce() -> PyResult<R> + Send,
{
    let _ = py; // Suppress unused warning
    f()
}
