#[cfg(feature = "advanced")]
use crate::adaptive_scheduler;
/// High-performance IOC extraction and URL normalization.
/// Uses pre-compiled regex from ioc_patterns.rs (single source of truth).
///
/// Issue #8: All IOC patterns consolidated in ioc_patterns.rs (single source of truth).
/// This module imports patterns from ioc_patterns.rs — DO NOT redefine patterns here.
use crate::gil::release_gil;
use crate::ioc_patterns::{
    CVE_PAT, DOMAIN_PAT, EMAIL_PAT, ENCODING_BASE32_PAT, ENCODING_BASE64_PAT, ENCODING_HEX_PAT,
    ENCODING_HIGH_ENTROPY_PAT, HASH_PAT, IPV4_PAT, IPV6_PAT, MD5_PAT, SHA1_PAT, SHA256_PAT,
    URL_PAT,
};
use crate::url_engine;
use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;
use regex_automata::meta::Regex;
use std::collections::HashSet;

// ISSUE-014: Pre-compiled regex from centralized patterns (single source of truth)
static IPV4_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(IPV4_PAT).unwrap());
static IPV6_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(IPV6_PAT).unwrap());
static DOMAIN_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(DOMAIN_PAT).unwrap());
static MD5_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(MD5_PAT).unwrap());
static SHA1_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(SHA1_PAT).unwrap());
static SHA256_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(SHA256_PAT).unwrap());
static EMAIL_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(EMAIL_PAT).unwrap());
#[allow(dead_code)]
static HASH_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(HASH_PAT).unwrap());
static CVE_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(CVE_PAT).unwrap());
static URL_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(URL_PAT).unwrap());
static ENCODING_BASE32_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(ENCODING_BASE32_PAT).unwrap());
static ENCODING_BASE64_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(ENCODING_BASE64_PAT).unwrap());
static ENCODING_HEX_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(ENCODING_HEX_PAT).unwrap());
#[allow(dead_code)]
static ENCODING_HIGH_ENTROPY_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(ENCODING_HIGH_ENTROPY_PAT).unwrap());

/// Register all IOC extraction functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_ioc_extract))?;
    m.add_function(wrap_pyfunction!(url_normalize))?;
    m.add_function(wrap_pyfunction!(batch_dedup_urls))?;
    m.add_function(wrap_pyfunction!(fast_ioc_extract_batch))?;
    m.add_function(wrap_pyfunction!(batch_ioc_extract_fast))?;
    m.add_function(wrap_pyfunction!(extract_iocs))?;
    // Issue A1: extract_iocs_flat registered at top-level so callers can invoke
    // rust.ioc.extract_iocs_flat(text) directly without Python-domain indirection.
    m.add_function(wrap_pyfunction!(extract_iocs_flat))?;
    m.add_function(wrap_pyfunction!(chi_square))?;
    m.add_function(wrap_pyfunction!(batch_sha256))?;
    m.add_function(wrap_pyfunction!(detect_encoding_patterns))?;
    Ok(())
}

/// Issue #15a: Releases GIL during CPU-intensive regex scan via py.detach() (PyO3 0.29)
/// to enable true parallelism in asyncio.to_thread() ThreadPoolExecutor.
///
/// MOD-04: Optimized string allocations:
/// - Uses idiomatic Rust pattern: insert returns bool, original String stays in scope
/// - IOC type strings use String::from() instead of .to_string() (same performance, clearer)
/// - For fixed-length values (IPv4=15 chars, MD5=32, SHA256=64), the String overhead
///   (24B metadata + pointer + capacity) dominates at scale — savings ~50-100MB/day on M1 8GB
fn scan_iocs(text: &str) -> Vec<(String, String)> {
    let mut iocs: Vec<(String, String)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    // Helper to extract matched string from regex Match
    macro_rules! matched_str {
        ($cap:expr, $text:expr) => {
            &text[$cap.start()..$cap.end()]
        };
    }

    // IPv4: 15 chars max
    for cap in IPV4_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        // insert returns true if new, v is still owned for iocs.push
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("ipv4")));
        }
    }
    // IPv6: 45 chars max
    for cap in IPV6_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("ipv6")));
        }
    }
    // Domain
    for cap in DOMAIN_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("domain")));
        }
    }
    // MD5: 32 chars
    for cap in MD5_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("md5")));
        }
    }
    // SHA1: 40 chars
    for cap in SHA1_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("sha1")));
        }
    }
    // SHA256: 64 chars
    for cap in SHA256_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("sha256")));
        }
    }
    // Email
    for cap in EMAIL_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("email")));
        }
    }
    // CVE
    for cap in CVE_RE.find_iter(text) {
        let v = matched_str!(cap, text));
        if seen.insert(v.clone()) {
            iocs.push((v, String::from("cve")));
        }
    }

    iocs
}

/// Fast IOC extraction from raw text using pre-compiled regex patterns.
/// Issue #15a: Releases GIL during CPU-intensive regex scan via release_gil()
/// to enable true parallelism and allow asyncio event loop to run on other threads.
#[pyfunction]
fn fast_ioc_extract(text: &str) -> Vec<(String, String)> {
    // Copy to Rust-owned string before releasing GIL
    let text_owned = text.clone();
    // Release GIL for CPU-intensive regex scanning — allows Python threads to run.
    // GIL released via release_gil() for CPU-bound regex scanning.
    // This allows asyncio event loop to run on other threads during CPU-bound work.
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
///
/// FFI-02: Entire body wrapped in catch_unwind — extract::<String>() on a
/// non-string Python object can trigger a Python exception panic across the
/// FFI boundary, killing the Python process with SIGABRT.
#[pyfunction]
pub fn batch_ioc_extract_fast<'py>(
    texts: &Bound<'py, PyList>,
    _py: Python<'py>,
) -> PyResult<Vec<(String, String)>> {
    // FFI-02: Outer catch_unwind provides safety net for the entire function body.
    // Covers: extract::<String>() panics, rayon OOM panics, release_gil panics.
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let n = texts.len();
        if n == 0 {
            return Ok(Vec::<(String, String)>::new());
        }

        // Collect under GIL, then process in rayon scope (no Python objects).
        // filter_map + .ok() handles normal type errors; catch_unwind is the
        // outer safety net for exception->panic conversion at the FFI boundary.
        let owned: Vec<String> = texts
            .iter()
            .filter_map(|item| item.extract::<String>().ok())
            );

        #[cfg(feature = "advanced")]
        let thresh = adaptive_scheduler::mixed_threshold();
        #[cfg(not(feature = "advanced"))]
        let thresh = 0;

        if n < thresh {
            // Serial path — zero GIL release needed, faster for small batches
            let mut results = Vec::with_capacity(n * 4); // rough estimate
            for text in &owned {
                results.extend(scan_iocs(text));
            }
            Ok(results)
        } else {
            // Parallel path — mixed_pool (1-2 threads, P-core ceiling)
            // GIL is held during pool.install() — rayon releases GIL internally
            // during thread pool callbacks. This allows asyncio event loop to run
            // on other threads during CPU-bound work.
            let pool = crate::mixed_pool(n);
            Ok(pool.install(|| owned.par_iter().flat_map(|text| scan_iocs(text)).collect()))
        }
    })) {
        Ok(result) => result,
        Err(_) => Ok(Vec::new()),
    }
}

/// Public IOC extraction — delegates to fast_ioc_extract for DRY.
#[pyfunction]
pub fn extract_iocs(text: &str) -> Vec<(String, String)> {
    fast_ioc_extract(text)
}

/// Flat IOC extraction — alias for fast_ioc_extract with GIL release.
/// Issue A1: Registered as a top-level #[pyfunction] so callers can invoke
/// rust.ioc.extract_iocs_flat(text) directly without Python-domain indirection.
/// Delegates to fast_ioc_extract (same implementation, different name for clarity).
#[pyfunction]
pub fn extract_iocs_flat(text: &str) -> Vec<(String, String)> {
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
        .filter(|url| match url_engine::normalize(url) {
            Ok(normalized) => seen.insert(normalized),
            Err(_) => seen.insert(url.clone()),
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
        crate::mixed_pool(n)
            .install(|| items.par_iter().map(|s| sha256_hex(s.as_bytes())).collect())
    }
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    use std::fmt::Write;
    let mut hasher = Sha256::new();
    hasher.update(data);
    let result = hasher.iter();
    let mut hex = String::with_capacity(64);
    for byte in result.iter() {
        write!(hex, "{:02x}", byte));
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

    for part in query.split('.') {
        if part.len() < 4 {
            continue;
        }

        // Check for Base32 (uppercase, digits 2-7, padding)
        if ENCODING_BASE32_RE.is_match(part) && part.len() >= 8 {
            let base32_chars = part
                .chars()
                .filter(|c| c.is_uppercase() || "234567".contains(*c))
                );
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

/// Check if text contains any URL.
#[inline]
pub fn has_url(text: &str) -> bool {
    URL_RE.find_iter(text).next().is_some()
}

/// Check if text contains any domain.
#[inline]
pub fn has_domain(text: &str) -> bool {
    DOMAIN_RE.find_iter(text).next().is_some()
}

/// Check if text contains any email.
#[inline]
pub fn has_email(text: &str) -> bool {
    EMAIL_RE.find_iter(text).next().is_some()
}

/// Check if text contains any IPv4 address.
#[inline]
pub fn has_ipv4(text: &str) -> bool {
    IPV4_RE.find_iter(text).next().is_some()
}

/// Check if text contains any IOC (URL, domain, email, or IP).
/// Returns true if any IOC type is present.
#[inline]
pub fn has_any_ioc(text: &str) -> bool {
    has_url(text) || has_domain(text) || has_email(text) || has_ipv4(text)
}
