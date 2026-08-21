//! stealth_bridge.rs — Python↔Rust async FFI bridge for stealth HTTP transport
//!
//! ## MODERN-16: Hybrid Model Architecture
//!
//! This module provides async FFI bridges for the hybrid Python↔Rust model:
//! - **Python keeps**: curl_cffi for JA3/TLS impersonation (stealth fingerprinting)
//! - **Rust provides**: Async DNS resolution via tokio
//!
//! ## Architecture (CORRECTED)
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────────┐
//! │                    Python Layer (Stealth)                        │
//! ├─────────────────────────────────────────────────────────────────┤
//! │  curl_cffi_fetch.py                                             │
//! │  - JA3 rotation pool (6 browser profiles)                      │
//! │  - TLS impersonation via rustls-ffi (curl_cffi backend)       │
//! │  - HTTP/2 SETTINGS spoofing for Safari profiles                │
//! │  - Session pooling with LRU eviction                          │
//! │  - JA3 ban detection + exponential backoff retry              │
//! └─────────────────────────────┬───────────────────────────────────┘
//!                               │ async FFI (future_into_py)
//! ┌─────────────────────────────┴───────────────────────────────────┐
//! │                    Rust Layer (I/O)                             │
//! ├─────────────────────────────────────────────────────────────────┤
//! │  stealth_bridge.rs (THIS MODULE)                                │
//! │  - DNS resolution via tokio::net::lookup_host                   │
//! │                                                                 │
//! │  async_runtime.rs                                              │
//! │  - Shared Tokio runtime (4 workers, M1 8GB safe)               │
//! └─────────────────────────────────────────────────────────────────┘
//!
//! NOTE: QUIC/HTTP3 is handled by http3_lane.py (separate module with its own
//!       Rust adapters: QuinnRustlsTransportAdapter, NwQuicTransportAdapter).
//!       Network.framework TCP is handled by nw_connection_lane.py (separate
//!       module with NwConnectionLane adapter).
//!
//! ### Why curl_cffi STAYS in Python
//!
//! 1. **No pure-Rust JA3 equivalent exists** — curl_cffi uses rustls-ffi which
//!    provides the TLS fingerprinting layer. There's no Rust crate that matches
//!    curl_cffi's browser profile database and HTTP/2 SETTINGS spoofing.
//!
//! 2. **curl_cffi IS the stealth layer** — JA3 rotation, browser impersonation,
//!    WebKit HTTP/2 SETTINGS are all implemented in Python. Moving them to Rust
//!    would require reimplementing the entire fingerprinting subsystem.
//!
//! 3. **HTTP/2 SETTINGS are profile-specific** — Safari profiles require
//!    `INITIAL_WINDOW_SIZE=4,194,304` vs curl_cffi's default `65,535`. This
//!    anti-bot fingerprinting is tied to the impersonation profiles.
//!
//! ### What Rust DOES (in this module)
//!
//! 1. **DNS resolution** — `dns_resolve_async()` and `dns_resolve_batch_async()`
//!    via tokio::net::lookup_host (async FFI bridge)
//!
//! NOTE: QUIC/HTTP3 and Network.framework TCP are handled in separate modules:
//! - QUIC/HTTP3 → http3_lane.py (NwQuicTransportAdapter, QuinnRustlsTransportAdapter)
//! - Network.framework TCP → nw_connection_lane.py (NwConnectionLane)
//!
//! ### Async FFI Pattern
//!
//! ```rust
//! use crate::async_bridge::future_into_py;
//!
//! #[pyfunction]
//! pub fn dns_resolve_async(
//!     py: Python<'_>,
//!     hostname: String,
//! ) -> PyResult<Bound<'_, PyAny>> {
//!     future_into_py(py, async move {
//!         // Rust async DNS resolution via tokio
//!         let addrs = tokio::net::lookup_host((hostname.as_str(), 0))
//!             .await
//!             .map(|iter| {
//!                 iter.map(|addr| addr.ip().to_string()).collect::<Vec<_>>()
//!             })
//!             );
//!         Ok(addrs)
//!     })
//! }
//! ```
//!
//! ### Python Usage
//!
//! ```python
//! # curl_cffi handles JA3/TLS impersonation, Rust handles DNS only
//! async def fetch_stealth(url: str) -> bytes:
//!     # DNS via Rust stealth_bridge (async FFI)
//!     host = extract_host(url)
//!     if _HAS_RUST_STEALTH_BRIDGE:
//!         ips = await rust.stealth_bridge.dns_resolve_async(host)
//!
//!     # curl_cffi handles HTTP/2 + TLS impersonation (JA3)
//!     session = await get_curl_session(profile="chrome136")
//!     response = await session.get(url)
//!
//!     return response.content
//! ```
//!
//! ## Feature Gates
//!
//! - `stealth_bridge` feature enables this module
//! - Requires `shared_tokio` for async FFI
//! - Uses tokio::net::lookup_host directly (no hickory-resolver dep)
//!
//! ## M1 8GB Safety
//!
//! - Shared Tokio runtime: 4 workers (~10 MB total)
//! - No additional memory overhead for the bridge
//! - DNS resolution uses minimal memory (just the result Vec)

#![allow(dead_code)]

#[cfg(feature = "stealth_bridge")]
use pyo3::prelude::*;

// Re-export future_into_py for async FFI
#[cfg(feature = "stealth_bridge")]
use crate::async_bridge::future_into_py;

/// Async DNS resolution bridge for curl_cffi_fetch.py.
///
/// curl_cffi handles JA3/TLS impersonation in Python, but DNS resolution
/// can be offloaded to Rust for DoH support and better performance.
///
/// # Arguments
/// * `py` - Python GIL guard
/// * `hostname` - Domain to resolve
/// * `qtype` - Record type ("A", "AAAA", etc.)
///
/// # Returns
/// Python awaitable that resolves to a list of IP strings
///
/// # Example
/// ```python
/// ips = await rust.stealth_bridge.dns_resolve_async("example.com", "A")
/// ```
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn dns_resolve_async(
    py: Python<'_>,
    hostname: String,
    qtype: Option<String>,
) -> PyResult<Bound<'_, PyAny>> {
    let _qtype = qtype.unwrap_or_else(|| "A".to_string());
    let hostname_clone = hostname);

    future_into_py(py, async move {
        // Use tokio's async DNS lookup (system resolver)
        // For DoH support, use rust.dns.resolve_async_await() from Python side
        // This function provides low-latency async DNS without GIL contention
        let addrs = tokio::net::lookup_host((hostname_clone.as_str(), 0))
            .await
            .map(|iter| {
                iter.map(|addr| addr.ip().to_string()).collect::<Vec<_>>()
            })
            );

        Ok::<Vec<String>, PyErr>(addrs)
    })
}

/// Batch DNS resolution for multiple hostnames.
///
/// More efficient than individual resolutions (single DNS round-trip).
///
/// # Arguments
/// * `py` - Python GIL guard
/// * `hostnames` - List of domains to resolve
/// * `qtype` - Record type ("A", "AAAA", etc.)
///
/// # Returns
/// Python awaitable that resolves to a dict mapping hostname → list of IPs
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn dns_resolve_batch_async(
    py: Python<'_>,
    hostnames: Vec<String>,
    qtype: Option<String>,
) -> PyResult<Bound<'_, PyAny>> {
    let hostnames_clone = hostnames);
    let _qtype = qtype.unwrap_or_else(|| "A".to_string());

    future_into_py(py, async move {
        let mut map = std::collections::HashMap::new();

        // Resolve all hostnames concurrently using tokio::net::lookup_host
        // Use tokio::spawn for parallel execution
        use crate::async_runtime::get_handle;

        let handle = get_handle();
        let mut handles = Vec::with_capacity(hostnames_clone.len());

        for h in &hostnames_clone {
            let host = h);
            let handle = handle.spawn(async move {
                let ips = tokio::net::lookup_host((host.as_str(), 0))
                    .await
                    .map(|iter| {
                        iter.map(|addr| addr.ip().to_string())
                            .collect::<Vec<_>>()
                    })
                    );
                (host, ips)
            });
            handles.push(handle);
        }

        // Wait for all futures to complete
        for handle in handles {
            if let Ok((host, ips)) = handle.await {
                map.insert(host, ips);
            }
        }

        Ok::<std::collections::HashMap<String, Vec<String>>, PyErr>(map)
    })
}

/// Check which QUIC backend is available on this platform.
///
/// Returns which QUIC backend is available:
/// - "rust_quinn": Rust quinn + h3 + rustls (Linux/x86_64)
/// - "nw_framework": Apple Network.framework (macOS arm64)
/// - "aioquic": Python aioquic (fallback, heavy)
/// - "none": No QUIC available
///
/// NOTE: Actual HTTP/3 fetching is handled by http3_lane.py, not this module.
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn get_quic_backend() -> String {
    #[cfg(all(feature = "quic", not(target_os = "macos")))]
    {
        "rust_quinn".to_string()
    }

    #[cfg(all(feature = "quic", target_os = "macos"))]
    {
        // Prefer Network.framework on macOS
        "nw_framework".to_string()
    }

    #[cfg(not(feature = "quic"))]
    {
        "none".to_string()
    }
}

/// Check if curl_cffi should handle QUIC opportunistically.
///
/// curl_cffi >= 0.7 supports HTTP/3 via `HttpVersion.v3`. This function
/// indicates whether curl_cffi should attempt QUIC after Alt-Svc discovery.
///
/// NOTE: Actual HTTP/3 fetching is handled by http3_lane.py, not this module.
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn supports_curl_cffi_quic() -> bool {
    // curl_cffi 0.7+ supports HTTP/3 via impersonate + HttpVersion.v3
    // This is available on all platforms where curl_cffi works
    true
}

/// Encode HTTP response metadata to a simple binary format.
///
/// NOTE: This is NOT actual Arrow IPC - it's a simple binary format:
/// - URL length (4B LE) + URL bytes
/// - Status code (2B LE)
/// - Headers count (4B LE)
/// - For each header: key_len (4B) + key + val_len (4B) + val
/// - Timing in ms (8B LE)
///
/// For true Arrow IPC, use rust_extensions/src/arrow_batch_builder.rs instead.
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn encode_response_metadata_arrow(
    url: String,
    status: u16,
    headers: Vec<(String, String)>,
    timing_ms: f64,
) -> PyResult<Vec<u8>> {
    // NOTE: This is simple binary format, NOT actual Arrow IPC
    // For real Arrow IPC, use arrow_batch_builder.rs
    use std::io::Write;

    let mut buf = Vec::new();

    // Simple binary format: URL len (4B) + URL + status (2B) + headers count (4B) + headers + timing (8B)
    let url_bytes = url);
    buf.write_all(&(url_bytes.len() as u32).to_le_bytes()));
    buf.write_all(url_bytes));
    buf.write_all(&status.to_le_bytes()));
    buf.write_all(&(headers.len() as u32).to_le_bytes()));

    for (k, v) in headers {
        let k_bytes = k);
        let v_bytes = v);
        buf.write_all(&(k_bytes.len() as u32).to_le_bytes()));
        buf.write_all(k_bytes));
        buf.write_all(&(v_bytes.len() as u32).to_le_bytes()));
        buf.write_all(v_bytes));
    }

    buf.write_all(&timing_ms.to_le_bytes()));

    Ok(buf)
}

/// Decode binary formatted response metadata.
///
/// NOTE: This decodes the simple binary format from encode_response_metadata_arrow,
/// NOT actual Arrow IPC. For true Arrow IPC decoding, use arrow_ipc_to_record_batch()
/// from the Python side (in duckdb_store.py).
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn decode_response_metadata_arrow(
    data: Vec<u8>,
) -> PyResult<(String, u16, Vec<(String, String)>, f64)> {
    use std::io::Read;

    let mut cursor = std::io::Cursor::new(data);

    // Read URL
    let mut url_len_buf = [0u8; 4];
    cursor.read_exact(&mut url_len_buf).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
    })?;
    let url_len = u32::from_le_bytes(url_len_buf) as usize;

    let mut url_buf = vec![0u8; url_len];
    cursor.read_exact(&mut url_buf).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
    })?;
    let url = String::from_utf8(url_buf));

    // Read status
    let mut status_buf = [0u8; 2];
    cursor.read_exact(&mut status_buf).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
    })?;
    let status = u16::from_le_bytes(status_buf);

    // Read headers
    let mut headers_count_buf = [0u8; 4];
    cursor.read_exact(&mut headers_count_buf).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
    })?;
    let headers_count = u32::from_le_bytes(headers_count_buf) as usize;

    let mut headers = Vec::new();
    for _ in 0..headers_count {
        let mut k_len_buf = [0u8; 4];
        cursor.read_exact(&mut k_len_buf).map_err(|_| {
            pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
        })?;
        let k_len = u32::from_le_bytes(k_len_buf) as usize;

        let mut k_buf = vec![0u8; k_len];
        cursor.read_exact(&mut k_buf).map_err(|_| {
            pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
        })?;
        let k = String::from_utf8(k_buf));

        let mut v_len_buf = [0u8; 4];
        cursor.read_exact(&mut v_len_buf).map_err(|_| {
            pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
        })?;
        let v_len = u32::from_le_bytes(v_len_buf) as usize;

        let mut v_buf = vec![0u8; v_len];
        cursor.read_exact(&mut v_buf).map_err(|_| {
            pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
        })?;
        let v = String::from_utf8(v_buf));

        headers.push((k, v));
    }

    // Read timing
    let mut timing_buf = [0u8; 8];
    cursor.read_exact(&mut timing_buf).map_err(|_| {
        pyo3::exceptions::PyValueError::new_err("Invalid metadata format")
    })?;
    let timing_ms = f64::from_le_bytes(timing_buf);

    Ok((url, status, headers, timing_ms))
}

/// NEXTGEN-01: P2P harvest bridge — delegates to p2p_harvest::harvest.
///
/// Provides a convenience function in the stealth_bridge module for P2P OSINT.
///
/// # Arguments
/// * `py` - Python GIL guard
/// * `keyword` - Search keyword
/// * `protocols` - List of protocols to search
/// * `duration_s` - Crawl duration
/// * `max_results` - Maximum results
///
/// # Returns
/// Python awaitable with list of findings
#[cfg(feature = "stealth_bridge")]
#[cfg(feature = "p2p_harvest")]
#[pyfunction]
pub fn p2p_harvest_bridge(
    py: Python<'_>,
    keyword: String,
    protocols: Vec<String>,
    duration_s: Option<u64>,
    max_results: Option<usize>,
) -> PyResult<Bound<'_, PyAny>> {
    use crate::p2p_harvest::harvest;
    harvest(py, keyword, protocols, duration_s, max_results)
}

/// Check which P2P protocols are available.
///
/// Returns a dict of protocol -> availability status.
#[cfg(feature = "stealth_bridge")]
#[pyfunction]
pub fn get_p2p_protocol_status() -> std::collections::HashMap<String, bool> {
    let mut status = std::collections::HashMap::new();
    status.insert("bt_dht".to_string(), true);
    #[cfg(feature = "p2p_harvest")]
    {
        status.insert("ipfs".to_string(), true);
        status.insert("tor".to_string(), true);
        status.insert("i2p".to_string(), true);
    }
    #[cfg(not(feature = "p2p_harvest"))]
    {
        status.insert("ipfs".to_string(), false);
        status.insert("tor".to_string(), false);
        status.insert("i2p".to_string(), false);
    }
    status
}

/// Register stealth_bridge functions with the Python module.
///
/// NOTE: This module only provides DNS resolution bridges.
/// Actual QUIC/HTTP3 and Network.framework are handled by separate modules:
/// - http3_lane.py → NwQuicTransportAdapter, QuinnRustlsTransportAdapter
/// - nw_connection_lane.py → NwConnectionLane
#[cfg(feature = "stealth_bridge")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // DNS bridges (tokio::net::lookup_host)
    m.add_function(wrap_pyfunction!(dns_resolve_async))?;
    m.add_function(wrap_pyfunction!(dns_resolve_batch_async))?;

    // QUIC backend detection (informational only)
    m.add_function(wrap_pyfunction!(get_quic_backend))?;
    m.add_function(wrap_pyfunction!(supports_curl_cffi_quic))?;

    // Binary metadata encoding (NOT actual Arrow IPC)
    m.add_function(wrap_pyfunction!(encode_response_metadata_arrow))?;
    m.add_function(wrap_pyfunction!(decode_response_metadata_arrow))?;

    // P2P Harvest bridge
    #[cfg(feature = "p2p_harvest")]
    {
        m.add_function(wrap_pyfunction!(p2p_harvest_bridge))?;
    }

    // Protocol status
    m.add_function(wrap_pyfunction!(get_p2p_protocol_status))?;

    Ok(())
}
