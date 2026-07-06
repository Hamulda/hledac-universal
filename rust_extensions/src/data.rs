//! Data module — DuckDB bridge for parallel graph traversal and async queries.
//!
//! This module provides DuckDB bindings for Hledac's data layer.
//! It can be extracted to a separate cdylib in the future for reduced .dylib size.
//!
//! ## Architecture
//!
//! | Submodule | Purpose | M1 8GB |
//! |-----------|---------|--------|
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

use pyo3::prelude::{Bound, PyModule, PyResult};

pub mod connection;
pub mod query;
pub mod graph_traverse;
pub mod cache;

/// Register all data module functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    connection::register_functions(m)?;
    query::register_functions(m)?;
    graph_traverse::register_functions(m)?;
    Ok(())
}
