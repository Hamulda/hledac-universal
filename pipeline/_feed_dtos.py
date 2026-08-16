"""Feed DTOs — Sprint C3 refactor.

Split from live_feed_pipeline.py (3 208 LOC).
- FeedQualityMetrics: kvalita + adapter bands





- FeedFallbackMetrics: fallback_useful, fallback_waste, decision
- FeedEconomicsVerdict: rich_ratio, squandered_high_usefulness
- FeedTelemetry: signal_stage, sample_texts, zero_signal_reason

All DTOs are msgspec.Struct(frozen=True) — 5-7x faster instantiation
than @dataclass(slots=True), frozen=True by default for hashability.

Decision trees moved to Rust: feed_decision_classify, feed_stage_diagnose,
feed_branch_verdict (pure functions, no I/O, called 1000+ times/sprint).
"""

from __future__ import annotations

from typing import Any

from hledac.universal.compat.msgspec_gc_compat import Struct, field as msgspec_field

# === Feed Quality Metrics ===

class FeedQualityMetrics(Struct, frozen=True):
    """Kvalita + adapter bands — extracted from FeedIngestContext."""

    signal_stage: str = "unknown"
    quality_band: str = "unknown"
    adapter_source_priority_bias: float = 0.0
    adapter_metadata_richness_band: str = "unknown"
    adapter_entry_usefulness_band: str = "unknown"
    assembled_text_chars_total: int = 0
    avg_assembled_text_len: float = 0.0
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    total_pattern_hits: int = 0
    findings_built_pre_store: int = 0
    patterns_configured: int = 0
    findings_lost_to_dedup: int = 0
    findings_lost_to_dedup_total: int = 0
    metadata_boost: bool = False
    language_mismatch: bool = False
    temporal_feed_vocabulary_mismatch: bool = False


# === Feed Fallback Metrics ===

class FeedFallbackMetrics(Struct, frozen=True):
    """Fallback economics — extracted from FeedIngestContext.

    decision_reason: canonical reason tag for fallback decision.
    should_fetch: True if article fetch should be attempted.
    forced: True if decision was forced by metadata/content mismatch.
    wasted: True if fallback was attempted but feed-native already had hits.
    helpful: True if fallback produced findings that feed-native did not.
    skip_because: reason string if fallback was skipped.
    """

    fallback_useful: int = 0
    fallback_waste: int = 0
    fallback_useful_count: int = 0
    fallback_waste_count: int = 0
    decision_reason: str = "undecided"
    should_fetch: bool = False
    forced: bool = False
    wasted: bool = False
    helpful: bool = False
    skip_because: str = ""
    entries_with_rich_feed_content: int = 0
    entries_with_article_fallback: int = 0
    article_fallback_fetch_attempts: int = 0
    article_fallback_fetch_successes: int = 0
    enriched_text_chars_total: int = 0
    avg_enriched_text_len: float = 0.0
    enrichment_phase_used: str = "none"
    findings_from_rich_feed: int = 0
    findings_from_fallback: int = 0
    pre_fallback_hits_total: int = 0
    post_fallback_hits_total: int = 0
    feed_branch_signal_present: bool = False
    squandered_high_usefulness_entries: int = 0
    metadata_strong_but_content_weak: int = 0
    low_trust_feed_hits: int = 0
    feed_native_yield_ratio: float = 0.0
    fallback_value_ratio: float = 0.0


# === Feed Economics Verdict ===

class FeedEconomicsVerdict(Struct, frozen=True):
    """Rich ratio + squandered_high_usefulness — extracted from FeedIngestContext."""

    verdict_tag: str = "no_signal"  # "feed_lean" | "fallback_lean" | "balanced" | "no_signal"
    rich_ratio: float = 0.0
    squandered_high_usefulness: int = 0
    feed_corroborates: bool = False
    feed_burns_budget: bool = False
    feed_next_action: str = "unknown"  # "continue_feed" | "fallback_more" | "reassess_feed" | "stop"
    feed_confidence_note: str = ""
    feed_confidence_score: int = 0  # 0-100
    high_usefulness_waste_rate: float = 0.0
    metadata_strong_content_weak: int = 0
    low_trust_feed_hits: int = 0
    entries_with_hits: int = 0
    entries_seen: int = 0
    feed_economics_tuple: tuple[str, int, int, int, int] = ("", 0, 0, 0, 0)
    winning_source_breakdown: dict[str, int] = msgspec_field(default_factory=dict)


# === Feed Telemetry ===

class FeedTelemetry(Struct, frozen=True):
    """Signal stage + samples + zero_signal_reason — extracted from FeedIngestContext."""

    signal_stage: str = "unknown"
    zero_signal_reason: str | None = None
    sample_scanned_texts: tuple[str, ...] = ()
    sample_hit_counts: tuple[int, ...] = ()
    sample_hit_labels_union: tuple[str, ...] = ()
    sample_texts_truncated: bool = False
    sample_enriched_texts: tuple[str, ...] = ()
    feed_content_mismatch: bool = False
    upstream_fetch_blocker: str | None = None
    upstream_parse_blocker: str | None = None
    source_accessibility_blocker: str | None = None
    root_zero_yield_reason: str | None = None
    had_substantive_content_but_no_hits: bool = False
    zero_hit_feed_fetch_count: int = 0
    zero_hit_feed_fetch_reasons: dict[str, int] = msgspec_field(default_factory=dict)
    zero_hit_feed_fetch_samples: tuple[tuple[str, str], ...] = ()
    feed_url: str = ""
    raw_count: int = 0
    built_count: int = 0
    max_entries: int = 20
    max_bytes: int = 2_000_000
    timeout_s: float = 35.0
    sprint_id: str = ""
    assembly_tier: str = "unknown"
    feed_branch_verdict: dict[str, Any] = msgspec_field(default_factory=dict)


# === Pure decision functions (candidates for Rust migration) ===
# Constants shared between Python fallback classifiers and Rust implementation
_MIN_ARTICLE_FALLBACK_CHARS: int = 150


def classify_fallback_decision_python(
    assembled_text_len: int,
    pre_fallback_hits_count: int,
    quality_band: str,
    metadata_boost: bool,
    language_mismatch: bool,
    article_fallback_used: bool,
    article_fallback_attempted: bool,
    post_fallback_findings_count: int,
    adapter_source_priority_bias: float,
    adapter_metadata_richness_band: str,
) -> tuple[str, bool, bool, bool, bool, str]:
    """Python fallback of Rust feed_decision_classify.

    Returns: (reason, should_fetch, forced, wasted, helpful, skip_because)
    Decision tree (in priority order):
    1. If pre-fallback hits exist → wasteful fallback
    2. If article fallback was skipped due to quality → skip_because set
    3. If fallback was forced by metadata/content mismatch → forced=True
    4. If fallback was skipped because high-quality assembled text → skip_because
    5. If fallback produced new findings → helpful=True
    6. If fallback was attempted but produced no new findings → wasted
    7. Otherwise → undecided
    """
    if pre_fallback_hits_count > 0:
        return (
            "feed_native_had_signal",
            False,
            False,
            True,
            False,
            "feed-native already carried hits",
    )

    if not article_fallback_attempted:
        if (
            assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS
            and quality_band in ("high", "medium")
        ):
            return (
                "skipped_high_quality",
                False,
                False,
                False,
                False,
                f"high quality ({quality_band}), assembled {assembled_text_len} chars",
    )
        if (
            adapter_source_priority_bias >= 0.1
            and assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS
        ):
            return (
                "skipped_adapter_bias",
                False,
                False,
                False,
                False,
                f"adapter source_priority_bias={adapter_source_priority_bias:.2f}",
    )
        return (
            "no_fetch_warranted",
            False,
            False,
            False,
            False,
            f"assembled={assembled_text_len}, quality={quality_band}",
    )

    if (
        metadata_boost
        and not language_mismatch
        and assembled_text_len < _MIN_ARTICLE_FALLBACK_CHARS
    ):
        if post_fallback_findings_count > 0:
            return ("forced_metadata_mismatch", True, True, False, True, "")
        return ("forced_no_yield", True, True, True, False, "")

    if (
        assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS
        and quality_band == "low"
    ):
        if post_fallback_findings_count > 0:
            return ("aged_structured_yield", True, True, False, True, "")
        return ("aged_structured_no_yield", True, True, True, False, "")

    if (
        adapter_metadata_richness_band == "high"
        and assembled_text_len < _MIN_ARTICLE_FALLBACK_CHARS
    ):
        if post_fallback_findings_count > 0:
            return ("forced_adapter_metadata", True, True, False, True, "")
        return ("forced_adapter_no_yield", True, True, True, False, "")

    if post_fallback_findings_count > 0:
        return ("normal_fallback_yield", True, False, False, True, "")
    return ("normal_fallback_no_yield", True, False, False, False, "")


def diagnose_signal_stage_python(
    entries_seen: int,
    entries_with_empty_assembled_text: int,
    entries_scanned: int,
    entries_with_hits: int,
    findings_built_pre_store: int,
    patterns_configured: int,
    findings_lost_to_dedup_total: int = 0,
) -> str:
    """Python fallback of Rust feed_stage_diagnose.

    Returns one of:
      empty_registry           — no patterns configured at all
      empty_fetch              — no entries arrived at all
      content_empty            — entries arrived but assembled text was empty
      no_pattern_hits          — entries with text arrived but no pattern matched
      no_pattern_hits_with_content — entries with content, no hits
      findings_build_loss      — hits existed but all were deduped away
      prestore_findings_present — findings exist pre-store
      unknown                  — counters not yet populated
    """
    if patterns_configured == 0:
        return "empty_registry"
    if entries_seen == 0:
        return "empty_fetch"
    if entries_with_empty_assembled_text > 0 and entries_scanned == 0:
        return "content_empty"
    if entries_scanned == 0:
        return "no_pattern_hits"
    if findings_built_pre_store == 0 and findings_lost_to_dedup_total > 0:
        return "findings_build_loss"
    if entries_with_hits == 0:
        return "no_pattern_hits_with_content"
    if findings_built_pre_store > 0:
        return "prestore_findings_present"
    return "unknown"


def compute_feed_branch_hint_python(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    entries_with_hits: int,
) -> str:
    """Python fallback of Rust feed_branch_hint."""
    if entries_with_hits == 0:
        return "unknown"
    if feed_signal_present and fallback_waste == 0:
        return "feed_strong"
    if feed_signal_present and fallback_waste > 0 and fallback_useful == 0:
        return "feed_weak"
    if fallback_useful > 0 and findings_fallback > 0:
        return "fallback_valuable"
    if feed_signal_present or fallback_useful > 0:
        return "mixed"
    return "unknown"


def compute_feed_economics_verdict_python(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
) -> tuple[str, int, int, int, int]:
    """Python fallback of Rust feed_economics_verdict."""
    total_findings = findings_rich + findings_fallback
    if total_findings == 0:
        return ("no_signal", int(feed_signal_present), fallback_useful, fallback_waste, 0)

    rich_ratio = findings_rich / total_findings if total_findings > 0 else 0.0
    waste_ratio = (
        fallback_waste / (fallback_useful + fallback_waste)
        if (fallback_useful + fallback_waste) > 0
        else 0.0
    )

    if rich_ratio >= 0.7:
        verdict_tag = "feed_lean"
    elif rich_ratio <= 0.3:
        verdict_tag = "fallback_lean"
    else:
        verdict_tag = "balanced"

    quality = int(rich_ratio * 100 * (1.0 - waste_ratio * 0.5))
    return (verdict_tag, int(feed_signal_present), fallback_useful, fallback_waste, quality)


def compute_feed_branch_verdict_python(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    squandered_high_usefulness: int,
    metadata_strong_but_content_weak: int,
    low_trust_feed_hits: int,
    total_entries_with_hits: int,
    entries_seen: int,
    feed_native_yield_ratio: float,
    fallback_value_ratio: float,
) -> dict[str, Any]:
    """Python fallback of Rust feed_branch_verdict."""
    total_findings = findings_rich + findings_fallback
    verdict: dict[str, Any] = {
        "verdict_tag": "no_signal",
        "feed_native_yield": findings_rich,
        "fallback_yield": findings_fallback,
        "total_yield": total_findings,
        "squandered_high_usefulness_entries": squandered_high_usefulness,
        "unnecessary_fallbacks": fallback_waste,
        "useful_fallbacks": fallback_useful,
        "feed_corroborates": feed_signal_present and fallback_useful > 0,
        "feed_burns_budget": fallback_waste > 0 and findings_rich == 0,
        "feed_next_action": "unknown",
        "feed_confidence_note": "",
        "feed_confidence_score": 0,
        "feed_native_yield_ratio": feed_native_yield_ratio,
        "fallback_value_ratio": fallback_value_ratio,
        "high_usefulness_waste_rate": 0.0,
        "metadata_strong_content_weak": metadata_strong_but_content_weak,
        "low_trust_feed_hits": low_trust_feed_hits,
        "entries_with_hits": total_entries_with_hits,
        "entries_seen": entries_seen,
    }

    if total_findings == 0:
        verdict["verdict_tag"] = "no_signal"
        verdict["feed_next_action"] = "reassess_feed"
        verdict["feed_confidence_note"] = "no findings in either branch"
        verdict["feed_confidence_score"] = 0
        return verdict

    if squandered_high_usefulness + fallback_waste > 0:
        waste_denom = squandered_high_usefulness + fallback_waste
        verdict["high_usefulness_waste_rate"] = fallback_waste / waste_denom

    rich_ratio = feed_native_yield_ratio
    if rich_ratio >= 0.7:
        verdict["verdict_tag"] = "feed_lean"
    elif rich_ratio <= 0.3:
        verdict["verdict_tag"] = "fallback_lean"
    else:
        verdict["verdict_tag"] = "balanced"

    verdict["feed_corroborates"] = feed_signal_present and fallback_useful > 0
    verdict["feed_burns_budget"] = fallback_waste > 0 and findings_rich == 0

    if not feed_signal_present and fallback_useful == 0:
        verdict["feed_next_action"] = "reassess_feed"
        verdict["feed_confidence_note"] = "neither branch produced signal"
    elif verdict["feed_burns_budget"]:
        verdict["feed_next_action"] = "fallback_more"
        verdict["feed_confidence_note"] = "feed burns budget; rely on fallback"
    elif verdict["feed_corroborates"]:
        verdict["feed_next_action"] = "continue_feed"
        verdict["feed_confidence_note"] = "both branches contribute; feed is valuable"
    elif feed_signal_present and fallback_useful == 0:
        verdict["feed_next_action"] = "continue_feed"
        verdict["feed_confidence_note"] = "feed-native only; fallback not needed"
    else:
        verdict["feed_next_action"] = "reassess_feed"
        verdict["feed_confidence_note"] = "mixed signals; review feed quality"

    confidence = int(rich_ratio * 100 * (1.0 - verdict["high_usefulness_waste_rate"] * 0.5))
    verdict["feed_confidence_score"] = max(0, min(100, confidence))

    return verdict


# === Try to import Rust-accelerated versions ===
try:
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal._core.rust_backend import rust
    _rust = rust.raw.module

    if hasattr(_rust, "feed_decision_classify"):
        _HAS_RUST_FEED_DECISION = True
    else:
        _HAS_RUST_FEED_DECISION = False
except Exception:
    _HAS_RUST_FEED_DECISION = False


import sys as _sys
from _core import aclose


def _make_rust_wrapper(rust_fn_name: str) -> Any:
    """Create a Rust/Python wrapper for feed decision functions.

    Reduces 5 identical wrapper functions to one factory call.
    """
    python_fn_name = f"{rust_fn_name}_python"

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _HAS_RUST_FEED_DECISION:
            return getattr(_rust, rust_fn_name)(*args, **kwargs)
        return getattr(_sys.modules[__name__], python_fn_name)(*args, **kwargs)

    wrapper.__name__ = rust_fn_name
    wrapper.__doc__ = "Wrapper: use Rust if available, Python fallback otherwise."
    return wrapper


classify_fallback_decision = _make_rust_wrapper("feed_decision_classify")
diagnose_signal_stage = _make_rust_wrapper("feed_stage_diagnose")
compute_feed_branch_hint = _make_rust_wrapper("feed_branch_hint")
compute_feed_economics_verdict = _make_rust_wrapper("feed_economics_verdict")
compute_feed_branch_verdict = _make_rust_wrapper("feed_branch_verdict")


# === Fallback Decision (moved from live_feed_pipeline.py) ===

class FallbackDecision(msgspec.Struct, frozen=True, gc=False):
    """Structured fallback decision output.

    reason: canonical reason tag for the decision
    should_fetch: True if article fetch should be attempted
    forced: True if decision was forced by metadata/content mismatch
    wasted: True if fallback was attempted but feed-native already had hits
    helpful: True if fallback produced findings that feed-native did not
    skip_because: reason string if fallback was skipped
    """

    reason: str = "undecided"
    should_fetch: bool = False
    forced: bool = False
    wasted: bool = False
    helpful: bool = False
    skip_because: str = ""
