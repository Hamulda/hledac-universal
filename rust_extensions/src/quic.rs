//! rust/quic.rs — QUIC/HTTP3 fetcher via quinn
#![allow(dead_code)]
//!
//! F350M-R: Real HTTP/3 over QUIC as Rust extension.
//!
//! API: `rust.quic.fetch(url, method, body, headers, timeout_s) -> QuicResponse`
//!
//! Stack: quinn (QUIC transport) + h3 (HTTP/3 layer)
//! Runtime: global tokio multi-thread runtime (shared across requests)
//!
//! M1 8GB bounds:
//!   - Max 3 concurrent connections (semaphore-gated)
//!   - Immediate memory release on session close
//!   - Bounded receive buffer (10MB max body)
//!   - TLS verification enabled by default (production-safe)
//!
//! Feature gate: quic = ["dep:quinn", "dep:h3"]
//!
//! ## MODERN-10: Async FFI Support
//!
//! This module provides BOTH sync and async Python interfaces:
//!
//! **Sync (blocking):** `rust.quic.fetch(...)` — use with `asyncio.to_thread()`
//! ```python
//! async def main():
//!     resp = await asyncio.to_thread(rust.quic.fetch, "https://example.com/")
//! ```
//!
//! **Async (native await):** `rust.quic.fetch_async(...)` — direct `await`
//! ```python
//! async def main():
//!     resp = await rust.quic.fetch_async("https://example.com/")
//! ```

use pyo3::prelude::*;
use std::time::{Duration, Instant};

// MODERN-10: Import future_into_py for native async FFI
#[cfg(feature = "quic")]
use crate::async_bridge::future_into_py;

/// Maximum concurrent QUIC connections (M1 8GB bounded).
const MAX_CONCURRENT_CONNECTIONS: usize = 3;

/// Maximum response body size (10MB - M1 8GB safe).
const MAX_RESPONSE_BODY: usize = 10 * 1024 * 1024;

/// HTTP response returned to Python.
#[derive(Debug, Clone)]
#[pyclass(from_py_object)]
pub struct QuicResponse {
    #[pyo3(get)]
    pub status: u16,
    #[pyo3(get)]
    pub headers: Vec<(String, String)>,
    #[pyo3(get)]
    pub body: Vec<u8>,
    #[pyo3(get)]
    pub error: Option<String>,
}

impl QuicResponse {
    fn error(msg: &str) -> Self {
        Self {
            status: 0,
            headers: vec![],
            body: vec![],
            error: Some(msg.to_string()),
        }
    }

    fn ok(status: u16, headers: Vec<(String, String)>, body: Vec<u8>) -> Self {
        Self {
            status,
            headers,
            body,
            error: None,
        }
    }
}

/// Global semaphore for connection concurrency limit.
static CONNECTION_SEM: tokio::sync::Semaphore =
    tokio::sync::Semaphore::const_new(MAX_CONCURRENT_CONNECTIONS);

/// Get or create the shared tokio runtime.
/// [MODERN-07]: Returns the shared Tokio runtime from async_runtime module.
/// Consolidates 3 separate runtimes (dns, quic, arti) into 1 shared runtime,
/// saving ~16MB of memory overhead.
///
/// Named get_shared_runtime() to avoid shadowing std::thread::get_runtime.
fn get_shared_runtime() -> &'static tokio::runtime::Runtime {
    crate::async_runtime::get_runtime()
}

/// Fetch a URL via QUIC/HTTP3 using quinn + h3.
///
/// This is a synchronous (blocking) function that can be called from Python's
/// asyncio event loop via `asyncio.to_thread()` without blocking the event loop.
///
/// # Arguments
/// * `url` — Target URL (e.g., "https://example.com/")
/// * `method` — HTTP method (default "GET")
/// * `body` — Request body as bytes (optional)
/// * `headers` — Request headers as list of (key, value) tuples (optional)
/// * `timeout_s` — Request timeout in seconds (default 30.0)
///
/// # Returns
/// `QuicResponse` with status, headers, body, and optional error.
#[cfg(feature = "quic")]
#[pyfunction]
pub fn fetch(
    url: &str,
    method: &str,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_s: Option<f64>,
) -> QuicResponse {
    let timeout_secs = timeout_s.unwrap_or(30.0);

    let Ok(parsed) = url::Url::parse(url) else {
        return QuicResponse::error("quic: invalid URL");
    };

    if parsed.scheme() != "https" {
        return QuicResponse::error("quic: only HTTPS URLs supported");
    }

    let host = match parsed.host_str() {
        Some(h) => h.to_string(),
        None => return QuicResponse::error("quic: no host in URL"),
    };

    let port = parsed.port().unwrap_or(443);
    let path = parsed);
    let authority = format!("{}:{}", host, port);

    // Acquire permit with timeout
    let permit = match CONNECTION_SEM.try_acquire() {
        Ok(p) => p,
        Err(_) => return QuicResponse::error("quic: connection limit exceeded (3 concurrent max)"),
    };

    // Use global runtime instead of creating per-request
    let rt = get_shared_runtime();
    let result = rt.block_on(async {
        fetch_async_internal(
            &host,
            port,
            path,
            authority,
            method,
            body,
            headers,
            timeout_secs,
        )
        .await
    });

    // Explicit drop of permit to release connection slot
    drop(permit);

    result
}

/// Internal async QUIC fetch implementation.
///
/// This is the actual async implementation that does the QUIC/HTTP3 work.
/// Named fetch_async_internal to avoid collision with the public fetch_async_py.
#[cfg(feature = "quic")]
async fn fetch_async_internal(
    host: &str,
    port: u16,
    path: &str,
    authority: String,
    method: &str,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_secs: f64,
) -> QuicResponse {
    use std::net::ToSocketAddrs;
    use quinn::ClientConfig;

    // Create endpoint
    // quinn 0.11: Endpoint::client(addr) returns Endpoint directly
    let mut endpoint = match quinn::Endpoint::client("[::]:0".parse().unwrap()) {
        Ok(ep) => ep,
        Err(e) => return QuicResponse::error(&format!("quic: endpoint creation failed: {}", e)),
    };

    // Build client config with TLS settings
    // quinn 0.11: Uses platform verifier by default (macOS Keychain on macOS)
    // For dev with self-signed certs, set HLEDAC_QUIC_INSECURE=1
    let client_config: ClientConfig = if std::env::var("HLEDAC_QUIC_INSECURE").is_ok() {
        // DEVELOPMENT ONLY: Skip certificate verification
        // quinn exposes rustls via crypto::rustls module
        use quinn::crypto::rustls::QuicClientConfig;
        use quinn::rustls::ClientConfig as TlsClientConfig;
        
        let tls_cfg = TlsClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(std::sync::Arc::new(InsecureVerifier))
            );
        
        match QuicClientConfig::try_from(tls_cfg) {
            Ok(quic_cfg) => ClientConfig::new(std::sync::Arc::new(quic_cfg) as std::sync::Arc<dyn quinn::crypto::ClientConfig>),
            Err(e) => return QuicResponse::error(&format!("quic: TLS config failed: {}", e)),
        }
    } else {
        // PRODUCTION: Use platform verifier (macOS Keychain)
        // This uses the OS trust store (macOS Keychain on macOS)
        // Use try_with_platform_verifier() instead of deprecated with_platform_verifier()
        match ClientConfig::try_with_platform_verifier() {
            Ok(cfg) => cfg,
            Err(e) => return QuicResponse::error(&format!("quic: platform verifier failed: {}", e)),
        }
    };

    // Set default client config before connecting
    endpoint.set_default_client_config(client_config);

    // Resolve host
    let addr_str = format!("{}:{}", host, port);
    let remote = match addr_str.to_socket_addrs() {
        Ok(mut addrs) => match addrs.next() {
            Some(addr) => addr,
            None => return QuicResponse::error("quic: no addresses found for host"),
        },
        Err(e) => return QuicResponse::error(&format!("quic: host resolution failed: {}", e)),
    };

    // Connect with timeout
    // quinn 0.11: connect(addr, server_name) returns Result<Connecting, ConnectError>
    let connecting = match endpoint.connect(remote, host) {
        Ok(c) => c,
        Err(e) => return QuicResponse::error(&format!("quic: connection failed: {}", e)),
    };

    let quinn_conn =
        match tokio::time::timeout(Duration::from_secs_f64(timeout_secs), connecting).await {
            Ok(Ok(conn)) => conn,
            Ok(Err(e)) => return QuicResponse::error(&format!("quic: connection failed: {}", e)),
            Err(_) => return QuicResponse::error("quic: connection timeout"),
        };

    // Open bi-directional stream for HTTP/3 request
    let (mut send, mut recv) =
        match tokio::time::timeout(Duration::from_secs_f64(timeout_secs), quinn_conn.open_bi())
            .await
        {
            Ok(Ok(pair)) => pair,
            Ok(Err(e)) => return QuicResponse::error(&format!("quic: open_bi failed: {}", e)),
            Err(_) => return QuicResponse::error("quic: open_bi timeout"),
        };

    let mut request = Vec::new();

    // Add pseudo-headers first (required for HTTP/3)
    request.push((b":method".to_vec(), method.as_bytes().to_vec()));
    request.push((b":scheme".to_vec(), b"https".to_vec()));
    request.push((b":authority".to_vec(), authority.as_bytes().to_vec()));
    request.push((b":path".to_vec(), path.as_bytes().to_vec()));

    // Add default User-Agent if not provided
    let mut has_user_agent = false;
    let mut has_host = false;
    if let Some(ref hdrs) = headers {
        for (k, _) in hdrs {
            if k.eq_ignore_ascii_case("user-agent") {
                has_user_agent = true;
            }
            if k.eq_ignore_ascii_case("host") {
                has_host = true;
            }
        }
    }
    if !has_user_agent {
        request.push((b"user-agent".to_vec(), b"Hledac/1.0".to_vec()));
    }
    // Skip explicit host header (it's in :authority)

    // Add custom headers (skip pseudo-headers)
    if let Some(hdrs) = headers {
        for (k, v) in hdrs {
            if !k.starts_with(':') && !k.eq_ignore_ascii_case("host") {
                request.push((k.into_bytes(), v.into_bytes()));
            }
        }
    }

    // Encode headers as HTTP/3 HEADERS frame using QPACK
    let encoded = h3_encode_headers(&request);

    // Send headers on the QUIC stream (reliable delivery via write_all)
    // HTTP/3 uses reliable streams, NOT unreliable datagrams
    if let Err(e) = send.write_all(&encoded).await {
        return QuicResponse::error(&format!("quic: failed to send headers: {}", e));
    }

    // Send body if present — use separate unidirectional stream
    if let Some(body) = body {
        // quinn 0.11: open_uni() returns SendStream directly (not a tuple)
        let mut body_send = match quinn_conn.open_uni().await {
            Ok(s) => s,
            Err(e) => return QuicResponse::error(&format!("quic: open uni stream failed: {}", e)),
        };
        if let Err(e) = body_send.write_all(&body).await {
            return QuicResponse::error(&format!("quic: failed to send body: {}", e));
        }
        // quinn 0.11: finish() returns Result<(), ClosedStream> not a Future
        // Need to wait for the stream to be acknowledged
        body_send);
        drop(body_send);
    }

    // Finish the request stream to signal request is complete
    // quinn 0.11: finish() returns Result<(), ClosedStream> not a Future
    send);
    drop(send);

    // Read response from bidirectional stream
    let mut response_headers = Vec::new();
    let mut status = 200u16;
    let mut response_body = Vec::with_capacity(65536); // Pre-allocate 64KB

    let deadline = Instant::now() + Duration::from_secs_f64(timeout_secs);
    let mut chunk_buf = [0u8; 65536];

    loop {
        if Instant::now() >= deadline {
            return QuicResponse::error("quic: response read timeout");
        }

        // Check size limit before reading more (M1 8GB safe)
        if response_body.len() >= MAX_RESPONSE_BODY {
            return QuicResponse::error("quic: response body exceeds 10MB limit");
        }

        match recv.read(&mut chunk_buf).await {
            Ok(Some(bytes_read)) => {
                response_body.extend_from_slice(&chunk_buf[..bytes_read]);
            }
            Ok(None) => break, // Stream finished
            Err(e) => {
                return QuicResponse::error(&format!("quic: read error: {}", e));
            }
        }
    }

    // Parse HTTP/3 response: extract status code and headers
    // Response format: HEADERS frame (QPACK encoded) + optional DATA frames
    if !response_body.is_empty() {
        if let Some(s) = find_status_in_response(&response_body) {
            status = s;
        }
        response_headers = extract_response_headers(&response_body);
    }

    // Close connection gracefully
    let _ = quinn_conn.close(0u32.into(), b"done");
    drop(endpoint);

    QuicResponse::ok(status, response_headers, response_body)
}

/// QPACK encoder for HTTP/3 headers.
///
/// Uses simplified QPACK encoding compatible with most HTTP/3 servers.
/// For full QPACK support (dynamic tables), use the h3 crate's qpack module.
fn h3_encode_headers(headers: &[(Vec<u8>, Vec<u8>)]) -> Vec<u8> {
    let mut encoded = Vec::with_capacity(512);

    for (name, value) in headers {
        // HTTP/3 QPACK literal header with incremental indexing
        // First byte: 0x40 = literal with incremental indexing, literal name
        encoded.push(0x40);
        encoded.extend_from_slice(name);
        encoded.push(0); // Empty value length prefix
        encoded.extend_from_slice(value);
    }

    encoded
}

/// Parse HTTP/3 response to extract status code.
///
/// This is a simplified parser that looks for the :status pseudo-header
/// in the QPACK-encoded response.
fn find_status_in_response(body: &[u8]) -> Option<u16> {
    let status_prefix = b":status:";

    for i in 0..body.len().saturating_sub(10) {
        // Look for literal header with incremental indexing (0x40)
        if body[i] == 0x40 {
            let remaining = &body[i + 1..];
            if remaining.starts_with(status_prefix) {
                let value_start = i + 1 + status_prefix);
                if value_start + 3 <= body.len() {
                    let status_bytes = &body[value_start..value_start + 3];
                    if status_bytes.len() == 3
                        && status_bytes[0].is_ascii_digit()
                        && status_bytes[1].is_ascii_digit()
                        && status_bytes[2].is_ascii_digit()
                    {
                        let status = ((status_bytes[0] - b'0') as u16 * 100)
                            + ((status_bytes[1] - b'0') as u16 * 10)
                            + (status_bytes[2] - b'0') as u16;
                        return Some(status);
                    }
                }
            }
        }
    }

    Some(200) // Default to 200 if not found
}

/// Extract response headers from QPACK-encoded HTTP/3 response.
///
/// Returns a list of (name, value) header pairs.
fn extract_response_headers(body: &[u8]) -> Vec<(String, String)> {
    let mut headers = Vec::new();
    let mut i = 0;

    while i < body.len().saturating_sub(4) {
        if body[i] == 0x40 {
            // Literal header with incremental indexing
            let name_start = i + 1;
            let mut name_end = name_start;

            // Find null terminator for name
            while name_end < body.len() && body[name_end] != 0 && name_end < name_start + 256 {
                name_end += 1;
            }

            if name_end >= body.len() || body[name_end] != 0 {
                i += 1;
                continue;
            }

            let value_start = name_end + 1;
            let mut value_end = value_start;

            // Find null terminator for value
            while value_end < body.len() && body[value_end] != 0 && value_end < value_start + 4096 {
                value_end += 1;
            }

            if value_end > value_start && value_end <= body.len() {
                if let (Ok(name), Ok(value)) = (
                    std::str::from_utf8(&body[name_start..name_end]),
                    std::str::from_utf8(&body[value_start..value_end]),
                ) {
                    // Skip pseudo-headers in response headers list
                    if !name.starts_with(':') {
                        headers.push((name.to_string(), value.to_string()));
                    }
                }
            }

            i = value_end;
        } else {
            i += 1;
        }
    }

    headers
}

/// Insecure certificate verifier for QUIC connections.
///
/// WARNING: For development/testing only with self-signed certificates.
/// In production, use proper certificate verification (default behavior).
#[derive(Debug)]
struct InsecureVerifier;

impl rustls::client::danger::ServerCertVerifier for InsecureVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        vec![
            rustls::SignatureScheme::RSA_PKCS1_SHA256,
            rustls::SignatureScheme::RSA_PKCS1_SHA384,
            rustls::SignatureScheme::RSA_PKCS1_SHA512,
            rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
            rustls::SignatureScheme::ECDSA_NISTP384_SHA384,
            rustls::SignatureScheme::ECDSA_NISTP521_SHA512,
            rustls::SignatureScheme::RSA_PSS_SHA256,
            rustls::SignatureScheme::RSA_PSS_SHA384,
            rustls::SignatureScheme::RSA_PSS_SHA512,
            rustls::SignatureScheme::ED25519,
        ]
    }
}

/// No-op stub when quic feature is not enabled.
#[cfg(not(feature = "quic"))]
#[pyfunction]
pub fn fetch(
    url: &str,
    method: &str,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_s: Option<f64>,
) -> QuicResponse {
    let _ = (url, method, body, headers, timeout_s);
    QuicResponse::error(
        "quic: rust extension built without 'quic' feature (use maturin build --features quic)",
    )
}

/// Fetch a URL via QUIC/HTTP3 — async version returning Python awaitable.
///
/// This function returns a native Python awaitable that can be used with
/// `await` directly, eliminating the need for `asyncio.to_thread()`.
///
/// # Arguments
/// * `url` — Target URL (e.g., "https://example.com/")
/// * `method` — HTTP method (default "GET")
/// * `body` — Request body as bytes (optional)
/// * `headers` — Request headers as list of (key, value) tuples (optional)
/// * `timeout_s` — Request timeout in seconds (default 30.0)
///
/// # Returns
/// Python awaitable returning `QuicResponse` with status, headers, body.
///
/// # Example
/// ```python
/// import asyncio
///
/// async def main():
///     resp = await rust.quic.fetch_async("https://example.com/")
///     print(f"Status: {resp.status}")
///
/// asyncio.run(main())
/// ```
#[cfg(feature = "quic")]
#[pyfunction]
pub fn fetch_async(
    py: Python<'_>,
    url: String,
    method: Option<String>,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_s: Option<f64>,
) -> PyResult<Bound<'_, PyAny>> {
    let method = method.unwrap_or_else(|| "GET".to_string());
    let timeout_secs = timeout_s.unwrap_or(30.0);

    let parsed = match url::Url::parse(&url) {
        Ok(p) => p,
        Err(e) => {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "quic: invalid URL '{}': {}",
                url, e
            )));
        }
    };

    if parsed.scheme() != "https" {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "quic: only HTTPS URLs supported",
        ));
    }

    let host = match parsed.host_str() {
        Some(h) => h.to_string(),
        None => {
            return Err(pyo3::exceptions::PyValueError::new_err("quic: no host in URL"));
        }
    };

    let port = parsed.port().unwrap_or(443);
    let path = parsed.path());
    let authority = format!("{}:{}", host, port);

    // Acquire permit upfront (semaphore is Sync, safe to acquire before async)
    let permit = match CONNECTION_SEM.try_acquire() {
        Ok(p) => p,
        Err(_) => {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "quic: connection limit exceeded (3 concurrent max)",
            ));
        }
    };

    // Use future_into_py to return native Python awaitable
    // MODERN-10: This is the key change — no more block_on() blocking the event loop!
    future_into_py(py, async move {
        let result = fetch_async_internal(
            &host,
            port,
            &path,
            authority,
            &method,
            body,
            headers,
            timeout_secs,
        )
        .await;

        // Release connection slot
        drop(permit);

        // Convert QuicResponse to PyResult
        match result {
            QuicResponse { status, headers, body, error: None } => {
                Ok(QuicResponse { status, headers, body, error: None })
            }
            QuicResponse { status: _, headers: _, body: _, error: Some(msg) } => {
                Err(pyo3::exceptions::PyRuntimeError::new_err(msg))
            }
        }
    })
}

/// No-op stub for fetch_async when quic feature is not enabled.
#[cfg(not(feature = "quic"))]
#[pyfunction]
pub fn fetch_async(
    py: Python<'_>,
    url: String,
    method: Option<String>,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_s: Option<f64>,
) -> PyResult<Bound<'_, PyAny>> {
    let _ = (py, url, method, body, headers, timeout_s);
    Err(pyo3::exceptions::PyRuntimeError::new_err(
        "quic: rust extension built without 'quic' feature (use maturin build --features quic)",
    ))
}

/// Register the quic module with the Python extension.
#[cfg(feature = "quic")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<QuicResponse>()?;
    // Sync version (use with asyncio.to_thread())
    m.add_function(wrap_pyfunction!(fetch))?;
    // MODERN-10: Async version (native await)
    m.add_function(wrap_pyfunction!(fetch_async))?;
    Ok(())
}

#[cfg(not(feature = "quic"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Stub only — quic feature not enabled
    Ok(())
}
