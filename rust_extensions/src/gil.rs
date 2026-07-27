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

use pyo3::prelude::*;

/// Execute a closure with GIL temporarily released (if GIL is active).
///
/// - Standard Python: releases GIL → other coroutines can progress on this thread
/// - Free-threaded Python (Py_GIL_DISABLED=1): no-op, GIL never existed
///
/// # Safety
///
/// The closure MUST NOT access any Python objects (no `Py<...>` types,
/// no `&str` that might be Python-allocated, no `String` from Python).
/// The `R: pyo3::marker::Ungil` bound enforces this.
#[inline]
pub fn release_gil<F, R>(py: Python<'_>, f: F) -> R
where
    F: FnOnce() -> R + Send,
    R: pyo3::marker::Ungil,
{
    // PyO3 0.29: py.detach() releases the GIL for the duration of the closure.
    // This is the direct replacement for py.allow_threads() from PyO3 0.24.
    py.detach(f)
}
