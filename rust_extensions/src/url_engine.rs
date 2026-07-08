//! URL normalization and fingerprinting engine.
//!
//! Provides high-performance URL canonicalization for OSINT deduplication.
//! Pure Rust — no C dependencies, M1-safe.

use pyo3::prelude::*;
use url::Url;

/// Tracking parameters to strip from URLs (common analytics/campaign params).
/// Exposed as a public static for Python-side import via hledac_rust_extensions.
pub static TRACKING_PARAMS: &[&str] = &[
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid",
    "msclkid", "twclid",
    "mc_cid", "mc_eid",
    "_ga", "_gl",
    "yclid", "ymclid",
    "spm", "scm_source", "scm_content",
    "share_source", "share_medium",
    "ref", "referrer", "ref_src", "ref_url",
    "campaign", "source", "affiliate",
    "zanpid", "aff_id",
];

/// Normalizes a URL for canonical representation.
#[pyfunction]
pub fn normalize(raw_url: &str) -> PyResult<String> {
    let parsed = Url::parse(raw_url)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    canonicalize_url_internal(&parsed)
}

/// Internal canonicalization (no parse — already parsed)
#[inline]
fn canonicalize_url_internal(parsed: &Url) -> PyResult<String> {
    // 1. Lowercase scheme and host (url crate handles this automatically)
    // 2. Remove default ports (80 for http, 443 for https)
    let mut normalized = parsed.clone();

    if let Some(port) = normalized.port() {
        let scheme = normalized.scheme();
        match (scheme, port) {
            ("http", 80) | ("https", 443) => {
                let _ = normalized.set_port(None);
            }
            _ => {}
        }
    }

    // 3. Sort query parameters alphabetically
    if let Some(query) = normalized.query() {
        let mut pairs: Vec<(String, String)> = url::form_urlencoded::parse(query.as_bytes())
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect();
        pairs.sort_by(|a, b| a.0.cmp(&b.0));
        let sorted_query = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(pairs)
            .finish();
        normalized.set_query(if sorted_query.is_empty() { None } else { Some(&sorted_query) });
    }

    // 4. Remove fragment
    normalized.set_fragment(None);

    Ok(normalized.to_string())
}

/// Computes a fast 64-bit fingerprint of a URL for dedup.
/// Combines canonicalization + xxhash3-64 hashing in one call.
///
/// # Arguments
/// * `url` - URL string to fingerprint
///
/// # Returns
/// * 64-bit unsigned integer fingerprint (xxh3-64 of canonical URL)
#[pyfunction]
pub fn fingerprint(url: &str) -> PyResult<u64> {
    url_fingerprint(url)
}

/// URL fingerprint — xxh3-64 hash of canonical URL
#[inline]
pub fn url_fingerprint(url: &str) -> PyResult<u64> {
    let canonical = normalize(url)?;
    use xxhash_rust::xxh3::xxh3_64;
    Ok(xxh3_64(canonical.as_bytes()))
}

/// Strips tracking parameters from a URL.
/// Returns URL with tracking params (UTM, fbclid, etc.) removed.
///
/// # Arguments
/// * `url` - URL string to strip tracking params from
///
/// # Returns
/// * URL with tracking parameters removed
#[pyfunction]
pub fn strip_tracking_params(url: &str) -> PyResult<String> {
    let parsed = Url::parse(url)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let mut normalized = parsed.clone();

    // Remove default ports
    if let Some(port) = normalized.port() {
        let scheme = normalized.scheme();
        match (scheme, port) {
            ("http", 80) | ("https", 443) => {
                let _ = normalized.set_port(None);
            }
            _ => {}
        }
    }

    // Filter out tracking parameters
    if let Some(query) = parsed.query() {
        let filtered: Vec<(String, String)> = url::form_urlencoded::parse(query.as_bytes())
            .filter(|(k, _)| !TRACKING_PARAMS.contains(&k.as_ref()))
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect();

        let new_query = if filtered.is_empty() {
            None
        } else {
            let encoded = url::form_urlencoded::Serializer::new(String::new())
                .extend_pairs(filtered)
                .finish();
            Some(encoded)
        };
        normalized.set_query(new_query.as_deref());
    }

    // Remove fragment
    normalized.set_fragment(None);

    Ok(normalized.to_string())
}

/// Batch canonicalize URLs.
#[pyfunction]
pub fn canonicalize_batch(urls: Vec<String>) -> Vec<String> {
    urls.into_iter()
        .filter_map(|u| normalize(&u).ok())
        .collect()
}

/// Batch fingerprint URLs.
#[pyfunction]
pub fn batch_fingerprint(urls: Vec<String>) -> Vec<Option<u64>> {
    urls.into_iter()
        .map(|u| fingerprint(&u).ok())
        .collect()
}

/// Validates that a URL is syntactically valid and uses http/https scheme.
#[pyfunction]
pub fn is_valid_url(url: &str) -> bool {
    Url::parse(url)
        .map(|parsed| matches!(parsed.scheme(), "http" | "https"))
        .unwrap_or(false)
}

/// Filters a list of URLs to only valid http/https URLs.
#[pyfunction]
pub fn filter_valid_urls(urls: Vec<String>) -> Vec<String> {
    urls.into_iter()
        .filter(|u| is_valid_url(u))
        .collect()
}

/// Returns the list of tracking parameter names stripped by strip_tracking_params().
#[pyfunction]
pub fn get_tracking_params() -> Vec<&'static str> {
    TRACKING_PARAMS.to_vec()
}

/// Extracts just the domain from a URL.
///
/// # Arguments
/// * `url` - URL string
///
/// # Returns
/// * Domain string (e.g., "example.com" from "https://www.example.com/path")
#[pyfunction]
pub fn extract_domain(url: &str) -> PyResult<String> {
    let parsed = Url::parse(url)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    let host = parsed.host_str()
        .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("no host in URL"))?;

    Ok(host.to_string())
}

/// Registers all URL engine functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(normalize, m)?)?;
    m.add_function(wrap_pyfunction!(fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(strip_tracking_params, m)?)?;
    m.add_function(wrap_pyfunction!(get_tracking_params, m)?)?;
    m.add_function(wrap_pyfunction!(canonicalize_batch, m)?)?;
    m.add_function(wrap_pyfunction!(batch_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(is_valid_url, m)?)?;
    m.add_function(wrap_pyfunction!(filter_valid_urls, m)?)?;
    m.add_function(wrap_pyfunction!(extract_domain, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_lowercase() {
        let result = normalize("http://EXAMPLE.COM/path").unwrap();
        assert_eq!(result, "http://example.com/path");
    }

    #[test]
    fn test_normalize_default_port() {
        let result = normalize("https://example.com:443/path").unwrap();
        assert_eq!(result, "https://example.com/path");
    }

    #[test]
    fn test_normalize_sorted_params() {
        let result = normalize("https://example.com/path?b=2&a=1").unwrap();
        assert_eq!(result, "https://example.com/path?a=1&b=2");
    }

    #[test]
    fn test_normalize_strip_fragment() {
        let result = normalize("https://example.com/path#section").unwrap();
        assert_eq!(result, "https://example.com/path");
    }

    #[test]
    fn test_fingerprint() {
        let fp1 = url_fingerprint("https://example.com/path").unwrap();
        let fp2 = url_fingerprint("https://EXAMPLE.COM/path").unwrap();
        // Same canonical URL should produce same fingerprint
        assert_eq!(fp1, fp2);
    }

    #[test]
    fn test_strip_tracking_params() {
        let result = strip_tracking_params(
            "https://example.com/page?utm_source=google&fbclid=abc123&id=123"
        ).unwrap();
        assert!(!result.contains("utm_source"));
        assert!(!result.contains("fbclid"));
        assert!(result.contains("id=123"));
    }

    #[test]
    fn test_is_valid_url() {
        assert!(is_valid_url("https://example.com"));
        assert!(is_valid_url("http://test.org/path?query=1"));
        assert!(!is_valid_url("ftp://example.com"));
        assert!(!is_valid_url("not-a-url"));
    }
}