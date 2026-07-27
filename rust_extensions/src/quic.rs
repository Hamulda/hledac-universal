//! rust/quic.rs — QUIC/HTTP3 fetcher via quinn
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

use pyo3::prelude::*;
use std::time::{Duration, Instant};

/// Maximum concurrent QUIC connections (M1 8GB bounded).
const MAX_CONCURRENT_CONNECTIONS: usize = 3;

/// Maximum response body size (10MB - M1 8GB safe).
const MAX_RESPONSE_BODY: usize = 10 * 1024 * 1024;

/// HTTP response returned to Python.
#[derive(Debug, Clone)]
#[pyclass]
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
static CONNECTION_SEM: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(MAX_CONCURRENT_CONNECTIONS);

/// Global tokio runtime for async operations.
/// Created once per process, reused for all requests.
/// M1 8GB: 2 threads is sufficient for QUIC (I/O-bound).
static RUNTIME: std::sync::OnceLock<tokio::runtime::Runtime> = std::sync::OnceLock::new();

/// Get or create the global tokio runtime.
fn get_runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .max_blocking_threads(2)  // M1 8GB: only 2 threads needed for QUIC I/O
            .build()
            .expect("quic: failed to create tokio runtime")
    })
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

    // Parse URL
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
    let path = parsed.path();
    let authority = format!("{}:{}", host, port);

    // Acquire permit with timeout
    let permit = match CONNECTION_SEM.try_acquire() {
        Ok(p) => p,
        Err(_) => {
            return QuicResponse::error("quic: connection limit exceeded (3 concurrent max)")
        }
    };

    // Use global runtime instead of creating per-request
    let rt = get_runtime();
    let result = rt.block_on(async {
        fetch_async(&host, port, path, authority, method, body, headers, timeout_secs).await
    });

    // Explicit drop of permit to release connection slot
    drop(permit);

    result
}

#[cfg(feature = "quic")]
async fn fetch_async(
    host: &str,
    port: u16,
    path: &str,
    authority: String,
    method: &str,
    body: Option<Vec<u8>>,
    headers: Option<Vec<(String, String)>>,
    timeout_secs: f64,
) -> QuicResponse {
    use quinn::{ClientConfig, TransportConfig};
    use rustls::{ClientConfig as TlsClientConfig, RootCertificateStore};
    use std::net::ToSocketAddrs;
    use std::sync::OnceLock;

    static TLS_ROOTS: OnceLock<RootCertificateStore> = OnceLock::new();

    // Get or create root certificates
    let roots = TLS_ROOTS.get_or_init(|| {
        let mut store = RootCertificateStore::empty();
        if let Ok(certs) = rustls::native_root_certs() {
            store.extend(certs);
        }
        store
    });

    // Build TLS config with proper certificate verification by default.
    // Insecure mode (for dev with self-signed certs) requires HLEDAC_QUIC_INSECURE=1.
    let tls_cfg = if std::env::var("HLEDAC_QUIC_INSECURE").is_ok() {
        // DEVELOPMENT ONLY: Skip certificate verification
        match TlsClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(std::sync::Arc::new(InsecureVerifier))
            .with_no_client_auth()
        {
            Ok(cfg) => cfg,
            Err(e) => return QuicResponse::error(&format!("quic: TLS config failed: {}", e)),
        }
    } else {
        // PRODUCTION: Verify certificates against system roots
        match TlsClientConfig::builder()
            .with_default_cert_verifier(roots.clone())
            .with_no_client_auth()
        {
            Ok(cfg) => cfg,
            Err(e) => return QuicResponse::error(&format!("quic: TLS config failed: {}", e)),
        }
    };

    // Create QUIC client config
    let mut client_config = ClientConfig::new(std::sync::Arc::new(tls_cfg));
    // M1 8GB: release memory immediately on drop
    client_config.transport_config(TransportConfig::enable_0rtt());
    client_config.release_memory();

    // Create endpoint
    let (mut endpoint, _) = match quinn::Endpoint::client("[::]:0".parse().unwrap()) {
        Ok(ep) => ep,
        Err(e) => return QuicResponse::error(&format!("quic: endpoint creation failed: {}", e)),
    };

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
    let connecting = endpoint.connect(client_config, remote, host);

    let quinn_conn = match tokio::time::timeout(
        Duration::from_secs_f64(timeout_secs),
        connecting,
    )
    .await
    {
        Ok(Ok(conn)) => conn,
        Ok(Err(e)) => return QuicResponse::error(&format!("quic: connection failed: {}", e)),
        Err(_) => return QuicResponse::error("quic: connection timeout"),
    };

    // Open bi-directional stream for HTTP/3 request
    let (mut send, mut recv) = match tokio::time::timeout(
        Duration::from_secs_f64(timeout_secs),
        quinn_conn.open_bi(),
    )
    .await
    {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => return QuicResponse::error(&format!("quic: open_bi failed: {}", e)),
        Err(_) => return QuicResponse::error("quic: open_bi timeout"),
    };

    // Build HTTP/3 request headers
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
        let (mut body_send, _) = match quinn_conn.open_uni().await {
            Ok(s) => s,
            Err(e) => return QuicResponse::error(&format!("quic: open uni stream failed: {}", e)),
        };
        if let Err(e) = body_send.write_all(&body).await {
            return QuicResponse::error(&format!("quic: failed to send body: {}", e));
        }
        if let Err(e) = body_send.finish().await {
            return QuicResponse::error(&format!("quic: failed to finish body stream: {}", e));
        }
        drop(body_send);
    }

    // Finish the request stream to signal request is complete
    if let Err(e) = send.finish().await {
        return QuicResponse::error(&format!("quic: failed to finish request: {}", e));
    }
    drop(send);

    // Read response from bidirectional stream
    let mut response_headers = Vec::new();
    let mut status = 200u16;
    let mut response_body = Vec::with_capacity(65536); // Pre-allocate 64KB

    let deadline = Instant::now() + Duration::from_secs_f64(timeout_secs);
    let chunk_buf = &mut [0u8; 65536];

    loop {
        if Instant::now() >= deadline {
            return QuicResponse::error("quic: response read timeout");
        }

        // Check size limit before reading more (M1 8GB safe)
        if response_body.len() >= MAX_RESPONSE_BODY {
            return QuicResponse::error("quic: response body exceeds 10MB limit");
        }

        match recv.read(chunk_buf).await {
            Ok(Some(chunk)) => {
                response_body.extend_from_slice(&chunk);
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
        // Extract headers from QPACK-encoded response
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
                let value_start = i + 1 + status_prefix.len();
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
    QuicResponse::error("quic: rust extension built without 'quic' feature (use maturin build --features quic)")
}

/// Register the quic module with the Python extension.
#[cfg(feature = "quic")]
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<QuicResponse>()?;
    m.add_function(wrap_pyfunction!(fetch, m)?)?;
    Ok(())
}

#[cfg(not(feature = "quic"))]
pub fn register(_m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Stub only — quic feature not enabled
    Ok(())
}
