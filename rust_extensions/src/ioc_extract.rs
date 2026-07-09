/// High-performance IOC extraction and URL normalization.
/// Uses pre-compiled regex from ioc_patterns.rs (single source of truth).
///
/// Issue #8: All IOC patterns consolidated in ioc_patterns.rs (single source of truth).
/// This module imports patterns from ioc_patterns.rs — DO NOT redefine patterns here.

use crate::gil::release_gil;
use crate::ioc_patterns::{
    CVE_PAT, DOMAIN_PAT, EMAIL_PAT, ENCODING_BASE32_PAT, ENCODING_BASE64_PAT,
    ENCODING_HEX_PAT, ENCODING_HIGH_ENTROPY_PAT, HASH_PAT, IPV4_PAT, IPV6_PAT,
    MD5_PAT, SHA1_PAT, SHA256_PAT, URL_PAT,
};
use crate::url_engine;
use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;
use regex::Regex;
use std::collections::HashSet;

// ISSUE-014: Pre-compiled regex from centralized patterns (single source of truth)
static IPV4_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(IPV4_PAT).unwrap());
static IPV6_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(IPV6_PAT).unwrap());
static DOMAIN_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(DOMAIN_PAT).unwrap());
static MD5_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(MD5_PAT).unwrap());
static SHA1_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(SHA1_PAT).unwrap());
static SHA256_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(SHA256_PAT).unwrap());
static EMAIL_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(EMAIL_PAT).unwrap());
static CVE_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(CVE_PAT).unwrap());
static URL_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(URL_PAT).unwrap());
static HASH_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(HASH_PAT).unwrap());
static ENCODING_BASE32_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(ENCODING_BASE32_PAT).unwrap());
static ENCODING_BASE64_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(ENCODING_BASE64_PAT).unwrap());
static ENCODING_HEX_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(ENCODING_HEX_PAT).unwrap());
static ENCODING_HIGH_ENTROPY_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(ENCODING_HIGH_ENTROPY_PAT).unwrap());

/// Register all IOC extraction functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_ioc_extract, m)?)?;
    m.add_function(wrap_pyfunction!(url_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_urls, m)?)?;
    m.add_function(wrap_pyfunction!(fast_ioc_extract_batch, m)?)?;
    m.add_function(wrap_pyfunction!(batch_ioc_extract_fast, m)?)?;
    m.add_function(wrap_pyfunction!(extract_iocs, m)?)?;
    m.add_function(wrap_pyfunction!(chi_square, m)?)?;
    m.add_function(wrap_pyfunction!(batch_sha256, m)?)?;
    m.add_function(wrap_pyfunction!(detect_encoding_patterns, m)?)?;
    Ok(())
}

/// Issue #15a: Releases GIL during CPU-intensive regex scan via py.allow_threads()
/// to enable true parallelism in asyncio.to_thread() ThreadPoolExecutor.
fn scan_iocs(text: &str) -> Vec<(String, String)> {
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

/// Fast IOC extraction from raw text using pre-compiled regex patterns.
/// Issue #15a: Releases GIL during CPU-intensive regex scan via release_gil()
/// to enable true parallelism in asyncio.to_thread() ThreadPoolExecutor.
#[pyfunction]
fn fast_ioc_extract(text: &str) -> Vec<(String, String)> {
    // Copy to Rust-owned string before releasing GIL
    let text_owned = text.to_string();
    // Release GIL for CPU-intensive regex scanning — allows Python threads to run.
    // Uses gil::release_gil() which probes allow_threads availability once
    // and caches the result (zero overhead in hot paths).
    Python::attach(|py| release_gil(py, || scan_iocs(&text_owned)))
}

/// Alias for backwards compatibility.
#[pyfunction]
fn fast_ioc_extract_batch(text: &str) -> Vec<(String, String)> {
    fast_ioc_extract(text)
}

/// Bulk IOC extraction from Python list — single GIL acquisition for entire batch.
/// Uses `Bound<PyList>::iter()` (PyO3 0.29+) for borrowed iteration.
/// For n >= threshold: rayon parallel with mixed_pool.
#[pyfunction]
pub fn batch_ioc_extract_fast<'py>(
    texts: &Bound<'py, PyList>,
    _py: Python<'py>,
) -> PyResult<Vec<(String, String)>> {
    let n = texts.len();
    if n == 0 {
        return Ok(vec![]);
    }

    // Collect under GIL, then process in rayon scope (no Python objects)
    let owned: Vec<String> = texts
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    if n < crate::adaptive_scheduler::mixed_threshold() {
        // Serial path — zero GIL release needed, faster for small batches
        let mut results = Vec::with_capacity(n * 4); // rough estimate
        for text in &owned {
            results.extend(scan_iocs(text));
        }
        Ok(results)
    } else {
        // Parallel path — mixed_pool (1-2 threads, P-core ceiling)
        // Issue #6: GIL released via `release_gil` to enable true rayon parallelism.
        let pool = crate::mixed_pool(n);
        Ok(Python::attach(|py| {
            release_gil(py, || {
                pool.install(|| {
                    owned
                        .par_iter()
                        .flat_map(|text| scan_iocs(text))
                        .collect()
                })
            })
        }))
    }
}

/// Public IOC extraction — delegates to fast_ioc_extract for DRY.
#[pyfunction]
pub fn extract_iocs(text: &str) -> Vec<(String, String)> {
    fast_ioc_extract(text)
}

/// URL normalizer — delegates to url_engine::normalize() for canonical form.
#[pyfunction]
fn url_normalize(url: &str) -> String {
    match url_engine::normalize(url) {
        Ok(s) => s,
        Err(_) => url.to_string(),
    }
}

/// In-memory URL deduplication with normalization.
/// Returns unique URLs with normalized forms used for dedup.
#[pyfunction]
fn batch_dedup_urls(urls: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    urls.into_iter()
        .filter(|url| {
            match url_engine::normalize(url) {
                Ok(normalized) => seen.insert(normalized),
                Err(_) => seen.insert(url.clone()),
            }
        })
        .collect()
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

/// Issue #9 fix: Uses mixed_pool for large batches (>= 128 items) — adaptive P-core
/// parallelism. cpu_pool would saturate E-cores on M1; mixed_pool uses only P-cores.
/// For small batches (< 128) serial execution avoids thread-spawn overhead.
#[pyfunction]
pub fn batch_sha256(items: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    let n = items.len();
    if n < 128 {
        items.iter().map(|s| sha256_hex(s.as_bytes())).collect()
    } else {
        // Issue #9 fix: mixed_pool (P-core only) instead of cpu_pool (all cores)
        crate::mixed_pool(n).install(|| {
            items.par_iter().map(|s| sha256_hex(s.as_bytes())).collect()
        })
    }
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

/// Detect encoding patterns in a DNS query subdomain part.
/// Returns list of encoding types: "base32", "base64", "hex".
/// Issue #11: Rust regex for high-performance encoding detection.
#[pyfunction]
pub fn detect_encoding_patterns(query: &str) -> Vec<String> {
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut patterns: Vec<String> = Vec::new();

    // Extract subdomain parts for analysis
    for part in query.split('.') {
        if part.len() < 4 {
            continue;
        }

        // Check for Base32 (uppercase, digits 2-7, padding)
        if ENCODING_BASE32_RE.is_match(part) && part.len() >= 8 {
            let base32_chars = part.chars().filter(|c| c.is_uppercase() || "234567".contains(*c)).count();
            if base32_chars as f64 / part.len() as f64 > 0.9 {
                if seen.insert("base32".to_string()) {
                    patterns.push("base32".to_string());
                }
                continue;
            }
        }

        // Check for Base64 (mixed case, digits, +/, padding)
        if ENCODING_BASE64_RE.is_match(part) && part.len() >= 8 {
            let has_lower = part.chars().any(|c| c.is_lowercase());
            let has_upper = part.chars().any(|c| c.is_uppercase());
            let has_digit = part.chars().any(|c| c.is_ascii_digit());

            if (has_lower || has_upper) && (has_digit || part.contains('+') || part.contains('/')) {
                if seen.insert("base64".to_string()) {
                    patterns.push("base64".to_string());
                }
                continue;
            }
        }

        // Check for hex (digits and a-f, even length)
        if ENCODING_HEX_RE.is_match(part) && part.len() >= 8 && part.len() % 2 == 0 {
            if seen.insert("hex".to_string()) {
                patterns.push("hex".to_string());
            }
        }
    }

    patterns
}
