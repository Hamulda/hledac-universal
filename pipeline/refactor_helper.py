"""Helper script to refactor _generate_and_store_report function."""
import re
from core import aclose

with open('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py', 'r') as f:
    content = f.read()

start = content.find('async def _generate_and_store_report(')
if start == -1:
    print("Function not found!")
    exit(1)

# Find the next function at column 0 (not indented)
rest = content[start:]
next_func_match = re.search(r'\n(?:async )?def [a-zA-Z_]', rest[50:])
if next_func_match:
    end = start + 50 + next_func_match.start()
else:
    print("Could not find end of function")
    exit(1)

old_function = content[start:end]
print(f"Found function of length {len(old_function)}")

new_functions = '''async def _generate_and_store_report(
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
    sorted_pages = sorted(pages, key=lambda p: (p.matched_patterns or 0, p.accepted_findings or 0), reverse=True)
    top_pages = sorted_pages[:_REPORT_TOP_N]
    if not top_pages:
        return ""
    context_items = _build_report_context(pages, top_pages, vector_candidates)
    report_text = await _generate_routed_report(query, context_items, hermes_engine)
    if not report_text:
        return ""
    if store is not None:
        report_id = _make_finding_id(query=query, url="synthetic://report", label="osint_report", pattern="synthetic", value=report_text[:200])
        await _store_report_and_inference(store, query, report_text, report_id, hermes_engine)
    return report_text


async def _vector_search_context(query: str, vector_store) -> list:
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
    except Exception as e:
        logger.warning(f"[P13] Vector search failed: {e}")
        return []


def _build_report_context(pages: tuple, top_pages: list, vector_candidates: list) -> list[str]:
    """Build context items from pages with RRF fusion."""
    pattern_ranked = [(getattr(p, "url", "") or "", (p.matched_patterns or 0) + (p.accepted_findings or 0) * 0.5) for p in top_pages if getattr(p, "url", "")]
    if vector_candidates and pattern_ranked:
        try:
            from hledac.universal.utils.ranking import rrf_fuse
            fused_ids = rrf_fuse([vector_candidates, pattern_ranked], k=60)
            url_order = fused_ids[:_REPORT_TOP_N]
        except Exception:
            url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    else:
        url_order = [u for u, _ in pattern_ranked[:_REPORT_TOP_N]]
    url_to_page = {getattr(p, "url", ""): p for p in pages}
    context_items = []
    for url in url_order:
        page = url_to_page.get(url)
        if page:
            context_items.append(f"URL: {url}\\nTitle/Reason: {getattr(page, 'discovery_reason', '') or getattr(page, 'quality_reason', '') or url}\\nIOC count: {page.matched_patterns or 0}, Accepted findings: {page.accepted_findings or 0}")
    return context_items


async def _generate_routed_report(query: str, context_items: list, hermes_engine) -> str:
    """Route model and generate report."""
    try:
        from hledac.universal.brain.moe_router import route as moe_route
        model_choice = moe_route(query, {"urls": []})
    except Exception:
        model_choice = "hermes"
    try:
        match model_choice:
            case "vision":
                return "[image description] " + "\\n".join(context_items[:3])
            case "modernbert":
                try:
                    from hledac.universal.brain.modernbert_engine import ModernBertEngine
                    return await ModernBertEngine().summarize(context_items)
                except Exception as e:
                    logger.warning(f"[P14] ModernBERT failed: {e}")
                    return await hermes_engine.generate_report(query, context_items)
            case _:
                return await hermes_engine.generate_report(query, context_items)
    except Exception as e:
        logger.warning(f"[REPORT] Generation failed: {e}")
        return ""


async def _store_report_and_inference(store, query: str, report_text: str, report_id: str, hermes_engine) -> None:
    """Store report and Hermes inference findings."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    try:
        report_finding = CanonicalFinding(
            finding_id=report_id, query=query, source_type=_REPORT_SOURCE_TYPE,
            confidence=0.7, ts=time.time(),
            provenance=("source_family:public", "report_generation", hermes_engine.__class__.__name__),
            payload_text=report_text,
        )
        await store.submit_findings([report_finding])
        logger.info(f"[REPORT] Stored report {report_id[:8]}")
    except Exception as e:
        logger.warning(f"[REPORT] Storage failed: {e}")
    try:
        from hledac.universal.runtime.hermes_pivot_contract import HermesInferenceOutput
        key_iocs, key_entities = await _extract_iocs_from_report(report_text)
        hermes_output = HermesInferenceOutput(
            output_id=report_id, source_finding_id=report_id, inference_type="report_synthesis",
            timestamp=time.time(), primary_text=report_text, confidence=0.7,
            key_iocs=key_iocs, key_entities=key_entities, pivot_suggestions=key_iocs[:10],
            bounded=False, tokens_used=0, model_name=hermes_engine.__class__.__name__, source_hints=("public",),
        )
        hermes_finding = CanonicalFinding(
            finding_id=hermes_output.output_id, query=query, source_type="hermes_inference",
            confidence=hermes_output.confidence, ts=hermes_output.timestamp,
            provenance=("source_family:public", "hermes_inference", hermes_engine.__class__.__name__),
            payload_text=_json.encode(hermes_output.to_dict()).decode("utf-8")[:4096],
        )
        await store.submit_findings([hermes_finding])
        logger.info(f"[F256] Stored hermes_inference {hermes_output.output_id[:8]}")
    except Exception as e:
        logger.warning(f"[F256] HermesInferenceOutput failed: {e}")


async def _extract_iocs_from_report(report_text: str) -> tuple[list[str], list[str]]:
    """Extract IOCs and entities from report text."""
    key_iocs, key_entities = [], []
    ioc_json = re.search(r"<IOC_JSON>\\s*(\\{.*?\\})\\s*</IOC_JSON>", report_text, re.DOTALL)
    if ioc_json:
        try:
            ioc_data = _json.decode(ioc_json.group(1))
            return list(ioc_data.get("iocs", [])[:20]), list(ioc_data.get("entities", [])[:20])
        except (ValueError, KeyError):
            pass
    try:
        from hledac.universal.utils.ioc_extract import extract_iocs_single
        ioc_tuples = await extract_iocs_single(report_text)
        return [v for _, v in ioc_tuples if len(v) > 3][:20], [v for t, v in ioc_tuples if t in ("org", "person", "gpe", "product")][:20]
    except ImportError:
        try:
            from hledac.universal.brain.ner_engine import extract_iocs_from_text
            ioc_results = extract_iocs_from_text(report_text)
            return [r["value"] for r in ioc_results if r.get("value") and len(r["value"]) > 3][:20], [r["value"] for r in ioc_results if r.get("ioc_type") in ("org", "person", "gpe", "product")][:20]
        except Exception:
            pass
    return [], []


'''

new_content = content[:start] + new_functions + content[end:]

with open('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py', 'w') as f:
    f.write(new_content)

print("File updated successfully")
