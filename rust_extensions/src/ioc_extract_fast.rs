//! Fast IOC extraction using unified regex engine.
//!
//! Architecture:
//!   1. All IOC patterns compiled into ONE RegexSet (single-pass scan)
//!   2. Single pass: which patterns matched → which IOC types
//!   3. Individual regex captures for exact match spans + start/end positions
//!   4. Rayon batch parallelization for multiple texts
//!
//! M1 8GB: 2 rayon workers, 1000 text batch limit
//!
//! Issue #8: SHA1/SHA256/MD5 patterns use NO \b boundaries due to RegexSet
//! limitation (no word boundary support). Hash validation via is_valid_hex_hash()
//! compensates to prevent false positives.
//!
//! Issue #15: Extended to cover ALL structured IOC patterns from Python post-pass
//! (BTC, GHSA, Telegram, XMR, I2P, PGP, IPFS, USDT, LTC, DOGE, AWS, Google,
//! Stripe, Slack, MISP UUID, Onion v3) — single GIL acquisition replaces
//! 25× Python re.finditer() calls.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use rayon::iter::{IntoParallelRefIterator, ParallelIterator};
use regex::{Regex, RegexSet};
use std::collections::HashSet;

use crate::gil::release_gil;

// R24: tracing instrumentation — conditionally compiled when tracing feature is enabled
#[cfg(feature = "otel")]
use tracing::instrument;

/// Maximum texts per batch (M1 8GB memory guard)
const BATCH_MAX_TEXTS: usize = 1000;

/// Maximum text size in bytes per item
const TEXT_MAX_BYTES: usize = 1_000_000;

/// IOC type for each pattern index — MUST match pattern order in build_ioc_regex_set().
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IocType {
    Ipv4,
    Ipv6,
    Domain,
    Md5,
    Sha1,
    Sha256,
    Email,
    Cve,
    // Issue #15: extended patterns from Python post-pass
    BtcLegacy,
    BtcBech32,
    Ghsa,
    Telegram,
    MispUuid,
    OnionV3,
    XmrAddr,
    I2PAddr,
    PgpFingerprint,
    IpfsCid,
    UsdtTrc20,
    LtcAddr,
    DogeAddr,
    AwsKeyId,
    GoogleApiKey,
    StripeSk,
    SlackToken,
    EthAddr,
}

impl IocType {
    fn as_str(&self) -> &'static str {
        match self {
            IocType::Ipv4 => "ipv4",
            IocType::Ipv6 => "ipv6",
            IocType::Domain => "domain",
            IocType::Md5 => "md5",
            IocType::Sha1 => "sha1",
            IocType::Sha256 => "sha256",
            IocType::Email => "email",
            IocType::Cve => "cve",
            // Issue #15: extended types
            IocType::BtcLegacy => "btc_address",
            IocType::BtcBech32 => "btc_address",
            IocType::Ghsa => "ghsa_identifier",
            IocType::Telegram => "telegram_link",
            IocType::MispUuid => "misp_uuid",
            IocType::OnionV3 => "onion_v3",
            IocType::XmrAddr => "xmr_address",
            IocType::I2PAddr => "i2p_address",
            IocType::PgpFingerprint => "pgp_fingerprint",
            IocType::IpfsCid => "ipfs_cid",
            IocType::UsdtTrc20 => "usdt_trc20",
            IocType::LtcAddr => "ltc_address",
            IocType::DogeAddr => "doge_address",
            IocType::AwsKeyId => "aws_access_key_id",
            IocType::GoogleApiKey => "google_api_key",
            IocType::StripeSk => "stripe_secret_key",
            IocType::SlackToken => "slack_token",
            IocType::EthAddr => "eth_address",
        }
    }

    /// Returns true if this IOC type is a hash requiring hex validation.
    fn is_hash(&self) -> bool {
        matches!(self, IocType::Md5 | IocType::Sha1 | IocType::Sha256)
    }

    /// Expected character count for hash types, or None for non-hashes.
    fn hash_len(&self) -> Option<usize> {
        match self {
            IocType::Md5 => Some(32),
            IocType::Sha1 => Some(40),
            IocType::Sha256 => Some(64),
            _ => None,
        }
    }

    /// Returns true for hex hashes that need entropy validation (reject trivial values).
    fn needs_entropy_filter(&self) -> bool {
        matches!(self, IocType::Md5 | IocType::Sha1 | IocType::Sha256)
    }
}

/// Validate that a captured string is a valid hex hash for the given IOC type.
///
/// This compensates for RegexSet's lack of \b word boundaries.
fn is_valid_hex_hash(value: &str, ioc_type: IocType) -> bool {
    let Some(expected_len) = ioc_type.hash_len() else {
        return true;
    };
    if value.len() != expected_len {
        return false;
    }
    value.chars().all(|c| c.is_ascii_hexdigit())
}

/// Validate high-entropy hash: reject trivial patterns (all same char, sequential).
fn has_sufficient_entropy(value: &str) -> bool {
    let unique_chars: std::collections::HashSet<char> = value.chars().collect();
    unique_chars.len() >= 8
}

/// Build unified RegexSet for ALL IOC patterns (Issue #15).
///
/// Returns (RegexSet, individual Regexes, ioc_types)
/// Covers: IPv4/6, domain, MD5/SHA1/SHA256, email, CVE, + all Python post-pass
/// patterns (BTC, GHSA, Telegram, XMR, I2P, PGP, IPFS, USDT, LTC, DOGE,
/// AWS, Google, Stripe, Slack, MISP UUID, Onion v3).
///
/// CRITICAL: Pattern order MUST match IocType enum order exactly.
fn build_ioc_regex_set() -> (RegexSet, Vec<Regex>, Vec<IocType>) {
    // IMPORTANT: Order must match IocType enum order (ipv4=0, ipv6=1, domain=2, ...)
    let patterns: Vec<&str> = vec![
        // 0: Ipv4
        r"(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])",
        // 1: Ipv6
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
        // 2: Domain
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
        // 3: MD5 — validated by is_valid_hex_hash()
        r"[a-fA-F0-9]{32}",
        // 4: SHA1 — validated by is_valid_hex_hash()
        r"[a-fA-F0-9]{40}",
        // 5: SHA256 — validated by is_valid_hex_hash()
        r"[a-fA-F0-9]{64}",
        // 6: Email
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        // 7: CVE
        r"CVE-\d{4}-\d{4,}",
        // 8: BTC Legacy (base58check, 26-34 chars, starts with 1 or 3)
        r"[13][a-km-zA-HJ-NP-Z1-9]{26,34}",
        // 9: BTC Bech32 (bc1...)
        r"bc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,71}",
        // 10: GHSA
        r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}",
        // 11: Telegram t.me/ links
        r"t\.me/[\w\-]{3,}",
        // 12: MISP UUID
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        // 13: Onion v3 (56 char base32 + .onion)
        r"[a-z2-7]{56}\.onion",
        // 14: Monero (XMR) — 95 chars, starts with 4
        r"4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}",
        // 15: I2P B32 address
        r"[a-z2-7]{52}\.b32\.i2p",
        // 16: PGP fingerprint (40 hex chars, optional spaces)
        r"(?:[0-9A-F]{4}\s?){10}",
        // 17: IPFS CIDv0
        r"Qm[1-9A-HJ-NP-Za-km-z]{44}",
        // 18: USDT TRC20 (T prefix + 33 base58)
        r"T[A-HJ-NP-Za-km-z1-9]{33}",
        // 19: Litecoin P2PKH (L prefix + 33 base58)
        r"L[1-9A-HJ-NP-Za-km-z]{33}",
        // 20: Dogecoin P2PKH (D prefix + 33 base58)
        r"D[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{33}",
        // 21: AWS Access Key ID
        r"AKIA[0-9A-Z]{16}",
        // 22: Google API Key
        r"AIza[0-9A-Za-z\-_]{35}",
        // 23: Stripe live secret key
        r"sk_live_[0-9a-zA-Z]{24}",
        // 24: Slack token
        r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,32}",
        // 25: ETH address
        r"0x[a-fA-F0-9]{40}",
    ];

    let ioc_types: Vec<IocType> = vec![
        IocType::Ipv4,
        IocType::Ipv6,
        IocType::Domain,
        IocType::Md5,
        IocType::Sha1,
        IocType::Sha256,
        IocType::Email,
        IocType::Cve,
        IocType::BtcLegacy,
        IocType::BtcBech32,
        IocType::Ghsa,
        IocType::Telegram,
        IocType::MispUuid,
        IocType::OnionV3,
        IocType::XmrAddr,
        IocType::I2PAddr,
        IocType::PgpFingerprint,
        IocType::IpfsCid,
        IocType::UsdtTrc20,
        IocType::LtcAddr,
        IocType::DogeAddr,
        IocType::AwsKeyId,
        IocType::GoogleApiKey,
        IocType::StripeSk,
        IocType::SlackToken,
        IocType::EthAddr,
    ];

    let regex_set = RegexSet::new(&patterns).expect("valid IOC patterns");

    let individual_regexes: Vec<Regex> = patterns
        .iter()
        .map(|p| Regex::new(p).expect("valid pattern"))
        .collect();

    (regex_set, individual_regexes, ioc_types)
}

// ISSUE-014: LazyLock replaces lazy_static! macro
static IOC_REGEX: std::sync::LazyLock<(RegexSet, Vec<Regex>, Vec<IocType>)> =
    std::sync::LazyLock::new(build_ioc_regex_set);

/// Extract IOCs from a single text using unified RegexSet.
///
/// Returns list of (ioc_value, ioc_type) tuples.
/// Deduplication: same value appears only once per text.
pub fn extract_iocs_from_text(text: &str) -> Vec<(String, String)> {
    let (regex_set, individual_regexes, ioc_types) = &*IOC_REGEX;

    // Quick check: which patterns matched at all?
    let matches = regex_set.matches(text);

    let mut seen: HashSet<String> = HashSet::new();
    let mut results: Vec<(String, String)> = Vec::new();

    // For each matched pattern, find all captures
    for pattern_idx in matches.into_iter() {
        if pattern_idx >= ioc_types.len() {
            continue;
        }
        let ioc_type = ioc_types[pattern_idx];
        let re = &individual_regexes[pattern_idx];

        for m in re.find_iter(text) {
            let value = m.as_str();
            // FIX Issue #8: Validate hash matches to prevent false positives.
            if ioc_type.is_hash() && !is_valid_hex_hash(value, ioc_type) {
                continue;
            }
            // Issue #15: entropy filter for hex hashes
            if ioc_type.needs_entropy_filter() && !has_sufficient_entropy(value) {
                continue;
            }
            if seen.insert(value.to_string()) {
                let normalized = match ioc_type {
                    IocType::Domain | IocType::Email => value.to_lowercase(),
                    _ => value.to_string(),
                };
                results.push((normalized, ioc_type.as_str().to_string()));
            }
        }
    }

    results
}

/// Extract structured entities with position info — replaces Python 25× re.finditer() loop.
///
/// Issue #15: Returns (start, end, value, label) tuples sorted by start offset.
/// Single GIL acquisition vs 25× Python re.finditer() calls.
///
/// Deduplication by (label, value) pair.
pub fn extract_structured_entities(text: &str) -> Vec<(usize, usize, String, String)> {
    let (regex_set, individual_regexes, ioc_types) = &*IOC_REGEX;

    let matches = regex_set.matches(text);

    let mut seen: HashSet<(String, String)> = HashSet::new();
    let mut results: Vec<(usize, usize, String, String)> = Vec::new();

    for pattern_idx in matches.into_iter() {
        if pattern_idx >= ioc_types.len() {
            continue;
        }
        let ioc_type = ioc_types[pattern_idx];
        let re = &individual_regexes[pattern_idx];

        for m in re.find_iter(text) {
            let value = m.as_str();
            let start = m.start();
            let end = m.end();

            // Validate hashes
            if ioc_type.is_hash() && !is_valid_hex_hash(value, ioc_type) {
                continue;
            }
            if ioc_type.needs_entropy_filter() && !has_sufficient_entropy(value) {
                continue;
            }

            let label = ioc_type.as_str();
            let key = (label.to_string(), value.to_string());
            if seen.insert(key) {
                results.push((start, end, value.to_string(), label.to_string()));
            }
        }
    }

    // Sort by start offset (already in order from individual regex scans, but ensure sorted)
    results.sort_by_key(|r| r.0);
    results
}

/// Batch extract structured entities with rayon parallelization.
///
/// M1 8GB: adaptive 1-2 threads, 1000 text batch limit.
/// GIL is released via py.detach() so rayon workers don't block other coroutines.
pub fn batch_extract_structured_entities(
    texts: Vec<String>,
) -> Vec<Vec<(usize, usize, String, String)>> {
    if texts.is_empty() {
        return vec![];
    }

    let texts: Vec<String> = texts.into_iter().take(BATCH_MAX_TEXTS).collect();
    let n = texts.len();

    // Release GIL during rayon parallel scan — rayon workers are pure Rust (no Python objects).
    // GIL is reacquired automatically when the closure returns.
    Python::attach(|py| {
        release_gil(py, || {
            crate::mixed_pool(n).install(|| {
                texts
                    .par_iter()
                    .map(|text| {
                        if text.len() > TEXT_MAX_BYTES {
                            extract_structured_entities(&text[..TEXT_MAX_BYTES])
                        } else {
                            extract_structured_entities(text)
                        }
                    })
                    .collect()
            })
        })
    })
}

/// Issue #15: Extract structured entities with positions — Python-facing.
///
/// Replaces Python 25× re.finditer() post-pass in match_text().
/// Returns Vec of (start, end, value, label) sorted by start offset.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(text_len = text.len())))]
#[pyfunction]
pub fn extract_structured_entities_py(text: &str) -> Vec<(usize, usize, String, String)> {
    extract_structured_entities(text)
}

/// Issue #15: Batch extract structured entities with rayon parallelization.
///
/// M1 8GB: adaptive 1-2 threads, 1000 text batch limit.
/// Returns Vec of Vec of (start, end, value, label).
#[cfg_attr(feature = "otel", instrument(skip_all, fields(batch_size = texts.len())))]
#[pyfunction]
pub fn batch_extract_structured_entities_py(
    texts: Vec<String>,
) -> Vec<Vec<(usize, usize, String, String)>> {
    batch_extract_structured_entities(texts)
}

/// Extract IOCs using unified regex engine (Python-facing).
///
/// Single pass across all IOC patterns.
/// Thread-safe, reuses compiled RegexSet.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(text_len = text.len())))]
#[pyfunction]
pub fn ioc_extract_unified(text: &str) -> Vec<(String, String)> {
    extract_iocs_from_text(text)
}

/// Batch extract IOCs using unified regex engine + rayon parallelization.
///
/// M1 8GB: limited to 2 workers, 1000 text batch limit.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(batch_size = texts.len())))]
#[pyfunction]
pub fn batch_ioc_extract_unified(texts: Vec<String>) -> Vec<Vec<(String, String)>> {
    if texts.is_empty() {
        return vec![];
    }

    // Memory guard: limit batch size
    let texts: Vec<String> = texts.into_iter().take(BATCH_MAX_TEXTS).collect();
    let n = texts.len();

    // Release GIL during rayon parallel scan — rayon workers are pure Rust (no Python objects).
    // GIL is reacquired automatically when the closure returns.
    Python::attach(|py| {
        release_gil(py, || {
            // adaptive 1-2 threads: n < 64 → 1 thread (no pool overhead); n ≥ 64 → 2 threads (P-core ceiling)
            crate::mixed_pool(n).install(|| {
                texts.par_iter()
                    .map(|text| {
                        if text.len() > TEXT_MAX_BYTES {
                            extract_iocs_from_text(&text[..TEXT_MAX_BYTES])
                        } else {
                            extract_iocs_from_text(text)
                        }
                    })
                    .collect()
            })
        })
    })
}

/// Zero-copy batch IOC extractor — writes results directly into Python heap.
#[cfg_attr(feature = "otel", instrument(skip_all, fields(batch_size = texts.len())))]
#[pyfunction]
pub fn batch_ioc_extract_unified_python<'py>(
    texts: Vec<String>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    if texts.is_empty() {
        return Ok(PyList::empty(py));
    }

    let texts: Vec<String> = texts.into_iter().take(BATCH_MAX_TEXTS).collect();
    let n = texts.len();

    // Phase 1: rayon-parallel extraction — pure Rust, no Python objects.
    // Release GIL so rayon workers don't block other coroutines.
    let rust_results: Vec<Vec<(String, String)>> = release_gil(py, || {
        crate::mixed_pool(n).install(|| {
            texts
                .par_iter()
                .map(|text| {
                    if text.len() > TEXT_MAX_BYTES {
                        extract_iocs_from_text(&text[..TEXT_MAX_BYTES])
                    } else {
                        extract_iocs_from_text(text)
                    }
                })
                .collect()
        })
    });

    // Phase 2: build Python objects AFTER rayon completes (GIL block, no rayon active)
    let outer: Bound<'py, PyList> = PyList::empty(py);
    for inner_vec in rust_results {
        let inner_list: Bound<'py, PyList> = PyList::empty(py);
        for (value, ioc_type) in inner_vec {
            let t = PyTuple::new(py, &[&value, &ioc_type]).unwrap();
            let _ = inner_list.append(t);
        }
        let _ = outer.append(inner_list).unwrap();
    }

    Ok(outer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ipv4_extraction() {
        let results = extract_iocs_from_text("Server 192.168.1.1 and 8.8.8.8");
        let ips: Vec<_> = results.iter().filter(|(_v, t)| t == "ipv4").collect();
        assert!(!ips.is_empty(), "Should extract some IPs: {results:?}");
    }

    #[test]
    fn test_hash_extraction() {
        let text = r"MD5: d41d8cd98f00b204e9800998ecf8427e, SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
        let results = extract_iocs_from_text(text);
        assert!(results.iter().any(|(v, t)| t == "md5" && v == "d41d8cd98f00b204e9800998ecf8427e"));
        assert!(results.iter().any(|(v, t)| t == "sha256" && v == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    }

    #[test]
    fn test_email_extraction() {
        let results = extract_iocs_from_text("Contact admin@example.com or support@test.org");
        assert!(results.iter().any(|(v, t)| t == "email" && v == "admin@example.com"));
    }

    #[test]
    fn test_cve_extraction() {
        let results = extract_iocs_from_text("CVE-2024-12345 vulnerability");
        assert!(results.iter().any(|(v, t)| t == "cve" && v == "CVE-2024-12345"));
    }

    #[test]
    fn test_dedup() {
        let results = extract_iocs_from_text("8.8.8.8 8.8.8.8 8.8.8.8");
        let ips: Vec<_> = results.iter().filter(|(_, t)| t == "ipv4").collect();
        assert_eq!(ips.len(), 1, "Should dedupe: {results:?}");
    }

    #[test]
    fn test_batch_extract() {
        let texts = vec![
            "Server 8.8.8.8".to_string(),
            "admin@example.com".to_string(),
        ];
        let results = batch_ioc_extract_unified(texts);
        assert_eq!(results.len(), 2);
        assert!(!results[0].is_empty() || !results[1].is_empty());
    }
}
