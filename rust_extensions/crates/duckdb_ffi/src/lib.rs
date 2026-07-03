//! DuckDB FFI bindings — parallel graph traversal, Arrow IPC
//!
//! | Module | Purpose | M1 8GB |
//! |--------|---------|---------|
//! | graph_traverse | Parallel DuckPGQ traversal | 2-thread ceiling |
//! | embedding_index | ANN HNSW index | 307 MB max |
//! | graph_cache | TinyLFU LRU cache | bounded |
//!
//! BUNDLED STATIC BUILD: DuckDB source compiles into .dylib (~25 MB).
//! Read-only connections safe across rayon worker threads.

use pyo3::prelude::*;

pub mod graph_traverse;
pub mod embedding_index;
pub mod graph_cache;
pub mod madvise;
pub mod compress;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register DuckDB FFI functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    graph_traverse::register_functions(m)?;
    m.add_class::<embedding_index::PyHNSWIndex>()?;
    m.add_class::<graph_cache::PyGraphLRUCache>()?;
    madvise::register_functions(m)?;
    compress::register_functions(m)?;
    Ok(())
}
