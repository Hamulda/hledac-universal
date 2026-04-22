"""
Sprint 8AE: First live public OSINT pipeline wiring.

query -> discovery (8AC duckduckgo) -> fetch (8AD public_fetcher) ->
lightweight HTML extraction -> PatternMatcher (8X) -> quality gate (8W) ->
CanonicalFinding -> storage (8S/8R DuckDBShadowStore).

No LLM calls. No AO. No new storage schema.
All heavy I/O (HTML parsing, pattern scanning) offloaded via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import hashlib
import html.parser
import os
import re
import sys
import time
import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

MAX_EXTRACTED_TEXT_CHARS: int = 200_000
"""Hard cap on extracted text size per page."""

MAX_METADATA_PREPEND_CHARS: int = 500
"""Max chars of title+snippet prepended to extracted text for pattern scan context."""

_SOURCE_TYPE: str = "live_public_pipeline"
"""source_type value for all findings produced by this pipeline."""

_REPORT_SOURCE_TYPE: str = "report"
"""source_type value for generated OSINT reports."""

_DEFAULT_CONFIDENCE: float = 0.8

# P6: Top results for report generation
_REPORT_TOP_N: int = 5
"""Number of top results to include in OSINT report."""
"""Confidence for pipeline findings — executed but unverified."""

_FINDING_ID_CONTEXT_RADIUS: int = 100
"""Character radius around pattern hit for payload_text context window."""

# Sprint F150I: tier thresholds (additive, no new framework)
_QUALITY_TIER_VERY_GOOD = "very_good"
_QUALITY_TIER_GOOD = "good"
_QUALITY_TIER_OK = "ok"
_QUALITY_TIER_WEAK = "weak_low_signal"
_QUALITY_TIER_SKIP = "SKIP_WEAK"

# Sprint F161B: conversion truth consolidation
# Changes:
# - _compute_page_usable_fields: distinguish false-positive discovery from structural waste
# - _score_page_quality: pre-fetch skip for extremely low text BEFORE budget spent
# - New derived fields: discovery_false_positive, waste_category, structural_quality
# - Bounded: all additive, backward-compatible, M1-safe

_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3

# Adaptive fetch budget tiers: multiplier on base fetch_timeout_s
_FETCH_BUDGET_STRONG: float = 1.25   # very_good or discovery_score >= 0.7
_FETCH_BUDGET_NORMAL: float = 1.0    # ok, good
_FETCH_BUDGET_WEAK: float = 0.65     # weak_low_signal, low discovery score
_FETCH_BUDGET_SKIP: float = 0.0       # SKIP_WEAK — dead until Fix A in F150J

# Sprint F161B: pre-fetch text-length gate — BEFORE budget is spent
# Previously this check happened post-fetch in _score_page_quality (wasteful)
_PRE_FETCH_TEXT_MIN_CHARS: int = 150
"""Minimum extracted text chars to consider fetch worthwhile."""

# Sprint F163B: low-entropy gate — detect repetitive placeholder noise
_LOW_ENTROPY_UNIQUE_WORD_RATIO: float = 0.25

# Sprint F188B: CT winner slice — bounded CT subdomain injection
_CT_SUBDOMAIN_BOUND: int = 10
"""Max CT subdomains to inject as synthetic discovery hits."""
_CT_SUBDOMAIN_SCORE: float = 0.85
"""Discovery score assigned to CT-synthesized hits (high confidence)."""
_CT_QUERY_IS_DOMAIN_RE: re.Pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.\-*[a-zA-Z0-9\]]+\.[a-zA-Z]{2,}$")
"""Regex to detect domain-like query strings suitable for CT subdomain lookup."""

# Sprint F161B: discovery false-positive band — legitimate signal but no conversion
_DISCOVERY_FALSE_POSITIVE_THRESHOLD: float = 0.5
"""Discovery score above this with zero patterns = false positive, not waste."""

# Sprint F150J: pre-fetch skip threshold — below this score with no strong signal → SKIP tier
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
"""If discovery_score is below this AND no strong signal, skip fetch entirely."""

# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------


# Sprint F193B: Explicit fetch policy — policy-driven JS/DoH/stealth, not dormant defaults
from dataclasses import dataclass, field



@dataclass(frozen=True)
class FetchPolicy:
    """Bounded fetch policy for canonical public sprint."""
    use_js: bool = False
    use_doh: bool = False
    use_stealth: bool = False

    @classmethod
    def default(cls) -> "FetchPolicy":
        return cls()


    @classmethod
    def js_capable(cls) -> "FetchPolicy":
        return cls(use_js=True)

    @classmethod
    def tor_like(cls) -> "FetchPolicy":
        return cls(use_doh=True, use_stealth=True)




def _compute_fetch_policy(
    url: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    strong_signal: bool,
) -> FetchPolicy:
    """
    Sprint F193B: Policy-driven fetch policy — JS/DoH/stealth driven by signal
    strength and URL class, not just dormant defaults.

    Policy rules:
    - discovery_score >= 0.7 OR strong_signal → use_js (JS-heavy page likely)
    - Onion/I2P/Freenet → tor_like policy (use_doh + use_stealth)
    - discovery_reason contains 'ct_' → DoH (accuracy for CT-log sources)
    - discovery_score >= 0.5 with moderate signal → use_doh only
    - everything else → default (plain fetch)

    Bounded: no network calls, no external state.
    """
    if ".onion" in url or ".i2p" in url or ".b32.i2p" in url or ".freenet" in url:
        return FetchPolicy.tor_like()

    if discovery_score is not None and discovery_score >= 0.7:
        return FetchPolicy.js_capable()
    if strong_signal:
        return FetchPolicy.js_capable()
    if discovery_reason and "ct_" in discovery_reason:
        return FetchPolicy(use_doh=True)
    if discovery_score is not None and discovery_score >= 0.5:
        return FetchPolicy(use_doh=True)
    return FetchPolicy.default()



class PipelinePageResult(msgspec.Struct, frozen=True, gc=False):
    """Result of processing a single discovered page."""

    url: str
    fetched: bool
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    error: str | None = None
    quality_reason: str | None = None  # why page was good/weak/skipped
    discovery_score: float | None = None  # signal strength from discovery hit
    discovery_reason: str | None = None  # reason from discovery hit
    discovery_signal: bool = False  # True if hit had score >= 0.3 or reason
    # Sprint F150L: usable-value layer — conversion story per page
    usable_signal: bool = False  # True if page converted to usable value
    value_tier: str = "none"  # high | medium | low | waste
    resolution_reason: str = ""  # why this page resolved the way it did
    # Sprint F161B: conversion truth surfaces
    discovery_false_positive: bool = False  # True if discovery signal was legitimate but page converted to waste
    waste_category: str = ""  # "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: str = ""  # "" | "healthy" | "thin" | "dead"
    # Sprint F170D: fetch accessibility truth — failure_stage from FetchResult
    failure_stage: str | None = None  # validation | connection | tls | http | body | size
    # Sprint F171A: redirect truth surfaces — redirect-induced non-content vs weak conversion
    redirected: bool = False  # True when page was redirected (final_url != original_url)
    redirect_target: str | None = None  # redirect destination URL when redirected=True


class PipelineRunResult(msgspec.Struct, frozen=True, gc=False):
    """Top-level result of a full pipeline run."""

    query: str
    discovered: int
    fetched: int
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    patterns_configured: int
    pages: tuple[PipelinePageResult, ...]
    error: str | None = None
    # Sprint F150I: branch economics observability (additive)
    strong_pages: int = 0  # very_good tier, high yield
    weak_pages_skipped: int = 0  # SKIP_WEAK early exits (Fix B: was error-based, now quality_reason-based)
    low_value_fetches: int = 0  # fetched but matched nothing + poor quality
    # Sprint F150J: derived value counters
    discovery_strong_content_weak: int = 0  # discovery signal but zero pattern yield
    discovery_and_content_strong: int = 0  # both discovery signal and pattern yield
    # Sprint F150K: additional derived economics signals (additive)
    discovery_squandered: int = 0  # strong discovery hit but page quality weak
    noise_fetch_ratio: float = 0.0  # ratio of fetched pages that yielded zero patterns
    corroboration_vs_burn: float = 0.0  # corroboration signal vs pure budget burn
    public_next_action: str = ""  # operator-facing one-liner next action hint
    public_confidence_note: str = ""  # operator-facing confidence note
    # Sprint F150J: condensed public-branch verdict (additive dict)
    public_branch_verdict: dict = {}
    # Sprint F150L: usable-value run-level aggregates
    usable_findings_ratio: float = 0.0  # stored_findings / max(discovered, 1)
    discovery_to_findings_efficiency: float = 0.0  # discovery_and_content_strong / max(discovered, 1)
    quality_mix: str = ""  # high|medium|low|waste composition summary
    public_proof_grade: str = ""  # proof quality of the public branch run
    public_value_density: float = 0.0  # stored_findings / max(fetched, 1)
    top_waste_pattern: str = ""  # dominant reason pages went to waste (heuristic)
    # Sprint F161B: conversion truth run-level aggregates
    discovery_false_positive_count: int = 0  # pages with discovery signal but no conversion
    waste_category_counts: dict = {}  # {"structural": N, "signalless": N, "false_positive": N, "error": N}
    structural_health_ratio: float = 0.0  # fraction of fetched pages with structural_quality=healthy
    # Sprint F162B: factual value density + clean waste code
    factual_value_density: float = 0.0  # stored / fetched (real conversion density)
    run_waste_pattern_code: str = ""   # dominant waste category clean code
    waste_reason_breakdown: str = ""   # waste category distribution
    # Sprint F163B: backend degradation flag — true when fetch errors dominate discovery output
    backend_degraded: bool = False
    # Sprint F170D: lower-layer truth consumption — discovery block / fetch accessibility
    # None | "uma_emergency_abort" | "backend_error_no_fallback" | "backend_error_fallback_failed"
    public_discovery_blocker: str | None = None
    # True when any page had fetch accessibility failure (DNS/TLS/connection/timeout)
    public_fetch_accessibility_blocker: bool = False
    # None | "primary_failed_fallback_succeeded" | "primary_failed_fallback_failed" | "no_fallback_needed"
    public_discovery_fallback_state: str | None = None
    # Dominant failure mode across all pages and discovery
    dominant_public_failure_mode: str | None = None
    # Sprint F173C: zero-hit evidence — bounded surfaces for next gate
    # zero_hit_accessible_fetch_count: pages that were fetched (fetched=True) with 0 pattern matches
    # (distinct from discovery_strong_content_weak which includes SKIP-tier pages)
    zero_hit_accessible_fetch_count: int = 0
    # Sprint F188B: CT winner slice — bounded CT-discovered subdomain count (additive)
    ct_subdomain_injected: int = 0
    # F192E: CommonCrawl CDX — bounded CC-discovered archive URL count (additive)
    cc_archive_injected: int = 0
    # F193B: Academic discovery persisted findings count (additive)
    academic_findings_count: int = 0
    # P20: PastebinMonitor + GitHubSecretScanner telemetry (additive)
    pastebin_findings_count: int = 0
    github_secrets_count: int = 0
    # zero_hit_quality_reason_counts: breakdown of WHY zero-hit pages failed
    # keys are the specific quality_reason values from PipelinePageResult
    zero_hit_quality_reason_counts: dict = {}
    # zero_hit_title_samples: bounded title+URL sample for zero-hit pages (max 5, no raw text)
    zero_hit_title_samples: tuple = ()
    # public_zero_hit_summary: run-level structured summary for gate review
    public_zero_hit_summary: dict = {}


# -----------------------------------------------------------------------------
# UMA helpers
# -----------------------------------------------------------------------------


def _get_uma_state() -> tuple[str, bool]:
    """
    Read UMA status via 8AB surface.
    Returns (state_str, io_only_hint).
    Raises: propagates any exception from resource_governor.

    Sprint 8AK: Uses SSOT labels from resource_governor — no localUMA interpretation.
    """
    # Sprint 8AB surface — lazy import to avoid module-level side effects
    from hledac.universal.core.resource_governor import (
        evaluate_uma_state,
        sample_uma_status,
        UMA_STATE_EMERGENCY,
    )

    status = sample_uma_status()
    state = evaluate_uma_state(status.system_used_gib)
    io_only = status.io_only
    return state, io_only


# -----------------------------------------------------------------------------
# HTML extraction helpers
# -----------------------------------------------------------------------------


class _HTMLTextExtractor(html.parser.HTMLParser):
    """
    Lightweight HTMLParser that collects only text from body-level tags
    and collapses whitespace. Fail-soft: never raises on malformed HTML.
    """

    __slots__ = ("_in_body", "_chunks", "_last_end")

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._chunks: list[str] = []
        self._last_end = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]  # noqa: ARG002
    ) -> None:
        if tag in ("body", "div", "p", "tr", "li", "article", "section", "main"):
            if not self._chunks or self._chunks[-1] != " ":
                self._chunks.append(" ")
        elif tag in ("br", "hr"):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in (
            "body", "div", "p", "tr", "li", "article", "section", "main", "h1",
            "h2", "h3", "h4", "h5", "h6", "ul", "ol",
        ):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._chunks.append(stripped)
            if self._chunks[-1] != " ":
                self._chunks.append(" ")

    def get_text(self) -> str:
        result = "".join(self._chunks)
        # Collapse any runs of whitespace to single space
        result = re.sub(r"\s+", " ", result).strip()
        return result


def _html_to_text(html_content: str) -> str:
    """
    Convert HTML to plain text using stdlib HTMLParser.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    try:
        parser = _HTMLTextExtractor()
        parser.feed(html_content)
        text = parser.get_text()
    except Exception:
        # Defensive: fall back to stripping tags via regex
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = re.sub(r"\s+", " ", text).strip()
    return text


# -----------------------------------------------------------------------------
# Finding ID helper
# -----------------------------------------------------------------------------

def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """
    Deterministic finding ID via SHA-256 hash of pipeline inputs.
    hash() is forbidden (non-deterministic across processes).
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# -----------------------------------------------------------------------------
# Context window helper
# -----------------------------------------------------------------------------
# Sentinel: use a private module-level constant so the call site is self-explanatory
_NO_HIT_START = object()


def _pattern_context(
    text: str,
    start: int,
    end: int,
    radius: int = _FINDING_ID_CONTEXT_RADIUS,
) -> str:
    """
    Extract a context window around a pattern hit.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    if start is _NO_HIT_START or end is _NO_HIT_START:
        return text[:MAX_EXTRACTED_TEXT_CHARS]
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


# -----------------------------------------------------------------------------
# Text enrichment with discovery metadata (Sprint F150I)
# Prepend title/snippet to extracted text so pattern scanner gets better signal.
# Hard-capped, M1-safe, no new dependency.
# -----------------------------------------------------------------------------


def _enrich_text_with_metadata(
    title: str,
    snippet: str,
    extracted_text: str,
) -> str:
    """
    Build a bounded scan text from: [title] [snippet] [extracted_content].

    Rationale: title + snippet contain query-aware signal that raw HTML→text
    loses (e.g. search engine bolded terms). Prepending them gives pattern
    matcher better context without any LLM or external call.

    The result is hard-capped at MAX_EXTRACTED_TEXT_CHARS.
    """
    # Build metadata prefix bounded to MAX_METADATA_PREPEND_CHARS
    meta_parts: list[str] = []
    remaining_meta = MAX_METADATA_PREPEND_CHARS

    if title:
        title_trunc = title[:remaining_meta]
        meta_parts.append(title_trunc)
        remaining_meta -= len(title_trunc)

    if snippet and remaining_meta > 20:
        snippet_trunc = snippet[:remaining_meta]
        meta_parts.append(snippet_trunc)

    meta_prefix = "\n".join(meta_parts) + "\n---\n"

    # Hard cap: meta_prefix + extracted_text capped at MAX_EXTRACTED_TEXT_CHARS
    max_content = MAX_EXTRACTED_TEXT_CHARS - len(meta_prefix)
    if max_content < 0:
        # meta_prefix alone exceeds cap — truncate it
        meta_prefix = meta_prefix[:MAX_EXTRACTED_TEXT_CHARS]
        max_content = 0

    content = extracted_text[:max_content] if max_content > 0 else ""

    return meta_prefix + content


# -----------------------------------------------------------------------------
# Page quality scoring (Sprint F150I)
# Query-aware heuristic for fetch budget prioritization.
# Bounded, no ML, no external calls.
# -----------------------------------------------------------------------------


def _score_page_quality(
    *,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    query: str,
    extracted_text: str,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
) -> str:
    """
    Return a short quality tier string for a discovered page.

    Signals (compositional, no ML):
    - query-term density in title/snippet
    - URL structural depth
    - text richness (avg word len + word count)
    - discovery hit score / reason (if present)
    - rank priority (top-5 benefit of doubt)
    - pre-filter: skip extremely thin pages

    Returns one of:
      SKIP_WEAK: below minimum — skip immediately
      weak_low_signal: poor signals even after fetch
      ok: acceptable but not exceptional
      good: strong multi-dimensional signals
      very_good: exceptional signals, full investment warranted
    """
    # --- Discovery signal blend (additive, fail-soft) ------------
    has_discovery_signal = (
        (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
        or (discovery_reason is not None and discovery_reason.strip() != "")
    )
    strong_discovery = (
        discovery_score is not None and discovery_score >= 0.7
    )

    query_lower = query.lower()
    query_terms = frozenset(query_lower.split())

    # --- Pre-filter: skip pages with almost no content BEFORE signal scoring ---
    # Sprint F163B: apply text-length gate first — avoids wasting compute on dead pages
    if len(extracted_text) < _PRE_FETCH_TEXT_MIN_CHARS:
        return "SKIP_WEAK:very_low_text"

    # --- Signalless gate: very low word-level entropy = spam/placeholder ---
    # Sprint F163B: detect "lorem ipsum" / repetitive filler / template noise
    # This is orthogonal to text length — catches thin-but-long pages
    words = extracted_text.split()
    if len(words) >= 10:
        unique_ratio = len(frozenset(w.lower() for w in words)) / len(words)
        if unique_ratio < 0.25:
            return "SKIP_WEAK:low_entropy"

    # --- Title query-term density --------------------------------
    title_words = frozenset(hit_title.lower().split())
    title_query_hits = len(query_terms & title_words)
    title_has_query = title_query_hits > 0

    # --- Snippet query-term density -----------------------------
    snippet_words = frozenset(hit_snippet.lower().split())
    snippet_query_hits = len(query_terms & snippet_words)
    snippet_has_query = snippet_query_hits > 0

    # --- URL structural signal -----------------------------------
    url_has_path = "/" in hit_url and len(hit_url.split("/")) > 3

    # --- Text richness -----------------------------------------
    text_len = len(extracted_text)
    word_count = len(extracted_text.split())
    avg_word_len = text_len / max(word_count, 1)
    text_is_meaningful = avg_word_len >= 3.5 and word_count >= 50

    # --- Composite scoring --------------------------------------
    signals_good = sum([
        title_has_query,
        snippet_has_query,
        url_has_path,
        text_is_meaningful,
    ])
    if strong_discovery:
        signals_good += 1  # discovery bonus

    rank_bonus = hit_rank < 5

    # --- Tier determination -------------------------------------
    if signals_good >= 4 or (signals_good >= 3 and (rank_bonus or strong_discovery)):
        return "very_good"
    elif signals_good >= 3:
        return "good"
    elif signals_good >= 2:
        return "ok"
    elif signals_good >= 1:
        return "ok"
    elif has_discovery_signal and text_is_meaningful and text_len > 1000:
        return "ok:no_query_signal"
    else:
        return "weak_low_signal"


# -----------------------------------------------------------------------------
# Per-page usable-value computation (Sprint F150L)
# Bounded heuristic — no new analysis, purely derived from existing buckets.
# -----------------------------------------------------------------------------


def _compute_page_usable_fields(
    *,
    fetched: bool,
    matched_patterns: int,
    stored_findings: int,
    quality_reason: str | None,
    discovery_signal: bool,
    discovery_score: float | None,
    error: str | None,
    extracted_text_len: int = 0,
) -> tuple[bool, str, str, bool, str, str]:
    """
    Derive usable_signal, value_tier, resolution_reason, discovery_false_positive,
    waste_category, structural_quality from existing page data.

    usable_signal: page contributed to real output (stored findings or strong signal).
    value_tier: conversion quality — high/medium/low/waste.
    resolution_reason: human-readable why the page resolved as it did.
    discovery_false_positive: True if discovery signal was legitimate but page wasted.
    waste_category: "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: "" | "healthy" | "thin" | "dead"

    All derived from existing fields — no new heavy analysis.
    """
    if not fetched or error is not None:
        tier = "waste"
        reason = f"unfetched_or_error:{error or 'none'}"
        false_pos = False
        waste_cat = "error"
        structural = "dead"
        return False, tier, reason, false_pos, waste_cat, structural

    if stored_findings > 0:
        tier = "high"
        reason = "stored_findings"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0 and discovery_signal:
        tier = "medium"
        reason = "patterns_found_discovery_signal"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0:
        tier = "medium"
        reason = "patterns_found_no_discovery"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    # Fetched but nothing matched — distinguish waste categories
    # Sprint F163B: signalless detection BEFORE SKIP_WEAK — signalless is a real category
    if not discovery_signal:
        # No discovery signal at all — signalless waste (not structural)
        tier = "waste"
        reason = quality_reason or "no_discovery_signal"
        false_pos = False
        waste_cat = "signalless"
        structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
        return False, tier, reason, false_pos, waste_cat, structural

    if discovery_score is not None and discovery_score >= _DISCOVERY_FALSE_POSITIVE_THRESHOLD:
        # Sprint F161B: legitimate discovery signal, no pattern yield = false positive
        tier = "low"
        reason = "discovery_signal_no_patterns"
        false_pos = True
        waste_cat = "false_positive"
        structural = "healthy" if extracted_text_len >= _PRE_FETCH_TEXT_MIN_CHARS else "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    if quality_reason is not None and quality_reason.startswith("SKIP_WEAK"):
        tier = "waste"
        reason = f"quality_skip:{quality_reason}"
        false_pos = False
        waste_cat = "structural"
        structural = "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    # Final fallback
    tier = "waste"
    reason = quality_reason or "no_match_no_signal"
    false_pos = False
    waste_cat = "signalless"
    structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
    return False, tier, reason, false_pos, waste_cat, structural


# -----------------------------------------------------------------------------
# PatternMatcher helpers
# -----------------------------------------------------------------------------


def _get_patterns_configured_count() -> int:
    """Return current pattern count from singleton registry (0 if dirty/empty)."""
    state = sys.modules["hledac.universal.patterns.pattern_matcher"]._matcher_state
    return len(state._registry_snapshot) if state._registry_snapshot else 0


# -----------------------------------------------------------------------------
# Per-page finding extraction
# -----------------------------------------------------------------------------


async def _extract_live_public_findings_from_page(
    *,
    query: str,
    url: str,
    hit_label: str,
    hit_pattern: str,
    hit_value: str,
    hit_start: int,
    hit_end: int,
    page_text: str,
) -> tuple:  # CanonicalFinding — imported lazily to satisfy runtime
    """
    Construct CanonicalFinding for a single PatternHit.
    All heavy work (context extraction) offloaded to thread executor.
    """
    # Lazy import to avoid TYPE_CHECKING-only circular issues at runtime
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    loop = asyncio.get_running_loop()

    # Extract context in thread to avoid blocking event loop
    context: str = await loop.run_in_executor(
        None, _pattern_context, page_text, hit_start, hit_end
    )

    # Truncate to hard cap (double-check since context is already bounded)
    if len(context) > MAX_EXTRACTED_TEXT_CHARS:
        context = context[:MAX_EXTRACTED_TEXT_CHARS]

    finding_id = _make_finding_id(query, url, hit_label, hit_pattern, hit_value)

    # provenance: (source, url, hit_label, hit_pattern)
    provenance: tuple[str, ...] = ("duckduckgo", url, hit_label or "", hit_pattern)

    finding = CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=_SOURCE_TYPE,
        confidence=_DEFAULT_CONFIDENCE,
        ts=time.time(),
        provenance=provenance,
        payload_text=context,
    )
    return (finding,)


# -----------------------------------------------------------------------------
# Single-page fetch + extract + match + store
# -----------------------------------------------------------------------------


async def _fetch_and_process_page(
    *,
    semaphore: asyncio.Semaphore,
    query: str,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    fetch_timeout_s: float,
    fetch_max_bytes: int,
    store: Any | None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
    vector_store: Any | None = None,
    graph: Any | None = None,
) -> PipelinePageResult:
    """
    Fetch one URL, extract text, scan patterns, optionally store findings.
    Discovery signal (score/reason) is propagated for observability and
    used for adaptive budget selection — fail-soft when absent.
    """
    # --- Adaptive budget tier ----------------------------------------
    has_signal = (
        (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
        or (discovery_reason is not None and discovery_reason.strip() != "")
    )
    strong_signal = discovery_score is not None and discovery_score >= 0.7

    # Sprint F150J Fix A: wire SKIP tier — was dead code before
    low_discovery = (
        discovery_score is not None
        and discovery_score < _DISCOVERY_SKIP_THRESHOLD
        and not strong_signal
    )
    if low_discovery:
        budget_mult = _FETCH_BUDGET_SKIP  # 0.0 → true skip
    elif discovery_score is not None and discovery_score >= 0.85:
        budget_mult = _FETCH_BUDGET_STRONG
    elif strong_signal or has_signal:
        budget_mult = _FETCH_BUDGET_NORMAL
    else:
        budget_mult = _FETCH_BUDGET_WEAK

    effective_timeout = fetch_timeout_s * budget_mult
    # Don't call fetch at all for SKIP tier (budget_mult == 0)
    skip_fetch = budget_mult <= 0

    async with semaphore:
        # ---- Fetch -----------------------------------------------------------
        if skip_fetch:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason="SKIP_WEAK:weak_discovery",
                discovery_signal=has_signal,
                discovery_score=discovery_score,
                error="skipped:weak_discovery",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url,
                fetched=False,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
                error="skipped:weak_discovery",
                quality_reason="SKIP_WEAK:weak_discovery",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=None,
                redirected=False,
                redirect_target=None,
            )

        # Sprint F193B: Policy-driven fetch — JS/DoH/stealth driven by signal, not dormant defaults
        policy = _compute_fetch_policy(hit_url, discovery_score, discovery_reason, strong_signal)

        try:
            result = await asyncio.wait_for(
                _ASYNC_FETCH_PUBLIC_TEXT(
                    hit_url, effective_timeout, fetch_max_bytes,
                    use_stealth=policy.use_stealth,
                    use_js=policy.use_js,
                    use_doh=policy.use_doh,
                ),
                timeout=effective_timeout + 5.0,
            )
        except asyncio.TimeoutError:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"fetch_timeout_after_{effective_timeout:.1f}s",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url, fetched=False, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"fetch_timeout_after_{effective_timeout:.1f}s",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage="connection",
                redirected=False,
                redirect_target=None,
            )
        except asyncio.CancelledError:
            raise  # [I6] propagate, never swallow
        except Exception as exc:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"fetch_exception:{type(exc).__name__}:{exc}",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url, fetched=False, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"fetch_exception:{type(exc).__name__}:{exc}",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage="connection",
                redirected=False,
                redirect_target=None,
            )

        # Unpack fetch result (FetchResult frozen struct)
        # Sprint F170D: also read failure_stage for accessibility truth
        # Sprint F171A: also read redirected + redirect_target for redirect-induced non-content detection
        fetched_text: str | None
        fetched_failure_stage: str | None = None
        fetched_redirected: bool = False
        fetched_redirect_target: str | None = None
        if hasattr(result, "text"):
            fetched_text = result.text
            fetched_failure_stage = getattr(result, "failure_stage", None)
            fetched_redirected = getattr(result, "redirected", False)
            fetched_redirect_target = getattr(result, "redirect_target", None)
        else:
            fetched_text = None

        if not fetched_text:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error="fetch_text_none_or_empty",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error="fetch_text_none_or_empty",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=None,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
            )

        # ---- Extract ---------------------------------------------------------
        loop = asyncio.get_running_loop()
        try:
            extracted_text: str = await loop.run_in_executor(
                None, _html_to_text, fetched_text
            )
        except Exception as exc:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"html_extract_failed:{exc}",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"html_extract_failed:{exc}",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
            )

        # Hard cap
        if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
            extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

        # Build quality signal from discovery metadata + text metrics
        # Sprint F150I: query-aware page selection, bounded signal scoring
        quality_reason = _score_page_quality(
            hit_url=hit_url,
            hit_title=hit_title or "",
            hit_snippet=hit_snippet or "",
            hit_rank=hit_rank,
            query=query,
            extracted_text=extracted_text,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
        )

        # Skip very-low-quality pages early — preserve fetch budget
        if quality_reason.startswith("SKIP_WEAK"):
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=quality_reason, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=None,
                extracted_text_len=len(extracted_text),
            )
            return PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=None, quality_reason=quality_reason,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
            )

        # Sprint F150I: enrich extracted text with discovery metadata
        # This gives pattern scanner better signal (title/snippet hints present)
        scan_text = _enrich_text_with_metadata(
            hit_title or "", hit_snippet or "", extracted_text
        )

        # Free raw HTML reference early
        del fetched_text

        # ---- Pattern scan ----------------------------------------------------
        # 8X surface — run in thread executor; use enriched text
        try:
            loop = asyncio.get_running_loop()
            hits: list = await loop.run_in_executor(
                None, _SYNC_MATCH_TEXT, scan_text
            )
        except Exception:
            hits = []
        if hits is None:
            hits = []

        matched_count = len(hits)

        # FÁZE P9: Stream graph entities per-page (pattern scan results)
        if graph is not None and hits:
            _add_pattern_hits_to_graph(hits, graph)
        if matched_count == 0:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=quality_reason, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=None,
                extracted_text_len=len(extracted_text),
            )
            return PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                quality_reason=quality_reason,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
            )

        # ---- Per-page dedup: (label, pattern, value) exact dedup -----------
        # F182D: Order changed from (value,label,pattern) to match feed pipeline (label,pattern,value)
        seen: set[tuple[str, str, str]] = set()
        unique_findings: list = []

        for hit in hits:
            key = (hit.label or "", hit.pattern, hit.value)
            if key in seen:
                continue
            seen.add(key)

            findings_tuple = await _extract_live_public_findings_from_page(
                query=query,
                url=hit_url,
                hit_label=hit.label if hit.label else "",
                hit_pattern=hit.pattern,
                hit_value=hit.value,
                hit_start=hit.start,
                hit_end=hit.end,
                page_text=extracted_text,
            )
            unique_findings.append(findings_tuple[0])

        # F180B FIX: accepted_count = quality-gated count (before storage)
        # stored_count = actual storage success (lmdb_success)
        # These are SEPARATE — accepted does NOT imply stored (DuckDB may fail)
        accepted_count = 0
        stored_count = 0

        # ---- Storage ---------------------------------------------------------
        if store is not None and unique_findings:
            try:
                # DuckDBShadowStore quality-gated ingest surface (8W + 8S)
                store_results = await store.async_ingest_findings_batch(unique_findings)
                # F180B FIX: accepted_count from quality gate, stored_count from lmdb_success.
                # accepted_count = number that passed quality gate (may not all reach storage)
                # stored_count = number that actually reached LMDB WAL successfully
                for sr in store_results:
                    if isinstance(sr, dict):
                        # FindingQualityDecision: has "accepted" key
                        if sr.get("accepted"):
                            accepted_count += 1
                        # ActivationResult: has "lmdb_success" key
                        if sr.get("lmdb_success"):
                            stored_count += 1
                    else:
                        # msgspec struct
                        if getattr(sr, "accepted", False):
                            accepted_count += 1
                        if getattr(sr, "lmdb_success", False):
                            stored_count += 1

                # P11: Write to memory manager after DuckDB storage succeeds
                # This enables RAG context for future queries
                if memory_manager is not None and session_id is not None:
                    for finding in unique_findings:
                        try:
                            finding_id = getattr(finding, "finding_id", None) or str(hash(hit_url))
                            memory_entry = {
                                "finding_id": finding_id,
                                "query": query,
                                "url": hit_url,
                                "timestamp": time.time(),
                                "payload_text": getattr(finding, "payload_text", ""),
                                "source_type": getattr(finding, "source_type", ""),
                                "confidence": getattr(finding, "confidence", 0.0),
                                "provenance": list(getattr(finding, "provenance", ())),
                            }
                            await memory_manager.put(
                                session_id,
                                f"finding:{finding_id}",
                                memory_entry
                            )
                        except Exception:
                            # Fail-soft: memory write errors don't fail the page
                            pass

            except asyncio.CancelledError:
                raise  # [I6]
            except Exception:
                # Fail-soft: storage error does not fail the page
                # accepted_count/stored_count already set to 0 (pre-loop init) on error
                pass

            # P13: Store page text embedding in vector store
            # Only for html/text content, not binary
            if vector_store is not None and extracted_text and len(extracted_text) > 50:
                try:
                    from hledac.universal.embedding_pipeline import generate_embeddings_async
                    from hledac.universal.brain.model_manager import get_model_manager

                    # Use extracted_text (not enriched scan_text) for embedding
                    # P16: Wrap with embedding_lifecycle() for proper M1 memory management
                    model_manager = get_model_manager()
                    async with model_manager.embedding_lifecycle():
                        embeddings = await generate_embeddings_async([extracted_text])
                    if embeddings is not None and len(embeddings) > 0:
                        # Use URL-based ID for vector lookup
                        finding_id_for_vec = _make_finding_id(
                            query=query,
                            url=hit_url,
                            label="page_text",
                            pattern="embedding",
                            value=extracted_text[:100]
                        )
                        # P16: Ensure embeddings are float32 numpy array with correct shape
                        import numpy as np
                        vec = np.asarray(embeddings[0], dtype=np.float32)
                        vector_store.add_vectors(
                            [finding_id_for_vec],
                            vec.reshape(1, -1),
                            index_type="text"
                        )
                        logger.debug(f"[P16] Stored embedding for {hit_url[:50]}")
                except Exception:
                    # Fail-soft: vector storage errors don't fail the page
                    pass

        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=matched_count,
            stored_findings=stored_count,
            quality_reason=quality_reason,
            discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=None,
            extracted_text_len=len(extracted_text),
        )
        return PipelinePageResult(
            url=hit_url,
            fetched=True,
            matched_patterns=matched_count,
            accepted_findings=accepted_count,
            stored_findings=stored_count,
            quality_reason=quality_reason,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            discovery_signal=has_signal,
            usable_signal=usable_signal,
            value_tier=value_tier,
            resolution_reason=resolution_reason,
            discovery_false_positive=discovery_false_positive,
            waste_category=waste_category,
            structural_quality=structural_quality,
            failure_stage=fetched_failure_stage,
            redirected=fetched_redirected,
            redirect_target=fetched_redirect_target,
        )


# -----------------------------------------------------------------------------
# Placeholder fetch/match imports (patched in tests; real code uses 8AD/8X)
# -----------------------------------------------------------------------------

_ASYNC_FETCH_PUBLIC_TEXT: Any = None  # patched by tests
_SYNC_MATCH_TEXT: Any = None  # patched by tests
_PATCHED_BY_ENSURE: bool = False  # guard: once _ensure_patched() runs, don't re-overwrite


def _patch_fetcher_and_matcher(
    fetch_fn: Any, match_fn: Any
) -> None:
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT
    _ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    _SYNC_MATCH_TEXT = match_fn


def _ensure_patched() -> None:
    """Ensure runtime fetch/matcher are patched from 8AD/8X modules.

    Idempotent: once called (by production code), never re-runs.
    Tests patch _ASYNC_FETCH_PUBLIC_TEXT and _SYNC_MATCH_TEXT BEFORE calling
    the pipeline; this guard preserves those patches by skipping the real import
    once any code (tests or production) has triggered this function.
    """
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT, _PATCHED_BY_ENSURE
    if _PATCHED_BY_ENSURE:
        return
    _PATCHED_BY_ENSURE = True
    if _ASYNC_FETCH_PUBLIC_TEXT is None:
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        _ASYNC_FETCH_PUBLIC_TEXT = async_fetch_public_text
    if _SYNC_MATCH_TEXT is None:
        from hledac.universal.patterns.pattern_matcher import match_text
        _SYNC_MATCH_TEXT = match_text


# -----------------------------------------------------------------------------
# P6: OSINT Report Generation
# -----------------------------------------------------------------------------


def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """
    Deterministic finding ID via SHA-256 hash of pipeline inputs.
    hash() is forbidden (non-deterministic across processes).
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


async def _generate_and_store_report(
    query: str,
    pages: tuple,
    store: Any | None,
    hermes_engine: Any | None,
    vector_store: Any | None = None,
) -> str:
    """
    P6: Generate OSINT report from top findings and store in DuckDB.
    P13: Integrate vector search, MMR reranking, and RRF fusion for RAG context.

    Collects top 5 pages by matched_patterns count, generates report via Hermes
    (if available), and stores with source_type='report'.

    Fail-soft: returns empty string on any error. Pipeline continues regardless.

    Args:
        query: Research query
        pages: Tuple of PipelinePageResult
        store: Optional DuckDBShadowStore instance
        hermes_engine: Optional Hermes3Engine instance (if None, report generation skipped)
        vector_store: Optional VectorStore instance for semantic search

    Returns:
        Generated report text, or empty string if skipped/failed
    """
    if hermes_engine is None:
        return ""  # No Hermes, skip report generation

    # P13: Vector search for RAG context with MMR reranking
    vector_candidates: list[tuple[str, float]] = []
    if vector_store is not None:
        try:
            from hledac.universal.embedding_pipeline import embed_query_async
            from hledac.universal.context_optimization.mmr import maximal_marginal_relevance
            from utils.ranking import rrf_fuse
            from hledac.universal.brain.model_manager import get_model_manager

            # Generate query embedding with proper lifecycle management
            model_manager = get_model_manager()
            async with model_manager.embedding_lifecycle():
                query_vec = await embed_query_async(query)

                # Query vector store for similar documents
                raw_similar = vector_store.query(query_vec, k=10, index_type="text")
                if raw_similar:
                    logger.info(f"[P13] Vector search found {len(raw_similar)} similar docs")
                    vector_candidates = raw_similar

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[P13] Vector search failed: {e}")
            vector_candidates = []

    # Collect top N pages by matched_patterns (proxy for IOC density)
    sorted_pages = sorted(
        pages,
        key=lambda p: (p.matched_patterns or 0, p.accepted_findings or 0),
        reverse=True
    )
    top_pages = sorted_pages[:_REPORT_TOP_N]

    if not top_pages:
        return ""  # No findings to report on

    # P13: Build pattern_matcher ranked list for RRF fusion
    pattern_ranked: list[tuple[str, float]] = []
    for p in top_pages:
        url = getattr(p, 'url', '') or ''
        score = (p.matched_patterns or 0) + (p.accepted_findings or 0) * 0.5
        if url:
            pattern_ranked.append((url, score))

    # P13: Fuse vector search results with pattern matcher results using RRF
    if vector_candidates and pattern_ranked:
        try:
            fused_ids = rrf_fuse([vector_candidates, pattern_ranked], k=60)
            logger.info(f"[P13] RRF fused {len(fused_ids)} results")
            # Use fused order for context building
            fused_url_order = fused_ids[:_REPORT_TOP_N]
        except Exception:
            # Fallback to pattern matcher order if RRF fails
            fused_url_order = [url for url, _ in pattern_ranked[:_REPORT_TOP_N]]
    else:
        fused_url_order = [url for url, _ in pattern_ranked[:_REPORT_TOP_N]]

    # Build context from fused/ranked pages
    context_items: list[str] = []
    url_to_page = {getattr(p, 'url', ''): p for p in pages}

    for url in fused_url_order:
        page = url_to_page.get(url)
        if page is None:
            continue
        # Format page info as context item
        ioc_count = page.matched_patterns or 0
        accepted = page.accepted_findings or 0
        title = getattr(page, 'discovery_reason', '') or getattr(page, 'quality_reason', '') or url

        context_items.append(
            f"URL: {url}\n"
            f"Title/Reason: {title}\n"
            f"IOC count: {ioc_count}, Accepted findings: {accepted}"
        )

    # If no context from fusion, fall back to top_pages
    if not context_items:
        for p in top_pages:
            ioc_count = p.matched_patterns or 0
            accepted = p.accepted_findings or 0
            url = getattr(p, 'url', '') or ''
            title = getattr(p, 'discovery_reason', '') or getattr(p, 'quality_reason', '') or url

            context_items.append(
                f"URL: {url}\n"
                f"Title/Reason: {title}\n"
                f"IOC count: {ioc_count}, Accepted findings: {accepted}"
            )

    # FÁZE P14: Build routing context and determine best model
    route_context: dict = {
        "urls": [getattr(p, 'url', '') for p in top_pages if hasattr(p, 'url')],
        "content_type": "html",  # Default content type
    }

    # Check for images in page data (vision routing)
    has_images = any(
        getattr(p, 'redirected', False) and 'image' in (getattr(p, 'redirect_target', '') or '').lower()
        for p in top_pages
    )
    if has_images:
        route_context["has_images"] = True

    # P16: Route via MoERouter.route() to get expert IDs for generator selection
    expert_ids: list[str] = []
    try:
        from hledac.universal.brain.moe_router import create_moe_router, MoERouter
        router = await create_moe_router()
        if router is not None:
            expert_ids = await router.route(query, context_items)
            logger.info(f"[P16] MoE experts: {expert_ids} for query: {query[:50]}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[P16] MoE routing failed: {e}")
        expert_ids = []

    # FÁZE P14: Route to appropriate model (legacy fallback)
    from hledac.universal.brain.moe_router import route as moe_route
    model_choice = moe_route(query, route_context)
    logger.info(f"[P14] MoE route: {model_choice} for query: {query[:50]}")

    # Generate report based on routed model
    report_text = ""
    try:
        if model_choice == "vision":
            # Vision encoder placeholder (P15) - return placeholder text
            # In P15 this would call actual vision encoder
            report_text = "[image description] " + "\n".join(context_items[:3])
            logger.info("[P14] Using vision encoder placeholder")

        elif model_choice == "modernbert":
            # ModernBERT summarizer - use ModernBERT for summarization
            # Try to use modernbert summarizer if available
            try:
                from hledac.universal.brain.modernbert_engine import ModernBertEngine
                modernbert = ModernBertEngine()
                report_text = await modernbert.summarize(context_items)
                logger.info("[P14] Using ModernBERT summarizer")
            except Exception as e:
                # Fallback to Hermes if ModernBERT unavailable
                logger.warning(f"[P14] ModernBERT failed, falling back to Hermes: {e}")
                report_text = await hermes_engine.generate_report(query, context_items)
        else:
            # Default: Hermes3 for general text generation
            report_text = await hermes_engine.generate_report(query, context_items)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[REPORT] Generation failed: {e}")
        return ""

    if not report_text:
        return ""  # Report generation returned empty

    # Store report as CanonicalFinding with source_type='report'
    if store is not None:
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            report_id = _make_finding_id(
                query=query,
                url="synthetic://report",
                label="osint_report",
                pattern="synthetic",
                value=report_text[:200]  # Use first 200 chars as value for ID
            )

            report_finding = CanonicalFinding(
                finding_id=report_id,
                query=query,
                source_type=_REPORT_SOURCE_TYPE,
                confidence=0.7,  # Moderate confidence for generated content
                ts=time.time(),
                provenance=("report_generation", hermes_engine.__class__.__name__),
                payload_text=report_text,
            )

            # Store using existing async API
            await store.async_ingest_findings_batch([report_finding])
            import logging
            logging.getLogger(__name__).info(f"[REPORT] Stored report {report_id[:8]} for query: {query[:50]}")

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[REPORT] Storage failed: {e}")
            # Fail-soft: report was generated but not stored - still return it

    return report_text


# -----------------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------------


def _query_looks_like_domain(query: str) -> bool:
    """
    Sprint F188B: Detect if query is a domain name suitable for CT subdomain lookup.

    Returns True for "example.com", "api.example.com", "*.example.com".
    Returns False for "apple inc", "what is DNS", "site:example.com".
    """
    q = query.strip()
    if not q or len(q) > 253:
        return False
    return bool(_CT_QUERY_IS_DOMAIN_RE.match(q))


def _extract_base_domain(domain: str) -> str:
    """
    Sprint F188B: Extract base domain from a domain string for CT scanner input.

    "www.example.com" -> "example.com"
    "api.example.com" -> "example.com"
    "example.com"     -> "example.com"
    "*.example.com"   -> "example.com"

    Returns the input unchanged if it can't be parsed.
    """
    # Remove wildcard prefix
    if domain.startswith("*."):
        domain = domain[2:]
    parts = domain.split(".")
    if len(parts) >= 3:
        # Heuristic: last two parts are the registered domain
        return ".".join(parts[-2:])
    return domain


# =============================================================================
# FÁZE P9: GraphManager integration
# =============================================================================


def _add_pattern_hits_to_graph(hits: list, graph: Any) -> None:
    """
    FÁZE P9: Stream pattern hits into GraphManager.

    Called per-page after pattern scan — lightweight, no heavy ops.
    Max 1000 entries per page enforced (M1 8GB safe).
    """
    if graph is None or not hits:
        return
    try:
        seen: set[tuple[str, str]] = set()
        for hit in hits[:1000]:  # Hard cap per page
            entity_type = hit.label or "unknown"
            value = hit.value
            key = (entity_type, value)
            if key in seen:
                continue
            seen.add(key)
            graph.add_entity(entity_type, value)
    except Exception:
        pass  # Fail-soft: graph errors don't fail pipeline


async def _inject_ct_subdomain_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """
    Sprint F188B: Thin CT winner-slice adapter.

    If query looks like a domain, call the CT scanner to get subdomains,
    synthesize them as high-confidence discovery hits, and prepend to the
    existing hits tuple.

    Fail-soft: scanner errors or non-domain queries return hits unchanged.
    Bounded: at most _CT_SUBDOMAIN_BOUND subdomains injected.
    M1-safe: CT scanner owns its cache; shared session reuse via async_session.

    This is NOT a new discovery world — it augments existing discovery hits
    with CT-sourced subdomains within the same fetch batch.
    """
    global _CT_SCANNER_GET_SUBDOMAINS

    if not hits or not _query_looks_like_domain(query):
        return hits

    _ensure_ct_scanner_patched()
    if _CT_SCANNER_GET_SUBDOMAINS is None:
        return hits

    base_domain = _extract_base_domain(query)

    # Sprint F188B: use shared aiohttp session for connection pooling
    shared_session = None
    try:
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        shared_session = await async_get_aiohttp_session()
    except Exception:
        pass

    try:
        subdomains: list[str] = await _CT_SCANNER_GET_SUBDOMAINS(
            base_domain, async_session=shared_session
        )
    except Exception:
        subdomains = []

    if not subdomains:
        return hits

    subdomains = subdomains[:_CT_SUBDOMAIN_BOUND]

    # Sprint F188B: synthesize CT hits as simple structs with the same
    # attribute interface that _fetch_and_process_page expects.
    # Attribute-based access: hit.url, hit.title, hit.snippet, hit.rank, hit.score, hit.reason
    class _CTHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, rank: int):
            self.url = url
            self.title = f"[CT] {url}"
            self.snippet = f"Certificate Transparency subdomain of {base_domain}"
            self.rank = rank
            self.score = _CT_SUBDOMAIN_SCORE
            self.reason = "ct_subdomain"

    ct_hits = tuple(
        _CTHit(f"https://{subdomain}", idx) for idx, subdomain in enumerate(subdomains)
    )
    return ct_hits + hits


# F192E: CommonCrawl domain discovery injection
_CC_SCANNER_LOOKUP: Any = None


def _query_looks_like_domain_for_cc(query: str) -> bool:
    """
    F192E: Detect if query is a domain name suitable for CommonCrawl CDX lookup.

    Returns True for "example.com", "*.example.com", "site:example.com".
    Returns False for "apple inc", "what is DNS", etc.
    """
    q = query.strip()
    if not q or len(q) > 253:
        return False
    # Match: "example.com", "*.example.com", "site:example.com", "domain:example.com"
    import re
    _CC_DOMAIN_RE = re.compile(
        r"^(?:\*?\.)?[a-zA-Z0-9][a-zA-Z0-9.\-*[a-zA-Z0-9]\.[a-zA-Z]{2,}$"
        r"|^(?:site|domain):"
    )
    return bool(_CC_DOMAIN_RE.match(q))


async def _inject_commoncrawl_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """
    F192E: Thin CommonCrawl CDX injection as discovery augmentation.

    CommonCrawl CDX API is a domain index (historical URL archive), not a
    general search engine. It only activates for domain-like queries.

    This is NOT a new discovery world — it augments existing discovery hits
    with CC-sourced archived URLs within the same fetch batch.

    Fail-soft: CC errors or non-domain queries return hits unchanged.
    Bounded: at most 20 CC results injected.
    M1-safe: adapter owns its HTTP calls, shared session reuse.
    """
    global _CC_SCANNER_LOOKUP

    if not hits or not _query_looks_like_domain_for_cc(query):
        return hits

    # Lazy-patch CommonCrawl scanner
    if _CC_SCANNER_LOOKUP is None:
        try:
            from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

            class _MinimalStealth:
                async def get(self, url: str) -> str:
                    from hledac.universal.network.session_runtime import async_get_aiohttp_session
                    s = await async_get_aiohttp_session()
                    async with s.get(url) as r:
                        return await r.text()

            _CC_SCANNER_LOOKUP = CommonCrawlAdapter(stealth=_MinimalStealth())
        except Exception:
            return hits

    # Extract domain from query (strip site:/domain: prefix)
    import re
    clean_domain = re.sub(r"^(site|domain):", "", query.strip(), flags=re.IGNORECASE).strip()
    if not clean_domain:
        return hits

    try:
        cc_results: list = await _CC_SCANNER_LOOKUP.search(clean_domain, max_results=20)
    except Exception:
        return hits

    if not cc_results:
        return hits

    # Synthesize CC hits as simple attribute-based objects (same interface as CT hits)
    class _CCHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, title: str, snippet: str, rank: int):
            self.url = url
            self.title = title
            self.snippet = snippet
            self.rank = rank
            self.score = 0.75  # F192E: CC hits get strong baseline score
            self.reason = "commoncrawl_archive"

    cc_hits = tuple(
        _CCHit(
            url=r.get("url", ""),
            title=r.get("title", ""),
            snippet=r.get("snippet", ""),
            rank=idx,
        )
        for idx, r in enumerate(cc_results[:20])
    )
    # Prepend CC hits to give them priority in the fetch batch
    return cc_hits + hits


# Sprint F193A: Onion discovery + scraping block
_ONION_HIT_MAX = 5
_ONION_CIRCUIT_FAIL_LIMIT = 3
_onion_circuit_state = {"failures": 0, "opened_at": 0.0}
_onion_circuit_lock = asyncio.Lock()


def _onion_circuit_is_open() -> bool:
    """Check if onion circuit breaker is open."""
    if _onion_circuit_state["failures"] < _ONION_CIRCUIT_FAIL_LIMIT:
        return False
    import time
    if time.time() - _onion_circuit_state["opened_at"] >= 60.0:
        _onion_circuit_state["failures"] = 0
        _onion_circuit_state["opened_at"] = 0.0
        return False
    return True


def _onion_circuit_record_failure() -> None:
    """Record a failure in the onion circuit breaker."""
    import time
    _onion_circuit_state["failures"] += 1
    if _onion_circuit_state["failures"] >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_state["opened_at"] = time.time()
        logger.warning("[F193A] Onion circuit breaker OPEN — pausing 60s")


async def _inject_onion_hits(
    hits: tuple,
    query: str,
    store: "DuckDBShadowStore",
) -> int:
    """
    Sprint F193A: Onion discovery + scraping via Tor.

    Discovers .onion URLs via Ahmia search and scrapes them using
    Tor-capable async_fetch_public_text(). Converts results to CanonicalFinding
    and stores via duckdb_store.

    Bounded: max 5 onion hits, circuit breaker after 3 failures, fail-soft.
    Returns number of onion findings stored.
    """
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    # Quick check: skip if circuit is open
    if _onion_circuit_is_open():
        return 0

    # Detect .onion URLs in existing hits (already discovered)
    onion_urls: list[str] = []
    for hit in hits:
        url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else None)
        if url and ".onion" in url.lower():
            onion_urls.append(url if url.startswith("http") else f"http://{url}")

    if not onion_urls:
        return 0

    onion_urls = onion_urls[:_ONION_HIT_MAX]

    findings: list[CanonicalFinding] = []
    ts_now = time.time()
    failure_count = 0

    for onion_url in onion_urls:
        try:
            result = await async_fetch_public_text(
                onion_url,
                timeout_s=30.0,
                max_bytes=200_000,
            )
            if result.error or result.text is None:
                failure_count += 1
                continue

            content = result.text
            pf_id = hashlib.sha256(
                f"{query}\x00{onion_url}\x00onion_discovery".encode()
            ).hexdigest()[:16]

            findings.append(CanonicalFinding(
                finding_id=pf_id,
                query=query,
                source_type="onion_discovery",
                confidence=0.55,
                ts=ts_now,
                provenance=("onion_discovery", onion_url),
                payload_text=content[:500] if content else None,
            ))

        except Exception as e:
            logger.debug(f"[F193A] Onion fetch {onion_url}: {e}")
            failure_count += 1
            if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
                _onion_circuit_record_failure()
                break

    if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_record_failure()

    if findings and store is not None:
        try:
            await store.async_ingest_findings_batch(findings)
            logger.info(f"[F193A] Stored {len(findings)} onion findings")
        except Exception as e:
            logger.debug(f"[F193A] Onion findings persist failed: {e}")

    return len(findings)


async def async_run_live_public_pipeline(
    query: str,
    store: "DuckDBShadowStore | None" = None,
    max_results: int = 10,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2_000_000,
    fetch_concurrency: int = 5,
    hermes_engine: Any | None = None,
    graph: Any | None = None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    vector_store: Any | None = None,
    run_loop: bool = False,  # P16: If True, run ResearchLoop after pipeline
    rl_steps: int = 0,  # P17: Number of RL steps (0 = use time limit)
    enqueue_hypothesis_pivot: Any | None = None,  # Sprint F193B: bounded feedback seam
) -> PipelineRunResult:
    """
    Sprint 8AE: Live public OSINT pipeline.

    Orchestration-only: wires existing 8AC/8AD/8X/8W/8S components.
    P6: Optional Hermes3Engine for OSINT report generation.
    P11: Optional MemoryManager for persistent RAG history.

    Parameters
    ----------
    query:
        Research query string (passed to CanonicalFinding.query).
    store:
        Optional DuckDBShadowStore instance. If None, storage is a no-op
        and only counting happens.
    max_results:
        Maximum discovery hits to process (default 10).
    fetch_timeout_s:
        Per-fetch operation timeout in seconds (applied per-page via 8AD API).
    fetch_max_bytes:
        Maximum bytes to fetch per page.
    fetch_concurrency:
        Maximum concurrent fetches in the batch.
    memory_manager:
        Optional MemoryManager instance for persistent RAG history.
    session_id:
        Optional session ID for memory manager. If None, uses query hash.

    Returns
    -------
    PipelineRunResult with typed counts and per-page error breakdown.
    """
    # Ensure hot-path imports are resolved
    _ensure_patched()

    # P11: Initialize session ID for memory manager
    if session_id is None:
        import hashlib
        session_id = hashlib.sha256(query.encode()).hexdigest()[:16]

    # P11: Load relevant RAG history from memory manager (if available)
    rag_context: list[dict] = []
    if memory_manager is not None:
        try:
            history = await memory_manager.get_session_history(session_id, limit=50)
            # Extract payload_text from past findings for RAG context
            for entry in history:
                value = entry.get("value", {})
                if isinstance(value, dict):
                    payload = value.get("payload_text", "")
                    if payload:
                        rag_context.append({
                            "query": value.get("query", ""),
                            "payload": payload[:500],  # Truncate for context
                            "timestamp": value.get("timestamp", 0),
                        })
        except Exception:
            rag_context = []  # Fail-soft: memory errors don't fail pipeline

    # ---- UMA check -----------------------------------------------------------
    # Sprint 8AK: SSOT labels from resource_governor — no local string literals
    from hledac.universal.core.resource_governor import (
        UMA_STATE_EMERGENCY,
        UMA_STATE_CRITICAL,
        UMA_STATE_OK,
    )

    uma_state = UMA_STATE_OK
    try:
        uma_state, _ = _get_uma_state()
    except Exception:
        pass  # Defensive: proceed with ok state

    if uma_state == UMA_STATE_EMERGENCY:
        return PipelineRunResult(
            query=query,
            discovered=0,
            fetched=0,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=_get_patterns_configured_count(),
            pages=(),
            error="uma_emergency_abort",
            public_discovery_blocker="uma_emergency_abort",
            public_fetch_accessibility_blocker=False,
            public_discovery_fallback_state=None,
            dominant_public_failure_mode="uma_emergency_abort",
        )

    effective_concurrency = fetch_concurrency
    if uma_state == UMA_STATE_CRITICAL or uma_state == UMA_STATE_EMERGENCY:
        effective_concurrency = 1

    semaphore = asyncio.Semaphore(effective_concurrency)

    # ---- Discovery (8AC) -----------------------------------------------------
    discovery_error: str | None = None
    hits: tuple = ()

    try:
        # 8AC surface — duckduckgo_search passive discovery
        discovery_result = await _ASYNC_DISCOVERY_SEARCH(query, max_results)
        if hasattr(discovery_result, "hits"):
            hits = discovery_result.hits
        elif isinstance(discovery_result, dict):
            hits = discovery_result.get("hits", ())

        err_val = discovery_result.get("error") if isinstance(discovery_result, dict) else getattr(discovery_result, "error", None)
        if err_val:
            discovery_error = str(err_val)
    except asyncio.CancelledError:
        raise  # [I6]
    except Exception as exc:
        discovery_error = f"discovery_exception:{type(exc).__name__}:{exc}"
        hits = ()

    if not hits:
        return PipelineRunResult(
            query=query,
            discovered=0,
            fetched=0,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=_get_patterns_configured_count(),
            pages=(),
            error=discovery_error or "discovery_empty",
            public_proof_grade="no_discovery",
            public_discovery_blocker=discovery_error if discovery_error else "no_discovery",
            public_fetch_accessibility_blocker=False,
            public_discovery_fallback_state="primary_failed_fallback_failed" if discovery_error else None,
            dominant_public_failure_mode=discovery_error if discovery_error else "no_discovery",
        )

    # P16: Academic discovery integration — run after DuckDuckGo discovery
    # Max 3 concurrent queries via shared semaphore
    academic_findings_count = 0
    if store is not None:
        try:
            from hledac.universal.intelligence.academic_discovery import search_academic_all
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            # P16: Use semaphore to limit concurrent academic queries to 3
            academic_semaphore = asyncio.Semaphore(3)

            async def limited_academic_search():
                async with academic_semaphore:
                    return await search_academic_all(query, max_results=10, rate_limit=50)

            academic_results = await limited_academic_search()

            # Convert academic papers to CanonicalFinding and store
            all_papers = []
            for source, papers in academic_results.items():
                for paper in papers:
                    all_papers.append(paper)

            if all_papers:
                academic_findings = []
                for paper in all_papers[:20]:  # Limit to 20 academic findings
                    paper_id = hashlib.sha256(
                        f"{query}\x00{paper.get('link', '')}\x00academic".encode()
                    ).hexdigest()[:16]
                    provenance = ("academic", source, paper.get('title', ''))
                    academic_finding = CanonicalFinding(
                        finding_id=paper_id,
                        query=query,
                        source_type="academic_discovery",
                        confidence=0.7,
                        ts=time.time(),
                        provenance=provenance,
                        payload_text=f"{paper.get('title', '')}\n{paper.get('abstract', '')}".strip()[:500],
                    )
                    academic_findings.append(academic_finding)

                if academic_findings:
                    await store.async_ingest_findings_batch(academic_findings)
                    academic_hits_count = len(academic_findings)
                    academic_findings_count = academic_hits_count
                    logger.info(f"[P16] Stored {academic_hits_count} academic findings")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[P16] Academic discovery failed: {e}")

    # Sprint F188B: CT winner-slice injection — augment discovery with CT subdomains
    # One bounded adapter: _inject_ct_subdomain_hits. Fail-soft, shared session reuse.
    # NOT a new discovery world — same fetch batch processes both DDG and CT hits.
    original_hit_count = len(hits)
    hits = await _inject_ct_subdomain_hits(hits, query)
    ct_injected = len(hits) - original_hit_count

    # F192E: CommonCrawl CDX domain injection — thin archival URL augmentation
    # One bounded adapter: _inject_commoncrawl_hits. Fail-soft, domain queries only.
    # NOT a new discovery world — same fetch batch processes DDG, CT, and CC hits.
    original_hit_count = len(hits)
    hits = await _inject_commoncrawl_hits(hits, query)
    cc_injected = len(hits) - original_hit_count

    # Sprint F193A: Onion discovery + scraping block
    # Discover .onion URLs via Ahmia search, scrape via Tor-capable async_fetch_public_text.
    # Bounded: max 5 onion hits, circuit breaker after 3 failures, fail-soft.
    # Produces CanonicalFinding with source_type="onion_discovery".
    onion_findings_count = 0
    if store is not None:
        try:
            onion_findings_count = await _inject_onion_hits(hits, query, store)
        except Exception as e:
            logger.debug(f"[F193A] Onion discovery failed: {e}")

    # P20: PastebinMonitor + GitHubSecretScanner — run only when query contains
    # a domain name or organization identifier (limits API calls to targeted searches)
    pastebin_findings_count = 0
    github_secrets_count = 0
    if store is not None:
        try:
            import re as _re
            _DOMAIN_ORG_RE = _re.compile(
                r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
            )
            _match = _DOMAIN_ORG_RE.search(query)
            if _match:
                target = _match.group()
                logger.info(f"[P20] PastebinMonitor targeting: {target}")

                # PastebinMonitor — rate-limited async paste scraping
                from hledac.universal.intelligence.pastebin_monitor import run as pastebin_run
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                paste_findings = await pastebin_run(target)
                if paste_findings:
                    p20_findings = []
                    for pf in paste_findings:
                        pf_id = hashlib.sha256(
                            f"{query}\x00{pf.uri}\x00pastebin".encode()
                        ).hexdigest()[:16]
                        masked = pf.masked_secrets()
                        p20_findings.append(CanonicalFinding(
                            finding_id=pf_id,
                            query=query,
                            source_type="pastebin_monitor",
                            confidence=0.6,
                            ts=time.time(),
                            provenance=("pastebin", pf.source, target),
                            payload_text=(
                                f"uri={pf.uri}\n"
                                f"emails={pf.emails}\n"
                                f"ips={pf.ip_addresses}\n"
                                f"masked_secrets={masked}\n"
                                f"snippet={pf.context_snippet[:300]}"
                            ),
                        ))
                    if p20_findings:
                        await store.async_ingest_findings_batch(p20_findings)
                        pastebin_findings_count = len(p20_findings)
                        logger.info(f"[P20] Stored {pastebin_findings_count} pastebin findings")

                # GitHubSecretScanner — public repo scanning for exposed secrets
                # Strip TLD to get potential org name, scan up to 10 public repos
                org_candidate = _match.group().rsplit(".", 1)[0]
                from hledac.universal.intelligence.github_secret_scanner import (
                    search_org_secrets,
                    scan_repo,
                )

                # Try org-level scan first; fall back to direct repo scan
                gh_findings: list[CanonicalFinding] = []
                if org_candidate:
                    try:
                        gh_results = await search_org_secrets(org_candidate)
                    except Exception:
                        gh_results = []

                    for gf in gh_results:
                        gf_id = hashlib.sha256(
                            f"{query}\x00{gf.file_path}\x00{gf.pattern}\x00github".encode()
                        ).hexdigest()[:16]
                        gh_findings.append(CanonicalFinding(
                            finding_id=gf_id,
                            query=query,
                            source_type="github_secret_scanner",
                            confidence=0.55,
                            ts=time.time(),
                            provenance=("github", gf.pattern, org_candidate),
                            payload_text=(
                                f"pattern={gf.pattern}\n"
                                f"file={gf.file_path}\n"
                                f"line={gf.line}\n"
                                f"context={gf.context[:300]}"
                            ),
                        ))

                if gh_findings:
                    await store.async_ingest_findings_batch(gh_findings)
                    github_secrets_count = len(gh_findings)
                    logger.info(f"[P20] Stored {github_secrets_count} GitHub secret findings")
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[P20] Pastebin/GitHub scan failed: {e}")

    # ---- Fetch batch ---------------------------------------------------------
    # Per-call semaphore, no global batch timeout
    tasks: list[asyncio.Task] = []
    for hit in hits:
        # Sprint F150I: extract discovery score/reason if present (additive, fail-soft)
        hit_score: float | None = getattr(hit, "score", None)
        if hit_score is None and hasattr(hit, "__getitem__"):
            try:
                hit_score = float(hit[4]) if len(hit) > 4 else None
            except (ValueError, TypeError):
                hit_score = None

        hit_reason: str | None = getattr(hit, "reason", None)
        if hit_reason is None and hasattr(hit, "__getitem__"):
            try:
                hit_reason = str(hit[5]) if len(hit) > 5 else None
            except (ValueError, TypeError):
                hit_reason = None

        task = asyncio.create_task(
            _fetch_and_process_page(
                semaphore=semaphore,
                query=query,
                hit_url=hit.url if hasattr(hit, "url") else str(hit[2]),
                hit_title=hit.title if hasattr(hit, "title") else str(hit[1] if len(hit) > 1 else ""),
                hit_snippet=hit.snippet if hasattr(hit, "snippet") else str(hit[3] if len(hit) > 3 else ""),
                hit_rank=hit.rank if hasattr(hit, "rank") else 0,
                fetch_timeout_s=fetch_timeout_s,
                fetch_max_bytes=fetch_max_bytes,
                store=store,
                memory_manager=memory_manager,
                session_id=session_id,
                discovery_score=hit_score,
                discovery_reason=hit_reason,
                vector_store=vector_store,
                graph=graph,
            )
        )
        tasks.append(task)

    # asyncio.gather preserves order; _check_gathered enforces [I6][I7][I8]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # _check_gathered propagates CancelledError [I6] and BaseException [I7]
    from hledac.universal.network.session_runtime import _check_gathered
    ok_results, error_results = _check_gathered(raw_results)

    # Assemble page results in discovery order (skipping exceptions)
    all_page_results: list[PipelinePageResult] = []
    for item in ok_results:
        if isinstance(item, PipelinePageResult):
            all_page_results.append(item)

    # ---- Aggregate -----------------------------------------------------------
    total_discovered = len(hits)
    total_fetched = sum(1 for p in all_page_results if p.fetched)
    total_matched = sum(p.matched_patterns for p in all_page_results)
    total_accepted = sum(p.accepted_findings for p in all_page_results)
    total_stored = sum(p.stored_findings for p in all_page_results)
    patterns_cfg = _get_patterns_configured_count()

    # Sprint F150J Fix B: branch economics counters
    # Fix weak_pages_skipped: SKIP_WEAK post-fetch pages have error=None (not error!=None)
    strong_pages = sum(
        1 for p in all_page_results
        if p.quality_reason == "very_good"
    )
    weak_pages_skipped = sum(
        1 for p in all_page_results
        if p.quality_reason is not None and p.quality_reason.startswith("SKIP_WEAK")
    )
    # low-value = fetched but poor quality + no matches
    low_value_fetches = sum(
        1 for p in all_page_results
        if p.fetched
        and p.matched_patterns == 0
        and p.quality_reason in ("weak_low_signal", "ok:no_query_signal")
    )
    # Sprint F150J: additive derived counters for public-branch value assessment
    # discovery_strong_content_weak: discovery signal but page yielded nothing
    discovery_strong_content_weak = sum(
        1 for p in all_page_results
        if (p.discovery_signal and p.matched_patterns == 0)
    )
    # discovery_and_content_strong: both discovery signal and pattern yield
    discovery_and_content_strong = sum(
        1 for p in all_page_results
        if p.discovery_signal and p.matched_patterns > 0
    )
    # Sprint F150K: discovery_squandered — strong discovery score but page quality weak
    # (promarněný strong discovery hit = high score but got SKIP_WEAK or weak_low_signal)
    # Sprint F162B: threshold aligned with _FETCH_BUDGET_STRONG = 0.85
    discovery_squandered = sum(
        1 for p in all_page_results
        if p.discovery_score is not None
        and p.discovery_score >= 0.85
        and p.quality_reason in ("weak_low_signal", "SKIP_WEAK:weak_discovery", "SKIP_WEAK:very_low_text")
    )
    # Sprint F150K: build derived value metrics
    fetched_pages = [p for p in all_page_results if p.fetched]
    fetched_count = len(fetched_pages)

    # noise_fetch_ratio: what fraction of fetched pages yielded zero patterns
    noise_fetch_ratio = (
        round(low_value_fetches / fetched_count, 3)
        if fetched_count > 0
        else 0.0
    )
    # waste_ratio = pages that consumed budget but yielded nothing
    waste_ratio = (
        round(low_value_fetches / fetched_count, 3)
        if fetched_count > 0
        else 0.0
    )
    # value_ratio = pages with actual pattern yield vs total discovered
    value_ratio = (
        round(discovery_and_content_strong / total_discovered, 3)
        if total_discovered > 0
        else 0.0
    )
    # public_branch_hint: one-liner signal quality label
    if strong_pages >= 2 and discovery_and_content_strong >= 2:
        public_branch_hint = "high_value"
    elif discovery_and_content_strong >= 1:
        public_branch_hint = "some_value"
    elif discovery_strong_content_weak >= 1:
        public_branch_hint = "weak_signal"
    elif weak_pages_skipped > 0 and fetched_count == 0:
        public_branch_hint = "skipped_low_quality"
    else:
        public_branch_hint = "low_value"

    # corroboration_vs_burn: strong signal corroboration vs pure budget drain
    # = (discovery_and_content_strong + strong_pages) / max(total_discovered, 1)
    corroboration_vs_burn = (
        round((discovery_and_content_strong + strong_pages) / max(total_discovered, 1), 3)
    )

    run_error: str | None = None
    if discovery_error:
        run_error = discovery_error
    elif error_results:
        # Surface first error
        err = error_results[0]
        run_error = f"batch_error:{type(err).__name__}:{err}"

    # Sprint F150K: operator-facing hints
    if strong_pages >= 2 and discovery_and_content_strong >= 2:
        public_next_action = "expand_public_branch"
        public_confidence_note = "high_yield_run"
    elif discovery_and_content_strong >= 1 and discovery_squandered == 0:
        public_next_action = "continue_public_branch"
        public_confidence_note = "positive_signal"
    elif discovery_squandered >= 1 and discovery_strong_content_weak >= 1:
        public_next_action = "review_discovery_quality"
        public_confidence_note = "squandered_hits_detected"
    elif noise_fetch_ratio >= 0.5:
        public_next_action = "drain_public_branch"
        public_confidence_note = "high_noise_ratio"
    elif weak_pages_skipped >= total_discovered * 0.5:
        public_next_action = "throttle_public_branch"
        public_confidence_note = "low_quality_majority"
    else:
        public_next_action = "hold_public_branch"
        public_confidence_note = "marginal_signal"

    public_branch_verdict = {
        "waste_ratio": waste_ratio,
        "value_ratio": value_ratio,
        "public_branch_hint": public_branch_hint,
        "strong_pages": strong_pages,
        "weak_pages_skipped": weak_pages_skipped,
        "discovery_strong_content_weak": discovery_strong_content_weak,
        "discovery_and_content_strong": discovery_and_content_strong,
        "low_value_fetches": low_value_fetches,
        "discovery_squandered": discovery_squandered,
        "noise_fetch_ratio": noise_fetch_ratio,
        "corroboration_vs_burn": corroboration_vs_burn,
        "public_next_action": public_next_action,
        "public_confidence_note": public_confidence_note,
    }

    # Sprint F150L: usable-value run-level aggregates
    usable_findings_ratio = round(total_stored / max(total_discovered, 1), 3)
    discovery_to_findings_efficiency = round(
        discovery_and_content_strong / max(total_discovered, 1), 3
    )
    public_value_density = round(total_stored / max(total_fetched, 1), 3)
    # Sprint F162B: factual_value_density uses fetched as denominator (real conversion density)
    factual_value_density = round(total_stored / max(total_fetched, 1), 3)

    # quality_mix: composition summary from per-page value_tiers
    tier_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "waste": 0, "none": 0}
    for p in all_page_results:
        tier = getattr(p, "value_tier", "none")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    mix_parts = [f"{v}{k[0]}" for k, v in tier_counts.items() if v > 0]
    quality_mix = "|".join(mix_parts) if mix_parts else "empty"

    # top_waste_pattern: dominant waste reason from existing buckets
    waste_reasons: dict[str, int] = {}
    for p in all_page_results:
        if getattr(p, "value_tier", "none") == "waste":
            reason = getattr(p, "resolution_reason", "unknown") or "unknown"
            waste_reasons[reason] = waste_reasons.get(reason, 0) + 1
    top_waste_pattern = (
        max(waste_reasons, key=lambda r: waste_reasons[r]) if waste_reasons else ""
    )

    # Sprint F161B: conversion truth run-level aggregates
    fetched_pages = [p for p in all_page_results if p.fetched]
    fetched_count = len(fetched_pages)

    discovery_false_positive_count = sum(
        1 for p in all_page_results if getattr(p, "discovery_false_positive", False)
    )

    # waste_category_counts: aggregate from per-page waste_category
    waste_category_counts = {"structural": 0, "signalless": 0, "false_positive": 0, "error": 0}
    for p in all_page_results:
        cat = getattr(p, "waste_category", "")
        if cat in waste_category_counts:
            waste_category_counts[cat] += 1

    # structural_health_ratio: fraction of fetched pages that are structurally healthy
    structural_health_ratio = (
        round(sum(1 for p in fetched_pages if getattr(p, "structural_quality", "") == "healthy") / max(fetched_count, 1), 3)
        if fetched_count > 0 else 0.0
    )

    # Sprint F162B: run_waste_pattern_code — dominant clean waste category code
    run_waste_pattern_code = (
        max(waste_category_counts, key=lambda k: waste_category_counts[k])
        if any(v > 0 for v in waste_category_counts.values())
        else ""
    )

    # Sprint F162B: waste_reason_breakdown — distribution of waste categories
    waste_reason_breakdown = "|".join(
        f"{v}{k[:3]}" for k, v in sorted(waste_category_counts.items()) if v > 0
    ) if any(v > 0 for v in waste_category_counts.values()) else "none"

    # Sprint F163B: backend_degraded — fetch errors dominate discovery output
    # Not "low value" — true infrastructure failure that makes content inaccessible
    # Threshold: >60% of all pages had fetch errors OR discovery failed with zero fetches
    _error_page_count = sum(1 for p in all_page_results if p.error is not None and "fetch_exception" in p.error)
    _error_dominated = total_discovered > 0 and _error_page_count / total_discovered > 0.6
    _backend_degraded = bool(_error_dominated or (discovery_error is not None and total_fetched == 0))

    # Sprint F163B: enhanced public_proof_grade — decouple backend failure from weak content
    # "no_discovery" and "empty" are discovery problems, not content problems
    # "backend_degraded" overrides everything below it — the content was never even evaluated
    if _backend_degraded:
        _derived_proof_grade = "backend_degraded"
    elif factual_value_density >= 0.5 and structural_health_ratio >= 0.7 and noise_fetch_ratio <= 0.3:
        _derived_proof_grade = "strong"
    elif factual_value_density >= 0.3 and noise_fetch_ratio <= 0.5:
        _derived_proof_grade = "moderate"
    elif factual_value_density > 0 or total_stored > 0:
        _derived_proof_grade = "weak"
    elif total_discovered > 0:
        _derived_proof_grade = "empty"
    else:
        _derived_proof_grade = "no_discovery"

    # Sprint F163B: embed backend_degraded and public_proof_grade into verdict dict
    public_branch_verdict["backend_degraded"] = _backend_degraded
    public_branch_verdict["public_proof_grade"] = _derived_proof_grade

    # Sprint F170D: lower-layer truth consumption
    # Read fallback_triggered from discovery_result
    fallback_triggered: str | None = getattr(discovery_result, "fallback_triggered", None)

    # F185A DF-3 FIX: replace hardcoded if/elif chain with explicit dictionary.
    # Key: duckduckgo_adapter.py fallback_triggered string → public pipeline enum string.
    # This eliminates the silent-fail risk when new fallback_triggered variants are added.
    _FALLBACK_STATE_MAP: dict[str, str] = {
        "primary_backend_failed_fallback_succeeded": "primary_failed_fallback_succeeded",
        "primary_backend_failed_fallback_failed": "primary_failed_fallback_failed",
    }
    public_discovery_fallback_state = _FALLBACK_STATE_MAP.get(fallback_triggered) or (
        "no_fallback_needed" if discovery_error is None else None
    )

    # F185A DF-3 FIX: same dictionary approach for public_discovery_blocker
    _BLOCKER_BY_BACKEND_ERROR: dict[str, str] = {
        "primary_backend_failed_fallback_failed": "backend_error_fallback_failed",
    }
    if uma_state == "UMA_STATE_EMERGENCY":
        public_discovery_blocker = "uma_emergency_abort"
    elif discovery_error is not None and fallback_triggered is None:
        public_discovery_blocker = "backend_error_no_fallback"
    else:
        public_discovery_blocker = _BLOCKER_BY_BACKEND_ERROR.get(fallback_triggered)

    # public_fetch_accessibility_blocker: True when any page had connectivity/TLS/timeout failure
    # failure_stage IN {connection, tls, http} OR network_error_kind signals accessibility issue
    _accessibility_failure_stages = {"connection", "tls", "http"}
    public_fetch_accessibility_blocker = any(
        p.failure_stage in _accessibility_failure_stages
        for p in all_page_results
    )

    # dominant_public_failure_mode: aggregate failure story
    # Priority: discovery blocker > fetch_accessibility_blocker > redirect_non_content > waste:*
    _failure_modes: list[str] = []
    if public_discovery_blocker:
        _failure_modes.append(public_discovery_blocker)
    if public_fetch_accessibility_blocker:
        _failure_modes.append("fetch_accessibility_blocker")
    # Sprint F171A: redirect-induced non-content — redirected AND ended as structural/signalless waste
    # Only triggers for pages that were actually fetched and found thin/dead content at redirect target
    _any_redirect_non_content = any(
        p.redirected and p.waste_category in ("structural", "signalless")
        for p in all_page_results
    )
    if _any_redirect_non_content:
        _failure_modes.append("redirect_non_content")
    # Add dominant waste category if present
    if run_waste_pattern_code and run_waste_pattern_code != "none":
        _failure_modes.append(f"waste:{run_waste_pattern_code}")
    dominant_public_failure_mode = _failure_modes[0] if _failure_modes else None

    # Sprint F173C: zero-hit evidence aggregation
    # zero_hit_accessible_fetch_count: pages that were fetched with 0 matches
    zero_hit_accessible_fetch_count = sum(
        1 for p in all_page_results
        if p.fetched and p.matched_patterns == 0
    )
    # zero_hit_quality_reason_counts: why zero-hit pages failed
    _zero_hit_reasons: dict[str, int] = {}
    _zero_hit_titles: list[tuple[str, str]] = []  # (title, url) pairs, bounded
    for p in all_page_results:
        if p.fetched and p.matched_patterns == 0 and p.quality_reason:
            _zero_hit_reasons[p.quality_reason] = _zero_hit_reasons.get(p.quality_reason, 0) + 1
        if p.fetched and p.matched_patterns == 0 and len(_zero_hit_titles) < 5:
            # Capture title+url for gate evidence (no raw text)
            p_title = getattr(p, "discovery_reason", "") or ""
            _zero_hit_titles.append((p_title, p.url))
    zero_hit_quality_reason_counts = _zero_hit_reasons
    zero_hit_title_samples = tuple(_zero_hit_titles)
    # public_zero_hit_summary: structured run-level summary
    public_zero_hit_summary = {
        "zero_hit_accessible_fetch_count": zero_hit_accessible_fetch_count,
        "zero_hit_unique_reasons": list(zero_hit_quality_reason_counts.keys()),
        "zero_hit_has_substantive_content": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "structural_quality", "") == "healthy"
            for p in all_page_results
        ),
        "zero_hit_has_signalless": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "waste_category", "") == "signalless"
            for p in all_page_results
        ),
        "zero_hit_has_false_positive": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "discovery_false_positive", False)
            for p in all_page_results
        ),
        "zero_hit_has_redirect_non_content": any(
            p.fetched and p.matched_patterns == 0
            and p.redirected and p.waste_category in ("structural", "signalless")
            for p in all_page_results
        ),
    }

    # P6: Generate OSINT report from top findings (if Hermes available)
    # Fail-soft: report generation is optional, pipeline continues regardless
    generated_report = ""
    if hermes_engine is not None and all_page_results:
        try:
            generated_report = await _generate_and_store_report(
                query=query,
                pages=tuple(all_page_results),
                store=store,
                hermes_engine=hermes_engine,
                vector_store=vector_store,
            )
        except Exception:
            generated_report = ""  # Fail-soft: report generation errors don't fail the pipeline

    # FÁZE P9: Export graph after pipeline completes (legacy path)
    if graph is not None and graph.node_count() > 0:
        try:
            export_path = os.path.expanduser("~/new_hledac_graph.html")
            graph.export_html(export_path)
        except Exception:
            pass  # Fail-soft: graph export errors don't fail pipeline

    # P17: Run ResearchLoop if --loop flag was set
    # Supports either rl_steps count (--rl-steps N) or time limit (default 5 min)
    if run_loop and hermes_engine is not None:
        try:
            from hledac.universal.loops.research_loop import ResearchLoop, ResearchResult
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            # P17: Default RL loop time limit (5 minutes)
            _RL_LOOP_TIME_LIMIT_S = 300.0

            research_loop = ResearchLoop(
                hypothesis_engine=hermes_engine,
                graph=graph,
                duckdb_store=store,
                memory_manager=memory_manager,
            )

            # P17: Run either N steps or until time limit
            rl_start_time = time.monotonic()
            step_count = 0
            prev_reward = 0.0

            while True:
                # Check step limit first
                if rl_steps > 0 and step_count >= rl_steps:
                    break

                # Check time limit
                elapsed = time.monotonic() - rl_start_time
                if elapsed >= _RL_LOOP_TIME_LIMIT_S:
                    logger.info(f"[P17] RL loop time limit reached ({elapsed:.1f}s)")
                    break

                # Run one RL iteration
                loop_result: ResearchResult = await research_loop.run_once(query)

                # P17: Store findings to DuckDB if available
                if store is not None and loop_result.findings:
                    try:
                        for finding_data in loop_result.findings:
                            finding_id = hashlib.sha256(
                                f"{query}\x00{str(finding_data)}\x00rl".encode()
                            ).hexdigest()[:16]
                            rl_finding = CanonicalFinding(
                                finding_id=finding_id,
                                query=query,
                                source_type="rl_research",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=("rl", loop_result.action),
                                payload_text=str(finding_data)[:500],
                            )
                            await store.async_ingest_findings_batch([rl_finding])
                    except Exception as e:
                        logger.warning(f"[P17] Failed to store RL finding: {e}")

                # P17: Store RL result to memory manager
                if memory_manager is not None and session_id is not None:
                    try:
                        await memory_manager.put(
                            session_id,
                            f"rl_result:{step_count}",
                            {
                                "action": loop_result.action,
                                "reward": loop_result.reward,
                                "findings_count": len(loop_result.findings),
                                "timestamp": time.time(),
                            }
                        )
                    except Exception:
                        pass  # Fail-soft

                prev_reward = loop_result.reward
                step_count += 1

                logger.info(
                    f"[P17] RL step {step_count}: action={loop_result.action}, "
                    f"reward={loop_result.reward:.3f}, findings={len(loop_result.findings)}"
                )

            logger.info(f"[P17] ResearchLoop completed {step_count} RL steps")

        except Exception as e:
            logger.warning(f"[P17] ResearchLoop.run_once failed: {e}")

    # FÁZE P18: Export to Obsidian Markdown and interactive HTML graph
    # Only export on successful pipeline completion (run_error is None)
    if run_error is None:
        try:
            from hledac.universal.export.export_manager import get_export_manager
            from hledac.universal.memory.memory_manager import export_session

            export_mgr = get_export_manager()

            # Build sources list from pages
            sources = [
                p.url for p in all_page_results
                if hasattr(p, 'url') and p.url
            ][:20]

            # Get findings from memory manager
            session_findings = []
            if memory_manager is not None and session_id is not None:
                try:
                    session_data = await export_session(session_id)
                    session_findings = session_data.get("findings", [])
                except Exception:
                    session_findings = []

            # Export metadata for YAML front matter
            export_metadata = {
                "query": query,
                "sources": sources,
                "tags": ["hledac", "osint", "public-pipeline"],
                "session_id": session_id,
                "stored_findings": str(total_stored),
                "discovered": str(total_discovered),
                "fetched": str(total_fetched),
            }

            # Export markdown report (Obsidian-compatible)
            try:
                md_path = export_mgr.export_markdown(
                    report=generated_report,
                    findings=session_findings,
                    file_path=None,  # Uses timestamp
                    metadata=export_metadata,
                )
                if md_path:
                    logger.info(f"[P18] Exported markdown to {md_path}")
            except Exception as e:
                logger.warning(f"[P18] Markdown export failed: {e}")

            # Export graph HTML (interactive pyvis)
            if graph is not None and graph.node_count() > 0:
                try:
                    html_path = export_mgr.export_graph_html(
                        graph_manager=graph,
                        file_path=None,  # Uses timestamp
                        title=f"Hledac Graph - {query[:50]}",
                    )
                    if html_path:
                        logger.info(f"[P18] Exported graph HTML to {html_path}")
                except Exception as e:
                    logger.warning(f"[P18] Graph HTML export failed: {e}")

        except Exception as e:
            logger.warning(f"[P18] Export failed: {e}")

    # P12: Hypothesis generation and ToT evaluation — POST-STORAGE variant
    # Runs AFTER findings are stored (real persisted evidence), not before fetch.
    # Canonical sprint: gated on store+hermes_engine (not memory_manager alone).
    # M1 8GB: bounded to 5 hypotheses, fail-soft, no ToT in hot path.
    # NOTE: This block executes BEFORE the return so it is always reachable.
    tot_solution_count = 0
    if store is not None and hermes_engine is not None and total_stored > 0:
        try:
            from hledac.universal.brain.hypothesis_engine import HypothesisEngine
            from hledac.universal.tot_integration import TotIntegrationLayer

            hypo_engine = HypothesisEngine()
            tot_layer = TotIntegrationLayer()

            # Query real persisted findings as hypothesis input
            recent_findings = await store.async_get_recent_findings(limit=20)
            if not recent_findings:
                logger.debug("[P12] No stored findings — hypothesis layer skipped")
            else:
                # Build context from real findings, not placeholder RAG/graph summary
                hypo_context = {
                    "query": query,
                    "stored_findings_count": total_stored,
                    "findings": [
                        {
                            "finding_id": f.finding_id if hasattr(f, "finding_id") else str(f.get("finding_id", "")),
                            "source_type": f.source_type if hasattr(f, "source_type") else str(f.get("source_type", "")),
                            "confidence": f.confidence if hasattr(f, "confidence") else float(f.get("confidence", 0.0)),
                            "provenance": f.provenance if hasattr(f, "provenance") else f.get("provenance", ""),
                        }
                        for f in recent_findings[:20]
                    ],
                }

                # Generate hypotheses from real stored findings
                hypotheses = await hypo_engine.generate_hypotheses_async(
                    context=hypo_context,
                    hermes_engine=hermes_engine
                )

                # Evaluate each hypothesis via ToT if complex — bounded to 5
                for hypo in hypotheses[:5]:
                    tot_result = await tot_layer.solve_with_tot(hypo)
                    if tot_result:
                        tot_solution_count += 1
                        try:
                            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                            tot_finding = CanonicalFinding(
                                finding_id=f"tot_{hashlib.sha256(tot_result.encode()).hexdigest()[:16]}",
                                query=query,
                                source_type="tot_synthesis",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=("tot", hypo[:100]),
                                payload_text=tot_result[:1000],
                            )
                            await store.async_ingest_findings_batch([tot_finding])
                        except Exception:
                            pass  # Fail-soft

                        # Sprint F193B: Bounded hypothesis → finding feedback loop
                        # Enqueue ToT result as hypothesis-driven pivot (depth=1 for first pass)
                        # This creates new scheduler work from hypothesis output without runaway
                        if enqueue_hypothesis_pivot is not None:
                            try:
                                # Extract key terms from hypothesis for pivot keywords
                                # Use first 200 chars of ToT result as pivot seed
                                pivot_seed = tot_result[:200].split()[:5]
                                for i, term in enumerate(pivot_seed):
                                    # Depth increases with each iteration (1, 2, 3...)
                                    enqueue_hypothesis_pivot(
                                        ioc_value=term.lower(),
                                        ioc_type="hypothesis",
                                        confidence=0.6,
                                        depth=1,
                                    )
                            except Exception:
                                pass  # Fail-soft: feedback is optional

        except Exception:
            pass  # P12: fail-soft, hypothesis generation is optional

    return PipelineRunResult(
        query=query,
        discovered=total_discovered,
        fetched=total_fetched,
        matched_patterns=total_matched,
        accepted_findings=total_accepted,
        stored_findings=total_stored,
        patterns_configured=patterns_cfg,
        pages=tuple(all_page_results),
        error=run_error,
        strong_pages=strong_pages,
        weak_pages_skipped=weak_pages_skipped,
        low_value_fetches=low_value_fetches,
        discovery_strong_content_weak=discovery_strong_content_weak,
        discovery_and_content_strong=discovery_and_content_strong,
        discovery_squandered=discovery_squandered,
        noise_fetch_ratio=noise_fetch_ratio,
        corroboration_vs_burn=corroboration_vs_burn,
        public_next_action=public_next_action,
        public_confidence_note=public_confidence_note,
        public_branch_verdict=public_branch_verdict,
        usable_findings_ratio=usable_findings_ratio,
        discovery_to_findings_efficiency=discovery_to_findings_efficiency,
        quality_mix=quality_mix,
        public_proof_grade=_derived_proof_grade,
        public_value_density=public_value_density,
        top_waste_pattern=top_waste_pattern,
        discovery_false_positive_count=discovery_false_positive_count,
        waste_category_counts=waste_category_counts,
        structural_health_ratio=structural_health_ratio,
        factual_value_density=factual_value_density,
        run_waste_pattern_code=run_waste_pattern_code,
        waste_reason_breakdown=waste_reason_breakdown,
        backend_degraded=_backend_degraded,
        public_discovery_blocker=public_discovery_blocker,
        public_fetch_accessibility_blocker=public_fetch_accessibility_blocker,
        public_discovery_fallback_state=public_discovery_fallback_state,
        dominant_public_failure_mode=dominant_public_failure_mode,
        zero_hit_accessible_fetch_count=zero_hit_accessible_fetch_count,
        zero_hit_quality_reason_counts=zero_hit_quality_reason_counts,
        zero_hit_title_samples=zero_hit_title_samples,
        public_zero_hit_summary=public_zero_hit_summary,
        # Sprint F188B: CT winner-slice telemetry
        ct_subdomain_injected=ct_injected,
        cc_archive_injected=cc_injected,
        # F193B: Academic discovery telemetry
        academic_findings_count=academic_findings_count,
        # P20: PastebinMonitor + GitHubSecretScanner telemetry
        pastebin_findings_count=pastebin_findings_count,
        github_secrets_count=github_secrets_count,
    )


# Placeholder for discovery (patched in tests)
_ASYNC_DISCOVERY_SEARCH: Any = None

# Sprint F188B: CT winner slice — optional scanner seam (patched in tests)
_CT_SCANNER_GET_SUBDOMAINS: Any = None


def _patch_discovery(search_fn: Any) -> None:
    global _ASYNC_DISCOVERY_SEARCH
    _ASYNC_DISCOVERY_SEARCH = search_fn


def _ensure_discovery_patched() -> None:
    global _ASYNC_DISCOVERY_SEARCH
    if _ASYNC_DISCOVERY_SEARCH is None:
        from hledac.universal.discovery.duckduckgo_adapter import (
            async_search_public_web,
        )
        _ASYNC_DISCOVERY_SEARCH = async_search_public_web


# Ensure discovery is patched on module import
_ensure_discovery_patched()


def _patch_ct_scanner(get_subdomains_fn: Any) -> None:
    """Patch in a CT scanner get_subdomains(domain, async_session) -> List[str]."""
    global _CT_SCANNER_GET_SUBDOMAINS
    _CT_SCANNER_GET_SUBDOMAINS = get_subdomains_fn


def _ensure_ct_scanner_patched() -> None:
    """Lazily patch the CT scanner from network.ct_log_scanner."""
    global _CT_SCANNER_GET_SUBDOMAINS
    if _CT_SCANNER_GET_SUBDOMAINS is not None:
        return
    try:
        from hledac.universal.network.ct_log_scanner import _CTLogScanner

        _scanner = _CTLogScanner(allow_external=True, cache_ttl_days=30)

        async def _get_subdomains(
            domain: str, async_session: Any = None
        ) -> list[str]:
            return await _scanner.get_subdomains(domain, async_session=async_session)

        _CT_SCANNER_GET_SUBDOMAINS = _get_subdomains
    except Exception:
        # Fail-soft: CT scanner unavailable
        _CT_SCANNER_GET_SUBDOMAINS = None
