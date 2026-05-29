//! Aho-Corasick multi-pattern matcher implementation.

use pyo3::prelude::*;
use aho_corasick::AhoCorasick;

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
}

#[pymethods]
impl AhoCorasickMatcher {
    /// Create a new AhoCorasickMatcher with the given patterns.
    #[new]
    #[pyo3(signature = (patterns = vec![]))]
    fn new(patterns: Vec<String>) -> Self {
        let automaton = AhoCorasick::new(&patterns).expect("Failed to build automaton");
        Self { automaton, patterns }
    }

    /// Scan text and return all pattern matches.
    ///
    /// Returns list of (start, end, pattern_name) tuples.
    fn scan(&self, text: &str) -> Vec<(usize, usize, String)> {
        let mut results = Vec::new();
        for m in self.automaton.find_iter(text.as_bytes()) {
            if let Some(pattern) = self.patterns.get(m.pattern().as_usize()) {
                let start = m.start();
                let end = m.end();
                results.push((start, end, pattern.clone()));
            }
        }
        results
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
}