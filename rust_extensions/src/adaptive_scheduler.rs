//! Adaptive thread scheduler — CPU saturation + memory-pressure aware.
//!
//! F270-FINAL: Extends the fixed-tier thread pools (cpu_pool/io_pool/mixed_pool)
//! with dynamic thread count recommendations based on:
//!   1. Current memory pressure level (from memory module)
//!   2. CPU queue depth estimate (via active rayon worker count)
//!   3. Workload type (CPU-bound / I/O-bound / Mixed)
//!
//! Unlike the static pools (4/2/1-2 fixed threads), this module provides
//! advisory recommendations. Callers decide whether to use cpu_pool vs io_pool
//! based on workload type, but this module tells them HOW MANY threads
//! to use within that pool's ceiling.

use std::sync::atomic::{AtomicU8, Ordering};

// ---------------------------------------------------------------------------
// Constants — match lib.rs MIXED_THRESHOLD
// ---------------------------------------------------------------------------

/// Threshold when system is idle (pressure=0) — eager parallelism.
const IDLE_THRESHOLD: usize = 16;
/// Threshold under normal memory pressure (pressure=1) — balanced.
const NORMAL_THRESHOLD: usize = 32;
/// Threshold under high memory pressure (pressure>=2) — conservative, sequential.
const PRESSURE_THRESHOLD: usize = 64;

// ---------------------------------------------------------------------------
// Atomic state
// ---------------------------------------------------------------------------

static CPU_SATURATION: AtomicU8 = AtomicU8::new(0);
static MEMORY_PRESSURE: AtomicU8 = AtomicU8::new(0);

// ---------------------------------------------------------------------------
// Core logic
// ---------------------------------------------------------------------------

#[inline]
fn memory_pressure() -> u8 {
    MEMORY_PRESSURE.load(Ordering::Relaxed)
}

#[inline]
#[allow(dead_code)]
fn cpu_saturation() -> u8 {
    CPU_SATURATION.load(Ordering::Relaxed)
}

/// Recommended thread count for CPU-bound workloads (cpu_pool ceiling).
#[inline]
pub fn recommended_cpu_threads() -> usize {
    match memory_pressure() {
        0 => 4,
        1 => 2,
        _ => 1,
    }
}

/// Recommended thread count for I/O-bound workloads (io_pool ceiling).
#[inline]
pub fn recommended_io_threads() -> usize {
    match memory_pressure() {
        0 => 2,
        _ => 1,
    }
}

/// MIXED_THRESHOLD for switching mixed_pool from 1→2 threads.
/// Dynamic: higher threshold under pressure = more sequential.
#[inline]
pub fn mixed_threshold() -> usize {
    match memory_pressure() {
        0 => IDLE_THRESHOLD,       // 16: eager parallelism when idle
        1 => NORMAL_THRESHOLD,     // 32: normal
        _ => PRESSURE_THRESHOLD,   // 64: conservative when under pressure
    }
}

/// Fraction-based thresholds relative to dynamic Metal cache limit (MEM-2).
///
/// Uses `get_metal_limit_bytes()` (probes Python `get_dynamic_metal_cache_limit()`)
/// to obtain the runtime cache ceiling, then computes fraction of that ceiling
/// rather than using absolute GiB constants.
///
/// | Metal active fraction of cache limit | Threshold | Rationale              |
/// |--------------------------------------|-----------|------------------------|
/// | < 0.60                               | 16        | Idle: eager parallelism|
/// | 0.60–0.85                            | 32        | Normal: balanced       |
/// | > 0.85                               | 64        | Pressure: sequential   |
///
/// Falls back to NORMAL_THRESHOLD (32) if MLX or Python probe is unavailable.
#[inline]
pub fn mixed_threshold_via_metal(py: Python<'_>) -> usize {
    let limit_bytes = get_metal_limit_bytes(py);
    if limit_bytes == 0 {
        return NORMAL_THRESHOLD; // fail-safe
    }
    let active = crate::memory::get_metal_active_memory_bytes(py);
    let fraction = active as f64 / limit_bytes as f64;
    if fraction < 0.60 {
        IDLE_THRESHOLD        // 16: eager parallelism when GPU idle
    } else if fraction < 0.85 {
        NORMAL_THRESHOLD      // 32: normal
    } else {
        PRESSURE_THRESHOLD    // 64: conservative when GPU saturated
    }
}

/// Probes Python `utils.mlx_cache.get_dynamic_metal_cache_limit()` via GIL.
///
/// This is the MEM-2 dynamic Metal cache ceiling computed from available system
/// memory: min(max(available * 0.2, 512 MiB), 1.5 GiB).
/// Returns 0 if the Python function is unavailable.
fn get_metal_limit_bytes(py: Python<'_>) -> u64 {
    if let Ok(module) = py.import("hledac.universal.utils.mlx_cache") {
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
    }
    0 // fail-safe: caller must handle
}

/// Syncs MLX Metal memory pressure → adaptive_scheduler state → returns new threshold.
///
/// Uses fraction-based thresholds relative to dynamic Metal cache limit (same as
/// mixed_threshold_via_metal). Reads current MLX Metal active memory, derives
/// pressure level (0/1/2), updates MEMORY_PRESSURE atomic, and returns threshold.
#[inline]
pub fn sync_metal_memory_pressure(py: Python<'_>) -> usize {
    let limit_bytes = get_metal_limit_bytes(py);
    let active = crate::memory::get_metal_active_memory_bytes(py);
    let level = if limit_bytes > 0 {
        let fraction = active as f64 / limit_bytes as f64;
        if fraction < 0.60 {
            0
        } else if fraction < 0.85 {
            1
        } else {
            2
        }
    } else {
        1 // default to normal when limit unavailable
    };
    update_memory_pressure(level);
    mixed_threshold()
}

pub fn update_memory_pressure(level: u8) {
    MEMORY_PRESSURE.store(level.min(2), Ordering::Relaxed);
}

pub fn update_cpu_saturation(pct: u8) {
    CPU_SATURATION.store(pct.min(100), Ordering::Relaxed);
}

// ---------------------------------------------------------------------------
// PyO3 bindings
// ---------------------------------------------------------------------------

use pyo3::prelude::*;

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
/// Does NOT update MEMORY_PRESSURE atomic — pure probe.
#[pyfunction]
pub fn get_adaptive_mixed_threshold_via_metal(py: Python<'_>) -> usize {
    mixed_threshold_via_metal(py)
}

/// Reads MLX Metal memory, syncs pressure to adaptive_scheduler, returns threshold.
/// One-shot: updates MEMORY_PRESSURE atomic + returns new threshold.
#[pyfunction]
pub fn sync_metal_memory_pressure_py(py: Python<'_>) -> usize {
    sync_metal_memory_pressure(py)
}

#[pyfunction]
pub fn sync_adaptive_state(memory_pressure: u8, cpu_saturation: u8) {
    update_memory_pressure(memory_pressure);
    update_cpu_saturation(cpu_saturation);
}

/// Returns the dynamic Metal cache limit in bytes by probing Python's
/// `utils.mlx_cache.get_dynamic_metal_cache_limit()`.
/// Returns 0 if MLX/Python is unavailable.
#[pyfunction]
pub fn get_metal_limit_bytes_py(py: Python<'_>) -> u64 {
    get_metal_limit_bytes(py)
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_adaptive_cpu_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_io_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold_via_metal, m)?)?;
    m.add_function(wrap_pyfunction!(sync_metal_memory_pressure_py, m)?)?;
    m.add_function(wrap_pyfunction!(sync_adaptive_state, m)?)?;
    m.add_function(wrap_pyfunction!(get_metal_limit_bytes_py, m)?)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mixed_threshold_idle() {
        update_memory_pressure(0);
        assert_eq!(mixed_threshold(), IDLE_THRESHOLD);
        assert_eq!(mixed_threshold(), 16);
    }

    #[test]
    fn test_mixed_threshold_normal() {
        update_memory_pressure(1);
        assert_eq!(mixed_threshold(), NORMAL_THRESHOLD);
        assert_eq!(mixed_threshold(), 32);
    }

    #[test]
    fn test_mixed_threshold_pressure() {
        update_memory_pressure(2);
        assert_eq!(mixed_threshold(), PRESSURE_THRESHOLD);
        assert_eq!(mixed_threshold(), 64);
    }

    #[test]
    fn test_cpu_threads() {
        update_memory_pressure(0);
        assert_eq!(recommended_cpu_threads(), 4);
        update_memory_pressure(1);
        assert_eq!(recommended_cpu_threads(), 2);
        update_memory_pressure(2);
        assert_eq!(recommended_cpu_threads(), 1);
    }
}
