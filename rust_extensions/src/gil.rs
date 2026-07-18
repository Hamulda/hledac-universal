//! gil.rs — GIL release helper for CPU-intensive hot paths
//!
//! Standard Python (CPython) GIL: `release_gil` calls `py.allow_threads()` to
//! temporarily release the GIL during CPU-bound Rust work, allowing other Python
//! coroutines to make progress on this thread.
//!
//! Free-threaded Python (PEP 703, Py_GIL_DISABLED=1): `py.allow_threads()` is a
//! no-op — the GIL never existed, so nothing is released.
//!
//! MLX is NOT free-threaded compatible (PyO3/Metal/NumPy coordination assumes
//! GIL on tensors). This module is a no-op alias for the current codebase.
//!
//! Supported: PyO3 0.28 with `py.allow_threads()`.

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
    py.allow_threads(f)
}
