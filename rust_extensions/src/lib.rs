//! hledac-rust-extensions - High-performance Rust extensions for hledac OSINT platform.
//!
//! Provides native-speed implementations of:
//! - AhoCorasickMatcher: Multi-pattern string matching for IOC detection
//! - BloomFilter: Probabilistic URL deduplication
//! - RollingHashEngine: Rabin-Karp rolling hash for URL fingerprinting
//! - FastHasher: xxhash64 fast hashing
//! - fast_ioc_extract: Regex-based IOC extraction (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE)
//! - url_normalize: Canonical URL normalization
//! - bloom_check: Batch Bloom filter check for URL dedup pre-screening

use pyo3::prelude::*;

mod aho_corasick;
mod bloom;
pub mod ioc_extract;
mod rolling_hash;

#[pymodule]
fn hledac_rust_extensions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<aho_corasick::AhoCorasickMatcher>()?;
    m.add_class::<bloom::BloomFilter>()?;
    m.add_class::<rolling_hash::RollingHashEngine>()?;
    m.add_class::<rolling_hash::FastHasher>()?;

    // IOC extraction and URL normalization functions
    ioc_extract::register_functions(m)?;

    Ok(())
}