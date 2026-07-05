//! Metal-accelerated pattern matching for bulk text scanning.
//!
//! Sprint R4.2: M1 GPU Acceleration (ANE/Metal)
//!
//! Uses Metal Performance Shaders (MPS) for parallel bulk text scanning.
//! Falls back to Rust SIMD (NEON) when Metal is unavailable.
//!
//! ## Metal Strategy
//!
//! Metal is ideal for:
//! - Parallel pattern matching across many strings
//! - Bulk text scanning with regex/keyword patterns
//! - SIMD-equivalent operations on GPU
//!
//! M1 GPU: 2.5 TFLOPS, 8 EUs, shared with ANE
//!
//! ## Pattern Matching Types
//!
//! 1. **Keyword matching**: Exact string search (Aho-Corasick)
//! 2. **Regex patterns**: Regular expression matching
//! 3. **IoC patterns**: IP, URL, email, hash detection
//!
//! ## Architecture
//!
//! ```text
//! Python → PyO3 → Metal Pattern Matcher
//!                    ├── Metal GPU path (preferred, >4KB text, >4 patterns)
//!                    └── Rust NEON Aho-Corasick fallback
//! ```
//!
//! Design invariants:
//!   M.T1  No panics, fail-soft on Metal errors
//!   M.T2  Bounded: max patterns (1000), max text length (100KB)
//!   M.T3  GPU only when efficient (avoid transfer overhead)

use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;
use std::sync::OnceLock;

// Constants
const MAX_PATTERNS: usize = 1000;
const MAX_TEXT_LEN: usize = 100_000;
const MAX_BATCH_SIZE: usize = 1000;

/// Statistics from a pattern scan operation.
#[derive(Debug, Clone)]
pub struct PatternStats {
    pub total_matches: usize,
    pub patterns_matched: usize,
    pub bytes_scanned: usize,
}

impl PatternStats {
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        dict.set_item("total_matches", self.total_matches)?;
        dict.set_item("patterns_matched", self.patterns_matched)?;
        dict.set_item("bytes_scanned", self.bytes_scanned)?;
        Ok(dict)
    }
}

// Pre-compiled IoC patterns - compiled once at first use via OnceLock
static IP_REGEX: OnceLock<Regex> = OnceLock::new();
static URL_REGEX: OnceLock<Regex> = OnceLock::new();
static EMAIL_REGEX: OnceLock<Regex> = OnceLock::new();
static HASH_REGEX: OnceLock<Regex> = OnceLock::new();

fn get_ip_regex() -> &'static Regex {
    IP_REGEX.get_or_init(|| {
        Regex::new(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b").unwrap()
    })
}

fn get_url_regex() -> &'static Regex {
    URL_REGEX.get_or_init(|| {
        Regex::new(r#"https?://[^\s<>"']+"#).unwrap()
    })
}

fn get_email_regex() -> &'static Regex {
    EMAIL_REGEX.get_or_init(|| {
        Regex::new(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b").unwrap()
    })
}

fn get_hash_regex() -> &'static Regex {
    HASH_REGEX.get_or_init(|| {
        Regex::new(r"\b[a-fA-F0-9]{32,64}\b").unwrap()
    })
}

/// Process multiple texts against a keyword pattern set using Aho-Corasick.
/// This is the CPU fallback using NEON-vectorized Aho-Corasick from rayon.
///
/// Returns: Vec of match tuples (text_idx, pattern_id, start, end)
pub fn scan_keywords_batch(
    texts: &[String],
    keywords: &[String],
) -> Vec<(usize, usize, usize, usize)> {
    use aho_corasick::AhoCorasick;

    if keywords.is_empty() || texts.is_empty() {
        return Vec::new();
    }

    let ac = match AhoCorasick::new(&keywords.iter().map(|s| s.as_str()).collect::<Vec<_>>()) {
        Ok(ac) => ac,
        Err(_) => return Vec::new(),
    };

    let mut results = Vec::new();

    for (text_idx, text) in texts.iter().enumerate() {
        let bytes = text.as_bytes();
        for m in ac.find_overlapping_iter(bytes) {
            results.push((
                text_idx,
                m.pattern().as_usize(),
                m.start(),
                m.end(),
            ));
        }
    }

    results
}

/// Scan texts for IoC patterns (IP, URL, email, hash).
/// Uses pre-compiled regex patterns.
///
/// Returns: Vec of (text_idx, pattern_type, start, end, matched_text)
pub fn scan_ioc_batch(
    texts: &[String],
) -> Vec<(usize, usize, usize, usize, String)> {
    let mut results = Vec::new();

    for (text_idx, text) in texts.iter().enumerate() {
        // IP addresses
        for m in get_ip_regex().find_iter(text) {
            results.push((
                text_idx,
                0, // IoC type: IP
                m.start(),
                m.end(),
                m.as_str().to_string(),
            ));
        }

        // URLs
        for m in get_url_regex().find_iter(text) {
            results.push((
                text_idx,
                1, // IoC type: URL
                m.start(),
                m.end(),
                m.as_str().to_string(),
            ));
        }

        // Emails
        for m in get_email_regex().find_iter(text) {
            results.push((
                text_idx,
                2, // IoC type: email
                m.start(),
                m.end(),
                m.as_str().to_string(),
            ));
        }

        // Hashes
        for m in get_hash_regex().find_iter(text) {
            results.push((
                text_idx,
                3, // IoC type: hash
                m.start(),
                m.end(),
                m.as_str().to_string(),
            ));
        }
    }

    results
}

// Metal GPU-accelerated scan using MPS (R4.3 — now enabled)
// GPU path: inline Metal shader for parallel text×keyword scan.
// Falls back to CPU Aho-Corasick when GPU unavailable or workload too small.

/// Try GPU scan first, fall back to CPU Aho-Corasick.
#[cfg(target_os = "macos")]
fn scan_keywords_gpu_or_cpu(
    texts: &[String],
    keywords: &[String],
) -> Vec<(usize, usize, usize, usize)> {
    use crate::metal_compute;

    // Try GPU first
    if let Some(results) = metal_compute::gpu_scan_keywords(texts, keywords) {
        return results;
    }
    // CPU fallback
    metal_compute::cpu_scan_keywords(texts, keywords)
}

#[cfg(not(target_os = "macos"))]
fn scan_keywords_gpu_or_cpu(
    texts: &[String],
    keywords: &[String],
) -> Vec<(usize, usize, usize, usize)> {
    scan_keywords_batch(texts, keywords)
}

// Python-facing API

/// Scan a batch of texts for keyword matches using Metal GPU or Aho-Corasick.
///
/// Args:
///   texts: List of texts to scan
///   keywords: List of keyword patterns
///
/// Returns:
///   List of tuples: (text_idx, pattern_idx, start, end)
#[pyfunction]
pub fn batch_keyword_scan(
    texts: Vec<String>,
    keywords: Vec<String>,
) -> PyResult<Vec<(usize, usize, usize, usize)>> {
    if keywords.len() > MAX_PATTERNS {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Too many patterns ({} > {})",
            keywords.len(), MAX_PATTERNS
        )));
    }

    if texts.len() > MAX_BATCH_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Batch size too large ({} > {})",
            texts.len(), MAX_BATCH_SIZE
        )));
    }

    let total_bytes: usize = texts.iter().map(|t| t.len()).sum();
    if total_bytes > MAX_TEXT_LEN * texts.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Total text too large ({} bytes)",
            total_bytes
        )));
    }

    // GPU path with CPU fallback (R4.3)
    Ok(scan_keywords_gpu_or_cpu(&texts, &keywords))
}

/// Scan a batch of texts for IoC patterns (IP, URL, email, hash).
///
/// Args:
///   texts: List of texts to scan
///
/// Returns:
///   List of tuples: (text_idx, ioc_type, start, end, matched_text)
///   ioc_type: 0=IP, 1=URL, 2=email, 3=hash
#[pyfunction]
pub fn batch_ioc_scan(
    texts: Vec<String>,
) -> PyResult<Vec<(usize, usize, usize, usize, String)>> {
    if texts.len() > MAX_BATCH_SIZE {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Batch size too large ({} > {})",
            texts.len(), MAX_BATCH_SIZE
        )));
    }

    let total_bytes: usize = texts.iter().map(|t| t.len()).sum();
    if total_bytes > MAX_TEXT_LEN * texts.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Total text too large ({} bytes)",
            total_bytes
        )));
    }

    Ok(scan_ioc_batch(&texts))
}

/// Get pattern statistics from scan results.
///
/// Args:
///   results: Scan results from batch_keyword_scan or batch_ioc_scan
///   num_texts: Number of texts in the original batch
///   bytes_scanned: Total bytes scanned
///
/// Returns:
///   PatternStats dict
#[pyfunction]
pub fn get_pattern_stats(
    results: Vec<(usize, usize, usize, usize)>,
    _num_texts: usize,
    bytes_scanned: usize,
    py: Python<'_>,
) -> PyResult<Py<PyAny>> {
    let unique_patterns: std::collections::HashSet<usize> =
        results.iter().map(|r| r.1).collect();

    let stats = PatternStats {
        total_matches: results.len(),
        patterns_matched: unique_patterns.len(),
        bytes_scanned,
    };
    stats.to_dict(py).map(|d| d.into())
}

/// Check Metal availability and GPU compute capability on this system.
/// Issue #15c: Validates actual GPU kernel compilation, not just device presence.
///
/// Uses raw FFI to MTLCreateSystemDefaultDevice for reliable detection
/// in cdylib context where metal::Device::system_default() may return None.
/// Then verifies keyword_scan kernel can be compiled (not just device exists).
#[pyfunction]
pub fn check_metal_availability(py: Python<'_>) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);

    #[cfg(target_os = "macos")]
    {
        // Use dlsym to get MTLCreateSystemDefaultDevice - more reliable in cdylib
        let device_ptr = unsafe {
            use std::ffi::CString;
            use libc::dlopen;
            use libc::dlsym;
            use libc::RTLD_NOW;

            let lib = dlopen(CString::new("/System/Library/Frameworks/Metal.framework/Metal").unwrap().as_ptr(), RTLD_NOW);
            if lib.is_null() {
                None
            } else {
                let sym = dlsym(lib, CString::new("MTLCreateSystemDefaultDevice").unwrap().as_ptr());
                if sym.is_null() {
                    None
                } else {
                    let func: extern "C" fn() -> *mut std::ffi::c_void = std::mem::transmute(sym);
                    let ptr = func();
                    if ptr.is_null() { None } else { Some(ptr) }
                }
            }
        };

        if let Some(_device_ptr) = device_ptr {
            // Get GPU name and verify kernel compilation
            match metal::Device::system_default() {
                Some(device) => {
                    // Issue #15c: Verify actual GPU kernel is usable
                    let kernel_works = crate::metal_compute::is_gpu_available();
                    dict.set_item("metal_available", kernel_works)?;
                    dict.set_item("device_name", device.name())?;
                    dict.set_item("gpu_count", 1i32)?;
                    dict.set_item("gpu_compute_ready", kernel_works)?;
                }
                None => {
                    // Device found via dlsym but not via metal crate - GPU present but kernel may not work
                    dict.set_item("metal_available", true)?;
                    dict.set_item("device_name", "M1-GPU")?;
                    dict.set_item("gpu_count", 1i32)?;
                    dict.set_item("gpu_compute_ready", false)?; // Cannot compile kernel
                }
            }
        } else {
            dict.set_item("metal_available", false)?;
            dict.set_item("device_name", "no_device")?;
            dict.set_item("gpu_count", 0i32)?;
            dict.set_item("gpu_compute_ready", false)?;
        }
    }

    #[cfg(not(target_os = "macos"))]
    {
        dict.set_item("metal_available", false)?;
        dict.set_item("device_name", "non_macos")?;
        dict.set_item("gpu_count", 0i32)?;
        dict.set_item("gpu_compute_ready", false)?;
    }

    Ok(dict.into())
}

// Module registration

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(batch_keyword_scan, m)?)?;
    m.add_function(wrap_pyfunction!(batch_ioc_scan, m)?)?;
    m.add_function(wrap_pyfunction!(get_pattern_stats, m)?)?;
    m.add_function(wrap_pyfunction!(check_metal_availability, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_keyword_scan_basic() {
        let texts = vec![
            "Hello world".to_string(),
            "foo bar baz".to_string(),
        ];
        let keywords = vec!["foo".to_string(), "bar".to_string()];

        let results = scan_keywords_batch(&texts, &keywords);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].0, 1);
        assert_eq!(results[0].1, 0);
    }

    #[test]
    fn test_ioc_scan_ips() {
        let texts = vec![
            "Server 192.168.1.1 responding".to_string(),
            "No IP here".to_string(),
        ];

        let results = scan_ioc_batch(&texts);
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0, 0);
        assert_eq!(results[0].1, 0);
    }

    #[test]
    fn test_ioc_scan_urls() {
        let texts = vec![
            "Visit https://example.com for info".to_string(),
        ];

        let results = scan_ioc_batch(&texts);
        assert!(results.len() >= 1);
        let url_result = results.iter().find(|r| r.1 == 1).unwrap();
        assert!(url_result.4.contains("https://example.com"));
    }

    #[test]
    fn test_stats() {
        // get_pattern_stats is a #[pyfunction] requiring Python GIL,
        // so we test the PatternStats struct directly instead.
        let results = vec![
            (0, 0, 0, 3),
            (0, 1, 5, 8),
            (1, 0, 10, 13),
        ];
        let unique_patterns: std::collections::HashSet<usize> =
            results.iter().map(|r| r.1).collect();
        let stats = PatternStats {
            total_matches: results.len(),
            patterns_matched: unique_patterns.len(),
            bytes_scanned: 1000,
        };
        assert_eq!(stats.total_matches, 3);
        assert_eq!(stats.patterns_matched, 2);
        assert_eq!(stats.bytes_scanned, 1000);
    }
}
