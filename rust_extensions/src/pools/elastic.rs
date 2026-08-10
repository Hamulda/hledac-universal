//! # Elastic Pool Module
//!
//! Dynamic pool resizing facade that delegates to `elastic_pool`.
//!
//! ## Architecture
//!
//! Wraps cpu_pool and io_pool with Arc<RwLock<Option<ThreadPool>>>
//! for seamless runtime replacement.

pub use crate::elastic_pool::{
    get_cpu_pool, get_cpu_pool_threads, get_io_pool, get_io_pool_threads, get_total_active_threads,
    init_default_pools, resize_cpu_pool, resize_io_pool,
};

// MODERN-34: Import MAX_TOTAL_THREADS for budget-safe sizing
use crate::adaptive_scheduler::MAX_TOTAL_THREADS;

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
    ///
    /// MODERN-34 FIX: All values clamped to respect MAX_TOTAL_THREADS=8 budget.
    /// Synthesis uses 4 P-cores max (leaves room for io + dispatchers).
    pub fn cpu_threads(&self) -> usize {
        match self {
            PoolPhase::Boot => 4,
            PoolPhase::Active => 4,
            PoolPhase::Synthesis => 4, // Was 6 — violated MAX_TOTAL_THREADS budget
            PoolPhase::Windup => 3,
        }
        .min(MAX_TOTAL_THREADS)
    }

    /// Get I/O pool size for this phase.
    ///
    /// MODERN-34 FIX: Values adjusted to respect global budget.
    /// cpu_threads + io_threads + dispatchers(3) + mixed(max 2) <= 8
    pub fn io_threads(&self) -> usize {
        match self {
            PoolPhase::Boot => 2,
            PoolPhase::Active => 2, // Was 4 — violated budget with cpu=4
            PoolPhase::Synthesis => 2,
            PoolPhase::Windup => 1,
        }
        .min(MAX_TOTAL_THREADS.saturating_sub(self.cpu_threads() + 3 + 2)) // Reserve for dispatchers + mixed
        .max(1) // At least 1 IO thread
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
