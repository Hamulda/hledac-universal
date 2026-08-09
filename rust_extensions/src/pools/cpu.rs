//! # CPU Pool Module
//!
//! CPU-bound thread pool facade that delegates to `elastic_pool`.
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
pub fn default_threads() -> usize {
    4 // Default P-core count
}
