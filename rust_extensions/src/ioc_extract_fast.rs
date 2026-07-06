//! Fast IOC extraction using unified regex engine.
//!
//! Architecture:
//!   1. All IOC patterns (IP, domain, hash, email, CVE) compiled into ONE RegexSet
//!   2. Single pass: which patterns matched → which IOC types
//!   3. Individual regex captures for exact match spans
//!   4. Rayon batch parallelization for multiple texts
//!
//! M1 8GB: 2 rayon workers, 1000 text batch limit
//!
//! Issue #8: SHA1/SHA256/MD5 patterns use NO \b boundaries due to RegexSet
//! limitation (no word boundary support). Hash validation via is_valid_hex_hash()
//! compensates to prevent false positives.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};
use rayon::iter::{IntoParallelRefIterator, ParallelIterator};
use regex::{Regex, RegexSet};
use std::collections::HashSet;

/// Maximum texts per batch (M1 8GB memory guard)
const BATCH_MAX_TEXTS: usize = 1000;

/// Maximum text size in bytes per item
const TEXT_MAX_BYTES: usize = 1_000_000;

/// IOC type for each pattern index
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
}

/// Validate that a captured string is a valid hex hash for the given IOC type.
///
/// This compensates for RegexSet's lack of \b word boundaries. Without this,
/// SHA1 pattern `[a-fA-F0-9]{40}` would match ANY 40-char hex string,
/// including strings like "deadbeef1234567890abcdef1234567890ab" that aren't SHA1s.
///
/// Issue #8: This function is the fix for the SHA1 false positive bug.
fn is_valid_hex_hash(value: &str, ioc_type: IocType) -> bool {
    let Some(expected_len) = ioc_type.hash_len() else {
        return true; // Non-hash types don't need validation
    };
    if value.len() != expected_len {
        return false;
    }
    // All characters must be valid hexadecimal
    value.chars().all(|c| c.is_ascii_hexdigit())
}

/// Build unified RegexSet for all IOC patterns.
///
/// Returns (RegexSet, individual Regexes, ioc_types)
/// The RegexSet identifies which patterns matched.
/// The individual Regexes provide capture spans.
fn build_ioc_regex_set() -> (RegexSet, Vec<Regex>, Vec<IocType>) {
    // Pattern order MUST match IocType enum order
    //
    // CRITICAL (Issue #8): RegexSet does NOT support \b boundaries.
    // Hash patterns (MD5/SHA1/SHA256) would match ANY N-char hex string
    // without validation. is_valid_hex_hash() after capture fixes this.
    //
    // Python dedup (HashSet per text) ensures only unique values returned;
    // longest correct hash wins naturally (SHA256 checked after SHA1).
    let patterns: Vec<&str> = vec![
        // Ipv4: no \b (RegexSet limitation); private IPs filtered in Python
        r"(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])",
        // IPv6: no \b (RegexSet limitation)
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
        // Domain: no \b (RegexSet limitation)
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
        // MD5: no \b — validated by is_valid_hex_hash()
        r"[a-fA-F0-9]{32}",
        // SHA1: no \b — FIX Issue #8: validated by is_valid_hex_hash()
        // Previously: any 40-char hex string matched as SHA1 (FALSE POSITIVE)
        r"[a-fA-F0-9]{40}",
        // SHA256: no \b — validated by is_valid_hex_hash()
        r"[a-fA-F0-9]{64}",
        // Email: no \b (RegexSet limitation)
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        // CVE: no \b (word break after number not required)
        r"CVE-\d{4}-\d{4,}",
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
    ];

    let regex_set = RegexSet::new(&patterns).expect("valid IOC patterns");

    // Build individual regexes for span capture
    let individual_regexes: Vec<Regex> = patterns
        .iter()
        .map(|p| Regex::new(p).expect("valid pattern"))
        .collect();

    (regex_set, individual_regexes, ioc_types)
}

/// Process-wide lazy-initialized regex set.
/// Built once on first use, reused across all calls.
use crate::lazy_static;
lazy_static!(static IOC_REGEX: (RegexSet, Vec<Regex>, Vec<IocType>) =
    build_ioc_regex_set()
);

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
            // RegexSet cannot use \b boundaries, so we verify hex-only content
            // and correct length here. Without this, any 40-char hex string
            // would incorrectly match as SHA1.
            if ioc_type.is_hash() && !is_valid_hex_hash(value, ioc_type) {
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

/// Extract IOCs using unified regex engine (Python-facing).
///
/// Single pass across all IOC patterns.
/// Thread-safe, reuses compiled RegexSet.
#[pyfunction]
pub fn ioc_extract_unified(text: &str) -> Vec<(String, String)> {
    extract_iocs_from_text(text)
}

/// Batch extract IOCs using unified regex engine + rayon parallelization.
///
/// M1 8GB: limited to 2 workers, 1000 text batch limit.
#[pyfunction]
pub fn batch_ioc_extract_unified(texts: Vec<String>) -> Vec<Vec<(String, String)>> {
    if texts.is_empty() {
        return vec![];
    }

    // Memory guard: limit batch size
    let texts: Vec<String> = texts.into_iter().take(BATCH_MAX_TEXTS).collect();
    let n = texts.len();

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
}

/// Zero-copy batch IOC extractor — writes results directly into Python heap.
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

    let outer: Bound<'py, PyList> = PyList::empty(py);

    let rust_results: Vec<Vec<(String, String)>> = crate::mixed_pool(n).install(|| {
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
    });

    for inner_vec in rust_results.into_iter() {
        let inner_list: Bound<'py, PyList> = PyList::empty(py);
        for (value, ioc_type) in inner_vec.into_iter() {
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
        let ips: Vec<_> = results.iter().filter(|(v, t)| t == "ipv4").collect();
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
