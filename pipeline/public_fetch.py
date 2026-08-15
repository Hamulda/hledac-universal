"""Public pipeline fetch — _fetch_and_process_page.

Extracted from live_public_pipeline.py.
Handles: single-page fetch + extract + match + store loop.

DI seams (testable via _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT globals):
- _ASYNC_FETCH_PUBLIC_TEXT: async fetch function (patched by tests)
- _SYNC_MATCH_TEXT: sync pattern match function (patched by tests)
- _compute_fetch_policy: from public_discovery
- _score_page_quality: from public_patterns
- _build_public_finding: from public_acceptance
- _enrich_text_with_metadata: from public_patterns
- _compute_page_usable_fields: from public_patterns
- _add_pattern_hits_to_graph: from live_public_pipeline.py (local import)
- CanonicalFinding: from duckdb_store (lazy import)
- run_in_cpu_pool_async: from utils.rayon_pool (lazy import)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .public_acceptance import _build_public_finding
from .public_acceptance import _extract_live_public_findings_from_page
from .public_discovery import _compute_fetch_policy
from .public_patterns import _compute_page_usable_fields
from .public_patterns import _enrich_text_with_metadata
from .public_patterns import _html_to_text
from .public_patterns import _js_confidence_from_verdict
from .public_patterns import _make_finding_id
from .public_patterns import _score_page_quality
from .public_stages import PipelinePageResult
from hledac.universal.brain.model_manager import get_model_manager
from hledac.universal.embedding_pipeline import generate_embeddings_async
from hledac.universal.layers import get_temporal_signal_layer
from hledac.universal.layers.temporal_signal_layer import event_from_finding_like
from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator
from hledac.universal.utils.asyncx import parallel
from hledac.universal.utils.rayon_pool import run_in_cpu_pool_async
from hledac.universal.utils.patterns.pattern_matcher import PatternHit

import numpy as np
from core import aclose

# ----------------------------------------------------------------------
# DI globals — patched by tests; real code uses _ensure_patched()
# ----------------------------------------------------------------------
_ASYNC_FETCH_PUBLIC_TEXT: Any = None
_SYNC_MATCH_TEXT: Any = None
_PATCHED_BY_ENSURE: bool = False

# ----------------------------------------------------------------------
# Constants (from public_constants)
# ----------------------------------------------------------------------
MAX_EXTRACTED_TEXT_CHARS: int = 200_000
_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
_FETCH_BUDGET_SKIP: float = 0.0
_FETCH_BUDGET_STRONG: float = 1.0
_FETCH_BUDGET_NORMAL: float = 0.75
_FETCH_BUDGET_WEAK: float = 0.4
_PRE_FETCH_TEXT_MIN_CHARS: int = 50


# ----------------------------------------------------------------------
# Patch helpers (for tests)
# ----------------------------------------------------------------------


def _patch_fetcher_and_matcher(fetch_fn: Any, match_fn: Any) -> None:
    """Patch DI globals for test injection."""
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT
    _ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    _SYNC_MATCH_TEXT = match_fn


def _ensure_patched() -> None:
    """Ensure runtime fetch/matcher are patched from 8AD/8X modules."""
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT, _PATCHED_BY_ENSURE
    if _PATCHED_BY_ENSURE:
        return
    _PATCHED_BY_ENSURE = True
    if _ASYNC_FETCH_PUBLIC_TEXT is None:
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        _ASYNC_FETCH_PUBLIC_TEXT = async_fetch_public_text
    if _SYNC_MATCH_TEXT is None:
        from hledac.universal.utils.patterns.pattern_matcher import match_text

        _SYNC_MATCH_TEXT = match_text


# ----------------------------------------------------------------------
# Graph helper (inline to avoid circular imports)
# ----------------------------------------------------------------------


def _add_pattern_hits_to_graph(
    hits: list,
    graph: Any,
    observed_at: float | None = None,
) -> None:
    """Add pattern hits to graph (inline from live_public_pipeline.py).

    [META]-012: observed_at captures the HTTP fetch timestamp for temporal provenance.
    """
    if not hits or graph is None:
        return
    try:
        for hit in hits:
            label = getattr(hit, "label", None) or ""
            pattern = getattr(hit, "pattern", None) or ""
            value = getattr(hit, "value", None) or ""
            if label and pattern:
                try:
                    graph.upsert_ioc(
                        ioc_type=label,
                        value=value,
                        source="public_pipeline",
                        properties={"pattern": pattern},
                        observed_at=observed_at,
                    )
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001


# ----------------------------------------------------------------------
# Pipeline Context — holds all intermediate state
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelinePageContext:
    """Immutable context passed through the pipeline stages."""

    # Input parameters
    query: str = ""
    hit_url: str = ""
    hit_title: str = ""
    hit_snippet: str = ""
    hit_rank: int = 0
    fetch_timeout_s: float = 30.0
    fetch_max_bytes: int = 2_000_000
    store: Any = None
    memory_manager: Any = None
    session_id: str | None = None
    discovery_score: float | None = None
    discovery_reason: str | None = None
    vector_store: Any = None
    graph: Any = None

    # Computed early
    effective_timeout: float = 0.0
    skip_fetch: bool = False
    has_signal: bool = False
    strong_signal: bool = False
    budget_mult: float = 0.0

    @classmethod
    def from_params(
        cls,
        query: str,
        hit_url: str,
        hit_title: str,
        hit_snippet: str,
        hit_rank: int,
        fetch_timeout_s: float,
        fetch_max_bytes: int,
        store: Any,
        memory_manager: Any = None,
        session_id: str | None = None,
        discovery_score: float | None = None,
        discovery_reason: str | None = None,
        vector_store: Any = None,
        graph: Any = None,
    ) -> "PipelinePageContext":
        """Create context from function parameters."""
        has_signal = (
            (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
            or (discovery_reason is not None and discovery_reason.strip() != "")
        )
        strong_signal = discovery_score is not None and discovery_score >= 0.7
        low_discovery = (
            discovery_score is not None
            and discovery_score < _DISCOVERY_SKIP_THRESHOLD
            and not strong_signal
        )
        if low_discovery:
            budget_mult = _FETCH_BUDGET_SKIP
        elif discovery_score is not None and discovery_score >= 0.85:
            budget_mult = _FETCH_BUDGET_STRONG
        elif strong_signal or has_signal:
            budget_mult = _FETCH_BUDGET_NORMAL
        else:
            budget_mult = _FETCH_BUDGET_WEAK

        effective_timeout = fetch_timeout_s * budget_mult
        skip_fetch = budget_mult <= 0

        return cls(
            query=query,
            hit_url=hit_url,
            hit_title=hit_title or "",
            hit_snippet=hit_snippet or "",
            hit_rank=hit_rank,
            fetch_timeout_s=fetch_timeout_s,
            fetch_max_bytes=fetch_max_bytes,
            store=store,
            memory_manager=memory_manager,
            session_id=session_id,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            vector_store=vector_store,
            graph=graph,
            effective_timeout=effective_timeout,
            skip_fetch=skip_fetch,
            has_signal=has_signal,
            strong_signal=strong_signal,
            budget_mult=budget_mult,
        )


# ----------------------------------------------------------------------
# Terminal Reason State Machine
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TerminalState:
    """Represents the terminal state of a page fetch."""

    terminal_reason: str | None
    rejection_reason: str | None


class TerminalReasonMachine:
    """Determines terminal reason based on pipeline outcomes."""

    @staticmethod
    def from_js_skip(js_skip_reason: str | None) -> TerminalState:
        """Handle JS/browser skip reasons."""
        if js_skip_reason == "browser_unavailable":
            return TerminalState("skipped_browser_unavailable", "browser_unavailable")
        if js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
            return TerminalState("skipped_xml_or_feed", "xml_or_feed")
        return TerminalState(None, None)

    @staticmethod
    def from_storage_outcome(
        accepted_count: int,
        stored_count: int,
        storage_error: bool,
        quality_gate_rejected: bool,
    ) -> TerminalState:
        """Determine terminal state from storage results."""
        if accepted_count > 0 and not storage_error:
            return TerminalState(None, None)
        if storage_error:
            return TerminalState("rejected_storage_rejected", "storage_rejected")
        if quality_gate_rejected:
            return TerminalState("rejected_quality_gate", "quality_gate_rejected")
        return TerminalState("rejected_storage_rejected", "storage_rejected")


# ----------------------------------------------------------------------
# Skip Result Exception
# ----------------------------------------------------------------------


class _SkipWithResult(Exception):
    """Signal that processing should skip to end with a specific result."""

    def __init__(self, result: Any):
        self.result = result
        super().__init__()


# ----------------------------------------------------------------------
# Phase Result Dataclasses (slots=True for M1 8GB memory efficiency)
# ----------------------------------------------------------------------


@dataclass(slots=True)
class _FetchStageResult:
    """Result from Stage 4 (Fetch)."""

    result: Any  # FetchResult object or None
    failure_stage: str | None
    redirected: bool
    redirect_target: str | None
    js_skip_reason: str | None


@dataclass(slots=True)
class _TextExtractionResult:
    """Result from Stage 5 (Text Extraction)."""

    extracted_text: str
    quality_reason: str


@dataclass(slots=True)
class _JsRetryResult:
    """Result from Stage 7 (JS Retry)."""

    extracted_text: str
    quality_reason: str
    js_result: Any  # Original js_result for pattern scanning


@dataclass(slots=True)
class _PatternScanResult:
    """Result from Stage 8 (Pattern Scanning)."""

    hits: list
    matched_count: int
    observed_at: float | None
    js_result: Any  # Pass through for quality reasoning


@dataclass(slots=True)
class _DeduplicationResult:
    """Result from Stage 10 (Deduplication)."""

    deduped_hits: list
    unique_findings: list


@dataclass(slots=True)
class _StorageResult:
    """Result from Stage 11 (Storage & Embeddings)."""

    accepted_count: int
    stored_count: int
    storage_error: bool
    quality_gate_rejected: bool


# ----------------------------------------------------------------------
# Fetch Stage Helper (extracted for CC reduction)
# ----------------------------------------------------------------------


async def _execute_fetch_stage(
    hit_url: str,
    semaphore: asyncio.Semaphore,
    effective_timeout: float,
    fetch_max_bytes: int,
    policy: Any,
) -> _FetchStageResult:
    """Execute fetch with semaphore, timeout, and comprehensive error handling.

    Returns _FetchStageResult with all fetch metadata needed by subsequent stages.
    """
    _ensure_patched()

    async with semaphore:
        try:
            async with asyncio.timeout(effective_timeout + 5.0):
                result = await _ASYNC_FETCH_PUBLIC_TEXT(
                    hit_url,
                    effective_timeout,
                    fetch_max_bytes,
                    use_stealth=policy.use_stealth,
                    use_js=policy.use_js,
                    use_doh=policy.use_doh,
                )
        except TimeoutError:
            return _FetchStageResult(
                result=None,
                failure_stage="fetch_timeout",
                redirected=False,
                redirect_target=None,
                js_skip_reason=None,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return _FetchStageResult(
                result=None,
                failure_stage="fetch_error",
                redirected=False,
                redirect_target=None,
                js_skip_reason=None,
            )

    # Extract metadata from successful fetch
    return _FetchStageResult(
        result=result,
        failure_stage=getattr(result, "failure_stage", None),
        redirected=getattr(result, "redirected", False),
        redirect_target=getattr(result, "redirect_target", None),
        js_skip_reason=getattr(result, "js_renderer_skipped_reason", None),
    )


# ----------------------------------------------------------------------
# Pattern Scan Stage Helper (extracted for CC reduction)
# ----------------------------------------------------------------------


def _execute_pattern_scan_stage(
    hit_title: str,
    hit_snippet: str,
    extracted_text: str,
    result: Any,
    quality_reason: str | None,
    query: str,
    has_signal: bool,
    graph: Any,
) -> _PatternScanResult:
    """Execute pattern scanning and secondary matching.

    Returns _PatternScanResult with hits, matched_count, and observed_at.
    """
    scan_text = _enrich_text_with_metadata(hit_title, hit_snippet, extracted_text)
    hits, matched_count, observed_at = _run_pattern_scan(scan_text, result, quality_reason)

    # Graph injection
    if graph is not None and hits:
        _add_pattern_hits_to_graph(hits, graph, observed_at=observed_at)

    # Secondary query-term matching
    search_text = extracted_text
    if not search_text and (hit_title or hit_snippet):
        search_text = f"{hit_title} {hit_snippet}"
    hits, matched_count = _secondary_query_term_match(
        hits, matched_count, query, search_text, has_signal, graph, observed_at
    )

    return _PatternScanResult(
        hits=hits,
        matched_count=matched_count,
        observed_at=observed_at,
        js_result=None,
    )


async def _run_pattern_scan(
    scan_text: str,
    result: Any,
    quality_reason: str | None,
) -> tuple[list, int, float | None]:
    """Run async pattern scanning. Wrapper for compatibility."""
    return await _scan_patterns(scan_text, result, quality_reason, js_result=None)


# ----------------------------------------------------------------------
# Storage Stage Helper (extracted for CC reduction)
# ----------------------------------------------------------------------


async def _execute_storage_stage(
    unique_findings: list,
    store: Any,
    memory_manager: Any,
    session_id: str | None,
    vector_store: Any,
    graph: Any,
    query: str,
    hit_url: str,
    extracted_text: str,
) -> _StorageResult:
    """Execute storage and embedding operations.

    Returns _StorageResult with accepted/stored counts and error flags.
    """
    accepted_count, stored_count, storage_error, quality_gate_rejected = await _store_and_embed(
        unique_findings=unique_findings,
        store=store,
        memory_manager=memory_manager,
        session_id=session_id,
        vector_store=vector_store,
        graph=graph,
        query=query,
        hit_url=hit_url,
        extracted_text=extracted_text,
    )

    return _StorageResult(
        accepted_count=accepted_count,
        stored_count=stored_count,
        storage_error=storage_error,
        quality_gate_rejected=quality_gate_rejected,
    )


# ----------------------------------------------------------------------
# Public Findings Counter Helper (extracted from _handle_no_pattern_match)
# ----------------------------------------------------------------------


def _count_public_findings_results(
    public_findings: list,
    store: Any,
) -> tuple[int, int]:
    """Count accepted and stored public findings.

    Returns: (pub_accepted, pub_stored)
    """
    if not public_findings:
        return 0, 0

    if store is None:
        count = len(public_findings)
        return count, count

    try:
        pub_results = store.drain_and_get_accepted(public_findings)
    except Exception:
        return 0, 0

    accepted = 0
    stored = 0
    for sr in pub_results:
        if isinstance(sr, dict):
            if sr.get("accepted"):
                accepted += 1
            if sr.get("lmdb_success"):
                stored += 1
        else:
            if getattr(sr, "accepted", False):
                accepted += 1
            if getattr(sr, "lmdb_success", False):
                stored += 1

    return accepted, stored


# ----------------------------------------------------------------------
# URL Validation
# ----------------------------------------------------------------------


def _validate_url_scheme(hit_url: str) -> tuple[bool, str | None]:
    """Validate URL has http/https scheme."""
    _parsed_url = urlparse(hit_url)
    is_valid = bool(_parsed_url.scheme and _parsed_url.scheme.lower() in ("http", "https"))
    return is_valid, _parsed_url.scheme


# ----------------------------------------------------------------------
# Page Text Extraction
# ----------------------------------------------------------------------


async def _extract_page_text(
    result: Any,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
) -> str | None:
    """Extract and return page text from fetch result.

    Returns None for empty text when has_signal is True (triggers JS retry path).
    Raises _SkipWithResult for empty text without signal or extraction failures.
    """
    if hasattr(result, "text"):
        fetched_text = str(result.text) if result.text else None
    else:
        fetched_text = None

    if not fetched_text:
        if has_signal:
            return ""  # Empty string triggers JS retry
        _raise_skip_ppr(
            url=getattr(result, "url", ""),
            error="fetch_text_none_or_empty",
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            has_signal=has_signal,
            fetched_failure_stage=fetched_failure_stage,
            fetched_redirected=fetched_redirected,
            fetched_redirect_target=fetched_redirect_target,
            fetched_js_skip_reason=fetched_js_skip_reason,
            rejection_reason="empty_text",
            terminal_reason="rejected_empty_text",
        )

    # HTML to text extraction
    try:
        content_type = getattr(result, "content_type", None)
        extracted_text = await run_in_cpu_pool_async(
            lambda: _html_to_text(fetched_text, content_type)
        )
    except Exception as exc:
        _raise_skip_ppr(
            url=getattr(result, "url", ""),
            error=f"html_extract_failed:{exc}",
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            has_signal=has_signal,
            fetched_failure_stage=fetched_failure_stage,
            fetched_redirected=fetched_redirected,
            fetched_redirect_target=fetched_redirect_target,
            fetched_js_skip_reason=fetched_js_skip_reason,
            rejection_reason="extraction_failed",
            terminal_reason="rejected_extraction_failed",
        )

    # Hard cap
    if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
        extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

    return extracted_text


def _raise_skip_ppr(
    url: str,
    error: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    has_signal: bool,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
    rejection_reason: str,
    terminal_reason: str,
) -> None:
    """Raise _SkipWithResult with a PipelinePageResult."""
    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=0,
        quality_reason=None, discovery_signal=has_signal,
        discovery_score=discovery_score,
        error=error,
        extracted_text_len=0,
    )
    raise _SkipWithResult(
        PipelinePageResult(
            url=url,
            fetched=True,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            error=error,
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
            js_renderer_skipped_reason=fetched_js_skip_reason,
            rejection_reason=rejection_reason,
            terminal_reason=terminal_reason,
        )
    )


# ----------------------------------------------------------------------
# JS Retry
# ----------------------------------------------------------------------


async def _perform_js_retry_if_needed(
    hit_url: str,
    result: Any,
    policy: Any,
    effective_timeout: float,
    fetch_max_bytes: int,
    quality_reason: str | None,
    discovery_score: float | None,
    discovery_reason: str | None,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    query: str,
    current_text: str,
) -> tuple[str, str | None]:
    """Perform JS retry if quality_reason starts with RETRY_JS.

    Returns: (final_text, final_quality_reason)
    """
    if quality_reason is None or not quality_reason.startswith("RETRY_JS"):
        return current_text, quality_reason

    js_result = None
    _fetched_text = getattr(result, "text", None)
    _js_conf = _js_confidence_from_verdict(
        quality_reason,
        status_code=getattr(result, "status_code", None),
        content_length=len(_fetched_text) if _fetched_text else None,
    )
    try:
        js_result = await _ASYNC_FETCH_PUBLIC_TEXT(
            hit_url, effective_timeout, fetch_max_bytes,
            use_stealth=policy.use_stealth,
            use_js=True,
            use_doh=policy.use_doh,
            js_confidence=_js_conf,
            priority=3,
        )
    except Exception:
        js_result = None

    if js_result is None or not js_result.text or len(js_result.text) < _PRE_FETCH_TEXT_MIN_CHARS:
        return current_text, quality_reason

    # JS content available - re-extract
    try:
        js_content_type = getattr(js_result, "content_type", None)
        extracted_text = await run_in_cpu_pool_async(
            lambda: _html_to_text(js_result.text, js_content_type)
        )
    except Exception:
        extracted_text = js_result.text or ""

    if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
        extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

    # Re-score with JS content
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

    return extracted_text, quality_reason


# ----------------------------------------------------------------------
# Pattern Scanning
# ----------------------------------------------------------------------


async def _scan_patterns(
    scan_text: str,
    result: Any,
    quality_reason: str | None,
    js_result: Any,
) -> tuple[list, int, float | None]:
    """Scan for patterns and perform secondary query-term matching.

    Returns: (hits, matched_count, observed_at)
    """
    _matched_source = js_result if (quality_reason is not None and quality_reason.startswith("RETRY_JS") and js_result is not None) else result
    _result_matched = getattr(_matched_source, "matched_patterns", None) or ()

    if _result_matched:
        try:
            hits = [
                PatternHit(label=p[0], pattern=p[1], start=0, end=0, value=p[2])
                for p in (tuple(x.split("|", 2)) for x in _result_matched)
                if len(p) >= 3
            ]
        except Exception:
            hits = []
        matched_count = len(hits)
    else:
        try:
            hits = await run_in_cpu_pool_async(_SYNC_MATCH_TEXT, scan_text)
        except Exception:
            hits = []
        if hits is None:
            hits = []
        matched_count = len(hits)

    # Graph injection
    _observed_at = getattr(result, "fetched_at", None) or time.time()

    return hits, matched_count, _observed_at


# ----------------------------------------------------------------------
# Secondary Query Match
# ----------------------------------------------------------------------


def _secondary_query_term_match(
    hits: list,
    matched_count: int,
    query: str,
    search_text: str,
    has_signal: bool,
    graph: Any,
    observed_at: float | None,
) -> tuple[list, int]:
    """Secondary query-term matching for zero-pattern matches with signal.

    Returns: (hits, matched_count)
    """
    if matched_count > 0 or not has_signal or not search_text:
        return hits, matched_count

    try:
        _query_terms = [t.strip() for t in query.lower().split() if len(t.strip()) >= 4]
        _text_lower = search_text.lower()
        _found_terms = [t for t in _query_terms if t in _text_lower]

        if not _found_terms:
            return hits, matched_count

        _query_hits = [
            PatternHit(
                label="query_term",
                pattern=term,
                start=_text_lower.find(term),
                end=_text_lower.find(term) + len(term),
                value=search_text[_text_lower.find(term):_text_lower.find(term) + len(term)]
            )
            for term in _found_terms
            if term in _text_lower
        ]

        if _query_hits:
            if graph is not None:
                _add_pattern_hits_to_graph(_query_hits, graph, observed_at=observed_at)
            return _query_hits, len(_query_hits)
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001

    return hits, matched_count


# ----------------------------------------------------------------------
# Hit Deduplication
# ----------------------------------------------------------------------


def _deduplicate_hits(hits: list) -> list:
    """Remove duplicate pattern hits."""
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for hit in hits:
        key = (hit.label or "", hit.pattern, hit.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


# ----------------------------------------------------------------------
# Findings Extraction
# ----------------------------------------------------------------------


async def _extract_findings_parallel(
    deduped_hits: list,
    query: str,
    hit_url: str,
    extracted_text: str,
    discovery_score: float | None,
) -> list:
    """Extract findings from deduped hits in parallel."""
    async def _extract_one_hit(hit: Any) -> Any | None:
        try:
            findings_tuple = await _extract_live_public_findings_from_page(
                query=query,
                url=hit_url,
                hit_label=hit.label if hit.label else "",
                hit_pattern=hit.pattern,
                hit_value=hit.value,
                hit_start=hit.start,
                hit_end=hit.end,
                page_text=extracted_text,
                discovery_score=discovery_score,
            )
            return findings_tuple[0]
        except Exception:
            return None

    results = await parallel(
        [_extract_one_hit(hit) for hit in deduped_hits],
        policy="log",
        ctx="public_fetch:_extract_hit"
    )
    return [r for r in results if r is not None]


# ----------------------------------------------------------------------
# Storage Result Extraction
# ----------------------------------------------------------------------


def _extract_counts_from_results(
    store_results: list,
    unique_findings: list,
) -> tuple[int, int, bool, bool]:
    """Extract counts from store results and determine storage status.

    Returns: (accepted_count, stored_count, storage_error, quality_gate_rejected)
    """
    accepted_count = sum(
        (sr.get("accepted") if isinstance(sr, dict) else bool(getattr(sr, "accepted", False)))
        for sr in store_results
    )
    stored_count = sum(
        (sr.get("lmdb_success") if isinstance(sr, dict) else bool(getattr(sr, "lmdb_success", False)))
        for sr in store_results
    )
    quality_gate_rejected = bool(unique_findings and accepted_count == 0)
    storage_error = bool(stored_count == 0 and unique_findings)
    return accepted_count, stored_count, storage_error, quality_gate_rejected


# ----------------------------------------------------------------------
# Memory Manager Helper
# ----------------------------------------------------------------------


async def _store_in_memory_manager(
    memory_manager: Any,
    session_id: str,
    unique_findings: list,
    query: str,
    hit_url: str,
) -> None:
    """Store findings in memory manager for RAG context."""
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
            await memory_manager.put(session_id, f"finding:{finding_id}", memory_entry)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001


# ----------------------------------------------------------------------
# Storage & Embeddings
# ----------------------------------------------------------------------


async def _store_and_embed(
    unique_findings: list,
    store: Any,
    memory_manager: Any,
    session_id: str | None,
    vector_store: Any,
    graph: Any,
    query: str,
    hit_url: str,
    extracted_text: str,
) -> tuple[int, int, bool, bool]:
    """Store findings and generate embeddings.

    Returns: (accepted_count, stored_count, storage_error, quality_gate_rejected)
    """
    if store is None or not unique_findings:
        return 0, 0, False, False

    try:
        store_results = await store.drain_and_get_accepted(unique_findings)
    except asyncio.CancelledError:
        raise
    except Exception:
        return 0, 0, True, False

    # Graph accumulation
    _accumulate_to_graph(unique_findings)

    # Extract counts
    accepted_count, stored_count, storage_error, quality_gate_rejected = _extract_counts_from_results(
        store_results, unique_findings
    )

    # Memory manager: RAG context
    if memory_manager is not None and session_id is not None and unique_findings:
        await _store_in_memory_manager(memory_manager, session_id, unique_findings, query, hit_url)

    # Per-finding embeddings
    if vector_store is not None and unique_findings and accepted_count > 0:
        await _embed_findings(unique_findings, store_results, vector_store)

    # Page text embedding
    if vector_store is not None and extracted_text and len(extracted_text) > 50:
        await _embed_page_text(vector_store, extracted_text, query, hit_url)

    return accepted_count, stored_count, storage_error, quality_gate_rejected


def _accumulate_to_graph(unique_findings: list) -> None:
    """Accumulate findings to graph after canonical write."""
    if not unique_findings:
        return
    try:
        _acc = SprintGraphAccumulator()
        _acc.accumulate_findings(unique_findings, sprint_id="")
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001


def _is_accepted(sr: Any) -> bool:
    """Check if a store result was accepted."""
    return bool(sr.get("accepted") if isinstance(sr, dict) else getattr(sr, "accepted", False))


def _observe_temporal_event(finding: Any, temporal_layer: Any) -> None:
    """Observe temporal event from finding if possible."""
    try:
        te = event_from_finding_like(finding)
        if te:
            temporal_layer.observe(te)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001


def _collect_accepted_findings(
    unique_findings: list,
    store_results: list,
    temporal_layer: Any,
) -> tuple[list[str], list[str]]:
    """Collect IDs and texts from accepted findings."""
    accepted_ids: list[str] = []
    accepted_texts: list[str] = []

    for finding, sr in zip(unique_findings, store_results, strict=False):
        if _is_accepted(sr):
            if temporal_layer is not None:
                _observe_temporal_event(finding, temporal_layer)

            pt = getattr(finding, "payload_text", "") or ""
            if len(pt) > 20:
                fid = getattr(finding, "finding_id", None)
                if fid:
                    accepted_ids.append(fid)
                    accepted_texts.append(pt)

    return accepted_ids, accepted_texts


async def _embed_findings(unique_findings: list, store_results: list, vector_store: Any) -> None:
    """Generate embeddings for accepted findings."""
    temporal_layer = None
    try:
        temporal_layer = get_temporal_signal_layer()
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001

    accepted_ids, accepted_texts = _collect_accepted_findings(unique_findings, store_results, temporal_layer)

    if accepted_texts:
        model_manager = get_model_manager()
        async with model_manager.embedding_lifecycle():
            embeddings = await generate_embeddings_async(accepted_texts, keep_loaded=True)
        if embeddings is not None and embeddings:
            vec_array = np.asarray(embeddings, dtype=np.float32)
            vector_store.add_vectors(accepted_ids, vec_array, index_type="finding")


async def _embed_page_text(vector_store: Any, extracted_text: str, query: str, hit_url: str) -> None:
    """Generate embedding for page text."""
    model_manager = get_model_manager()
    async with model_manager.embedding_lifecycle():
        embeddings = await generate_embeddings_async([extracted_text], keep_loaded=True)
    if embeddings is not None and len(embeddings) > 0:
        finding_id_for_vec = _make_finding_id(
            query=query,
            url=hit_url,
            label="page_text",
            pattern="embedding",
            value=extracted_text[:100]
        )
        vec = np.asarray(embeddings[0], dtype=np.float32)
        vector_store.add_vectors([finding_id_for_vec], vec.reshape(1, -1), index_type="text")


# ----------------------------------------------------------------------
# No-pattern-match handling
# ----------------------------------------------------------------------


def _build_public_findings(
    query: str,
    hit_url: str,
    extracted_text: str,
    hit_title: str,
    hit_snippet: str,
    quality_reason: str | None,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
    result: Any,
) -> list:
    """Build public findings from page content."""
    findings: list = []
    http_status = getattr(result, "status_code", 0) or 0
    # Primary: build from quality reason with text
    if (
        quality_reason is not None
        and not quality_reason.startswith("SKIP_WEAK")
        and (extracted_text or hit_title or hit_snippet)
    ):
        try:
            _pub_tuple = _build_public_finding(
                query=query,
                url=hit_url,
                page_text=extracted_text or "",
                hit_title=hit_title or "",
                hit_snippet=hit_snippet or "",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                http_status_code=http_status,
            )
            if _pub_tuple:
                findings.append(_pub_tuple[0])
        except Exception:
            return []
    # Fallback: build from signal with title/snippet only
    if not findings and has_signal and (hit_title or hit_snippet):
        try:
            _signal_tuple = _build_public_finding(
                query=query,
                url=hit_url,
                page_text="",
                hit_title=hit_title or "",
                hit_snippet=hit_snippet or "",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                http_status_code=http_status,
            )
            if _signal_tuple:
                findings.extend(_signal_tuple)
        except Exception:  # noqa: BLE001
            pass
    return findings


async def _handle_no_pattern_match(
    ctx: PipelinePageContext,
    result: Any,
    extracted_text: str | None,
    quality_reason: str | None,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
) -> Any:
    """Handle the no-pattern-match branch.

    Attempts public surface fallback, then returns appropriate PPR.
    """
    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=0,
        quality_reason=quality_reason, discovery_signal=ctx.has_signal,
        discovery_score=ctx.discovery_score,
        error=None,
        extracted_text_len=len(extracted_text or ""),
    )

    public_findings = _build_public_findings(
        ctx.query, ctx.hit_url, extracted_text or "", ctx.hit_title,
        ctx.hit_snippet, quality_reason, ctx.has_signal, ctx.discovery_score,
        ctx.discovery_reason, result,
    )

    pub_accepted, pub_stored = _count_public_findings_results(public_findings, ctx.store)

    # Early return: public findings accepted
    if pub_accepted > 0:
        return _build_public_surface_ppr(
            ctx=ctx,
            pub_accepted=pub_accepted,
            pub_stored=pub_stored,
            public_findings=public_findings,
            quality_reason=quality_reason,
            extracted_text=extracted_text,
            fetched_failure_stage=fetched_failure_stage,
            fetched_redirected=fetched_redirected,
            fetched_redirect_target=fetched_redirect_target,
            fetched_js_skip_reason=fetched_js_skip_reason,
        )

    # Final return: terminal state
    terminal_state = TerminalReasonMachine.from_js_skip(fetched_js_skip_reason)
    return PipelinePageResult(
        url=ctx.hit_url, fetched=True, matched_patterns=0,
        accepted_findings=0, stored_findings=0,
        quality_reason=quality_reason,
        discovery_score=ctx.discovery_score,
        discovery_reason=ctx.discovery_reason,
        discovery_signal=ctx.has_signal,
        usable_signal=usable_signal,
        value_tier=value_tier,
        resolution_reason=resolution_reason,
        discovery_false_positive=discovery_false_positive,
        waste_category=waste_category,
        structural_quality=structural_quality,
        failure_stage=fetched_failure_stage,
        redirected=fetched_redirected,
        redirect_target=fetched_redirect_target,
        js_renderer_skipped_reason=fetched_js_skip_reason,
        rejection_reason=terminal_state.rejection_reason,
        terminal_reason=terminal_state.terminal_reason,
    )


def _build_public_surface_ppr(
    ctx: PipelinePageContext,
    pub_accepted: int,
    pub_stored: int,
    public_findings: list,
    quality_reason: str | None,
    extracted_text: str | None,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
) -> PipelinePageResult:
    """Build PPR for public surface findings case."""
    is_dup = bool(public_findings and pub_stored > 0 and pub_accepted == 0)

    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=pub_stored,
        quality_reason=quality_reason, discovery_signal=ctx.has_signal,
        discovery_score=ctx.discovery_score,
        error=None,
        extracted_text_len=len(extracted_text or ""),
    )

    return PipelinePageResult(
        url=ctx.hit_url, fetched=True, matched_patterns=0,
        accepted_findings=pub_accepted, stored_findings=pub_stored,
        quality_reason=quality_reason,
        discovery_score=ctx.discovery_score,
        discovery_reason=ctx.discovery_reason,
        discovery_signal=ctx.has_signal,
        usable_signal=usable_signal,
        value_tier=value_tier,
        resolution_reason=resolution_reason,
        discovery_false_positive=discovery_false_positive,
        waste_category=waste_category,
        structural_quality=structural_quality,
        failure_stage=fetched_failure_stage,
        redirected=fetched_redirected,
        redirect_target=fetched_redirect_target,
        js_renderer_skipped_reason=fetched_js_skip_reason,
        rejection_reason=None,
        terminal_reason=None,
        public_surface_dup=is_dup,
    )


# ----------------------------------------------------------------------
# Shortcut PPR builders
# ----------------------------------------------------------------------


def _make_skip_weak_discovery_ppr(
    hit_url: str,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
) -> Any:
    """Build PPR for skipped weak discovery."""
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
        fetch_blocked_reason="quality_skip",
        rejection_reason="no_fetch_result",
        terminal_reason="skipped_quality_gate",
    )


def _make_invalid_url_ppr(
    hit_url: str,
    url_scheme: str | None,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
) -> Any:
    """Build PPR for invalid URL scheme."""
    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=False, matched_patterns=0, stored_findings=0,
        quality_reason=None, discovery_signal=has_signal,
        discovery_score=discovery_score,
        error=f"url_unsupported_scheme:{url_scheme}",
        extracted_text_len=0,
    )
    return PipelinePageResult(
        url=hit_url,
        fetched=False,
        matched_patterns=0,
        accepted_findings=0,
        stored_findings=0,
        error=f"url_unsupported_scheme:{url_scheme}",
        quality_reason=None,
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
        fetch_blocked_reason="unsupported_scheme",
        rejection_reason="fetch_error",
        terminal_reason="skipped_unsupported_scheme",
    )


def _make_timeout_ppr(
    hit_url: str,
    effective_timeout: float,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
) -> Any:
    """Build PPR for fetch timeout."""
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
        fetch_blocked_reason="timeout",
        rejection_reason="fetch_error",
        terminal_reason="skipped_timeout",
    )


def _make_fetch_error_ppr(
    hit_url: str,
    exc: Exception,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
) -> Any:
    """Build PPR for fetch exception."""
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
        fetch_blocked_reason="exception",
        rejection_reason="fetch_error",
        terminal_reason="skipped_fetch_error",
    )


def _make_skip_weak_ppr(
    hit_url: str,
    quality_reason: str,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
    extracted_text: str,
) -> Any:
    """Build PPR for skip weak quality."""
    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=0,
        quality_reason=quality_reason, discovery_signal=has_signal,
        discovery_score=discovery_score,
        error=None,
        extracted_text_len=len(extracted_text),
    )
    _tr_skipped: str | None = None
    if fetched_js_skip_reason == "browser_unavailable":
        _tr_skipped = "skipped_browser_unavailable"
    elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
        _tr_skipped = "skipped_xml_or_feed"
    _terminal_reason = _tr_skipped if _tr_skipped else "rejected_low_information"
    _rejection_reason = _tr_skipped if _tr_skipped else "low_information"

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
        js_renderer_skipped_reason=fetched_js_skip_reason,
        rejection_reason=_rejection_reason,
        terminal_reason=_terminal_reason,
    )


# ----------------------------------------------------------------------
# MAIN FETCH FUNCTION — Simplified Orchestrator
# ----------------------------------------------------------------------


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
    """Single-page fetch + extract + match + store.

    Returns PipelinePageResult (frozen msgspec.Struct).

    F226B: PUBLIC acceptance uplift telemetry — each parallel task has its own counters.
    """
    # --- Stage 1: Create context & early exits -----------------------
    ctx = PipelinePageContext.from_params(
        query=query,
        hit_url=hit_url,
        hit_title=hit_title,
        hit_snippet=hit_snippet,
        hit_rank=hit_rank,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        store=store,
        memory_manager=memory_manager,
        session_id=session_id,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
        vector_store=vector_store,
        graph=graph,
    )

    # Early exit: weak discovery
    if ctx.skip_fetch:
        return _make_skip_weak_discovery_ppr(
            hit_url, ctx.has_signal, discovery_score, discovery_reason
        )

    # --- Stage 2: URL validation --------------------------------------
    is_valid_url, url_scheme = _validate_url_scheme(hit_url)
    if not is_valid_url:
        return _make_invalid_url_ppr(
            hit_url, url_scheme, ctx.has_signal, discovery_score, discovery_reason
        )

    # --- Stage 3: Policy computation ----------------------------------
    policy = _compute_fetch_policy(hit_url, discovery_score, discovery_reason, ctx.strong_signal)

    # --- Stage 4: Execute fetch with error handling ------------------
    fetch_result = await _execute_fetch_stage(
        hit_url=hit_url,
        semaphore=semaphore,
        effective_timeout=ctx.effective_timeout,
        fetch_max_bytes=fetch_max_bytes,
        policy=policy,
    )

    # Handle fetch failures via early returns
    if fetch_result.failure_stage == "fetch_timeout":
        return _make_timeout_ppr(
            hit_url, ctx.effective_timeout, ctx.has_signal, discovery_score, discovery_reason
        )
    if fetch_result.failure_stage == "fetch_error":
        return _make_fetch_error_ppr(
            hit_url, Exception("Unknown"), ctx.has_signal, discovery_score, discovery_reason
        )

    result = fetch_result.result

    # --- Stage 5: Text extraction ------------------------------------
    try:
        extracted_text = await _extract_page_text(
            result=result,
            has_signal=ctx.has_signal,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            fetched_failure_stage=fetch_result.failure_stage,
            fetched_redirected=fetch_result.redirected,
            fetched_redirect_target=fetch_result.redirect_target,
            fetched_js_skip_reason=fetch_result.js_skip_reason,
        )
    except _SkipWithResult as e:
        return e.result

    # --- Stage 6: Quality scoring ------------------------------------
    quality_reason = _score_page_quality(
        hit_url=hit_url,
        hit_title=ctx.hit_title,
        hit_snippet=ctx.hit_snippet,
        hit_rank=hit_rank,
        query=query,
        extracted_text=extracted_text,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
    )

    # Early exit: low quality
    if quality_reason.startswith("SKIP_WEAK"):
        return _make_skip_weak_ppr(
            hit_url, quality_reason, ctx.has_signal, discovery_score, discovery_reason,
            fetch_result.failure_stage, fetch_result.redirected,
            fetch_result.redirect_target, fetch_result.js_skip_reason,
            extracted_text,
        )

    # --- Stage 7: JS retry if needed ---------------------------------
    extracted_text, quality_reason = await _perform_js_retry_if_needed(
        hit_url=hit_url,
        result=result,
        policy=policy,
        effective_timeout=ctx.effective_timeout,
        fetch_max_bytes=fetch_max_bytes,
        quality_reason=quality_reason,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
        hit_title=ctx.hit_title,
        hit_snippet=ctx.hit_snippet,
        hit_rank=hit_rank,
        query=query,
        current_text=extracted_text,
    )

    # --- Stage 8: Pattern scanning -----------------------------------
    scan_result = _execute_pattern_scan_stage(
        hit_title=ctx.hit_title,
        hit_snippet=ctx.hit_snippet,
        extracted_text=extracted_text,
        result=result,
        quality_reason=quality_reason,
        query=query,
        has_signal=ctx.has_signal,
        graph=graph,
    )

    # Early exit: no pattern matches — public surface fallback
    if scan_result.matched_count == 0:
        return await _handle_no_pattern_match(
            ctx=ctx,
            result=result,
            extracted_text=extracted_text,
            quality_reason=quality_reason,
            fetched_failure_stage=fetch_result.failure_stage,
            fetched_redirected=fetch_result.redirected,
            fetched_redirect_target=fetch_result.redirect_target,
            fetched_js_skip_reason=fetch_result.js_skip_reason,
        )

    # --- Stage 9: Deduplication & extraction ------------------------
    deduped_hits = _deduplicate_hits(scan_result.hits)
    unique_findings = await _extract_findings_parallel(
        deduped_hits, query, hit_url, extracted_text, discovery_score
    )

    # --- Stage 10: Storage & embeddings -------------------------------
    storage_result = await _execute_storage_stage(
        unique_findings=unique_findings,
        store=store,
        memory_manager=memory_manager,
        session_id=session_id,
        vector_store=vector_store,
        graph=graph,
        query=query,
        hit_url=hit_url,
        extracted_text=extracted_text,
    )

    # --- Stage 11: Final PPR construction ----------------------------
    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True,
        matched_patterns=scan_result.matched_count,
        stored_findings=storage_result.stored_count,
        quality_reason=quality_reason,
        discovery_signal=ctx.has_signal,
        discovery_score=discovery_score,
        error=None,
        extracted_text_len=len(extracted_text),
    )

    # Determine terminal state
    terminal_state = TerminalReasonMachine.from_storage_outcome(
        storage_result.accepted_count,
        storage_result.stored_count,
        storage_result.storage_error,
        storage_result.quality_gate_rejected,
    )
    # Override for JS/browser skips
    if terminal_state.terminal_reason is None and fetch_result.js_skip_reason is not None:
        terminal_state = TerminalReasonMachine.from_js_skip(fetch_result.js_skip_reason)

    return PipelinePageResult(
        url=hit_url,
        fetched=True,
        matched_patterns=scan_result.matched_count,
        accepted_findings=storage_result.accepted_count,
        stored_findings=storage_result.stored_count,
        quality_reason=quality_reason,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
        discovery_signal=ctx.has_signal,
        usable_signal=usable_signal,
        value_tier=value_tier,
        resolution_reason=resolution_reason,
        discovery_false_positive=discovery_false_positive,
        waste_category=waste_category,
        structural_quality=structural_quality,
        failure_stage=fetch_result.failure_stage,
        redirected=fetch_result.redirected,
        redirect_target=fetch_result.redirect_target,
        js_renderer_skipped_reason=fetch_result.js_skip_reason,
        rejection_reason=terminal_state.rejection_reason,
        terminal_reason=terminal_state.terminal_reason,
    )
