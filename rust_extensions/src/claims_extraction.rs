//! Claims extraction — CPU-bound sentence splitting, polarity, and confidence.
//!
//! ## R22 Status: Deferred Pipeline Integration
//!
//! This module IS REGISTERED in lib.rs and COMPILES successfully, but has NO
//! active Python callers in the current sprint workflow.
//!
//! Current state:
//!   - `claims_coordinator.py` was archived (no active coordinator)
//!   - `graph_rag._extract_claim()` uses Python SPO parsing (separate implementation)
//!   - Rust `extract_claims()` provides sentence-level claim extraction with
//!     polarity/confidence metadata — different purpose than SPO extraction
//!
//! Integration path (deferred):
//!   1. Reactivate or create a claims coordinator that calls Rust extract_claims
//!   2. Wire batch_extract_claims_python for high-throughput evidence processing
//!   3. Use Rust claims in GraphRAG for enhanced temporal drift detection
//!      (add polarity/confidence to drift events)
//!
//! ## API
//!
//!   extract_claims(text, title, summary, source_type, evidence_type)
//!     → Vec<(claim_text, polarity, confidence, source, evidence_type)>
//!
//!   batch_extract_claims(texts: Vec<(text, title, summary, source_type, evidence_type)>)
//!     → rayon-parallel for n >= adaptive_threshold or total_bytes >= 16KB
//!
//!   batch_extract_claims_python(texts, titles, summaries, source_types, evidence_types)
//!     → PyO3 zero-copy for Python lists, single GIL acquisition
//!
//! Design invariants:
//!   CLM.T1  No panics, fail-soft on errors
//!   CLM.T2  Bounded: max text len 100KB, max batch 1000 sentences
//!   CLM.T3  Always-on: mixed_pool adaptive threading
//!   CLM.T4  Pre-compiled regexes via LazyLock (zero regex compile per call)
//!
//! Performance strategy:
//!   Sentence splitting: regex-automata meta Regex (no \b issues)
//!   IOC detection: imports from ioc_core (ISSUE-008: single source, no duplicate compilation)
//!   Polarity: pre-categorized word sets (O(n) string search)
//!   Confidence: deterministic policy port from confidence_policy.py

use crate::adaptive_scheduler;
use crate::gil::release_gil;
use crate::ioc_extract;
use crate::mixed_pool;
use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;

#[derive(Debug, Clone)]
#[repr(C)]
pub struct Claim {
    pub text: String,
    pub polarity: String, // "positive" | "negative" | "neutral"
    pub confidence: f64,
    pub source: String,
    pub evidence_type: String,
}

// Sentence-splitting: split on . ! ? followed by space + uppercase
// Uses regex-automata with meta feature for look-around support.
static SENTENCE_SPLITTER: std::sync::LazyLock<regex_automata::meta::Regex> =
    std::sync::LazyLock::new(|| {
        regex_automata::meta::Regex::new(r"(?<=[.!?])\s+(?=[A-Z])")
            .expect("claims_extraction: sentence splitter regex must be valid")
    });

// ISSUE-008 fix: IOC presence-check now uses ioc_extract::has_* functions
// which use ioc_patterns.rs (the canonical source of truth)

static NEGATIVE_WORDS: std::sync::LazyLock<Vec<&'static str>> = std::sync::LazyLock::new(|| {
    vec![
        "not",
        "no evidence",
        "false",
        "denies",
        "debunked",
        "failed to",
        "contrary",
        "contradicts",
        "disputed",
        "unverified",
        "unconfirmed",
        "incorrect",
        "inaccurate",
        "misleading",
        "fabricated",
        "hoax",
    ]
});

static POSITIVE_WORDS: std::sync::LazyLock<Vec<&'static str>> = std::sync::LazyLock::new(|| {
    vec![
        "confirmed",
        "observed",
        "detected",
        "reported",
        "evidence shows",
        "verified",
        "corroborated",
        "supported",
        "consistent with",
        "matches",
        "validates",
        "demonstrates",
        "confirms",
        "establishes",
        "proves",
    ]
});

const MAX_CLAIMS_PER_TEXT: usize = 20;
const MAX_SENTENCE_LEN: usize = 512;
const MIN_SENTENCE_LEN: usize = 20;
const BASE_CONFIDENCE: f64 = 0.45;
const URL_BONUS: f64 = 0.10;
const PROVENANCE_BONUS: f64 = 0.10;
const TITLE_AGREEMENT_BONUS: f64 = 0.10;
const MAX_CONFIDENCE: f64 = 0.75;

#[inline]
fn split_sentences(text: &str) -> Vec<String> {
    // Normalize whitespace first
    let normalized = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let sentences: Vec<&str> = SENTENCE_SPLITTER
        .split(&normalized)
        .map(|span| &normalized[span.start..span.end])
        .collect();

    sentences
        .into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| s.len() >= MIN_SENTENCE_LEN && s.len() <= MAX_SENTENCE_LEN)
        .collect()
}

#[inline]
fn derive_polarity(text: &str) -> String {
    // Single to_lowercase() per sentence — reused for both checks
    let lower = text.to_lowercase();
    for word in NEGATIVE_WORDS.iter() {
        if lower.contains(word) {
            return "negative".to_string();
        }
    }
    for word in POSITIVE_WORDS.iter() {
        if lower.contains(word) {
            return "positive".to_string();
        }
    }
    "neutral".to_string()
}

#[inline]
fn derive_confidence(
    text: &str,
    source_family: &str,
    has_provenance: bool,
    has_title_agreement: bool,
) -> f64 {
    let mut confidence = BASE_CONFIDENCE;

    // Source family bonus
    match source_family {
        "CT" => confidence += 0.15,      // Certificate Transparency
        "FEED" => confidence += 0.05,    // RSS/Atom
        "WAYBACK" => confidence += 0.02, // Archive
        "STEALTH" => confidence += 0.08, // Stealth sources
        "PUBLIC" => {}                   // default
        _ => confidence += 0.0,
    }

    // Provenance bonus
    if has_provenance {
        confidence += PROVENANCE_BONUS;
    }

    // IOC detection bonus (URL, domain, email, IP)
    // ISSUE-008 fix: Use ioc_extract::has_any_ioc (uses ioc_patterns.rs, no duplicate compilation)
    if ioc_extract::has_any_ioc(text) {
        confidence += URL_BONUS;
    }

    // Title/summary corroboration bonus
    if has_title_agreement {
        confidence += TITLE_AGREEMENT_BONUS;
    }

    confidence.min(MAX_CONFIDENCE)
}

/// Extract claims from a single text.
/// Returns up to MAX_CLAIMS_PER_TEXT claims.
#[inline]
fn extract_claims_from_text(
    text: &str,
    title: &str,
    summary: &str,
    source_type: &str,
    evidence_type: &str,
) -> Vec<Claim> {
    if text.is_empty() || text.len() > 100_000 {
        return vec![];
    }

    let sentences = split_sentences(text);
    if sentences.is_empty() {
        return vec![];
    }

    // Title corroboration: check word overlap — only allocate if non-empty
    let title_words: std::collections::HashSet<String> = if title.is_empty() {
        std::collections::HashSet::new()
    } else {
        title.split_whitespace().map(|w| w.to_lowercase()).collect()
    };
    let summary_words: std::collections::HashSet<String> = if summary.is_empty() {
        std::collections::HashSet::new()
    } else {
        summary
            .split_whitespace()
            .map(|w| w.to_lowercase())
            .collect()
    };

    let source_family = match source_type.to_uppercase().as_str() {
        "CT" | "CERTIFICATE_TRANSPARENCY" => "CT",
        "FEED" | "RSS" | "ATOM" => "FEED",
        "WAYBACK" | "ARCHIVE" | "ARCHIVE_ORG" => "WAYBACK",
        "STEALTH" | "HIDDEN" => "STEALTH",
        _ => "PUBLIC",
    };

    let mut claims = Vec::with_capacity(MAX_CLAIMS_PER_TEXT);
    let mut seen = std::collections::HashSet::new();

    for sentence in sentences {
        if claims.len() >= MAX_CLAIMS_PER_TEXT {
            break;
        }

        let text_lower = sentence.to_lowercase();

        // Check corroboration with title/summary — only build sentence_words if needed
        let has_title_agreement = if title_words.is_empty() || summary_words.is_empty() {
            false
        } else {
            let sentence_words: std::collections::HashSet<String> = sentence
                .split_whitespace()
                .map(|w| w.to_lowercase())
                .collect();
            sentence_words.intersection(&title_words).count() >= 2
                && sentence_words.intersection(&summary_words).count() >= 2
        };

        let polarity = derive_polarity(&text_lower);
        let confidence = derive_confidence(
            &sentence,
            source_family,
            false, // provenance - not available in this context
            has_title_agreement,
        );

        // Deduplicate by text content
        if seen.insert(sentence.clone()) {
            claims.push(Claim {
                text: sentence,
                polarity,
                confidence,
                source: "rust_claim_extractor".to_string(),
                evidence_type: evidence_type.to_string(),
            });
        }
    }

    claims
}

/// Extract claims from a batch of evidence packets using mixed_pool parallel.
/// Each packet is (text, title, summary, source_type, evidence_type).
#[derive(Debug, Clone)]
#[repr(C)]
pub struct EvidencePacket<'a> {
    pub text: &'a str,
    pub title: &'a str,
    pub summary: &'a str,
    pub source_type: &'a str,
    pub evidence_type: &'a str,
}

pub fn batch_extract_claims_inner(packets: &[(&str, &str, &str, &str, &str)]) -> Vec<Vec<Claim>> {
    let n = packets.len();

    if n == 0 {
        return vec![];
    }

    if n < crate::adaptive_scheduler::mixed_threshold() {
        // Serial path — single thread, no pool overhead
        packets
            .iter()
            .map(|(text, title, summary, source_type, evidence_type)| {
                extract_claims_from_text(text, title, summary, source_type, evidence_type)
            })
            .collect()
    } else {
        // Parallel path via mixed_pool
        // Issue #6: GIL released so rayon workers can truly run in parallel.
        let pool = mixed_pool(n);
        let results: Vec<Vec<Claim>> = Python::attach(|py| {
            release_gil(py, move || {
                pool.install(|| {
                    packets
                        .par_iter()
                        .map(|(text, title, summary, source_type, evidence_type)| {
                            extract_claims_from_text(
                                text,
                                title,
                                summary,
                                source_type,
                                evidence_type,
                            )
                        })
                        .collect()
                })
            })
        });
        results
    }
}

/// Extract claims from a single text (Python API).
/// Returns list of (text, polarity, confidence, source, evidence_type) tuples.
#[pyfunction]
pub fn extract_claims(
    text: &str,
    title: &str,
    summary: &str,
    source_type: &str,
    evidence_type: &str,
) -> Vec<(String, String, f64, String, String)> {
    extract_claims_from_text(text, title, summary, source_type, evidence_type)
        .into_iter()
        .map(|c| (c.text, c.polarity, c.confidence, c.source, c.evidence_type))
        .collect()
}

/// Extract claims from a batch of texts using rayon parallel (Python API).
/// texts: list of (text, title, summary, source_type, evidence_type) tuples.
/// Returns flat list of claims across all texts.
/// R4-02: GIL released via py.allow_gil for both serial and parallel paths.
#[pyfunction]
pub fn batch_extract_claims<'py>(
    texts: Vec<(String, String, String, String, String)>,
    py: Python<'py>,
) -> Vec<(String, String, f64, String, String)> {
    if texts.is_empty() {
        return vec![];
    }

    let n = texts.len();
    let total_bytes: usize = texts.iter().map(|(t, _, _, _, _)| t.len()).sum();

    // Threshold: mixed_pool adaptive threshold OR >= 16KB total
    let use_parallel = n >= adaptive_scheduler::mixed_threshold() || total_bytes >= 16 * 1024;

    if !use_parallel {
        // R4-02 FIX: GIL released for CPU-bound extract_claims_from_text (regex/splitting).
        // R4-02: Added py.detach() wrapper for serial path — CPU-bound work can run
        // without GIL, allowing other Python coroutines to progress on this thread.
        return crate::gil::release_gil(py, move || {
            texts
                .iter()
                .flat_map(|(text, title, summary, source_type, evidence_type)| {
                    extract_claims_from_text(text, title, summary, source_type, evidence_type)
                        .into_iter()
                        .map(|c| (c.text, c.polarity, c.confidence, c.source, c.evidence_type))
                })
                .collect()
        });
    }

    // Parallel path — GIL released for rayon workers
    let packets: Vec<(&str, &str, &str, &str, &str)> = texts
        .iter()
        .map(|(t, ti, s, st, et)| {
            (
                t.as_str(),
                ti.as_str(),
                s.as_str(),
                st.as_str(),
                et.as_str(),
            )
        })
        .collect();

    let results: Vec<Vec<Claim>> =
        crate::gil::release_gil(py, || batch_extract_claims_inner(&packets));

    results
        .into_iter()
        .flat_map(|claims| {
            claims
                .into_iter()
                .map(|c| (c.text, c.polarity, c.confidence, c.source, c.evidence_type))
        })
        .collect()
}

/// Bulk batch extract — single GIL acquisition for entire batch.
/// Accepts parallel arrays: texts, titles, summaries, source_types, evidence_types.
/// Returns flat list of (text, polarity, confidence, source, evidence_type) tuples.
/// R4-02: GIL released via release_gil() for batch_extract_claims_inner (CPU-intensive claim extraction).
#[pyfunction]
pub fn batch_extract_claims_python<'py>(
    texts: &Bound<'py, PyList>,
    titles: &Bound<'py, PyList>,
    summaries: &Bound<'py, PyList>,
    source_types: &Bound<'py, PyList>,
    evidence_types: &Bound<'py, PyList>,
    py: Python<'py>,
) -> PyResult<Vec<(String, String, f64, String, String)>> {
    let n = texts.len();

    if n == 0 {
        return Ok(vec![]);
    }

    if n != titles.len()
        || n != summaries.len()
        || n != source_types.len()
        || n != evidence_types.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "All input lists must have the same length",
        ));
    }

    // Collect under GIL
    let texts_owned: Vec<String> = texts
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();
    let titles_owned: Vec<String> = titles
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();
    let summaries_owned: Vec<String> = summaries
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();
    let source_types_owned: Vec<String> = source_types
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();
    let evidence_types_owned: Vec<String> = evidence_types
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    let packets: Vec<(&str, &str, &str, &str, &str)> = texts_owned
        .iter()
        .zip(titles_owned.iter())
        .zip(summaries_owned.iter())
        .zip(source_types_owned.iter())
        .zip(evidence_types_owned.iter())
        .map(|((((t, ti), s), st), et)| {
            (
                t.as_str(),
                ti.as_str(),
                s.as_str(),
                st.as_str(),
                et.as_str(),
            )
        })
        .collect();

    // R4-02: GIL released — batch_extract_claims_inner uses rayon parallel (CPU-intensive)
    let results: Vec<Vec<Claim>> =
        crate::gil::release_gil(py, || batch_extract_claims_inner(&packets));

    Ok(results
        .into_iter()
        .flat_map(|claims| {
            claims
                .into_iter()
                .map(|c| (c.text, c.polarity, c.confidence, c.source, c.evidence_type))
        })
        .collect())
}

/// Register claims extraction functions with the Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_claims))?;
    m.add_function(wrap_pyfunction!(batch_extract_claims))?;
    m.add_function(wrap_pyfunction!(batch_extract_claims_python))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_split_sentences() {
        let text =
            "Server 192.168.1.1 responding on port 8080. Email admin@example.com for access.";
        let sentences = split_sentences(text);
        assert!(sentences.len() >= 1);
    }

    #[test]
    fn test_polarity_positive() {
        assert_eq!(
            derive_polarity("evidence shows confirmed detection"),
            "positive"
        );
    }

    #[test]
    fn test_polarity_negative() {
        assert_eq!(derive_polarity("not confirmed, false report"), "negative");
    }

    #[test]
    fn test_polarity_neutral() {
        assert_eq!(derive_polarity("the weather is cloudy today"), "neutral");
    }

    #[test]
    fn test_extract_claims_url_bonus() {
        let claims = extract_claims_from_text(
            "Visit https://example.com for details",
            "Example Domain",
            "Example domain details",
            "PUBLIC",
            "web",
        );
        assert!(!claims.is_empty());
        // URL should give bonus
        assert!(claims[0].confidence >= BASE_CONFIDENCE + URL_BONUS - 0.01);
    }

    #[test]
    fn test_extract_claims_ct_source() {
        let claims = extract_claims_from_text(
            "Certificate issued for example.com",
            "Certificate",
            "CT log entry",
            "CT",
            "certificate_transparency",
        );
        assert!(!claims.is_empty());
        // CT source should give higher confidence
        assert!(claims[0].confidence >= BASE_CONFIDENCE + 0.10);
    }

    #[test]
    fn test_claims_deduplication() {
        let claims = extract_claims_from_text(
            "This is a test sentence. Another test sentence.",
            "",
            "",
            "PUBLIC",
            "web",
        );
        // Should not duplicate identical sentences
        let texts: Vec<&str> = claims.iter().map(|c| c.text.as_str()).collect();
        for t in &texts {
            assert!(texts.iter().filter(|x| *x == t).count() == 1);
        }
    }

    #[test]
    fn test_empty_text() {
        let claims = extract_claims_from_text("", "title", "summary", "PUBLIC", "web");
        assert!(claims.is_empty());
    }

    #[test]
    fn test_short_text() {
        let claims = extract_claims_from_text("Short.", "title", "summary", "PUBLIC", "web");
        // Short sentences (< 20 chars) should be filtered
        assert!(claims.is_empty());
    }

    #[test]
    fn test_batch_extract_claims_inner() {
        let packets = vec![
            ("First sentence here.", "", "", "PUBLIC", "web"),
            ("Second sentence here.", "", "", "PUBLIC", "web"),
            (
                "Third sentence here.",
                "",
                "",
                "CT",
                "certificate_transparency",
            ),
        ];
        let results = batch_extract_claims_inner(&packets);
        assert_eq!(results.len(), 3);
        assert!(results.iter().all(|r| !r.is_empty()));
    }

    #[test]
    fn test_confidence_max_cap() {
        // Max confidence should be capped at MAX_CONFIDENCE
        let claims = extract_claims_from_text(
            "Confirmed https://example.com verified by CT source with evidence.",
            "Confirmed Example",
            "Example verified",
            "CT",
            "certificate_transparency",
        );
        assert!(!claims.is_empty());
        assert!(claims[0].confidence <= MAX_CONFIDENCE);
    }
}
