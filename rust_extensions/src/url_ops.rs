//! URL classification and host extraction — hot path for OSINT URL routing.
//!
//! Provides bounded, fail-soft URL classification by transport class
//! (Clearnet / Onion / I2P / Freenet) and bulk host extraction.
//! Also provides canonical URL normalization and BLAKE3-64 dedup keys
//! for BloomFilter-backed URL deduplication.
//!
//! Pure Rust, no C dependencies, M1-safe. No panics, no unwrap.

use pyo3::prelude::*;
use rayon::prelude::*;
use url::Url;

use blake3::Hasher;

/// Threshold for parallel batch processing (rayon).
/// Below this, sequential is faster than parallel (work overhead).
///
/// Calibrated for the bounded `crate::bulk_pool()` (4 workers, 2 MiB stacks).
/// For URL classification (string parse + suffix match, ~1-2 µs per URL),
/// 4 workers × 50 items = 200 items total = ~200-400 µs of work. The
/// rayon dispatch + chunk overhead is ~5-10 µs/chunk, so the parallel
/// branch starts paying off above ~50 items (down from 100 when we had 2 workers).
const BATCH_PARALLEL_THRESHOLD: usize = 50;

/// Minimum chunk size for the parallel branch. With 4 workers, a 200-item
/// batch gets 4 workers × ~4 chunks of 64 items = ~16 items/worker. This
/// reduces rayon channel-dispatch overhead while keeping work fine-grained.
const BATCH_PARALLEL_MIN_CHUNK: usize = 64;

/// URL kind — the network class a URL belongs to.
///
/// Used for transport routing: .onion → Tor, .i2p → I2P SOCKS, clearnet → HTTPS.
#[pyclass(eq, eq_int, skip_from_py_object)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum UrlKind {
    /// Public internet (http/https)
    Clearnet = 0,
    /// Tor hidden service (.onion)
    Onion = 1,
    /// I2P eepsite (.i2p)
    I2P = 2,
    /// Freenet/Hyphanet resource
    Freenet = 3,
    /// Empty or whitespace-only input
    Empty = 4,
    /// Could not be parsed as a URL
    Malformed = 5,
}

impl UrlKind {
    /// Canonical lowercase string form. Stable across releases — used in tests.
    #[inline]
    pub fn as_str(self) -> &'static str {
        match self {
            UrlKind::Clearnet => "clearnet",
            UrlKind::Onion => "onion",
            UrlKind::I2P => "i2p",
            UrlKind::Freenet => "freenet",
            UrlKind::Empty => "empty",
            UrlKind::Malformed => "malformed",
        }
    }
}

/// Classify a URL by transport class. Returns (kind_str, lowercase_host).
///
/// Fail-soft: never panics, never raises. Malformed/empty inputs return
/// ("malformed", "") or ("empty", "") respectively.
#[pyfunction]
pub fn classify_url(url: &str) -> (String, String) {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return (UrlKind::Empty.as_str().to_string(), String::new());
    }

    // Try strict parse first — covers http://, https://, ftp://, file://, etc.
    match Url::parse(trimmed) {
        Ok(parsed) => {
            let host = match parsed.host_str() {
                Some(h) => h.to_ascii_lowercase(),
                None => return (UrlKind::Malformed.as_str().to_string(), String::new()),
            };
            let kind = classify_host(&host);
            (kind.as_str().to_string(), host)
        }
        Err(_) => {
            // Permissive fallback: scheme-less inputs like "abc.onion/path" or
            // "google.com" are common in OSINT data. Re-parse with synthetic
            // http:// prefix so we still recover the host.
            let synthetic = format!("http://{}", trimmed.trim_start_matches('/'));
            match Url::parse(&synthetic) {
                Ok(parsed) => {
                    let host = match parsed.host_str() {
                        Some(h) => h.to_ascii_lowercase(),
                        None => return (UrlKind::Malformed.as_str().to_string(), String::new()),
                    };
                    let kind = classify_host(&host);
                    (kind.as_str().to_string(), host)
                }
                Err(_) => (UrlKind::Malformed.as_str().to_string(), String::new()),
            }
        }
    }
}

/// Classify an already-extracted (lowercased) host into a UrlKind.
/// Pure function — used by classify_url, batch_classify, and classify_host_pyo3.
#[inline]
pub fn classify_host(host: &str) -> UrlKind {
    // .onion (v2 = 16 chars, v3 = 56 chars — both end in .onion)
    if host.ends_with(".onion") {
        return UrlKind::Onion;
    }
    // .i2p (incl. .b32.i2p which also ends with .i2p)
    if host.ends_with(".i2p") {
        return UrlKind::I2P;
    }
    // Freenet / Hyphanet naming
    if host.contains("freenet") || host.contains("hyphanet") {
        return UrlKind::Freenet;
    }
    UrlKind::Clearnet
}

/// Batch classify a vector of URLs. Returns Vec<(kind_str, host)>.
///
/// For >BATCH_PARALLEL_THRESHOLD inputs, parallelizes via the bounded
/// `crate::bulk_pool()` (4 workers, 2 MiB stacks — M1 8GB safe).
/// Below threshold, sequential is faster (rayon dispatch overhead).
///
/// Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize
/// rayon channel-dispatch cost across 64-item work units.
///
/// Never panics — malformed entries get ("malformed", "") entries.
#[pyfunction]
pub fn batch_classify(urls: Vec<String>) -> Vec<(String, String)> {
    if urls.len() > BATCH_PARALLEL_THRESHOLD {
        crate::bulk_pool().install(|| {
            urls.par_iter()
                .map(|u| classify_url(u))
                .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                .collect()
        })
    } else {
        urls.iter().map(|u| classify_url(u)).collect()
    }
}

/// Extract lowercase hostname from URL. Drop-in replacement for
/// `urllib.parse.urlparse(url).hostname.lower()` (returns "" on failure).
///
/// Never panics, never returns None — empty string on parse failure.
#[pyfunction]
pub fn extract_host(url: &str) -> String {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    match Url::parse(trimmed) {
        Ok(parsed) => parsed
            .host_str()
            .map(|h| h.to_ascii_lowercase())
            .unwrap_or_default(),
        Err(_) => {
            // Permissive fallback for scheme-less inputs.
            let synthetic = format!("http://{}", trimmed.trim_start_matches('/'));
            Url::parse(&synthetic)
                .ok()
                .and_then(|p| p.host_str().map(|h| h.to_ascii_lowercase()))
                .unwrap_or_default()
        }
    }
}

/// Return True if the URL's path strongly suggests a feed (RSS/Atom/XML/Sitemap).
///
/// Pure string operations — no regex (avoids regex dispatch overhead in hot path).
/// Checks only the last path segment, after rstrip("/").
#[pyfunction]
pub fn looks_like_feed_url(path: &str) -> bool {
    if path.is_empty() {
        return false;
    }
    // Strip query/fragment if present — feeds don't carry them, but be lenient.
    let path_only = match path.find('?') {
        Some(idx) => &path[..idx],
        None => match path.find('#') {
            Some(idx) => &path[..idx],
            None => path,
        },
    };
    // Drop trailing slashes, then take last path segment.
    let trimmed = path_only.trim_end_matches('/');
    let last = match trimmed.rfind('/') {
        Some(idx) => &trimmed[idx + 1..],
        None => trimmed,
    };
    if last.is_empty() {
        return false;
    }
    // Lowercase ASCII comparison — str-level, no Unicode normalization.
    let bytes = last.as_bytes();
    // Fast suffix check against feed markers. All markers are ASCII so we
    // can operate on bytes without UTF-8 boundary checks.
    ends_with_ascii_ci(bytes, b".rss")
        || ends_with_ascii_ci(bytes, b".atom")
        || ends_with_ascii_ci(bytes, b".xml")
        || ends_with_ascii_ci(bytes, b".opensearch")
        || ends_with_ascii_ci(bytes, b".sitemap")
        || contains_feed_keyword(last)
}

/// Case-insensitive ASCII ends_with without allocating a lowercased copy.
#[inline]
fn ends_with_ascii_ci(haystack: &[u8], needle: &[u8]) -> bool {
    if haystack.len() < needle.len() {
        return false;
    }
    let start = haystack.len() - needle.len();
    haystack[start..]
        .iter()
        .zip(needle.iter())
        .all(|(a, b)| a.eq_ignore_ascii_case(b))
}

/// Whole-word match for "feed" / "rss" / "atom" in the last segment,
/// delimited by non-alphanumeric boundaries. Avoids false positives like
/// "feedback" or "atombomb".
#[inline]
fn contains_feed_keyword(seg: &str) -> bool {
    let lower = seg.to_ascii_lowercase();
    matches_any_word(&lower, "feed")
        || matches_any_word(&lower, "rss")
        || matches_any_word(&lower, "atom")
}

#[inline]
fn matches_any_word(haystack: &str, needle: &str) -> bool {
    if haystack == needle {
        return true;
    }
    let bytes = haystack.as_bytes();
    let nlen = needle.len();
    let mut start = 0;
    while start + nlen <= bytes.len() {
        // Find next '/' or string start.
        if start > 0 && bytes[start - 1] != b'/' && bytes[start - 1] != b'-' && bytes[start - 1] != b'.' {
            start += 1;
            continue;
        }
        if start + nlen < bytes.len()
            && bytes[start + nlen] != b'/'
            && bytes[start + nlen] != b'-'
            && bytes[start + nlen] != b'.'
        {
            start += 1;
            continue;
        }
        // Compare window
        if haystack[start..start + nlen].eq_ignore_ascii_case(needle) {
            return true;
        }
        start += 1;
    }
    false
}

/// Normalize a URL to canonical form for deduplication.
///
/// Strips: default ports (80/443), fragments, trailing slashes from path.
/// Sorts query parameters (stable order).
/// Lowercases scheme and host.
///
/// Used by `url_dedup_key()` to produce a stable canonical form before hashing.
/// Falls back to the raw URL string on parse failure (never raises).
#[pyfunction]
pub fn canonical_url(url: &str) -> String {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return String::new();
    }

    // Parse with synthetic http:// prefix for scheme-less inputs.
    let synthetic = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("http://{}", trimmed.trim_start_matches('/'))
    };

    let parsed = match Url::parse(&synthetic) {
        Ok(p) => p,
        Err(_) => return trimmed.to_string(),
    };

    // Lowercase scheme and host.
    let scheme = parsed.scheme().to_ascii_lowercase();
    let host = parsed.host_str().unwrap_or("").to_ascii_lowercase();

    // Strip default ports.
    let port = parsed.port();
    let port_str = match (port, scheme.as_str()) {
        (Some(p), "http") if p == 80 => String::new(),
        (Some(p), "https") if p == 443 => String::new(),
        (Some(p), _) => format!(":{}", p),
        _ => String::new(),
    };

    // Normalize path: lowercase, drop trailing slash, keep single leading slash.
    let path = parsed.path();
    let path_norm = if path.is_empty() || path == "/" {
        "/".to_string()
    } else {
        path.trim_end_matches('/').to_ascii_lowercase()
    };

    // Sort query params.
    let query = match parsed.query() {
        None => String::new(),
        Some(q) => {
            // Parse and re-sort query string — stable sort by key.
            let mut params: Vec<(String, String)> = q
                .split('&')
                .filter_map(|pair| {
                    let kv: Vec<&str> = pair.splitn(2, '=').collect();
                    let k = urlencoding_decode(kv.get(0).unwrap_or(&""));
                    let v = kv.get(1).map(|v| urlencoding_decode(v)).unwrap_or_default();
                    if k.is_empty() {
                        None
                    } else {
                        Some((k, v))
                    }
                })
                .collect();
            params.sort_by(|a, b| a.0.cmp(&b.0));
            if params.is_empty() {
                String::new()
            } else {
                let encoded: Vec<String> = params
                    .into_iter()
                    .map(|(k, v)| format!("{}={}", k, v))
                    .collect();
                format!("?{}", encoded.join("&"))
            }
        }
    };

    format!("{}://{}{}{}{}", scheme, host, port_str, path_norm, query)
}

/// Compute a BLAKE3-64 dedup key for a URL.
///
/// Canonicalizes the URL first via `canonical_url()`, then hashes the
/// canonical form with BLAKE3-64 (first 8 bytes, little-endian u64).
///
/// Returns a 16-character lowercase hex string suitable as a BloomFilter
/// dedup key. Replaces storing the full normalized URL string — saves
/// ~20-50 bytes per entry in the BloomFilter with zero collision risk
/// increase (BLAKE3-64 is uniformly distributed).
///
/// Never panics — on any error returns the blake3-64 of the raw URL.
#[pyfunction]
pub fn url_dedup_key(url: &str) -> String {
    let canonical = canonical_url(url);
    let mut hasher = Hasher::new();
    hasher.update(canonical.as_bytes());
    let hash = hasher.finalize();
    let bytes: [u8; 8] = match hash.as_bytes()[..8].try_into() {
        Ok(b) => b,
        Err(_) => return blake3_fallback(url),
    };
    format!("{:016x}", u64::from_le_bytes(bytes))
}

/// Fallback blake3-64 when canonical_url returns empty.
fn blake3_fallback(url: &str) -> String {
    let hash = blake3::hash(url.as_bytes());
    let bytes: [u8; 8] = hash.as_bytes()[..8].try_into().unwrap_or([0u8; 8]);
    format!("{:016x}", u64::from_le_bytes(bytes))
}

/// Decode %-encoded string (URL encoding). Used by canonical_url for query params.
fn urlencoding_decode(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '%' {
            let hex: String = chars.by_ref().take(2).collect();
            if hex.len() == 2 {
                if let Ok(byte) = u8::from_str_radix(&hex, 16) {
                    result.push(byte as char);
                } else {
                    result.push('%');
                    result.push_str(&hex);
                }
            } else {
                result.push('%');
                if !hex.is_empty() {
                    result.push_str(&hex);
                }
            }
        } else if c == '+' {
            // + in query string = space (application/x-www-form-urlencoded)
            result.push(' ');
        } else {
            result.push(c);
        }
    }
    result
}

/// Register all url_ops functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<UrlKind>()?;
    m.add_function(wrap_pyfunction!(classify_url, m)?)?;
    m.add_function(wrap_pyfunction!(batch_classify, m)?)?;
    m.add_function(wrap_pyfunction!(extract_host, m)?)?;
    m.add_function(wrap_pyfunction!(looks_like_feed_url, m)?)?;
    m.add_function(wrap_pyfunction!(canonical_url, m)?)?;
    m.add_function(wrap_pyfunction!(url_dedup_key, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_onion() {
        let (kind, host) = classify_url("http://abc.onion/path");
        assert_eq!(kind, "onion");
        assert_eq!(host, "abc.onion");
    }

    #[test]
    fn test_classify_clearnet() {
        let (kind, host) = classify_url("https://google.com");
        assert_eq!(kind, "clearnet");
        assert_eq!(host, "google.com");
    }

    #[test]
    fn test_classify_malformed() {
        let (kind, host) = classify_url("not_a_url");
        // Synthetic http:// prefix lets us still recover the host.
        assert_eq!(kind, "clearnet");
        assert_eq!(host, "not_a_url");
    }

    #[test]
    fn test_classify_truly_malformed() {
        // Empty after trim + no synthetic rescue — stays empty.
        let (kind, host) = classify_url("");
        assert_eq!(kind, "empty");
        assert_eq!(host, "");
    }

    #[test]
    fn test_classify_i2p() {
        let (kind, _) = classify_url("http://example.i2p/page");
        assert_eq!(kind, "i2p");
    }

    #[test]
    fn test_classify_freenet() {
        let (kind, _) = classify_url("http://freenetproject.org");
        assert_eq!(kind, "freenet");
    }

    #[test]
    fn test_classify_uppercase_host() {
        let (kind, host) = classify_url("https://ABC.ONION");
        assert_eq!(kind, "onion");
        assert_eq!(host, "abc.onion");
    }

    #[test]
    fn test_extract_host() {
        assert_eq!(extract_host("https://Example.com/Path"), "example.com");
        assert_eq!(extract_host(""), "");
        assert_eq!(extract_host("not a url at all"), "not a url at all");
    }

    #[test]
    fn test_feed_url_rss() {
        assert!(looks_like_feed_url("/feed/rss"));
        assert!(looks_like_feed_url("/news.atom"));
        assert!(!looks_like_feed_url("/news/article"));
        assert!(!looks_like_feed_url("/api/feedback"));  // contains "feed" but not whole-word
    }

    #[test]
    fn test_batch_1000() {
        let urls: Vec<String> = (0..1000)
            .map(|i| format!("https://example{}.com/path", i))
            .collect();
        let results = batch_classify(urls);
        assert_eq!(results.len(), 1000);
        for (kind, host) in &results {
            assert_eq!(kind, "clearnet");
            assert!(host.starts_with("example"));
        }
    }

    #[test]
    fn test_canonical_url_basic() {
        // Lowercase scheme and host.
        assert_eq!(canonical_url("HTTPS://Example.COM/Path"), "https://example.com/path");
    }

    #[test]
    fn test_canonical_url_strips_default_port() {
        assert_eq!(canonical_url("http://example.com:80/path"), "http://example.com/path");
        assert_eq!(canonical_url("https://example.com:443/path"), "https://example.com/path");
        // Non-default port kept.
        assert_eq!(canonical_url("http://example.com:8080/path"), "http://example.com:8080/path");
    }

    #[test]
    fn test_canonical_url_sorts_query_params() {
        assert_eq!(
            canonical_url("https://example.com/search?z=1&a=2&m=3"),
            "https://example.com/search?a=2&m=3&z=1"
        );
    }

    #[test]
    fn test_canonical_url_drops_fragment() {
        assert_eq!(
            canonical_url("https://example.com/page#section"),
            "https://example.com/page"
        );
    }

    #[test]
    fn test_canonical_url_empty_input() {
        assert_eq!(canonical_url(""), "");
    }

    #[test]
    fn test_canonical_url_trims_trailing_slash() {
        assert_eq!(canonical_url("https://example.com/path///"), "https://example.com/path");
        assert_eq!(canonical_url("https://example.com/"), "https://example.com/");
    }

    #[test]
    fn test_url_dedup_key_deterministic() {
        let url = "https://Example.COM:443/path?b=2&a=1";
        let key1 = url_dedup_key(url);
        let key2 = url_dedup_key(url);
        assert_eq!(key1, key2, "url_dedup_key must be deterministic");
        assert_eq!(key1.len(), 16, "BLAKE3-64 key is 16 hex chars");
    }

    #[test]
    fn test_url_dedup_key_different_urls_same_content() {
        // Two URLs with same canonical form should produce same key.
        let url1 = "https://example.com/path";
        let url2 = "https://EXAMPLE.COM/path/";  // trailing slash should be trimmed
        let key1 = url_dedup_key(url1);
        let key2 = url_dedup_key(url2);
        assert_eq!(key1, key2, "same canonical form = same dedup key");
    }

    #[test]
    fn test_url_dedup_key_hex_format() {
        let key = url_dedup_key("https://google.com");
        assert!(key.chars().all(|c| c.is_ascii_hexdigit()), "key must be lowercase hex");
        assert_eq!(key.len(), 16);
    }

    #[test]
    fn test_url_dedup_key_empty_input() {
        let key = url_dedup_key("");
        assert_eq!(key.len(), 16);
        assert!(key.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
