// Manually maintained source of truth for all IOC regex patterns.
// Codegen: tools/codegen_ioc_patterns.py generates:
//   - forensics/ioc_patterns_generated.py (Python fallback)
//   - rust_extensions/src/ioc_patterns_generated.rs (Rust SIMD patterns)
// Pattern order in ioc_patterns_generated.rs MUST match pattern_to_ioc_type
// indices in ioc_extract_simd.rs: 0=IPv4, 1=Domain, 2=MD5, 3=SHA1,
// 4=SHA256, 5=Email, 6=CVE, 7=MAC, 8=BTC, 9=ETH
//
// NOTE: build.rs (Cargo build script) only handles Python version detection
// via pyo3-build-config — it does NOT generate or modify these patterns.

/// IPv4 address pattern (RFC 791)
/// Format: 4 octets 0-255 separated by dots
pub static IPV4_PAT: &str = r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b";

/// IPv6 address pattern (RFC 4291)
/// Format: 8 groups of 4 hex digits separated by colons
pub static IPV6_PAT: &str = r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b";

/// Domain name pattern (RFC 1035)
/// Format: labels separated by dots, TLD must be 2+ letters
pub static DOMAIN_PAT: &str =
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b";

/// MD5 hash pattern
/// Format: exactly 32 hexadecimal characters with word boundaries
pub static MD5_PAT: &str = r"\b[a-fA-F0-9]{32}\b";

/// SHA1 hash pattern
/// Format: exactly 40 hexadecimal characters with word boundaries
/// CRITICAL: word boundary prevents matching arbitrary 40-char hex strings
pub static SHA1_PAT: &str = r"\b[a-fA-F0-9]{40}\b";

/// SHA256 hash pattern
/// Format: exactly 64 hexadecimal characters with word boundaries
pub static SHA256_PAT: &str = r"\b[a-fA-F0-9]{64}\b";

/// Email address pattern (RFC 5321)
/// Format: local@domain with allowed chars: .%+-
pub static EMAIL_PAT: &str = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b";

/// CVE identifier pattern
/// Format: CVE-YYYY-NNNNN+ where YYYY is year, NNNNN+ is 4+ digit ID
/// NOTE: No trailing \b — CVE numbers don't have word break after them
///       e.g., "CVE-2024-12345678" has no \b after the number
pub static CVE_PAT: &str = r"CVE-\d{4}-\d{4,}";

/// URL pattern (http/https)
/// Matches http:// or https:// followed by non-whitespace content
pub static URL_PAT: &str = r#"https?://[^\s<>"']+"#;

/// Generic hash pattern for Python fallback (32-64 hex chars)
/// Used when specific hash type cannot be determined
pub static HASH_PAT: &str = r"\b[a-fA-F0-9]{32,64}\b";

/// Base32 encoding pattern
/// Format: A-Z and 2-7 characters, optional padding with =
pub static ENCODING_BASE32_PAT: &str = r"^[A-Z2-7]+=*$";

/// Base64 encoding pattern
/// Format: mixed alphanumeric with +/ padding
pub static ENCODING_BASE64_PAT: &str = r"^[A-Za-z0-9+/]+=*$";

/// Hexadecimal encoding pattern
/// Format: even length sequence of hex digits
pub static ENCODING_HEX_PAT: &str = r"^[0-9a-fA-F]+$";

/// High entropy string pattern (mix of cases/digits)
/// Detects potential encoded content in DNS queries
pub static ENCODING_HIGH_ENTROPY_PAT: &str = r"[a-z][A-Z]|[A-Z][a-z]|[a-zA-Z][0-9]|[0-9][a-zA-Z]";

// Issue #4: MAC address, Bitcoin, Ethereum IOC patterns

/// MAC address pattern (IEEE 802)
/// Format: 6 hex pairs separated by colons or hyphens
pub static MAC_PAT: &str = r"\b[0-9a-fA-F]{2}(?:[:\-][0-9a-fA-F]{2}){5}\b";

/// Bitcoin address pattern (P2PKH, P2SH, Bech32)
/// P2PKH: 1... (base58) | P2SH: 3... (base58) | Bech32: bc1... (bech32)
pub static BTC_PAT: &str = r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}\b";

/// Ethereum address pattern
/// Format: 0x + 40 hex characters
pub static ETH_PAT: &str = r"\b0x[a-fA-F0-9]{40}\b";
