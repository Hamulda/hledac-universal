"""
Sprint 8AN: Live RSS/Atom feed pipeline v2 — pattern-backed findings.

feed_url -> 8AF fetch+parse -> entry normalization
    -> HTML->text (word-boundary safe, entity-safe)
    -> pattern scan via PatternMatcher (offloaded, bounded concurrency)
    -> CanonicalFinding per PatternHit
    -> storage

Public API:
    async_run_live_feed_pipeline()
    FeedPipelineEntryResult, FeedPipelineRunResult

Invariants:
- Public/passive-only, no AO, no LLM
- store=None is valid no-op
- PatternMatcher is SSOT — no regex fallback
- Empty matcher registry = valid zero-findings state
- source_type = "rss_atom_pipeline", confidence = 0.8
- Deterministic finding_id via sha256 (no hash())
- payload_text = short context around hit (200 char radius)
- Per-entry dedup by (label, pattern, value) preserve-first
- Per-run dedup by entry_url
- HTML->text: strip script/style first, tag→space, then unescape
- Pattern scan offloaded via asyncio.to_thread + shared semaphore (max 4)
- PatternMatcher case-insensitive (matcher handles .lower() internally)
- entry_hash in FeedEntryHit for future dedup
-UMA emergency -> fail-soft abort
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import msgspec

from hledac.universal.pipeline.scoring import (
    ASSEMBLY_TIER_NO_CONTENT,
    ASSEMBLY_TIER_RICH_CONTENT,
    ASSEMBLY_TIER_SUMMARY_ONLY,
    ASSEMBLY_TIER_TITLE_ONLY,
    EntryQualitySignal,
    _assemble_clean_feed_text,
    _assemble_enriched_feed_text,
    _classify_assembly_substance,
    _compute_entry_quality_signal,
    _convert_rich_html_to_text,
    _entry_payload_text,
    _strip_html_tags_from_text,
)

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import (
        CanonicalFinding,
        DuckDBShadowStore,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FEED_TEXT_CHARS: int = 4000
FEED_PAYLOAD_CONTEXT_CHARS: int = 200
MAX_FEED_PATTERN_TASKS: int = 4

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Patchable symbol for pattern offload (tests patch this, not asyncio.to_thread)
# ---------------------------------------------------------------------------

_ASYNC_PATTERN_OFFLOAD: Any = asyncio.to_thread

# ---------------------------------------------------------------------------
# Shared semaphore for bounded pattern offload concurrency
# ---------------------------------------------------------------------------

_pattern_semaphore: asyncio.Semaphore | None = None


def _get_pattern_offload_semaphore() -> asyncio.Semaphore:
    """Return the shared module-level semaphore for pattern offload concurrency."""
    global _pattern_semaphore
    if _pattern_semaphore is None:
        _pattern_semaphore = asyncio.Semaphore(MAX_FEED_PATTERN_TASKS)
    return _pattern_semaphore


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class FeedPipelineEntryResult(msgspec.Struct, frozen=True, gc=False):
    """Result for a single feed entry."""
    entry_url: str
    accepted_findings: int
    stored_findings: int
    error: str | None = None
    # F188D: assembly quality transparency for zero-finding diagnosis
    assembly_tier: str = ""           # "no_content" | "title_only" | "summary_only" | "rich_content"
    quality_reason_tag: str = ""      # comma-separated: "author_present" | "feed_title_context" | "language_match" | "title_only" | etc.


class FeedPipelineRunResult(msgspec.Struct, frozen=True, gc=False):
    """Result for a full feed pipeline run."""
    feed_url: str
    fetched_entries: int
    accepted_findings: int = 0
    stored_findings: int = 0
    patterns_configured: int = 0
    matched_patterns: int = 0
    pages: tuple[FeedPipelineEntryResult, ...] = ()
    error: str | None = None
    # Sprint 8AU: pre-store observability
    entries_seen: int = 0
    entries_with_empty_assembled_text: int = 0
    entries_with_text: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    total_pattern_hits: int = 0
    findings_built_pre_store: int = 0
    assembled_text_chars_total: int = 0
    avg_assembled_text_len: float = 0.0
    signal_stage: str = "unknown"
    # Sprint F159: zero-signal surfacing — derived, not persisted
    zero_signal_reason: str | None = None
    # Sprint 8BC: bounded sample capture (first 3 entries, truncated to 160 chars)
    sample_scanned_texts: tuple[str, ...] = ()
    sample_hit_counts: tuple[int, ...] = ()
    sample_hit_labels_union: tuple[str, ...] = ()
    sample_texts_truncated: bool = False
    feed_content_mismatch: bool = False
    # Sprint 8BE: source-specific text enrichment
    entries_with_rich_feed_content: int = 0
    entries_with_article_fallback: int = 0
    article_fallback_fetch_attempts: int = 0
    article_fallback_fetch_successes: int = 0
    enriched_text_chars_total: int = 0
    avg_enriched_text_len: float = 0.0
    sample_enriched_texts: tuple[str, ...] = ()
    enrichment_phase_used: str = "none"   # "feed_rich_content" / "article_fallback" / "mixed"
    temporal_feed_vocabulary_mismatch: bool = False
    # Sprint F150I: feed economics verdicts
    feed_branch_signal_present: bool = False        # True if >=1 entry had feed-native hits (no fallback needed)
    fallback_useful_count: int = 0                  # Fallback entries that produced new findings vs no-signal fallbacks
    fallback_waste_count: int = 0                   # Fallback entries where feed-native already had signal (unnecessary)
    findings_from_rich_feed: int = 0                 # Findings where feed-native content carried the hit
    findings_from_fallback: int = 0                  # Findings where article fallback was the winning source
    feed_branch_hint: str = "unknown"                # "feed_strong" | "feed_weak" | "mixed" | "unknown" — next-sprint signal
    # Sprint F150I: condensed economics verdict (analogous to public branch economics)
    feed_economics_verdict: tuple[str, int, int, int, int] = ("", 0, 0, 0, 0)
    # (verdict_tag, feed_branch_signal_present_int, fallback_useful, fallback_waste, feed_signal_quality)
    # Sprint F150J: dict-style additive feed branch verdict
    feed_branch_verdict: dict[str, Any] = dict()
    # Sprint F150J: derived feed counters with real scheduling value
    squandered_high_usefulness_entries: int = 0        # fallback attempted on entries that had high-usefulness but no hits
    fallback_value_ratio: float = 0.0                  # fallback_useful / max(1, fallback_useful + fallback_waste)
    feed_native_yield_ratio: float = 0.0               # findings_rich / max(1, findings_rich + findings_fallback)
    metadata_strong_but_content_weak: int = 0           # entries where metadata_boost=True but assembled_text < threshold
    low_trust_feed_hits: int = 0                        # feed-native hits on entries with low quality_band
    feed_next_action: str = "unknown"                   # "continue_feed" | "fallback_more" | "reassess_feed" | "stop"
    feed_confidence_note: str = ""                       # human-readable confidence annotation
    # Sprint F151A: surf feed_confidence_score from verdict dict into flat field
    feed_confidence_score: int = 0                       # 0-100, adapter-informed confidence
    # Sprint F151A: winning source breakdown for scheduler/exporter
    winning_source_breakdown: dict[str, int] = dict()     # {"feed_native": N, "fallback": N, "mixed": N}
    # Sprint F169D: root-cause propagation into FeedPipelineRunResult
    upstream_fetch_blocker: str | None = None       # "http_error" | "timeout" | "dns_failure" | "connection_error" | "robots_blocked"
    upstream_parse_blocker: str | None = None        # "malformed_xml" | "wrong_content_type" | "redirected_non_feed"
    source_accessibility_blocker: str | None = None  # source-level fetch failure label
    root_zero_yield_reason: str | None = None       # canonical root cause of zero findings
    had_substantive_content_but_no_hits: bool = False  # True if entries_with_text > 0 but findings == 0
    # Sprint F160A: hits that arrived but were filtered by per-entry dedup
    findings_lost_to_dedup: int = 0
    # F185A DF-2: pre/post fallback hit count totals at run level
    pre_fallback_hits_total: int = 0
    post_fallback_hits_total: int = 0
    # F185A DF-6: structured zero-hit evidence surface (mirrors live_public_pipeline.py)
    zero_hit_feed_fetch_count: int = 0       # entries fetched with 0 matched patterns
    zero_hit_feed_fetch_reasons: dict = {}   # quality_reason_tag -> count
    zero_hit_feed_fetch_samples: tuple = ()  # (title, url) pairs, max 5


# ---------------------------------------------------------------------------
# Pre-store signal diagnosis helper (Sprint 8AU)
# ---------------------------------------------------------------------------


# ==============================================================================
# Fallback decision classifier — Sprint F160A consolidation
# Replaces 5+ scattered booleans with a single structured decision tree
# ==============================================================================

class FallbackDecision(msgspec.Struct, frozen=True, gc=False):
    """
    Structured fallback decision output.

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


def _classify_fallback_decision(
    assembled_text_len: int,
    pre_fallback_hits_count: int,
    quality_signal: EntryQualitySignal,
    article_fallback_used: bool,
    article_fallback_attempted: bool,
    post_fallback_findings_count: int,
    adapter_source_priority_bias: float,
    adapter_metadata_richness_band: str,
    adapter_entry_usefulness_band: str,
) -> FallbackDecision:
    """
    Classify the fallback decision outcome with a single structured output.

    Decision tree (in priority order):
    1. If pre-fallback hits exist → fallback was wasteful (wasted=True)
    2. If article fallback was skipped due to quality → skip_because set
    3. If fallback was forced by metadata/content mismatch → forced=True
    4. If fallback was skipped because high-quality assembled text → skip_because
    5. If fallback produced new findings → helpful=True
    6. If fallback was attempted but produced no new findings → wasted
    7. Otherwise → undecided
    """
    # Case 1: pre-fallback hits exist → wasteful fallback
    if pre_fallback_hits_count > 0:
        return FallbackDecision(
            reason="feed_native_had_signal",
            should_fetch=False,
            wasted=True,
            helpful=False,
            skip_because="feed-native already carried hits",
        )

    # Case 2: article fallback was not attempted — classify why
    if not article_fallback_attempted:
        # High-quality assembled text above threshold — skip was correct
        if assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS and quality_signal.quality_band in ("high", "medium"):
            return FallbackDecision(
                reason="skipped_high_quality",
                should_fetch=False,
                forced=False,
                wasted=False,
                helpful=False,
                skip_because=f"high quality ({quality_signal.quality_band}), assembled {assembled_text_len} chars",
            )
        # Adapter override: high source priority bias skips even medium quality
        if adapter_source_priority_bias >= 0.1 and assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS:
            return FallbackDecision(
                reason="skipped_adapter_bias",
                should_fetch=False,
                forced=False,
                wasted=False,
                helpful=False,
                skip_because=f"adapter source_priority_bias={adapter_source_priority_bias:.2f}",
            )
        # Unknown / no signal possible
        return FallbackDecision(
            reason="no_fetch_warranted",
            should_fetch=False,
            forced=False,
            wasted=False,
            helpful=False,
            skip_because=f"assembled={assembled_text_len}, quality={quality_signal.quality_band}",
        )

    # Case 3: fallback was forced by metadata/content mismatch
    if (
        quality_signal.metadata_boost
        and not quality_signal.language_mismatch
        and assembled_text_len < _MIN_ARTICLE_FALLBACK_CHARS
    ):
        # Forced fallback — assess outcome
        if post_fallback_findings_count > 0:
            return FallbackDecision(
                reason="forced_metadata_mismatch",
                should_fetch=True,
                forced=True,
                wasted=False,
                helpful=True,
            )
        else:
            return FallbackDecision(
                reason="forced_no_yield",
                should_fetch=True,
                forced=True,
                wasted=True,
                helpful=False,
            )

    # Case 4: aged but structured entry (low quality but above threshold)
    if (
        assembled_text_len >= _MIN_ARTICLE_FALLBACK_CHARS
        and quality_signal.quality_band == "low"
    ):
        if post_fallback_findings_count > 0:
            return FallbackDecision(
                reason="aged_structured_yield",
                should_fetch=True,
                forced=True,
                wasted=False,
                helpful=True,
            )
        else:
            return FallbackDecision(
                reason="aged_structured_no_yield",
                should_fetch=True,
                forced=True,
                wasted=True,
                helpful=False,
            )

    # Case 5: adapter-mandated fallback (high metadata richness band, weak content)
    if adapter_metadata_richness_band == "high" and assembled_text_len < _MIN_ARTICLE_FALLBACK_CHARS:
        if post_fallback_findings_count > 0:
            return FallbackDecision(
                reason="forced_adapter_metadata",
                should_fetch=True,
                forced=True,
                wasted=False,
                helpful=True,
            )
        else:
            return FallbackDecision(
                reason="forced_adapter_no_yield",
                should_fetch=True,
                forced=True,
                wasted=True,
                helpful=False,
            )

    # Case 6: normal below-threshold fallback
    if post_fallback_findings_count > 0:
        return FallbackDecision(
            reason="normal_fallback_yield",
            should_fetch=True,
            forced=False,
            wasted=False,
            helpful=True,
        )
    else:
        return FallbackDecision(
            reason="normal_fallback_no_yield",
            should_fetch=True,
            forced=False,
            wasted=False,
            helpful=False,
        )


def diagnose_feed_signal_stage(
    entries_seen: int,
    entries_with_empty_assembled_text: int,
    entries_scanned: int,
    entries_with_hits: int,
    findings_built_pre_store: int,
    patterns_configured: int,
    findings_lost_to_dedup_total: int = 0,
) -> str:
    """
    Diagnose which stage the signal is lost at.

    Returns one of:
      empty_registry           — no patterns configured at all
      empty_fetch              — no entries arrived at all
      content_empty            — entries arrived but assembled text was empty (all tiers title_only or no_content)
      no_pattern_hits          — entries with text arrived but no pattern matched
      no_pattern_hits_with_content — entries with content, no hits (substance tier above title_only)
      findings_build_loss      — hits existed but all were deduped away
      prestore_findings_present — findings exist pre-store
      unknown                  — counters not yet populated

    Findings-build loss is now distinguishable from pure no-hits:
      - no_pattern_hits_with_content: text was scanned, substance was present, no hits arrived
      - findings_build_loss: hits arrived but were filtered by per-entry dedup
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
        # Had hits but they were all lost to dedup — distinct from no-hits-with-content
        return "findings_build_loss"
    if entries_with_hits == 0:
        # Entries had content (scanned) but no hits arrived
        return "no_pattern_hits_with_content"
    if findings_built_pre_store > 0:
        return "prestore_findings_present"
    return "unknown"


# Sprint F150I: feed economics verdict helpers


def _compute_feed_branch_hint(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    entries_with_hits: int,
) -> str:
    """
    Compute a hint for next sprint about feed branch quality.
    """
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


def _compute_feed_economics_verdict(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
) -> tuple[str, int, int, int, int]:
    """
    Compute condensed economics verdict for the run.
    Returns (verdict_tag, feed_signal_int, fallback_useful, fallback_waste, feed_signal_quality).
    verdict_tag: "feed_lean" | "fallback_lean" | "balanced" | "no_signal"
    """
    total_findings = findings_rich + findings_fallback
    if total_findings == 0:
        return ("no_signal", int(feed_signal_present), fallback_useful, fallback_waste, 0)

    rich_ratio = findings_rich / total_findings if total_findings > 0 else 0.0
    waste_ratio = fallback_waste / (fallback_useful + fallback_waste) if (fallback_useful + fallback_waste) > 0 else 0.0

    if rich_ratio >= 0.7:
        verdict_tag = "feed_lean"
    elif rich_ratio <= 0.3:
        verdict_tag = "fallback_lean"
    else:
        verdict_tag = "balanced"

    # Signal quality: 0-100 based on feed-native hit rate and waste ratio
    quality = int(rich_ratio * 100 * (1.0 - waste_ratio * 0.5))

    return (verdict_tag, int(feed_signal_present), fallback_useful, fallback_waste, quality)


# Sprint F150J: dict-style additive feed branch verdict


def _compute_feed_branch_verdict(
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
    """
    Compute a rich dict-style verdict for feed branch economics.

    Provides actionable signals for scheduler/exporter:
    - feed-native yield vs fallback yield breakdown
    - wasted high-usefulness entries count
    - unnecessary fallback count
    - whether feed branch corroborates or burns fetch budget
    - next action recommendation
    - confidence annotation
    """
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

    # Waste rate for high-usefulness entries
    total_fallbacks = fallback_useful + fallback_waste
    if squandered_high_usefulness + fallback_waste > 0:
        waste_denom = squandered_high_usefulness + fallback_waste
        verdict["high_usefulness_waste_rate"] = fallback_waste / waste_denom

    # Verdict tag
    rich_ratio = feed_native_yield_ratio
    if rich_ratio >= 0.7:
        verdict["verdict_tag"] = "feed_lean"
    elif rich_ratio <= 0.3:
        verdict["verdict_tag"] = "fallback_lean"
    else:
        verdict["verdict_tag"] = "balanced"

    # Feed corroborates: feed had hits AND fallback contributed something
    verdict["feed_corroborates"] = feed_signal_present and fallback_useful > 0
    # Feed burns budget: waste > 0 AND feed contributed nothing
    verdict["feed_burns_budget"] = fallback_waste > 0 and findings_rich == 0

    # Next action
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

    # Confidence score
    confidence = int(rich_ratio * 100 * (1.0 - verdict["high_usefulness_waste_rate"] * 0.5))
    verdict["feed_confidence_score"] = max(0, min(100, confidence))

    return verdict


def _compute_feed_next_action_and_confidence(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    squandered_high_usefulness: int,
    metadata_strong_but_content_weak: int,
    low_trust_feed_hits: int,
) -> tuple[str, str]:
    """Compute feed_next_action and feed_confidence_note directly."""
    total_findings = findings_rich + findings_fallback
    if total_findings == 0:
        return ("reassess_feed", "no findings in either branch")
    if fallback_waste > 0 and findings_rich == 0:
        return ("fallback_more", "feed burns budget; rely on fallback")
    if feed_signal_present and fallback_useful > 0:
        return ("continue_feed", "both branches contribute; feed is valuable")
    if feed_signal_present and fallback_useful == 0:
        return ("continue_feed", "feed-native only; fallback not needed")
    if squandered_high_usefulness > 0:
        return ("reassess_feed", f"{squandered_high_usefulness} high-usefulness entries squandered")
    if metadata_strong_but_content_weak > 0:
        return ("fallback_more", f"{metadata_strong_but_content_weak} entries: strong metadata but weak content")
    if low_trust_feed_hits > 0:
        return ("reassess_feed", f"{low_trust_feed_hits} low-trust feed hits; quality uncertain")
    return ("reassess_feed", "mixed signals; review feed quality")


# Sprint F151A: winning source breakdown helper


def _float_attr(obj: object, name: str, default: float) -> float:
    """Get a float attribute from an object with MagicMock safety."""
    val = getattr(obj, name, default)
    if isinstance(val, (int, float)):
        return float(val)
    return default


def _str_attr(obj: object, name: str, default: str) -> str:
    """Get a string attribute from an object with MagicMock safety."""
    val = getattr(obj, name, default)
    if isinstance(val, str):
        return val
    return default


def _compute_winning_source_breakdown(
    feed_native_signal_carried: bool,
    article_fallback_used: bool,
    findings: list[dict],
    adapter_selection_reason: str,
) -> dict[str, int]:
    """
    Breakdown of which source layer produced the winning findings.

    Fallback is 'mixed' when article fallback was used alongside existing feed-native signal
    (both contributed to findings). 'feed_native' when only feed-native had hits.
    'fallback' when only fallback produced findings.

    adapter_selection_reason is used fail-soft to annotate mixed cases.
    """
    breakdown: dict[str, int] = {"feed_native": 0, "fallback": 0, "mixed": 0}

    if not findings:
        return breakdown

    if feed_native_signal_carried and article_fallback_used:
        breakdown["mixed"] = len(findings)
    elif feed_native_signal_carried:
        breakdown["feed_native"] = len(findings)
    elif article_fallback_used:
        breakdown["fallback"] = len(findings)
    else:
        # Neither — shouldn't happen, but count as feed_native by convention
        breakdown["feed_native"] = len(findings)

    return breakdown


def _compute_adapter_adjusted_confidence(
    base_confidence_score: int,
    adapter_source_priority_bias: float,
    adapter_timestamp_reliability: float,
    adapter_metadata_richness_band: str,
    adapter_entry_usefulness_band: str,
    adapter_selection_reason: str,
    feed_native_signal_carried: bool,
) -> int:
    """
    Fail-soft adjustment of feed_confidence_score using adapter-derived signals.

    adapter_selection_reason is used fail-soft: if it contains keywords like
    "curated", "priority", "high" it adds a small boost; if it contains
    "fallback", "retry", "low" it reduces confidence slightly.
    """
    adjusted = base_confidence_score

    # Source priority bias: +5 bonus per 0.1 of bias (capped at +20)
    if adapter_source_priority_bias > 0:
        bias_bonus = int(adapter_source_priority_bias * 50)
        adjusted += min(bias_bonus, 20)

    # Timestamp reliability: +10 bonus if high reliability (>0.7)
    if adapter_timestamp_reliability > 0.7:
        adjusted += 10

    # Metadata richness: +10 if "high"
    if adapter_metadata_richness_band == "high":
        adjusted += 10

    # Entry usefulness: +5 if "high"
    if adapter_entry_usefulness_band == "high":
        adjusted += 5

    # Selection reason keywords — small positive/negative adjustments
    if adapter_selection_reason:
        reason_lower = adapter_selection_reason.lower()
        positive_keywords = ("curated", "priority", "high", "authoritative", "manual")
        negative_keywords = ("fallback", "retry", "low", "unknown", "derived")
        for kw in positive_keywords:
            if kw in reason_lower:
                adjusted += 5
                break
        for kw in negative_keywords:
            if kw in reason_lower:
                adjusted -= 5
                break

    # If feed-native signal carried hits, give a small additional nudge
    if feed_native_signal_carried:
        adjusted += 5

    return max(0, min(100, adjusted))


# ---------------------------------------------------------------------------
# Batch DTOs (Sprint 8AL)
# ---------------------------------------------------------------------------

class FeedSourceRunResult(msgspec.Struct, frozen=True, gc=False):
    """Result for a single feed source run within a batch."""
    feed_url: str
    label: str
    origin: str
    priority: int
    fetched_entries: int
    accepted_findings: int
    stored_findings: int
    elapsed_ms: float = 0.0
    error: str | None = None
    signal_stage: str = "unknown"
    # F164C: per-source dedup loss counter
    findings_lost_to_dedup: int = 0


class FeedSourceBatchRunResult(msgspec.Struct, frozen=True, gc=False):
    """Result for a multi-feed source batch run."""
    total_sources: int
    completed_sources: int
    fetched_entries: int
    accepted_findings: int
    stored_findings: int
    sources: tuple[FeedSourceRunResult, ...]
    error: str | None = None
    # Sprint 8BE Phase 3: dominant signal stage across all sources (mode)
    dominant_signal_stage: str = "unknown"
    # Sprint F164C: batch-level dedup loss aggregation (per-entry hits filtered by dedup)
    findings_lost_to_dedup: int = 0
    # Sprint F207F: feed source dominance telemetry
    feed_findings_by_source: tuple[tuple[str, str, int], ...] = ()
    dominant_feed_source: str = ""
    dominant_feed_share_pct: float = 0.0
    feed_sources_successful: int = 0
    feed_source_cap_applied: bool = False
    # Sprint F207I: feed dominance scoring
    feed_dominance_score: float = 0.0
    feed_balance_recommendation: str = "feed_yield_ok"
    # Sprint F207I: dry-run cap estimator (does not enforce)
    estimated_per_source_soft_cap: int = 0



# ---------------------------------------------------------------------------
# --- HTML text processing (imported from pipeline.scoring) ---



def _assemble_clean_feed_text(title: str, summary: str) -> str:
    """
    Assemble deterministic clean text from title + summary.

    Deterministic assembly order:
    1. title (if non-empty)
    2. summary (stripped and cleaned, if non-empty)
    3. sentinel "[no content]" if both empty

    No html.unescape before tag stripping (per B.9).
    """
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if summary:
        stripped = _strip_html_tags_from_text(summary)
        if stripped:
            parts.append(stripped)
    if not parts:
        return "[no content]"
    return "\n\n".join(parts)


# --- Feed text assembly (imported from pipeline.scoring) ---

# Backwards-compatible alias (used by probe_8ah tests)
_entry_payload_text = _assemble_clean_feed_text

# ---------------------------------------------------------------------------
# Backwards-compatible entry-to-candidate-findings (used by probe_8ah tests)
# DEPRECATED: pipeline now uses pattern-backed approach via _entry_to_pattern_findings
# ---------------------------------------------------------------------------


def _entry_to_candidate_findings(
    feed_url: str,
    entry: Any,
    query_context: str | None,
) -> list[dict]:
    """
    [DEPRECATED — Sprint 8AN] Entry-backed CanonicalFinding dicts.
    Replaced by pattern-backed _entry_to_pattern_findings().

    This function is kept for probe_8ah test compatibility only.
    """
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    entry_url = getattr(entry, "entry_url", "") or ""
    published_raw = getattr(entry, "published_raw", "") or ""
    published_ts = getattr(entry, "published_ts", None)

    if not entry_url:
        entry_url = f"urn:feed:entry:{title[:64]}"

    payload = _assemble_clean_feed_text(title, summary)
    ts = _sane_timestamp(published_ts)

    query = query_context or feed_url

    return [{
        "finding_id": _make_feed_finding_id(
            feed_url, entry_url, title, published_raw
        ),
        "query": query,
        "source_type": "rss_atom_pipeline",
        "confidence": 0.8,
        "ts": ts,
        "provenance": ("rss_atom", feed_url, entry_url, "feed_entry"),
        "payload_text": payload,
    }]


# ---------------------------------------------------------------------------
# Timestamp sanity
# ---------------------------------------------------------------------------

_MIN_SANE_TS = 946684800.0  # 2000-01-01 00:00:00 UTC
_ONE_DAY_S = 86400.0


def _sane_timestamp(published_ts: float | None) -> float:
    """Return sane timestamp or fallback to time.time()."""
    now = time.time()
    if published_ts is None:
        return now
    if published_ts < _MIN_SANE_TS or published_ts > (now + _ONE_DAY_S):
        return now
    return published_ts


# ---------------------------------------------------------------------------
# Deterministic finding ID
# ---------------------------------------------------------------------------

def _make_feed_finding_id(
    feed_url: str,
    entry_url: str,
    label: str,
    pattern: str,
    value: str = "",
) -> str:
    """
    Deterministic ID via sha256 using pattern identity fields.
    No hash() — deterministic across runs.
    """
    key = f"{feed_url}\x00{entry_url}\x00{label}\x00{pattern}\x00{value}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-run dedup
# ---------------------------------------------------------------------------

class _RunDeduper:
    """Per-run preserve-first dedup by entry_url.

    Backwards-compatible: is_new(entry_url) for pattern-backed pipeline,
    is_new(entry_url, title, published_raw) for legacy entry-backed callers.
    """

    def __init__(self) -> None:
        self._seen: dict[str, bool] = {}

    def is_new(self, entry_url: str, _title: str = "", _raw: str = "") -> bool:
        # Legacy entry-backed callers pass (url, title, raw) — key is entry_url only
        # Pattern-backed callers pass just (entry_url,)
        if entry_url in self._seen:
            return False
        self._seen[entry_url] = True
        return True


# ---------------------------------------------------------------------------
# PatternMatcher import and helpers
# ---------------------------------------------------------------------------

# Import here so that absence of pattern_matcher is a hard fail at import time
from hledac.universal.patterns.pattern_matcher import match_text

# ---------------------------------------------------------------------------
# Per-entry dedup for pattern-backed findings
# ---------------------------------------------------------------------------

class _EntryDeduper:
    """Per-entry dedup by (label, pattern, value) preserve-first."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str]] = set()

    def is_new(self, label: str, pattern: str, value: str) -> bool:
        key = (label or "", pattern, value)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


# ---------------------------------------------------------------------------
# Pattern scan — offloaded, bounded concurrency
# ---------------------------------------------------------------------------


async def _async_scan_feed_text(text: str) -> list:
    """
    Offload pattern scan to thread executor with shared semaphore.

    PatternMatcher.match_text() handles lowercasing internally.
    Empty registry = empty list (valid zero-findings state).

    Raises:
        RuntimeError: if the pattern matcher itself fails (for fail-soft guard).
        CancelledError: propagated if task is cancelled.
    """
    if not text:
        return []

    # Bounded concurrency via shared semaphore
    sem = _get_pattern_offload_semaphore()

    async with sem:
        hits: list = await _ASYNC_PATTERN_OFFLOAD(match_text, text)
    return hits


# ---------------------------------------------------------------------------
# Payload text extraction around hit — unicode-safe, 200 char radius
# ---------------------------------------------------------------------------


def _extract_payload_context(
    text: str,
    hit_start: int,
    hit_end: int,
) -> str:
    """
    Extract unicode-safe payload context around pattern hit.

    Uses FEED_PAYLOAD_CONTEXT_CHARS radius.
    Cuts at whitespace boundaries if possible.
    """
    radius = FEED_PAYLOAD_CONTEXT_CHARS
    start = max(0, hit_start - radius)
    end = min(len(text), hit_end + radius)

    ctx = text[start:end]

    # Cut at whitespace boundaries to avoid mid-word cuts
    # Prefer breaking at newline/space before the hit
    if start > 0:
        # Find last whitespace before hit_start in the context window
        pre_cut = ctx[: hit_start - start]
        last_ws = max(pre_cut.rfind("\n"), pre_cut.rfind(" "))
        if last_ws > 0:
            ctx = ctx[last_ws + 1:]

    if end < len(text):
        # Find first whitespace after hit_end
        post_cut = ctx[hit_end - start:]
        first_ws = min(post_cut.find("\n"), post_cut.find(" "))
        if first_ws > 0:
            ctx = ctx[: hit_end - start + first_ws]

    ctx = ctx.strip()
    # Add ellipsis only if we actually cut
    cut_left = start > 0
    cut_right = end < len(text)
    if cut_left:
        ctx = "…" + ctx
    if cut_right:
        ctx = ctx + "…"
    return ctx


# ---------------------------------------------------------------------------
# PatternHit -> CanonicalFinding
# ---------------------------------------------------------------------------


def _pattern_hit_to_finding(
    feed_url: str,
    entry_url: str,
    hit: Any,
    query_context: str | None,
    clean_text: str,
) -> dict:
    """
    Map a single PatternHit to a CanonicalFinding dict.

    PatternHit: pattern, start, end, value, label
    """
    label = hit.label or ""
    pattern = hit.pattern
    value = hit.value

    ts = time.time()
    query = query_context or feed_url

    payload_text = _extract_payload_context(
        clean_text,
        hit.start,
        hit.end,
    )

    return {
        "finding_id": _make_feed_finding_id(
            feed_url, entry_url, label, pattern, value
        ),
        "query": query,
        "source_type": "rss_atom_pipeline",
        "confidence": 0.8,
        "ts": ts,
        "provenance": ("rss_atom", feed_url, entry_url, f"pattern:{label}"),
        "payload_text": payload_text,
    }


# ---------------------------------------------------------------------------
# Entry -> pattern-backed findings (replaces _entry_to_candidate_findings)
# ---------------------------------------------------------------------------


# Threshold for triggering article fallback.
# Feed entries with >= 250 chars of feed-native text (rich_content/summary)
# are considered substantive enough — no article fetch needed.
# Title-only entries will have ~50-100 chars, triggering fallback (intentional).
_MIN_ARTICLE_FALLBACK_CHARS: int = 250
_MAX_ARTICLE_FALLBACK_TIMEOUT: float = 8.0
_MAX_ARTICLE_FALLBACK_KB: int = 150

# F183E: Wayback CDX seam constants
_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
_WAYBACK_CDX_MAX_AGE_DAYS = 90  # prefer captures within 90 days
_WAYBACK_CDX_TIMEOUT = 4.0


async def _check_wayback_cdx(entry_url: str, session: Any) -> str | None:
    """
    F183E: Check Wayback Machine CDX API for recent capture of entry_url.

    Returns archive URL if recent capture exists (within _WAYBACK_CDX_MAX_AGE_DAYS),
    otherwise None. Does NOT raise — returns None on any failure.

    CDX API returns: [[url, timestamp, original, mimetype, status, ...], ...]
    """
    try:
        import aiohttp as _aiohttp
    except Exception:
        return None

    try:
        cdx_url = f"{_WAYBACK_CDX_URL}?url={entry_url}&output=json&limit=1&filter=statuscode:200&from={_WAYBACK_CDX_MAX_AGE_DAYS}d"
        async with asyncio.timeout(_WAYBACK_CDX_TIMEOUT):
            try:
                async with session.get(cdx_url, timeout=_aiohttp.ClientTimeout(total=_WAYBACK_CDX_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return None
                    raw = await resp.read()
            except Exception:
                return None
    except Exception:
        return None

    try:
        import json as _json
        entries = _json.loads(raw)
        if not entries or len(entries) < 2:
            return None
        # entries[0] is header, entries[1] is first result [url, timestamp, original, ...]
        capture_ts = entries[1][1]
        if not capture_ts:
            return None
        # Construct Wayback URL with timestamp
        archive_url = f"https://web.archive.org/web/{capture_ts}/{entry_url}"
        return archive_url
    except Exception:
        return None



async def _fetch_article_text(entry_url: str) -> tuple[str, bool, int]:
    """
    Fetch article body via direct aiohttp GET and strip HTML.

    F183E EXPANSION: Wayback CDX seam — before live fetch, check if archive
    capture exists and is recent (within 90 days). If so, fetch from archive
    instead of live URL. This improves source yield when live sources are
    inaccessible or degraded.

    Returns (article_text, success, replacement_count).
      - article_text: stripped text content, or "" on failure
      - success: True if article was fetched and has non-empty stripped text
      - replacement_count: U+FFFD replacement char count from _try_decode
    NEVER raises — all exceptions are caught, success=False on any failure.
    CancelledError is NOT caught (propagated).

    AUTHORITY NOTE (Sprint 8UX):
        This function is the article-fallback seam inside the feed pipeline.
        It does NOT go through FetchCoordinator (source-ingress owner).
        It uses session_runtime.py shared surface directly for HTTP.
        This is intentional: article fallback is a best-effort enrichment step,
        not part of the primary fetch pipeline.
        If the shared surface is later redirected to use FetchCoordinator's
        transport layer, this function will automatically benefit.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(entry_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ("", False, 0)
    except Exception:
        return ("", False, 0)

    try:
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
    except Exception:
        return ("", False, 0)

    try:
        session = await async_get_aiohttp_session()
    except Exception:
        return ("", False, 0)

    try:
        import aiohttp as _aiohttp
    except Exception:
        return ("", False, 0)

    # F183E: Wayback CDX seam — check for recent archive capture first
    wayback_url = await _check_wayback_cdx(entry_url, session)
    fetch_url = wayback_url if wayback_url else entry_url

    try:
        async with asyncio.timeout(_MAX_ARTICLE_FALLBACK_TIMEOUT):
            try:
                async with session.get(fetch_url, timeout=_aiohttp.ClientTimeout(total=_MAX_ARTICLE_FALLBACK_TIMEOUT)) as resp:
                    if resp.status != 200:
                        return ("", False, 0)
                    raw = await resp.read()
            except asyncio.CancelledError:
                raise
            except Exception:
                return ("", False, 0)
    except asyncio.CancelledError:
        raise
    except Exception:
        return ("", False, 0)

    # Decode with fallback, cap at MAX_ARTICLE_FALLBACK_KB
    # F185A DF-8 FIX: use _try_decode from public_fetcher instead of raw decode().
    # _try_decode returns (text, replaced_bool, replacement_count) and tries
    # UTF-8 → Windows-1252 → Latin-1 before replace, giving charset truth
    # that raw decode() discards entirely.
    # Combined decode+strip into one block so all returns are consistent 3-tuples.
    # Initialize before try so CancelledError propagation doesn't cause UnboundLocalError.
    article_decode_replacement_count: int = 0
    try:
        raw = raw[: _MAX_ARTICLE_FALLBACK_KB * 1024]
        try:
            from hledac.universal.fetching.public_fetcher import _try_decode
        except Exception:
            # Defensive: if import fails, fall back to simple decode
            text = raw.decode("utf-8", errors="replace")
            article_text = _strip_html_tags_from_text(text)
            if not article_text:
                return ("", False, 0)
            return (article_text.strip(), True, 0)
        text, decode_replaced, article_decode_replacement_count = _try_decode(raw)
        if not text.strip():
            return ("", False, article_decode_replacement_count)
        article_text = _strip_html_tags_from_text(text)
        if not article_text:
            return ("", False, article_decode_replacement_count)
        return (article_text.strip(), True, article_decode_replacement_count)
    except asyncio.CancelledError:
        raise
    except Exception:
        return ("", False, article_decode_replacement_count)


async def _entry_to_pattern_findings(
    feed_url: str,
    entry: Any,
    query_context: str | None,
) -> tuple[
    list[dict],
    int,
    int,
    int,
    str,
    str,
    bool,
    bool,
    EntryQualitySignal,
    FallbackDecision,
    str,
    int,
    int,
    int,
    int,
]:
    """
    Entry -> pattern-backed CanonicalFinding dicts.

    Returns (in order):
      findings, patterns_configured, matched_patterns, assembled_text_len,
      clean_text, enrichment_phase, article_fallback_used, article_fallback_attempted,
      quality_signal, fallback_decision, assembly_tier,
      pre_fallback_hits_count, post_fallback_hits_count, findings_lost_to_dedup

    - assembly_tier: result of _classify_assembly_substance
    - pre_fallback_hits_count: hits from feed-native text only
    - post_fallback_hits_count: hits after fallback (includes pre_fallback if not skipped)
    - findings_lost_to_dedup: hits that were deduped away (post - accepted)
    - fallback_decision: FallbackDecision structured assessment

    Empty registry = valid zero-findings state (patterns_configured=0, matched=0).
    """
    title = getattr(entry, "title", "") or ""
    summary = getattr(entry, "summary", "") or ""
    rich_content = getattr(entry, "rich_content", "") or ""
    entry_url = getattr(entry, "entry_url", "") or ""
    entry_author = getattr(entry, "entry_author", "") or ""
    feed_title = getattr(entry, "feed_title", "") or ""
    feed_language = getattr(entry, "feed_language", "") or ""

    # Adapter-derived signals (fail-soft)
    adapter_source_priority_bias: float = _float_attr(entry, "source_priority_bias", 0.0)
    adapter_metadata_richness_band: str = _str_attr(entry, "metadata_richness_band", "")
    adapter_entry_usefulness_band: str = _str_attr(entry, "entry_usefulness_band", "")

    if not entry_url:
        entry_url = f"urn:feed:entry:{title[:64]}"

    # Quality signal — computed before assembly
    # F192D DF-2: pass adapter quality_score for richer quality signal
    adapter_qs: float | None = _float_attr(entry, "quality_score", None)
    quality_signal = _compute_entry_quality_signal(
        title=title,
        summary=summary,
        rich_content=rich_content,
        entry_author=entry_author,
        feed_title=feed_title,
        feed_language=feed_language,
        adapter_quality_score=adapter_qs,
    )

    # Assembly substance classification — used for signal-loss diagnosis
    assembly_tier, _ = _classify_assembly_substance(title, summary, rich_content)

    # Enriched assembly
    clean_text, enrichment_phase = _assemble_enriched_feed_text(
        title, summary, rich_content, feed_title=feed_title, entry_author=entry_author
    )
    assembled_text_len = len(clean_text)

    # Pre-fallback scan — determines whether fallback is needed at all
    pre_fallback_hits_count = 0
    try:
        pre_hits = await _async_scan_feed_text(clean_text)
        pre_fallback_hits_count = len(pre_hits)
    except asyncio.CancelledError:
        raise
    except Exception:
        pre_hits = []

    # Fallback decision — single structured call replaces 5 scattered booleans
    # post_fallback_hits_count unknown at this point; use 0 as placeholder
    fallback_decision = _classify_fallback_decision(
        assembled_text_len=assembled_text_len,
        pre_fallback_hits_count=pre_fallback_hits_count,
        quality_signal=quality_signal,
        article_fallback_used=False,
        article_fallback_attempted=False,
        post_fallback_findings_count=0,
        adapter_source_priority_bias=adapter_source_priority_bias,
        adapter_metadata_richness_band=adapter_metadata_richness_band,
        adapter_entry_usefulness_band=adapter_entry_usefulness_band,
    )

    article_fallback_used = False
    article_fallback_attempted = False
    post_fallback_hits_count = pre_fallback_hits_count
    combined_text = clean_text

    # Skip post-fallback scan if pre-fallback hits exist — fallback would be wasteful
    # UNLESS aged/structured override applies
    skip_post_fallback_scan = (
        pre_fallback_hits_count > 0
        and fallback_decision.reason not in (
            "aged_structured_yield",
            "aged_structured_no_yield",
        )
    )

    article_decode_replacement_count = 0
    if not skip_post_fallback_scan and fallback_decision.should_fetch:
        article_text = ""
        article_success = False
        try:
            article_text, article_success, article_decode_replacement_count = await _fetch_article_text(entry_url)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        article_fallback_attempted = True
        if article_success and article_text:
            combined = f"{clean_text}\n\n{article_text}"
            if len(combined) > MAX_FEED_TEXT_CHARS:
                combined = combined[:MAX_FEED_TEXT_CHARS]
            combined_text = combined
            assembled_text_len = len(combined_text)
            enrichment_phase = "article_fallback"
            article_fallback_used = True

            # Post-fallback scan — scan the enriched text
            try:
                post_hits = await _async_scan_feed_text(combined_text)
                post_fallback_hits_count = len(post_hits)
            except asyncio.CancelledError:
                raise
            except Exception:
                post_hits = []
                post_fallback_hits_count = pre_fallback_hits_count
        else:
            # Fallback attempted but failed — post count = pre count
            post_fallback_hits_count = pre_fallback_hits_count

    # Hard cap on assembled text
    if assembled_text_len > MAX_FEED_TEXT_CHARS:
        combined_text = combined_text[:MAX_FEED_TEXT_CHARS]
        assembled_text_len = len(combined_text)

    # Get pattern count (local import avoids singleton init at module load time)
    from hledac.universal.patterns.pattern_matcher import get_pattern_matcher
    matcher_state = get_pattern_matcher()
    patterns_configured = len(matcher_state._registry_snapshot)

    # Final classification with actual post_fallback_hits_count
    fallback_decision = _classify_fallback_decision(
        assembled_text_len=assembled_text_len,
        pre_fallback_hits_count=pre_fallback_hits_count,
        quality_signal=quality_signal,
        article_fallback_used=article_fallback_used,
        article_fallback_attempted=article_fallback_attempted,
        post_fallback_findings_count=post_fallback_hits_count,
        adapter_source_priority_bias=adapter_source_priority_bias,
        adapter_metadata_richness_band=adapter_metadata_richness_band,
        adapter_entry_usefulness_band=adapter_entry_usefulness_band,
    )

    # Pattern scan — use combined_text (either enriched or original)
    scan_text = combined_text if article_fallback_used else clean_text
    try:
        hits = await _async_scan_feed_text(scan_text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RuntimeError(f"pattern_scan_failed: {exc}") from exc

    matched_patterns = len(hits)

    if not hits:
        # F182D: matched_patterns=0 is the canonical post-scan truth.
        # F183E fix: use post_fallback_hits_count (computed from fallback scan),
        # not matched_patterns (=0 from this empty final scan).
        # F192D DF-1 FIX: findings_lost_to_dedup = pre_fallback_hits_count when
        # fallback was used but produced no new hits (all pre-hits were deduped).
        # The hardcoded 0 was wrong: it discarded the actual dedup loss count.
        findings_lost_to_dedup_early = pre_fallback_hits_count
        return (
            [], patterns_configured, matched_patterns, assembled_text_len,
            scan_text, enrichment_phase, article_fallback_used, article_fallback_attempted,
            quality_signal, fallback_decision, assembly_tier,
            pre_fallback_hits_count, pre_fallback_hits_count, findings_lost_to_dedup_early,
            article_decode_replacement_count,
        )

    # Per-entry dedup by (label, pattern, value)
    entry_deduper = _EntryDeduper()
    findings: list[dict] = []
    for hit in hits:
        label = hit.label or ""
        pattern = hit.pattern
        value = hit.value
        if not entry_deduper.is_new(label, pattern, value):
            continue
        finding = _pattern_hit_to_finding(
            feed_url, entry_url, hit, query_context, scan_text
        )
        findings.append(finding)

    findings_lost_to_dedup = matched_patterns - len(findings)

    return (
        findings, patterns_configured, matched_patterns, assembled_text_len,
        scan_text, enrichment_phase, article_fallback_used, article_fallback_attempted,
        quality_signal, fallback_decision, assembly_tier,
        pre_fallback_hits_count, post_fallback_hits_count, findings_lost_to_dedup,
        article_decode_replacement_count,
    )


# ---------------------------------------------------------------------------
# UMA interaction
# ---------------------------------------------------------------------------

async def _check_uma_emergency() -> bool:
    """Return True if UMA is in emergency state."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        uma = sample_uma_status()
        return uma.state == "emergency"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main pipeline (pattern-backed)
# ---------------------------------------------------------------------------

async def async_run_live_feed_pipeline(
    feed_url: str,
    store: "DuckDBShadowStore | None" = None,
    query_context: str | None = None,
    max_entries: int = 20,
    timeout_s: float = 35.0,
    max_bytes: int = 2_000_000,
) -> FeedPipelineRunResult:
    """
    Run live feed pipeline for a single feed_url.

    Steps:
    1. Check UMA emergency -> fail-soft abort
    2. Fetch+parse via 8AF async_fetch_feed_entries()
    3. Per-entry: assemble clean text -> pattern scan -> dedup -> storage
    4. Return aggregated result with pattern observability

    Parameters
    ----------
    feed_url : str
        The feed URL to fetch.
    store : DuckDBShadowStore | None
        Optional storage. None = count-only mode.
    query_context : str | None
        Optional query context for findings.
    max_entries : int
        Max entries to process (clamped by 8AF to 1-100).
    timeout_s : float
        Feed fetch timeout.
    max_bytes : int
        Max bytes to fetch.

    Returns
    -------
    FeedPipelineRunResult
        With patterns_configured and matched_patterns observability.
    """
    # Step 1: UMA emergency check
    try:
        if await _check_uma_emergency():
            return FeedPipelineRunResult(
                feed_url=feed_url,
                fetched_entries=0,
                accepted_findings=0,
                stored_findings=0,
                patterns_configured=0,
                matched_patterns=0,
                pages=(),
                error="uma_emergency_abort",
            )
    except Exception:
        pass  # UMA check is best-effort; continue with pipeline

    # Step 2: Fetch via 8AF
    from hledac.universal.discovery.rss_atom_adapter import async_fetch_feed_entries

    try:
        batch = await async_fetch_feed_entries(
            feed_url=feed_url,
            max_entries=max_entries,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )
    except asyncio.CancelledError:
        raise  # never swallow
    except Exception as exc:
        return FeedPipelineRunResult(
            feed_url=feed_url,
            fetched_entries=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=0,
            matched_patterns=0,
            pages=(),
            error=f"fetch_exception:{type(exc).__name__}:{exc}",
        )

    # Handle fetch-level errors fail-soft
    if batch.error:
        # F170C: extract granular upstream blocker from batch.error
        _fetch_err = batch.error or ""
        _parse_blocker: str | None = None
        _fetch_blocker: str | None = None
        if "xml" in _fetch_err.lower() or "parse" in _fetch_err.lower() or "malformed" in _fetch_err.lower():
            _parse_blocker = "malformed_xml"
        elif "content" in _fetch_err.lower() or "type" in _fetch_err.lower():
            _parse_blocker = "wrong_content_type"
        elif "redirect" in _fetch_err.lower():
            _parse_blocker = "redirected_non_feed"
        # F170C: granular fetch blocker from error string patterns
        elif "timeout" in _fetch_err.lower() or "timed out" in _fetch_err.lower():
            _fetch_blocker = "timeout"
        elif "dns" in _fetch_err.lower() or "name or service not known" in _fetch_err.lower():
            _fetch_blocker = "dns_failure"
        elif "connection" in _fetch_err.lower() or "connect" in _fetch_err.lower():
            _fetch_blocker = "connection_error"
        elif "robot" in _fetch_err.lower() or "blocked" in _fetch_err.lower():
            _fetch_blocker = "robots_blocked"
        elif "403" in _fetch_err or "401" in _fetch_err or "Forbidden" in _fetch_err:
            _fetch_blocker = "http_error"
        elif "500" in _fetch_err or "502" in _fetch_err or "503" in _fetch_err or "504" in _fetch_err:
            _fetch_blocker = "http_error"
        else:
            _fetch_blocker = "http_error"
        # F169C: source_accessibility_error from adapter carries the true source-level failure
        _source_blocker: str | None = None
        if hasattr(batch, "source_accessibility_error") and batch.source_accessibility_error:
            _source_blocker = batch.source_accessibility_error
        return FeedPipelineRunResult(
            feed_url=feed_url,
            fetched_entries=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=0,
            matched_patterns=0,
            pages=(),
            error=f"fetch_error:{batch.error}",
            entries_seen=0,
            entries_with_empty_assembled_text=0,
            entries_with_text=0,
            entries_scanned=0,
            entries_with_hits=0,
            total_pattern_hits=0,
            findings_built_pre_store=0,
            assembled_text_chars_total=0,
            avg_assembled_text_len=0.0,
            signal_stage="empty_fetch",
            # Sprint F169D: root-cause propagation
            upstream_fetch_blocker=_fetch_blocker,
            upstream_parse_blocker=_parse_blocker,
            source_accessibility_blocker=_source_blocker,
            root_zero_yield_reason="fetch_error",
            had_substantive_content_but_no_hits=False,
        )

    entries = batch.entries
    fetched_count = len(entries)

    # Handle empty but valid response
    if fetched_count == 0:
        # F170C: source_accessibility_error from adapter carries source-level truth
        _source_blocker_empty: str | None = None
        if hasattr(batch, "source_accessibility_error") and batch.source_accessibility_error:
            _source_blocker_empty = batch.source_accessibility_error
        return FeedPipelineRunResult(
            feed_url=feed_url,
            fetched_entries=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=0,
            matched_patterns=0,
            pages=(),
            error=None,
            entries_seen=0,
            entries_with_empty_assembled_text=0,
            entries_with_text=0,
            entries_scanned=0,
            entries_with_hits=0,
            total_pattern_hits=0,
            findings_built_pre_store=0,
            assembled_text_chars_total=0,
            avg_assembled_text_len=0.0,
            signal_stage="empty_fetch",
            # Sprint 8BE: enrichment
            entries_with_rich_feed_content=0,
            entries_with_article_fallback=0,
            article_fallback_fetch_attempts=0,
            article_fallback_fetch_successes=0,
            enriched_text_chars_total=0,
            avg_enriched_text_len=0.0,
            sample_enriched_texts=(),
            enrichment_phase_used="none",
            temporal_feed_vocabulary_mismatch=False,
            # Sprint F169D + F170C: root-cause propagation
            upstream_fetch_blocker=None,
            upstream_parse_blocker=None,
            source_accessibility_blocker=_source_blocker_empty,
            root_zero_yield_reason="empty_fetch",
            had_substantive_content_but_no_hits=False,
        )

    # Step 3: Per-entry processing — pattern-backed
    run_deduper = _RunDeduper()
    pages: list[FeedPipelineEntryResult] = []
    total_accepted = 0
    total_stored = 0
    total_matched = 0
    total_patterns_configured = 0
    # Sprint 8AU: pre-store observability counters
    entries_seen = 0
    entries_with_empty_assembled_text = 0
    entries_with_text = 0
    entries_scanned = 0
    entries_with_hits = 0
    total_pattern_hits = 0
    findings_built_pre_store = 0
    assembled_text_chars_total = 0
    # Sprint 8BE: enrichment counters
    entries_with_rich_feed_content = 0
    entries_with_article_fallback = 0
    article_fallback_fetch_attempts = 0
    article_fallback_fetch_successes = 0
    enriched_text_chars_total = 0
    # Sprint 8BC: bounded sample capture (max 3 entries, max 160 chars per sample)
    _sample_texts: list[str] = []
    _sample_hit_counts: list[int] = []
    _sample_hit_labels: list[str] = []
    _sample_texts_truncated = False
    _entries_with_content_seen = 0
    _MAX_SAMPLE_ENTRIES = 3
    _MAX_SAMPLE_CHARS = 160
    # Sprint F300C: separate enriched sample (post-enrichment text)
    _sample_enriched_texts: list[str] = []
    _sample_enriched_texts_truncated = False
    # Sprint F150I: feed economics counters
    _feed_branch_signal_present = False
    _fallback_useful_count = 0
    _fallback_waste_count = 0
    _findings_from_rich_feed = 0
    _findings_from_fallback = 0
    # Sprint F150J: derived feed counters
    _squandered_high_usefulness_entries = 0
    _metadata_strong_but_content_weak = 0
    _low_trust_feed_hits = 0
    _findings_lost_to_dedup_total = 0
    # Sprint F151A: winning source breakdown accumulator
    _winning_source_breakdown_acc: dict[str, int] = {"feed_native": 0, "fallback": 0, "mixed": 0}
    _adapter_source_priority_bias_acc: float = 0.0
    _adapter_timestamp_reliability_acc: float = 0.0
    _adapter_metadata_richness_band_acc: str = ""
    _adapter_entry_usefulness_band_acc: str = ""
    _adapter_selection_reason_acc: str = ""
    _adapter_signal_count: int = 0  # W3: count entries with adapter signals for proper averaging
    _temporal_vocabulary_mismatch: bool = False  # W4: temporal vocabulary gap signal
    # F185A DF-2: pre/post fallback hit counts aggregated at run level
    _pre_fallback_hits_total: int = 0
    _post_fallback_hits_total: int = 0
    # F185A DF-6: structured zero-hit evidence (mirrors live_public_pipeline.py zero-hit surface)
    _zero_hit_feed_fetch_count: int = 0
    _zero_hit_reasons_acc: dict[str, int] = {}
    _zero_hit_title_samples_acc: list[tuple[str, str]] = []

    for entry in entries:
        entry_url = getattr(entry, "entry_url", "") or f"urn:feed:entry:{getattr(entry, 'title', '')[:64]}"

        # Per-run dedup: skip if we've already seen this entry_url
        if not run_deduper.is_new(entry_url):
            pages.append(FeedPipelineEntryResult(
                entry_url=entry_url,
                accepted_findings=0,
                stored_findings=0,
                error=None,
            ))
            continue

        entries_seen += 1

        # Pattern scan + mapping — fail-soft per entry
        try:
            (findings, patterns_cfg, matched, assembled_len, clean_text,
             enrichment_phase, article_fallback_used, article_fallback_attempted,
             quality_signal, fallback_decision, assembly_tier,
             pre_fallback_hits, post_fallback_hits, findings_lost_to_dedup,
             article_decode_replacement_count) = await _entry_to_pattern_findings(
                feed_url, entry, query_context
            )
        except asyncio.CancelledError:
            raise  # never swallow
        except Exception:
            pages.append(FeedPipelineEntryResult(
                entry_url=entry_url,
                accepted_findings=0,
                stored_findings=0,
                error="pattern_step_failed",
            ))
            continue

        total_patterns_configured += patterns_cfg
        total_matched += matched

        # Sprint 8AU: update assembled text counters
        # "[no content]" sentinel means no real content (both title and summary were empty)
        is_empty_content = (assembled_len == 0) or (clean_text == "[no content]")
        assembled_text_chars_total += assembled_len
        if is_empty_content:
            entries_with_empty_assembled_text += 1
        else:
            entries_text = clean_text
            if len(entries_text) > _MAX_SAMPLE_CHARS:
                entries_text = entries_text[:_MAX_SAMPLE_CHARS]
                _sample_texts_truncated = True
            _entries_with_content_seen += 1
            if _entries_with_content_seen <= _MAX_SAMPLE_ENTRIES:
                _sample_texts.append(entries_text)
                _sample_hit_counts.append(matched)
                if matched > 0:
                    # W1: Only scan for labels if we have hits AND sample slot available.
                    # Reuse clean_text (already casefolded in _async_scan_feed_text) — no new match_text needed
                    # to get labels. The second scan here is bounded: max 1 per sample entry, 3 samples max.
                    try:
                        from hledac.universal.patterns.pattern_matcher import match_text
                        hits_for_labels = match_text(entries_text)  # entries_text is clean_text truncated
                        for h in hits_for_labels:
                            if h.label and len(_sample_hit_labels) < 20:
                                _sample_hit_labels.append(h.label)
                    except Exception:
                        pass
            entries_with_text += 1
            entries_scanned += 1
            total_pattern_hits += matched
            # Sprint 8BE: track enrichment phase
            if enrichment_phase == "feed_rich_content":
                entries_with_rich_feed_content += 1
            elif enrichment_phase == "article_fallback":
                entries_with_article_fallback += 1
            if article_fallback_attempted:
                article_fallback_fetch_attempts += 1
            if article_fallback_used:
                article_fallback_fetch_successes += 1
            enriched_text_chars_total += assembled_len
            # F300C: capture post-enrichment text for enriched sample (bounded, separate from scanned sample)
            if len(_sample_enriched_texts) < _MAX_SAMPLE_ENTRIES:
                enriched_trunc = clean_text[:_MAX_SAMPLE_CHARS]
                if len(clean_text) > _MAX_SAMPLE_CHARS:
                    _sample_enriched_texts_truncated = True
                _sample_enriched_texts.append(enriched_trunc)
            if matched > 0:
                entries_with_hits += 1
                findings_built_pre_store += len(findings)

            # F160A: consolidated economics tracking via FallbackDecision
            fd = fallback_decision
            if fd.wasted:
                _fallback_waste_count += 1
            elif fd.helpful:
                _fallback_useful_count += 1

            # Track feed-native signal presence
            # F192D DF-3 FIX: _feed_branch_signal_present must be True when fallback
            # provides signal even if pre_fallback_hits == 0. Previously only set when
            # pre_fallback_hits > 0, so fallback-only signal was invisible.
            # F192D DF-4 FIX: When pre > 0 AND fallback was helpful, only pre's own hits
            # should be attributed to rich_feed. Fallback's new findings (beyond pre count)
            # go to fallback. Using min(pre, len(findings)) correctly handles both:
            # - skipped fallback: all matched are pre-native
            # - helpful fallback: matched = pre + fallback_new, rich_feed gets min(pre, matched)
            if pre_fallback_hits > 0:
                _feed_branch_signal_present = True
                rich_feed_gets = min(pre_fallback_hits, len(findings))
                _findings_from_rich_feed += rich_feed_gets
                # Any findings beyond pre's hits are fallback's contribution
                fallback_new = len(findings) - rich_feed_gets
                if fallback_new > 0:
                    _findings_from_fallback += fallback_new
            elif fd.helpful:
                _feed_branch_signal_present = True
                _findings_from_fallback += len(findings)

            # Squandered: forced fallback on high-quality entry with no yield
            if fd.forced and quality_signal.quality_band == "high" and not fd.helpful:
                _squandered_high_usefulness_entries += 1

            # Metadata strong but content weak
            if quality_signal.metadata_boost and assembled_len < _MIN_ARTICLE_FALLBACK_CHARS and pre_fallback_hits == 0:
                _metadata_strong_but_content_weak += 1

            # Low-trust feed hits
            if pre_fallback_hits > 0 and quality_signal.quality_band == "low":
                _low_trust_feed_hits += 1

            # F160A: findings lost to per-entry dedup (hits arrived but filtered)
            _findings_lost_to_dedup_total += findings_lost_to_dedup

            # F185A DF-2: accumulate pre/post fallback hit counts at run level
            _pre_fallback_hits_total += pre_fallback_hits
            _post_fallback_hits_total += post_fallback_hits

            # F185A DF-6: structured zero-hit evidence — entries with matched == 0
            # AND no pre-fallback signal (pre_fallback_hits == 0).
            # F192D DF-2 FIX: matched == 0 alone is insufficient — if pre_fallback_hits > 0,
            # signal existed but was all deduped away. Those entries should NOT appear
            # in zero-hit evidence (they belong in findings_build_loss diagnosis).
            # Skip: entries with pre-fallback hits (signal existed, was filtered by dedup).
            # Also skip: skipped fallback due to pre_hits (fallback never ran — not a true zero-hit).
            if matched == 0 and pre_fallback_hits == 0:
                _zero_hit_feed_fetch_count += 1
                # quality_reason_tag from EntryQualitySignal — why content had no hits
                _reason_key = quality_signal.quality_reason_tag or "unknown"
                _zero_hit_reasons_acc[_reason_key] = _zero_hit_reasons_acc.get(_reason_key, 0) + 1
                # Bounded title sample (max 5, no raw text)
                if len(_zero_hit_title_samples_acc) < 5:
                    _title = getattr(entry, "title", "") or ""
                    _zero_hit_title_samples_acc.append((_title, entry_url))

            # W3 FIX: Accumulate adapter signals (+=) instead of last-write overwrite (=).
            # _float_attr is safe with MagicMock — returns 0.0 for missing attrs.
            _adapter_source_priority_bias_acc += _float_attr(entry, "source_priority_bias", 0.0)
            _adapter_timestamp_reliability_acc += _float_attr(entry, "timestamp_reliability", 0.0)
            # String fields: keep first non-empty value (representative, not last)
            _adapter_metadata_richness_band_acc = _adapter_metadata_richness_band_acc or _str_attr(entry, "metadata_richness_band", "")
            _adapter_entry_usefulness_band_acc = _adapter_entry_usefulness_band_acc or _str_attr(entry, "entry_usefulness_band", "")
            _adapter_selection_reason_acc = _adapter_selection_reason_acc or _str_attr(entry, "selection_reason", "")
            _adapter_signal_count += 1

            # W4 FIX: temporal_feed_vocabulary_mismatch — true when feed has substantive
            # content but got zero hits, while other entries in the same run DID get hits.
            # This means the feed's vocabulary doesn't match pattern vocabulary.
            if not is_empty_content and matched == 0 and assembled_len >= _MIN_ARTICLE_FALLBACK_CHARS:
                # Content was substantive but no hits — possible vocabulary gap
                if entries_with_hits > 0:
                    _temporal_vocabulary_mismatch = True

            # Winning source breakdown via FallbackDecision
            feed_native_carried = pre_fallback_hits > 0
            entry_breakdown = _compute_winning_source_breakdown(
                feed_native_carried, article_fallback_used, findings, _adapter_selection_reason_acc
            )
            for k, v in entry_breakdown.items():
                _winning_source_breakdown_acc[k] = _winning_source_breakdown_acc.get(k, 0) + v

        if not findings:
            pages.append(FeedPipelineEntryResult(
                entry_url=entry_url,
                accepted_findings=0,
                stored_findings=0,
                error=None,
                assembly_tier=assembly_tier,
                quality_reason_tag=quality_signal.quality_reason_tag if quality_signal else "",
            ))
            continue

        # Step 4: Storage
        # F180B FIX: accepted_findings and stored_findings must be isolated from
        # each other and preserved across exceptions (fail-soft semantics).
        # accepted_findings = quality-gated count (from async_ingest_findings_batch results)
        # stored_findings = actual storage success count (from lmdb_success field)
        accepted_findings = len(findings)  # pre-set: quality gate pass = all findings
        stored_findings = 0
        _entry_store_error: str | None = None

        if store is not None:
            try:
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                canonicals: list[CanonicalFinding] = [
                    CanonicalFinding(**f) for f in findings
                ]

                results = await store.async_ingest_findings_batch(canonicals)

                # F180B FIX: Count accepted (quality-gated) and stored (lmdb_success)
                # separately — accepted does NOT imply stored when DuckDB fails.
                # accepted: FindingQualityDecision.accepted OR ActivationResult.accepted
                # stored: lmdb_success (WAL write succeeded)
                accepted_findings = 0
                stored_findings = 0
                for r in results:
                    if isinstance(r, dict):
                        accepted_findings += int(r.get("accepted", False))
                        stored_findings += int(r.get("lmdb_success", False))
                    else:
                        accepted_findings += int(getattr(r, "accepted", False))
                        stored_findings += int(getattr(r, "lmdb_success", False))

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # F180B FIX: Preserve partial results accumulated so far in this entry.
                # Do NOT reset accepted_findings/stored_findings to 0 on exception —
                # partial results from before the exception are still valid.
                _entry_store_error = f"store_exception:{type(exc).__name__}"
                # accepted_findings and stored_findings already hold the last valid values
                # from this entry's processing (or 0 if exception happened before any count)
        else:
            # No store: count-only mode — accepted is pre-storage gate hit count,
            # stored must be 0 (nothing reached storage)
            accepted_findings = len(findings)
            stored_findings = 0

        total_accepted += accepted_findings
        total_stored += stored_findings

        pages.append(FeedPipelineEntryResult(
            entry_url=entry_url,
            accepted_findings=accepted_findings,
            stored_findings=stored_findings,
            error=_entry_store_error,
            assembly_tier=assembly_tier,
            quality_reason_tag=quality_signal.quality_reason_tag if quality_signal else "",
        ))

    # Sprint 8AU + F160A: compute signal stage diagnosis with findings_build_loss tracking
    signal_stage = diagnose_feed_signal_stage(
        entries_seen=entries_seen,
        entries_with_empty_assembled_text=entries_with_empty_assembled_text,
        entries_scanned=entries_scanned,
        entries_with_hits=entries_with_hits,
        findings_built_pre_store=findings_built_pre_store,
        patterns_configured=total_patterns_configured,
        findings_lost_to_dedup_total=_findings_lost_to_dedup_total,
    )
    avg_text_len = (
        assembled_text_chars_total / entries_with_text
        if entries_with_text > 0
        else 0.0
    )
    # W3 FIX: Average adapter signals over entries that contributed them.
    _avg_bias = _adapter_source_priority_bias_acc / max(1, _adapter_signal_count)
    _avg_timestamp = _adapter_timestamp_reliability_acc / max(1, _adapter_signal_count)

    # F164C: compute once, use twice — eliminates duplicate recompute drift
    _next_action_and_note = _compute_feed_next_action_and_confidence(
        _feed_branch_signal_present, _fallback_useful_count, _fallback_waste_count,
        _findings_from_rich_feed, _findings_from_fallback,
        _squandered_high_usefulness_entries, _metadata_strong_but_content_weak, _low_trust_feed_hits,
    )

    return FeedPipelineRunResult(
        feed_url=feed_url,
        fetched_entries=fetched_count,
        accepted_findings=total_accepted,
        stored_findings=total_stored,
        patterns_configured=total_patterns_configured,
        matched_patterns=total_matched,
        pages=tuple(pages),
        error=None,
        entries_seen=entries_seen,
        entries_with_empty_assembled_text=entries_with_empty_assembled_text,
        entries_with_text=entries_with_text,
        entries_scanned=entries_scanned,
        entries_with_hits=entries_with_hits,
        total_pattern_hits=total_pattern_hits,
        findings_built_pre_store=findings_built_pre_store,
        assembled_text_chars_total=assembled_text_chars_total,
        avg_assembled_text_len=avg_text_len,
        signal_stage=signal_stage,
        # Sprint F159: zero_signal_reason — derived fail-soft from signal_stage
        zero_signal_reason=signal_stage if signal_stage in (
            "empty_fetch", "content_empty", "no_pattern_hits",
            "no_pattern_hits_with_content", "findings_build_loss",
            "empty_registry",
        ) else None,
        # Sprint 8BC: bounded sample capture
        sample_scanned_texts=tuple(_sample_texts),
        sample_hit_counts=tuple(_sample_hit_counts),
        sample_hit_labels_union=tuple(dict.fromkeys(_sample_hit_labels)),
        sample_texts_truncated=_sample_texts_truncated,
        feed_content_mismatch=bool(_entries_with_content_seen > 0 and all(c == 0 for c in _sample_hit_counts)),
        # Sprint 8BE: enrichment
        entries_with_rich_feed_content=entries_with_rich_feed_content,
        entries_with_article_fallback=entries_with_article_fallback,
        article_fallback_fetch_attempts=article_fallback_fetch_attempts,
        article_fallback_fetch_successes=article_fallback_fetch_successes,
        enriched_text_chars_total=enriched_text_chars_total,
        avg_enriched_text_len=(
            enriched_text_chars_total / (entries_with_rich_feed_content + entries_with_article_fallback)
            if (entries_with_rich_feed_content + entries_with_article_fallback) > 0
            else 0.0
        ),
        sample_enriched_texts=tuple(_sample_enriched_texts),
        enrichment_phase_used="article_fallback" if entries_with_article_fallback > 0 else ("feed_rich_content" if entries_with_rich_feed_content > 0 else "none"),
        temporal_feed_vocabulary_mismatch=_temporal_vocabulary_mismatch,
        # Sprint F150I: feed economics verdicts
        feed_branch_signal_present=_feed_branch_signal_present,
        fallback_useful_count=_fallback_useful_count,
        fallback_waste_count=_fallback_waste_count,
        findings_from_rich_feed=_findings_from_rich_feed,
        findings_from_fallback=_findings_from_fallback,
        feed_branch_hint=_compute_feed_branch_hint(
            _feed_branch_signal_present, _fallback_useful_count, _fallback_waste_count,
            _findings_from_rich_feed, _findings_from_fallback, entries_with_hits,
        ),
        feed_economics_verdict=_compute_feed_economics_verdict(
            _feed_branch_signal_present, _fallback_useful_count, _fallback_waste_count,
            _findings_from_rich_feed, _findings_from_fallback,
        ),
        # Sprint F150J: derived feed counters + dict verdict
        squandered_high_usefulness_entries=_squandered_high_usefulness_entries,
        metadata_strong_but_content_weak=_metadata_strong_but_content_weak,
        low_trust_feed_hits=_low_trust_feed_hits,
        fallback_value_ratio=(
            _fallback_useful_count / max(1, _fallback_useful_count + _fallback_waste_count)
        ),
        feed_native_yield_ratio=(
            _findings_from_rich_feed / max(1, _findings_from_rich_feed + _findings_from_fallback)
        ),
        # F164C: use pre-computed result (computed before return block)
        feed_next_action=_next_action_and_note[0],
        feed_confidence_note=_next_action_and_note[1],
        feed_branch_verdict=_compute_feed_branch_verdict(
            _feed_branch_signal_present, _fallback_useful_count, _fallback_waste_count,
            _findings_from_rich_feed, _findings_from_fallback,
            _squandered_high_usefulness_entries, _metadata_strong_but_content_weak, _low_trust_feed_hits,
            entries_with_hits, entries_seen,
            _findings_from_rich_feed / max(1, _findings_from_rich_feed + _findings_from_fallback),
            _fallback_useful_count / max(1, _fallback_useful_count + _fallback_waste_count),
        ),
        # Sprint F151A: winning source breakdown + adapter-adjusted confidence
        winning_source_breakdown=dict(_winning_source_breakdown_acc),
        findings_lost_to_dedup=_findings_lost_to_dedup_total,
        feed_confidence_score=_compute_adapter_adjusted_confidence(
            max(0, min(100, int(
                (_findings_from_rich_feed / max(1, _findings_from_rich_feed + _findings_from_fallback)) * 100
            ))),
            _avg_bias,
            _avg_timestamp,
            _adapter_metadata_richness_band_acc,
            _adapter_entry_usefulness_band_acc,
            _adapter_selection_reason_acc,
            _feed_branch_signal_present,
        ),
        # Sprint F169D: root-cause propagation
        upstream_fetch_blocker=None,
        upstream_parse_blocker=None,
        source_accessibility_blocker=None,
        root_zero_yield_reason=signal_stage if (
            signal_stage in ("empty_fetch", "content_empty", "no_pattern_hits",
                            "no_pattern_hits_with_content", "findings_build_loss", "empty_registry")
            and total_accepted == 0
        ) else None,
        had_substantive_content_but_no_hits=bool(
            entries_with_text > 0 and entries_with_hits == 0 and total_accepted == 0
        ),
        # F185A DF-2: pre/post fallback hit counts aggregated at run level
        pre_fallback_hits_total=_pre_fallback_hits_total,
        post_fallback_hits_total=_post_fallback_hits_total,
        # F185A DF-6: structured zero-hit evidence surface
        zero_hit_feed_fetch_count=_zero_hit_feed_fetch_count,
        zero_hit_feed_fetch_reasons=dict(_zero_hit_reasons_acc),
        zero_hit_feed_fetch_samples=tuple(_zero_hit_title_samples_acc),
    )


# ---------------------------------------------------------------------------
# Batch source coercion (Sprint 8AL — unchanged public signature)
# ---------------------------------------------------------------------------


def _coerce_source_to_tuple(
    source: object,
) -> tuple[str, str, str, int]:
    """
    Coerce FeedSeed / FeedDiscoveryHit / MergedFeedSource / plain str
    into a unified (feed_url, label, origin, priority) tuple.

    Label fallback = "" (never None -> "None" string).
    FeedSeed uses 'source' field for origin.
    FeedDiscoveryHit has no origin/priority — use "" and 0.
    MergedFeedSource has both origin and priority.
    """
    if isinstance(source, str):
        return (source, "", "unknown", 0)

    if hasattr(source, "source") and not hasattr(source, "origin"):
        feed_url = getattr(source, "feed_url", "") or ""
        label = getattr(source, "label", None)
        label = "" if label is None else label
        origin = getattr(source, "source", None)
        origin = "" if origin is None else origin
        priority = int(getattr(source, "priority", 0) or 0)
        return (feed_url, label, origin, priority)

    feed_url = getattr(source, "feed_url", "") or ""
    label = getattr(source, "label", None)
    label = "" if label is None else label
    origin = getattr(source, "origin", None)
    origin = "" if origin is None else origin
    priority = int(getattr(source, "priority", 0) or 0)
    return (feed_url, label, origin, priority)


# ---------------------------------------------------------------------------
# Feed Dominance Scoring (Sprint F207I)
# ---------------------------------------------------------------------------

def compute_feed_dominance_score(
    dominant_feed_share_pct: float,
    total_feed_findings: int,
    feed_sources_successful: int,
) -> float:
    """
    Compute a 0.0-1.0 feed dominance score.

    Combines dominant share (60%) and source concentration (40%) into a single
    score where 0 = balanced multi-source, 1 = single-source domination.

    Inputs:
      dominant_feed_share_pct -- 0.0-100.0, from FeedSourceBatchRunResult
      total_feed_findings -- total accepted findings across all feed sources
      feed_sources_successful -- count of sources that returned without error
    """
    if total_feed_findings == 0:
        return 0.0

    # Dominant-share component: 0=balanced(<=50%), 1=full dominance(100%)
    # Linear from 50% -> 100% maps to 0.0 -> 1.0
    _dom_component = max(0.0, (dominant_feed_share_pct - 50.0) / 50.0)

    # Concentration component: 1/source_count -- 1 source = max concentration (1.0)
    # More sources = lower concentration = lower dominance score
    _source_count = max(1, feed_sources_successful)
    _concentration_component = 1.0 / _source_count

    # Weighted combination: 60% dominant share, 40% concentration
    _score = (_dom_component * 0.6) + (_concentration_component * 0.4)
    return round(min(1.0, max(0.0, _score)), 3)


def compute_feed_balance_recommendation(
    feed_dominance_score: float,
    dominant_feed_share_pct: float,
    feed_sources_successful: int,
    total_feed_findings: int,
) -> str:
    """
    Produce an actionable recommendation string from dominance metrics.

    Recommendation strings:
      "balanced"               -- no action needed, sources well-distributed
      "dominant_source_watch"  -- one source leading (50-80%), monitor next run
      "recommend_soft_cap_next_run" -- dominance >= 0.7, suggest per-source cap
      "low_feed_diversity"     -- only 1 source succeeded, consider adding feeds
      "feed_yield_ok"          -- no findings yet, ok state
    """
    if total_feed_findings == 0:
        return "feed_yield_ok"

    if feed_sources_successful <= 1:
        return "low_feed_diversity"

    if feed_dominance_score >= 0.7:
        return "recommend_soft_cap_next_run"

    if dominant_feed_share_pct >= 50.0:
        return "dominant_source_watch"

    return "balanced"


def estimate_per_source_soft_cap(
    total_budget: int,
    source_count: int,
) -> int:
    """
    Dry-run per-source soft cap estimator.

    Returns a suggested per-source ceiling. Does NOT enforce -- callers use
    this value only for reporting/recommendation purposes.

    The formula: equal share with 20% headroom for top sources.
    """
    if total_budget <= 0 or source_count <= 0:
        return 0

    _equal_share = total_budget // source_count
    _soft_cap = int(_equal_share * 1.2)
    return max(1, _soft_cap)


# ---------------------------------------------------------------------------
# Batch runner (Sprint 8AL -- unchanged public signature)
# ---------------------------------------------------------------------------


async def async_run_feed_source_batch(
    sources: tuple[object, ...],
    store: Any | None = None,
    max_entries_per_feed: int = 20,
    feed_concurrency: int = 3,
    query_context: str | None = None,
    per_feed_timeout_s: float = 45.0,
    batch_timeout_s: float = 300.0,
) -> FeedSourceBatchRunResult:
    """
    Run a one-shot batch over heterogeneous feed sources.

    Unchanged signature from 8AL — no breaking changes to public API.
    """
    if not sources:
        return FeedSourceBatchRunResult(
            total_sources=0,
            completed_sources=0,
            fetched_entries=0,
            accepted_findings=0,
            stored_findings=0,
            sources=(),
            error=None,
        )

    normalized: list[tuple[str, str, str, int]] = [
        _coerce_source_to_tuple(s) for s in sources
    ]
    normalized.sort(key=lambda x: -x[3])

    # UMA check at batch start
    emergency_abort = False
    critical_clamp = False
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        uma = sample_uma_status()
        if uma.state == "emergency":
            emergency_abort = True
        elif uma.state == "critical":
            critical_clamp = True
    except Exception:
        pass

    if emergency_abort:
        return FeedSourceBatchRunResult(
            total_sources=len(normalized),
            completed_sources=0,
            fetched_entries=0,
            accepted_findings=0,
            stored_findings=0,
            sources=(),
            error="uma_emergency_abort",
        )

    effective_concurrency = 1 if critical_clamp else feed_concurrency

    async def _run_single(
        feed_url: str,
        label: str,
        origin: str,
        priority: int,
    ) -> FeedSourceRunResult:
        start = time.monotonic()
        elapsed_ms = 0.0

        resolved_query = query_context
        if not resolved_query:
            resolved_query = label if label else feed_url

        try:
            async with asyncio.timeout(per_feed_timeout_s):
                result: FeedPipelineRunResult = await async_run_live_feed_pipeline(
                    feed_url=feed_url,
                    store=store,
                    query_context=resolved_query,
                    max_entries=max_entries_per_feed,
                    timeout_s=per_feed_timeout_s,
                )
        except asyncio.CancelledError:
            raise  # never swallow
        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            return FeedSourceRunResult(
                feed_url=feed_url,
                label=label,
                origin=origin,
                priority=priority,
                fetched_entries=0,
                accepted_findings=0,
                stored_findings=0,
                elapsed_ms=elapsed_ms,
                error="per_feed_timeout",
            )
        except BaseException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            return FeedSourceRunResult(
                feed_url=feed_url,
                label=label,
                origin=origin,
                priority=priority,
                fetched_entries=0,
                accepted_findings=0,
                stored_findings=0,
                elapsed_ms=elapsed_ms,
                error=f"unexpected:{type(exc).__name__}:{exc}",
            )

        elapsed_ms = (time.monotonic() - start) * 1000.0
        return FeedSourceRunResult(
            feed_url=feed_url,
            label=label,
            origin=origin,
            priority=priority,
            fetched_entries=result.fetched_entries,
            accepted_findings=result.accepted_findings,
            stored_findings=result.stored_findings,
            elapsed_ms=elapsed_ms,
            error=result.error,
            signal_stage=result.signal_stage,
            # F164C: propagate per-source dedup loss counter
            findings_lost_to_dedup=result.findings_lost_to_dedup,
        )

    results: list[FeedSourceRunResult] = []

    try:
        async with asyncio.timeout(batch_timeout_s):
            for i in range(0, len(normalized), effective_concurrency):
                batch_slice = normalized[i : i + effective_concurrency]
                tasks = [
                    _run_single(url, lbl, org, pri)
                    for url, lbl, org, pri in batch_slice
                ]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in batch_results:
                    if isinstance(res, asyncio.CancelledError):
                        raise res
                    elif isinstance(res, BaseException):
                        results.append(FeedSourceRunResult(
                            feed_url="<unknown>",
                            label="",
                            origin="unknown",
                            priority=0,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            error=f"gather_exception:{type(res).__name__}:{res}",
                        ))
                    else:
                        results.append(res)
    except asyncio.CancelledError:
        raise  # never swallow
    except asyncio.TimeoutError:
        pass

    total_fetched = sum(r.fetched_entries for r in results)
    total_accepted = sum(r.accepted_findings for r in results)
    total_stored = sum(r.stored_findings for r in results)
    completed = sum(1 for r in results if r.error is None)
    batch_error = "batch_timeout" if (
        len(results) < len(normalized) or
        any(r.error == "per_feed_timeout" for r in results)
    ) else None

    # Sprint 8BE Phase 3: dominant signal stage (mode) across all sources
    stage_counter: Counter[str] = Counter()
    for r in results:
        if r.signal_stage and r.signal_stage != "unknown":
            stage_counter[r.signal_stage] += 1
    dominant_stage = stage_counter.most_common(1)[0][0] if stage_counter else "unknown"

    _logger = logging.getLogger(__name__)
    _logger.info(f"[BATCH] dominant_signal_stage={dominant_stage}")

    # F164C: aggregate findings_lost_to_dedup from all sources
    _batch_dedup_loss = sum(r.findings_lost_to_dedup for r in results)

    # Sprint F207F: feed source dominance telemetry — compute before building result
    _feed_by_source: list[tuple[str, str, int]] = [
        (r.feed_url, r.label, r.accepted_findings) for r in results
    ]
    _dominant = max(
        results,
        key=lambda r: r.accepted_findings,
        default=None,
    )
    _dom_source: str = ""
    _dom_share_pct: float = 0.0
    if _dominant is not None and total_accepted > 0:
        _dom_source = _dominant.label or _dominant.feed_url
        _dom_share_pct = round(_dominant.accepted_findings / total_accepted * 100.0, 2)
    _successful = sum(1 for r in results if r.error is None)

    # Sprint F207I: compute dominance score and recommendation
    _dominance_score = compute_feed_dominance_score(
        _dom_share_pct, total_accepted, _successful,
    )
    _recommendation = compute_feed_balance_recommendation(
        _dominance_score, _dom_share_pct, _successful, total_accepted,
    )
    _soft_cap = estimate_per_source_soft_cap(total_accepted, _successful)

    return FeedSourceBatchRunResult(
        total_sources=len(normalized),
        completed_sources=completed,
        fetched_entries=total_fetched,
        accepted_findings=total_accepted,
        stored_findings=total_stored,
        sources=tuple(results),
        error=batch_error,
        dominant_signal_stage=dominant_stage,
        # F164C: batch-level dedup loss
        findings_lost_to_dedup=_batch_dedup_loss,
        # Sprint F207F: feed source dominance telemetry
        feed_findings_by_source=tuple(_feed_by_source),
        dominant_feed_source=_dom_source,
        dominant_feed_share_pct=_dom_share_pct,
        feed_sources_successful=_successful,
        feed_source_cap_applied=False,
        # Sprint F207I: feed dominance scoring
        feed_dominance_score=_dominance_score,
        feed_balance_recommendation=_recommendation,
        estimated_per_source_soft_cap=_soft_cap,
    )


async def async_run_default_feed_batch(
    store: Any | None = None,
    max_entries_per_feed: int = 20,
    feed_concurrency: int = 3,
    query_context: str | None = None,
    per_feed_timeout_s: float = 45.0,
    batch_timeout_s: float = 300.0,
) -> FeedSourceBatchRunResult:
    """
    Run a one-shot batch over the default curated feed seeds (8AJ).

    Unchanged signature from 8AL.

    F164C: Uses get_runtime_feed_seeds() SSOT — returns ONLY curated_seed sources,
    pre-sorted by priority descending. topology_candidates are excluded at the
    accessor level (get_runtime_feed_seeds is the canonical curated_seed surface).
    """
    # F164C: use SSOT accessor — get_runtime_feed_seeds() returns ONLY curated_seed
    # sources, pre-sorted by priority descending. No manual filter needed.
    from hledac.universal.discovery.rss_atom_adapter import get_runtime_feed_seeds

    runtime_seeds = get_runtime_feed_seeds()
    return await async_run_feed_source_batch(
        sources=runtime_seeds,
        store=store,
        max_entries_per_feed=max_entries_per_feed,
        feed_concurrency=feed_concurrency,
        query_context=query_context,
        per_feed_timeout_s=per_feed_timeout_s,
        batch_timeout_s=batch_timeout_s,
    )
