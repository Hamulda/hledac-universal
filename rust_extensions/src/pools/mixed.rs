//! # Mixed Pool Module
//!
//! Adaptive thread pool facade for mixed CPU/I/O workloads.
//!
//! ## Design
//!
//! This module delegates to `adaptive_scheduler::mixed_threshold()` for
//! adaptive batch sizing and provides `mixed_pool()` for workload dispatch.

pub use crate::adaptive_scheduler::mixed_threshold;

/// Get mixed pool based on batch size.
///
/// Returns 1-thread pool when n < threshold (avoids spawn overhead).
/// Returns 2-thread pool when n >= threshold (parallel speedup).
pub fn mixed_pool(n_items: usize) -> &'static rayon::ThreadPool {
    crate::mixed_pool(n_items)
}

/// Get current mixed threshold value.
pub fn mixed_threshold_value() -> usize {
    mixed_threshold()
}
