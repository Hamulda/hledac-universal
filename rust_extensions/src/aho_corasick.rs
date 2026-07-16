//! Aho-Corasick multi-pattern matcher implementation.
//!
//! Upgraded from basic finder to support capture-group extraction
//! via Rust regex post-processing. Architecture:
//!
//!   1. Aho-Corasick: O(n) multi-pattern scan across all needles
//!   2. Rust regex:   capture-group extraction on matched substrings
//!
//! This hybrid approach gives Aho-Corasick speed for the N-pattern scan
//! while retaining full regex capture semantics for username extraction.
//!
//! M1 8GB: automaton is built once (module-level or __init__), stored
//! as a PyO3 class field. Memory footprint ~O(patterns × avg_len).

use pyo3::prelude::*;
use aho_corasick::AhoCorasick;
use regex::Regex;

/// Aho-Corasick multi-pattern matcher for fast IOC detection.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import AhoCorasickMatcher
/// matcher = AhoCorasickMatcher(patterns=["malware", "phishing", "suspicious"])
/// results = matcher.scan("Check out this phishing site")
/// # Returns: [(start, end, pattern), ...]
/// ```
#[pyclass]
pub struct AhoCorasickMatcher {
    automaton: AhoCorasick,
    patterns: Vec<String>,
    /// Labels parallel to `patterns` — returned directly in scan results.
    /// Eliminates Python-side _PATTERN_LABEL_INDEX dict lookup in hot path.
    labels: Vec<String>,
    /// Raw regex strings for capture-group extraction.
    /// Parallel to `patterns`; empty string = no capture for that slot.
    capture_patterns_raw: Vec<String>,
}

// ---------------------------------------------------------------------------
// Private module-level helpers — NOT part of PyO3 #[pymethods]
// ---------------------------------------------------------------------------

/// Returns true when the character at `byte_offset - 1` is NOT alphanumeric.
/// Used for "before" boundary check — offset is the match START.
/// This is the inverse of Python's str.isalnum().
#[inline(always)]
fn is_boundary_char(text: &str, byte_offset: usize) -> bool {
    // Guard: offset 0 = start of string = boundary (caller handles before_ok)
    if byte_offset == 0 {
        return false;
    }
    // Get the character immediately before byte_offset
    // next_back() is O(1) for ASCII, O(k) for multi-byte UTF-8 (max 4 bytes).
    // For OSINT text (primarily ASCII), this is ~50-100ns per call.
    text[..byte_offset.min(text.len())]
        .chars()
        .next_back()
        .map_or(false, |c| !c.is_alphanumeric())
}

/// Returns true when the character at `byte_offset` is NOT alphanumeric.
/// Used for "after" boundary check — offset is the match END.
#[inline(always)]
fn is_boundary_char_at(text: &str, byte_offset: usize) -> bool {
    // Guard: offset >= len = end of string = boundary (caller handles after_ok)
    if byte_offset >= text.len() {
        return false;
    }
    // Get the character at byte_offset
    text[byte_offset..]
        .chars()
        .next()
        .map_or(false, |c| !c.is_alphanumeric())
}

#[pymethods]
impl AhoCorasickMatcher {
    /// Create a new AhoCorasickMatcher with the given patterns.
    ///
    /// `capture_patterns` is an optional parallel list of regex patterns
    /// with a single capture group (e.g. `r"github\.com/([^/]+)"`).
    /// When provided, `scan_with_captures` can extract subgroup values.
    #[new]
    #[pyo3(signature = (patterns = vec![], labels = vec![], capture_patterns = vec![]))]
    fn new(patterns: Vec<String>, labels: Vec<String>, capture_patterns: Vec<String>) -> PyResult<Self> {
        let automaton = AhoCorasick::new(&patterns).expect("Failed to build automaton");
        // labels must be same length as patterns; pad with empty string if mismatch
        let labels = if labels.len() == patterns.len() {
            labels
        } else {
            patterns.iter().map(|_| String::new()).collect()
        };
        Ok(Self { automaton, patterns, labels, capture_patterns_raw: capture_patterns })
    }

    /// Scan text and return all pattern matches.
    ///
    /// boundary_policy:
    ///   None or "none" — all matches returned (default)
    ///   "word"         — require word-boundary: prev char NOT alphanumeric AND
    ///                     next char NOT alphanumeric (or at text boundaries)
    ///
    /// Returns list of (start, end, pattern_name, label) tuples.
    /// Label is returned directly from the parallel `labels` list —
    /// eliminates Python-side dict lookup in hot path (Issue #14).
    /// Boundary check is done in Rust — eliminates 2× Python str.isalnum() per hit (Issue #18).
    #[pyo3(signature = (text, boundary_policy=None))]
    fn scan(
        &self,
        text: &str,
        boundary_policy: Option<&str>,
    ) -> Vec<(usize, usize, String, String)> {
        let check_boundary = boundary_policy == Some("word");
        let text_len = text.len();
        let mut results = Vec::new();
        for m in self.automaton.find_iter(text.as_bytes()) {
            let idx = m.pattern().as_usize();
            let start = m.start();
            let end = m.end();

            if check_boundary {
                // before_ok: start==0 OR char before is NOT alphanumeric
                let before_ok = start == 0 || !is_boundary_char(text, start);
                // after_ok: end>=len OR char at end is NOT alphanumeric
                let after_ok = end >= text_len || !is_boundary_char_at(text, end);
                if !(before_ok && after_ok) {
                    continue;
                }
            }

            let pattern_name = self.patterns.get(idx).cloned().unwrap_or_default();
            let label = self.labels.get(idx).cloned().unwrap_or_default();
            results.push((start, end, pattern_name, label));
        }
        results
    }

    /// Scan text and extract capture groups from matched substrings.
    ///
    /// First runs Aho-Corasick to find which pattern matched at each position,
    /// then applies the corresponding capture regex to the matched substring.
    /// Returns (start, end, pattern_name, label, captured_value) tuples.
    /// Label is returned directly — eliminates Python-side dict lookup (Issue #14).
    /// If no capture pattern exists for a match, captured_value is empty string.
    ///
    /// Note: capture regexes are compiled once per call (not per match).
    /// For 17 patterns with 10 matches each this saves ~153 regex compilations.
    fn scan_with_captures(&self, text: &str) -> Vec<(usize, usize, String, String, String)> {
        // Pre-compile all capture regexes once — avoids O(matches × patterns) Regex::new() calls.
        let compiled: Vec<Option<Regex>> = self
            .capture_patterns_raw
            .iter()
            .map(|raw| {
                if raw.is_empty() {
                    None
                } else {
                    Regex::new(raw).ok()
                }
            })
            .collect();

        let mut results = Vec::new();
        for m in self.automaton.find_iter(text.as_bytes()) {
            let idx = m.pattern().as_usize();
            let start = m.start();
            let end = m.end();
            let matched_text = &text[start..end];
            let capture_val = if let Some(Some(re)) = compiled.get(idx) {
                re.captures(matched_text)
                    .and_then(|c| c.get(1))
                    .map_or(String::new(), |g| g.as_str().to_string())
            } else {
                String::new()
            };
            let pattern_name = self.patterns.get(idx).cloned().unwrap_or_default();
            let label = self.labels.get(idx).cloned().unwrap_or_default();
            results.push((start, end, pattern_name, label, capture_val));
        }
        results
    }

    /// Batch scan: process multiple texts in parallel via rayon.
    ///
    /// boundary_policy: same as `scan` — None/"none" for all matches, "word" for boundary check.
    /// Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.
    /// Issue #6: GIL released via `Python::attach` + `release_gil` to enable true rayon parallelism.
    /// Issue #18: boundary check done in Rust — eliminates 2× Python str.isalnum() per hit per text.
    /// Returns Vec of Vec of (start, end, pattern_name, label) — label inline (Issue #14).
    #[pyo3(signature = (texts, boundary_policy=None))]
    fn scan_batch(
        &self,
        texts: Vec<String>,
        boundary_policy: Option<&str>,
    ) -> Vec<Vec<(usize, usize, String, String)>> {
        use crate::gil::release_gil;
        let n = texts.len();
        let pool = crate::mixed_pool(n);
        let check_boundary = boundary_policy == Some("word");
        Python::with_gil(|py| {
            release_gil(py, || {
                pool.install(|| {
                    texts
                        .into_iter()
                        .map(|text| {
                            let t_len = text.len();
                            let mut results = Vec::new();
                            for m in self.automaton.find_iter(text.as_bytes()) {
                                let idx = m.pattern().as_usize();
                                let start = m.start();
                                let end = m.end();
                                if check_boundary {
                                    let before_ok =
                                        start == 0 || !is_boundary_char(&text, start);
                                    let after_ok =
                                        end >= t_len || !is_boundary_char_at(&text, end);
                                    if !(before_ok && after_ok) {
                                        continue;
                                    }
                                }
                                let pattern_name = self.patterns.get(idx).cloned().unwrap_or_default();
                                let label = self.labels.get(idx).cloned().unwrap_or_default();
                                results.push((start, end, pattern_name, label));
                            }
                            results
                        })
                        .collect()
                })
            })
        })
    }

    /// Get the number of patterns.
    fn len(&self) -> usize {
        self.patterns.len()
    }

    /// Check if no patterns are loaded.
    fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }

    /// Fast path: return True if any pattern matches, False otherwise.
    /// Optimized for short-circuit evaluation on large texts.
    fn find_any(&self, text: &str) -> bool {
        self.automaton.is_match(text.as_bytes())
    }

    /// Explicitly release held resources.
    ///
    /// Drops the automaton and pattern vectors, freeing memory immediately
    /// instead of waiting for Python GC. Safe to call multiple times —
    /// subsequent calls are no-ops (fields are taken by std::mem::take).
    fn close(&mut self) {
        use std::mem;
        // std::mem::take replaces the value with its Default (empty vec / None automaton)
        // This drops the old values immediately rather than waiting for struct drop.
        // Type annotation (&[] as &[String]) resolves E0283: type annotations needed.
        self.automaton = AhoCorasick::new(&[] as &[String]).unwrap();
        self.patterns = mem::take(&mut self.patterns);
        self.labels = mem::take(&mut self.labels);
        self.capture_patterns_raw = mem::take(&mut self.capture_patterns_raw);
    }
}
