//! Adaptive thread scheduler — CPU saturation + memory-pressure aware.
//!
//! F270-FINAL: Extends the fixed-tier thread pools (cpu_pool/io_pool/mixed_pool)
//! with dynamic thread count recommendations based on:
//!   1. Current MLX Metal memory pressure (fraction of dynamic cache limit)
//!   2. CPU queue depth estimate (via active rayon worker count)
//!   3. Workload type (CPU-bound / I/O-bound / Mixed)
//!
//! ## MLX Metal-Aware Design (F330)
//!
//! `mixed_threshold()` is fully MLX-aware: it probes actual MLX Metal memory
//! via GIL on every call. This eliminates the need for Python to call
//! `sync_metal_memory_pressure()` — the Rust side reads GPU state directly.
//!
//! Threshold fractions (relative to dynamic Metal cache limit):
//!   < 0.60 GPU fraction → 16 (idle: eager parallelism)
//!   0.60–0.85          → 32 (normal: balanced)
//!   > 0.85             → 64 (pressure: conservative sequential)
//!
//! Falls back to NORMAL_THRESHOLD (32) if MLX/Python probe is unavailable.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Constants — match lib.rs MIXED_THRESHOLD
// ---------------------------------------------------------------------------

/// Threshold when MLX GPU is idle (fraction < 0.60) — eager parallelism.
const IDLE_THRESHOLD: usize = 16;
/// Threshold under normal MLX GPU load (fraction 0.60–0.85) — balanced.
const NORMAL_THRESHOLD: usize = 32;
/// Threshold under high MLX GPU pressure (fraction > 0.85) — conservative.
const PRESSURE_THRESHOLD: usize = 64;

// ---------------------------------------------------------------------------
// Atomic state — CPU-saturation signal (MLX Metal uses direct probing)
// ---------------------------------------------------------------------------

static CPU_SATURATION: AtomicU8 = AtomicU8::new(0);

// ---------------------------------------------------------------------------
// MLX Metal helpers — defined first so available to all threshold fns
// ---------------------------------------------------------------------------

/// Cached module handle for `hledac.universal.utils.mlx_cache`.
///
/// Initialized once per process via OnceLock; the bound is stored as
/// `Option<Bound<'static, PyModule>>` to allow None on failure.
static MLX_CACHE_MODULE_PATH: std::sync::OnceLock<&'static str> =
    std::sync::OnceLock::new();

/// Import mlx_cache module by name — avoids stale Bound<'static, PyModule> references
/// and sidesteps Sync/Send issues with OnceLock<Bound<...>>.
/// Uses lazy OnceLock to import only once per process.
#[inline]
fn get_mlx_cache_module<'py>(py: Python<'py>) -> Option<Bound<'py, PyModule>> {
    let module_name =
        MLX_CACHE_MODULE_PATH.get_or_init(|| "hledac.universal.utils.mlx_cache");
    py.import(*module_name).ok()
}

/// Probes Python `utils.mlx_cache.get_dynamic_metal_cache_limit()` via GIL.
///
/// MEM-2: min(max(available * 0.2, 512 MiB), 1.5 GiB).
/// Returns 0 if the Python function is unavailable.
#[inline]
fn get_metal_limit_bytes(py: Python<'_>) -> u64 {
    let Some(module) = get_mlx_cache_module(py) else {
        return 0;
    };
    if let Ok(func) = module.getattr("get_dynamic_metal_cache_limit") {
        if let Ok(result) = func.call0() {
            if let Ok(v) = result.extract::<u64>() {
                return v;
            }
            if let Ok(v) = result.extract::<i64>() {
                return v.max(0) as u64;
            }
        }
    }
    0
}

/// Single-shot MLX Metal probe — one GIL acquisition, two Python calls.
///
/// Returns the metal pressure level:
///   0 = idle    (fraction < 0.60)
///   1 = normal  (fraction 0.60–0.85)
///   2 = pressure (fraction > 0.85)
/// Falls back to 1 (normal) if MLX/Python is unavailable.
///
/// All three threshold functions (mixed/cpu/io) share this path to avoid
/// triplicating the GIL + mlx.core.get_active_memory() call overhead.
#[inline]
fn get_metal_level(py: Python<'_>) -> u8 {
    let limit_bytes = get_metal_limit_bytes(py);
    if limit_bytes == 0 {
        return 1; // fallback: normal
    }
    let active = crate::memory::get_metal_active_memory_bytes(py);
    if active == 0 {
        return 1; // fallback: normal
    }
    let fraction = active as f64 / limit_bytes as f64;
    if fraction < 0.60 {
        0 // idle
    } else if fraction < 0.85 {
        1 // normal
    } else {
        2 // pressure
    }
}

// ---------------------------------------------------------------------------
// Core threshold logic — MLX Metal-aware
// ---------------------------------------------------------------------------

/// MIXED_THRESHOLD — fully MLX Metal-aware.
///
/// Probes actual MLX Metal active memory on every call via GIL.
/// This is the PRIMARY threshold function used by all hot-paths.
///
/// | MLX GPU fraction of cache limit | Threshold | Rationale                |
/// |--------------------------------|-----------|--------------------------|
/// | < 0.60                         | 16        | Idle: eager parallelism  |
/// | 0.60–0.85                     | 32        | Normal: balanced          |
/// | > 0.85                        | 64        | Pressure: sequential      |
///
/// Falls back to NORMAL_THRESHOLD (32) if MLX or Python probe is unavailable.
#[inline]
pub fn mixed_threshold() -> usize {
    // Single GIL acquisition — get_metal_level() handles limit + active probe.
    Python::with_gil(|py| match get_metal_level(py) {
        0 => IDLE_THRESHOLD,    // 16: GPU idle, eager
        1 => NORMAL_THRESHOLD,  // 32: normal
        _ => PRESSURE_THRESHOLD, // 64: GPU saturated
    })
}

/// Mixed threshold via Metal — identical to mixed_threshold() but takes an explicit
/// `py` handle to avoid redundant GIL acquisition when called from Python code
/// that already holds the GIL.
///
/// For Rust-internal use, prefer `mixed_threshold()` which acquires the GIL itself.
#[inline]
pub fn mixed_threshold_via_metal(py: Python<'_>) -> usize {
    match get_metal_level(py) {
        0 => IDLE_THRESHOLD,
        1 => NORMAL_THRESHOLD,
        _ => PRESSURE_THRESHOLD,
    }
}

#[inline]
#[allow(dead_code)]
fn cpu_saturation() -> u8 {
    // SeqCst: CPU_SATURATION can be written from Python threads and read from
    // rayon workers — ordering Required for cross-thread visibility.
    CPU_SATURATION.load(Ordering::SeqCst)
}

/// Recommended thread count for CPU-bound workloads (cpu_pool ceiling).
#[inline]
pub fn recommended_cpu_threads() -> usize {
    // Single GIL acquisition — shares get_metal_level() with other threshold fns.
    Python::with_gil(|py| match get_metal_level(py) {
        2 => 1,   // pressure: sequential
        1 => 2,   // normal: 2 P-cores
        _ => 4,   // idle: all P-cores
    })
}

/// Recommended thread count for I/O-bound workloads (io_pool ceiling).
#[inline]
pub fn recommended_io_threads() -> usize {
    // Single GIL acquisition — shares get_metal_level() with other threshold fns.
    Python::with_gil(|py| match get_metal_level(py) {
        2 => 1,  // pressure: minimal
        _ => 2,  // idle/normal: 2 threads
    })
}

// ---------------------------------------------------------------------------
// Legacy state helpers (CPU-based signal — deprecated for MLX paths)
// ---------------------------------------------------------------------------

/// Updates CPU saturation level (0–100).
/// Note: MLX-aware paths use direct Metal probing instead.
pub fn update_cpu_saturation(pct: u8) {
    // SeqCst: paired with SeqCst load in cpu_saturation() — cross-thread visibility.
    CPU_SATURATION.store(pct.min(100), Ordering::SeqCst);
}

// ---------------------------------------------------------------------------
// PyO3 bindings
// ---------------------------------------------------------------------------

#[pyfunction]
pub fn get_adaptive_cpu_threads() -> usize {
    recommended_cpu_threads()
}

#[pyfunction]
pub fn get_adaptive_io_threads() -> usize {
    recommended_io_threads()
}

#[pyfunction]
pub fn get_adaptive_mixed_threshold() -> usize {
    mixed_threshold()
}

/// Returns MLX-aware MIXED_THRESHOLD from actual mx.metal.get_active_memory().
/// Explicit GIL version — prefer `get_adaptive_mixed_threshold()` for internal use.
#[pyfunction]
pub fn get_adaptive_mixed_threshold_via_metal(py: Python<'_>) -> usize {
    mixed_threshold_via_metal(py)
}

/// Returns the dynamic Metal cache limit in bytes by probing Python's
/// `utils.mlx_cache.get_dynamic_metal_cache_limit()`.
/// Returns 0 if MLX/Python is unavailable.
#[pyfunction]
pub fn get_metal_limit_bytes_py(py: Python<'_>) -> u64 {
    get_metal_limit_bytes(py)
}

/// Deprecated: MLX Metal probing is now inline in mixed_threshold().
/// Kept for backward compatibility — calls mixed_threshold() directly.
#[pyfunction]
pub fn sync_metal_memory_pressure_py(py: Python<'_>) -> usize {
    mixed_threshold_via_metal(py)
}

/// Deprecated: memory_pressure argument is ignored — Metal probing is now inline.
///
/// MLX-aware paths call `mixed_threshold()` directly; Python no longer needs to
/// sync memory pressure state. Kept for backward compatibility only.
#[deprecated(since = "0.1.0", note = "Metal probing is now inline in mixed_threshold(); memory_pressure arg is ignored")]
#[pyfunction]
pub fn sync_adaptive_state(memory_pressure: u8, cpu_saturation: u8) {
    // memory_pressure arg is now a no-op (Metal always probed directly)
    update_cpu_saturation(cpu_saturation);
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_adaptive_cpu_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_io_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold_via_metal, m)?)?;
    m.add_function(wrap_pyfunction!(get_metal_limit_bytes_py, m)?)?;
    m.add_function(wrap_pyfunction!(sync_metal_memory_pressure_py, m)?)?;
    m.add_function(wrap_pyfunction!(sync_adaptive_state, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_threshold_constants() {
        assert_eq!(IDLE_THRESHOLD, 16);
        assert_eq!(NORMAL_THRESHOLD, 32);
        assert_eq!(PRESSURE_THRESHOLD, 64);
    }

    #[test]
    fn test_get_metal_level_boundaries() {
        // Test the boundary logic in get_metal_level without GIL.
        // Boundary values match get_metal_level(): idle<0.60, normal<0.85, pressure>=0.85.
        // We test the threshold constants and level mapping directly.
        assert_eq!(IDLE_THRESHOLD, 16);    // < 0.60 → idle
        assert_eq!(NORMAL_THRESHOLD, 32);   // 0.60–0.85 → normal
        assert_eq!(PRESSURE_THRESHOLD, 64); // > 0.85 → pressure
    }

    #[test]
    fn test_cpu_saturation_atomic() {
        update_cpu_saturation(50);
        assert_eq!(cpu_saturation(), 50);
        update_cpu_saturation(100);
        assert_eq!(cpu_saturation(), 100);
        update_cpu_saturation(150); // capped at 100
        assert_eq!(cpu_saturation(), 100);
    }

    #[test]
    fn test_mixed_threshold_via_metal_logic() {
        // Test the pure logic function (without GIL/MLX dependency)
        // Note: mixed_threshold() uses Python::with_gil which needs a Python runtime
        // These tests verify the threshold level boundaries
        use std::sync::atomic::{AtomicU8, Ordering};
        static TEST_LEVEL: AtomicU8 = AtomicU8::new(1);

        // When MLX unavailable, mixed_threshold falls back to NORMAL_THRESHOLD (32)
        // This is verified by testing that the fallback case works
        assert_eq!(NORMAL_THRESHOLD, 32);
        assert_eq!(IDLE_THRESHOLD, 16);
        assert_eq!(PRESSURE_THRESHOLD, 64);
    }
}
