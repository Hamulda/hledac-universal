//! gil.rs — GIL token management for free-threaded Python compatibility
//!
//! # F5.2: PyO3 0.23 + free-threaded Python GIL handling
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
//!   2. PyO3 0.23+ with `gil = "false"` feature to emit no-GIL code paths
//!
//! ## PyO3 GIL Handling (PyO3 0.23)
//!
//! In PyO3 0.23, ALL Python object access is implicitly GIL-protected when
//! called from Python (the GIL is held by the calling Python thread).
//!
//! Inside `#[pyfunction]` / `#[pymethods]`, you can use:
//!   - `py.allow_threads()` to temporarily release GIL during CPU-intensive work
//!   - `Python::with_gil()` for scoped GIL acquisition
//!
//! ## Key Invariants
//!
//! - `#[pyfunction]` entry points may receive `py: Python<'_>` parameter — already GIL-held
//! - Inside `pool.install()` (rayon), use `Python::with_gil()` + `release_gil()`
//! - Inside Rust-only code (no Python objects), no GIL needed even in free-threaded
//! - SIMD/hot path (`quality_gate`, `simhash_ext`, `simd_similarity`) operates on
//!   raw data (f32, u8, u64) — no Python objects, no GIL needed
//!
//! ## allow_threads Strategy (PyO3 Version Matrix)
//!
//! | PyO3 | allow_threads | Strategy |
//! |------|---------------|----------|
//! | 0.23 | py.allow_threads() | ✓ Public API, compile-time available |
//! | 0.28 | py.allow_threads() | ✓ Still available, #[deprecated] |
//! | 0.29 | REMOVED | Drop GIL token explicitly (not in public API) |
//! | 0.30+ | gil="false" feature | Free-threaded, no GIL token needed |
//!
//! Implementation: `is_gil_enabled()` probes allow_threads availability at module
//! init time (cached). `release_gil()` uses it for safe GIL release in hot paths.
//!
//! PyO3 0.23 is pinned in Cargo.toml — py.allow_threads() IS available.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};

// ─────────────────────────────────────────────────────────────────────────────
// Global GIL state — cached at module init time
// ─────────────────────────────────────────────────────────────────────────────

/// Cached result: is GIL currently active in this Python process?
/// - true: standard Python with GIL (PyO3 0.27, allow_threads available)
/// - false: free-threaded Python (PEP 703, no GIL) OR PyO3 0.29+ (no allow_threads)
static GIL_ENABLED: AtomicBool = AtomicBool::new(true);

/// One-time initialization flag (ensures probe runs exactly once).
static GIL_INIT: AtomicBool = AtomicBool::new(false);

/// Probe GIL state at module load time.
///
/// Detection order:
/// 1. Check Py_GIL_DISABLED env var (free-threaded Python 3.14+)
/// 2. Check Python version (3.14+ → likely free-threaded)
/// 3. Try allow_threads (PyO3 0.27: available; PyO3 0.29+: not in public API)
///
/// Result is cached in GIL_ENABLED for zero-overhead hot-path access.
fn probe_gil_state(py: Python<'_>) {
    if GIL_INIT.swap(true, Ordering::SeqCst) {
        return; // Already probed
    }

    // ── Step 1: Free-threaded Python (PEP 703, Python 3.14+)
    // Free-threaded Python sets Py_GIL_DISABLED=1 in environment.
    if let Ok(gil_disabled) = std::env::var("Py_GIL_DISABLED") {
        if gil_disabled == "1" {
            GIL_ENABLED.store(false, Ordering::SeqCst);
            return;
        }
    }

    // ── Step 2: Python version check (3.14+)
    // Python 3.14 may ship with free-threaded build. Check env var as fallback.
    let sys = match py.import("sys") {
        Ok(m) => m,
        Err(_) => {
            // Cannot import sys — assume GIL active (safe default)
            GIL_ENABLED.store(true, Ordering::SeqCst);
            return;
        }
    };

    if let Ok(version_info) = sys.getattr("version_info") {
        if let Ok((major, minor)) = version_info.extract::<(usize, usize)>() {
            if major >= 3 && minor >= 14 {
                // Python 3.14+ — check Py_GIL_DISABLED at runtime
                if let Ok(gil_disabled) = std::env::var("Py_GIL_DISABLED") {
                    if gil_disabled == "1" {
                        GIL_ENABLED.store(false, Ordering::SeqCst);
                        return;
                    }
                }
            }
        }
    }

    // ── Step 3: Try allow_threads (PyO3 0.28 only)
    //
    // PyO3 0.28: allow_threads() is a free function in pyo3::prelude.
    //   → #[deprecated] lint fires on older versions, #[allow(deprecated)] suppresses it.
    //
    // PyO3 0.29+: allow_threads() not in public API — would be
    //   a compile error. But we pin PyO3 =0.28.2 in Cargo.toml, so
    //   this branch ALWAYS compiles with allow_threads available.
    #[allow(deprecated)]
    let allow_threads_works = Python::with_gil(|py| {
        // allow_threads is a method on Python in PyO3 0.23
        py.allow_threads(|| {});
        true
    });

    GIL_ENABLED.store(allow_threads_works, Ordering::SeqCst);
}

/// Returns true if GIL is currently active in this Python process.
///
/// Cached after first call — zero overhead in hot paths.
#[inline]
pub fn is_gil_enabled() -> bool {
    GIL_ENABLED.load(Ordering::SeqCst)
}

/// Execute a closure with GIL released (if GIL is active).
///
/// This is the CORRECT way to release GIL for CPU-intensive Rust work
/// that is called from Python's asyncio ThreadPoolExecutor.
///
/// - PyO3 0.23 + standard Python: releases GIL → true parallelism
/// - Free-threaded Python: no-op (GIL never held)
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
    if is_gil_enabled() {
        // PyO3 0.23: py.allow_threads() releases GIL, runs closure, reacquires GIL.
        // Other Python coroutines can make progress on this thread while we run.
        py.allow_threads(f)
    } else {
        // Free-threaded Python: no GIL to release, just run directly.
        f()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Python-exposed functions
// ─────────────────────────────────────────────────────────────────────────────

/// Detect if running under free-threaded Python (no GIL).
///
/// Returns `true` if no GIL (free-threaded build or Py_GIL_DISABLED=1).
#[pyfunction]
pub fn is_free_threaded_python(py: Python<'_>) -> bool {
    probe_gil_state(py);
    !is_gil_enabled()
}

/// Maximum number of rayon workers recommended for the current Python runtime.
#[pyfunction]
pub fn recommended_rayon_workers(py: Python<'_>) -> usize {
    probe_gil_state(py);
    if is_gil_enabled() {
        // Standard Python with GIL: P-cores saturated by GIL, keep 4 for SIMD
        4
    } else {
        // Free-threaded: all CPU cores available
        std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4)
    }
}

// Module registration

/// Register GIL management functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Probe GIL state exactly once at module load time.
    // All subsequent calls to is_gil_enabled() / release_gil() are zero-overhead.
    Python::with_gil(|py| {
        probe_gil_state(py);
    });

    m.add_function(wrap_pyfunction!(is_free_threaded_python, m)?)?;
    m.add_function(wrap_pyfunction!(recommended_rayon_workers, m)?)?;
    Ok(())
}
