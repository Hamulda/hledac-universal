//! Feed pipeline — unified parse + scan + dedup in Rust.

use aho_corasick::AhoCorasick;
use parking_lot::Mutex;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
use xml::reader::{EventReader, XmlEvent};
use xxhash_rust::xxh3::xxh3_64;

#[cfg(test)]
use xxhash_rust::xxh3::xxh3_64;

#[derive(Debug, Clone)]
struct FeedEntryRaw {
    title: String,
    link: String,
    description: String,
    guid: String,
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
                    entries.push(FeedEntryRaw {
                        title: current_title.trim().to_string(),
                        link: current_link.trim().to_string(),
                        description: current_description.trim().to_string(),
                        guid: if current_guid.trim().is_empty() {
                            current_link.trim().to_string()
                        } else { current_guid.trim().to_string() },
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

fn build_automaton(patterns: &[String]) -> AhoCorasick {
    AhoCorasick::new(patterns).expect("Failed to build AC automaton")
}

fn scan_text(automaton: &AhoCorasick, patterns: &[String], text: &str, labels: &[String]) -> Vec<PatternHitRaw> {
    let text_lower = text.to_lowercase();
    let mut hits = Vec::new();
    let mut value_buf = String::new();
    for m in automaton.find_iter(&text_lower) {
        let idx = m.pattern().as_usize();
        let start = m.start();
        let end = m.end();
        let pattern = patterns.get(idx).cloned().unwrap_or_default();
        let label = labels.get(idx).cloned().unwrap_or_default();
        let value = {
            value_buf.clear();
            value_buf.push_str(&text_lower[start..end]);
            value_buf.clone()
        };
        hits.push(PatternHitRaw { start, end, pattern, label, value });
    }
    hits
}

#[cfg(test)]
use xxhash_rust::xxh3::xxh3_64;

fn simple_hash(s: &str) -> u64 {
    xxh3_64(s.as_bytes())
}

fn scan_entries_parallel(
    entries: &[FeedEntryRaw],
    patterns: &[String],
    labels: &[String],
    seen_guids: &Mutex<HashSet<u64>>,
    max_entries: usize,
) -> Vec<EntryScanResult> {
    let automaton = build_automaton(patterns);
    let entries_slice = if entries.len() > max_entries && max_entries > 0 {
        &entries[..max_entries]
    } else { entries };

    entries_slice
        .par_iter()
        .enumerate()
        .filter_map(|(idx, entry)| {
            let guid_lower = entry.guid.to_lowercase();
            let entry_hash = simple_hash(&guid_lower);
            let is_dupe = {
                let mut seen = seen_guids.lock();
                if seen.contains(&entry_hash) { true }
                else { seen.insert(entry_hash); false }
            };
            if is_dupe { return None; }

            let title_lower = entry.title.to_lowercase();
            let assembly_text = if entry.description.is_empty() {
                title_lower
            } else {
                let desc_lower = entry.description.to_lowercase();
                format!("{}\n\n{}", title_lower, desc_lower)
            };
            let combined_hits = scan_text(&automaton, patterns, &assembly_text, labels);
            let entry_url = if !entry.link.is_empty() {
                entry.link.clone()
            } else {
                format!("urn:feed:entry:{}", entry.title.chars().take(64).collect::<String>())
            };
            Some(EntryScanResult {
                entry_idx: idx,
                combined_hits,
                entry_url,
                assembly_phase: if entry.description.is_empty() { "title_only".to_string() } else { "title_description".to_string() },
            })
        })
        .collect()
}

#[pyfunction]
pub fn feed_entry_pipeline(
    raw_xml: String,
    max_entries: usize,
    patterns: Vec<String>,
    labels: Vec<String>,
) -> Vec<(usize, String, Vec<(usize, usize, String, String, String)>, usize, usize, String)> {
    let seen_guids: Mutex<HashSet<u64>> = Mutex::new(HashSet::new());
    let entries = parse_rss_xml(&raw_xml);
    if entries.is_empty() { return Vec::new(); }
    let results = scan_entries_parallel(&entries, &patterns, &labels, &seen_guids, max_entries);
    results.into_iter().map(|r| {
        let combined_hits = r.combined_hits.into_iter().map(|h| (h.start, h.end, h.pattern, h.label, h.value)).collect();
        (r.entry_idx, r.entry_url, combined_hits, 0, 0, r.assembly_phase)
    }).collect()
}

#[pyfunction]
pub fn feed_batch_pipeline(
    feeds: Vec<(String, usize)>,
    patterns: Vec<String>,
    labels: Vec<String>,
) -> Vec<Vec<(usize, String, Vec<(usize, usize, String, String, String)>, usize, usize, String)>> {
    let patterns_clone = patterns.clone();
    let labels_clone = labels.clone();
    feeds.into_par_iter().map(|(xml, max_entries)| {
        feed_entry_pipeline(xml, max_entries, patterns_clone.clone(), labels_clone.clone())
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
        let automaton = build_automaton(&patterns);
        let hits = scan_text(&automaton, &patterns, "malware detected", &labels);
        assert_eq!(hits.len(), 1);
    }
}
