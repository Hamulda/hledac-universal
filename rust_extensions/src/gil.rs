//! gil.rs — GIL release helper for CPU-intensive hot paths
//!
//! PyO3 0.29: `release_gil` uses `py.detach()` to temporarily release
//! the GIL during CPU-bound Rust work, allowing other Python coroutines to
//! make progress on this thread.
//!
//! PyO3 0.29 API changes:
//! - `py.allow_threads(f)` → `py.detach(f)` (same semantics)
//! - `Python::with_gil(|py| ...)` → `Python::attach(|py| ...)`
//!
//! MLX is NOT free-threaded compatible (PyO3/Metal/NumPy coordination assumes
//! GIL on tensors). This module uses the `py.detach()` API.
//!
//! Supported: PyO3 0.29 with `py.detach()`
//!
//! ## RUST-PANIC-001 FIX: Panic boundary
//! Every `py.detach()` call runs in a `std::panic::catch_unwind` wrapper.
//! Without this, a panic in any Rust FFI closure (LMDB ops, rayon workers,
//! regex parsing, Aho-Corasick) propagates through the FFI boundary and
//! triggers SIGABRT — crashing the entire Python process on M1 8GB.
//! PyO3 does NOT automatically add panic catch at FFI boundaries.

use pyo3::prelude::*;

/// Thread-local flag set when release_gil catches a panic.
/// Checked by callers to raise PyErr after GIL is reacquired.
static RELEASE_GIL_PANICKED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Returns true if the previous release_gil call caught a panic.
/// Resets the flag after returning (single-use).
///
/// Used by callers that need to raise PyErr after GIL is reacquired.
#[inline]
pub fn release_gil_caught_panic() -> bool {
    RELEASE_GIL_PANICKED
        .swap(false, std::sync::atomic::Ordering::SeqCst)
}

/// Execute a closure with GIL temporarily released (if GIL is active).
///
/// - Standard Python: releases GIL → other coroutines can progress on this thread
/// - Free-threaded Python (Py_GIL_DISABLED=1): no-op, GIL never existed
///
/// ## RUST-PANIC-001 FIX
/// The actual `py.detach` call is wrapped in `std::panic::catch_unwind`.
/// If the closure panics (OOM in rayon, regex panic on malformed input,
/// index out of bounds in LMDB ops), the panic is caught and a zero value
/// is returned instead. Callers should call `release_gil_caught_panic()`
/// after this returns and raise `PyErr` if true.
///
/// # Safety
///
/// The closure MUST NOT access any Python objects (no `Py<...>` types,
/// no `&str` that might be Python-allocated, no `String` from Python).
/// The `R: pyo3::marker::Ungil` bound enforces this.
#[inline]
pub fn release_gil<F, R>(py: Python<'_>, f: F) -> R
where
    F: FnOnce() -> R + Send + std::panic::UnwindSafe,
    R: pyo3::marker::Ungil + Default,
{
    // RUST-PANIC-001: Wrap py.detach in catch_unwind so panics in Rust
    // FFI closures don't propagate through the FFI boundary as SIGABRT.
    // On panic: sets flag, returns Default::default() so Python can handle.
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| py.detach(f))) {
        Ok(v) => v,
        Err(_) => {
            RELEASE_GIL_PANICKED.store(true, std::sync::atomic::Ordering::SeqCst);
            R::default()
        }
    }
}

/// Variant of `release_gil` for closures that return `PyResult<R>`.
/// On panic, returns `Err(PyRuntimeError)` instead of a default value.
/// This is the correct variant for use inside `#[pyfunction]` that already
/// return `PyResult<T>` — panic becomes a Python exception, not a SIGABRT.
///
/// # Safety
/// Same as `release_gil`: closure must not access Python objects.
#[inline]
pub fn release_gil_py<F, R>(py: Python<'_>, f: F) -> PyResult<R>
where
    F: FnOnce() -> PyResult<R> + Send + std::panic::UnwindSafe,
    R: pyo3::marker::Ungil,
{
    // RUST-PANIC-001: Same catch_unwind wrapper, but returns PyErr on panic.
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| py.detach(f))) {
        Ok(v) => v,
        Err(_) => {
            Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                "Rust panic in FFI boundary (GIL-released operation)",
            ))
        }
    }
}
