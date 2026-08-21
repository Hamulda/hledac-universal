//! # Thread Pool Module
//!
//! Unified thread pool management with adaptive sizing based on workload and system resources.
//!
//! ## MODERN-34: P/E Core Affinity Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────────┐
//! │                    pools::PoolManager                          │
//! │                  (Unified Pool Interface)                        │
//! └──────────────────────────┬──────────────────────────────────────┘
//!                            │
//!         ┌──────────────────┼──────────────────┐
//!         ▼                  ▼                  ▼
//! ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐
//! │   pools::cpu  │  │   pools::io   │  │  pools::mixed   │
//! │  P-cores ★   │  │  E-cores ★   │  │  P-cores ★     │
//! │  USER_INIT   │  │  UTILITY     │  │  Adaptive       │
//! └───────────────┘  └───────────────┘  └─────────────────┘
//!                            │
//! ┌───────────────────────────────────────────────────────────────┐
//! │              topology::TopologyInfo                            │
//! │   (Cached perflevel0/1 at startup)                           │
//! └───────────────────────────────────────────────────────────────┘
//! ```
//!
//! ★ = QoS class enforced per thread via `thread_policy_set` Mach API
//!
//! ## MODERN-34: Core-to-Workload Mapping
//!
//! | Pool | Cores | QoS | Workloads |
//! |------|-------|-----|-----------|
//! | cpu | P-cores | USER_INITIATED (0x19) | Aho-Corasick, deobfuscate, SIMD, MLX inference |
//! | io | E-cores | UTILITY (0x11) | DNS, HTTP, QUIC, DuckDB, WAL |
//! | mixed | P-cores | USER_INITIATED | IOC extract, URL ops (adaptive) |
//!
//! ## M1 8GB Thread Budget (MODERN-33)
//!
//! | Component | Threads | Cores | QoS |
//! |-----------|---------|-------|-----|
//! | cpu_pool | 3 | P-cores | USER_INITIATED |
//! | io_pool | 2 | E-cores | UTILITY |
//! | mixed_pool | 1 | P-cores | USER_INITIATED |
//! | dispatchers | 2 | E-cores | UTILITY |
//! | **Total** | **8** | 4P + 4E | - |
//!
//! ## Safety Features
//!
//! - MAX_TOTAL_THREADS = 8 enforced globally
//! - Memory-pressure aware thresholds (adaptive_scheduler)
//! - Elastic resizing without restart
//! - Graceful OOM fallback (1-thread minimum)

pub mod cpu;
pub mod elastic;
pub mod io;
pub mod mixed;

// Re-export commonly used pool operations
pub use cpu::{cpu_pool, cpu_pool_threads, resize_cpu_pool};
pub use elastic::{get_pool_metrics, PoolMetrics, PoolPhase};
pub use io::{io_pool, io_pool_threads, resize_io_pool};
pub use mixed::{mixed_pool, mixed_threshold};

/// Unified interface for thread pools.
///
/// This trait allows polymorphic pool usage and easier testing via mock pools.
pub trait ThreadPool: Send + Sync {
    /// Execute a closure on the pool.
    fn execute<F>(&self, f: F)
    where
        F: FnOnce() + Send + 'static;

    /// Get current number of threads.
    fn thread_count(&self) -> usize;

    /// Get pool name for logging.
    fn name(&self) -> &'static str;
}

/// Available pool kinds for unified operations.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PoolKind {
    /// CPU-bound pool (P-cores)
    Cpu,
    /// I/O-bound pool
    Io,
    /// Mixed workload pool
    Mixed,
}

impl PoolKind {
    /// Get default thread count for this pool kind.
    pub fn default_threads(&self) -> usize {
        match self {
            PoolKind::Cpu => 4,   // P-core count
            PoolKind::Io => 2,    // DuckDB ceiling
            PoolKind::Mixed => 2, // Adaptive
        }
    }

    /// Get maximum thread count for this pool kind.
    pub fn max_threads(&self) -> usize {
        match self {
            PoolKind::Cpu => 4,
            PoolKind::Io => 4,
            PoolKind::Mixed => 2,
        }
    }
}

/// Aggregated statistics from all pools.
#[derive(Debug, Clone, Default)]
pub struct PoolStats {
    /// Total number of pools
    pub pool_count: usize,
    /// Total threads across all pools
    pub total_threads: usize,
    /// Per-pool details
    pub pools: Vec<PoolInfo>,
}

/// Information about a single pool.
#[derive(Debug, Clone)]
pub struct PoolInfo {
    /// Pool kind
    pub kind: PoolKind,
    /// Current thread count
    pub threads: usize,
    /// Pool name
    pub name: &'static str,
}

impl PoolStats {
    /// Create empty stats.
    pub fn new() -> Self {
        Self::default()
    }

    /// Collect stats from all pools.
    pub fn collect() -> Self {
        let mut pools = Vec::new();

        // CPU pool
        pools.push(PoolInfo {
            kind: PoolKind::Cpu,
            threads: cpu::cpu_pool().current_num_threads(),
            name: "hledac-cpu",
        });

        // I/O pool
        pools.push(PoolInfo {
            kind: PoolKind::Io,
            threads: io::io_pool().current_num_threads(),
            name: "hledac-io",
        });

        let total_threads: usize = pools.iter().map(|p| p.threads).sum();

        Self {
            pool_count: pools.len(),
            total_threads,
            pools,
        }
    }
}

/// Get aggregated pool statistics.
pub fn stats() -> PoolStats {
    PoolStats::collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pool_kinds() {
        assert_eq!(PoolKind::Cpu.default_threads(), 4);
        assert_eq!(PoolKind::Io.default_threads(), 2);
        assert_eq!(PoolKind::Mixed.default_threads(), 2);
    }

    #[test]
    fn test_pool_stats_collect() {
        let stats = PoolStats::collect();
        assert_eq!(stats.pool_count, 2);
        assert!(stats.total_threads >= 2);
    }
}
