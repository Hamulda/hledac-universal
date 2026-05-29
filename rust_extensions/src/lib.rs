//! hledac-rust-extensions - High-performance Rust extensions for hledac OSINT platform.
//!
//! Provides native-speed implementations of:
//! - Aho-Corasick multi-pattern matching
//! - BloomFilter for URL deduplication
//! - Rolling hash for content fingerprinting
//! - IOC extraction and URL normalization
//! - IOC and relation deduplication sets
//! - URL normalization and fingerprinting

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
    // bloom::bloom_check_batch intentionally NOT exposed — see RUST_INTEGRATION_ROADMAP_2026.md
    m.add_class::<ioc_dedup::IocSet>()?;
    m.add_class::<ioc_dedup::RelSet>()?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;
    m.add_class::<rolling_hash::FastHasher>()?;

    // URL dedup via FNV-1a hashing (high-frequency: called on every fetch)
    m.add_class::<url_set::UrlSet>()?;
    ioc_extract::register_functions(m)?;

    // URL engine functions: normalize, fingerprint, strip_tracking_params
    m.add_function(wrap_pyfunction!(url_engine::normalize, m)?)?;
    m.add_function(wrap_pyfunction!(url_engine::fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(url_engine::strip_tracking_params, m)?)?;

    // xxHash3-64 for non-cryptographic content hashing (dedup keys, cache IDs)
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_64, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash, m)?)?;
    m.add_function(wrap_pyfunction!(xxhash_ext::batch_content_hash_hex, m)?)?;

    // SimHash for near-duplicate detection via Hamming distance
    m.add_function(wrap_pyfunction!(simhash_ext::compute_simhash, m)?)?;
    m.add_function(wrap_pyfunction!(simhash_ext::hamming_distance, m)?)?;
    m.add_function(wrap_pyfunction!(simhash_ext::batch_compute_simhash, m)?)?;
    m.add_function(wrap_pyfunction!(simhash_ext::is_near_duplicate, m)?)?;
    m.add_function(wrap_pyfunction!(simhash_ext::find_near_duplicates, m)?)?;

    Ok(())
}
