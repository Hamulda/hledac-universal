//! URL normalization and classification — canonicalization, dedup keys, transport routing
//!
//! | Function | Purpose | GIL-free |
//! |----------|---------|----------|
//! | classify_url | Transport class (onion/i2p/clearnet) | ✅ |
//! | extract_host | Fast host extraction | ✅ |
//! | canonical_url | Full normalization | ✅ |
//! | url_dedup_hash | BLAKE3-64 dedup key | ✅ |
//!
//! Uses Rust `url` crate — replaces Python urllib.parse + regex

use pyo3::prelude::*;

pub mod url_ops;
pub mod url_engine;
pub mod url_set;

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register URL functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    url_ops::register_functions(m)?;
    url_engine::register_functions(m)?;

    // URL dedup via FNV-1a hashing — both in-memory and mmap-backed
    m.add_class::<url_set::MmapUrlSet>()?;
    m.add_class::<url_set::UrlSet>()?;

    Ok(())
}
