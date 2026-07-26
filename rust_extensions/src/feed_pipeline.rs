//! Feed pipeline — unified parse + scan + dedup in Rust.

use crate::os_unfair_lock::{OsUnfairLock, OsUnfairLockExt};
use aho_corasick::AhoCorasick;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
use std::sync::Arc;
use xml::reader::{EventReader, XmlEvent};
use xxhash_rust::xxh3::xxh3_64;

/// Shared dedup state — OsUnfairLock for ~5ns lock/unlock (vs ~25ns parking_lot::Mutex).
/// OsUnfairLock is NOT reentrant but is safe here because the critical section
/// (HashSet::contains + insert) is purely computational with no suspension points.
struct SeenGuids {
    lock: OsUnfairLock,
    set: HashSet<u64>,
}

unsafe impl Send for SeenGuids {}
unsafe impl Sync for SeenGuids {}

impl SeenGuids {
    fn new() -> Self {
        Self {
            lock: OsUnfairLock::new(),
            set: HashSet::new(),
        }
    }

    /// Check if hash is present, insert if not. Returns true if duplicate.
    #[inline]
    fn check_and_insert(&self, hash: u64) -> bool {
        // SAFETY: lock_guard is held for the entire contains/insert pair.
        // No suspension points in this closure (pure computational).
        // Each rayon thread gets its own &self; os_unfair_lock is Sync.
        let mut guard = self.lock.lock_guard();
        guard.contains(&hash) || guard.insert(hash)
    }
}

#[derive(Debug, Clone)]
struct FeedEntryRaw {
    title: String,
    link: String,
    // Pre-lowercased variants — computed once during parse, reused in scan.
    guid_lower: String,
    title_lower: String,
    desc_lower: String,
}

#[derive(Debug, Clone)]
struct PatternHitRaw {
    start: usize,
    end: usize,
    pattern: String,
    label: String,
    value: String,
}

#[derive(Debug)]
struct EntryScanResult {
    entry_idx: usize,
    combined_hits: Vec<PatternHitRaw>,
    entry_url: String,
    assembly_phase: String,
}

fn parse_rss_xml(xml_str: &str) -> Vec<FeedEntryRaw> {
    let mut entries = Vec::new();
    let mut in_entry = false;
    let mut current_tag = String::new();
    let mut current_title = String::new();
    let mut current_link = String::new();
    let mut current_description = String::new();
    let mut current_guid = String::new();

    let reader = EventReader::from_str(xml_str);
    for e in reader {
        match e {
            Ok(XmlEvent::StartElement { name, attributes, .. }) => {
                let tag = name.local_name.clone();
                if tag == "item" || tag == "entry" {
                    in_entry = true;
                    current_title.clear();
                    current_link.clear();
                    current_description.clear();
                    current_guid.clear();
                }
                if tag == "link" {
                    for attr in attributes {
                        if attr.name.local_name == "href" {
                            current_link.clear();
                            current_link.push_str(&attr.value);
                        }
                    }
                }
                current_tag = tag;
            }
            Ok(XmlEvent::EndElement { name }) => {
                let tag = name.local_name.clone();
                if (tag == "item" || tag == "entry") && in_entry {
                    in_entry = false;
                    let guid_val = if current_guid.trim().is_empty() {
                        current_link.trim().to_string()
                    } else { current_guid.trim().to_string() };
                    let title_trimmed = current_title.trim().to_string();
                    let desc_trimmed = current_description.trim().to_string();
                    entries.push(FeedEntryRaw {
                        title: title_trimmed.clone(),
                        link: current_link.trim().to_string(),
                        guid_lower: guid_val.to_lowercase(),
                        title_lower: title_trimmed.to_lowercase(),
                        desc_lower: desc_trimmed.to_lowercase(),
                    });
                }
                current_tag.clear();
            }
            Ok(XmlEvent::Characters(s)) => {
                if !in_entry { continue; }
                match current_tag.as_str() {
                    "title" => current_title.push_str(&s),
                    "link" => { if current_link.is_empty() { current_link.push_str(&s); } }
                    "description" | "summary" | "content" => current_description.push_str(&s),
                    "guid" | "id" => current_guid.push_str(&s),
                    _ => {}
                }
            }
            Ok(XmlEvent::CData(s)) => {
                if !in_entry { continue; }
                match current_tag.as_str() {
                    "title" => current_title.push_str(&s),
                    "description" | "summary" | "content" => current_description.push_str(&s),
                    _ => {}
                }
            }
            Err(_) => break,
            Ok(XmlEvent::EndDocument) => break,
            _ => {}
        }
    }
    entries
}

fn build_automaton(patterns: &[String]) -> Option<AhoCorasick> {
    if patterns.is_empty() {
        return None;
    }
    AhoCorasick::new(patterns).ok()
}

// pre_lowercased: caller guarantees text is already lowercase (assembly_text is built from title_lower + desc_lower).
fn scan_text(automaton: &AhoCorasick, patterns: &[String], pre_lowercased_text: &str, labels: &[String]) -> Vec<PatternHitRaw> {
    let mut hits = Vec::new();
    let mut value_buf = String::new();
    for m in automaton.find_iter(pre_lowercased_text) {
        let idx = m.pattern().as_usize();
        let start = m.start();
        let end = m.end();
        let pattern = patterns.get(idx).cloned().unwrap_or_default();
        let label = labels.get(idx).cloned().unwrap_or_default();
        let value = {
            value_buf.clear();
            value_buf.push_str(&pre_lowercased_text[start..end]);
            value_buf.clone()
        };
        hits.push(PatternHitRaw { start, end, pattern, label, value });
    }
    hits
}

fn simple_hash(s: &str) -> u64 {
    xxh3_64(s.as_bytes())
}

fn scan_entries_parallel(
    entries: &[FeedEntryRaw],
    patterns: &[String],
    labels: &[String],
    seen_guids: &Arc<SeenGuids>,
    max_entries: usize,
) -> Vec<EntryScanResult> {
    let automaton = match build_automaton(patterns) {
        Some(a) => a,
        None => return Vec::new(), // Empty patterns — nothing to match.
    };
    scan_entries_parallel_impl(entries, patterns, labels, seen_guids, max_entries, &automaton)
}

/// Like scan_entries_parallel but accepts a pre-built automaton.
/// Used by feed_batch_pipeline to avoid rebuilding automaton per feed.
fn scan_entries_parallel_with_automaton(
    entries: &[FeedEntryRaw],
    patterns: &[String],
    labels: &[String],
    seen_guids: &Arc<SeenGuids>,
    max_entries: usize,
    automaton: &AhoCorasick,
) -> Vec<EntryScanResult> {
    scan_entries_parallel_impl(entries, patterns, labels, seen_guids, max_entries, automaton)
}

fn scan_entries_parallel_impl(
    entries: &[FeedEntryRaw],
    patterns: &[String],
    labels: &[String],
    seen_guids: &Arc<SeenGuids>,
    max_entries: usize,
    automaton: &AhoCorasick,
) -> Vec<EntryScanResult> {
    // max_entries == 0 is the no-limit sentinel: process all entries.
    // max_entries > 0: cap at that many entries (for per-feed budgets).
    let entries_slice = if max_entries == 0 {
        entries
    } else if entries.len() > max_entries {
        &entries[..max_entries]
    } else {
        entries
    };

    entries_slice
        .par_iter()
        .enumerate()
        .filter_map(|(idx, entry)| {
            // Use pre-lowercased values — computed once in parse_rss_xml.
            let entry_hash = simple_hash(&entry.guid_lower);
            // OsUnfairLock: ~5ns lock/unlock vs ~25ns parking_lot::Mutex.
            // NOT reentrant but safe here: purely computational, no suspension.
            let is_dupe = !seen_guids.check_and_insert(entry_hash);
            if is_dupe { return None; }

            // Reuse pre-lowercased title/desc — only allocate assembly_text.
            let assembly_text = if entry.desc_lower.is_empty() {
                entry.title_lower.clone()
            } else {
                format!("{}\n\n{}", entry.title_lower, entry.desc_lower)
            };
            let combined_hits = scan_text(automaton, patterns, &assembly_text, labels);
            let entry_url = if !entry.link.is_empty() {
                entry.link.clone()
            } else {
                format!("urn:feed:entry:{}", entry.title.chars().take(64).collect::<String>())
            };
            Some(EntryScanResult {
                entry_idx: idx,
                combined_hits,
                entry_url,
                assembly_phase: if entry.desc_lower.is_empty() { "title_only".to_string() } else { "title_description".to_string() },
            })
        })
        .collect()
}

#[pyfunction]
pub fn feed_entry_pipeline(
    _py: Python<'_>,
    raw_xml: String,
    max_entries: usize,
    patterns: Vec<String>,
    labels: Vec<String>,
) -> Vec<(usize, String, Vec<(usize, usize, String, String, String)>, usize, usize, String)> {
    let seen_guids = Arc::new(SeenGuids::new());
    let entries = parse_rss_xml(&raw_xml);
    if entries.is_empty() { return Vec::new(); }
    // Release GIL during rayon parallel scan — rayon threads are pure Rust,
    // no Python callbacks. Allows concurrent HTTP I/O on other threads.
    let results = _py.allow_threads(|| {
        scan_entries_parallel(&entries, &patterns, &labels, &seen_guids, max_entries)
    });
    results.into_iter().map(|r| {
        let combined_hits = r.combined_hits.into_iter().map(|h| (h.start, h.end, h.pattern, h.label, h.value)).collect();
        (r.entry_idx, r.entry_url, combined_hits, 0, 0, r.assembly_phase)
    }).collect()
}

#[pyfunction]
pub fn feed_batch_pipeline(
    _py: Python<'_>,
    feeds: Vec<(String, usize)>,
    patterns: Vec<String>,
    labels: Vec<String>,
) -> Vec<Vec<(usize, String, Vec<(usize, usize, String, String, String)>, usize, usize, String)>> {
    // Build automaton ONCE for all feeds — avoids redundant O(N·M) rebuilds.
    let automaton = match build_automaton(&patterns) {
        Some(a) => a,
        None => return Vec::new(), // Empty patterns — nothing to match.
    };
    // Cross-feed dedup: single shared SeenGuids (OsUnfairLock + HashSet) passed to all feed scans.
    let seen_guids = Arc::new(SeenGuids::new());
    // Release GIL during rayon parallel feed processing.
    // NESTED allow_threads is safe (PyO3 detects already-released GIL) but
    // feed_entry_pipeline_xml no longer calls allow_threads internally.
    // Pass &patterns / &labels (not clones) — String is Sync, sharing owned
    // Vec is unnecessary here since the closure doesn't consume patterns/labels.
    _py.allow_threads(|| {
        feeds.into_par_iter().map(|(xml, max_entries)| {
            feed_entry_pipeline_xml_impl(
                &xml,
                max_entries,
                &patterns,
                &labels,
                &automaton,
                &seen_guids,
            )
        }).collect()
    })
}

// Internal non-PyO3 version — called from feed_batch_pipeline within allow_threads scope.
// Shares automaton and seen_guids across all feeds for cross-feed dedup.
fn feed_entry_pipeline_xml_impl(
    raw_xml: &str,
    max_entries: usize,
    patterns: &[String],
    labels: &[String],
    automaton: &AhoCorasick,
    seen_guids: &Arc<SeenGuids>,
) -> Vec<(usize, String, Vec<(usize, usize, String, String, String)>, usize, usize, String)> {
    let entries = parse_rss_xml(raw_xml);
    if entries.is_empty() { return Vec::new(); }
    let results = scan_entries_parallel_with_automaton(
        &entries,
        patterns,
        labels,
        seen_guids,
        max_entries,
        automaton,
    );
    results.into_iter().map(|r| {
        let combined_hits = r.combined_hits.into_iter().map(|h| (h.start, h.end, h.pattern, h.label, h.value)).collect();
        (r.entry_idx, r.entry_url, combined_hits, 0, 0, r.assembly_phase)
    }).collect()
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(feed_entry_pipeline, m)?)?;
    m.add_function(wrap_pyfunction!(feed_batch_pipeline, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_rss_xml() {
        let xml = r#"<?xml version="1.0"?><rss version="2.0"><channel><item><title>Test</title><link>https://ex.com/1</link><description>malware here</description><guid>g1</guid></item></channel></rss>"#;
        let entries = parse_rss_xml(xml);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].title, "Test");
    }

    #[test]
    fn test_scan_text() {
        let patterns = vec!["malware".to_string()];
        let labels = vec!["threat".to_string()];
        let automaton = build_automaton(&patterns).expect("automaton for non-empty patterns");
        // scan_text expects pre-lowercased text — lowercase the input ourselves.
        let hits = scan_text(&automaton, &patterns, "malware detected".to_lowercase(), &labels);
        assert_eq!(hits.len(), 1);
    }
}
