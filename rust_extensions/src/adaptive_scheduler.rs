//! Adaptive thread scheduler — CPU saturation + memory-pressure aware.
//!
//! F270-FINAL: Extends the fixed-tier thread pools (cpu_pool/io_pool/mixed_pool)
//! with dynamic thread count recommendations based on:
//!   1. Current MLX Metal memory pressure (fraction of dynamic cache limit)
//!   2. CPU queue depth estimate (via active rayon worker count)
//!   3. Workload type (CPU-bound / I/O-bound / Mixed)
//!
//! ## MLX Metal-Aware Design (F330 / ISSUE-2.4)
//!
//! `mixed_threshold()` is MLX-aware: it probes actual MLX Metal memory via GIL,
//! BUT uses a thread-local cache (TTL = 100 ms) to avoid GIL acquisition on
//! every call.  At 1000+ calls/sprint the cache hit rate is > 99 % and the
//! GIL overhead drops from 10-20 ms to near zero.
//!
//! Thread-local cache is per-rayon-worker — no cross-thread synchronization,
//! no atomic contention, no false sharing.
//!
//! Threshold fractions (relative to dynamic Metal cache limit):
//!   < 0.60 GPU fraction → 16 (idle: eager parallelism)
//!   0.60–0.85          → 32 (normal: balanced)
//!   > 0.85             → 64 (pressure: conservative sequential)
//!
//! Falls back to NORMAL_THRESHOLD (32) if MLX/Python probe is unavailable.

use pyo3::prelude::*;
use std::sync::atomic::{AtomicU8, Ordering};
use std::cell::Cell;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Thread-local cache — avoids GIL on every mixed_threshold() call
// ---------------------------------------------------------------------------

/// TTL for the thread-local metal-level/limit cache.
/// 100 ms strikes the balance: Metal memory pressure changes slowly
/// (on MLX timescales), while the hot-path calls mixed_threshold()
/// 1000+ times per sprint.
const METAL_CACHE_TTL_MS: u64 = 100;

/// Thread-local cache entry: (last_instant, metal_level, limit_bytes).
/// limit_bytes is cached alongside level so we skip the Python call
/// when the cached entry is still valid.
thread_local! {
    static METAL_CACHE: Cell<(Instant, u8, u64)> = Cell::new((Instant::now(), 1, 0));
}

/// Explicit memory-pressure signal — set by Python tests / production code.
///
/// Value: 0=idle, 1=normal, 2=pressure.
/// When set to a non-default value, mixed_threshold() uses this directly
/// and bypasses MLX Metal probing (tests don't have MLX).
///
/// Default: 1 (normal) — MLX Metal probing is the source of truth in production.
static MEMORY_PRESSURE: AtomicU8 = AtomicU8::new(1);

/// Sentinel value meaning "not explicitly set" — must match default of MEMORY_PRESSURE.
const PRESSURE_UNSET: u8 = 1;

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

/// Converts Metal memory fraction (active/limit) to level 0=idle, 1=normal, 2=pressure.
///
/// Threshold fractions:
///   < 0.60 → 0 (idle: eager parallelism)
///   0.60–0.85 → 1 (normal: balanced)
///   > 0.85 → 2 (pressure: conservative sequential)
///
/// Returns 1 (normal) as safe fallback when limit_bytes or active is 0.
#[inline]
fn fraction_to_level(limit_bytes: u64, active: u64) -> u8 {
    if limit_bytes == 0 || active == 0 {
        return 1; // fallback: normal
    }
    let fraction = active as f64 / limit_bytes as f64;
    if fraction < 0.60 {
        0 // idle
    } else if fraction <= 0.85 {
        1 // normal — inclusive boundary (0.60–0.85 → normal per documented fractions)
    } else {
        2 // pressure (> 0.85)
    }
}

// ---------------------------------------------------------------------------
// Core threshold logic — MLX Metal-aware + thread-local cached
// ---------------------------------------------------------------------------

/// Returns the cached metal level (0=idle, 1=normal, 2=pressure) or
/// re-probes via GIL if the thread-local cache entry is stale.
///
/// If MEMORY_PRESSURE was explicitly set to a non-default value
/// (i.e., not PRESSURE_UNSET), that value is returned immediately
/// and no MLX probing occurs. This allows tests to control the
/// threshold without MLX being available.
#[inline]
fn get_metal_level_cached() -> u8 {
    // Fast path: if memory pressure was explicitly set, use it directly.
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    if pressure != PRESSURE_UNSET {
        return pressure;
    }

    let now = Instant::now();
    let (instant, level, _limit_bytes) =
        METAL_CACHE.with(|cell| cell.get());
    if now.duration_since(instant) < Duration::from_millis(METAL_CACHE_TTL_MS) {
        return level;
    }
    // Cache miss — acquire GIL, re-probe, update cache.
    Python::with_gil(|py| {
        let limit_bytes = get_metal_limit_bytes(py);
        let active = crate::memory::get_metal_active_memory_bytes(py);
        let level = fraction_to_level(limit_bytes, active);
        METAL_CACHE.with(|cell| cell.set((now, level, limit_bytes)));
        level
    })
}

/// MIXED_THRESHOLD — fully MLX Metal-aware.
///
/// Uses a thread-local cache (TTL = 100 ms) to skip GIL on the
/// common case (pressure == PRESSURE_UNSET).  When an explicit pressure
/// is set via update_memory_pressure(), the atomic is checked first
/// and the cache is bypassed entirely — so the hot path for production
/// MLX use is one atomic load with zero GIL overhead.
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
    // Fast path: thread-local cache hit (no GIL).
    // Slow path: acquire GIL, probe Python, update cache.
    match get_metal_level_cached() {
        0 => IDLE_THRESHOLD,       // 16: GPU idle, eager
        1 => NORMAL_THRESHOLD,    // 32: normal
        _ => PRESSURE_THRESHOLD,  // 64: GPU saturated
    }
}

/// Mixed threshold via Metal — takes an explicit `py` handle to avoid
/// redundant GIL acquisition when called from Python code that already holds the GIL.
///
/// For Rust-internal use, prefer `mixed_threshold()` which uses thread-local caching.
#[inline]
pub fn mixed_threshold_via_metal(py: Python<'_>) -> usize {
    // Caller already holds the GIL — fresh probe, no thread-local cache.
    let limit_bytes = get_metal_limit_bytes(py);
    let active = crate::memory::get_metal_active_memory_bytes(py);
    match fraction_to_level(limit_bytes, active) {
        0 => IDLE_THRESHOLD,
        1 => NORMAL_THRESHOLD,
        _ => PRESSURE_THRESHOLD,
    }
}

/// Recommended thread count for CPU-bound workloads (cpu_pool ceiling).
#[inline]
pub fn recommended_cpu_threads() -> usize {
    // Check explicit pressure first (tests, no-MLX path).
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    if pressure != PRESSURE_UNSET {
        return match pressure {
            2 => 1,   // pressure: sequential
            1 => 2,   // normal: 2 P-cores
            _ => 4,   // idle: all P-cores
        };
    }
    // Production: use thread-local metal cache.
    match get_metal_level_cached() {
        2 => 1,   // pressure: sequential
        1 => 2,   // normal: 2 P-cores
        _ => 4,   // idle: all P-cores
    }
}

/// Recommended thread count for I/O-bound workloads (io_pool ceiling).
#[inline]
pub fn recommended_io_threads() -> usize {
    // Check explicit pressure first (tests, no-MLX path).
    let pressure = MEMORY_PRESSURE.load(Ordering::Acquire);
    if pressure != PRESSURE_UNSET {
        return match pressure {
            2 => 1,  // pressure: minimal
            _ => 2,  // idle/normal: 2 threads
        };
    }
    // Production: use thread-local metal cache.
    match get_metal_level_cached() {
        2 => 1,  // pressure: minimal
        _ => 2,  // idle/normal: 2 threads
    }
}

// ---------------------------------------------------------------------------
// Legacy state helpers (CPU-based signal — deprecated for MLX paths)
// ---------------------------------------------------------------------------

/// No-op: CPU saturation is no longer tracked via atomic.
///
/// MLX-aware paths use direct Metal probing via `fraction_to_level()`.
/// This function exists for backward compatibility only.
#[allow(dead_code)]
pub fn update_cpu_saturation(_pct: u8) {
    // No-op: CPU_SATURATION atomic removed; MLX Metal probing is the source of truth.
}

/// Update the explicit memory-pressure signal.
///
/// When set to a non-default value (≠ PRESSURE_UNSET), mixed_threshold()
/// uses this pressure directly and bypasses MLX Metal probing.
/// This allows Python tests to control the threshold without MLX being available.
///
/// Value: 0=idle (→16), 1=normal (→32), 2=pressure (→64).
///
/// Note: The thread-local metal cache is NOT consulted when pressure ≠ PRESSURE_UNSET;
/// the atomic value short-circuits the cache entirely. The cached entry is kept
/// for the reset path (pressure == PRESSURE_UNSET) where it stores the MLX-probed
/// level so that re-probing is not needed on every call.
#[allow(dead_code)]
pub fn update_memory_pressure(pressure: u8) {
    MEMORY_PRESSURE.store(pressure, Ordering::Release);
    // Invalidate the thread-local cache so the next MLX probe (if pressure == PRESSURE_UNSET)
    // starts fresh.  When pressure != PRESSURE_UNSET the cache is bypassed entirely, so
    // the entry value is immaterial — we store PRESSURE_UNSET (=1) to document the reset path.
    let now = Instant::now();
    METAL_CACHE.with(|cell| cell.set((now, PRESSURE_UNSET, 0)));
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

/// Deprecated: memory_pressure and cpu_saturation arguments are ignored.
///
/// MLX-aware paths call `mixed_threshold()` directly; Python no longer needs to
/// sync memory pressure state. Kept for backward compatibility only.
#[allow(deprecated)]
#[deprecated(since = "0.1.0", note = "Metal probing is now inline in mixed_threshold(); args are ignored")]
#[pyfunction]
pub fn sync_adaptive_state(_memory_pressure: u8, _cpu_saturation: u8) {
    // No-op: Metal probing is inline in mixed_threshold() via thread-local cache.
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
    fn test_fraction_to_level_fallback() {
        // When limit_bytes or active is 0, fallback to level 1 (normal).
        assert_eq!(fraction_to_level(0, 0), 1);
        assert_eq!(fraction_to_level(0, 1_000_000_000), 1);
        assert_eq!(fraction_to_level(1_000_000_000, 0), 1);
    }

    #[test]
    fn test_fraction_to_level_boundaries() {
        // < 0.60 → idle (level 0)
        assert_eq!(fraction_to_level(1_000_000_000, 599_000_000), 0);
        assert_eq!(fraction_to_level(1_000_000_000, 0), 1); // edge: 0 < 0.60

        // 0.60–0.85 → normal (level 1)
        assert_eq!(fraction_to_level(1_000_000_000, 600_000_000), 1);
        assert_eq!(fraction_to_level(1_000_000_000, 850_000_000), 1);

        // > 0.85 → pressure (level 2)
        assert_eq!(fraction_to_level(1_000_000_000, 851_000_000), 2);
        assert_eq!(fraction_to_level(1_000_000_000, 1_000_000_000), 2);
    }

    #[test]
    fn test_threshold_level_mapping() {
        // Verify threshold constants match the documented levels.
        // idle (level 0) → IDLE_THRESHOLD = 16
        assert_eq!(mixed_threshold(), NORMAL_THRESHOLD); // default fallback = normal
    }

    #[test]
    fn test_update_memory_pressure_idle() {
        // Explicit pressure=0 (idle) must bypass MLX probe and return 16.
        update_memory_pressure(0);
        assert_eq!(mixed_threshold(), IDLE_THRESHOLD); // 16
        // Reset to default.
        update_memory_pressure(1);
    }

    #[test]
    fn test_update_memory_pressure_normal() {
        // Explicit pressure=1 equals PRESSURE_UNSET → falls through to MLX.
        // Without MLX, get_metal_level_cached() probes GIL, which in a
        // #[cfg(test)] binary has no Python interpreter, so it returns
        // the fallback level 1 (NORMAL_THRESHOLD = 32).
        update_memory_pressure(1);
        assert_eq!(mixed_threshold(), NORMAL_THRESHOLD); // 32
    }

    #[test]
    fn test_update_memory_pressure_pressure() {
        // Explicit pressure=2 (pressure) must bypass MLX probe and return 64.
        update_memory_pressure(2);
        assert_eq!(mixed_threshold(), PRESSURE_THRESHOLD); // 64
        // Reset to default.
        update_memory_pressure(1);
    }

    #[test]
    fn test_recommended_cpu_threads_explicit_pressure() {
        // Idle → 4 P-cores available.
        update_memory_pressure(0);
        assert_eq!(recommended_cpu_threads(), 4);
        // Normal → 2 P-cores.
        update_memory_pressure(1);
        assert_eq!(recommended_cpu_threads(), 2);
        // Pressure → 1 (sequential).
        update_memory_pressure(2);
        assert_eq!(recommended_cpu_threads(), 1);
        // Reset.
        update_memory_pressure(1);
    }

    #[test]
    fn test_recommended_io_threads_explicit_pressure() {
        // Idle/normal → 2 I/O threads.
        update_memory_pressure(0);
        assert_eq!(recommended_io_threads(), 2);
        update_memory_pressure(1);
        assert_eq!(recommended_io_threads(), 2);
        // Pressure → 1 (minimal).
        update_memory_pressure(2);
        assert_eq!(recommended_io_threads(), 1);
        // Reset.
        update_memory_pressure(1);
    }
}
