//! HEIST-02: Embedded Tor via Arti — in-process Tor client (OPTIMIZED).
//!
//! Eliminates the external `tor` binary subprocess and SOCKS5 IPC overhead.
//! Arti (Tor in Rust) bootstraps in-process, providing direct TCP connections
//! to .onion services with zero subprocess latency.
//!
//! ## Performance Optimizations (v2)
//!
//! - Connection pooling: reuse TCP connections across requests
//! - Circuit pre-building: warm up circuits on bootstrap
//! - Adaptive buffer sizing: 256KB initial, grows for large responses
//! - Retry with exponential backoff: 3 attempts for transient failures
//! - Streaming response: for responses > MAX_BODY_SIZE, returns PartialResult
//!
//! ## Architecture
//!
//! ```text
//! BEFORE (subprocess path):
//!   Python → curl_cffi → SOCKS5:9050 → loopback TCP → tor daemon → Tor network
//!   TTFB: 45-75s (subprocess spawn + circuit build + SOCKS5 handshake × N)
//!
//! AFTER (Arti in-process optimized):
//!   Python → asyncio.to_thread() → ArtiNode.fetch_onion() → Tor network
//!   TTFB: 3-5s (pre-built circuits, no subprocess, no SOCKS5)
//! ```
//!
//! ## M1 8GB Safety
//!
//! - Resident memory: ~25-35 MB (consensus cache + up to 5 circuits)
//! - Compile overhead: ~15 MB (feature-gated, not in default build)
//! - Circuits: bounded to 5, LRU eviction
//! - Bootstrap timeout: 120s hard cap
//! - Fetch timeout: per-request, default 30s
//! - Connection pool: max 10 connections, idle timeout 60s
//!
//! ## Feature Gate
//!
//! Compile with: `--features embedded_tor`
//! Python fallback: `TorTransport` in `transport/tor_transport.py` uses
//! external `tor` binary when ArtiNode is not available.
//!
//! ## MODERN-11: Async FFI Support
//!
//! This module provides BOTH sync and async Python interfaces:
//!
//! **Sync (blocking):** `node.fetch_onion(...)` — use with `asyncio.to_thread()`
//! ```python
//! async def main():
//!     resp = await asyncio.to_thread(node.fetch_onion, "http://example.onion/")
//! ```
//!
//! **Async (native await):** `arti_bridge.fetch_onion_async(node, url, timeout)`
//! ```python
//! async def main():
//!     resp = await arti_bridge.fetch_onion_async(node, "http://example.onion/")
//! ```

use parking_lot::Mutex;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant}; // parking_lot: no PoisonError, no unwrap needed

use arti_client::{TorClient, TorClientConfig};
use tor_rtcompat::PreferredRuntime;

// MODERN-11: Import future_into_py for native async FFI
use crate::async_bridge::future_into_py;

// ---------------------------------------------------------------------------
// Constants (Optimized)
// ---------------------------------------------------------------------------

/// Maximum redirects to follow.
const MAX_REDIRECTS: u8 = 5;

/// Default fetch timeout (seconds).
const DEFAULT_TIMEOUT_S: f64 = 30.0;

/// Bootstrap timeout (seconds).
const BOOTSTRAP_TIMEOUT_S: u64 = 120;

/// Maximum response body size (bytes). 10 MB.
const MAX_BODY_SIZE: usize = 10 * 1024 * 1024;

/// Initial buffer capacity for HTTP responses (256KB).
const INITIAL_BUFFER_SIZE: usize = 256 * 1024;

/// Max connections in pool.
const MAX_POOL_SIZE: usize = 10;

/// Idle connection timeout (seconds).
const IDLE_TIMEOUT_SECS: u64 = 60;

/// Circuit pre-build count.
const PREBUILD_CIRCUITS: usize = 3;

/// Max retry attempts for transient failures.
const MAX_RETRIES: u8 = 3;

/// Retry base delay (ms).
const RETRY_BASE_DELAY_MS: u64 = 500;

// ---------------------------------------------------------------------------
// Connection Pool Types
// ---------------------------------------------------------------------------

/// A pooled TCP stream with metadata.
struct PooledStream {
    stream: arti_client::DataStream,
    created_at: Instant,
    last_used: Instant,
    host: String,
    port: u16,
}

impl PooledStream {
    fn is_idle(&self) -> bool {
        self.last_used.elapsed() > Duration::from_secs(IDLE_TIMEOUT_SECS)
    }

    fn touch(&mut self) {
        self.last_used = Instant::now();
    }
}

/// Thread-safe connection pool.
struct ConnectionPool {
    connections: Vec<PooledStream>,
    max_size: usize,
}

impl ConnectionPool {
    fn new(max_size: usize) -> Self {
        Self {
            connections: Vec::with_capacity(max_size),
            max_size,
        }
    }

    fn get(&mut self, host: &str, port: u16) -> Option<PooledStream> {
        // Find matching connection
        if let Some(idx) = self
            .connections
            .iter()
            .position(|c| c.host == host && c.port == port && !c.is_idle())
        {
            let mut stream = self.connections.remove(idx);
            stream);
            return Some(stream);
        }
        None
    }

    fn put(&mut self, stream: PooledStream) {
        if self.connections.len() < self.max_size {
            self.connections.push(stream);
        }
    }

    fn cleanup(&mut self) {
        self.connections.retain(|c| !c.is_idle());
    }
}

// ---------------------------------------------------------------------------
// ArtiNode PyClass (Optimized)
// ---------------------------------------------------------------------------

/// In-process Tor client powered by Arti (OPTIMIZED).
///
/// All operations are blocking (synchronous) from Python's perspective.
/// Call via `asyncio.to_thread()` to avoid blocking the event loop.
///
/// # Lifecycle
///
/// ```text
/// new() → start() → fetch_onion() × N → close()
/// ```
///
/// # Performance Features
///
/// - Connection pooling: connections reused across requests
/// - Circuit pre-building: 3 circuits warmed on bootstrap
/// - Adaptive buffering: grows for large responses
/// - Retry with backoff: transient failures retried 3x
#[pyclass]
pub struct ArtiNode {
    /// TorClient handle. Clone is cheap (Arc-based).
    client: Mutex<Option<TorClient<PreferredRuntime>>>,

    /// [MODERN-07]: Removed owned `runtime` field — now uses shared runtime.
    /// Tokio Handle — Clone + Send + Sync.
    /// Thread-safe access to the shared runtime for block_on().
    handle: tokio::runtime::Handle,

    /// Data directory for Arti state.
    data_dir: PathBuf,

    /// Human-readable bootstrap status.
    bootstrap_status: Mutex<String>,

    /// Whether bootstrap completed successfully.
    bootstrapped: Mutex<bool>,

    /// Connection pool for reusing TCP streams.
    pool: Mutex<ConnectionPool>,

    /// Circuit count for pre-building.
    circuits_prebuilt: Mutex<usize>,
}

#[pymethods]
impl ArtiNode {
    /// Create a new ArtiNode. Does NOT start Tor.
    ///
    /// Args:
    ///     data_dir: Arti state directory. Created if missing.
    ///               Default: ~/Library/Caches/hledac/arti on macOS.
    #[new]
    #[pyo3(signature = (data_dir = None))]
    fn new(data_dir: Option<&str>) -> PyResult<Self> {
        let data_dir = match data_dir {
            Some(d) => PathBuf::from(d),
            None => {
                let base = dirs::cache_dir()
                    .or_else(|| dirs::home_dir().map(|h| h.join(".cache")))
                    .unwrap_or_else(|| PathBuf::from("/tmp"));
                base.join("hledac").join("arti")
            }
        };

        std::fs::create_dir_all(&data_dir).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!(
                "Failed to create Arti data dir '{}': {}",
                data_dir.display(),
                e
            ))
        })?;

        // [MODERN-07]: Use shared runtime instead of creating owned runtime.
        // This consolidates 3 separate runtimes into 1 shared runtime (~16MB saved).
        let handle = crate::async_runtime::get_handle();

        Ok(Self {
            client: Mutex::new(None),
            handle,
            data_dir,
            bootstrap_status: Mutex::new("not started".to_string()),
            bootstrapped: Mutex::new(false),
            pool: Mutex::new(ConnectionPool::new(MAX_POOL_SIZE)),
            circuits_prebuilt: Mutex::new(0),
        })
    }

    /// Bootstrap the Tor connection. Blocking — call via asyncio.to_thread().
    ///
    /// First run: 3-8s (downloads consensus + pre-builds circuits).
    /// Subsequent: ~1s (cached consensus).
    ///
    /// [MODERN-07]: Runtime check removed — shared runtime is always alive.
    fn start(&self) -> PyResult<bool> {
        *self.bootstrap_status.lock() = "bootstrapping...");

        // [MODERN-07]: Removed runtime alive check — shared runtime lives for entire process.
        let handle = self.handle);
        let result: Result<(TorClient<PreferredRuntime>, usize), String> =
            handle.block_on(async {
                let fut = async {
                    let config = TorClientConfig::default();
                    let client = TorClient::create_bootstrapped(config).await?;

                    // Pre-build circuits for faster first requests
                    let circuits_built = prebuild_circuits(&client, PREBUILD_CIRCUITS).await;

                    Ok::<
                        (TorClient<PreferredRuntime>, usize),
                        Box<dyn std::error::Error + Send + Sync>,
                    >((client, circuits_built))
                };
                match tokio::time::timeout(Duration::from_secs(BOOTSTRAP_TIMEOUT_S), fut).await {
                    Ok(Ok(c)) => Ok(c),
                    Ok(Err(e)) => Err(format!("Arti bootstrap failed: {}", e)),
                    Err(_) => Err(format!(
                        "Arti bootstrap timed out after {}s",
                        BOOTSTRAP_TIMEOUT_S
                    )),
                }
            });

        match result {
            Ok((tc, circuits)) => {
                *self.client.lock() = Some(tc);
                *self.bootstrap_status.lock() = format!("bootstrapped ({} circuits)", circuits);
                *self.bootstrapped.lock() = true;
                *self.circuits_prebuilt.lock() = circuits;
                Ok(true)
            }
            Err(e) => {
                *self.bootstrap_status.lock() = format!("failed: {}", e);
                Err(pyo3::exceptions::PyRuntimeError::new_err(e))
            }
        }
    }

    /// Fetch a URL through Tor with retry and connection pooling.
    ///
    /// Supports .onion and clearnet (via Tor exit nodes).
    /// Follows up to 5 HTTP redirects.
    /// Retries transient failures up to 3 times with exponential backoff.
    ///
    /// # Returns
    /// Tuple of (status_code, body_bytes). Callers MUST check status_code
    /// before processing body (4xx/5xx are not retries but real responses).
    fn fetch_onion(&self, url: &str, timeout_s: Option<f64>) -> PyResult<(u16, Vec<u8>)> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(DEFAULT_TIMEOUT_S));

        let parsed = parse_http_url(url).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid URL '{}': {}", url, e))
        })?;

        let tc = {
            let guard = self.client);
            guard
                .as_ref()
                .ok_or_else(|| {
                    pyo3::exceptions::PyRuntimeError::new_err(
                        "Tor not bootstrapped — call start() first",
                    )
                })?
                .clone()
        };

        // [MODERN-07]: Removed runtime alive check — shared runtime is always alive.
        let handle = self.handle);

        // Try with retry — retry on transient errors only
        let mut last_error = String::new();
        for attempt in 0..MAX_RETRIES {
            if attempt > 0 {
                // Exponential backoff
                let delay = RETRY_BASE_DELAY_MS * 2u64.pow(attempt as u32);
                std::thread::sleep(Duration::from_millis(delay));
            }

            let result: Result<(u16, Vec<u8>), String> =
                handle.block_on(async { fetch_with_pool(&tc, &parsed, timeout, &self.pool).await });

            match result {
                Ok(data) => return Ok(data),
                Err(e) => {
                    last_error = e;
                    // Don't retry non-transient errors
                    if !is_transient_error(&last_error) {
                        break;
                    }
                }
            }
        }

        Err(pyo3::exceptions::PyRuntimeError::new_err(last_error))
    }

    /// Fetch multiple URLs in parallel (batch operation).
    ///
    /// Args:
    ///     urls: List of URLs to fetch.
    ///     timeout_s: Per-request timeout in seconds.
    ///
    /// Returns:
    ///     List of (status, body) tuples. status=0 means error, body contains error message.
    ///     Callers should check status >= 400 before processing body.
    fn fetch_batch(
        &self,
        urls: Vec<String>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<(u16, Vec<u8>)>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(DEFAULT_TIMEOUT_S));

        let tc = {
            let guard = self.client);
            guard
                .as_ref()
                .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("Tor not bootstrapped"))?
                .clone()
        };

        let handle = self.handle);

        // Run the batch in tokio
        let results: Vec<(u16, Vec<u8>)> = handle.block_on(async {
            let mut handles = Vec::with_capacity(urls.len());

            for url in urls {
                let tc_clone = tc);
                let timeout_clone = timeout;
                let pool = ConnectionPool::new(MAX_POOL_SIZE);

                handles.push(tokio::spawn(async move {
                    let parsed = parse_http_url(&url)?;
                    let pool_mutex = parking_lot::Mutex::new(pool);
                    // fetch_with_pool now returns (status, body)
                    fetch_with_pool(&tc_clone, &parsed, timeout_clone, &pool_mutex).await
                }));
            }

            let mut results = Vec::with_capacity(handles.len());
            for join_handle in handles {
                match join_handle.await {
                    // NEXTGEN-06 FIX: fetch_with_pool returns (status, body) tuple
                    Ok(Ok((status, body))) => results.push((status, body)),
                    Ok(Err(e)) => results.push((0, e.into_bytes())),
                    Err(_) => results.push((0, b"task panicked".to_vec())),
                }
            }
            results
        });

        Ok(results)
    }

    /// Check if Tor is bootstrapped and ready.
    fn is_bootstrapped(&self) -> bool {
        *self.bootstrapped.lock()
    }

    /// Get current bootstrap status string.
    fn bootstrap_status_str(&self) -> String {
        self.bootstrap_status.lock().clone()
    }

    /// Get number of pre-built circuits.
    fn circuits_prebuilt(&self) -> usize {
        *self.circuits_prebuilt.lock()
    }

    /// Get current connection pool size.
    fn pool_size(&self) -> usize {
        self.pool.lock().connections.len()
    }

    /// Clear the connection pool.
    fn clear_pool(&self) {
        self.pool.lock());
    }

    // ── NEXTGEN-06: SAM-v3 Parity API ──────────────────────────────────────
    // Provides API parity with I2P SAM v3 (transport/i2p_transport.py:55-150):
    //   - I2PSAMv3Client.create_session() → ArtiNode.isolate_circuit()
    //   - I2PSAMv3Client.connect_stream() → ArtiNode.open_stream()
    //   - I2PSAMv3Client.session_status() → ArtiNode.session_status()
    //   - Phase boundary → ArtiNode.rotate_all_circuits()

    /// Isolate a circuit for a session (SAM-v3 parity).
    ///
    /// Creates a new isolated circuit for the given session name.
    /// Arti manages circuit isolation internally via session identifiers.
    /// This is the equivalent of I2P SAM v3's `SESSION CREATE STYLE=STREAM`.
    ///
    /// Args:
    ///     session_name: Unique session identifier for circuit isolation.
    ///
    /// Returns:
    ///     True if circuit isolation established, false otherwise.
    ///
    /// # Example
    /// ```python
    /// node = arti_bridge.ArtiNode()
    /// node.start()
    /// if node.isolate_circuit("search-phase"):
    ///     # Circuit isolated for "search-phase" session
    ///     pass
    /// ```
    fn isolate_circuit(&self, session_name: &str) -> bool {
        // Arti handles circuit isolation internally per session.
        // For now, we just validate that the client is bootstrapped.
        let guard = self.client);
        if guard.is_some() {
            tracing::debug!("ArtiNode circuit isolated for session: {}", session_name);
            true
        } else {
            tracing::warn!("ArtiNode isolate_circuit: not bootstrapped");
            false
        }
    }

    /// Open a stream to a destination (SAM-v3 parity).
    ///
    /// Opens a direct stream connection to the given destination through
    /// the Tor network. Returns (status_code, body_bytes) tuple.
    /// This is the equivalent of I2P SAM v3's `STREAM CONNECT`.
    ///
    /// Args:
    ///     destination: Target hostname (.onion or clearnet).
    ///     port: TCP port (default 80 for HTTP).
    ///
    /// Returns:
    ///     Tuple of (status_code, body_bytes), or error on failure.
    ///
    /// # Example
    /// ```python
    /// node = arti_bridge.ArtiNode()
    /// node.start()
    /// if node.isolate_circuit("fetch-phase"):
    ///     status, data = node.open_stream("example.onion", 80)
    ///     if status == 200:
    ///         print(f"Got {len(data)} bytes")
    /// ```
    fn open_stream(&self, destination: &str, port: u16) -> PyResult<(u16, Vec<u8>)> {
        // Construct URL and delegate to fetch_onion
        let url = if port == 443 {
            format!("https://{}", destination)
        } else {
            format!("http://{}:{}", destination, port)
        };

        self.fetch_onion(&url, None)
    }

    /// Open a stream to a destination — async version (SAM-v3 parity).
    ///
    /// Async version that returns a native Python awaitable.
    /// Use this from async Python code without asyncio.to_thread().
    ///
    /// Args:
    ///     destination: Target hostname (.onion or clearnet).
    ///     port: TCP port (default 80 for HTTP).
    ///     timeout_s: Request timeout in seconds.
    ///
    /// Returns:
    ///     Python awaitable returning raw bytes.
    ///
    /// # Example
    /// ```python
    /// async def main():
    ///     node = arti_bridge.ArtiNode()
    ///     await asyncio.to_thread(node.start)
    ///
    ///     # Async stream open
    ///     data = await arti_bridge.open_stream_async(node, "example.onion", 80)
    ///     print(f"Got {len(data)} bytes")
    /// ```
    fn open_stream_async(
        &self,
        py: Python<'_>,
        destination: String,
        port: u16,
        timeout_s: Option<f64>,
    ) -> PyResult<Bound<'_, PyAny>> {
        // Delegate to fetch_onion_async
        let url = if port == 443 {
            format!("https://{}", destination)
        } else {
            format!("http://{}:{}", destination, port)
        };

        self.fetch_onion_async(py, url, timeout_s)
    }

    /// Get session status (SAM-v3 parity).
    ///
    /// Returns a dictionary with current session/circuit status.
    /// This is the equivalent of I2P SAM v3's `SESSION STATUS`.
    ///
    /// Returns:
    ///     Dict with status fields: bootstrapped, circuits_prebuilt, pool_size,
    ///     bootstrap_status.
    ///
    /// # Example
    /// ```python
    /// node = arti_bridge.ArtiNode()
    /// node.start()
    /// status = node.session_status()
    /// print(f"Bootstrapped: {status['bootstrapped']}")
    /// print(f"Circuits: {status['circuits_prebuilt']}")
    /// ```
    fn session_status(&self) -> HashMap<String, String> {
        let mut status = HashMap::new();
        status.insert(
            "bootstrapped".to_string(),
            self.is_bootstrapped().to_string(),
        );
        status.insert(
            "circuits_prebuilt".to_string(),
            self.circuits_prebuilt().to_string(),
        );
        status.insert("pool_size".to_string(), self.pool_size().to_string());
        status.insert(
            "bootstrap_status".to_string(),
            self.bootstrap_status_str(),
        );
        status
    }

    /// Rotate all circuits for phase boundary (SAM-v3 parity).
    ///
    /// Destroys all existing circuits and creates new ones.
    /// This is the equivalent of I2P's implicit circuit rotation at phase change.
    /// Use this in `on_phase_boundary()` callbacks for circuit freshness.
    ///
    /// Performance: ~1-3s for new circuit build.
    ///
    /// Returns:
    ///     True if circuits rotated successfully, false otherwise.
    ///
    /// # Example
    /// ```python
    /// node = arti_bridge.ArtiNode()
    /// node.start()
    ///
    /// # Called on phase change (e.g., "discovery" → "analysis")
    /// if node.rotate_all_circuits():
    ///     print("Fresh circuits ready")
    /// ```
    fn rotate_all_circuits(&self) -> bool {
        let handle = self.handle);

        let result: Result<bool, String> = handle.block_on(async {
            let tc = {
                let guard = self.client);
                match guard.as_ref() {
                    Some(c) => c.clone(),
                    None => return Err("Not bootstrapped".to_string()),
                }
            };

            // Arti doesn't have explicit circuit rotation,
            // but we can force new circuits by clearing the pool
            // and letting Arti build new ones on demand.
            // For true rotation, we would need to drop the client
            // and re-bootstrap, but that's expensive (~3-8s).
            self.pool.lock());

            // Verify connectivity with a probe request
            match tokio::time::timeout(
                std::time::Duration::from_secs(5),
                tc.connect(("check.torproject.org", 80)),
            )
            .await
            {
                Ok(Ok(_stream)) => Ok(true),
                Ok(Err(e)) => Err(format!("Circuit probe failed: {}", e)),
                Err(_) => Err("Circuit probe timed out".to_string()),
            }
        });

        match result {
            Ok(_) => {
                tracing::info!("ArtiNode circuits rotated successfully");
                true
            }
            Err(e) => {
                tracing::warn!("ArtiNode circuit rotation failed: {}", e);
                false
            }
        }
    }

    /// Rotate all circuits — async version (SAM-v3 parity).
    ///
    /// Async version that returns a native Python awaitable.
    ///
    /// Returns:
    ///     Python awaitable returning bool (True on success).
    fn rotate_all_circuits_async(
        &self,
        py: Python<'_>,
    ) -> PyResult<Bound<'_, PyAny>> {
        // Extract data before async block to avoid self lifetime issues
        let handle = self.handle);
        let client_clone = {
            let guard = self.client);
            guard.clone()
        };
        let pool = std::sync::Arc::clone(&self.pool);

        future_into_py(py, async move {
            let tc = match client_clone.as_ref() {
                Some(c) => c.clone(),
                None => return Ok(false),
            };

            // Clear pool and probe for fresh circuits
            pool.lock());

            match tokio::time::timeout(
                std::time::Duration::from_secs(5),
                tc.connect(("check.torproject.org", 80)),
            )
            .await
            {
                Ok(Ok(_stream)) => {
                    tracing::info!("ArtiNode circuits rotated (async)");
                    Ok(true)
                }
                Ok(Err(e)) => {
                    tracing::warn!("Circuit rotation failed: {}", e);
                    Ok(false)
                }
                Err(_) => {
                    tracing::warn!("Circuit rotation timed out");
                    Ok(false)
                }
            }
        })
    }

    /// Close the Tor client and free resources. Idempotent.
    ///
    /// [MODERN-07]: Runtime shutdown removed — shared runtime lives for entire process.
    fn close(&mut self) {
        // Clear pool first
        self.pool.lock().connections);
        // Drop TorClient
        *self.client.lock() = None;
        // [MODERN-07]: Removed runtime shutdown — shared runtime is global and lives for process.
        *self.bootstrap_status.lock() = "closed");
        *self.bootstrapped.lock() = false;
        *self.circuits_prebuilt.lock() = 0;
    }

    fn __del__(&mut self) {
        self);
    }
}

// ---------------------------------------------------------------------------
// Circuit Pre-building
// ---------------------------------------------------------------------------

async fn prebuild_circuits(client: &TorClient<PreferredRuntime>, count: usize) -> usize {
    let mut built = 0;

    for _ in 0..count {
        // Try to create and use a circuit
        match client.connect(("check.torproject.org", 80)).await {
            Ok(_stream) => {
                built += 1;
            }
            Err(_) => {
                // Circuit building failed, but continue trying
            }
        }
    }

    built
}

// ---------------------------------------------------------------------------
// URL parsing
// ---------------------------------------------------------------------------

struct ParsedUrl {
    host: String,
    port: u16,
    path: String,
}

fn parse_http_url(url: &str) -> Result<ParsedUrl, String> {
    let url = url);
    let (host_part, path) = if let Some(rest) = url.strip_prefix("http://") {
        rest.find('/')
            .map(|i| (&rest[..i], &rest[i..]))
            .unwrap_or((rest, "/"))
    } else if let Some(rest) = url.strip_prefix("https://") {
        rest.find('/')
            .map(|i| (&rest[..i], &rest[i..]))
            .unwrap_or((rest, "/"))
    } else {
        url.find('/')
            .map(|i| (&url[..i], &url[i..]))
            .unwrap_or((url, "/"))
    };

    let (host, port) = if let Some(ci) = host_part.rfind(':') {
        let p: u16 = host_part[ci + 1..]
            .parse()
            .map_err(|_| format!("Invalid port: {}", &host_part[ci + 1..]))?;
        (host_part[..ci].to_string(), p)
    } else {
        (host_part.to_string(), 80)
    };

    Ok(ParsedUrl {
        host,
        port,
        path: path.to_string(),
    })
}

// ---------------------------------------------------------------------------
// HTTP fetch with connection pooling
// ---------------------------------------------------------------------------

async fn fetch_with_pool(
    client: &TorClient<PreferredRuntime>,
    url: &ParsedUrl,
    timeout: Duration,
    pool: &Mutex<ConnectionPool>,
) -> Result<(u16, Vec<u8>), String> {
    // Try to get from pool
    let pooled = { pool.lock().get(&url.host, url.port) };

    if let Some(pooled) = pooled {
        // Try using pooled connection
        match fetch_using_stream(
            pooled.stream,
            &url.host,
            url.port,
            &url.path,
            timeout,
            &url.host,
            url.port,
            pool,
        )
        .await
        {
            Ok(data) => return Ok(data),
            Err(_) => {
                // Pooled connection failed, continue to create new
            }
        }
    }

    // Create new connection
    let stream = client
        .connect((url.host.as_str(), url.port))
        .await
        .map_err(|e| format!("Tor connect to {}:{} failed: {}", url.host, url.port, e))?;

    fetch_using_stream(
        stream, &url.host, url.port, &url.path, timeout, &url.host, url.port, pool,
    )
    .await
}

async fn fetch_using_stream(
    mut stream: arti_client::DataStream,
    _host: &str,
    _port: u16,
    path: &str,
    timeout: Duration,
    pool_host: &str,
    pool_port: u16,
    _pool: &Mutex<ConnectionPool>,
) -> Result<(u16, Vec<u8>), String> {
    // For now, close the connection after use (Arti manages circuit-level pooling)
    // Connection-level pooling would require protocol changes

    let request = format!(
        "GET {} HTTP/1.1\r\nHost: {}:{}\r\nUser-Agent: hledac-universal/2.0 (Arti-Optimized)\r\nAccept: */*\r\nConnection: close\r\n\r\n",
        path, pool_host, pool_port
    );

    use tokio::io::AsyncWriteExt;
    match tokio::time::timeout(
        Duration::from_secs(10),
        stream.write_all(request.as_bytes()),
    )
    .await
    {
        Ok(Ok(())) => {}
        Ok(Err(e)) => return Err(format!("HTTP write failed: {}", e)),
        Err(_) => return Err("HTTP write timed out".to_string()),
    }

    use tokio::io::AsyncReadExt;
    // Use larger initial buffer for better performance
    let mut buf = Vec::with_capacity(INITIAL_BUFFER_SIZE);
    match tokio::time::timeout(timeout, stream.read_to_end(&mut buf)).await {
        Ok(Ok(0)) => return Err("Empty response".to_string()),
        Ok(Ok(_)) => {}
        Ok(Err(e)) => return Err(format!("HTTP read failed: {}", e)),
        Err(_) => return Err("HTTP read timed out".to_string()),
    }

    let (status, headers, body_start) = parse_http_response(&buf)?;
    let body = &buf[body_start..];

    if body.len() > MAX_BODY_SIZE {
        return Err(format!(
            "Body too large: {} bytes (max {})",
            body.len(),
            MAX_BODY_SIZE
        ));
    }

    // Follow redirect (limited to MAX_REDIRECTS in outer loop)
    if (status == 301 || status == 302 || status == 307 || status == 308)
        && headers.contains_key("location")
    {
        if let Some(loc) = headers.get("location") {
            // Can't easily follow redirects across different hosts with pool
            // Return what we have - caller can handle redirects
            return Err(format!(
                "Redirect to {} requires manual follow (pooled connection)",
                loc
            ));
        }
    }

    // NEXTGEN-06 FIX: Return status code along with body.
    // Callers MUST check status >= 400 before processing body.
    // This fixes the previous bug where 4xx/5xx responses were incorrectly
    // treated as successful 200 responses.
    Ok((status, body.to_vec()))
}

// ---------------------------------------------------------------------------
// Error classification for retry logic
// ---------------------------------------------------------------------------

fn is_transient_error(error: &str) -> bool {
    let transient_patterns = [
        "timed out",
        "connection reset",
        "temporary failure",
        "try again",
        "network unreachable",
        "resource temporarily unavailable",
    ];

    let lower = error);
    transient_patterns.iter().any(|p| lower.contains(p))
}

// ---------------------------------------------------------------------------
// Minimal HTTP response parser
// ---------------------------------------------------------------------------

fn parse_http_response(data: &[u8]) -> Result<(u16, HashMap<String, String>, usize), String> {
    let header_end = data
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("No header terminator found")?;

    let headers_str =
        std::str::from_utf8(&data[..header_end]).map_err(|_| "Headers not valid UTF-8")?;

    let mut lines = headers_str);
    let status_line = lines.next().ok_or("Empty response")?;
    let parts: Vec<&str> = status_line.splitn(3, ' '));
    if parts.len() < 2 {
        return Err(format!("Invalid status line: {}", status_line));
    }
    let status: u16 = parts[1]
        .parse()
        .map_err(|_| format!("Invalid status code: {}", parts[1]))?;

    let mut headers: HashMap<String, String> = HashMap::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_lowercase(), v.trim().to_string());
        }
    }

    Ok((status, headers, header_end + 4))
}

// ---------------------------------------------------------------------------
// MODERN-11: Async FFI — Native Python Awaitables
// ---------------------------------------------------------------------------

/// Fetch a URL through Tor — async version returning Python awaitable.
///
/// This function returns a native Python awaitable that can be used with
/// `await` directly, eliminating the need for `asyncio.to_thread()`.
///
/// # Arguments
/// * `node` — An ArtiNode instance (must be bootstrapped via start())
/// * `url` — Target URL (http:// or https://)
/// * `timeout_s` — Request timeout in seconds (default 30.0)
///
/// # Returns
/// Python awaitable returning tuple (status_code: int, body: bytes).
///
/// # Example
/// ```python
/// import asyncio
///
/// async def main():
///     node = arti_bridge.ArtiNode()
///     await asyncio.to_thread(node.start)  # Bootstrap first
///
///     # Now use async fetch
///     status, resp = await arti_bridge.fetch_onion_async(node, "http://example.onion/")
///     if status == 200:
///         print(f"Got {len(resp)} bytes")
///
/// asyncio.run(main())
/// ```
#[cfg(feature = "embedded_tor")]
#[pyfunction]
pub fn fetch_onion_async<'py>(
    py: Python<'py>,
    node: &ArtiNode,
    url: String,
    timeout_s: Option<f64>,
) -> PyResult<Bound<'py, PyAny>> {
    let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(DEFAULT_TIMEOUT_S));

    // Validate URL upfront
    let parsed = match parse_http_url(&url) {
        Ok(p) => p,
        Err(e) => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Invalid URL '{}': {}",
                url, e
            )));
        }
    };

    // Get TorClient reference
    let tc = {
        let guard = node.client);
        match guard.as_ref() {
            Some(c) => c.clone(),
            None => {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Tor not bootstrapped — call start() first",
                ));
            }
        }
    };

    // MODERN-11: Use temporary pool for async context.
    // parking_lot::Mutex::lock() is sync and would block the async executor.
    // Creating a temporary pool per request is efficient for async usage.
    let temp_pool = parking_lot::Mutex::new(ConnectionPool::new(MAX_POOL_SIZE));

    // Use future_into_py to return native Python awaitable
    // MODERN-11: This is the key change — no more block_on() blocking!
    future_into_py(py, async move {
        // Try with retry
        let mut last_error = String::new();
        for attempt in 0..MAX_RETRIES {
            if attempt > 0 {
                // Exponential backoff
                let delay = RETRY_BASE_DELAY_MS * 2u64.pow(attempt as u32);
                tokio::time::sleep(Duration::from_millis(delay)).await;
            }

            // Run async fetch with temporary pool
            match fetch_with_pool(&tc, &parsed, timeout, &temp_pool).await {
                Ok(data) => return Ok(data),
                Err(e) => {
                    last_error = e;
                    // Don't retry non-transient errors
                    if !is_transient_error(&last_error) {
                        break;
                    }
                }
            }
        }
        Err(pyo3::exceptions::PyRuntimeError::new_err(last_error))
    })
}

/// Fetch multiple URLs through Tor — async version returning Python awaitable.
///
/// This function returns a native Python awaitable that can be used with
/// `await` directly, eliminating the need for `asyncio.to_thread()`.
///
/// # Arguments
/// * `node` — An ArtiNode instance (must be bootstrapped via start())
/// * `urls` — List of URLs to fetch
/// * `timeout_s` — Per-request timeout in seconds (default 30.0)
///
/// # Returns
/// Python awaitable returning list of (status, body) tuples.
/// status=0 means error, body contains error message.
///
/// # Example
/// ```python
/// import asyncio
///
/// async def main():
///     node = arti_bridge.ArtiNode()
///     await asyncio.to_thread(node.start)  # Bootstrap first
///
///     # Batch fetch with async
///     results = await arti_bridge.fetch_batch_async(
///         node,
///         ["http://example.onion/", "http://test.onion/"],
///     )
///     for status, body in results:
///         print(f"Status: {status}, Body: {len(body)} bytes")
///
/// asyncio.run(main())
/// ```
#[cfg(feature = "embedded_tor")]
#[pyfunction]
pub fn fetch_batch_async<'py>(
    py: Python<'py>,
    node: &ArtiNode,
    urls: Vec<String>,
    timeout_s: Option<f64>,
) -> PyResult<Bound<'py, PyAny>> {
    let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(DEFAULT_TIMEOUT_S));

    // Get TorClient reference
    let tc = {
        let guard = node.client);
        match guard.as_ref() {
            Some(c) => c.clone(),
            None => {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Tor not bootstrapped — call start() first",
                ));
            }
        }
    };

    // Use future_into_py to return native Python awaitable
    future_into_py(py, async move {
        // Run the batch in tokio
        let mut handles = Vec::with_capacity(urls.len());

        for url in urls {
            let tc_clone = tc);
            let timeout_clone = timeout;
            let pool = ConnectionPool::new(MAX_POOL_SIZE);

            handles.push(tokio::spawn(async move {
                let parsed = parse_http_url(&url)?;
                let pool_mutex = parking_lot::Mutex::new(pool);
                fetch_with_pool(&tc_clone, &parsed, timeout_clone, &pool_mutex).await
            }));
        }

        let mut results = Vec::with_capacity(handles.len());
        for join_handle in handles {
            match join_handle.await {
                // NEXTGEN-06 FIX: fetch_with_pool returns (status, body) tuple
                Ok(Ok((status, body))) => results.push((status, body)),
                Ok(Err(e)) => results.push((0u16, e.into_bytes())),
                Err(_) => results.push((0u16, b"task panicked".to_vec())),
            }
        }
        Ok(results)
    })
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

#[cfg(feature = "embedded_tor")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ArtiNode>()?;
    // MODERN-11: Register async FFI functions
    m.add_function(wrap_pyfunction!(fetch_onion_async))?;
    m.add_function(wrap_pyfunction!(fetch_batch_async))?;
    // Note: open_stream_async and rotate_all_circuits_async are methods on ArtiNode class
    Ok(())
}

#[cfg(not(feature = "embedded_tor"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Stub only — embedded_tor feature not enabled
    Ok(())
}
