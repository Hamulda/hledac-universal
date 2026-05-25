/// High-performance IOC extraction and URL normalization.
/// Uses OnceCell for one-time regex compilation (performance critical).

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
static MD5_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[a-fA-F0-9]{32}\b").unwrap());
static SHA1_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[a-fA-F0-9]{40}\b").unwrap());
static SHA256_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[a-fA-F0-9]{64}\b").unwrap());
static EMAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b").unwrap()
});
static CVE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\bCVE-\d{4}-\d{4,}\b").unwrap());
static TRACKING_PARAMS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    let mut s = HashSet::new();
    s.insert("utm_source");
    s.insert("utm_medium");
    s.insert("utm_campaign");
    s.insert("utm_term");
    s.insert("utm_content");
    s.insert("fbclid");
    s.insert("gclid");
    s.insert("mc_cid");
    s.insert("mc_eid");
    s.insert("ref");
    s.insert("ref_src");
    s.insert("ref_url");
    s
});

/// Register all IOC extraction functions with Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_ioc_extract, m)?)?;
    m.add_function(wrap_pyfunction!(url_normalize, m)?)?;
    m.add_function(wrap_pyfunction!(batch_dedup_urls, m)?)?;
    m.add_function(wrap_pyfunction!(fast_ioc_extract_batch, m)?)?;
    m.add_function(wrap_pyfunction!(url_normalize_batch, m)?)?;
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

fn urlencoding_encode(s: &str) -> String {
    let mut result = String::new();
    for c in s.chars() {
        match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => result.push(c),
            _ => {
                for b in c.to_string().as_bytes() {
                    result.push_str(&format!("%{:02X}", b));
                }
            }
        }
    }
    result
}

/// URL normalizer with canonical form enforcement.
#[pyfunction]
fn url_normalize(url: &str) -> String {
    let parsed = match url::Url::parse(url) {
        Ok(p) => p,
        Err(_) => return url.to_string(),
    };

    let scheme = parsed.scheme().to_lowercase();
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    let port = parsed.port();

    let mut result = format!("{}://{}", scheme, host);
    if let Some(p) = port {
        let strip_port = (scheme == "http" && p == 80) || (scheme == "https" && p == 443);
        if !strip_port {
            result.push_str(&format!(":{}", p));
        }
    }

    let path = parsed.path();
    result.push_str(path);
    if result.is_empty() || !result.contains('.') {
        result.push('/');
    }

    let mut params: Vec<(String, String)> = parsed
        .query_pairs()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .filter(|(k, _)| !TRACKING_PARAMS.contains(k.as_str()))
        .collect();
    params.sort_by(|a, b| a.0.cmp(&b.0));

    if !params.is_empty() {
        result.push('?');
        for (i, (k, v)) in params.iter().enumerate() {
            if i > 0 {
                result.push('&');
            }
            result.push_str(&urlencoding_encode(k));
            result.push('=');
            result.push_str(&urlencoding_encode(v));
        }
    }

    result
}

/// Alias for backwards compatibility.
#[pyfunction]
fn url_normalize_batch(url: &str) -> String {
    url_normalize(url)
}

/// In-memory URL deduplication with normalization.
/// Returns unique URLs with normalized forms used for dedup.
#[pyfunction]
fn batch_dedup_urls(urls: Vec<String>) -> Vec<String> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut result: Vec<String> = Vec::new();

    for url in urls {
        let normalized = url_normalize(&url);
        if seen.insert(normalized.clone()) {
            result.push(url);
        }
    }

    result
}