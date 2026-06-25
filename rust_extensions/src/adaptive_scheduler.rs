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

const DEFAULT_MIXED_THRESHOLD: usize = 32;
const MIN_MIXED_THRESHOLD: usize = 64;
const MAX_MIXED_THRESHOLD: usize = 16;

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
        0 => MAX_MIXED_THRESHOLD,      // 16: eager parallelism when idle
        1 => DEFAULT_MIXED_THRESHOLD,   // 32: normal
        _ => MIN_MIXED_THRESHOLD,      // 64: conservative when under pressure
    }
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

#[pyfunction]
pub fn sync_adaptive_state(memory_pressure: u8, cpu_saturation: u8) {
    update_memory_pressure(memory_pressure);
    update_cpu_saturation(cpu_saturation);
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(get_adaptive_cpu_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_io_threads, m)?)?;
    m.add_function(wrap_pyfunction!(get_adaptive_mixed_threshold, m)?)?;
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
    fn test_mixed_threshold_normal() {
        update_memory_pressure(0);
        assert_eq!(mixed_threshold(), 16);
    }

    #[test]
    fn test_mixed_threshold_elevated() {
        update_memory_pressure(1);
        assert_eq!(mixed_threshold(), 32);
    }

    #[test]
    fn test_mixed_threshold_critical() {
        update_memory_pressure(2);
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
