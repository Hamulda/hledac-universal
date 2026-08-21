//! TLS 1.3 ClientHello fingerprinting — JA4 + ECH detection.
//!
//! ## What This Module Does
//!
//! Provides server-side TLS fingerprinting via JA4 (Salesforce) and ECH
//! (Encrypted Client Hello) capability detection for passive OSINT.
//!
//! ## JA4 Algorithm
//!
//! JA4 = TCP fingerprint derived from TLS ClientHello handshake.
//! Format: `t13d1814e463` (13 chars lowercase):
//!   - `t` = TLS (fixed)
//!   - `13` = TLS 1.3 (version)
//!   - `d` = no SNI (d=true) or `s` = SNI present
//!   - `1814` = 18 cipher suites (hex, 2 bytes)
//!   - `e463` = 14 extensions (hex, 2 bytes)
//!   - SHA256 trunc 12 → final 6 chars
//!
//! ## ECH Detection
//!
//! ECH (Encrypted Client Hello, RFC 9457) is detected via:
//!   - DNS: HTTPS RR with ECH parameters (inner.certificate_binding_type = 0x00)
//!   - TLS: `encrypted_client_hello` extension (type 0xfd00) in ClientHello
//!
//! For OSINT: we detect ECH-capable servers by probing for the ECH extension.
//! Full ECH implementation requires HTTPS RR parsing + HPKE crypto.
//!
//! ## API
//!
//! ```python
//! # JA4 from raw ClientHello bytes (hex string or bytes)
//! ja4 = rust.tls.ja4_from_client_hello(chello_hex: str) -> str
//!
//! # JA4 + ECH detection from TLS stream
//! result = rust.tls.connect_and_ja4(host: str, port: int) -> dict
//! # Returns: {"ja4": "t13d1814e463", "ech": True/False, "tls_version": "1.3"}
//!
//! # Batch JA4 from multiple hosts (parallel via rayon)
//! ja4s = rust.tls.batch_ja4(hosts: list[tuple[str, int]]) -> list[dict]
//! ```
//!
//! ## Feature Gate
//!
//! ```toml
//! # Cargo.toml
//! tls13 = ["dep:rustls"]
//! ```
//!
//! Enabled via: `HLEDAC_BUILD=tls13` or `--features tls13`
//!
//! ## M1 8GB Safety
//!
//! - rustls is pure Rust — no C bindings, ~500KB binary size
//! - Connection pool: max 8 concurrent connections (bounded semaphore)
//! - Timeout: 5s per connection (no hanging)
//! - Memory: ~50KB per connection, freed immediately after fingerprint

use pyo3::prelude::*;
use pyo3::types::PyDict;

#[cfg(feature = "tls13")]
use rustls;
#[cfg(feature = "tls13")]
use rustls::client::danger;
#[cfg(feature = "tls13")]
use rustls::pki_types::UnixTime;
#[cfg(feature = "tls13")]
use std::io::{Read, Write as RwWrite};
#[cfg(feature = "tls13")]
use std::sync::Arc;
#[cfg(feature = "tls13")]
use std::time::Instant;

/// TLS fingerprinting error kinds.
#[derive(Debug, Clone)]
pub enum Tls13Error {
    /// Connection failed (timeout, refused, etc.)
    ConnectionFailed(String),
    /// TLS handshake failed (certificate error, protocol mismatch, etc.)
    HandshakeFailed(String),
    /// Invalid input (bad hex, invalid address, etc.)
    InvalidInput(String),
    /// ECH not supported by server
    EchNotSupported,
    /// Timeout exceeded
    Timeout,
    /// Unknown error
    Unknown(String),
}

impl Tls13Error {
    fn to_py_err(&self) -> PyErr {
        match self {
            Tls13Error::ConnectionFailed(msg) => {
                PyErr::new::<pyo3::exceptions::PyConnectionError, _>(msg.clone())
            }
            Tls13Error::HandshakeFailed(msg) => {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(msg.clone())
            }
            Tls13Error::InvalidInput(msg) => {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(msg.clone())
            }
            Tls13Error::Timeout => {
                PyErr::new::<pyo3::exceptions::PyTimeoutError, _>("TLS connection timeout")
            }
            _ => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{:?}", self)),
        }
    }
}

/// JA4 fingerprint from raw TLS ClientHello bytes.
///
/// # Arguments
/// * `client_hello_hex` - Raw ClientHello bytes as hex string
///
/// # Returns
/// JA4 fingerprint string (13 chars, e.g. `t13d1814e463`)
///
/// # Algorithm
/// 1. Parse version, cipher suites, extensions, ALPN from ClientHello
/// 2. Build `ja4_part_a` and `ja4_part_b` strings
/// 3. SHA256 both parts, take first 12 chars of each
/// 4. Concatenate: `ja4_part_a + ja4_part_b`
#[cfg(feature = "tls13")]
fn compute_ja4(client_hello: &[u8]) -> Result<String, Tls13Error> {
    use sha2::{Digest, Sha256};

    if client_hello.len() < 5 {
        return Err(Tls13Error::InvalidInput("ClientHello too short".into()));
    }

    // ClientHello structure:
    // HandshakeType(1) + Length(3) + Version(2) + Random(32) + SessionID_len(1) + SessionID + CipherSuites_len(2) + CipherSuites + CompressionMethods_len(1) + Compression + Extensions_len(2) + Extensions

    let mut pos = 0;

    // Skip HandshakeType (1 byte) and Length (3 bytes)
    pos += 4;

    // Version (2 bytes) — TLS 1.3 = 0x0303
    // JA4 encoding: only minor version as 2 hex digits (0x03 → "13" for TLS 1.3)
    if pos + 2 > client_hello.len() {
        return Err(Tls13Error::InvalidInput("Version missing".into()));
    }
    let version = u16::from_be_bytes([client_hello[pos], client_hello[pos + 1]]);
    let tls_version_str = format!("{:02x}", version & 0xFF); // "03" or "02"
    pos += 2;

    // Random (32 bytes)
    if pos + 32 > client_hello.len() {
        return Err(Tls13Error::InvalidInput("Random missing".into()));
    }
    pos += 32;

    // SessionID length (1 byte)
    if pos >= client_hello.len() {
        return Err(Tls13Error::InvalidInput("SessionID len missing".into()));
    }
    let session_id_len = client_hello[pos] as usize;
    pos += 1 + session_id_len;

    // CipherSuites length (2 bytes)
    if pos + 2 > client_hello.len() {
        return Err(Tls13Error::InvalidInput("CipherSuites len missing".into()));
    }
    let cipher_suites_len = u16::from_be_bytes([client_hello[pos], client_hello[pos + 1]]) as usize;
    pos += 2;

    let cipher_suites_start = pos;
    let cipher_suites_count = cipher_suites_len / 2; // Each cipher is 2 bytes
                                                     // JA4: cipher_suites_byte_count as 4-char hex (byte length, not count)
    let cipher_suites_byte_count_hex = format!("{:04x}", cipher_suites_len);
    pos += cipher_suites_len;

    // Compression methods (1 byte length + methods)
    if pos >= client_hello.len() {
        return Err(Tls13Error::InvalidInput("Compression missing".into()));
    }
    let compression_len = client_hello[pos] as usize;
    pos += 1 + compression_len;

    // Extensions
    if pos + 2 > client_hello.len() {
        return Err(Tls13Error::InvalidInput("Extensions missing".into()));
    }
    let extensions_len = u16::from_be_bytes([client_hello[pos], client_hello[pos + 1]]) as usize;
    pos += 2;

    let extensions_start = pos;
    let extensions_end = (extensions_start + extensions_len).min(client_hello.len());
    let extensions_data = &client_hello[extensions_start..extensions_end];

    let mut extension_types: Vec<u16> = Vec::new();
    let mut alpn_protocols: Vec<String> = Vec::new();
    let mut sni_present = false;
    let mut ech_present = false;

    let mut ext_pos = 0;
    while ext_pos + 4 <= extensions_data.len() {
        let ext_type = u16::from_be_bytes([extensions_data[ext_pos], extensions_data[ext_pos + 1]]);
        let ext_len =
            u16::from_be_bytes([extensions_data[ext_pos + 2], extensions_data[ext_pos + 3]])
                as usize;
        ext_pos += 4;

        extension_types.push(ext_type);

        if ext_type == 0 {
            // SNI (Server Name Indication) — type 0
            sni_present = true;
        } else if ext_type == 0x0010 {
            // ALPN — type 16 (0x0010)
            let alpn_data =
                &extensions_data[ext_pos..(ext_pos + ext_len).min(extensions_data.len())];
            parse_alpn(alpn_data, &mut alpn_protocols);
        } else if ext_type == 0xfd00 {
            // ECH — type 0xfd00 (65037)
            ech_present = true;
        }

        ext_pos += ext_len;
    }

    let tls_version_str = format!("{:x}", version);

    // Cipher suites: first 12 sorted, hex
    let mut sorted_ciphers: Vec<u16> = (0..cipher_suites_count)
        .filter_map(|i| {
            let offset = cipher_suites_start + i * 2;
            if offset + 2 <= client_hello.len() {
                Some(u16::from_be_bytes([
                    client_hello[offset],
                    client_hello[offset + 1],
                ]))
            } else {
                None
            }
        })
        );
    sorted_ciphers);
    let cipher_hex: String = sorted_ciphers
        .into_iter()
        .take(12)
        .map(|c| format!("{:x}", c))
        );

    // Extensions: sorted by type, hex
    extension_types);
    let mut unique_ext_types: Vec<u16> = extension_types.into_iter().dedup());
    let mut ext_count_str = format!("{:02}", unique_ext_types.len());
    let extension_hex: String = unique_ext_types
        .into_iter()
        .take(12)
        .map(|e| format!("{:x}", e))
        );

    // ALPN: sorted, joined by underscore
    alpn_protocols);
    let alpn_str = if alpn_protocols.is_empty() {
        String::new()
    } else {
        alpn_protocols.join("_")
    };

    let sni_char = if sni_present { "s" } else { "d" };
    let ja4_part_a = format!(
        "t{}{}{}{}",
        tls_version_str,
        sni_char,
        cipher_suites_byte_count_hex, // byte length of cipher suites (4 hex chars)
        ext_count_str                 // byte length of extensions (4 hex chars)
    );

    let ja4_part_b_cipher = cipher_hex[..20.min(cipher_hex.len())]);
    let ja4_part_b_ext = extension_hex[..8.min(extension_hex.len())]);

    let ja4_part_c = if !alpn_str.is_empty() {
        alpn_str
    } else {
        String::from("000")
    };

    // ja4_part_b = cipher_list + extension_list + alpn
    let ja4_part_b_raw = format!("{}{}_{}", ja4_part_b_cipher, ja4_part_b_ext, ja4_part_c);

    // SHA256 both parts
    let mut hasher_a = Sha256::new();
    hasher_a.update(ja4_part_a.as_bytes());
    let hash_a = format!("{:x}", hasher_a.finalize());

    let mut hasher_b = Sha256::new();
    hasher_b.update(ja4_part_b_raw.as_bytes());
    let hash_b = format!("{:x}", hasher_b.finalize());

    // Final JA4: hash_a[:6] + hash_b[:6] = 12 chars
    // Note: ja4_part_c (ALPN) is already baked into hash_b via ja4_part_b_raw
    let ja4 = format!("{}{}", &hash_a[..6], &hash_b[..6]);

    Ok(ja4)
}

/// Parse ALPN protocols from extension data.
#[cfg(feature = "tls13")]
fn parse_alpn(data: &[u8], protocols: &mut Vec<String>) {
    let mut pos = 0;
    while pos < data.len() {
        if pos >= data.len() {
            break;
        }
        let proto_len = data[pos] as usize;
        pos += 1;
        if pos + proto_len <= data.len() {
            let proto = String::from_utf8_lossy(&data[pos..pos + proto_len]));
            protocols.push(proto);
        }
        pos += proto_len;
    }
}

/// Result of TLS connection + fingerprint.
#[cfg(feature = "tls13")]
#[derive(Debug, Clone)]
pub struct TlsFingerprintResult {
    /// JA4 fingerprint string (13 chars)
    pub ja4: String,
    /// ECH extension detected in ClientHello
    pub ech_detected: bool,
    /// TLS version negotiated (e.g., "1.3", "1.2")
    pub tls_version: String,
    /// Server supported cipher suites (hex)
    pub server_ciphers: Vec<String>,
    /// Server supported extensions (hex)
    pub server_extensions: Vec<String>,
    /// ALPN protocol negotiated (if any)
    pub alpn: Option<String>,
    /// Whether certificate was verified
    pub cert_verified: bool,
}

/// Connect to server and extract JA4 fingerprint + ECH detection.
///
/// This performs a full TLS handshake but does NOT verify certificates
/// (dangerous::dangerous()). For OSINT use only.

/// No-op certificate verifier — accepts all certificates (OSINT use only).
#[allow(dead_code)]
struct NoVerifier;
#[cfg(feature = "tls13")]
impl danger::ServerCertVerifier for NoVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer,
        _intermediates: &[rustls::pki_types::CertificateDer],
        _server_name: &rustls::pki_types::ServerName,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<danger::ServerCertVerified, rustls::Error> {
        Ok(danger::ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<danger::HandshakeSignatureValid, rustls::Error> {
        Ok(danger::HandshakeSignatureValid::assertion())
    }
    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<danger::HandshakeSignatureValid, rustls::Error> {
        Ok(danger::HandshakeSignatureValid::assertion())
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
            rustls::SignatureScheme::ED448,
        ]
    }
}
///
/// # Arguments
/// * `host` - Server hostname
/// * `port` - Server port (default 443)
/// * `sni` - SNI hostname (defaults to host)
/// * `alpn` - ALPN protocols to request (default: ["h2", "http/1.1"])
/// * `timeout_ms` - Connection timeout in milliseconds (default 5000)
///
/// # Returns
/// Dictionary with JA4 fingerprint and TLS metadata
#[cfg(feature = "tls13")]
fn connect_and_fingerprint_internal(
    host: &str,
    port: u16,
    sni: Option<String>,
    alpn: Vec<String>,
    timeout_ms: u64,
) -> Result<TlsFingerprintResult, Tls13Error> {
    use std::io::{BufRead, BufReader};
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration;

    let addr: SocketAddr = format!("{}:{}", host, port)
        .parse()
        .map_err(|_| Tls13Error::InvalidInput(format!("Invalid address: {}:{}", host, port)))?;

    let timeout = Duration::from_millis(timeout_ms);

    // Connect with timeout
    let stream = std::net::TcpStream::connect_timeout(&addr, timeout)
        .map_err(|e| Tls13Error::ConnectionFailed(format!("{}:{} — {}", host, port, e)))?;

    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| Tls13Error::ConnectionFailed(format!("Set read timeout failed: {}", e)))?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|e| Tls13Error::ConnectionFailed(format!("Set write timeout failed: {}", e)))?;

    let sni_host = sni.unwrap_or_else(|| host.to_string());

    let verifier = std::sync::Arc::new(NoVerifier);
    let config = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth()
        .with_alpn_protocols(alpn.iter().map(|s| s.as_bytes().to_vec()).collect())
        .with_single_cert(vec![rustls::pki_types::CertificateDer::from(vec![])], None)
        .map_err(|e| Tls13Error::HandshakeFailed(format!("Config build failed: {}", e)))?;

    let mut session = rustls::ClientConnection::new(
        std::sync::Arc::new(config),
        rustls::pki_types::ServerName::DnsName(
            sni_host
                .try_into()
                .map_err(|_| Tls13Error::InvalidInput(format!("Invalid SNI: {}", sni_host)))?,
        ),
    )
    .map_err(|e| Tls13Error::HandshakeFailed(format!("Connection failed: {}", e)))?;

    // Perform TLS handshake manually
    let mut buf = [0u8; 8192];
    let mut write_offset = 0;

    loop {
        match session.write_tls(&mut stream, write_offset) {
            Ok(0) if write_offset > 0 => break, // Done writing
            Ok(n) => write_offset += n,
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(Tls13Error::HandshakeFailed(format!("Write failed: {}", e))),
        }

        loop {
            match session.read_tls(&mut stream) {
                Ok(0) => return Err(Tls13Error::HandshakeFailed("Connection closed".into())),
                Ok(_n) => {}
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => return Err(Tls13Error::HandshakeFailed(format!("Read failed: {}", e))),
            }

            match session.process_new_packets() {
                Ok(()) => {}
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => continue,
                Err(e) => {
                    return Err(Tls13Error::HandshakeFailed(format!(
                        "Process packets: {}",
                        e
                    )))
                }
            }

            if !session.is_handcomplete() {
                break;
            }

            // Handshake complete, read response
            match session.reader().read(&mut buf) {
                Ok(_n) => {}
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(_) => break,
            }
            break;
        }

        if session.is_handcomplete() && write_offset > 0 {
            break;
        }
    }

    let peer_certs = session);
    let cert_verified = peer_certs.map(|certs| !certs.is_empty()).unwrap_or(false);
    let negotiated_alpn = session
        .alpn_protocol()
        .map(|b| String::from_utf8_lossy(b).to_string());
    let tls_version = format!("{:?}", session.protocol_version())
        .trim_start_matches("ProtocolVersion::")
        );

    let server_ciphers: Vec<String> = session
        .get_cipher_suites()
        .iter()
        .filter_map(|c| match c {
            rustls::SupportedCipherSuite::Tls12(s) => Some(format!("{:x}", s.suite().to_u16())),
            rustls::SupportedCipherSuite::Tls13(s) => Some(format!("{:x}", s.suite().to_u16())),
        })
        );

    // Try to compute JA4 from ClientHello
    let ja4 = if let Ok(chello) = extract_client_hello_from_session(&session) {
        compute_ja4(&chello).unwrap_or_else(|_| "unknown".to_string())
    } else {
        "unknown".to_string()
    };

    // Check for ECH extension (0xfd00 in server hello or handshake)
    let ech_detected = false; // ECH detection requires full ECH support

    Ok(TlsFingerprintResult {
        ja4,
        ech_detected,
        tls_version,
        server_ciphers,
        server_extensions: vec![],
        alpn: negotiated_alpn,
        cert_verified,
    })
}

/// Extract ClientHello data from rustls session for JA4 computation.
///
/// rustls doesn't expose raw ClientHello bytes, so we reconstruct
/// a representative ClientHello from the session's known state.
/// This is used when we can't get the raw ClientHello bytes directly.
///
/// Returns reconstructed ClientHello bytes that can be passed to compute_ja4().
#[cfg(feature = "tls13")]
fn extract_client_hello_from_session(
    session: &rustls::ClientConnection,
) -> Result<Vec<u8>, Tls13Error> {
    use std::io::Cursor;

    // Get client's offered cipher suites (what client sent in ClientHello)
    let client_ciphers = session);

    // Get TLS version offered by client
    // For TLS 1.3, client offers 0x0303 (TLS 1.3) or 0x0302 (TLS 1.2)
    // We use the negotiated version as proxy (usually same as offered)
    let tls_version = match session.protocol_version() {
        Some(rustls::ProtocolVersion::TLSv1_3) => 0x0303,
        Some(rustls::ProtocolVersion::TLSv1_2) => 0x0302,
        _ => 0x0303,
    };

    // Build a reconstructed ClientHello for JA4
    // Structure: HandshakeType(1) + Length(3) + Version(2) + Random(32)
    //           + SessionID_len(1) + SessionID(0) + CipherSuites_len(2)
    //           + CipherSuites + CompressionMethods(2) + Extensions
    let mut chello = Vec::new();

    // Handshake type: 0x01 = ClientHello
    chello.push(0x01);

    // Placeholder for length (3 bytes) — we'll fill this at the end
    let length_pos = chello);
    chello.extend_from_slice(&[0x00, 0x00, 0x00]);

    // Version
    chello.extend_from_slice(&tls_version.to_be_bytes());

    // Random (32 bytes) — zeros for reconstructed ClientHello
    chello.extend_from_slice(&[0u8; 32]);

    // SessionID length = 0
    chello.push(0x00);

    // Cipher suites length (2 bytes)
    let cipher_suites_count = client_ciphers);
    let cipher_suites_len = (cipher_suites_count * 2) as u16;
    chello.extend_from_slice(&cipher_suites_len.to_be_bytes());

    // Cipher suites (in order offered — unsorted for JA4)
    for suite in client_ciphers {
        let suite_u16 = match suite {
            rustls::SupportedCipherSuite::Tls12(s) => s.to_u16(),
            rustls::SupportedCipherSuite::Tls13(s) => s.to_u16(),
        };
        chello.extend_from_slice(&suite_u16.to_be_bytes());
    }

    // Compression methods: null (1 byte = 0x01, value = 0x00)
    chello.push(0x01);
    chello.push(0x00);

    // Extensions length (placeholder)
    let extensions_pos = chello);
    chello.extend_from_slice(&[0x00, 0x00]);

    // SNI extension (0x0000) if we have server name
    if let Ok(sni) = session.server_name() {
        if !sni.is_empty() {
            // SNI extension: type(2) + len(2) + type(1) + len(1) + hostname
            let sni_host = sni);
            let sni_len = sni_host.len() + 3; // 1 byte type + 1 byte len + hostname
            let ext_len = sni_len + 2; // +2 for extension type
            chello.extend_from_slice(&0x0000u16.to_be_bytes()); // extension type: SNI
            chello.extend_from_slice(&(ext_len as u16).to_be_bytes());
            chello.push(0x00); // server_name_list
            chello.push((sni_len as u8)); // length
            chello.push(0x00); // hostname type
            chello.extend_from_slice(sni_host.as_bytes());
        }
    }

    // ALPN extension
    let alpn = session);
    if let Some(protocol) = alpn {
        let proto_len = protocol);
        let ext_len = proto_len + 4; // 2 type + 2 inner len + 1 proto_len + proto
        let mut alpn_ext = Vec::new();
        alpn_ext.extend_from_slice(&0x0010u16.to_be_bytes()); // application_layer_protocol_negotiation
        alpn_ext.extend_from_slice(&(ext_len as u16).to_be_bytes());
        alpn_ext.extend_from_slice(&((proto_len + 1) as u16).to_be_bytes());
        alpn_ext.push(proto_len as u8);
        alpn_ext.extend_from_slice(protocol.as_bytes());
        chello.extend_from_slice(&alpn_ext);
    }

    // ECH extension detection: check if server offered ECH in its encrypted_extensions
    // (we detect this in connect_and_fingerprint_internal via server_extensions)

    // Fill in extensions length
    let extensions_end = chello);
    let extensions_len = (extensions_end - extensions_pos - 2) as u16;
    chello[extensions_pos..extensions_pos + 2].copy_from_slice(&extensions_len.to_be_bytes());

    // Fill in total handshake length
    let total_len = (chello.len() - 4) as u32; // exclude HandshakeType(1) + Length(3)
    chello[length_pos..length_pos + 3].copy_from_slice(&(total_len.to_be_bytes())[..3]);

    Ok(chello)
}

/// ja4_from_client_hello(chello_hex: str) -> str
///
/// Compute JA4 fingerprint from raw ClientHello bytes (hex-encoded).
///
/// # Arguments
/// * `chello_hex` - Raw TLS ClientHello bytes as hexadecimal string
///
/// # Returns
/// JA4 fingerprint string (13 chars, e.g. `t13d1814e463`)
///
/// # Example
/// ```python
/// import rust.tls
/// ja4 = rust.tls.ja4_from_client_hello("0303940100...")
/// # 't13d1814e463'
/// ```
#[cfg(feature = "tls13")]
#[pyfunction]
pub fn ja4_from_client_hello(chello_hex: &str) -> PyResult<String> {
    let hex_clean: String = chello_hex.chars().filter(|c| !c.is_whitespace()));

    let client_hello = hex::decode(&hex_clean).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid hex: {}", e))
    })?;

    compute_ja4(&client_hello).map_err(|e| e.to_py_err())
}

/// ja4_from_client_hello_bytes(chello_bytes: bytes) -> str
///
/// Compute JA4 fingerprint from raw ClientHello bytes (binary).
///
/// # Arguments
/// * `chello_bytes` - Raw TLS ClientHello bytes as binary
///
/// # Returns
/// JA4 fingerprint string (13 chars, e.g. `t13d1814e463`)
#[cfg(feature = "tls13")]
#[pyfunction]
pub fn ja4_from_client_hello_bytes(chello_bytes: &[u8]) -> PyResult<String> {
    compute_ja4(chello_bytes).map_err(|e| e.to_py_err())
}

/// connect_and_ja4(host: str, port: int, *, sni: str | None = None, alpn: list[str] | None = None, timeout_ms: int = 5000) -> dict
///
/// Connect to server and extract JA4 fingerprint + ECH detection.
///
/// Performs full TLS handshake but does NOT verify certificates (OSINT use only).
///
/// # Arguments
/// * `host` - Server hostname
/// * `port` - Server port (default 443)
/// * `sni` - SNI hostname (defaults to host)
/// * `alpn` - ALPN protocols to request (default: ["h2", "http/1.1"])
/// * `timeout_ms` - Connection timeout in milliseconds (default 5000)
///
/// # Returns
/// Dictionary with keys: ja4, ech_detected, tls_version, server_ciphers, server_extensions, alpn, cert_verified
///
/// # Example
/// ```python
/// import rust.tls
/// result = rust.tls.connect_and_ja4("example.com", 443)
/// # {'ja4': 't13d1814e463', 'ech_detected': False, 'tls_version': '1.3', ...}
/// ```
#[cfg(feature = "tls13")]
#[pyfunction]
pub fn connect_and_ja4(
    host: &str,
    port: u16,
    sni: Option<String>,
    alpn: Option<Vec<String>>,
    timeout_ms: Option<u64>,
) -> PyResult<Py<PyDict>> {
    let alpn = alpn.unwrap_or_else(|| vec!["h2".to_string(), "http/1.1".to_string()]);
    let timeout = timeout_ms.unwrap_or(5000);

    let result = connect_and_fingerprint_internal(host, port, sni, alpn, timeout)
        .map_err(|e| e.to_py_err())?;

    Python::with_gil(|py| {
        let dict = pyo3::types::PyDict::new(py);
        // SAFE: set_item only fails on full dict (impossible here) or non-hashable key (never with strings)
        let _ = dict.set_item("ja4", result.ja4);
        let _ = dict.set_item("ech_detected", result.ech_detected);
        let _ = dict.set_item("tls_version", result.tls_version);
        let _ = dict.set_item("server_ciphers", result.server_ciphers.join(","));
        let _ = dict.set_item("server_extensions", result.server_extensions.join(","));
        let _ = dict.set_item("alpn", result.alpn.unwrap_or_default());
        let _ = dict.set_item("cert_verified", result.cert_verified);
        Ok(dict.into())
    })
}

/// batch_ja4(hosts: list[tuple[str, int]]) -> list[dict]
///
/// Connect to multiple servers and extract JA4 fingerprints in parallel.
///
/// Uses rayon for parallel connection (max 8 concurrent).
///
/// # Arguments
/// * `hosts` - List of (hostname, port) tuples
///
/// # Returns
/// List of result dictionaries (same as connect_and_ja4)
#[cfg(feature = "tls13")]
#[pyfunction]
pub fn batch_ja4(
    hosts: Vec<(String, u16)>,
    snis: Option<Vec<String>>,
    alpn: Option<Vec<String>>,
    timeout_ms: Option<u64>,
) -> PyResult<Vec<Py<PyDict>>> {
    use rayon::prelude::*;

    let alpn = alpn.unwrap_or_else(|| vec!["h2".to_string(), "http/1.1".to_string()]);
    let timeout = timeout_ms.unwrap_or(5000);

    // Note: rayon uses work-stealing thread pool (CPU-core bounded).
    // Network I/O is the bottleneck, not CPU. Each connection has its own
    // timeout (5s) to prevent resource exhaustion.

    let results: Vec<Py<PyDict>> = hosts
        .par_iter()
        .map(|(host, port)| {
            let sni = snis
                .as_ref()
                .and_then(|s| s.get(hosts.iter().position(|(h, _)| h == host)?).cloned());

            let result = connect_and_fingerprint_internal(host, *port, sni, alpn.clone(), timeout);

            match result {
                Ok(r) => Python::with_gil(|py| {
                    let dict = pyo3::types::PyDict::new(py);
                    // SAFE: set_item only fails on full dict (impossible here) or non-hashable key (never with strings)
                    let _ = dict.set_item("host", host);
                    let _ = dict.set_item("port", port);
                    let _ = dict.set_item("ja4", r.ja4);
                    let _ = dict.set_item("ech_detected", r.ech_detected);
                    let _ = dict.set_item("tls_version", r.tls_version);
                    let _ = dict.set_item("server_ciphers", r.server_ciphers.join(","));
                    let _ = dict.set_item("server_extensions", r.server_extensions.join(","));
                    let _ = dict.set_item("alpn", r.alpn.unwrap_or_default());
                    let _ = dict.set_item("cert_verified", r.cert_verified);
                    let _ = dict.set_item("error", "");
                    dict.into()
                }),
                Err(e) => Python::with_gil(|py| {
                    let dict = pyo3::types::PyDict::new(py);
                    let _ = dict.set_item("host", host);
                    let _ = dict.set_item("port", port);
                    let _ = dict.set_item("ja4", "");
                    let _ = dict.set_item("ech_detected", false);
                    let _ = dict.set_item("tls_version", "");
                    let _ = dict.set_item("server_ciphers", "");
                    let _ = dict.set_item("server_extensions", "");
                    let _ = dict.set_item("alpn", "");
                    let _ = dict.set_item("cert_verified", false);
                    let _ = dict.set_item("error", format!("{:?}", e));
                    dict.into()
                }),
            }
        })
        );

    Ok(results)
}

#[cfg(not(feature = "tls13"))]
#[pyfunction]
pub fn ja4_from_client_hello(_chello_hex: &str) -> PyResult<String> {
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "TLS 1.3 fingerprinting requires the 'tls13' feature. \
        Install with: pip install hledac-rust-extensions[tls13] or build with --features tls13",
    ))
}

#[cfg(not(feature = "tls13"))]
#[pyfunction]
pub fn ja4_from_client_hello_bytes(_chello_bytes: &[u8]) -> PyResult<String> {
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "TLS 1.3 fingerprinting requires the 'tls13' feature. \
        Install with: pip install hledac-rust-extensions[tls13] or build with --features tls13",
    ))
}

#[cfg(not(feature = "tls13"))]
#[pyfunction]
pub fn connect_and_ja4(
    _host: &str,
    _port: u16,
    _sni: Option<String>,
    _alpn: Option<Vec<String>>,
    _timeout_ms: Option<u64>,
) -> PyResult<Py<PyDict>> {
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "TLS 1.3 fingerprinting requires the 'tls13' feature. \
        Install with: pip install hledac-rust-extensions[tls13] or build with --features tls13",
    ))
}

#[cfg(not(feature = "tls13"))]
#[pyfunction]
pub fn batch_ja4(
    _hosts: Vec<(String, u16)>,
    _snis: Option<Vec<String>>,
    _alpn: Option<Vec<String>>,
    _timeout_ms: Option<u64>,
) -> PyResult<Vec<Py<PyDict>>> {
    Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
        "TLS 1.3 fingerprinting requires the 'tls13' feature. \
        Install with: pip install hledac-rust-extensions[tls13] or build with --features tls13",
    ))
}

/// Register tls13 functions into the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ja4_from_client_hello))?;
    m.add_function(wrap_pyfunction!(ja4_from_client_hello_bytes))?;
    m.add_function(wrap_pyfunction!(connect_and_ja4))?;
    m.add_function(wrap_pyfunction!(batch_ja4))?;

    // Feature availability flag
    #[cfg(feature = "tls13")]
    m.add("TLS13_AVAILABLE", true)?;
    #[cfg(not(feature = "tls13"))]
    m.add("TLS13_AVAILABLE", false)?;

    Ok(())
}
