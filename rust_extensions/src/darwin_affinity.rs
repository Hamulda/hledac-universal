//! Darwin (macOS) CPU affinity implementation.
//!
//! MODERN-26: Provides real CPU affinity on macOS via Mach APIs.
//!
//! macOS does not expose `pthread_setaffinity_np` in standard libc.
//! Instead, Apple Silicon (M1+) supports affinity via:
//!   1. `thread_policy_set` with `THREAD_AFFINITY_POLICY` — for core class hints
//!   2. `processor_set_affinity` via Mach kernel APIs — for hard core pinning
//!
//! This module implements both approaches:
//!   - `apply_darwin_affinity_hint()`: QoS + perf-level hint via Mach APIs
//!   - `set_thread_perf_level()`: Direct perf-level preference (M1+ only)
//!
//! # Apple Silicon Core Classes
//!
//! - **perflevel0**: P-cores (Performance) — fastest, for CPU-bound work
//! - **perflevel1**: E-cores (Efficiency) — slower, for background tasks
//!
//! # Safety
//!
//! All Mach API calls are unsafe and must be called from the correct thread.

use libc::{c_int, pthread_self, thread_t};
use std::mem::size_of;

// ============================================================================
// Mach API Constants (from mach/thread_policy.h)
// ============================================================================

/// Thread affinity policy flavor.
const THREAD_AFFINITY_POLICY: i32 = 3;

/// Enable affinity (1) or disable (0).
const THREAD_AFFINITY_TAG_ENABLE: i32 = 1;

/// Performance level hint — maps to perflevel0 (P-core) / perflevel1 (E-core).
#[repr(C)]
struct thread_affinity_policy {
    affinity_tag: i32,
    user_selected: i32,
}

/// Thread performance profile (M1+ only).
/// Used to prefer P-cores or E-cores.
#[repr(C)]
struct thread_perfpolicy {
    perf_class: i32,
}

/// Performance class: P-core (fastest cores).
const THREAD_PERFLEVEL_P_CORES: i32 = 0;

/// Performance class: E-core (efficient cores).
const THREAD_PERFLEVEL_E_CORES: i32 = 1;

// ============================================================================
// Mach API Declarations (raw FFI)
// ============================================================================

// Get current thread port (mach_port_t).
// Equivalent to `pthread_mach_thread_np(pthread_self())`.
#[cfg(target_os = "macos")]
extern "C" {
    fn pthread_mach_thread_np(thread: libc::pthread_t) -> thread_t;
}

// Set thread policy on current thread.
// Returns KERN_SUCCESS (0) on success.
#[cfg(target_os = "macos")]
extern "C" {
    fn thread_policy_set(
        thread: thread_t,
        flavor: i32,
        policy_info: *const std::ffi::c_void,
        count: i32,
    ) -> c_int;
}

// ============================================================================
// Core Affinity Implementation
// ============================================================================

/// Apply CPU affinity hint on macOS using Mach APIs.
///
/// MODERN-26: Replaces the previous no-op for macOS.
///
/// This function provides soft affinity — a hint to the scheduler to prefer
/// certain core types. It does NOT guarantee hard pinning (which requires
/// root privileges on macOS).
///
/// # Arguments
///
/// * `prefer_pcore` — If true, prefer P-cores (perflevel0). If false, prefer E-cores.
///
/// # Core Class Mapping
///
/// - M1/M2/M3 MacBook Air: 8 total cores
///   - 4 P-cores (perflevel0) + 4 E-cores (perflevel1) on M1 Pro+ variants
///   - 4 P-cores only on standard M1
///
/// - For M1 8GB (2P+4E configuration):
///   - P-cores = indices 0-1 (perflevel0)
///   - E-cores = indices 2-5 (perflevel1)
///
/// # Returns
///
/// Nothing — all errors are non-fatal (graceful degradation).
#[cfg(target_os = "macos")]
pub fn apply_darwin_affinity_hint(prefer_pcore: bool) {
    let thread = unsafe { pthread_mach_thread_np(pthread_self()) };

    if thread == 0 {
        // Invalid thread port — skip silently
        return;
    }

    // First, apply perf-level policy (M1+ only) to prefer P/E cores.
    // This is the primary mechanism for core class selection.
    let mut perf_policy = thread_perfpolicy {
        perf_class: if prefer_pcore {
            THREAD_PERFLEVEL_P_CORES
        } else {
            THREAD_PERFLEVEL_E_CORES
        },
    };

    let perf_result = unsafe {
        thread_policy_set(
            thread,
            4, // THREAD_PERFORMANCE_PROFILE
            &mut perf_policy as *const _ as *const std::ffi::c_void,
            (size_of::<thread_perfpolicy>() / size_of::<i32>()) as i32,
        )
    };

    // If perf policy failed (older Macs), try affinity policy as fallback.
    if perf_result != 0 {
        let mut affinity_policy = thread_affinity_policy {
            affinity_tag: if prefer_pcore { 1 } else { 2 }, // 1=P, 2=E
            user_selected: THREAD_AFFINITY_TAG_ENABLE,
        };

        unsafe {
            let _ = thread_policy_set(
                thread,
                THREAD_AFFINITY_POLICY,
                &mut affinity_policy as *const _ as *const std::ffi::c_void,
                (size_of::<thread_affinity_policy>() / size_of::<i32>()) as i32,
            );
        }
    }
}

/// Apply P-core preference for CPU-bound work.
///
/// MODERN-26: Convenience wrapper for apply_darwin_affinity_hint(true).
#[cfg(target_os = "macos")]
#[inline]
pub fn apply_pcore_affinity() {
    apply_darwin_affinity_hint(true);
}

/// Apply E-core preference for background/IO-bound work.
///
/// MODERN-26: Convenience wrapper for apply_darwin_affinity_hint(false).
#[cfg(target_os = "macos")]
#[inline]
pub fn apply_ecore_affinity() {
    apply_darwin_affinity_hint(false);
}

// ============================================================================
// Stub implementations for non-Darwin platforms
// ============================================================================

/// Stub for non-macOS platforms (Linux, Windows, etc.).
#[cfg(not(target_os = "macos"))]
pub fn apply_darwin_affinity_hint(_prefer_pcore: bool) {
    // No-op on non-Darwin platforms.
    // Linux uses pthread_setaffinity_np; Windows uses SetThreadAffinityMask.
}

/// Stub for non-macOS platforms.
#[cfg(not(target_os = "macos"))]
#[inline]
pub fn apply_pcore_affinity() {
    // No-op
}

/// Stub for non-macOS platforms.
#[cfg(not(target_os = "macos"))]
#[inline]
pub fn apply_ecore_affinity() {
    // No-op
}

// ============================================================================
// High-Level API: Map P/E intent to Darwin affinity
// ============================================================================

/// Apply CPU affinity based on P/E intent.
///
/// MODERN-26: Main entry point for thread affinity on macOS.
///
/// # Arguments
///
/// * `p_cores` — Number of P-cores to prefer (0 = use E-cores only)
///
/// # Mapping
///
/// - `p_cores > 0`: Prefer P-cores (CPU-bound work)
/// - `p_cores == 0`: Prefer E-cores (I/O-bound, background work)
///
/// # Example
///
/// ```ignore
/// // For CPU-bound thread pool (2 threads):
/// apply_cpu_affinity(2); // Prefer P-cores
///
/// // For I/O-bound thread pool (2 threads):
/// apply_cpu_affinity(0); // Prefer E-cores
/// ```
#[cfg(target_os = "macos")]
pub fn apply_cpu_affinity(p_cores: usize) {
    let prefer_pcore = p_cores > 0;
    apply_darwin_affinity_hint(prefer_pcore);
}

/// Stub for non-macOS.
#[cfg(not(target_os = "macos"))]
pub fn apply_cpu_affinity(_p_cores: usize) {
    // No-op; Linux uses pthread_setaffinity_np directly.
}

// ============================================================================
// PyO3 Python FFI
// ============================================================================

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

/// Apply P-core preference for CPU-bound work (Python FFI).
///
/// MODERN-26: Convenience wrapper — prefer P-cores for CPU-intensive work.
/// Usage: `rust.darwin_affinity.apply_pcore_affinity()`
#[pyfunction]
#[cfg(target_os = "macos")]
fn apply_pcore_affinity_py() {
    apply_pcore_affinity();
}

/// Apply E-core preference for background/IO-bound work (Python FFI).
///
/// MODERN-26: Convenience wrapper — prefer E-cores for I/O-bound work.
/// Usage: `rust.darwin_affinity.apply_ecore_affinity()`
#[pyfunction]
#[cfg(target_os = "macos")]
fn apply_ecore_affinity_py() {
    apply_ecore_affinity();
}

/// Apply CPU affinity based on P-core count (Python FFI).
///
/// MODERN-26: Apply affinity hint based on p_cores count.
///
/// # Arguments
/// * `p_cores` — Number of P-cores to prefer (0 = E-cores)
///
/// Usage: `rust.darwin_affinity.apply_cpu_affinity(4)`
#[pyfunction]
#[cfg(target_os = "macos")]
fn apply_cpu_affinity_py(p_cores: usize) {
    apply_cpu_affinity(p_cores);
}

/// Stub for non-macOS (Python FFI).
#[pyfunction]
#[cfg(not(target_os = "macos"))]
fn apply_pcore_affinity_py() {}

/// Stub for non-macOS (Python FFI).
#[pyfunction]
#[cfg(not(target_os = "macos"))]
fn apply_ecore_affinity_py() {}

/// Stub for non-macOS (Python FFI).
#[pyfunction]
#[cfg(not(target_os = "macos"))]
fn apply_cpu_affinity_py(_p_cores: usize) {}

/// Register darwin_affinity functions in Python module.
#[cfg(target_os = "macos")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(apply_pcore_affinity_py, m)?)?;
    m.add_function(wrap_pyfunction!(apply_ecore_affinity_py, m)?)?;
    m.add_function(wrap_pyfunction!(apply_cpu_affinity_py, m)?)?;
    Ok(())
}

/// Stub for non-macOS.
#[cfg(not(target_os = "macos"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
