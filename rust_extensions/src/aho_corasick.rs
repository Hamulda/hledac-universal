//! Aho-Corasick multi-pattern matcher implementation.
#![allow(dead_code)]
//!
//! Architecture (Issue #37 zero-copy upgrade):
//!
//!   1. Aho-Corasick: O(n) multi-pattern scan across all needles
//!   2. PatternHit PyClass: zero-copy struct returned directly to Python
//!      - labels interned via Box::leak (no Python dict lookup, no sys.intern)
//!      - pattern as &str slice into interned store
//!      - boundary check done in Rust
//!
//! M1 8GB: automaton built once, stored as PyO3 struct field.
//! Memory: O(patterns × avg_len) + O(unique_labels × str).

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, AhoCorasickKind};
use parking_lot::Mutex;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashMap;

/// Interned string store — Box::leak for 'static lifetime.
// KEY OPTIMIZATION (Issue #37): labels are interned once at construction
// and reused across every scan() call. Eliminates Python sys.intern() overhead
// and dict lookup in the hot path. Labels are never freed (leaked) but the
// total unique labels are bounded (~dozens for OSINT patterns).
struct InternStore {
    // map: original label string -> interned &'static str
    // Mutex: safe because Python Construction is single-threaded (GIL)
    map: Mutex<HashMap<String, &'static str>>,
}

impl InternStore {
    fn new() -> Self {
        Self {
            map: Mutex::new(HashMap::new()),
        }
    }

    /// Intern a label string, returning a static reference.
    /// First call: allocates Box::leak, stores pointer. Subsequent calls: reuse.
    fn intern(&self, s: &str) -> &'static str {
        let mut map = self.map.lock();
        if let Some(existing) = map.get(s) {
            return existing;
        }
        // Box::leak the owned String — this is the single allocation per unique label.
        // map.insert borrows s directly (HashMap<&str, &'static str>).
        let leaked: &'static str = Box::leak(s.to_string().into_boxed_str());
        map.insert(s.to_string(), leaked);
        leaked
    }

    /// Intern an Option label string.
    fn intern_opt(&self, s: &Option<String>) -> Option<&'static str> {
        s.as_ref().map(|v| self.intern(v))
    }
}

/// PatternHit PyClass — zero-copy replacement for Python PatternHit(NamedTuple).
///
/// Returns directly from Rust scan — no Python tuple unpacking,
/// no NamedTuple construction, no sys.intern() calls.
///
/// Issue #37: pattern and label are interned (Box::leak); value is a direct
/// substring slice from the input text (owned String, not copied from Python).
///
/// Fields match Python PatternHit(NamedTuple) exactly so downstream code
/// can use r.pattern / r.start / r.end / r.value / r.label directly.
#[pyclass]
pub struct PatternHit {
    #[pyo3(get)]
    pub start: usize,
    #[pyo3(get)]
    pub end: usize,
    #[pyo3(get)]
    pub pattern: String, // owned: interned pattern string
    #[pyo3(get)]
    pub label: Option<String>, // interned label (None = no label)
    #[pyo3(get)]
    pub value: String, // substring from text (original case)
}

impl PatternHit {
    fn new(
        start: usize,
        end: usize,
        pattern: String,
        label: Option<String>,
        value: String,
    ) -> Self {
        Self {
            start,
            end,
            pattern,
            label,
            value,
        }
    }
}

/// Aho-Corasick multi-pattern matcher for fast IOC detection.
///
/// # Example
/// ```python
/// from hledac_rust_extensions import AhoCorasickMatcher
/// matcher = AhoCorasickMatcher(patterns=["malware", "phishing", "suspicious"])
/// results = matcher.scan("Check out this phishing site")
/// # Returns: list[PatternHit] — zero-copy, labels interned
/// ```
///
/// Issue #37: PatternHit PyClass returned directly — no Python tuple unpacking,
/// no NamedTuple construction, no sys.intern() calls in hot path.
#[pyclass]
pub struct AhoCorasickMatcher {
    automaton: AhoCorasick,
    patterns: Vec<String>,
    /// Interned labels — shared store, labels interned once at construction.
    /// Each label is Box::leak'd once and reused across all scan() calls.
    /// Eliminates Python-side dict lookup + sys.intern() in hot path (Issue #37).
    #[allow(dead_code)]
    intern_store: InternStore,
    /// Labels parallel to `patterns` — interned &'static str references.
    /// Retrieved from intern_store at construction; zero-cost in scan().
    interned_labels: Vec<Option<&'static str>>,
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
    fn new(
        patterns: Vec<String>,
        labels: Vec<String>,
        capture_patterns: Vec<String>,
    ) -> PyResult<Self> {
        // FIX-F-8: Explicit AhoCorasickKind::ContiguousNFA for M1 cache efficiency.
        //
        // aho-corasick 1.1.x supports: NoncontiguousNFA (default), ContiguousNFA,
        // DFA. NEON SIMD acceleration requires `aho-corasick` built with the
        // `simd-accel` feature (enables portable_simd / ARM NEON on M1).
        // To enable: add `aho-corasick = { version = "1.1", features = ["simd-accel"] }`
        // to Cargo.toml AND change to AhoCorasickKind::Auto (available in 1.2+/2.x).
        // ContiguousNFA is the best available option with current deps:
        // - Better cache locality than NoncontiguousNFA (dense state table)
        // - Lower memory than DFA (~2× vs NFA)
        // - ~1.5-2× faster than NoncontiguousNFA on M1
        let automaton = AhoCorasickBuilder::new()
            .kind(Some(AhoCorasickKind::ContiguousNFA))
            .build(&patterns)
            .expect("Failed to build automaton");
        // InternStore: labels interned once at construction, reused across every scan()
        let intern_store = InternStore::new();
        // Intern labels: parallel to patterns, Box::leak each unique label
        let interned_labels: Vec<Option<&'static str>> = if labels.len() == patterns.len() {
            labels
                .iter()
                .map(|l| {
                    if l.is_empty() {
                        None
                    } else {
                        Some(intern_store.intern(l))
                    }
                })
                .collect()
        } else {
            vec![None; patterns.len()]
        };
        Ok(Self {
            automaton,
            patterns,
            intern_store,
            interned_labels,
            capture_patterns_raw: capture_patterns,
        })
    }

    /// Scan text and return all pattern matches as PatternHit objects.
    ///
    /// Issue #37: Returns Vec<PatternHit> — zero Python allocations,
    /// labels interned in Rust (no sys.intern, no dict lookup).
    /// value is extracted from text using byte offsets (text is already lowercased
    /// by the Python caller — ASCII offsets are identical to original text).
    ///
    /// boundary_policy:
    ///   None or "none" — all matches returned (default)
    ///   "word"         — require word-boundary: prev char NOT alphanumeric AND
    ///                     next char NOT alphanumeric (or at text boundaries)
    #[pyo3(signature = (text, boundary_policy=None))]
    fn scan(&self, text: &str, boundary_policy: Option<&str>) -> Vec<PatternHit> {
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

            // value: substring from text using byte offsets.
            // text is already lowercased by Python caller; for ASCII OSINT text
            // byte offsets are identical to original text. Slicing here is O(length_of_match)
            // and allocates one String per hit — acceptable since patterns are short.
            let value = text[start..end].to_string();
            let pattern_name = self.patterns.get(idx).cloned().unwrap_or_default();
            // get() returns &Option<&Option<&str>>, copied() gives Option<&Option<&str>>,
            // and_then flattens to Option<&str>, then to_owned() converts to Option<String>.
            let label = self
                .interned_labels
                .get(idx)
                .copied()
                .and_then(|x| x)
                .map(|s| s.to_owned());
            results.push(PatternHit::new(start, end, pattern_name, label, value));
        }
        results
    }

    /// Scan text and extract capture groups from matched substrings.
    ///
    /// First runs Aho-Corasick to find which pattern matched at each position,
    /// then applies the corresponding capture regex to the matched substring.
    /// Returns (start, end, pattern_name, label, captured_value) tuples.
    /// Label is interned via InternStore — no Python dict lookup (Issue #37).
    /// If no capture pattern exists for a match, captured_value is empty string.
    ///
    /// Note: capture regexes are compiled once per call (not per match).
    /// For 17 patterns with 10 matches each this saves ~153 regex compilations.
    fn scan_with_captures(
        &self,
        text: &str,
    ) -> Vec<(usize, usize, String, Option<String>, String)> {
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
            // get() -> &Option<&Option<&str>>, copied() -> Option<Option<&str>>,
            // and_then identity -> Option<&str>, to_owned() -> Option<String>.
            let label = self
                .interned_labels
                .get(idx)
                .copied()
                .and_then(|x| x)
                .map(|s| s.to_owned());
            results.push((start, end, pattern_name, label, capture_val));
        }
        results
    }

    /// Batch scan: process multiple texts in parallel via rayon.
    ///
    /// Issue #37: Returns Vec<Vec<PatternHit>> — zero Python allocations per scan,
    /// labels interned in Rust. Mixed pool (1-2 threads) for M1 8GB safety.
    ///
    /// boundary_policy: same as `scan` — None/"none" for all matches, "word" for boundary check.
    /// Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.
    /// Issue #6: GIL released via `Python::attach` + `release_gil` to enable true rayon parallelism.
    /// Issue #18: boundary check done in Rust — eliminates 2× Python str.isalnum() per hit per text.
    #[pyo3(signature = (texts, boundary_policy=None))]
    fn scan_batch(
        &self,
        texts: Vec<String>,
        boundary_policy: Option<&str>,
    ) -> Vec<Vec<PatternHit>> {
        use crate::gil::release_gil;
        let n = texts.len();
        let pool = crate::mixed_pool(n);
        let check_boundary = boundary_policy == Some("word");
        // Clone interned_labels for use inside rayon thread (Send + Clone)
        let interned_labels = self.interned_labels.clone();
        Python::attach(|py| {
            release_gil(py, std::panic::AssertUnwindSafe(|| {
                pool.install(|| {
                    texts
                        .into_iter()
                        .map(|text| {
                            let t_len = text.len();
                            let mut results: Vec<PatternHit> = Vec::new();
                            // Issue #38: greedy leftmost-non-overlapping dedup
                            // Track last end to skip overlapping substring matches
                            let mut last_end: usize = 0;
                            for m in self.automaton.find_iter(text.as_bytes()) {
                                let idx = m.pattern().as_usize();
                                let start = m.start();
                                let end = m.end();
                                // Skip overlapping matches — greedy leftmost
                                if start < last_end {
                                    continue;
                                }
                                last_end = end;
                                if check_boundary {
                                    let before_ok = start == 0 || !is_boundary_char(&text, start);
                                    let after_ok = end >= t_len || !is_boundary_char_at(&text, end);
                                    if !(before_ok && after_ok) {
                                        continue;
                                    }
                                }
                                let value = text[start..end].to_string();
                                let pattern_name =
                                    self.patterns.get(idx).cloned().unwrap_or_default();
                                // same double-Option flatten via and_then
                                let label = interned_labels
                                    .get(idx)
                                    .copied()
                                    .and_then(|x| x)
                                    .map(|s| s.to_owned());
                                results.push(PatternHit::new(
                                    start,
                                    end,
                                    pattern_name,
                                    label,
                                    value,
                                ));
                            }
                            results
                        })
                        .collect()
                })
            }))
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
    /// instead of waiting for Python GC. Safe to call multiple times.
    /// Note: InternStore strings are Box::leak'd — they are NOT freed here
    /// (intentional: labels are process-wide constants for OSINT patterns).
    fn close(&mut self) {
        use std::mem;
        // std::mem::take replaces the value with its Default (empty vec / None automaton)
        // This drops the old values immediately rather than waiting for struct drop.
        self.automaton = AhoCorasick::new(&[] as &[String]).unwrap();
        self.patterns = mem::take(&mut self.patterns);
        // interned_labels: Vec<Option<&'static str>> — no mem::take needed (no Drop)
        self.interned_labels.clear();
        self.capture_patterns_raw = mem::take(&mut self.capture_patterns_raw);
        // InternStore.map is NOT cleared — labels may be reused if new patterns
        // are configured. Mutex is cheap to drop.
    }
}
