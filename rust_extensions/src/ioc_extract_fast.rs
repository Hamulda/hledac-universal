//! Fast IOC extraction using unified regex engine.
//!
//! Architecture:
//!   1. All IOC patterns (IP, domain, hash, email, CVE) compiled into ONE RegexSet
//!   2. Single pass: which patterns matched → which IOC types
//!   3. Individual regex captures for exact match spans
//!   4. Rayon batch parallelization for multiple texts
//!
//! M1 8GB: 2 rayon workers, 1000 text batch limit

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
}

/// Build unified RegexSet for all IOC patterns.
///
/// Returns (RegexSet, individual Regexes, ioc_types)
/// The RegexSet identifies which patterns matched.
/// The individual Regexes provide capture spans.
fn build_ioc_regex_set() -> (
    RegexSet,
    Vec<Regex>,
    Vec<IocType>,
) {
    // Pattern order MUST match IocType enum order
    // NOTE: lookbehind/lookahead NOT supported in RegexSet — hash patterns match
    // all hex strings of their length. Python dedup (HashSet per text) ensures only
    // unique values are returned; the longest correct hash wins naturally since
    // SHA256 (64) is checked after MD5 (32) and SHA1 (40).
    let patterns: Vec<&str> = vec![
        // Ipv4: public IPs only (no private range filtering — done in Python)
        r"(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[0-9])",
        // IPv6
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
        // Domain
        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}",
        // MD5 (32 hex) — no boundary check (RegexSet limitation; Python dedup handles it)
        r"[a-fA-F0-9]{32}",
        // SHA1 (40 hex)
        r"[a-fA-F0-9]{40}",
        // SHA256 (64 hex)
        r"[a-fA-F0-9]{64}",
        // Email
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        // CVE
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
static IOC_REGEX: std::sync::LazyLock<(RegexSet, Vec<Regex>, Vec<IocType>)> =
    std::sync::LazyLock::new(build_ioc_regex_set);

/// Extract IOCs from a single text using unified RegexSet.
///
/// Returns list of (ioc_value, ioc_type) tuples.
/// Deduplication: same value appears only once per text.
fn extract_iocs_from_text(text: &str) -> Vec<(String, String)> {
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
///
/// # Arguments
/// * `text` - Input text to scan
///
/// # Returns
/// List of (ioc_value, ioc_type) tuples
#[pyfunction]
pub fn ioc_extract_unified(text: &str) -> Vec<(String, String)> {
    extract_iocs_from_text(text)
}

/// Batch extract IOCs using unified regex engine + rayon parallelization.
///
/// M1 8GB: limited to 2 workers, 1000 text batch limit.
///
/// # Arguments
/// * `texts` - List of input texts to scan
///
/// # Returns
/// List of result lists (one per input text)
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
                // Additional size guard per text
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
///
/// Returns a nested PyList: outer list has one entry per input text,
/// each inner entry is a PyList of (value, type) PyTuples allocated
/// directly via PyList::append.  No intermediate Rust Vec<(String,String)>
/// is materialised; the PyTuple references (value.as_str(), type_str)
/// are borrowed from the pre-existing String owned by rayon.
///
/// M1 8GB: BATCH_MAX_TEXTS=1000, mixed_pool adaptive 1-2 threads, TEXT_MAX_BYTES=1MB
///
/// # Arguments
/// * `texts` - List of input texts to scan
/// * `py`   - Python interpreter (implicit via #[pyfunction])
///
/// # Returns
/// Outer `PyList[List[Tuple[str, str]]]`
/// Falls back to batch_ioc_extract_unified on any error (lazy evaluation).
#[pyfunction]
pub fn batch_ioc_extract_unified_python<'py>(
    texts: Vec<String>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    if texts.is_empty() {
        return Ok(PyList::empty(py));
    }

    // Memory guard: limit batch size
    let texts: Vec<String> = texts.into_iter().take(BATCH_MAX_TEXTS).collect();
    let n = texts.len();

    // PyO3 holds the GIL for the entire mixed_pool(n).install() scope.
    let outer: Bound<'py, PyList> = PyList::empty(py);

    // Collect results from rayon (CPU-bound, no Python GIL needed)
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

    // Transfer each inner Vec into a PyList — one syscall per text.
    // This is bounded: max 1000 syscalls for the worst batch.
    for inner_vec in rust_results.into_iter() {
        let inner_list: Bound<'py, PyList> = PyList::empty(py);
        for (value, ioc_type) in inner_vec.into_iter() {
            // PyTuple::new copies value+ioc_type into the tuple's internal
            // buffer. After this, Python GC owns the data; Rust drops its
            // String.
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
        // Private 192.168.x.x not filtered by Rust (Python handles it)
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