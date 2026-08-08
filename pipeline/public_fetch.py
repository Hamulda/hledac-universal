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
from typing import Any

# ----------------------------------------------------------------------
# Module-level imports — hoisted from function bodies.
# All inline imports in _fetch_and_process_page and helpers were runtime
# lazy-loads to avoid circular imports; the module already loads without
# error, so these can safely live at module level (avoids re-import on
# every call — critical for a pipeline running 1000s of URLs/sprint).
# ----------------------------------------------------------------------
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
from hledac.universal.utils.async_helpers import parallel
from hledac.universal.utils.rayon_pool import run_in_cpu_pool_async
from hledac.universal.utils.patterns.pattern_matcher import PatternHit

import numpy as np

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
# Main fetch function
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
) -> Any:  # PipelinePageResult
    """Single-page fetch + extract + match + store.

    Returns PipelinePageResult (frozen msgspec.Struct).

    F226B: PUBLIC acceptance uplift telemetry — each parallel task has its own counters.
    """
    # Local telemetry accumulators (per-page, not shared)
    _pub_build_success_count: int = 0
    _pub_build_failure_count: int = 0
    _pub_duplicate_count: int = 0
    _pub_bootstrap_accepted_findings: int = 0
    _pub_dup_found: bool = False

    # --- Adaptive budget tier (extracted) --------------------------------
    effective_timeout, skip_fetch, has_signal, strong_signal, budget_mult = _compute_budget_tier(
        discovery_score, discovery_reason, fetch_timeout_s
    )

    async with semaphore:
        # ---- SKIP tier: weak discovery (extracted) -----------------------
        if skip_fetch:
            return _make_skip_weak_discovery_ppr(
                hit_url, has_signal, discovery_score, discovery_reason
            )

        # ---- URL scheme validation (extracted) ---------------------------
        is_valid_url, url_scheme = _validate_url(hit_url)
        if not is_valid_url:
            return _make_invalid_url_ppr(
                hit_url, url_scheme, has_signal, discovery_score, discovery_reason
            )

        # ---- Policy-driven fetch --------------------------------------
        from .public_discovery import _compute_fetch_policy

        policy = _compute_fetch_policy(hit_url, discovery_score, discovery_reason, strong_signal)

        # Ensure DI globals are patched before use
        _ensure_patched()

        result = None
        try:
            async with asyncio.timeout(effective_timeout + 5.0):
                result = await _ASYNC_FETCH_PUBLIC_TEXT(
                    hit_url, effective_timeout, fetch_max_bytes,
                    use_stealth=policy.use_stealth,
                    use_js=policy.use_js,
                    use_doh=policy.use_doh,
                )
        except TimeoutError:
            return _make_timeout_ppr(hit_url, effective_timeout, has_signal, discovery_score, discovery_reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _make_fetch_error_ppr(hit_url, exc, has_signal, discovery_score, discovery_reason)

        # ---- Unpack fetch result (extracted) ------------------------------
        fetch_info = _unpack_fetch_result(result)
        fetched_text = fetch_info["text"]
        fetched_content_type = fetch_info["content_type"]
        fetched_failure_stage = fetch_info["failure_stage"]
        fetched_redirected = fetch_info["redirected"]
        fetched_redirect_target = fetch_info["redirect_target"]
        fetched_js_skip_reason = fetch_info["js_skip_reason"]

        # ---- Extract page text (extracted) ------------------------------
        try:
            extracted_text = await _extract_page_text(
                fetched_text, fetched_content_type, has_signal, discovery_score, discovery_reason,
                fetched_failure_stage, fetched_redirected, fetched_redirect_target, fetched_js_skip_reason
            )
        except _SkipWithResult as e:
            return e.result

        # ---- Quality scoring ------------------------------------------
        from .public_patterns import _score_page_quality

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

        # ---- SKIP_WEAK: low quality, early exit -----------------------
        if quality_reason.startswith("SKIP_WEAK"):
            return _make_skip_weak_ppr(
                hit_url, quality_reason, has_signal, discovery_score, discovery_reason,
                fetched_failure_stage, fetched_redirected, fetched_redirect_target, fetched_js_skip_reason,
                extracted_text,
            )

        # ---- F275 RETRY_JS: thin page with strong signal (extracted) --------
        js_result, extracted_text, quality_reason = await _perform_js_retry(
            result=result,
            policy=policy,
            hit_url=hit_url,
            effective_timeout=effective_timeout,
            fetch_max_bytes=fetch_max_bytes,
            quality_reason=quality_reason,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            hit_title=hit_title,
            hit_snippet=hit_snippet,
            hit_rank=hit_rank,
            query=query,
            current_text=extracted_text,
        )

        # ---- Enrich + pattern scan (extracted) ---------------------------
        from .public_patterns import _enrich_text_with_metadata
        scan_text = _enrich_text_with_metadata(hit_title or "", hit_snippet or "", extracted_text)

        hits, matched_count, _observed_at = await _scan_patterns(scan_text, result, quality_reason, js_result)

        # Graph injection
        if graph is not None and hits:
            _add_pattern_hits_to_graph(hits, graph, observed_at=_observed_at)

        # Secondary query-term matching
        _search_text = extracted_text
        if not _search_text and (hit_title or hit_snippet):
            _search_text = f"{hit_title or ''} {hit_snippet or ''}"
        hits, matched_count = await _secondary_query_match(
            hits, matched_count, query, _search_text, has_signal, graph, _observed_at
        )
        # ---- NO matches: public surface fallback ---------------------
        if matched_count == 0:
            return await _handle_no_pattern_match(
                hit_url=hit_url,
                query=query,
                extracted_text=extracted_text,
                hit_title=hit_title,
                hit_snippet=hit_snippet,
                quality_reason=quality_reason,
                has_signal=has_signal,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                result=result,
                fetched_failure_stage=fetched_failure_stage,
                fetched_redirected=fetched_redirected,
                fetched_redirect_target=fetched_redirect_target,
                fetched_js_skip_reason=fetched_js_skip_reason,
                store=store,
                graph=graph,
            )

        # ---- Per-page dedup + extract (extracted) -------------------------
        deduped_hits = _deduplicate_hits(hits)
        unique_findings = await _extract_findings_parallel(
            deduped_hits, query, hit_url, extracted_text, discovery_score
        )

        # ---- Storage --------------------------------------------------
        accepted_count = 0
        stored_count = 0
        storage_error: bool = False
        quality_gate_rejected: bool = False

        if store is not None and unique_findings:
            try:
                store_results = await store.drain_and_get_accepted(unique_findings)

                # F268: graph accumulation after canonical write
                if unique_findings:
                    try:
                        from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator

                        _acc = SprintGraphAccumulator()
                        _acc.accumulate_findings(unique_findings, sprint_id="")
                    except Exception:  # noqa: BLE001
                        pass  # noqa: BLE001

                for sr in store_results:
                    if isinstance(sr, dict):
                        if sr.get("accepted"):
                            accepted_count += 1
                        if sr.get("lmdb_success"):
                            stored_count += 1
                    else:
                        if getattr(sr, "accepted", False):
                            accepted_count += 1
                        if getattr(sr, "lmdb_success", False):
                            stored_count += 1
                if unique_findings and accepted_count == 0:
                    quality_gate_rejected = True
                if stored_count == 0 and unique_findings:
                    storage_error = True

                # Memory manager: RAG context
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
                            await memory_manager.put(session_id, f"finding:{finding_id}", memory_entry)
                        except Exception:  # noqa: BLE001
                            pass  # noqa: BLE001

            except asyncio.CancelledError:
                raise
            except Exception:
                storage_error = True

            # Per-finding embeddings (F197C)
            if vector_store is not None and unique_findings and accepted_count > 0:
                try:
                    from hledac.universal.brain.model_manager import get_model_manager
                    from hledac.universal.embedding_pipeline import generate_embeddings_async

                    try:
                        from hledac.universal.layers import get_temporal_signal_layer
                        from hledac.universal.layers.temporal_signal_layer import event_from_finding_like

                        temporal_layer = get_temporal_signal_layer()
                    except Exception:
                        temporal_layer = None

                    accepted_ids: list[str] = []
                    accepted_texts: list[str] = []
                    for finding, sr in zip(unique_findings, store_results, strict=False):
                        is_accepted = False
                        if isinstance(sr, dict):
                            is_accepted = bool(sr.get("accepted"))
                        else:
                            is_accepted = bool(getattr(sr, "accepted", False))
                        if is_accepted:
                            if temporal_layer is not None:
                                try:
                                    te = event_from_finding_like(finding)
                                    if te:
                                        temporal_layer.observe(te)
                                except asyncio.CancelledError:
                                    raise
                                except Exception:  # noqa: BLE001
                                    pass  # noqa: BLE001
                            pt = getattr(finding, "payload_text", "") or ""
                            if len(pt) > 20:
                                fid = getattr(finding, "finding_id", None)
                                if fid:
                                    accepted_ids.append(fid)
                                    accepted_texts.append(pt)

                    if accepted_texts:
                        model_manager = get_model_manager()
                        async with model_manager.embedding_lifecycle():
                            embeddings = await generate_embeddings_async(accepted_texts, keep_loaded=True)
                        if embeddings is not None and embeddings:
                            import numpy as np

                            vec_array = np.asarray(embeddings, dtype=np.float32)
                            vector_store.add_vectors(accepted_ids, vec_array, index_type="finding")
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001

            # Page text embedding (P13)
            if vector_store is not None and extracted_text and len(extracted_text) > 50:
                try:
                    from hledac.universal.brain.model_manager import get_model_manager
                    from hledac.universal.embedding_pipeline import generate_embeddings_async
                    from .public_patterns import _make_finding_id

                    model_manager = get_model_manager()
                    async with model_manager.embedding_lifecycle():
                        embeddings = await generate_embeddings_async([extracted_text], keep_loaded=True)
                    if embeddings is not None and len(embeddings) > 0:
                        import numpy as np

                        finding_id_for_vec = _make_finding_id(
                            query=query,
                            url=hit_url,
                            label="page_text",
                            pattern="embedding",
                            value=extracted_text[:100]
                        )
                        vec = np.asarray(embeddings[0], dtype=np.float32)
                        vector_store.add_vectors([finding_id_for_vec], vec.reshape(1, -1), index_type="text")
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001

        # ---- Final PipelinePageResult --------------------------------
        from .public_patterns import _compute_page_usable_fields
        from .public_stages import PipelinePageResult

        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=matched_count,
            stored_findings=stored_count,
            quality_reason=quality_reason,
            discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=None,
            extracted_text_len=len(extracted_text),
        )

        if fetched_js_skip_reason == "browser_unavailable":
            _terminal = "skipped_browser_unavailable"
            _rej_reason = "browser_unavailable"
        elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
            _terminal = "skipped_xml_or_feed"
            _rej_reason = "xml_or_feed"
        elif accepted_count > 0 and not storage_error:
            _terminal = None
            _rej_reason = None
        elif storage_error:
            _terminal = "rejected_storage_rejected"
            _rej_reason = "storage_rejected"
        elif quality_gate_rejected:
            _terminal = "rejected_quality_gate"
            _rej_reason = "quality_gate_rejected"
        else:
            _terminal = "rejected_storage_rejected"
            _rej_reason = "storage_rejected"

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
            js_renderer_skipped_reason=fetched_js_skip_reason,
            rejection_reason=_rej_reason,
            terminal_reason=_terminal,
        )



# ---------------------------------------------------------------------------
# Complexity reduction helpers for _fetch_and_process_page (83 -> ~10)
# ---------------------------------------------------------------------------


def _compute_budget_tier(
    discovery_score, discovery_reason, fetch_timeout_s
):
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
    return effective_timeout, skip_fetch, has_signal, strong_signal, budget_mult


def _validate_url(hit_url):
    _parsed_url = urlparse(hit_url)
    is_valid = bool(_parsed_url.scheme and _parsed_url.scheme.lower() in ("http", "https"))
    return is_valid, _parsed_url.scheme


def _unpack_fetch_result(result):
    if hasattr(result, "text"):
        return {
            "text": str(result.text) if result.text else None,
            "content_type": getattr(result, "content_type", None),
            "failure_stage": getattr(result, "failure_stage", None),
            "redirected": getattr(result, "redirected", False),
            "redirect_target": getattr(result, "redirect_target", None),
            "js_skip_reason": getattr(result, "js_renderer_skipped_reason", None),
        }
    return {
        "text": None,
        "content_type": None,
        "failure_stage": None,
        "redirected": False,
        "redirect_target": None,
        "js_skip_reason": None,
    }


class _SkipWithResult(Exception):
    def __init__(self, result):
        self.ppr_result = result
        super().__init__()

# Monkey-patch to make it work
_SkipWithResult.result = property(lambda self: self.ppr_result)



# ----------------------------------------------------------------------
# Helper functions for complex branches
# ----------------------------------------------------------------------




async def _extract_page_text(
    fetched_text, fetched_content_type, has_signal, discovery_score, discovery_reason,
    fetched_failure_stage, fetched_redirected, fetched_redirect_target, fetched_js_skip_reason
):
    """Extract and convert HTML to text."""
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

    if not fetched_text:
        if has_signal:
            return ""  # Empty string triggers JS retry
        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=0,
            quality_reason=None, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error="fetch_text_none_or_empty",
            extracted_text_len=0,
        )
        raise _SkipWithResult(
            PipelinePageResult(
                url="", fetched=True, matched_patterns=0, accepted_findings=0, stored_findings=0,
                error="fetch_text_none_or_empty", discovery_score=discovery_score,
                discovery_reason=discovery_reason, discovery_signal=has_signal,
                usable_signal=usable_signal, value_tier=value_tier,
                resolution_reason=resolution_reason, discovery_false_positive=discovery_false_positive,
                waste_category=waste_category, structural_quality=structural_quality,
                failure_stage=fetched_failure_stage, redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,
                rejection_reason="empty_text", terminal_reason="rejected_empty_text",
            )
        )

    try:
        extracted_text = await run_in_cpu_pool_async(
            lambda: _html_to_text(fetched_text, fetched_content_type)
        )
    except Exception as exc:
        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=0,
            quality_reason=None, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=f"html_extract_failed:{exc}",
            extracted_text_len=0,
        )
        raise _SkipWithResult(
            PipelinePageResult(
                url="", fetched=True, matched_patterns=0, accepted_findings=0, stored_findings=0,
                error=f"html_extract_failed:{exc}", discovery_score=discovery_score,
                discovery_reason=discovery_reason, discovery_signal=has_signal,
                usable_signal=usable_signal, value_tier=value_tier,
                resolution_reason=resolution_reason, discovery_false_positive=discovery_false_positive,
                waste_category=waste_category, structural_quality=structural_quality,
                failure_stage=fetched_failure_stage, redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,
                rejection_reason="extraction_failed", terminal_reason="rejected_extraction_failed",
            )
        )

    if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
        extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]
    return extracted_text


def _deduplicate_hits(hits):
    """Remove duplicate pattern hits."""
    seen = set()
    deduped = []
    for hit in hits:
        key = (hit.label or "", hit.pattern, hit.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


async def _scan_patterns(scan_text, result, quality_reason, js_result):
    """Scan for patterns in text."""
    _matched_source = js_result if (quality_reason and quality_reason.startswith("RETRY_JS") and js_result) else result
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
    else:
        try:
            hits = await run_in_cpu_pool_async(_SYNC_MATCH_TEXT, scan_text)
        except Exception:
            hits = []
        if hits is None:
            hits = []

    import time as _time
    _observed_at = getattr(result, "fetched_at", None) or _time.time()
    return hits, len(hits), _observed_at





async def _secondary_query_match(hits, matched_count, query, search_text, has_signal, graph, observed_at):
    if matched_count > 0 or not has_signal or not search_text:
        return hits, matched_count

    try:
        _query_lower = query.lower()
        _query_terms = [t.strip() for t in _query_lower.split() if len(t.strip()) >= 4]
        _text_lower = search_text.lower()
        _found_terms = [_t for _t in _query_terms if _t in _text_lower]

        if not _found_terms:
            return hits, matched_count

        _query_hits = []
        for _term in _found_terms:
            _idx = _text_lower.find(_term)
            if _idx >= 0:
                _query_hits.append(PatternHit(
                    label="query_term",
                    pattern=_term,
                    start=_idx,
                    end=_idx + len(_term),
                    value=search_text[_idx:_idx + len(_term)]
                ))

        if _query_hits:
            hits = _query_hits
            matched_count = len(_query_hits)
            if graph is not None:
                _add_pattern_hits_to_graph(hits, graph, observed_at=observed_at)
    except Exception:
        pass

    return hits, matched_count




async def _store_findings(
    store, unique_findings, memory_manager, session_id,
    query, hit_url, vector_store, extracted_text
):
    accepted_count = 0
    stored_count = 0
    storage_error = False
    quality_gate_rejected = False

    if store is None or not unique_findings:
        return accepted_count, stored_count, storage_error, quality_gate_rejected

    try:
        store_results = await store.drain_and_get_accepted(unique_findings)

        # F268: graph accumulation
        if unique_findings:
            try:
                from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator
                _acc = SprintGraphAccumulator()
                _acc.accumulate_findings(unique_findings, sprint_id="")
            except Exception:
                pass

        for sr in store_results:
            if isinstance(sr, dict):
                if sr.get("accepted"):
                    accepted_count += 1
                if sr.get("lmdb_success"):
                    stored_count += 1
            else:
                if getattr(sr, "accepted", False):
                    accepted_count += 1
                if getattr(sr, "lmdb_success", False):
                    stored_count += 1

        if unique_findings and accepted_count == 0:
            quality_gate_rejected = True
        if stored_count == 0 and unique_findings:
            storage_error = True

        # Memory manager: RAG context
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
                    await memory_manager.put(session_id, f"finding:{finding_id}", memory_entry)
                except Exception:
                    pass

    except asyncio.CancelledError:
        raise
    except Exception:
        storage_error = True

    # Per-finding embeddings (F197C)
    if vector_store is not None and unique_findings and accepted_count > 0:
        try:
            await _embed_findings(vector_store, unique_findings, store_results, accepted_texts)
        except Exception:
            pass

    return accepted_count, stored_count, storage_error, quality_gate_rejected


async def _embed_findings(vector_store, unique_findings, store_results, accepted_texts):
    from hledac.universal.brain.model_manager import get_model_manager
    from hledac.universal.embedding_pipeline import generate_embeddings_async

    try:
        from hledac.universal.layers import get_temporal_signal_layer
        from hledac.universal.layers.temporal_signal_layer import event_from_finding_like
        temporal_layer = get_temporal_signal_layer()
    except Exception:
        temporal_layer = None

    accepted_ids = []
    accepted_texts = []
    for finding, sr in zip(unique_findings, store_results, strict=False):
        is_accepted = False
        if isinstance(sr, dict):
            is_accepted = bool(sr.get("accepted"))
        else:
            is_accepted = bool(getattr(sr, "accepted", False))
        if is_accepted:
            if temporal_layer is not None:
                try:
                    te = event_from_finding_like(finding)
                    if te:
                        temporal_layer.observe(te)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            pt = getattr(finding, "payload_text", "") or ""
            if len(pt) > 20:
                fid = getattr(finding, "finding_id", None)
                if fid:
                    accepted_ids.append(fid)
                    accepted_texts.append(pt)

    if accepted_texts:
        model_manager = get_model_manager()
        async with model_manager.embedding_lifecycle():
            embeddings = await generate_embeddings_async(accepted_texts, keep_loaded=True)
        if embeddings is not None and embeddings:
            vec_array = np.asarray(embeddings, dtype=np.float32)
            vector_store.add_vectors(accepted_ids, vec_array, index_type="finding")


async def _embed_page_text(vector_store, extracted_text, query, hit_url):
    if vector_store is None or not extracted_text or len(extracted_text) <= 50:
        return

    try:
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
    except Exception:
        pass




def _make_skip_weak_discovery_ppr(hit_url, has_signal, discovery_score, discovery_reason):
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

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


def _make_invalid_url_ppr(hit_url, url_scheme, has_signal, discovery_score, discovery_reason):
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

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


# ── No-pattern-match helpers ─────────────────────────────────────────────────────


def _get_terminal_reasons(fetched_js_skip_reason: str | None) -> tuple[str, str]:
    """Determine terminal_reason and rejection_reason from JS skip reason."""
    if fetched_js_skip_reason == "browser_unavailable":
        return ("skipped_browser_unavailable", "skipped_browser_unavailable")
    if fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
        return ("skipped_xml_or_feed", "skipped_xml_or_feed")
    return ("rejected_no_pattern_match", "no_pattern_match")


async def _build_public_findings(
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
    from .public_acceptance import _build_public_finding

    findings: list = []
    http_status = getattr(result, "status_code", 0) or 0
    # Primary: build from quality reason with text
    if (
        quality_reason is not None
        and not quality_reason.startswith("SKIP_WEAK")
        and (extracted_text or hit_title or hit_snippet)
    ):
        try:
            _pub_tuple = await _build_public_finding(
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
            _signal_tuple = await _build_public_finding(
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


def _count_accepted_findings(
    public_findings: list,
    store: Any,
) -> tuple[int, int, bool]:
    """Count accepted and stored findings. Returns (accepted, stored, is_dup)."""
    pub_accepted = 0
    pub_stored = 0
    if public_findings:
        if store is not None:
            try:
                pub_results = store.drain_and_get_accepted(public_findings)  # sync
                for sr in pub_results:
                    if isinstance(sr, dict):
                        if sr.get("accepted"):
                            pub_accepted += 1
                        if sr.get("lmdb_success"):
                            pub_stored += 1
                    else:
                        if getattr(sr, "accepted", False):
                            pub_accepted += 1
                        if getattr(sr, "lmdb_success", False):
                            pub_stored += 1
            except Exception:  # noqa: BLE001
                pass
        else:
            pub_accepted = len(public_findings)
            pub_stored = pub_accepted
    is_dup = bool(public_findings and pub_stored > 0 and pub_accepted == 0)
    return pub_accepted, pub_stored, is_dup


async def _handle_no_pattern_match(
    *,
    hit_url: str,
    query: str,
    extracted_text: str,
    hit_title: str,
    hit_snippet: str,
    quality_reason: str | None,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
    result: Any,
    fetched_failure_stage: str | None,
    fetched_redirected: bool,
    fetched_redirect_target: str | None,
    fetched_js_skip_reason: str | None,
    store: Any,
    graph: Any,
) -> Any:  # PipelinePageResult
    """Handle the no-pattern-match branch of _fetch_and_process_page."""
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=0,
        quality_reason=quality_reason, discovery_signal=has_signal,
        discovery_score=discovery_score,
        error=None,
        extracted_text_len=len(extracted_text),
    )

    public_findings = await _build_public_findings(
        query, hit_url, extracted_text, hit_title, hit_snippet,
        quality_reason, has_signal, discovery_score, discovery_reason, result,
    )
    pub_accepted, pub_stored, is_dup = _count_accepted_findings(public_findings, store)

    if pub_accepted > 0:
        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=pub_stored,
            quality_reason=quality_reason, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=None,
            extracted_text_len=len(extracted_text),
        )
        return PipelinePageResult(
            url=hit_url, fetched=True, matched_patterns=0,
            accepted_findings=pub_accepted, stored_findings=pub_stored,
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
            js_renderer_skipped_reason=fetched_js_skip_reason,
            rejection_reason=None,
            terminal_reason=None,
            public_surface_dup=is_dup,
        )

    terminal_reason, rejection_reason = _get_terminal_reasons(fetched_js_skip_reason)
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
        js_renderer_skipped_reason=fetched_js_skip_reason,
        rejection_reason=rejection_reason,
        terminal_reason=terminal_reason,
    )


# ---------------------------------------------------------------------------
# Extracted helpers for _fetch_and_process_page complexity reduction (83 → ~10)
# ---------------------------------------------------------------------------


def _compute_budget_tier(
    discovery_score: float | None,
    discovery_reason: str | None,
    fetch_timeout_s: float,
) -> tuple[float, bool, bool, bool, float]:
    """Compute adaptive budget tier from discovery signals.

    Returns: (effective_timeout, skip_fetch, has_signal, strong_signal, budget_mult)
    """
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
    return effective_timeout, skip_fetch, has_signal, strong_signal, budget_mult


def _validate_url_scheme(hit_url: str) -> tuple[bool, str | None]:
    """Validate URL has http/https scheme.

    Returns: (is_valid, scheme)
    """
    _parsed_url = urlparse(hit_url)
    is_valid = bool(_parsed_url.scheme and _parsed_url.scheme.lower() in ("http", "https"))
    return is_valid, _parsed_url.scheme


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
    Returns None and logs error for extraction failures.
    """
    if hasattr(result, "text"):
        fetched_text = str(result.text) if result.text else None
    else:
        fetched_text = None

    if not fetched_text:
        if has_signal:
            return ""  # Empty string triggers JS retry
        from .public_patterns import _compute_page_usable_fields
        from .public_stages import PipelinePageResult

        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=0,
            quality_reason=None, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error="fetch_text_none_or_empty",
            extracted_text_len=0,
        )
        raise _SkipWithResult(
            PipelinePageResult(
                url=getattr(result, "url", ""),
                fetched=True,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
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
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,
                rejection_reason="empty_text",
                terminal_reason="rejected_empty_text",
            )
        )

    # HTML to text extraction
    try:
        content_type = fetched_content_type or getattr(result, "content_type", None)
        extracted_text = await run_in_cpu_pool_async(
            lambda: _html_to_text(fetched_text, content_type)
        )
    except Exception as exc:
        from .public_patterns import _compute_page_usable_fields
        from .public_stages import PipelinePageResult

        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=0,
            quality_reason=None, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=f"html_extract_failed:{exc}",
            extracted_text_len=0,
        )
        raise _SkipWithResult(
            PipelinePageResult(
                url=getattr(result, "url", ""),
                fetched=True,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
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
                js_renderer_skipped_reason=fetched_js_skip_reason,
                rejection_reason="extraction_failed",
                terminal_reason="rejected_extraction_failed",
            )
        )

    # Hard cap
    if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
        extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

    return extracted_text


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
    import time as _time
    _observed_at = getattr(result, "fetched_at", None) or _time.time()

    return hits, matched_count, _observed_at


async def _secondary_query_term_match(
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
        _query_lower = query.lower()
        _query_terms = [t.strip() for t in _query_lower.split() if len(t.strip()) >= 4]
        _text_lower = search_text.lower()
        _found_terms = [_t for _t in _query_terms if _t in _text_lower]

        if not _found_terms:
            return hits, matched_count

        _query_hits: list = []
        for _term in _found_terms:
            _idx = _text_lower.find(_term)
            if _idx >= 0:
                _query_hits.append(PatternHit(
                    label="query_term",
                    pattern=_term,
                    start=_idx,
                    end=_idx + len(_term),
                    value=search_text[_idx:_idx + len(_term)]
                ))

        if _query_hits:
            hits = _query_hits
            matched_count = len(_query_hits)
            if graph is not None:
                _add_pattern_hits_to_graph(hits, graph, observed_at=observed_at)
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001

    return hits, matched_count


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


async def _store_and_embed(
    unique_findings: list,
    store: Any,
    memory_manager: Any,
    session_id: str | None,
    vector_store: Any,
    graph: Any,
    query: str,
    hit_url: str,
    result: Any,
) -> tuple[int, int, bool, bool]:
    """Store findings and generate embeddings.

    Returns: (accepted_count, stored_count, storage_error, quality_gate_rejected)
    """
    accepted_count = 0
    stored_count = 0
    storage_error = False
    quality_gate_rejected = False

    if store is None or not unique_findings:
        return 0, 0, False, False

    try:
        store_results = await store.drain_and_get_accepted(unique_findings)

        # Graph accumulation after canonical write
        if unique_findings:
            try:
                _acc = SprintGraphAccumulator()
                _acc.accumulate_findings(unique_findings, sprint_id="")
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001

        for sr in store_results:
            if isinstance(sr, dict):
                if sr.get("accepted"):
                    accepted_count += 1
                if sr.get("lmdb_success"):
                    stored_count += 1
            else:
                if getattr(sr, "accepted", False):
                    accepted_count += 1
                if getattr(sr, "lmdb_success", False):
                    stored_count += 1

        if unique_findings and accepted_count == 0:
            quality_gate_rejected = True
        if stored_count == 0 and unique_findings:
            storage_error = True

        # Memory manager: RAG context
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
                    await memory_manager.put(session_id, f"finding:{finding_id}", memory_entry)
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001

    except asyncio.CancelledError:
        raise
    except Exception:
        storage_error = True

    # Per-finding embeddings
    if vector_store is not None and unique_findings and accepted_count > 0:
        try:
            try:
                temporal_layer = get_temporal_signal_layer()
            except Exception:
                temporal_layer = None

            accepted_ids: list[str] = []
            accepted_texts: list[str] = []
            for finding, sr in zip(unique_findings, store_results, strict=False):
                is_accepted = False
                if isinstance(sr, dict):
                    is_accepted = bool(sr.get("accepted"))
                else:
                    is_accepted = bool(getattr(sr, "accepted", False))

                if is_accepted:
                    if temporal_layer is not None:
                        try:
                            te = event_from_finding_like(finding)
                            if te:
                                temporal_layer.observe(te)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001
                            pass  # noqa: BLE001
                    pt = getattr(finding, "payload_text", "") or ""
                    if len(pt) > 20:
                        fid = getattr(finding, "finding_id", None)
                        if fid:
                            accepted_ids.append(fid)
                            accepted_texts.append(pt)

            if accepted_texts:
                model_manager = get_model_manager()
                async with model_manager.embedding_lifecycle():
                    embeddings = await generate_embeddings_async(accepted_texts, keep_loaded=True)
                if embeddings is not None and embeddings:
                    vec_array = np.asarray(embeddings, dtype=np.float32)
                    vector_store.add_vectors(accepted_ids, vec_array, index_type="finding")
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001

    # Page text embedding
    if vector_store is not None and unique_findings:
        for finding in unique_findings:
            try:
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
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001

    return accepted_count, stored_count, storage_error, quality_gate_rejected


class _SkipWithResult(Exception):
    """Signal that processing should skip to end with a specific result."""
    def __init__(self, result: Any):
        self.result = result
        super().__init__()


def _make_timeout_ppr(
    hit_url: str,
    effective_timeout: float,
    has_signal: bool,
    discovery_score: float | None,
    discovery_reason: str | None,
) -> Any:  # PipelinePageResult
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

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
) -> Any:  # PipelinePageResult
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

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
) -> Any:  # PipelinePageResult
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

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