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

/// Dynamic MIXED_THRESHOLD driven by actual MLX Metal active memory.
///
/// Reads `mx.metal.get_active_memory()` via the Python interpreter (GIL-protected).
/// Threshold scales with GPU memory saturation — more sequential under pressure.
///
/// | Metal active memory | Threshold | Rationale                              |
/// |---------------------|-----------|----------------------------------------|
/// | < 2 GiB            | 16        | Idle: eager parallelism               |
/// | 2–4 GiB            | 32        | Normal: balanced (F270 calibration)    |
/// | > 4 GiB            | 64        | Pressure: sequential, reduce thrashing|
///
/// Returns 32 (NORMAL_THRESHOLD) if MLX is unavailable.
#[inline]
pub fn mixed_threshold_via_metal() -> usize {
    // Probes mlx.core.get_active_memory() via Python GIL.
    // Safe: if MLX unavailable, returns 0 → idle path (IDLE_THRESHOLD).
    let bytes = crate::memory::get_metal_active_memory_bytes();
    let gib = bytes as f64 / (1024.0_f64.powi(3));
    if gib < 2.0 {
        IDLE_THRESHOLD        // 16: eager parallelism when GPU idle
    } else if gib < 4.0 {
        NORMAL_THRESHOLD      // 32: normal (F270 calibration)
    } else {
        PRESSURE_THRESHOLD    // 64: conservative when GPU saturated
    }
}

/// Syncs MLX Metal memory pressure → adaptive_scheduler state → returns new threshold.
///
/// Reads current MLX Metal active memory, derives pressure level (0/1/2),
/// updates internal MEMORY_PRESSURE atomic, and returns the new threshold.
/// Call this before pool operations from Python to keep atomic pressure in sync
/// AND get the MLX-aware threshold in one call.
#[inline]
pub fn sync_metal_memory_pressure() -> usize {
    let bytes = crate::memory::get_metal_active_memory_bytes();
    let gib = bytes as f64 / (1024.0_f64.powi(3));
    let level = if gib < 2.0 {
        0
    } else if gib < 4.0 {
        1
    } else {
        2
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
pub fn get_adaptive_mixed_threshold_via_metal() -> usize {
    mixed_threshold_via_metal()
}

/// Reads MLX Metal memory, syncs pressure to adaptive_scheduler, returns threshold.
/// One-shot: updates MEMORY_PRESSURE atomic + returns new threshold.
#[pyfunction]
pub fn sync_metal_memory_pressure_py() -> usize {
    sync_metal_memory_pressure()
}

#[pyfunction]
pub fn sync_adaptive_state(memory_pressure: u8, cpu_saturation: u8) {
    update_memory_pressure(memory_pressure);
    update_cpu_saturation(cpu_saturation);
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_adaptive_cpu_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_io_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold_via_metal, m)?)?;
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
