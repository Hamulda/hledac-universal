"""Report / RL / ToT helpers for Phase 6 of the live public pipeline.

F360-REFACTOR recovery
----------------------
These functions were extracted out of ``live_public_pipeline.py`` during the
phase split, but the ``_report_helpers`` module was never actually written.
``Phase6_ReportGenerator.run`` therefore imported a non-existent module and
crashed on every call. The real ``_generate_and_store_report`` implementation is
restored here from the refactor migration record; ``_run_rl_loop`` /
``_run_hypothesis_tot`` remain safe placeholders (they return
``{"tot_solution_count": 0}``) until their engines are wired.

All paths are fail-soft: any error returns an empty / zero result so the
pipeline keeps running. Concurrency of the three Phase-6 tasks is governed by
the caller through ``parallel(concurrency=...)`` (see ``_phases.py``), which
applies a memory-aware budget so several LLM tasks don't OOM the 8 GiB M1.

MLX safety
----------
``hermes_engine.generate_report`` runs inference on the M1 GPU, which is not
concurrency-safe. Report generation is serialized per-engine via an async lock
so that running report + RL + ToT concurrently can never corrupt model state.
"""

from __future__ import annotations

import asyncio
import logging
import msgspec.json as _json
import re
import time
from typing import Any

from hledac.universal.pipeline.public_patterns import _make_finding_id

logger = logging.getLogger(__name__)

# Report tuning constants (were module-level globals in the legacy pipeline).
_REPORT_TOP_N: int = 10
_REPORT_SOURCE_TYPE: str = "osint_report"

# Per-engine inference locks. MLX on the M1 GPU is not concurrency-safe, so we
# serialize generate calls that target the same engine instance. Keyed by
# ``id(engine)``; one lock per live engine is negligible memory.
_engine_locks: dict[int, asyncio.Lock] = {}


def _engine_lock(engine: Any) -> asyncio.Lock:
    """Return (creating if needed) the async lock serializing inference for ``engine``."""
    key = id(engine)
    lock = _engine_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _engine_locks[key] = lock
    return lock


async def _generate_and_store_report(
    query: str,
    pages: tuple,
    store: Any | None,
    hermes_engine: Any | None,
    vector_store: Any | None = None,
) -> str:
    """P6: Generate OSINT report from top findings and store in DuckDB.

    Fail-soft: returns empty string on any error. Pipeline continues regardless.
    """
    if hermes_engine is None:
        return ""
    vector_candidates = await _vector_search_context(query, vector_store)
    sorted_pages = sorted(
        pages, key=lambda p: (p.matched_patterns or 0, p.accepted_findings or 0), reverse=True
    )
    top_pages = sorted_pages[:_REPORT_TOP_N]
    if not top_pages:
        return ""
    context_items = _build_report_context(pages, top_pages, vector_candidates)
    report_text = await _generate_routed_report(query, context_items, hermes_engine)
    if not report_text:
        return ""
    if store is not None:
        report_id = _make_finding_id(
            query=query,
            url="synthetic://report",
            label="osint_report",
            pattern="synthetic",
            value=report_text[:200],
        )
        await _store_report_and_inference(store, query, report_text, report_id, hermes_engine)
    return report_text


async def _vector_search_context(query: str, vector_store: Any) -> list:
    """P13: Perform vector search for RAG context."""
    if vector_store is None:
        return []
    try:
        from hledac.universal.brain.model_manager import get_model_manager
        from hledac.universal.embedding_pipeline import embed_query_async

        model_manager = get_model_manager()
        async with model_manager.embedding_lifecycle():
            query_vec = await embed_query_async(query)
        raw_similar = vector_store.query(query_vec, k=10, index_type="text")
        if raw_similar:
            logger.info(f"[P13] Vector search found {len(raw_similar)} similar docs")
        return raw_similar or []
    except Exception as e:  # noqa: BLE001 - fail-soft RAG context
        logger.warning(f"[P13] Vector search failed: {e}")
        return []


def _build_report_context(pages: tuple, top_pages: list, vector_candidates: list) -> list[str]:
    """Build context items from pages with RRF fusion."""
    pattern_ranked = [
        (
            getattr(p, "url", "") or "",
            (p.matched_patterns or 0) + (p.accepted_findings or 0) * 0.5,
        )
        for p in top_pages
        if getattr(p, "url", "")
    ]
    if vector_candidates and pattern_ranked:
        try:
            from hledac.universal.utils.ranking import rrf_fuse

            fused_ids = rrf_fuse([vector_candidates, pattern_ranked], k=60)
            url_order = fused_ids[:_REPORT_TOP_N]
        except Exception:  # noqa: BLE001 - fall back to pattern ranking
            url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    else:
        url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    url_to_page = {getattr(p, "url", ""): p for p in pages}
    context_items: list[str] = []
    for url in url_order:
        page = url_to_page.get(url)
        if page:
            context_items.append(
                f"URL: {url}\n"
                f"Title/Reason: {getattr(page, 'discovery_reason', '') or getattr(page, 'quality_reason', '') or url}\n"
                f"IOC count: {page.matched_patterns or 0}, Accepted findings: {page.accepted_findings or 0}"
            )
    return context_items


async def _generate_routed_report(query: str, context_items: list, hermes_engine: Any) -> str:
    """Route model and generate report (serialized per-engine for MLX safety)."""
    if hermes_engine is None:
        return ""
    try:
        from hledac.universal.brain.moe_router import route as moe_route

        model_choice = moe_route(query, {"urls": []})
    except Exception:  # noqa: BLE001 - default to hermes on router failure
        model_choice = "hermes"
    try:
        async with _engine_lock(hermes_engine):
            match model_choice:
                case "vision":
                    return "[image description] " + "\n".join(context_items[:3])
                case "modernbert":
                    try:
                        from hledac.universal.brain.modernbert_engine import ModernBertEngine

                        return await ModernBertEngine().summarize(context_items)
                    except Exception as e:  # noqa: BLE001 - fall back to hermes
                        logger.warning(f"[P14] ModernBERT failed: {e}")
                        return await hermes_engine.generate_report(query, context_items)
                case _:
                    return await hermes_engine.generate_report(query, context_items)
    except Exception as e:  # noqa: BLE001 - fail-soft generation
        logger.warning(f"[REPORT] Generation failed: {e}")
        return ""


async def _store_report_and_inference(
    store: Any, query: str, report_text: str, report_id: str, hermes_engine: Any
) -> None:
    """Store report and Hermes inference findings."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    try:
        report_finding = CanonicalFinding(
            finding_id=report_id,
            query=query,
            source_type=_REPORT_SOURCE_TYPE,
            confidence=0.7,
            ts=time.time(),
            provenance=("source_family:public", "report_generation", hermes_engine.__class__.__name__),
            payload_text=report_text,
        )
        await store.submit_findings([report_finding])
        logger.info(f"[REPORT] Stored report {report_id[:8]}")
    except Exception as e:  # noqa: BLE001 - fail-soft storage
        logger.warning(f"[REPORT] Storage failed: {e}")
    try:
        from hledac.universal.runtime.hermes_pivot_contract import HermesInferenceOutput

        key_iocs, key_entities = await _extract_iocs_from_report(report_text)
        hermes_output = HermesInferenceOutput(
            output_id=report_id,
            source_finding_id=report_id,
            inference_type="report_synthesis",
            timestamp=time.time(),
            primary_text=report_text,
            confidence=0.7,
            key_iocs=key_iocs,
            key_entities=key_entities,
            pivot_suggestions=key_iocs[:10],
            bounded=False,
            tokens_used=0,
            model_name=hermes_engine.__class__.__name__,
            source_hints=("public",),
        )
        hermes_finding = CanonicalFinding(
            finding_id=hermes_output.output_id,
            query=query,
            source_type="hermes_inference",
            confidence=hermes_output.confidence,
            ts=hermes_output.timestamp,
            provenance=("source_family:public", "hermes_inference", hermes_engine.__class__.__name__),
            payload_text=_json.encode(hermes_output.to_dict()).decode("utf-8")[:4096],
        )
        await store.submit_findings([hermes_finding])
        logger.info(f"[F256] Stored hermes_inference {hermes_output.output_id[:8]}")
    except Exception as e:  # noqa: BLE001 - fail-soft inference storage
        logger.warning(f"[F256] HermesInferenceOutput failed: {e}")


async def _extract_iocs_from_report(report_text: str) -> tuple[list[str], list[str]]:
    """Extract IOCs and entities from report text."""
    key_iocs: list[str] = []
    key_entities: list[str] = []
    ioc_json = re.search(r"<IOC_JSON>\s*(\{.*?\})\s*</IOC_JSON>", report_text, re.DOTALL)
    if ioc_json:
        try:
            ioc_data = _json.decode(ioc_json.group(1))
            return list(ioc_data.get("iocs", [])[:20]), list(ioc_data.get("entities", [])[:20])
        except (ValueError, KeyError):
            pass
    try:
        from hledac.universal.utils.ioc_extract import extract_iocs_single

        ioc_tuples = await extract_iocs_single(report_text)
        return (
            [v for _, v in ioc_tuples if len(v) > 3][:20],
            [v for t, v in ioc_tuples if t in ("org", "person", "gpe", "product")][:20],
        )
    except ImportError:
        try:
            from hledac.universal.brain.ner_engine import extract_iocs_from_text

            ioc_results = extract_iocs_from_text(report_text)
            return (
                [r["value"] for r in ioc_results if r.get("value") and len(r["value"]) > 3][:20],
                [r["value"] for r in ioc_results if r.get("ioc_type") in ("org", "person", "gpe", "product")][:20],
            )
        except Exception:  # noqa: BLE001 - fall back to empty
            pass
    return [], []


async def _run_rl_loop(ctx: Any, all_page_results: list) -> dict:
    """Run reinforcement learning loop.

    Placeholder: the RL engine is not yet wired into the public pipeline.
    Returns a zero result so Phase 6 stays fail-soft.
    """
    return {"tot_solution_count": 0}


async def _run_hypothesis_tot(ctx: Any, all_page_results: list) -> dict:
    """Run hypothesis generation and Tree-of-Thoughts.

    Placeholder: the ToT engine is not yet wired into the public pipeline.
    Returns a zero result so Phase 6 stays fail-soft.
    """
    return {"tot_solution_count": 0}
