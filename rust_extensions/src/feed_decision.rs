//! Feed decision classifiers — Sprint C3.
//!
//! Pure functions (no I/O) for feed signal classification.
//! Called 1000+ times per sprint — Rust gives ~10x speedup over Python.

use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Minimum chars threshold for article fallback.
const MIN_ARTICLE_FALLBACK_CHARS: i32 = 150;

/// Classify fallback decision outcome.
/// Returns (reason, should_fetch, forced, wasted, helpful, skip_because)
#[pyfunction]
pub fn feed_decision_classify(
    assembled_text_len: i32,
    pre_fallback_hits_count: i32,
    quality_band: &str,
    metadata_boost: bool,
    language_mismatch: bool,
    _article_fallback_used: bool,
    article_fallback_attempted: bool,
    post_fallback_findings_count: i32,
    adapter_source_priority_bias: f64,
    adapter_metadata_richness_band: &str,
) -> (String, bool, bool, bool, bool, String) {
    // Case 1: pre-fallback hits exist → wasteful fallback
    if pre_fallback_hits_count > 0 {
        return (
            "feed_native_had_signal".to_string(),
            false,
            false,
            true,
            false,
            "feed-native already carried hits".to_string(),
        );
    }

    // Case 2: article fallback was not attempted — classify why
    if !article_fallback_attempted {
        if assembled_text_len >= MIN_ARTICLE_FALLBACK_CHARS
            && (quality_band == "high" || quality_band == "medium")
        {
            return (
                "skipped_high_quality".to_string(),
                false,
                false,
                false,
                false,
                format!(
                    "high quality ({}), assembled {} chars",
                    quality_band, assembled_text_len
                ),
            );
        }
        if adapter_source_priority_bias >= 0.1 && assembled_text_len >= MIN_ARTICLE_FALLBACK_CHARS {
            return (
                "skipped_adapter_bias".to_string(),
                false,
                false,
                false,
                false,
                format!(
                    "adapter source_priority_bias={:.2}",
                    adapter_source_priority_bias
                ),
            );
        }
        return (
            "no_fetch_warranted".to_string(),
            false,
            false,
            false,
            false,
            format!("assembled={}, quality={}", assembled_text_len, quality_band),
        );
    }

    // Case 3: fallback was forced by metadata/content mismatch
    if metadata_boost && !language_mismatch && assembled_text_len < MIN_ARTICLE_FALLBACK_CHARS {
        if post_fallback_findings_count > 0 {
            return (
                "forced_metadata_mismatch".to_string(),
                true,
                true,
                false,
                true,
                String::new(),
            );
        }
        return (
            "forced_no_yield".to_string(),
            true,
            true,
            true,
            false,
            String::new(),
        );
    }

    // Case 4: aged but structured entry (low quality but above threshold)
    if assembled_text_len >= MIN_ARTICLE_FALLBACK_CHARS && quality_band == "low" {
        if post_fallback_findings_count > 0 {
            return (
                "aged_structured_yield".to_string(),
                true,
                true,
                false,
                true,
                String::new(),
            );
        }
        return (
            "aged_structured_no_yield".to_string(),
            true,
            true,
            true,
            false,
            String::new(),
        );
    }

    // Case 5: adapter-mandated fallback
    if adapter_metadata_richness_band == "high" && assembled_text_len < MIN_ARTICLE_FALLBACK_CHARS {
        if post_fallback_findings_count > 0 {
            return (
                "forced_adapter_metadata".to_string(),
                true,
                true,
                false,
                true,
                String::new(),
            );
        }
        return (
            "forced_adapter_no_yield".to_string(),
            true,
            true,
            true,
            false,
            String::new(),
        );
    }

    // Case 6: normal below-threshold fallback
    if post_fallback_findings_count > 0 {
        return (
            "normal_fallback_yield".to_string(),
            true,
            false,
            false,
            true,
            String::new(),
        );
    }
    (
        "normal_fallback_no_yield".to_string(),
        true,
        false,
        false,
        false,
        String::new(),
    )
}

/// Diagnose which stage the signal is lost at.
#[pyfunction]
pub fn feed_stage_diagnose(
    entries_seen: i32,
    entries_with_empty_assembled_text: i32,
    entries_scanned: i32,
    entries_with_hits: i32,
    findings_built_pre_store: i32,
    patterns_configured: i32,
    findings_lost_to_dedup_total: i32,
) -> String {
    if patterns_configured == 0 {
        return "empty_registry";
    }
    if entries_seen == 0 {
        return "empty_fetch";
    }
    if entries_with_empty_assembled_text > 0 && entries_scanned == 0 {
        return "content_empty";
    }
    if entries_scanned == 0 {
        return "no_pattern_hits";
    }
    if findings_built_pre_store == 0 && findings_lost_to_dedup_total > 0 {
        return "findings_build_loss";
    }
    if entries_with_hits == 0 {
        return "no_pattern_hits_with_content";
    }
    if findings_built_pre_store > 0 {
        return "prestore_findings_present";
    }
    "unknown".to_string()
}

/// Compute a hint for next sprint about feed branch quality.
#[pyfunction]
pub fn feed_branch_hint(
    feed_signal_present: bool,
    fallback_useful: i32,
    fallback_waste: i32,
    _findings_rich: i32,
    findings_fallback: i32,
    entries_with_hits: i32,
) -> String {
    if entries_with_hits == 0 {
        return "unknown";
    }
    if feed_signal_present && fallback_waste == 0 {
        return "feed_strong";
    }
    if feed_signal_present && fallback_waste > 0 && fallback_useful == 0 {
        return "feed_weak";
    }
    if fallback_useful > 0 && findings_fallback > 0 {
        return "fallback_valuable";
    }
    if feed_signal_present || fallback_useful > 0 {
        return "mixed";
    }
    "unknown".to_string()
}

/// Compute condensed economics verdict for the run.
#[pyfunction]
pub fn feed_economics_verdict(
    feed_signal_present: bool,
    fallback_useful: i32,
    fallback_waste: i32,
    findings_rich: i32,
    findings_fallback: i32,
) -> (String, i32, i32, i32, i32) {
    let total_findings = findings_rich + findings_fallback;
    if total_findings == 0 {
        return (
            "no_signal".to_string(),
            feed_signal_present as i32,
            fallback_useful,
            fallback_waste,
            0,
        );
    }

    let rich_ratio = if total_findings > 0 {
        findings_rich as f64 / total_findings as f64
    } else {
        0.0
    };
    let waste_ratio = if fallback_useful + fallback_waste > 0 {
        fallback_waste as f64 / (fallback_useful + fallback_waste) as f64
    } else {
        0.0
    };

    let verdict_tag = if rich_ratio >= 0.7 {
        "feed_lean"
    } else if rich_ratio <= 0.3 {
        "fallback_lean"
    } else {
        "balanced"
    };

    let quality = (rich_ratio * 100.0 * (1.0 - waste_ratio * 0.5)) as i32;

    (
        verdict_tag.to_string(),
        feed_signal_present as i32,
        fallback_useful,
        fallback_waste,
        quality,
    )
}

/// Compute a rich dict-style verdict for feed branch economics.
#[pyfunction]
pub fn feed_branch_verdict(
    py: Python<'_>,
    feed_signal_present: bool,
    fallback_useful: i32,
    fallback_waste: i32,
    findings_rich: i32,
    findings_fallback: i32,
    squandered_high_usefulness: i32,
    metadata_strong_but_content_weak: i32,
    low_trust_feed_hits: i32,
    total_entries_with_hits: i32,
    entries_seen: i32,
    feed_native_yield_ratio: f64,
    fallback_value_ratio: f64,
) -> PyResult<Bound<'_, PyDict>> {
    let total_findings = findings_rich + findings_fallback;
    let feed_corroborates = feed_signal_present && fallback_useful > 0;
    let feed_burns_budget = fallback_waste > 0 && findings_rich == 0;

    let (verdict_tag, next_action, confidence_note) = if total_findings == 0 {
        ("no_signal", "reassess_feed", "no findings in either branch")
    } else {
        let rich_ratio = feed_native_yield_ratio;
        let (tag, _action, _note) = if rich_ratio >= 0.7 {
            ("feed_lean", "continue_feed", "feed-native dominant")
        } else if rich_ratio <= 0.3 {
            ("fallback_lean", "fallback_more", "fallback-dominant")
        } else {
            ("balanced", "continue_feed", "balanced feed+fallback")
        };

        let (final_action, final_note) = if !feed_signal_present && fallback_useful == 0 {
            ("reassess_feed", "neither branch produced signal")
        } else if feed_burns_budget {
            ("fallback_more", "feed burns budget; rely on fallback")
        } else if feed_corroborates {
            (
                "continue_feed",
                "both branches contribute; feed is valuable",
            )
        } else if feed_signal_present && fallback_useful == 0 {
            ("continue_feed", "feed-native only; fallback not needed")
        } else {
            ("reassess_feed", "mixed signals; review feed quality")
        };

        (tag, final_action, final_note)
    };

    let high_usefulness_waste_rate = if squandered_high_usefulness + fallback_waste > 0 {
        fallback_waste as f64 / (squandered_high_usefulness + fallback_waste) as f64
    } else {
        0.0
    };

    let confidence = if total_findings == 0 {
        0
    } else {
        let rich_ratio = feed_native_yield_ratio;
        let c = rich_ratio * 100.0 * (1.0 - high_usefulness_waste_rate * 0.5);
        c as i32
    };

    // Return Python dict directly — no JSON roundtrip parsing in Python.
    // [SWARM]-009 FIX: Replace .unwrap() with ? operator for PyErr propagation.
    let dict = PyDict::new(py);
    dict.set_item("verdict_tag", verdict_tag).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "feed_economics_verdict: failed to set verdict_tag: {e}"
        ))
    })?;
    dict.set_item("feed_native_yield", findings_rich)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_native_yield: {e}"
            ))
        })?;
    dict.set_item("fallback_yield", findings_fallback)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set fallback_yield: {e}"
            ))
        })?;
    dict.set_item("total_yield", total_findings).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "feed_economics_verdict: failed to set total_yield: {e}"
        ))
    })?;
    dict.set_item(
        "squandered_high_usefulness_entries",
        squandered_high_usefulness,
    )
    .map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "feed_economics_verdict: failed to set squandered_high_usefulness_entries: {e}"
        ))
    })?;
    dict.set_item("unnecessary_fallbacks", fallback_waste)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set unnecessary_fallbacks: {e}"
            ))
        })?;
    dict.set_item("useful_fallbacks", fallback_useful)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set useful_fallbacks: {e}"
            ))
        })?;
    dict.set_item("feed_corroborates", feed_corroborates)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_corroborates: {e}"
            ))
        })?;
    dict.set_item("feed_burns_budget", feed_burns_budget)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_burns_budget: {e}"
            ))
        })?;
    dict.set_item("feed_next_action", next_action)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_next_action: {e}"
            ))
        })?;
    dict.set_item("feed_confidence_note", confidence_note)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_confidence_note: {e}"
            ))
        })?;
    dict.set_item("feed_confidence_score", confidence)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_confidence_score: {e}"
            ))
        })?;
    dict.set_item("feed_native_yield_ratio", feed_native_yield_ratio)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set feed_native_yield_ratio: {e}"
            ))
        })?;
    dict.set_item("fallback_value_ratio", fallback_value_ratio)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set fallback_value_ratio: {e}"
            ))
        })?;
    dict.set_item("high_usefulness_waste_rate", high_usefulness_waste_rate)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set high_usefulness_waste_rate: {e}"
            ))
        })?;
    dict.set_item(
        "metadata_strong_content_weak",
        metadata_strong_but_content_weak,
    )
    .map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "feed_economics_verdict: failed to set metadata_strong_content_weak: {e}"
        ))
    })?;
    dict.set_item("low_trust_feed_hits", low_trust_feed_hits)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set low_trust_feed_hits: {e}"
            ))
        })?;
    dict.set_item("entries_with_hits", total_entries_with_hits)
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "feed_economics_verdict: failed to set entries_with_hits: {e}"
            ))
        })?;
    dict.set_item("entries_seen", entries_seen).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "feed_economics_verdict: failed to set entries_seen: {e}"
        ))
    })?;
    Ok(dict.into())
}

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(feed_decision_classify))?;
    m.add_function(wrap_pyfunction!(feed_stage_diagnose))?;
    m.add_function(wrap_pyfunction!(feed_branch_hint))?;
    m.add_function(wrap_pyfunction!(feed_economics_verdict))?;
    m.add_function(wrap_pyfunction!(feed_branch_verdict))?;
    Ok(())
}
