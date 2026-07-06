//! DuckDB Bridge — Standalone DuckDB connection and query execution for Hledac.
//!
//! This crate provides DuckDB bindings for parallel graph traversal and async
//! query execution. It is designed to be optionally compiled as a separate cdylib
//! to avoid linking DuckDB C++ into the main extension when DuckDB features
//! are not needed.
//!
//! ## Architecture
//!
//! | Module | Purpose | M1 8GB |
//! |--------|---------|---------|
//! | connection | Thread-local DuckDB connections | 2-thread ceiling |
//! | query | Async/sync query execution | bounded |
//! | graph_traverse | Parallel DuckPGQ traversal | 2-thread ceiling |
//! | cache | TinyLFU LRU cache | bounded |
//!
//! ## M1 8GB Constraints
//!
//! - DuckDB bundled static build compiles ~25 MB C++ into the .dylib
//! - Thread-local connections reuse pools to minimize memory
//! - Read-only connections eliminate WAL overhead
//! - PRAGMA threads=1 per connection (parallelization is across workers, not inside DuckDB)

use pyo3::prelude::*;

// Re-export core types for use by other modules
pub mod connection;
pub mod query;
pub mod graph_traverse;
pub mod cache;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register DuckDB data functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    connection::register_functions(m)?;
    query::register_functions(m)?;
    graph_traverse::register_functions(m)?;
    cache::register_functions(m)?;
    Ok(())
}
