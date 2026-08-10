//! # CPU Pool Module
//!
//! CPU-bound thread pool facade that delegates to `elastic_pool`.
//!
//! ## MODERN-34: P/E Core Affinity
//!
//! This pool is **P-core exclusive**. Workers run on performance cores via:
//! - `QOS_CLASS_USER_INITIATED (0x19)` — scheduler hint for P-cores
//! - `thread_perfpolicy perf_class=P` — explicit perf-level preference
//!
//! ## P-Core Workloads
//!
//! | Workload | Examples | Notes |
//! |----------|----------|-------|
//! | SIMD | Aho-Corasick, deobfuscate | CPU-intensive pattern matching |
//! | MLX | mlx_bridge inference | GPU coordination on CPU |
//! | Graph | graph_traverse DuckPGQ | Kuzu traversal |
//!
//! ## M1 8GB Thread Budget
//!
//! - cpu_pool: 4 P-cores (USER_INITIATED)
//! - See `elastic_pool` for global budget enforcement
//!
//! ## Design
//!
//! This module is a thin facade that delegates to `elastic_pool::get_cpu_pool()`.
//! It provides a cleaner API surface for the pools/ module group while
//! maintaining backward compatibility with existing callers.

pub use crate::elastic_pool::{cpu_pool, get_cpu_pool, get_cpu_pool_threads, resize_cpu_pool};

/// Alias for get_cpu_pool_threads (common name).
pub fn cpu_pool_threads() -> usize {
    crate::elastic_pool::get_cpu_pool_threads()
}

/// Get default CPU pool thread count (P-cores).
///
/// MODERN-34: Returns topology::p_core_count() for accurate P-core count.
pub fn default_threads() -> usize {
    crate::topology::p_core_count()
}
