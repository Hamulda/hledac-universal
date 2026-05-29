/// High-performance IOC extraction and URL normalization.
/// Uses OnceCell for one-time regex compilation (performance critical).

use crate::url_engine;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashSet;

/// Compiled regex patterns — initialized once, reused across all calls.
static IPV4_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b").unwrap()
});
static IPV6_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b").unwrap()
});
static DOMAIN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b").unwrap()
});
static MD5_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[a-fA-F0-9]{32}\b").unwrap()
});
static SHA1_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[a-fA-F0-9]{40}\b").unwrap()
});
static SHA256_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[a-fA-F0-9]{64}\b").unwrap()
});
static EMAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b").unwrap()
});
static CVE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\bCVE-\d{4}-\d{4,}\b").unwrap());
// WARNING: Do not add duplicate code here.
// TRACKING_PARAMS lives in url_engine.rs. All URL normalization now delegates to url_engine::normalize().

/// Register all IOC extraction functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_ioc_extract, m)?)?;
    m.add_function(wrap_pyfunction!(url_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_urls, m)?)?;
    m.add_function(wrap_pyfunction!(fast_ioc_extract_batch, m)?)?;
    m.add_function(wrap_pyfunction!(extract_iocs, m)?)?;
    m.add_function(wrap_pyfunction!(url_normalize_batch, m)?)?;
    m.add_function(wrap_pyfunction!(chi_square, m)?)?;
    m.add_function(wrap_pyfunction!(entropy, m)?)?;
    m.add_function(wrap_pyfunction!(batch_sha256, m)?)?;
    Ok(())
}

/// Fast IOC extraction from raw text using pre-compiled regex patterns.
/// Returns list of (ioc_value, ioc_type) tuples.
#[pyfunction]
fn fast_ioc_extract(text: &str) -> Vec<(String, String)> {
    let mut iocs: Vec<(String, String)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    // IPv4
    for cap in IPV4_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "ipv4".to_string()));
        }
    }
    // IPv6
    for cap in IPV6_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "ipv6".to_string()));
        }
    }
    // Domain
    for cap in DOMAIN_RE.find_iter(text) {
        let v = cap.as_str().to_lowercase();
        if seen.insert(v.clone()) {
            iocs.push((v, "domain".to_string()));
        }
    }
    // MD5
    for cap in MD5_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "md5".to_string()));
        }
    }
    // SHA1
    for cap in SHA1_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "sha1".to_string()));
        }
    }
    // SHA256
    for cap in SHA256_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "sha256".to_string()));
        }
    }
    // Email
    for cap in EMAIL_RE.find_iter(text) {
        let v = cap.as_str().to_lowercase();
        if seen.insert(v.clone()) {
            iocs.push((v, "email".to_string()));
        }
    }
    // CVE
    for cap in CVE_RE.find_iter(text) {
        let v = cap.as_str().to_string();
        if seen.insert(v.clone()) {
            iocs.push((v, "cve".to_string()));
        }
    }

    iocs
}

/// Alias for backwards compatibility.
#[pyfunction]
fn fast_ioc_extract_batch(text: &str) -> Vec<(String, String)> {
    fast_ioc_extract(text)
}

/// Public IOC extraction — delegates to fast_ioc_extract for DRY.
#[pyfunction]
pub fn extract_iocs(text: &str) -> Vec<(String, String)> {
    fast_ioc_extract(text)
}

/// URL normalizer — delegates to url_engine::normalize() for canonical form.
#[pyfunction]
fn url_normalize(url: &str) -> String {
    url_engine::normalize(url)
}

/// Alias for backwards compatibility.
#[pyfunction]
fn url_normalize_batch(url: &str) -> String {
    url_engine::normalize(url)
}

/// In-memory URL deduplication with normalization.
/// Returns unique URLs with normalized forms used for dedup.
#[pyfunction]
fn batch_dedup_urls(urls: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    urls.into_iter()
        .filter(|url| seen.insert(url_engine::normalize(url)))
        .collect()
}

/// Shannon entropy of byte data.
/// Returns value in bits (0.0 for empty, ~8.0 for random data).
#[pyfunction]
pub fn entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let mut counts = [0u64; 256];
    for &b in data {
        counts[b as usize] += 1;
    }
    let n = data.len() as f64;
    counts
        .iter()
        .filter(|&&c| c > 0)
        .map(|&c| {
            let p = c as f64 / n;
            -p * p.log2()
        })
        .sum()
}

/// Chi-square uniformity test for byte distribution.
/// Low value = uniform (encrypted/random), high = non-uniform.
#[pyfunction]
pub fn chi_square(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let mut counts = [0u64; 256];
    for &b in data {
        counts[b as usize] += 1;
    }
    let expected = data.len() as f64 / 256.0;
    counts
        .iter()
        .map(|&c| {
            let diff = c as f64 - expected;
            (diff * diff) / expected
        })
        .sum()
}

/// SHA256 hash each string — for fast dedup fingerprinting.
/// Returns list of hex-encoded SHA256 digests.
#[pyfunction]
pub fn batch_sha256(items: Vec<String>) -> Vec<String> {
    
    items
        .iter()
        .map(|s| sha256_hex(s.as_bytes()))
        .collect()
}

fn sha256_hex(data: &[u8]) -> String {
    use std::fmt::Write;
    use sha2::{Sha256, Digest};
    let mut hasher = Sha256::new();
    hasher.update(data);
    let result = hasher.finalize();
    let mut hex = String::with_capacity(64);
    for byte in result.iter() {
        write!(hex, "{:02x}", byte).unwrap();
    }
    hex
}