"""
Public pipeline fetch — _fetch_and_process_page.

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
- _add_pattern_hits_to_graph: locally defined (calls graph.upsert_ioc)
- CanonicalFinding: from duckdb_store (lazy import)
- run_in_cpu_pool_async: from utils.rayon_pool (lazy import)
"""


import asyncio
import time
from typing import Any

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


def _add_pattern_hits_to_graph(hits: list, graph: Any) -> None:
    """
    Add pattern hits to graph (inline from live_public_pipeline.py).

    M1 8GB safe: hard cap 1000 hits per page, deduplication by (ioc_type, value).
    Fail-soft: graph errors never propagate.
    """
    if not hits or graph is None:
        return
    # M1 8GB safe: cap at 1000 hits per page
    _MAX_HITS_PER_PAGE = 1000
    _seen: set[tuple[str, str]] = set()
    _count = 0
    try:
        for hit in hits:
            if _count >= _MAX_HITS_PER_PAGE:
                break
            label = getattr(hit, "label", None) or ""
            pattern = getattr(hit, "pattern", None) or ""
            value = getattr(hit, "value", None) or ""
            if not label or not pattern:
                continue
            # Deduplicate by (ioc_type, value)
            _key = (label, value)
            if _key in _seen:
                continue
            _seen.add(_key)
            _count += 1
            try:
                graph.upsert_ioc(
                    ioc_type=label,
                    value=value,
                    source="public_pipeline",
                    properties={"pattern": pattern},
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
    """
    Single-page fetch + extract + match + store.

    Returns PipelinePageResult (frozen msgspec.Struct).

    F226B: PUBLIC acceptance uplift telemetry — each parallel task has its own counters.
    """
    from urllib.parse import urlparse

    # Local telemetry accumulators (per-page, not shared)
    _pub_build_success_count: int = 0
    _pub_build_failure_count: int = 0
    _pub_duplicate_count: int = 0
    _pub_bootstrap_accepted_findings: int = 0
    _pub_dup_found: bool = False

    # --- Adaptive budget tier ----------------------------------------
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

    async with semaphore:
        # ---- SKIP tier: weak discovery, no fetch -----------------------
        if skip_fetch:
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

        # ---- URL scheme validation ------------------------------------
        _parsed_url = urlparse(hit_url)
        if not _parsed_url.scheme or _parsed_url.scheme.lower() not in ("http", "https"):
            from .public_patterns import _compute_page_usable_fields
            from .public_stages import PipelinePageResult

            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"url_unsupported_scheme:{_parsed_url.scheme}",
                extracted_text_len=0,
            )
            return PipelinePageResult(
                url=hit_url,
                fetched=False,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
                error=f"url_unsupported_scheme:{_parsed_url.scheme}",
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

        # ---- Unpack fetch result ---------------------------------------
        fetched_text: str | None
        fetched_failure_stage: str | None = None
        fetched_redirected: bool = False
        fetched_redirect_target: str | None = None
        fetched_js_skip_reason: str | None = None
        if hasattr(result, "text"):
            fetched_text = str(result.text) if result.text else None
            fetched_failure_stage = getattr(result, "failure_stage", None)
            fetched_redirected = getattr(result, "redirected", False)
            fetched_redirect_target = getattr(result, "redirect_target", None)
            fetched_js_skip_reason = getattr(result, "js_renderer_skipped_reason", None)
        else:
            fetched_text = None

        # ---- Empty text: decide skip vs JS retry ----------------------
        if not fetched_text:
            if has_signal:
                extracted_text = ""
            else:
                from .public_patterns import _compute_page_usable_fields
                from .public_stages import PipelinePageResult

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
                    js_renderer_skipped_reason=fetched_js_skip_reason,
                    rejection_reason="empty_text",
                    terminal_reason="rejected_empty_text",
                )
        else:
            from .public_patterns import _html_to_text

            try:
                # ISSUE-28: Use Rust `extract_html_text` (lol_html) directly.
                # Rust backend handles CPU-bound HTML→text without asyncio.to_thread
                # overhead. Falls back to Python HTMLParser if Rust unavailable.
                extracted_text = _html_to_text(fetched_text)
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
                    js_renderer_skipped_reason=fetched_js_skip_reason,
                    rejection_reason="extraction_failed",
                    terminal_reason="rejected_extraction_failed",
                )

        # Hard cap
        if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
            extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

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

        # ---- F275 RETRY_JS: thin page with strong signal --------------
        js_result = None
        if quality_reason is not None and quality_reason.startswith("RETRY_JS"):
            from .public_patterns import _js_confidence_from_verdict

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
            if js_result is not None and js_result.text and len(js_result.text) >= _PRE_FETCH_TEXT_MIN_CHARS:
                from .public_patterns import _html_to_text

                try:
                    # ISSUE-28: Rust `extract_html_text` (lol_html) — no asyncio.to_thread overhead
                    extracted_text = _html_to_text(js_result.text)
                except Exception:
                    extracted_text = js_result.text or ""
                if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
                    extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]
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
                if quality_reason.startswith("SKIP_WEAK"):
                    from .public_patterns import _compute_page_usable_fields
                    from .public_stages import PipelinePageResult

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
                        failure_stage=None,
                        redirected=getattr(js_result, "redirected", False),
                        redirect_target=getattr(js_result, "redirect_target", None),
                        js_renderer_skipped_reason=getattr(js_result, "js_renderer_skipped_reason", None),
                        rejection_reason="js_retry_thin",
                        terminal_reason="rejected_js_retry_thin",
                    )

        # ---- Enrich + pattern scan ------------------------------------
        from .public_patterns import _enrich_text_with_metadata

        scan_text = _enrich_text_with_metadata(hit_title or "", hit_snippet or "", extracted_text)
        del fetched_text

        # Pattern scan: use matched_patterns from FetchResult or re-match
        _matched_source = js_result if (quality_reason is not None and quality_reason.startswith("RETRY_JS") and js_result is not None) else result
        _result_matched = getattr(_matched_source, "matched_patterns", None) or ()
        if _result_matched:
            try:
                from patterns.pattern_matcher import PatternHit

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
                from utils.rayon_pool import run_in_cpu_pool_async

                hits = await run_in_cpu_pool_async(_SYNC_MATCH_TEXT, scan_text)
            except Exception:
                hits = []
            if hits is None:
                hits = []
            matched_count = len(hits)

        # Graph injection
        if graph is not None and hits:
            _add_pattern_hits_to_graph(hits, graph)

        # ---- Query-term secondary matching (P0-FIX F290) --------------
        _query_hits: list = []
        _search_text = extracted_text
        if not _search_text and (hit_title or hit_snippet):
            _search_text = f"{hit_title or ''} {hit_snippet or ''}"
        if matched_count == 0 and has_signal and _search_text:
            try:
                _query_lower = query.lower()
                _query_terms = [t.strip() for t in _query_lower.split() if len(t.strip()) >= 4]
                _text_lower = _search_text.lower()
                _found_terms = [_t for _t in _query_terms if _t in _text_lower]
                if _found_terms:
                    from patterns.pattern_matcher import PatternHit

                    for _term in _found_terms:
                        _idx = _text_lower.find(_term)
                        if _idx >= 0:
                            _query_hits.append(PatternHit(
                                label="query_term",
                                pattern=_term,
                                start=_idx,
                                end=_idx + len(_term),
                                value=_search_text[_idx:_idx + len(_term)]
                            ))
                    if _query_hits:
                        hits = _query_hits
                        matched_count = len(_query_hits)
                        if graph is not None:
                            _add_pattern_hits_to_graph(hits, graph)
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001

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

        # ---- Per-page dedup + extract --------------------------------
        seen: set[tuple[str, str, str]] = set()
        unique_findings: list = []

        for hit in hits:
            key = (hit.label or "", hit.pattern, hit.value)
            if key in seen:
                continue
            seen.add(key)
            from .public_acceptance import _extract_live_public_findings_from_page

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
            unique_findings.append(findings_tuple[0])

        # ---- Storage --------------------------------------------------
        accepted_count = 0
        stored_count = 0
        storage_error: bool = False
        quality_gate_rejected: bool = False

        if store is not None and unique_findings:
            try:
                store_results = await store.drain_and_get_accepted(unique_findings)

                # F268: graph accumulation after canonical write
                # FIX-F320: accumulate accepted findings only (store_results),
                # not raw unique_findings (which includes rejected findings).
                if store_results is not None:
                    try:
                        from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator

                        _acc = SprintGraphAccumulator()
                        # store_results is list of FindingQualityDecision dicts with "accepted" key
                        _accepted = [
                            f for f, r in zip(unique_findings, store_results)
                            if isinstance(r, dict) and r.get("accepted")
                        ] if isinstance(store_results, list) else unique_findings
                        _acc.accumulate_findings(_accepted if _accepted else unique_findings, sprint_id="")
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


# ----------------------------------------------------------------------
# Helper functions for complex branches
# ----------------------------------------------------------------------


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
    from .public_acceptance import _build_public_finding
    from .public_patterns import _compute_page_usable_fields
    from .public_stages import PipelinePageResult

    _public_findings: list = []

    usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
        fetched=True, matched_patterns=0, stored_findings=0,
        quality_reason=quality_reason, discovery_signal=has_signal,
        discovery_score=discovery_score,
        error=None,
        extracted_text_len=len(extracted_text),
    )

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
                http_status_code=getattr(result, "status_code", 0) or 0,
            )
            if _pub_tuple:
                _public_findings.append(_pub_tuple[0])
        except Exception:
            _public_findings = []

    if not _public_findings and has_signal and (hit_title or hit_snippet):
        try:
            _signal_tuple = await _build_public_finding(
                query=query,
                url=hit_url,
                page_text="",
                hit_title=hit_title or "",
                hit_snippet=hit_snippet or "",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                http_status_code=getattr(result, "status_code", 0) or 0,
            )
            if _signal_tuple:
                _public_findings.extend(_signal_tuple)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001

    # Graph injection for public surface hits
    if graph is not None and _public_findings:
        try:
            for pf in _public_findings:
                graph.upsert_ioc(
                    ioc_type=getattr(pf, "ioc_type", "public_surface") or "public_surface",
                    value=getattr(pf, "value", hit_url) or hit_url,
                    source="public_pipeline",
                    properties={"query": query, "type": "public_surface"},
                )
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001

    _pub_accepted = 0
    _pub_stored = 0
    if _public_findings:
        if store is not None:
            try:
                _pub_results = await store.drain_and_get_accepted(_public_findings)
                for _sr in _pub_results:
                    if isinstance(_sr, dict):
                        if _sr.get("accepted"):
                            _pub_accepted += 1
                        if _sr.get("lmdb_success"):
                            _pub_stored += 1
                    else:
                        if getattr(_sr, "accepted", False):
                            _pub_accepted += 1
                        if getattr(_sr, "lmdb_success", False):
                            _pub_stored += 1
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001
        else:
            _pub_accepted = len(_public_findings)
            _pub_stored = _pub_accepted

    if _public_findings and _pub_stored > 0 and _pub_accepted == 0:
        _pub_dup_found = True
    else:
        _pub_dup_found = False

    if _pub_accepted > 0:
        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=0, stored_findings=_pub_stored,
            quality_reason=quality_reason, discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=None,
            extracted_text_len=len(extracted_text),
        )
        return PipelinePageResult(
            url=hit_url, fetched=True, matched_patterns=0,
            accepted_findings=_pub_accepted, stored_findings=_pub_stored,
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
            public_surface_dup=_pub_dup_found,
        )

    _tr_skipped: str | None = None
    if fetched_js_skip_reason == "browser_unavailable":
        _tr_skipped = "skipped_browser_unavailable"
    elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
        _tr_skipped = "skipped_xml_or_feed"
    _terminal_reason = _tr_skipped if _tr_skipped else "rejected_no_pattern_match"
    _rejection_reason = _tr_skipped if _tr_skipped else "no_pattern_match"

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
        rejection_reason=_rejection_reason,
        terminal_reason=_terminal_reason,
    )


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
