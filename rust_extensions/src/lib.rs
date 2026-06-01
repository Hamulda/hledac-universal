//! hledac-rust-extensions - High-performance Rust extensions for hledac OSINT platform.
//!
//! Provides native-speed implementations of:
//! - Aho-Corasick multi-pattern matching
//! - BloomFilter for URL deduplication
//! - Rolling hash for content fingerprinting
//! - IOC extraction and URL normalization
//! - IOC deduplication (cross-sprint persistence)
//! - xxHash3-64 for non-cryptographic hashing
//! - SimHash for near-duplicate document detection

use pyo3::prelude::*;

pub mod aho_corasick;
pub mod bloom;
pub mod ioc_dedup;
pub mod ioc_extract;
pub mod rolling_hash;
pub mod simhash_ext;
pub mod url_engine;
pub mod url_set;
pub mod xxhash_ext;

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<bloom::BloomFilter>()?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;
    m.add_class::<rolling_hash::FastHasher>()?;

    // URL dedup via FNV-1a hashing
    m.add_class::<url_set::UrlSet>()?;

    // IOC extraction + URL normalization
    ioc_extract::register_functions(m)?;
    url_engine::register_functions(m)?;

    // IOC deduplication store (cross-sprint persistence)
    ioc_dedup::register_class(m)?;

    // SimHash for near-duplicate document detection
    simhash_ext::register_functions(m)?;

    // xxHash3-64 for non-cryptographic content hashing (dedup keys, cache IDs)
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_64, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex, m)?)?;
    m.add_class::<xxhash_ext::StreamHasher64>()?;

    Ok(())
}
