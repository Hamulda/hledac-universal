//! # I/O Pool Module
//!
//! I/O-bound thread pool facade that delegates to `elastic_pool`.
//!
//! ## MODERN-34: P/E Core Affinity
//!
//! This pool is **E-core exclusive**. Workers run on efficiency cores via:
//! - `QOS_CLASS_UTILITY (0x11)` — scheduler hint for E-cores
//! - `thread_perfpolicy perf_class=E` — explicit perf-level preference
//!
//! ## E-Core Workloads
//!
//! | Workload | Examples | Notes |
//! |----------|----------|-------|
//! | Network I/O | DNS, HTTP, QUIC | Shared tokio runtime |
//! | File I/O | DuckDB, WAL | Evidence log writes |
//! | Telemetry | telemetry_agg | Background metrics collection |
//!
//! ## M1 8GB Thread Budget
//!
//! - io_pool: 2 E-cores (UTILITY) — leaves P-cores for CPU work
//! - See `elastic_pool` for global budget enforcement
//!
//! ## Design
//!
//! This module is a thin facade that delegates to `elastic_pool::get_io_pool()`.
//! It provides a cleaner API surface for the pools/ module group while
//! maintaining backward compatibility with existing callers.

pub use crate::elastic_pool::{get_io_pool, get_io_pool_threads, io_pool, resize_io_pool};

/// Alias for get_io_pool_threads (common name).
pub fn io_pool_threads() -> usize {
    crate::elastic_pool::get_io_pool_threads()
}

/// Get default I/O pool thread count.
///
/// MODERN-34: Returns E-core count based on topology detection.
pub fn default_threads() -> usize {
    2 // Conservative default for E-cores
}
