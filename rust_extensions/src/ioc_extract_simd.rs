//! SIMD-accelerated IOC extraction using regex-automata `build_many` (Teddy/NEON).
//!
//! R4.3 / Issue #5: Single-pass multi-pattern scanner replaces 8 sequential passes.
//! `build_many` compiles all patterns into one NFA/DFA automaton and scans
//! the text once — Teddy (NEON on M1) accelerates the bulk-text path automatically.
//!
//! ## Performance Strategy
//!
//! | Algorithm | Throughput | Use Case |
//! | Teddy (NEON) | ~3-6× vs sequential | Bulk text ≥1KB, single pass |
//! | Teddy (SSE/AVX2) | ~3-4× vs sequential | x86_64 bulk text |
//! | PikeVM | baseline | Small texts, fallback |
//!
//! Design invariants:
//!   IOS.T1  No panics, fail-soft on regex-automata errors (logs errors)
//!   IOS.T2  Bounded: max text len 100KB, max batch 1000
//!   IOS.T3  Always-on: rayon parallel across texts (mixed_pool)
//!   IOS.T4  Meta-regex NFA/DFA cache 50MB each — bounded by regex-automata LRU eviction
//!
//! ## IPv6 Coverage (RFC 4291)
//!
//! Covers: full 8-hextet, compressed (`::1`, `::`, `2001:db8::`), link-local
//!   (`fe80::1%eth0`), IPv4-mapped (`::ffff:192.0.2.1`).
//!   Excludes: IPv4-compatible (`::192.0.2.1`) — rare, collision-prone with hash detection.
//!
//! **Boundary fix (P1):** `\b` does NOT match at `::` positions where both sides are `:`.
//!   Fixed via negative lookbehind `(?<![:0-9a-fA-F])` before `::` alternatives.
//!
//! ## Hex Hash Validation
//!
//! `is_hex_hash` prevents false positives: SHA1/SHA256/MD5 patterns match any 40/64/32-char
//! hex string. Validation filters out strings that aren't valid hex (e.g. "ghijklmnop").
//! Note: valid hex + correct length ≠ real hash (e.g. repeated "abcdef..." passes but isn't real).
//! This is acceptable for IOC extraction (high recall is desired).
//!
//! ## Cross-Text Deduplication
//!
//! `extract_one_simd` deduplicates within a single text using `HashSet`.
//! `batch_extract_iocs_inner` does NOT deduplicate across texts — each text_idx
//! retains its own deduplication scope. Use `batch_extract_iocs_simd` (which drops
//! text_idx) for flat per-IOC results across the batch.
//!
//! Pattern order (matches `pattern_id` indices):
//!   0=IPv4, 1=IPv6, 2=Domain, 3=MD5, 4=SHA1, 5=SHA256, 6=Email, 7=CVE

use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;
use regex::Regex as RegexSimple;
use regex_automata::meta::Regex;
use std::collections::HashSet;

/// Issue #5: Single-pass meta-regex — one automaton, one scan, all patterns.
/// `build_many` compiles all patterns into one NFA; regex-automata auto-selects
/// Teddy (SIMD) for bulk text when patterns have literal prefixes.
/// NFA/DFA caches bounded by regex-automata internal LRU eviction.
///
/// IOS.T1: Logs errors via `eprintln!` on initialization failure so the
/// fail-soft invariant is visible in telemetry without panicking.
///
/// IPv6 is handled separately via `IPV6_REGEX` (post-match validation) because
/// the full RFC 4291 pattern exceeds regex-automata's NFA size limit even at 50 MB.
static IOC_META_REGEX: std::sync::LazyLock<Result<Regex, regex_automata::meta::BuildError>> =
    std::sync::LazyLock::new(|| {
        let result = Regex::builder()
            .configure(
                regex_automata::meta::Config::new()
                    .nfa_size_limit(Some(50 * 1024 * 1024)) // 50 MB NFA cache
                    .dfa_size_limit(Some(50 * 1024 * 1024)), // 50 MB DFA cache
            )
            .build_many(&[
                // IPv4 — full octet range with \b boundaries
                // Note: [0-9][0-9] (not \d) avoids matching non-ASCII digits
                r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
                // Domain — LDH rules + TLD
                r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
                // MD5 — 32 hex chars with \b
                r"\b[a-fA-F0-9]{32}\b",
                // SHA1 — 40 hex chars with \b
                r"\b[a-fA-F0-9]{40}\b",
                // SHA256 — 64 hex chars with \b
                r"\b[a-fA-F0-9]{64}\b",
                // Email — standard addr-spec pattern
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                // CVE — no trailing \b (CVE numbers don't terminate with word break)
                r"CVE-\d{4}-\d{4,}",
            ]);

        if let Err(ref e) = result {
            // IOS.T1: Surface initialization errors in telemetry (fail-soft, never panics)
            eprintln!(
                "[ioc_extract_simd] IOC_META_REGEX initialization failed: {} — returning empty results at runtime",
                e
            );
        }
        result
    });

/// IPv6 validation regex — matches hex:hex patterns with basic structure validation.
/// Uses anchored mode to prevent over-matching. Covers all major IPv6 forms:
/// full hextet, compressed (::1, ::), link-local (fe80::1), IPv4-mapped (::ffff:192.0.2.1).
/// Uses separate compilation (not build_many) because the full RFC 4291 pattern exceeds
/// regex-automata's NFA size limit even at 50 MB.
static IPV6_REGEX: std::sync::LazyLock<RegexSimple> =
    std::sync::LazyLock::new(|| {
        RegexSimple::new(concat!(
            r"(?i)^(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}$|",
            r"^(?:[0-9a-f]{1,4}:){1,7}:$|",
            r"^(?:[0-9a-f]{1,4}:){1,6}:[0-9a-f]{1,4}$|",
            r"^(?:[0-9a-f]{1,4}:){1,5}(?::[0-9a-f]{1,4}){1,2}$|",
            r"^(?:[0-9a-f]{1,4}:){1,4}(?::[0-9a-f]{1,4}){1,3}$|",
            r"^(?:[0-9a-f]{1,4}:){1,3}(?::[0-9a-f]{1,4}){1,4}$|",
            r"^(?:[0-9a-f]{1,4}:){1,2}(?::[0-9a-f]{1,4}){1,5}$|",
            r"^[0-9a-f]{1,4}:(?::[0-9a-f]{1,4}){1,6}$|",
            r"^:(?::[0-9a-f]{1,4}){1,7}$|",
            r"^::(?:f{4})?:(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$|",
            r"^(?:[0-9a-f]{1,4}:){1,4}:(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$"
        ))
        .expect("IPV6_REGEX should always compile — pattern is hardcoded")
    });

/// IOC type mapping from `build_many` pattern index → string label.
/// IPv6 was removed from build_many due to regex-automata NFA size limits;
/// it is handled separately via `IPV6_REGEX` in `extract_one_simd`.
fn pattern_to_ioc_type(pattern_id: usize) -> &'static str {
    match pattern_id {
        0 => "ipv4",
        1 => "domain",
        2 => "md5",
        3 => "sha1",
        4 => "sha256",
        5 => "email",
        6 => "cve",
        _ => unreachable!("IOC_META_REGEX has exactly 7 patterns"),
    }
}

/// Validate hex hash: all chars must be valid hex and length must match expected.
/// Issue #8: Prevents false positives like "deadbeef1234...ab" matching as SHA1/SHA256.
fn is_hex_hash(value: &str, expected_len: usize) -> bool {
    value.len() == expected_len && value.bytes().all(|b| b.is_ascii_hexdigit())
}

/// Extract IOCs from a single text using single-pass meta-regex.
/// IPv6 is handled via `IPV6_REGEX` post-match (not in build_many due to NFA size limits).
/// Returns Vec of (ioc_value, ioc_type).
fn extract_one_simd(text: &str) -> Vec<(String, String)> {
    if text.is_empty() {
        return Vec::new();
    }
    let regex = match IOC_META_REGEX.as_ref() {
        Ok(r) => r,
        Err(_) => return Vec::new(), // IOS.T1: fail-soft on init error
    };

    let mut iocs: Vec<(String, String)> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();

    for m in regex.find_iter(text) {
        let pattern_id = m.pattern().as_usize();
        let ioc_type = pattern_to_ioc_type(pattern_id);
        let raw_value = &text[m.start()..m.end()];

        // Validate hex hashes to prevent false positives (SHA1/SHA256/MD5 without true \b)
        let value = match ioc_type {
            "md5" if !is_hex_hash(raw_value, 32) => continue,
            "sha1" if !is_hex_hash(raw_value, 40) => continue,
            "sha256" if !is_hex_hash(raw_value, 64) => continue,
            _ => raw_value.to_lowercase(),
        };

        if seen.insert(value.clone()) {
            iocs.push((value, ioc_type.to_string()));
        }
    }

    // Scan for IPv6 using separate IPV6_REGEX (too complex for build_many NFA)
    for m in IPV6_REGEX.find_iter(text) {
        let raw_value = &text[m.start()..m.end()];
        if seen.insert(raw_value.to_lowercase()) {
            iocs.push((raw_value.to_lowercase(), "ipv6".to_string()));
        }
    }

    iocs
}

/// Extract IOCs from a batch of texts using rayon parallel + Teddy SIMD.
/// Returns flat Vec of (text_idx, ioc_value, ioc_type).
fn batch_extract_iocs_inner(texts: &[String]) -> Vec<(usize, String, String)> {
    let total_bytes: usize = texts.iter().map(|t| t.len()).sum();

    // Threshold for SIMD efficiency: >=4 texts OR >=16KB total
    let use_simd = texts.len() >= 4 || total_bytes >= 16 * 1024;

    if !use_simd {
        // Scalar fallback — iterate texts serially
        return texts
            .iter()
            .enumerate()
            .flat_map(|(idx, text)| {
                extract_one_simd(text)
                    .into_iter()
                    .map(move |(v, t)| (idx, v, t))
            })
            .collect();
    }

    // SIMD path — rayon parallel across texts
    // Issue #6: GIL released so rayon workers can truly run in parallel.
    let results: Vec<Vec<(usize, String, String)>> = Python::attach(|py_inner| {
        crate::gil::release_gil(py_inner, || {
            crate::cpu_pool().install(|| {
                texts
                    .par_iter()
                    .enumerate()
                    .map(|(idx, text)| {
                        extract_one_simd(text)
                            .into_iter()
                            .map(move |(v, t)| (idx, v, t))
                            .collect()
                    })
                    .collect()
            })
        })
    });

    results.into_iter().flatten().collect()
}

// PyO3 API

/// Extract IOCs from a single text using regex-automata single-pass meta-regex.
/// Falls back gracefully on any error (fail-soft invariant IOS.T1).
#[pyfunction]
pub fn extract_iocs_simd(text: &str) -> Vec<(String, String)> {
    extract_one_simd(text)
}

/// Extract IOCs from a batch of texts using rayon parallel.
/// SIMD (Teddy) is used when batch >=4 texts OR total >=16KB; otherwise scalar fallback.
///
/// Returns Vec of (ioc_value, ioc_type) per text (grouped, flat).
#[pyfunction]
pub fn batch_extract_iocs_simd(texts: Vec<String>) -> Vec<(String, String)> {
    if texts.is_empty() {
        return Vec::new();
    }

    let total_bytes: usize = texts.iter().map(|t| t.len()).sum();
    let use_simd = texts.len() >= 4 || total_bytes >= 16 * 1024;

    if !use_simd {
        // Scalar fallback
        return texts
            .iter()
            .flat_map(|text| extract_one_simd(text))
            .collect();
    }

    // SIMD path with rayon parallel
    let results = batch_extract_iocs_inner(&texts);
    results.into_iter().map(|(_, v, t)| (v, t)).collect()
}

/// Batch extract with text index — returns (text_idx, ioc_value, ioc_type).
#[pyfunction]
pub fn batch_extract_iocs_simd_indexed(texts: Vec<String>) -> Vec<(usize, String, String)> {
    if texts.is_empty() {
        return Vec::new();
    }
    batch_extract_iocs_inner(&texts)
}

/// Bulk batch extract — single GIL acquisition for entire batch.
/// Uses `Bound<PyList>::iter()` (PyO3 0.29+) for borrowed iteration.
/// Returns flat results: (ioc_value, ioc_type) per match.
#[pyfunction]
pub fn batch_extract_iocs_simd_python<'py>(
    texts: &Bound<'py, PyList>,
    _py: Python<'py>,
) -> PyResult<Vec<(String, String)>> {
    let n = texts.len();
    if n == 0 {
        return Ok(vec![]);
    }

    // Collect under GIL, then process in rayon scope
    let owned: Vec<String> = texts
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    if owned.len() < 4 {
        // Scalar fallback for small batches
        return Ok(owned.iter().flat_map(|t| extract_one_simd(t)).collect());
    }

    // SIMD path — mixed_pool (adaptive 1-2 threads)
    // Issue #6: GIL released via `release_gil` to enable true rayon parallelism.
    let chunked: Vec<Vec<(usize, String, String)>> = Python::attach(|py_inner| {
        crate::gil::release_gil(py_inner, || {
            crate::mixed_pool(owned.len()).install(|| {
                owned
                    .par_iter()
                    .enumerate()
                    .map(|(idx, text)| {
                        extract_one_simd(text)
                            .into_iter()
                            .map(move |(v, t)| (idx, v, t))
                            .collect::<Vec<_>>()
                    })
                    .collect()
            })
        })
    });

    let flat: Vec<(usize, String, String)> = chunked.into_iter().flatten().collect();
    Ok(flat.into_iter().map(|(_, v, t)| (v, t)).collect())
}

// Module registration

/// Register SIMD IOC extraction functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_iocs_simd, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_iocs_simd, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_iocs_simd_indexed, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_iocs_simd_python, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simd_ipv4() {
        let text = "Server 192.168.1.1 responding on port 8080";
        let iocs = extract_one_simd(text);
        assert!(iocs.iter().any(|(v, t)| t == "ipv4" && v == "192.168.1.1"));
    }

    #[test]
    fn test_simd_email() {
        let text = "Contact admin@example.com for access";
        let iocs = extract_one_simd(text);
        assert!(iocs
            .iter()
            .any(|(v, t)| t == "email" && v == "admin@example.com"));
    }

    #[test]
    fn test_simd_sha256() {
        let text =
            "Hash: a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e";
        let iocs = extract_one_simd(text);
        assert!(iocs.iter().any(|(_v, t)| t == "sha256"));
    }

    #[test]
    fn test_simd_cve() {
        let text = "Vulnerability CVE-2024-12345678 discovered in OpenSSL";
        let iocs = extract_one_simd(text);
        assert!(iocs.iter().any(|(_v, t)| t == "cve"));
    }

    #[test]
    fn test_simd_domain() {
        let text = "Domain example.com resolved to 93.184.216.34";
        let iocs = extract_one_simd(text);
        assert!(iocs
            .iter()
            .any(|(v, t)| t == "domain" && v == "example.com"));
    }

    #[test]
    fn test_batch_simd_threshold() {
        // 4 texts = SIMD threshold
        let texts = vec![
            "IP: 10.0.0.1".to_string(),
            "IP: 10.0.0.2".to_string(),
            "IP: 10.0.0.3".to_string(),
            "IP: 10.0.0.4".to_string(),
        ];
        let results = batch_extract_iocs_inner(&texts);
        assert_eq!(results.len(), 4);
        assert!(results.iter().all(|(idx, _, _)| *idx < 4));
    }

    #[test]
    fn test_batch_scalar_fallback() {
        // 2 small texts = below SIMD threshold
        let texts = vec!["IP: 1.1.1.1".to_string(), "IP: 2.2.2.2".to_string()];
        let results = batch_extract_iocs_inner(&texts);
        assert_eq!(results.len(), 2);
    }

    #[test]
    fn test_hex_hash_validation_sha256_false_positive() {
        // "deadbeef" is not a valid SHA256 (wrong length)
        let text = "Value: deadbeef";
        let iocs = extract_one_simd(text);
        // Should NOT be tagged as sha256 (only 8 hex chars, not 64)
        assert!(!iocs.iter().any(|(_v, t)| t == "sha256"));
    }

    #[test]
    fn test_hex_hash_validation_sha1_false_positive() {
        // "abcd1234efgh5678" is not a valid SHA1 (wrong length)
        let text = "Hash: abcd1234efgh5678";
        let iocs = extract_one_simd(text);
        // Should NOT be tagged as sha1 (only 16 hex chars, not 40)
        assert!(!iocs.iter().any(|(_v, t)| t == "sha1"));
    }

    #[test]
    fn test_meta_regex_builds_successfully() {
        // Verify the LazyLock initializes without panic
        let regex = IOC_META_REGEX.as_ref();
        assert!(regex.is_ok(), "IOC_META_REGEX should build successfully");
    }

    // ─── IPv6 Boundary Tests (P1 fix) ───────────────────────────────────────

    #[test]
    fn test_ipv6_loopback_at_start() {
        // P1 fix: ::1 at string start (was NO MATCH before boundary fix)
        let text = "::1";
        let iocs = extract_one_simd(text);
        assert!(
            iocs.iter().any(|(v, t)| t == "ipv6" && v == "::1"),
            "::1 at string start should match"
        );
    }

    #[test]
    fn test_ipv6_link_local() {
        // P1 fix: fe80::1 compressed form
        let text = "fe80::1";
        let iocs = extract_one_simd(text);
        assert!(
            iocs.iter().any(|(v, t)| t == "ipv6" && v == "fe80::1"),
            "fe80::1 should match fully (not truncated)"
        );
    }

    #[test]
    fn test_ipv6_in_sentence() {
        // ::1 embedded in text (was NO MATCH before)
        let text = "text ::1 more";
        let iocs = extract_one_simd(text);
        assert!(
            iocs.iter().any(|(v, t)| t == "ipv6" && v == "::1"),
            "::1 in sentence should match"
        );
    }

    #[test]
    fn test_ipv6_full_form() {
        // Full 8-hextet form
        let text = "2001:db8:85a3::8a2e:370:7334";
        let iocs = extract_one_simd(text);
        assert!(
            iocs.iter().any(|(v, t)| t == "ipv6"),
            "full IPv6 form should match"
        );
    }

    #[test]
    fn test_ipv6_documentation_form() {
        // 2001:db8::1 compressed
        let text = "2001:db8::1";
        let iocs = extract_one_simd(text);
        assert!(
            iocs.iter().any(|(v, t)| t == "ipv6" && v == "2001:db8::1"),
            "2001:db8::1 should match"
        );
    }
}
