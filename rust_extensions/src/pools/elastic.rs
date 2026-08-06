//! # Elastic Pool Module
//!
//! Dynamic pool resizing facade that delegates to `elastic_pool`.
//!
//! ## Architecture
//!
//! Wraps cpu_pool and io_pool with Arc<RwLock<Option<ThreadPool>>>
//! for seamless runtime replacement.

pub use crate::elastic_pool::{
    resize_cpu_pool, resize_io_pool,
    get_cpu_pool, get_io_pool,
    get_cpu_pool_threads, get_io_pool_threads,
    get_total_active_threads,
    init_default_pools,
};

/// Pool phase for adaptive sizing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PoolPhase {
    /// Initial phase - balanced
    Boot,
    /// High I/O phase - fetch-heavy
    Active,
    /// CPU-intensive phase - synthesis
    Synthesis,
    /// Cleanup phase
    Windup,
}

impl PoolPhase {
    /// Get CPU pool size for this phase.
    pub fn cpu_threads(&self) -> usize {
        match self {
            PoolPhase::Boot => 4,
            PoolPhase::Active => 4,
            PoolPhase::Synthesis => 6,
            PoolPhase::Windup => 4,
        }
    }

    /// Get I/O pool size for this phase.
    pub fn io_threads(&self) -> usize {
        match self {
            PoolPhase::Boot => 2,
            PoolPhase::Active => 4,
            PoolPhase::Synthesis => 2,
            PoolPhase::Windup => 2,
        }
    }
}

/// Pool metrics for monitoring.
#[derive(Debug, Clone, Default)]
pub struct PoolMetrics {
    /// Current CPU pool thread count
    pub cpu_threads: usize,
    /// Current I/O pool thread count
    pub io_threads: usize,
    /// Total threads
    pub total_threads: usize,
}

/// Get current pool metrics.
pub fn get_pool_metrics() -> PoolMetrics {
    let cpu_threads = get_cpu_pool_threads();
    let io_threads = get_io_pool_threads();
    
    PoolMetrics {
        cpu_threads,
        io_threads,
        total_threads: cpu_threads + io_threads,
    }
}
