//! # I/O Pool Module
//!
//! I/O-bound thread pool facade that delegates to `elastic_pool`.
//!
//! ## Design
//!
//! This module is a thin facade that delegates to `elastic_pool::get_io_pool()`.
//! It provides a cleaner API surface for the pools/ module group while
//! maintaining backward compatibility with existing callers.

pub use crate::elastic_pool::{
    io_pool, get_io_pool, resize_io_pool, 
    get_io_pool_threads,
};

/// Alias for get_io_pool_threads (common name).
pub fn io_pool_threads() -> usize {
    crate::elastic_pool::get_io_pool_threads()
}

/// Get default I/O pool thread count.
pub fn default_threads() -> usize {
    2 // Default I/O thread count
}
