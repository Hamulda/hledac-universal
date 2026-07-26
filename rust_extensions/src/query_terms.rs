//! Query-context multi-pattern scanner — Issue B4.
//!
//! Replaces 4× Python `str.find` loops in `_scan_query_context_terms`
//! with a single O(n) Aho-Corasick scan.
//!
//! Input: text + separated term lists (domains / ipv4s / ipv6s / terms)
//! Output: sorted list of hits with (start, end, pattern, label, value)
//!
//! Architecture:
//! - One Aho-Corasick automaton built from all terms at once
//! - Single linear scan of text → all hits in one pass
//! - Sorted by position, overlapping hits filtered (keep first)
//!
//! M1 8GB: automaton built once per sprint, shared across entries.

use pyo3::prelude::*;
use aho_corasick::AhoCorasick;

/// Scan text for query-context terms using Aho-Corasick.
///
/// Replaces Python's 4× `str.find` loops with a single O(n) scan.
///
/// Args:
///   text: text to scan
///   domains: domain patterns
///   ipv4s: IPv4 address patterns
///   ipv6s: IPv6 address patterns
///   terms: word-based term patterns (max 20 in Python)
///
/// Returns:
///   List of (start, end, pattern, label, value) tuples sorted by start.
///   Empty list if no hits.
///
/// Python equivalent (4 loops → 1 scan):
///   text_lower = text.lower()
///   for dom in domains:
///       pos = text_lower.find(dom.lower())
///       while pos != -1: ...
#[pyfunction]
pub fn scan_query_context(
    text: &str,
    domains: Vec<String>,
    ipv4s: Vec<String>,
    ipv6s: Vec<String>,
    terms: Vec<String>,
) -> Vec<(usize, usize, String, String, String)> {
    // B4-ISSUE-1 FIX: compute total capacity first, then build inline metadata
    // (avoid lifetime issues with &str borrows from owned Strings)
    let term_limit = terms.len().min(20);
    let total = domains.len() + ipv4s.len() + ipv6s.len() + term_limit;
    if total == 0 {
        return Vec::new();
    }

    // Build combined pattern list + inline metadata (no lifetime issues)
    let mut pattern_prefixes: Vec<&'static str> = Vec::with_capacity(total);
    let mut pattern_labels: Vec<&'static str> = Vec::with_capacity(total);
    let mut patterns: Vec<String> = Vec::with_capacity(total);

    for dom in &domains {
        patterns.push(dom.clone());
        pattern_prefixes.push("query_domain:");
        pattern_labels.push("query_context_domain");
    }
    for ip in &ipv4s {
        patterns.push(ip.clone());
        pattern_prefixes.push("query_ipv4:");
        pattern_labels.push("query_context_ipv4");
    }
    for ip in &ipv6s {
        patterns.push(ip.clone());
        pattern_prefixes.push("query_ipv6:");
        pattern_labels.push("query_context_ipv6");
    }
    for term in terms.iter().take(20) {
        patterns.push(term.clone());
        pattern_prefixes.push("query_term:");
        pattern_labels.push("query_context_term");
    }

    if patterns.is_empty() {
        return Vec::new();
    }

    let automaton = match AhoCorasick::new(&patterns) {
        Ok(ac) => ac,
        Err(_) => return Vec::new(),
    };

    let text_lower = text.to_lowercase();

    // Collect all hits
    let mut hits: Vec<(usize, usize, String, String, String)> = Vec::new();

    for m in automaton.find_iter(&text_lower) {
        let idx = m.pattern().as_usize();
        if idx >= pattern_labels.len() {
            continue;
        }
        let prefix = pattern_prefixes[idx];
        let label = pattern_labels[idx];
        let start = m.start();
        let end = m.end();
        // Value: original-cased substring from source text
        let value = text[start..end].to_string();
        let pattern = format!("{}{}", prefix, patterns[idx]);
        hits.push((start, end, pattern, label.to_string(), value));
    }

    // Sort by start position
    hits.sort_unstable_by_key(|h| h.0);

    hits
}

/// Extract payload context with whitespace trimming.
///
/// Replaces 4× Python `str.find/rfind` with two Rust scans.
///
/// Args:
///   text: source text
///   hit_start: hit start byte offset
///   hit_end: hit end byte offset
///   radius: chars before/after hit (FEED_PAYLOAD_CONTEXT_CHARS=200)
///
/// Returns:
///   Context string with whitespace trimming + ellipsis.
#[pyfunction]
pub fn extract_payload_context(
    text: &str,
    hit_start: usize,
    hit_end: usize,
    radius: usize,
) -> String {
    let text_len = text.len();

    // Expand window
    let start = hit_start.saturating_sub(radius);
    let end = hit_end.saturating_add(radius).min(text_len);

    if start >= end {
        return String::new();
    }

    let mut ctx = &text[start..end];

    // Trim left at whitespace (newline or space) before hit_start
    if start > 0 {
        // Find last newline or space BEFORE hit_start in ctx
        // ctx[start..] means ctx[0] corresponds to text[start]
        // We need whitespace in ctx[..(hit_start - start)]
        let pre_len = hit_start - start;
        let pre = &ctx[..pre_len];
        let last_nl = pre.rfind('\n').unwrap_or(0);
        let last_sp = pre.rfind(' ').unwrap_or(0);
        let last_ws = last_nl.max(last_sp);
        if last_ws > 0 {
            ctx = &ctx[last_ws + 1..];
        }
    }

    // Trim right at whitespace (newline or space) after hit_end
    // ctx offset for hit_end
    let ctx_offset = if start > 0 {
        hit_end - start
    } else {
        hit_end
    };
    if ctx_offset < ctx.len() {
        let post = &ctx[ctx_offset..];
        let first_nl = post.find('\n').unwrap_or(usize::MAX);
        let first_sp = post.find(' ').unwrap_or(usize::MAX);
        let first_ws = first_nl.min(first_sp);
        if first_ws != usize::MAX && first_ws > 0 {
            ctx = &ctx[..ctx_offset + first_ws];
        }
    }

    let ctx = ctx.trim();

    let cut_left = start > 0;
    let cut_right = end < text_len;

    match (cut_left, cut_right) {
        (true, true) => format!("\u{2026}{ctx}\u{2026}"),
        (true, false) => format!("\u{2026}{ctx}"),
        (false, true) => format!("{ctx}\u{2026}"),
        (false, false) => ctx.to_string(),
    }
}

/// Register query_terms functions.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(scan_query_context, m)?)?;
    m.add_function(wrap_pyfunction!(extract_payload_context, m)?)?;
    Ok(())
}
