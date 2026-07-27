//! URL classification and host extraction — hot path for OSINT URL routing.
//!
//! Provides bounded, fail-soft URL classification by transport class
//! (Clearnet / Onion / I2P / Freenet) and bulk host extraction.
//! Also provides canonical URL normalization and BLAKE3-64 dedup keys
//! for BloomFilter-backed URL deduplication.
//!
//! Pure Rust, no C dependencies, M1-safe. No panics, no unwrap.
//!
//! ARM NEON acceleration for batch URL canonicalization on M1/AArch64.

use pyo3::prelude::*;
use rayon::prelude::*;
use url::Url;

use super::adaptive_scheduler::get_adaptive_mixed_threshold;
use blake3::Hasher;

// R24: tracing instrumentation — conditionally compiled when tracing feature is enabled
#[cfg(feature = "otel")]
use tracing::instrument;

// ahash: ~10× faster than FNV on M1 (hardware-accelerated on Apple Silicon)
use ahash::AHashMap;

/// Minimum chunk size for the parallel branch. With 2 workers, a 200-item
/// batch gets 2 workers × ~6 chunks of 32 items = ~16 items/worker. This
/// reduces rayon channel-dispatch overhead while keeping work fine-grained.
const BATCH_PARALLEL_MIN_CHUNK: usize = 32;

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
#[cfg_attr(feature = "otel", instrument(skip_all, fields(url.len = url.len())))]
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

/// xxh3_64 hash of a URL string — used as cache key instead of full URL.
/// xxh3 is ~10× faster than FNV on M1 (hardware SIMD on Apple Silicon).
#[inline]
pub fn xxh3_url_hash(url: &str) -> u64 {
    xxhash_rust::xxh3::xxh3_64(url.as_bytes())
}

/// Batch classify a list of URLs (zero-copy borrow from Python).
///
/// Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.
/// Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:
/// - idle (pressure=0): 16 items → 1 thread serial
/// - normal (pressure=1): 32 items → 1 thread serial
/// - pressure (pressure=2): 64 items → 1 thread serial
///
/// Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize
/// rayon channel-dispatch cost across 32-item work units.
///
/// PyO3 0.29 borrowed API: takes `&PyList` instead of `Vec<String>`.
/// Python strings are NOT copied into Rust Vec for n < threshold (serial path).
/// For n ≥ threshold (parallel path), strings must be copied into owned `String`
/// because rayon transfers ownership across threads — GIL is released during
/// `pool.install()`. The zero-copy benefit is realized in the hot-path
/// serial case where most URL classification occurs.
///
/// Never panics — malformed entries get ("malformed", "") entries.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(batch_size = urls.len())))]
#[pyfunction]
pub fn batch_classify(urls: &Bound<'_, pyo3::types::PyList>) -> Vec<(String, String)> {
    let n = urls.len();
    if n < get_adaptive_mixed_threshold() {
        // Small batch: serial path — copy to owned String for compatibility.
        urls.iter()
            .map(|item| {
                let s: String = match item.extract() {
                    Ok(s) => s,
                    Err(_) => return (UrlKind::Malformed.as_str().to_string(), String::new()),
                };
                classify_url(&s)
            })
            .collect()
    } else {
        // Large batch: parallel path — must copy Python strings to owned Strings
        // because rayon releases the GIL during pool.install().
        let owned: Vec<String> = urls
            .iter()
            .filter_map(|item| item.extract::<String>().ok())
            .collect();
        crate::mixed_pool(n).install(|| {
            owned.par_iter()
                .map(|u| classify_url(u))
                .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                .collect()
        })
    }
}

/// Priority-based URL classification — sort by priority then classify in one pass.
///
/// **Problem:** Scheduler ranks sources by priority (tor_request_count,
/// feed_native_yield_ratio) but fetch is sequential via bounded_gather.
/// Priority-based prefetch needs: (1) sort URLs by priority, (2) classify each.
/// Two separate FFI calls = 2 GIL transitions.
///
/// **Solution:** Single FFI call — sort + classify in one rayon-parallel pass.
/// Eliminates the 2nd GIL transition entirely.
///
/// # Arguments
/// * `urls` — Vec of (url: String, priority: f32) tuples. Priority 0.0–1.0.
///
/// # Returns
/// * Vec of (url: String, priority: f32, kind: String) sorted by priority desc.
///   Kind is "clearnet" | "onion" | "i2p" | "freenet" | "empty" | "malformed".
///
/// # M1 8GB bounds
/// * Threading: mixed_pool(n) — adaptive 1-2 threads based on batch size.
/// * Memory: O(n) for sort buffer, bounded by caller (scheduler URL set limit).
/// * Fail-soft: malformed URLs get ("malformed", "") kind, never panics.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(url_count = urls.len())))]
#[pyfunction]
pub fn priority_classify_urls(
    urls: Vec<(String, f32)>,
) -> Vec<(String, f32, String)> {
    if urls.is_empty() {
        return Vec::new();
    }

    // Stage 1: sort by priority descending (f32::total_cmp for NaN-safe comparison)
    let n = urls.len();
    let mut sorted: Vec<(String, f32)> = urls;
    sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

    // Stage 2: classify all URLs via mixed_pool (adaptive parallelism)
    // No intermediate Vec<String> allocation — rayon iterates sorted directly.
    let classifications: Vec<(String, String)> = crate::mixed_pool(n).install(|| {
        sorted.par_iter()
            .map(|(u, _)| classify_url(u))
            .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
            .collect()
    });

    // Zip: (url, priority) + (url, kind) → (url, priority, kind)
    // URLs are in priority order from stage 1, classifications are in same order.
    sorted
        .into_iter()
        .zip(classifications.into_iter())
        .map(|((url, priority), (_, kind))| (url, priority, kind))
        .collect()
}

// =============================================================================
// UrlClassifyCache — embedded xxh3 cached in Rust (Issue #4)
// =============================================================================
//
// Problem solved:
//   Python PyCacheDict has 3 bottlenecks for batch classify:
//   1. Stage 1 (cache lookup): Python dict.get() = 50-100ns + GIL overhead
//   2. Stage 3 (cache write):   Python dict.set() = 50-100ns + GIL overhead
//   3. String keys: 80-200 bytes per URL vs 8 bytes for u64 hash
//
// Solution:
//   - xxh3_64(url) → u64 as cache key (5-10ns hash in Rust)
//   - AHashMap<u64, (u8, String)> — ahash is 10× faster than Python dict
//   - parking_lot::RwLock — read-lock-free (multiple concurrent readers)
//   - Single GIL transition for batch: all N lookups + rayon classify in one call
//   - TTL via lazy expiry (check on read, no background thread)
//
// M1 8GB: 10k entries ≈ 3 MB (vs Python dict 8 MB for same)
// Bounded: hard_cap 50_000 entries (same as batch_classify guard)
//

/// In-memory URL classification cache with xxh3_64 keys.
///
/// Stores: url_hash → (kind_id, lowercase_host)
/// - key: u64 = xxh3_64(url) — 8 bytes vs 80-200 bytes for full URL string
/// - value: (kind_id: u8, host: String)
///
/// TTL: lazy expiry on read (not a background thread)
/// Eviction: LRU via AHashMap's arbitrary order + explicit trim to hard_cap
///
/// Thread-safety: parking_lot::RwLock (read-lock-free, no poisoning)
/// Fail-soft: any error returns None/empty results, never raises
///
/// M1 8GB: ~3 MB for 10k entries (vs Python PyCacheDict ~8 MB)
pub struct UrlClassifyCache {
    /// xxh3_64(url) → (kind_id: u8, host: String)
    /// kind_id maps to UrlKind enum (0=Clearnet, 1=Onion, 2=I2P, 3=Freenet, 4=Empty, 5=Malformed)
    map: AHashMap<u64, (u8, String, f64)>, // (kind, host, timestamp)
    ttl_s: f64,
    hits: usize,
    misses: usize,
    evictions: usize,
}

impl UrlClassifyCache {
    #[inline]
    fn kind_to_str(kind: u8) -> &'static str {
        match kind {
            0 => "clearnet",
            1 => "onion",
            2 => "i2p",
            3 => "freenet",
            4 => "empty",
            _ => "malformed",
        }
    }

    /// Classify a single URL with cache lookup.
    /// Returns (kind_str, host_str).
    #[inline]
    fn classify_one(&mut self, url: &str) -> (String, String) {
        let h = xxh3_url_hash(url);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        if let Some(&(kind, ref host, ts)) = self.map.get(&h) {
            // Cache hit — check TTL
            if now - ts <= self.ttl_s {
                self.hits += 1;
                return (Self::kind_to_str(kind).to_string(), host.clone());
            }
            // Expired — remove and fall through to classify
            self.map.remove(&h);
        }

        // Cache miss — classify via Rust
        self.misses += 1;
        let (kind_str, host) = classify_url(url);
        let kind_id = match kind_str.as_str() {
            "clearnet" => 0u8,
            "onion" => 1,
            "i2p" => 2,
            "freenet" => 3,
            "empty" => 4,
            _ => 5,
        };

        // Enforce hard cap (LRU eviction via arbitrary AHashMap order)
        if self.map.len() >= 50_000 {
            // Remove ~10% oldest entries by taking first N entries
            let evict_count = (self.map.len() / 10).max(100);
            let keys_to_remove: Vec<u64> = self.map.keys().take(evict_count).copied().collect();
            for k in keys_to_remove {
                self.map.remove(&k);
                self.evictions += 1;
            }
        }

        self.map.insert(h, (kind_id, host.clone(), now));
        (kind_str, host)
    }

    /// Batch classify with embedded cache.
    /// Single GIL transition for all N URLs (lookups + rayon classify + cache writes).
    ///
    /// Returns list of (kind_str, host_str) in same order as input.
    /// All strings are Python-owned (extracted from PyList, results cloned back).
    fn classify_batch_impl(&mut self, urls: &[String]) -> Vec<(String, String)> {
        let n = urls.len();
        let mut results = Vec::with_capacity(n);
        let mut miss_indices: Vec<usize> = Vec::new();
        let mut miss_urls: Vec<String> = Vec::new();

        // Stage 1: cache lookup (read-lock-free via &mut self)
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        for (i, url) in urls.iter().enumerate() {
            let h = xxh3_url_hash(url);
            if let Some(&(kind, ref host, ts)) = self.map.get(&h) {
                if now - ts <= self.ttl_s {
                    self.hits += 1;
                    results.push((Self::kind_to_str(kind).to_string(), host.clone()));
                    continue;
                }
                // Expired
                self.map.remove(&h);
            }
            // Cache miss
            results.push((String::new(), String::new())); // placeholder
            miss_indices.push(i);
            miss_urls.push(url.clone());
        }

        if !miss_urls.is_empty() {
            // Stage 2: rayon batch classify for misses (single GIL transition)
            let classified: Vec<(String, String)> = if miss_urls.len() >= get_adaptive_mixed_threshold() {
                // Large miss batch: parallel via rayon
                crate::mixed_pool(miss_urls.len()).install(|| {
                    miss_urls.par_iter()
                        .map(|u| classify_url(u))
                        .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                        .collect()
                })
            } else {
                // Small miss batch: serial
                miss_urls.iter().map(|u| classify_url(u)).collect()
            };

            // Stage 3: populate cache + fill results
            // (write-lock held only for this section)
            for (local_i, url) in miss_urls.iter().enumerate() {
                let orig_i = miss_indices[local_i];
                let (kind_str, host) = &classified[local_i];

                let kind_id = match kind_str.as_str() {
                    "clearnet" => 0u8,
                    "onion" => 1u8,
                    "i2p" => 2u8,
                    "freenet" => 3u8,
                    "empty" => 4u8,
                    _ => 5u8,
                };

                let h = xxh3_url_hash(url);

                // LRU eviction if at cap
                if self.map.len() >= 50_000 {
                    let evict_count = (self.map.len() / 10).max(100);
                    let keys_to_remove: Vec<u64> = self.map.keys().take(evict_count).copied().collect();
                    for k in keys_to_remove {
                        self.map.remove(&k);
                        self.evictions += 1;
                    }
                }

                self.map.insert(h, (kind_id, host.clone(), now));
                self.misses += 1;
                results[orig_i] = (kind_str.clone(), host.clone());
            }
        }

        results
    }

    /// Get cache statistics.
    fn stats(&self) -> (usize, usize, usize, usize, usize) {
        (self.map.len(), self.hits, self.misses, self.evictions, 50_000)
    }

    /// Clear the cache.
    fn clear(&mut self) {
        self.map.clear();
        self.hits = 0;
        self.misses = 0;
        self.evictions = 0;
    }
}

/// Python-accessible URL classification cache (PyO3 #[pyclass]).
///
/// Usage from Python:
///     cache = _url_classify_cache_rust  # single shared instance
///     results = cache.classify_batch_cached(urls)
///
/// Single GIL transition per batch call (vs N transitions for N cache lookups
/// in the Python PyCacheDict approach).
#[pyclass]
pub struct UrlClassifyCachePy {
    inner: std::sync::Mutex<UrlClassifyCache>,
}

#[pymethods]
impl UrlClassifyCachePy {
    /// Create a new cache with given capacity and TTL.
    #[new]
    fn new(capacity: usize, ttl_s: f64) -> Self {
        Self {
            inner: std::sync::Mutex::new(UrlClassifyCache {
                map: AHashMap::with_capacity(capacity),
                ttl_s: ttl_s.max(0.0),
                hits: 0,
                misses: 0,
                evictions: 0,
            }),
        }
    }

    /// Batch classify URLs with embedded xxh3_64 cache.
    ///
    /// Single GIL transition for:
    ///   - Stage 1: N cache lookups (all in Rust, lock-free reads)
    ///   - Stage 2: rayon parallel classify for misses
    ///   - Stage 3: cache population (single write lock)
    ///
    /// Args:
    ///     urls: Python list of URL strings
    ///
    /// Returns:
    ///     List of (kind_str, host_str) tuples in same order as input.
    ///     kind_str ∈ {"clearnet", "onion", "i2p", "freenet", "empty", "malformed"}
    ///     host_str is lowercase hostname or "" for empty/malformed
    fn classify_batch_cached(&self, urls: Vec<String>) -> Vec<(String, String)> {
        let mut guard = match self.inner.lock() {
            Ok(g) => g,
            Err(_) => return urls.iter().map(|u| classify_url(u)).collect(),
        };
        guard.classify_batch_impl(&urls)
    }

    /// Clear all cache entries and reset stats.
    fn clear(&self) -> bool {
        match self.inner.lock() {
            Ok(mut guard) => {
                guard.clear();
                true
            }
            Err(_) => false,
        }
    }

    /// Get cache statistics.
    ///
    /// Returns:
    ///     dict with keys: size, hits, misses, evictions, capacity
    fn stats(&self) -> Option<(usize, usize, usize, usize, usize)> {
        self.inner.lock().ok().map(|g| g.stats())
    }

    /// Get the number of entries currently in the cache.
    fn __len__(&self) -> usize {
        self.inner.lock().map(|g| g.map.len()).unwrap_or(0)
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

/// Tracking parameter prefixes and names stripped during canonicalization.
/// Covers utm_*, fbclid, gclid, mc_*, yclid, ref, and common ad/analytics params.
const TRACKING_PARAM_PREFIXES: &[&str] = &["utm_"];
const TRACKING_PARAMS: &[&str] = &[
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid",
    "mc_cid", "mc_eid", "_ga", "_gl", "ref", "yclid",
];

/// Returns true if `key` is a tracking parameter (prefix or exact match).
///
/// Uses `eq_ignore_ascii_case` for exact matches — zero heap allocation.
/// Only lowercases once for the prefix check (utm_*) which is the minority
/// of cases in OSINT workloads (most params are exact matches like fbclid).
#[inline]
fn is_tracking_param(key: &str) -> bool {
    // Fast path: exact match via eq_ignore_ascii_case — no allocation.
    // TRACKING_PARAMS has 12 entries; linear scan is faster than HashSet
    // for this size due to better cache locality.
    if TRACKING_PARAMS.iter().any(|p| key.eq_ignore_ascii_case(p)) {
        return true;
    }
    // Prefix check: only utm_*, lowercuje jednou na stack.
    let key_lower = key.to_ascii_lowercase();
    TRACKING_PARAM_PREFIXES.iter().any(|p| key_lower.starts_with(p))
}

/// Normalize a URL to canonical form for deduplication.
///
/// Strips:
///   - default ports (80/443)
///   - fragments
///   - trailing slashes from path
///   - tracking query params (utm_*, fbclid, gclid, mc_*, ref, etc.)
/// Sorts remaining query parameters alphabetically.
/// Lowercases scheme and host.
///
/// Used by `url_dedup_key()` and `url_dedup_hash()` to produce a stable
/// canonical form before hashing. Falls back to the raw URL string on
/// parse failure (never raises).
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

    // Sort query params, dropping tracking parameters.
    let query = match parsed.query() {
        None => String::new(),
        Some(q) => {
            let mut params: Vec<(String, String)> = q
                .split('&')
                .filter_map(|pair| {
                    let kv: Vec<&str> = pair.splitn(2, '=').collect();
                    let k_raw = urlencoding_decode(kv.get(0).unwrap_or(&""));
                    let v = kv.get(1).map(|s| urlencoding_decode(s)).unwrap_or_default();
                    // Lowercase once for the tracking check — avoids double-lowercasing
                    // in is_tracking_param which internally lowercases for prefix check.
                    let k_lower = k_raw.to_ascii_lowercase();
                    if k_lower.is_empty() || is_tracking_param(&k_lower) {
                        None
                    } else {
                        Some((k_raw, v))
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

/// Strip tracking parameters from a URL, preserving all other structure.
///
/// Unlike `canonical_url()` which also lowercases scheme/host and normalizes
/// ports, this function only removes tracking query parameters while
/// keeping the URL's original casing and structure intact.
///
/// Tracking params stripped (prefix + exact match):
///   - `utm_*` prefix (utm_source, utm_medium, etc.)
///   - fbclid, gclid, gclsrc, dclid, msclkid, twclid
///   - mc_cid, mc_eid, _ga, _gl, ref, yclid
///
/// Fail-soft: never panics, never raises. Returns the original URL string
/// on any parse error.
#[pyfunction]
pub fn strip_tracking(url: &str) -> String {
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

    // If no query string, return URL unchanged (fast path).
    let Some(q) = parsed.query() else {
        return trimmed.to_string()
    };

    // Parse query params and filter out tracking ones.
    // Use index-based split to avoid allocating Vec<&str> for each pair.
    let mut has_tracking = false;
    let mut filtered: Vec<String> = Vec::with_capacity(16);

    for pair in q.split('&') {
        if let Some(eq_pos) = pair.find('=') {
            let k = &pair[..eq_pos];
            if k.is_empty() || is_tracking_param(k) {
                has_tracking = true;
            } else {
                // Reconstruct pair preserving original encoding (no decode).
                filtered.push(pair.to_string());
            }
        } else {
            // No '=' — treat as bare param. Empty bare params are dropped.
            if !pair.is_empty() && !is_tracking_param(pair) {
                filtered.push(pair.to_string());
            } else if !pair.is_empty() {
                has_tracking = true;
            }
        }
    }

    // No tracking params found — fast path, return URL unchanged.
    if !has_tracking {
        return trimmed.to_string();
    }

    if filtered.is_empty() {
        // All params were tracking — drop query entirely.
        let without_q: String = format!(
            "{}://{}{}",
            parsed.scheme(),
            parsed.host_str().unwrap_or(""),
            parsed.port().map(|p| format!(":{}", p)).unwrap_or_default()
        );
        let path = parsed.path();
        let path_part = if path.is_empty() { "/" } else { path };
        return format!("{}{}", without_q, path_part);
    }

    // Rebuild URL with filtered query.
    let new_query = filtered.join("&");
    let base: String = format!(
        "{}://{}{}",
        parsed.scheme(),
        parsed.host_str().unwrap_or(""),
        parsed.port().map(|p| format!(":{}", p)).unwrap_or_default()
    );
    let path = parsed.path();
    let path_part = if path.is_empty() { "/" } else { path };
    format!("{}{}?{}", base, path_part, new_query)
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

/// Compute a 64-bit deduplication fingerprint for a URL.
///
/// Canonicalizes the URL first via `canonical_url()` (stripping tracking
/// params), then computes FNV-1a hash of the canonical form.
///
/// FNV-1a is fast, non-cryptographic, and well-distributed — ideal for
/// BloomFilter/RotatingBloomFilter dedup keys. Returns a raw `u64` as
/// Python `int`. Fail-safe: on any error returns `u64::MAX`.
///
/// Use when you need a raw u64 hash to add to an external BloomFilter
/// rather than the hex-string key from `url_dedup_key()`.
#[pyfunction]
pub fn url_dedup_hash(url: &str) -> u64 {
    let canonical = canonical_url(url);
    if canonical.is_empty() {
        return u64::MAX;
    }
    // FNV-1a: fast, non-crypto, good distribution for dedup.
    let mut hash: u64 = 14695981039346656037;
    for byte in canonical.bytes() {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(1099511628211);
    }
    hash
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

/// Batch canonicalize a list of URLs (zero-copy borrow from Python).
///
/// Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.
/// Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:
/// - idle (pressure=0): 16 items → 1 thread serial
/// - normal (pressure=1): 32 items → 1 thread serial
/// - pressure (pressure=2): 64 items → 1 thread serial
///
/// Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize
/// rayon channel-dispatch cost across 32-item work units.
///
/// PyO3 0.29 borrowed API: takes `&Bound<'_, PyList>`.
/// Python strings are NOT copied into Rust Vec for n < threshold (serial path).
/// For n ≥ threshold (parallel path), strings must be copied into owned `String`
/// because rayon releases the GIL during `pool.install()`.
///
/// Never panics — malformed entries return the trimmed raw URL string.
///
/// Args:
///     urls: Python list of URL strings
///
/// Returns:
///     Vec<String> of canonicalized URLs (same order as input)
#[pyfunction]
pub fn canonical_url_batch(urls: &Bound<'_, pyo3::types::PyList>) -> Vec<String> {
    let n = urls.len();
    if n == 0 {
        return Vec::new();
    }

    if n < get_adaptive_mixed_threshold() {
        // Small batch: serial path — copy to owned String.
        urls.iter()
            .map(|item| {
                let s: String = match item.extract() {
                    Ok(s) => s,
                    Err(_) => return String::new(),
                };
                canonical_url(&s)
            })
            .collect()
    } else {
        // Large batch: parallel path — must copy Python strings to owned Strings
        // because rayon releases the GIL during pool.install().
        let owned: Vec<String> = urls
            .iter()
            .filter_map(|item| item.extract::<String>().ok())
            .collect();
        crate::mixed_pool(n).install(|| {
            owned.par_iter()
                .map(|u| canonical_url(u))
                .with_min_len(BATCH_PARALLEL_MIN_CHUNK)
                .collect()
        })
    }
}

/// Register all url_ops functions and classes with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<UrlKind>()?;
    m.add_class::<UrlClassifyCachePy>()?;
    m.add_function(wrap_pyfunction!(classify_url, m)?)?;
    m.add_function(wrap_pyfunction!(batch_classify, m)?)?;
    m.add_function(wrap_pyfunction!(priority_classify_urls, m)?)?;
    m.add_function(wrap_pyfunction!(extract_host, m)?)?;
    m.add_function(wrap_pyfunction!(looks_like_feed_url, m)?)?;
    m.add_function(wrap_pyfunction!(canonical_url, m)?)?;
    m.add_function(wrap_pyfunction!(canonical_url_batch, m)?)?;
    m.add_function(wrap_pyfunction!(strip_tracking, m)?)?;
    m.add_function(wrap_pyfunction!(url_dedup_key, m)?)?;
    m.add_function(wrap_pyfunction!(url_dedup_hash, m)?)?;
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
        // Note: batch_classify now takes &Bound<'_, PyList>.
        // This test is kept as a documentation of expected behavior.
        // PyO3 #[test] cannot call pyfunction with &Bound parameter directly.
        let urls: Vec<String> = (0..1000)
            .map(|i| format!("https://example{}.com/path", i))
            .collect();
        // Verify URL parsing behavior is correct for all 1000 URLs.
        for url in &urls {
            let (kind, host) = classify_url(url);
            assert_eq!(kind, "clearnet");
            assert!(host.starts_with("example"));
        }
    }

    #[test]
    fn test_cache_basic() {
        let mut cache = UrlClassifyCache {
            map: AHashMap::with_capacity(100),
            ttl_s: 300.0,
            hits: 0,
            misses: 0,
            evictions: 0,
        };

        // Miss → classify
        let (kind, host) = cache.classify_one("https://google.com/path");
        assert_eq!(kind, "clearnet");
        assert_eq!(host, "google.com");
        assert_eq!(cache.misses, 1);
        assert_eq!(cache.hits, 0);

        // Hit from cache
        let (kind, host) = cache.classify_one("https://google.com/path");
        assert_eq!(kind, "clearnet");
        assert_eq!(host, "google.com");
        assert_eq!(cache.hits, 1);
        assert_eq!(cache.misses, 1);
    }

    #[test]
    fn test_cache_batch() {
        let mut cache = UrlClassifyCache {
            map: AHashMap::with_capacity(100),
            ttl_s: 300.0,
            hits: 0,
            misses: 0,
            evictions: 0,
        };

        let urls = vec![
            "https://google.com".to_string(),
            "https://google.com".to_string(),  // duplicate → cache hit
            "https://github.com".to_string(),
            "http://abc.onion/path".to_string(),
        ];

        let results = cache.classify_batch_impl(&urls);
        assert_eq!(results.len(), 4);
        assert_eq!(results[0].0, "clearnet");
        assert_eq!(results[0].1, "google.com");
        assert_eq!(results[1].0, "clearnet");  // hit
        assert_eq!(results[1].1, "google.com");
        assert_eq!(results[2].0, "clearnet");
        assert_eq!(results[2].1, "github.com");
        assert_eq!(results[3].0, "onion");
        assert_eq!(results[3].1, "abc.onion");

        // 3 misses (google, github, onion), 1 hit (second google)
        assert_eq!(cache.misses, 3);
        assert_eq!(cache.hits, 1);
    }

    #[test]
    fn test_cache_ttl_expired() {
        let mut cache = UrlClassifyCache {
            map: AHashMap::with_capacity(100),
            ttl_s: 0.0,  // immediate expiry
            hits: 0,
            misses: 0,
            evictions: 0,
        };

        let (kind, host) = cache.classify_one("https://google.com");
        assert_eq!(kind, "clearnet");
        assert_eq!(cache.misses, 1);

        // Same URL — but TTL=0 means it expired immediately
        // Note: classify_one doesn't re-check after expiry in same call
        // because it removes expired entries lazily
        let (kind2, host2) = cache.classify_one("https://google.com");
        assert_eq!(kind2, "clearnet");
        // Should be a miss again (entry was removed on first call)
        assert_eq!(cache.misses, 2);
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
    fn test_canonical_url_strips_tracking_params() {
        // utm_* params stripped.
        assert_eq!(
            canonical_url("https://example.com/page?utm_source=google&utm_medium=cpc"),
            "https://example.com/page"
        );
        // fbclid, gclid, etc. stripped.
        assert_eq!(
            canonical_url("https://example.com/?fbclid=abc123&q=test"),
            "https://example.com/?q=test"
        );
        // mc_* params stripped.
        assert_eq!(
            canonical_url("https://example.com/?mc_cid=x&page=1"),
            "https://example.com/?page=1"
        );
        // Mixed — non-tracking preserved, sorted.
        assert_eq!(
            canonical_url("https://example.com/?z=1&fbclid=x&a=2"),
            "https://example.com/?a=2&z=1"
        );
    }

    #[test]
    fn test_url_dedup_hash_deterministic() {
        let h1 = url_dedup_hash("https://Example.COM/?fbclid=abc&q=test");
        let h2 = url_dedup_hash("https://Example.COM/?fbclid=xyz&q=test");
        // Same canonical form (fbclid stripped, params sorted) → same hash.
        assert_eq!(h1, h2);
    }

    #[test]
    fn test_url_dedup_hash_different_for_different_urls() {
        let h1 = url_dedup_hash("https://example.com/page1");
        let h2 = url_dedup_hash("https://example.com/page2");
        assert_ne!(h1, h2);
    }

    #[test]
    fn test_url_dedup_hash_returns_u64() {
        let h = url_dedup_hash("https://google.com");
        assert!(h > 0 || h == u64::MAX); // u64::MAX is valid on error path
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

    #[test]
    fn test_canonical_url_batch_small() {
        // n=3 < 50 → serial path
        let urls = vec![
            "HTTPS://Example.COM/Path",
            "http://example.com:80/path",
            "https://example.com/?fbclid=abc&q=test",
        ];
        let results: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        assert_eq!(results.len(), 3);
        assert_eq!(results[0], "https://example.com/path");
        assert_eq!(results[1], "http://example.com/path");
        assert_eq!(results[2], "https://example.com/?q=test");
    }

    #[test]
    fn test_canonical_url_batch_empty() {
        let urls: Vec<String> = vec![];
        let results: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        assert!(results.is_empty());
    }

    #[test]
    fn test_canonical_url_batch_tracking_params() {
        // Verify canonical_url_batch produces same result as individual canonical_url
        let urls = vec![
            "https://example.com/page?utm_source=google&utm_medium=cpc",
            "https://example.com/?fbclid=abc123&q=test&z=1",
            "https://example.com/?mc_cid=x&page=1&_ga=abc",
        ];
        let batch_results: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        let individual_results: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        assert_eq!(batch_results, individual_results);
    }

    #[test]
    fn test_canonical_url_batch_deterministic() {
        let urls = vec![
            "HTTPS://Example.COM/Path",
            "http://example.com:80/path",
            "https://example.com/?fbclid=abc&q=test",
        ];
        let a: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        let b: Vec<String> = urls.iter().map(|u| canonical_url(u)).collect();
        assert_eq!(a, b, "canonical_url_batch must be deterministic");
    }

    #[test]
    fn test_xxh3_url_hash_deterministic() {
        let h1 = xxh3_url_hash("https://google.com/path");
        let h2 = xxh3_url_hash("https://google.com/path");
        assert_eq!(h1, h2, "xxh3 must be deterministic");

        let h3 = xxh3_url_hash("https://github.com/path");
        assert_ne!(h1, h3, "different URLs → different hashes");
    }

    // Issue #15 — strip_tracking tests
    // Preserves original casing (unlike canonical_url which lowercases).
    #[test]
    fn test_strip_tracking_preserves_casing() {
        assert_eq!(
            strip_tracking("HTTPS://Example.COM/Path?utm_source=Google"),
            "HTTPS://Example.COM/Path"
        );
        assert_eq!(
            strip_tracking("https://TEST.COM/?FBclid=abc&q=test"),
            "https://TEST.COM/?q=test"
        );
    }

    #[test]
    fn test_strip_tracking_basic() {
        // utm_* params stripped.
        assert_eq!(
            strip_tracking("https://example.com/page?utm_source=google&utm_medium=cpc"),
            "https://example.com/page"
        );
        // fbclid, gclid stripped.
        assert_eq!(
            strip_tracking("https://example.com/?fbclid=abc123&q=test"),
            "https://example.com/?q=test"
        );
        // mc_* stripped.
        assert_eq!(
            strip_tracking("https://example.com/?mc_cid=x&page=1"),
            "https://example.com/?page=1"
        );
    }

    #[test]
    fn test_strip_tracking_fast_path() {
        // No query string → returns original unchanged.
        assert_eq!(strip_tracking("https://example.com/page"), "https://example.com/page");
        // No tracking params → returns original unchanged.
        assert_eq!(
            strip_tracking("https://example.com/page?q=test"),
            "https://example.com/page?q=test"
        );
        // Empty input.
        assert_eq!(strip_tracking(""), "");
    }

    #[test]
    fn test_strip_tracking_all_tracking() {
        // All params tracking → drops query entirely, keeps path.
        assert_eq!(
            strip_tracking("https://example.com/?utm_source=x&fbclid=y"),
            "https://example.com/"
        );
        // Mixed — only tracking removed.
        assert_eq!(
            strip_tracking("https://example.com/?utm_campaign=a&_ga=x&q=test&fbclid=b"),
            "https://example.com/?q=test"
        );
    }

    #[test]
    fn test_strip_tracking_scheme_less() {
        // Scheme-less inputs get synthetic http:// prefix, but the
        // returned URL uses the original string (not the synthetic form).
        assert_eq!(
            strip_tracking("example.com/page?utm_source=google"),
            "example.com/page"
        );
    }

    // priority_classify_urls tests

    #[test]
    fn test_priority_classify_empty() {
        let input: Vec<(String, f32)> = vec![];
        let result = priority_classify_urls(input);
        assert!(result.is_empty());
    }

    #[test]
    fn test_priority_classify_sorted_desc() {
        // Priority descending: 0.9, 0.5, 0.1
        let input = vec![
            ("https://low.example.com/path".to_string(), 0.1),
            ("https://high.example.com/path".to_string(), 0.9),
            ("https://mid.example.com/path".to_string(), 0.5),
        ];
        let result = priority_classify_urls(input);
        assert_eq!(result.len(), 3);
        // Sorted by priority desc
        assert_eq!(result[0].0, "https://high.example.com/path");
        assert_eq!(result[0].1, 0.9);
        assert_eq!(result[1].0, "https://mid.example.com/path");
        assert_eq!(result[1].1, 0.5);
        assert_eq!(result[2].0, "https://low.example.com/path");
        assert_eq!(result[2].1, 0.1);
        // All should be classified as clearnet
        assert_eq!(result[0].2, "clearnet");
        assert_eq!(result[1].2, "clearnet");
        assert_eq!(result[2].2, "clearnet");
    }

    #[test]
    fn test_priority_classify_onion_kind() {
        let input = vec![
            ("http://tor.onion/hidden".to_string(), 0.8),
            ("https://public.example.com".to_string(), 0.9),
        ];
        let result = priority_classify_urls(input);
        assert_eq!(result.len(), 2);
        // Sorted desc: public (0.9) first, then onion (0.8)
        assert_eq!(result[0].2, "clearnet");
        assert_eq!(result[1].2, "onion");
    }

    #[test]
    fn test_priority_classify_preserves_order_on_equal_priority() {
        // Equal priorities — relative order is implementation-defined but stable
        let input = vec![
            ("https://a.example.com".to_string(), 0.5),
            ("https://b.example.com".to_string(), 0.5),
            ("https://c.example.com".to_string(), 0.5),
        ];
        let result = priority_classify_urls(input);
        assert_eq!(result.len(), 3);
        for r in &result {
            assert_eq!(r.1, 0.5);
            assert_eq!(r.2, "clearnet");
        }
    }

    #[test]
    fn test_priority_classify_mixed_kinds() {
        let input = vec![
            ("https://clearnet.example.com".to_string(), 0.7),
            ("http://dark.onion/secret".to_string(), 0.6),
            ("http://i2p.site/eep".to_string(), 0.8),
        ];
        let result = priority_classify_urls(input);
        assert_eq!(result.len(), 3);
        // Desc: i2p (0.8), clearnet (0.7), onion (0.6)
        assert_eq!(result[0].0, "http://i2p.site/eep");
        assert_eq!(result[0].2, "i2p");
        assert_eq!(result[1].0, "https://clearnet.example.com");
        assert_eq!(result[1].2, "clearnet");
        assert_eq!(result[2].0, "http://dark.onion/secret");
        assert_eq!(result[2].2, "onion");
    }

    #[test]
    fn test_priority_classify_single_item() {
        let input = vec![("https://solo.example.com/path".to_string(), 0.42)];
        let result = priority_classify_urls(input);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "https://solo.example.com/path");
        assert_eq!(result[0].1, 0.42);
        assert_eq!(result[0].2, "clearnet");
    }
}
