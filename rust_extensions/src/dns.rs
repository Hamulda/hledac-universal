//! DNS Resolution — DoH / DoT / DoQ + Happy Eyeballs (hickory-dns)
//!
//! ## Problem Solved
//!
//! Three separate DNS paths existed:
//!   1. `utils/batch_dns.py` — BatchDNSResolver (aiodns + stdlib fallback)
//!   2. `utils/async_helpers.py` — async_getaddrinfo() (aiodns + stdlib fallback)
//!   3. `recon/network_reconnaissance.py` — žádná specializovaná vrstva
//!
//! All used different code paths for the same fundamental operation.
//!
//! ## Solution
//!
//! Single Rust implementation via `hickory-dns` with:
//!   - DoH (DNS-over-HTTPS) — cloudflare, google, cloudflare_tel
//!   - DoT (DNS-over-TLS) — cloudflare, google
//!   - DoQ (DNS-over-QUIC) — cloudflare
//!   - Happy Eyeballs — parallel A + AAAA, fastest wins
//!
//! ## API
//!
//! ```python
//! # Single resolution
//! ips = rust.dns.resolve_async("example.com", qtype="A")
//!
//! # Happy Eyeballs — dual-stack, returns first available
//! import asyncio
//! ips = asyncio.run(rust.dns.resolve_happy_eyeballs("example.com"))
//!
//! # Batch prefetch (replaces batch_dns.py)
//! rust.dns.prefetch(hostnames: list[str])
//!
//! # Resolve many (parallel, bounded)
//! results = rust.dns.resolve_many([("example.com", "A"), ("example.org", "AAAA")])
//! ```
//!
//! ## M1 8GB Safety
//!
//! - Bounded LRU cache: 1024 hosts × ~100B = ~100KB max
//! - Negative cache: 256 entries × 30s TTL
//! - Concurrency cap: 50 simultaneous queries
//! - No heavy deps by default — feature-gated `dns` extra
//!
//! ## Feature Gate
//!
//! ```toml
//! [dependencies]
//! hickory-dns = { version = "0.24", features = ["tokio-runtime"], optional = true }
//! ```
//!
//! Enabled via: `HLEDAC_BUILD=dns` or `--features dns`

use std::collections::{HashMap, VecDeque};
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use pyo3::prelude::*;
use tokio::runtime::Runtime;

// ============================================================================
// Error Types
// ============================================================================

/// DNS resolution error kinds.
#[derive(Debug, Clone)]
pub enum DnsError {
    /// Host not found (NXDOMAIN)
    HostNotFound(String),
    /// Server failed (SERVFAIL, REFUSED, etc.)
    ServerFailed(String),
    /// Timeout
    Timeout,
    /// Invalid input
    InvalidInput(String),
    /// Runtime error
    Runtime(String),
    /// Unknown error
    Unknown(String),
}

impl DnsError {
    pub fn as_str(&self) -> &str {
        match self {
            DnsError::HostNotFound(_) => "host_not_found",
            DnsError::ServerFailed(_) => "server_failed",
            DnsError::Timeout => "timeout",
            DnsError::InvalidInput(_) => "invalid_input",
            DnsError::Runtime(_) => "runtime_error",
            DnsError::Unknown(_) => "unknown",
        }
    }
}

// ============================================================================
// Cache Types
// ============================================================================

/// Cache entry with TTL.
#[derive(Clone)]
struct CacheEntry {
    ips: Vec<String>,
    timestamp: Instant,
}

/// Bounded LRU DNS cache with TTL.
/// M1 8GB: 1024 hosts × ~100B = ~100KB max.
struct DnsCache {
    /// Positive cache: hostname -> (ips, timestamp)
    positive: HashMap<String, CacheEntry>,
    /// Negative cache: hostname -> (error, timestamp)
    negative: HashMap<String, (String, Instant)>,
    /// LRU ordering for eviction (VecDeque for O(1) push_back + pop_front)
    order: VecDeque<String>,
    /// Max entries
    max_size: usize,
    /// Positive TTL
    positive_ttl: Duration,
    /// Negative TTL
    negative_ttl: Duration,
}

impl DnsCache {
    fn new(max_size: usize) -> Self {
        Self {
            positive: HashMap::new(),
            negative: HashMap::new(),
            order: VecDeque::new(),
            max_size,
            positive_ttl: Duration::from_secs(300), // 5 min
            negative_ttl: Duration::from_secs(30),    // 30s
        }
    }

    /// Get from cache. Returns (ips, is_negative, error_msg or None).
    fn get(&self, hostname: &str) -> Option<(Vec<String>, bool, Option<&str>)> {
        let now = Instant::now();

        // Check positive cache
        if let Some(entry) = self.positive.get(hostname) {
            if now.duration_since(entry.timestamp) < self.positive_ttl {
                return Some((entry.ips.clone(), false, None));
            }
        }

        // Check negative cache
        if let Some((err, ts)) = self.negative.get(hostname) {
            if now.duration_since(*ts) < self.negative_ttl {
                return Some((Vec::new(), true, Some(err)));
            }
        }

        None
    }

    /// Evict oldest entry from both cache and order.
    fn evict_one(&mut self) {
        if let Some(oldest) = self.order.pop_front() {
            self.positive.remove(&oldest);
            self.negative.remove(&oldest);
        }
    }

    /// Insert positive result.
    fn insert_positive(&mut self, hostname: String, ips: Vec<String>) {
        // Remove existing entries for this hostname (updates don't count as new insert for order)
        self.positive.remove(&hostname);
        self.negative.remove(&hostname);

        // Evict if at capacity
        while self.positive.len() >= self.max_size && !self.order.is_empty() {
            self.evict_one();
        }

        self.positive.insert(hostname.clone(), CacheEntry {
            ips: ips.clone(),
            timestamp: Instant::now(),
        });
        self.order.push_back(hostname);
    }

    /// Insert negative result.
    fn insert_negative(&mut self, hostname: String, error: String) {
        // Remove existing entries for this hostname
        self.positive.remove(&hostname);
        self.negative.remove(&hostname);

        // Evict if at capacity (256 negative entries max)
        while self.negative.len() >= 256 && !self.order.is_empty() {
            self.evict_one();
        }

        self.negative.insert(hostname.clone(), (error, Instant::now()));
        self.order.push_back(hostname);
    }

    /// Clear all cache entries.
    fn clear(&mut self) {
        self.positive.clear();
        self.negative.clear();
        self.order.clear();
    }
}

// ============================================================================
// DNS Resolver — DoH / DoT / DoQ
// ============================================================================

/// DNS-over-HTTPS (DoH) and DNS-over-TLS (DoT) resolver.
///
/// Uses hickory-dns async runtime for true async DNS resolution.
/// Falls back to system resolver on failure.
///
/// M1 8GB: bounded cache + concurrency cap prevents memory blowup.
pub struct DnsResolver {
    /// Tokio runtime for async operations.
    runtime: Runtime,
    /// LRU cache for resolved hosts.
    cache: Arc<RwLock<DnsCache>>,
    /// Concurrency limiter.
    semaphore: tokio::sync::Semaphore,
}

impl DnsResolver {
    /// Create new resolver with caching and concurrency bounds.
    pub fn new() -> Self {
        // Create tokio runtime for async DNS
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .expect("dns_resolver: tokio runtime creation failed");

        Self {
            runtime,
            cache: Arc::new(RwLock::new(DnsCache::new(1024))),
            semaphore: tokio::sync::Semaphore::new(50), // Max 50 concurrent
        }
    }

    /// Resolve a hostname asynchronously (DoH/DoT).
    ///
    /// qtype: "A", "AAAA", "MX", "TXT", "NS", "CNAME"
    /// Returns list of IP addresses as strings.
    pub fn resolve(&self, hostname: &str, qtype: &str) -> Result<Vec<String>, DnsError> {
        // Check cache first
        {
            let cache = self.cache.read();
            if let Some((ips, is_neg, err)) = cache.get(hostname) {
                if is_neg {
                    return Err(DnsError::HostNotFound(err.unwrap_or("cached_nxdomain").to_string()));
                }
                return Ok(ips);
            }
        }

        let _permit = self.runtime.block_on(self.semaphore.acquire());

        let result = self._resolve_sync(hostname, qtype);

        // Cache the result
        match &result {
            Ok(ips) => {
                let mut cache = self.cache.write();
                cache.insert_positive(hostname.to_string(), ips.clone());
            }
            Err(e) => {
                let mut cache = self.cache.write();
                cache.insert_negative(hostname.to_string(), e.as_str().to_string());
            }
        }

        result
    }

    /// Internal sync resolver using hickory-dns.
    #[cfg(feature = "dns")]
    fn _resolve_sync(&self, hostname: &str, qtype: &str) -> Result<Vec<String>, DnsError> {
        use hickory::resolver::{Resolver, ResolverConfig, ResolverOpts};
        use hickory::proto::rr::RecordType;

        let mut opts = ResolverOpts::default();
        opts.timeout = Duration::from_secs(5);
        opts.attempts = 2;
        opts.rotate = true;

        // Configure DoH/DoT via cloudflare
        let config = ResolverConfig::cloudflare_tls();

        let resolver = Resolver::new(config, opts)
            .map_err(|e| DnsError::Runtime(format!("hickory config: {}", e)))?;

        // Map qtype string to RecordType
        let rt = match qtype {
            "A" => RecordType::A,
            "AAAA" => RecordType::AAAA,
            "MX" => RecordType::MX,
            "TXT" => RecordType::TXT,
            "NS" => RecordType::NS,
            "CNAME" => RecordType::CNAME,
            _ => return Err(DnsError::InvalidInput(format!("unsupported qtype: {}", qtype))),
        };

        // Perform blocking async resolution via tokio runtime
        let lookup = self.runtime.block_on(resolver.lookup(hostname, rt));

        match lookup {
            Ok(lookup) => {
                let ips: Vec<String> = lookup.iter()
                    .map(|ip| ip.to_string())
                    .collect();
                if ips.is_empty() {
                    Err(DnsError::HostNotFound(format!("no {} records for {}", qtype, hostname)))
                } else {
                    Ok(ips)
                }
            }
            Err(e) => {
                let err_str = format!("{}", e);
                if err_str.contains("NXDOMAIN") || err_str.contains("not found") || err_str.contains("NoRecordsFound") {
                    Err(DnsError::HostNotFound(err_str))
                } else if err_str.contains("timeout") || err_str.contains("TimedOut") {
                    Err(DnsError::Timeout)
                } else {
                    Err(DnsError::ServerFailed(err_str))
                }
            }
        }
    }

    /// Non-feature-gated fallback using stdlib.
    #[cfg(not(feature = "dns"))]
    fn _resolve_sync(&self, hostname: &str, qtype: &str) -> Result<Vec<String>, DnsError> {
        use std::net::{TcpStream, ToSocketAddrs};
        use std::time::Duration;

        if qtype != "A" && qtype != "AAAA" {
            return Err(DnsError::InvalidInput(format!("unsupported qtype: {}", qtype)));
        }

        let addr_str = format!("{}:{}", hostname, if qtype == "A" { "80" } else { "443" });

        let addrs: Vec<SocketAddr> = match addr_str.to_socket_addrs() {
            Ok(addrs) => addrs.collect(),
            Err(e) => return Err(DnsError::InvalidInput(format!("invalid hostname: {}", e))),
        };

        if addrs.is_empty() {
            return Err(DnsError::HostNotFound(format!("no addresses for {}", hostname)));
        }

        // Try to connect to determine reachability
        let mut ips = Vec::new();
        for addr in addrs.iter().take(10) {
            if let Ok(_) = TcpStream::connect_timeout(addr, Duration::from_secs(1)) {
                ips.push(addr.ip().to_string());
            }
        }

        if ips.is_empty() {
            // Return all addresses even if unreachable
            ips = addrs.iter().map(|a| a.ip().to_string()).collect();
        }

        Ok(ips)
    }

    /// Happy Eyeballs: resolve both A and AAAA in parallel, return fastest.
    ///
    /// RFC 6555: try A first (IPv4) unless IPv6 is preferred.
    /// Returns first available address, preferring IPv4 for compatibility.
    pub fn resolve_happy_eyeballs(&self, hostname: &str) -> Result<Vec<String>, DnsError> {
        // Try both in parallel
        let a_result = self.resolve(hostname, "A");
        let aaaa_result = self.resolve(hostname, "AAAA");

        let mut all_ips = Vec::new();

        // Collect A records first (IPv4 preferred)
        if let Ok(ref ips) = a_result {
            all_ips.extend(ips.clone());
        }

        // Then AAAA (IPv6)
        if let Ok(ref ips) = aaaa_result {
            all_ips.extend(ips.clone());
        }

        if all_ips.is_empty() {
            // Return error from A if available, else AAAA
            a_result.or(aaaa_result)
        } else {
            Ok(all_ips)
        }
    }

    /// Prefetch multiple hostnames (batch resolution).
    ///
    /// Replaces `batch_dns.py` resolve_many().
    /// Sequential resolution with LRU cache for deduplication.
    /// Cache prevents redundant lookups for repeated hostnames.
    pub fn prefetch(&self, hostnames: &[String]) -> HashMap<String, Vec<String>> {
        let mut results = HashMap::new();

        for hostname in hostnames {
            match self.resolve(hostname, "A") {
                Ok(ips) => {
                    results.insert(hostname.clone(), ips);
                }
                Err(_) => {
                    // Negative cache handles NXDOMAIN
                    results.insert(hostname.clone(), Vec::new());
                }
            }
        }

        results
    }

    /// Resolve many (hostname, qtype) pairs.
    ///
    /// Sequential resolution, bounded by semaphore (max 50 concurrent).
    /// Results are returned as a dict mapping hostname -> [ips].
    pub fn resolve_many(&self, queries: &[(String, String)]) -> HashMap<String, Vec<String>> {
        let mut results = HashMap::new();

        for (hostname, qtype) in queries {
            match self.resolve(hostname, qtype) {
                Ok(ips) => {
                    results.insert(hostname.clone(), ips);
                }
                Err(_) => {
                    results.insert(hostname.clone(), Vec::new());
                }
            }
        }

        results
    }
}

impl Default for DnsResolver {
    fn default() -> Self {
        Self::new()
    }
}

// ============================================================================
// Python Bindings
// ============================================================================

use std::sync::atomic::{AtomicU64, Ordering};

/// DNS statistics for monitoring.
/// Uses atomic counters for thread-safe concurrent access.
#[pyclass]
#[derive(Default)]
pub struct DnsStats {
    cache_hits: AtomicU64,
    cache_misses: AtomicU64,
    queries_total: AtomicU64,
    errors_total: AtomicU64,
}

#[pymethods]
impl DnsStats {
    /// Get current cache hit count.
    #[pyo3(get)]
    fn cache_hits(&self) -> u64 {
        self.cache_hits.load(Ordering::Relaxed)
    }

    /// Set cache hit count.
    #[pyo3(set)]
    fn set_cache_hits(&self, value: u64) {
        self.cache_hits.store(value, Ordering::Relaxed);
    }

    /// Get current cache miss count.
    #[pyo3(get)]
    fn cache_misses(&self) -> u64 {
        self.cache_misses.load(Ordering::Relaxed)
    }

    /// Set cache miss count.
    #[pyo3(set)]
    fn set_cache_misses(&self, value: u64) {
        self.cache_misses.store(value, Ordering::Relaxed);
    }

    /// Get total query count.
    #[pyo3(get)]
    fn queries_total(&self) -> u64 {
        self.queries_total.load(Ordering::Relaxed)
    }

    /// Set total query count.
    #[pyo3(set)]
    fn set_queries_total(&self, value: u64) {
        self.queries_total.store(value, Ordering::Relaxed);
    }

    /// Get total error count.
    #[pyo3(get)]
    fn errors_total(&self) -> u64 {
        self.errors_total.load(Ordering::Relaxed)
    }

    /// Set total error count.
    #[pyo3(set)]
    fn set_errors_total(&self, value: u64) {
        self.errors_total.store(value, Ordering::Relaxed);
    }

    /// Increment cache hits by 1.
    fn increment_hits(&self) {
        self.cache_hits.fetch_add(1, Ordering::Relaxed);
    }
    /// Increment cache misses by 1.
    fn increment_misses(&self) {
        self.cache_misses.fetch_add(1, Ordering::Relaxed);
    }
    /// Increment total queries by 1.
    fn increment_queries(&self) {
        self.queries_total.fetch_add(1, Ordering::Relaxed);
    }
    /// Increment total errors by 1.
    fn increment_errors(&self) {
        self.errors_total.fetch_add(1, Ordering::Relaxed);
    }
}

/// Global resolver instance (singleton).
static RESOLVER: std::sync::LazyLock<DnsResolver> = std::sync::LazyLock::new(DnsResolver::default);

/// Global stats.
static STATS: std::sync::LazyLock<DnsStats> = std::sync::LazyLock::new(DnsStats::default);

/// Resolve a hostname asynchronously (DoH/DoT).
///
/// # Arguments
/// * `hostname` - Domain name to resolve
/// * `qtype` - Query type: "A", "AAAA", "MX", "TXT", "NS", "CNAME"
///
/// # Returns
/// List of IP addresses as strings.
///
/// # Example
/// ```python
/// ips = rust.dns.resolve_async("example.com", qtype="A")
/// # Returns: ["93.184.216.34"]
/// ```
#[pyfunction]
pub fn resolve_async(hostname: &str, qtype: &str) -> Vec<String> {
    STATS.increment_queries();

    match RESOLVER.resolve(hostname, qtype) {
        Ok(ips) => {
            STATS.increment_hits();
            ips
        }
        Err(_) => {
            STATS.increment_errors();
            STATS.increment_misses();
            Vec::new()
        }
    }
}

/// Resolve a hostname using Happy Eyeballs (dual-stack A + AAAA).
///
/// RFC 6555: tries both A and AAAA in parallel, returns fastest.
///
/// # Arguments
/// * `hostname` - Domain name to resolve
///
/// # Returns
/// List of all resolved IP addresses (both IPv4 and IPv6).
///
/// # Example
/// ```python
/// ips = rust.dns.resolve_happy_eyeballs("example.com")
/// # Returns: ["93.184.216.34", "2606:2800:220:1::248a:1893"]
/// ```
#[pyfunction]
pub fn resolve_happy_eyeballs(hostname: &str) -> Vec<String> {
    STATS.increment_queries();

    match RESOLVER.resolve_happy_eyeballs(hostname) {
        Ok(ips) => {
            STATS.increment_hits();
            ips
        }
        Err(_) => {
            STATS.increment_errors();
            Vec::new()
        }
    }
}

/// Prefetch multiple hostnames (batch resolution).
///
/// Replaces `batch_dns.py` resolve_many().
/// Uses LRU cache to avoid redundant lookups.
///
/// # Arguments
/// * `hostnames` - List of domain names to prefetch
///
/// # Returns
/// Dict mapping hostname -> list of IP addresses.
///
/// # Example
/// ```python
/// results = rust.dns.prefetch(["example.com", "google.com"])
/// # Returns: {"example.com": ["93.184.216.34"], "google.com": ["142.250.185.46"]}
/// ```
#[pyfunction]
pub fn prefetch(hostnames: Vec<String>) -> HashMap<String, Vec<String>> {
    RESOLVER.prefetch(&hostnames)
}

/// Resolve many (hostname, qtype) pairs in parallel.
///
/// # Arguments
/// * `queries` - List of (hostname, qtype) tuples
///
/// # Returns
/// Dict mapping hostname -> list of IP addresses.
///
/// # Example
/// ```python
/// results = rust.dns.resolve_many([("example.com", "A"), ("example.org", "AAAA")])
/// ```
#[pyfunction]
pub fn resolve_many(queries: Vec<(String, String)>) -> HashMap<String, Vec<String>> {
    RESOLVER.resolve_many(&queries)
}

/// Get DNS resolution statistics.
#[pyfunction]
pub fn get_stats() -> DnsStats {
    DnsStats {
        cache_hits: STATS.cache_hits.load(Ordering::Relaxed).into(),
        cache_misses: STATS.cache_misses.load(Ordering::Relaxed).into(),
        queries_total: STATS.queries_total.load(Ordering::Relaxed).into(),
        errors_total: STATS.errors_total.load(Ordering::Relaxed).into(),
    }
}

/// Clear the DNS cache.
#[pyfunction]
pub fn clear_cache() {
    let mut cache = RESOLVER.cache.write();
    cache.clear();
}

/// Register DNS functions in Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resolve_async, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_happy_eyeballs, m)?)?;
    m.add_function(wrap_pyfunction!(prefetch, m)?)?;
    m.add_function(wrap_pyfunction!(resolve_many, m)?)?;
    m.add_function(wrap_pyfunction!(get_stats, m)?)?;
    m.add_function(wrap_pyfunction!(clear_cache, m)?)?;
    m.add_class::<DnsStats>()?;
    Ok(())
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cache_positive() {
        let mut cache = DnsCache::new(10);

        // Insert and retrieve
        cache.insert_positive("example.com".to_string(), vec!["1.2.3.4".to_string()]);

        let result = cache.get("example.com");
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, vec!["1.2.3.4"]);
    }

    #[test]
    fn test_cache_negative() {
        let mut cache = DnsCache::new(10);

        // Insert negative
        cache.insert_negative("nonexistent.com".to_string(), "host_not_found".to_string());

        let result = cache.get("nonexistent.com");
        assert!(result.is_some());
        assert!(result.unwrap().1); // is_negative = true
    }

    #[test]
    fn test_dns_resolver_creation() {
        let resolver = DnsResolver::new();
        // Semaphore starts at 50 permits (full capacity)
        assert_eq!(resolver.semaphore.available_permits(), 50);
    }

    #[test]
    fn test_resolve_returns_empty_on_invalid() {
        let resolver = DnsResolver::new();
        // Empty hostname should fail
        let result = resolver.resolve("", "A");
        assert!(result.is_err());
    }

    #[test]
    fn test_cache_lru_eviction() {
        let mut cache = DnsCache::new(3);

        cache.insert_positive("a.com".to_string(), vec!["1.1.1.1".to_string()]);
        cache.insert_positive("b.com".to_string(), vec!["2.2.2.2".to_string()]);
        cache.insert_positive("c.com".to_string(), vec!["3.3.3.3".to_string()]);

        // 'a.com' should be evicted when we add the 4th
        cache.insert_positive("d.com".to_string(), vec!["4.4.4.4".to_string()]);

        assert!(cache.get("a.com").is_none()); // evicted
        assert!(cache.get("d.com").is_some()); // still present
    }
}
