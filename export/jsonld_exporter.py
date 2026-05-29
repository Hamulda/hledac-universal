# hledac/universal/export/jsonld_exporter.py
# Sprint 8BJ — JSON-LD Structured Diagnostic Export
# Zero LLM / Zero model runtime / Zero network
"""
Deterministic, side-effect-free JSON-LD diagnostic exporter for ObservedRunReport.
Accepts msgspec.Struct or Mapping input. Produces stable JSON-LD output
with schema.org + ghost namespace context, ready for graph ingest and
future MLX/Outlines synthesis.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from hledac.universal.security.pq_crypto import (
    PostQuantumBackend,
    PQAvailability,
    PQSignature,
    PQStatus,
    create_post_quantum_backend,
)

__all__ = [
    "render_jsonld",
    "render_jsonld_str",
    "render_jsonld_to_path",
    "render_analyst_evidence_jsonld",
    "render_analyst_evidence_jsonld_str",
]

# ---------------------------------------------------------------------------
# Ghost namespace URI (local, self-hosted)
# ---------------------------------------------------------------------------
_GHOST_NS = "https://ghost-prime.ai/ns/2024/jsonld"

# JSON-LD @context (schema.org + ghost namespace)
_JSONLD_CONTEXT: list[str | dict[str, Any]] = [
    "https://schema.org",
    {
        "ghost": _GHOST_NS,
        "DiagnosticReport": "https://schema.org/DiagnosticReport",
        "SoftwareSourceCode": "https://schema.org/SoftwareSourceCode",
        "DataFeed": "https://schema.org/DataFeed",
        "WebContent": "https://schema.org/WebContent",
        "Person": "https://schema.org/Person",
        "Organization": "https://schema.org/Organization",
        "string": "https://schema.org/text",
        "number": "https://schema.org Number",
        "boolean": "https://schema.org/Boolean",
        "runMetadata": "https://schema.org/Thing",
        "signalFunnel": "https://schema.org/Thing",
        "storeRejectionTrace": "https://schema.org/Thing",
        "perSourceHealth": "https://schema.org/ItemList",
        "runtimeTruth": "https://schema.org/Thing",
        "generatedAt": "https://schema.org/dateCreated",
        "runId": "https://schema.org/identifier",
        "totalSources": "https://schema.org/Number",
        "completedSources": "https://schema.org/Number",
        "elapsedMs": "https://schema.org/Number",
        "acceptedFindings": "https://schema.org/Number",
        "rootCause": "https://schema.org/Text",
        "rootCauseLabel": "https://schema.org/Text",
        "recommendation": "https://schema.org/Text",
        "entriesSeen": "https://schema.org/Number",
        "entriesScanned": "https://schema.org/Number",
        "entriesWithHits": "https://schema.org/Number",
        "totalPatternHits": "https://schema.org/Number",
        "findingsBuilt": "https://schema.org/Number",
        "acceptedCountDelta": "https://schema.org/Number",
        "lowInfoRejected": "https://schema.org/Number",
        "inMemDupRejected": "https://schema.org/Number",
        "persistentDupRejected": "https://schema.org/Number",
        "otherRejected": "https://schema.org/Number",
        "isNetworkVariance": "https://schema.org/Boolean",
        "umaAvailable": "https://schema.org/Boolean",
        "umaSnapshot": "https://schema.org/Thing",
        "bootstrapApplied": "https://schema.org/Boolean",
        "patternsConfigured": "https://schema.org/Number",
        "successRate": "https://schema.org/Number",
        "failedSourceCount": "https://schema.org/Number",
        "perSource": "https://schema.org/ItemList",
        "feedUrl": "https://schema.org/URL",
        "label": "https://schema.org/Text",
        "fetchedEntries": "https://schema.org/Number",
        "storedFindings": "https://schema.org/Number",
        "elapsedSourceMs": "https://schema.org/Number",
        "error": "https://schema.org/Text",
        "signalStage": "https://schema.org/Text",
        "diagnosticRunId": "https://schema.org/identifier",
        "startedTs": "https://schema.org/Number",
        "finishedTs": "https://schema.org/Number",
        "batchError": "https://schema.org/Text",
        "dedupSurfaceAvailable": "https://schema.org/Boolean",
        "dedupDelta": "https://schema.org/Thing",
        "contentQualityValidated": "https://schema.org/Boolean",
        "actualLiveRunExecuted": "https://schema.org/Boolean",
        "healthBreakdown": "https://schema.org/Thing",
        "entriesWithEmptyAssembledText": "https://schema.org/Number",
        "entriesWithText": "https://schema.org/Number",
        "avgAssembledTextLen": "https://schema.org/Number",
    },
]

# Canonical root-cause → label (shared with markdown_reporter)
_ROOT_CAUSE_LABELS: dict[str, str] = {
    "network_variance": "Network Variance",
    "no_new_entries": "No New Entries",
    "empty_registry": "Empty Registry",
    "no_pattern_hits": "No Pattern Hits",
    "no_pattern_hits_possible_morphology_gap": "No Pattern Hits (Morphology Gap)",
    "pattern_hits_but_no_findings_built": "Pattern Hits But No Findings Built",
    "low_information_rejection_dominant": "Low-Information Rejection Dominant",
    "duplicate_rejection_dominant": "Duplicate Rejection Dominant",
    "accepted_present": "Accepted Findings Present",
    "unknown": "Unknown",
}

# Root-cause → recommendation fallback (shared with markdown_reporter)
_FALLBACK_RECOMMENDATION: dict[str, str] = {
    "network_variance": "repeat_live_run",
    "no_new_entries": "repeat_live_run",
    "empty_registry": "check_registry",
    "no_pattern_hits": "update_patterns",
    "no_pattern_hits_possible_morphology_gap": "update_patterns",
    "pattern_hits_but_no_findings_built": "update_extraction_logic",
    "low_information_rejection_dominant": "update_quality_thresholds",
    "duplicate_rejection_dominant": "update_dedup_logic",
    "accepted_present": "continue_monitoring",
    "unknown": "repeat_live_run",
}


# ---------------------------------------------------------------------------
# Input normalisation (delegates to markdown_reporter; also standalone-safe)
# ---------------------------------------------------------------------------
def normalize_export_input(report: object) -> dict[str, Any]:
    """
    Convert ObservedRunReport (msgspec.Struct) or Mapping → plain dict.

    msgspec.Structs use ``__struct_fields__`` for field order.
    For Mapping objects, dict(report) is safe.
    """
    if hasattr(report, "__struct_fields__"):
        return {f: getattr(report, f) for f in report.__struct_fields__}
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "keys"):
        return dict(cast(Mapping, report))
    raise TypeError(
        f"report must be msgspec.Struct or Mapping, got {type(report).__name__}"
    )


# ---------------------------------------------------------------------------
# Canonical label helpers (exported for reuse)
# ---------------------------------------------------------------------------
def get_root_cause_label(root_cause: str) -> str:
    return _ROOT_CAUSE_LABELS.get(root_cause, _ROOT_CAUSE_LABELS["unknown"])


def get_recommendation(report: dict[str, Any]) -> str:
    rec = report.get("recommendation")
    if rec:
        return rec
    root = report.get("diagnostic_root_cause", "unknown")
    return _FALLBACK_RECOMMENDATION.get(root, _FALLBACK_RECOMMENDATION["unknown"])


# ---------------------------------------------------------------------------
# JSON-LD render helpers
# ---------------------------------------------------------------------------
def _iso_timestamp(ts: Any) -> str:
    """Convert unix timestamp to RFC3339 ISO string."""
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (TypeError, ValueError):
        return "unknown"


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


def _build_run_metadata(data: dict[str, Any]) -> dict[str, Any]:
    ts = data.get("started_ts") or data.get("finished_ts")
    generated = _iso_timestamp(ts) if ts else "unknown"
    return {
        "@type": "ghost:RunMetadata",
        "ghost:generatedAt": generated,
        "ghost:diagnosticRunId": _safe_str(data.get("diagnostic_run_id") or data.get("run_id") or "unknown"),
        "ghost:startedTs": data.get("started_ts"),
        "ghost:finishedTs": data.get("finished_ts"),
        "ghost:elapsedMs": data.get("elapsed_ms"),
        "ghost:totalSources": data.get("total_sources"),
        "ghost:completedSources": data.get("completed_sources"),
        "ghost:actualLiveRunExecuted": data.get("actual_live_run_executed", False),
        "ghost:batchError": _safe_str(data.get("batch_error") or ""),
    }


def _build_signal_funnel(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "@type": "ghost:SignalFunnel",
        "ghost:entriesSeen": data.get("entries_seen", 0),
        "ghost:entriesWithEmptyAssembledText": data.get("entries_with_empty_assembled_text", 0),
        "ghost:entriesWithText": data.get("entries_with_text", 0),
        "ghost:entriesScanned": data.get("entries_scanned", 0),
        "ghost:entriesWithHits": data.get("entries_with_hits", 0),
        "ghost:totalPatternHits": data.get("total_pattern_hits", 0),
        "ghost:findingsBuilt": data.get("findings_built_pre_store", 0),
        "ghost:signalStage": _safe_str(data.get("signal_stage") or "unknown"),
    }


def _build_store_rejection_trace(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "@type": "ghost:StoreRejectionTrace",
        "ghost:acceptedCountDelta": data.get("accepted_count_delta", 0),
        "ghost:lowInformationRejectedCountDelta": data.get("low_information_rejected_count_delta", 0),
        "ghost:inMemoryDuplicateRejectedCountDelta": data.get("in_memory_duplicate_rejected_count_delta", 0),
        "ghost:persistentDuplicateRejectedCountDelta": data.get("persistent_duplicate_rejected_count_delta", 0),
        "ghost:otherRejectedCountDelta": data.get("other_rejected_count_delta", 0),
        "ghost:entropyThreshold": data.get("entropy_threshold"),
        "ghost:entropyMinLen": data.get("entropy_min_len"),
    }


def _build_runtime_truth(data: dict[str, Any]) -> dict[str, Any]:
    uma = data.get("uma_snapshot", {})
    return {
        "@type": "ghost:RuntimeTruth",
        "ghost:umaAvailable": bool(uma),
        "ghost:umaSnapshot": uma or None,
        "ghost:dedupSurfaceAvailable": data.get("dedup_surface_available", False),
        "ghost:dedupDelta": data.get("dedup_delta") or None,
        "ghost:bootstrapApplied": data.get("bootstrap_applied", False),
        "ghost:patternsConfigured": data.get("patterns_configured", 0),
        "ghost:contentQualityValidated": data.get("content_quality_validated", False),
        "ghost:successRate": data.get("success_rate"),
        "ghost:failedSourceCount": data.get("failed_source_count", 0),
        "ghost:healthBreakdown": data.get("health_breakdown") or None,
    }


def _build_per_source_health(data: dict[str, Any]) -> list[dict[str, Any]]:
    per_source = data.get("per_source")
    if not per_source:
        return []
    # Sort by feed_url for determinism
    sorted_sources = sorted(per_source, key=lambda s: str(s.get("feed_url", "")))
    items = []
    for src in sorted_sources:
        items.append({
            "@type": "ghost:SourceHealth",
            "ghost:feedUrl": _safe_str(src.get("feed_url", "")),
            "ghost:label": _safe_str(src.get("label", "")),
            "ghost:origin": _safe_str(src.get("origin", "")),
            "ghost:priority": src.get("priority"),
            "ghost:fetchedEntries": src.get("fetched_entries", 0),
            "ghost:acceptedFindings": src.get("accepted_findings", 0),
            "ghost:storedFindings": src.get("stored_findings", 0),
            "ghost:elapsedSourceMs": src.get("elapsed_ms", 0),
            "ghost:error": _safe_str(src.get("error") or "") or None,
        })
    return items


def _build_root_cause(data: dict[str, Any]) -> dict[str, Any]:
    root = data.get("diagnostic_root_cause", "unknown")
    label = get_root_cause_label(root)
    return {
        "@type": "ghost:RootCause",
        "ghost:rootCause": root,
        "ghost:rootCauseLabel": label,
        "ghost:isNetworkVariance": data.get("is_network_variance", False),
        "ghost:recommendation": get_recommendation(data),
    }


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------
def render_jsonld(report: object) -> dict[str, Any]:
    """
    Render an ObservedRunReport (or Mapping) as a JSON-LD dict.

    Parameters
    ----------
    report : msgspec.Struct or Mapping
        The observed run report.

    Returns
    -------
    dict
        JSON-LD-formatted diagnostic report with @context, @type, and
        ghost: namespace fields.
    """
    data = normalize_export_input(report)

    root_cause_data = _build_root_cause(data)

    obj: dict[str, Any] = {
        "@context": _JSONLD_CONTEXT,
        "@type": "ghost:DiagnosticReport",
        "ghost:reportVersion": "1.0",
        "ghost:generatedAt": _iso_timestamp(
            data.get("started_ts") or data.get("finished_ts")
        ),
        "ghost:runMetadata": _build_run_metadata(data),
        "ghost:acceptedFindings": data.get("accepted_findings", 0),
        "ghost:signalFunnel": _build_signal_funnel(data),
        "ghost:storeRejectionTrace": _build_store_rejection_trace(data),
        "ghost:runtimeTruth": _build_runtime_truth(data),
        "ghost:rootCause": root_cause_data,
        "ghost:perSourceHealth": _build_per_source_health(data),
        "ghost:diagnosticRunId": _safe_str(data.get("diagnostic_run_id") or data.get("run_id") or "unknown"),
    }

    # Remove None values for cleaner output
    def _clean(v: Any) -> Any:
        if isinstance(v, dict):
            return {k2: _clean(v2) for k2, v2 in v.items() if v2 is not None}
        if isinstance(v, list):
            return [_clean(i) for i in v if i is not None]
        return v

    return _maybe_sign_jsonld(_clean(obj))


# ---------------------------------------------------------------------------
# Sprint F214AC: Post-Quantum ML-DSA-65 JSON-LD signature
# Fail-safe throughout — skip silently if PQ backend unavailable
# ---------------------------------------------------------------------------

def _maybe_sign_jsonld(obj: dict[str, Any]) -> dict[str, Any]:
    """
    Add ML-DSA-65 PQ signature to JSON-LD dict if backend available.

    GHOST_INVARIANTS: no asyncio.run() in async context.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_maybe_sign_jsonld_async(obj))
    else:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pq_sign")
        future = loop.run_in_executor(pool, _sync_pq_sign_jsonld, obj)
        try:
            return future.result()
        finally:
            pool.shutdown(wait=True)


def _sync_pq_sign_jsonld(obj: dict[str, Any]) -> dict[str, Any]:
    """to_thread target — runs event loop in a separate thread."""
    return asyncio.run(_maybe_sign_jsonld_async(obj))


async def _maybe_sign_jsonld_async(obj: dict[str, Any]) -> dict[str, Any]:
    """
    Async PQ signing path — gather(return_exceptions=True) on all awaits.

    Returns obj unchanged if PQ unavailable or signing fails.
    """
    try:
        results = await asyncio.gather(
            _get_pq_backend_async(),
            return_exceptions=True,
        )
        error_results = [r for r in results if isinstance(r, Exception)]
        if error_results:
            return obj

        backend, status = results[0]
        if not backend.is_available():
            return obj
        if status.availability not in (PQAvailability.AVAILABLE,):
            return obj

        key_id = "com.hledac.pq.signing.v1"
        extension = _build_pq_extension_jsonld(obj, backend, key_id)
        if extension is None:
            return obj

        signed = dict(obj)
        signed["ghost:pqSignature"] = extension
        return signed
    except Exception:
        return obj


async def _get_pq_backend_async() -> tuple[PostQuantumBackend, PQStatus]:
    """Get PQ backend — always use create_post_quantum_backend (async factory)."""
    backend, status = await create_post_quantum_backend()
    return backend, status


def _build_pq_extension_jsonld(obj: dict[str, Any], backend: PostQuantumBackend, key_id: str) -> dict[str, Any] | None:
    """
    Compute ML-DSA-65 signature over JSON-LD object canonical digest.

    Returns None silently on any error (GHOST_INVARIANTS: fail-safe).
    """
    try:
        import hashlib

        canonical: bytes = json.dumps(
            obj,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest: str = hashlib.sha256(canonical).hexdigest()

        if not backend.ensure_mldsa_key(key_id, level=65):
            return None

        sig: PQSignature = backend.sign_mldsa_digest(key_id, digest, level=65)

        return {
            "extension_type": "hledac:pq-signature",
            "ml_dsa_signature": sig.signature.hex(),
            "ml_dsa_level": sig.security_level,
            "key_id": key_id,
            "jsonld_sha256": digest,
            "backend": backend.name(),
            "hybrid": True,
        }
    except Exception:
        return None


def render_jsonld_str(report: object) -> str:
    """
    Render report as a deterministic JSON string.

    Returns
    -------
    str
        JSON string with sorted keys for determinism.
    """
    obj = render_jsonld(report)
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# File-output helper
# ---------------------------------------------------------------------------
def render_jsonld_to_path(
    report: object,
    path: str | Path | None = None,
) -> Path:
    """
    Render report as JSON-LD and write to ``path``.

    If ``path`` is None:
      1. ``GHOST_EXPORT_DIR`` env var (override, backward compatible)
      2. ``RUNS_ROOT`` (runtime/runs/)

    Filename is deterministic: ``ghost_diagnostic_{run_id}.jsonld``
    falling back to ``ghost_diagnostic_{timestamp}.jsonld``.

    Returns the Path of the written file.
    """
    content = render_jsonld_str(report)

    if path is None:
        export_dir_env = os.environ.get("GHOST_EXPORT_DIR")
        if export_dir_env:
            base = Path(export_dir_env)
        else:
            from hledac.universal.paths import RUNS_ROOT
            base = RUNS_ROOT
            base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(path).parent

    filename = Path(path).name if path else None
    if not filename:
        try:
            data = normalize_export_input(report)
            run_id = data.get("diagnostic_run_id") or data.get("run_id")
        except Exception:
            run_id = None
        if run_id:
            safe = str(run_id).replace("/", "_").replace("\\", "_")
            filename = f"ghost_diagnostic_{safe}.jsonld"
        else:
            try:
                ts = normalize_export_input(report).get("started_ts") or normalize_export_input(report).get("finished_ts")
            except Exception:
                ts = None
            if ts:
                filename = f"ghost_diagnostic_{int(ts)}.jsonld"
            else:
                filename = "ghost_diagnostic.jsonld"

    out_path = base / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ============================================================================
# Sprint F202F: Analyst Workbench Evidence Export
# ============================================================================
def render_analyst_evidence_jsonld(
    question: str,
    extractive_answer: str,
    evidence_pointers: list,
    related_entities: list,
    sources_used: list[str],
    context_bytes: int,
    model_used: bool,
    timing_ms: float,
) -> dict[str, Any]:
    """
    Sprint F202F: Render analyst answer evidence as JSON-LD.

    Formats an analyst workbench answer with evidence pointers and related
    entities as a JSON-LD document using the ghost namespace.

    Args:
        question: Original analyst question
        extractive_answer: Deterministic extractive text answer
        evidence_pointers: List of EvidencePointer dataclass instances
        related_entities: List of RelatedEntity dataclass instances
        sources_used: List of source_type strings consulted
        context_bytes: Bytes used for extractive answer context
        model_used: True if LLM was used for this answer
        timing_ms: Total time in milliseconds

    Returns:
        dict: JSON-LD formatted analyst evidence document
    """
    evidence_items = []
    for ep in evidence_pointers:
        item = {
            "@type": "ghost:EvidencePointer",
            "ghost:findingId": ep.finding_id,
            "ghost:sourceType": ep.source_type,
            "ghost:query": ep.query,
            "ghost:confidence": ep.confidence,
            "ghost:timestamp": ep.ts,
            "ghost:provenance": list(ep.provenance),
            "ghost:envelopeAvailable": ep.envelope_available,
        }
        if ep.snippet:
            item["ghost:snippet"] = ep.snippet
        evidence_items.append(item)

    entity_items = []
    for entity in related_entities:
        item = {
            "@type": "ghost:RelatedEntity",
            "ghost:entityValue": entity.entity_value,
            "ghost:entityType": entity.entity_type,
            "ghost:confidence": entity.confidence,
            "ghost:hops": entity.hops,
            "ghost:relationTypes": list(entity.relation_types),
        }
        entity_items.append(item)

    return {
        "@context": _JSONLD_CONTEXT,
        "@type": "ghost:AnalystEvidence",
        "ghost:reportVersion": "1.0",
        "ghost:question": question,
        "ghost:extractiveAnswer": extractive_answer,
        "ghost:evidencePointers": evidence_items,
        "ghost:relatedEntities": entity_items,
        "ghost:sourcesUsed": sources_used,
        "ghost:contextBytes": context_bytes,
        "ghost:modelUsed": model_used,
        "ghost:timingMs": timing_ms,
        "ghost:generatedAt": datetime.now(UTC).isoformat(),
    }


def render_analyst_evidence_jsonld_str(
    question: str,
    extractive_answer: str,
    evidence_pointers: list,
    related_entities: list,
    sources_used: list[str],
    context_bytes: int,
    model_used: bool,
    timing_ms: float,
) -> str:
    """
    Render analyst evidence as a deterministic JSON string.

    Returns:
        str: JSON string with sorted keys for determinism.
    """
    obj = render_analyst_evidence_jsonld(
        question=question,
        extractive_answer=extractive_answer,
        evidence_pointers=evidence_pointers,
        related_entities=related_entities,
        sources_used=sources_used,
        context_bytes=context_bytes,
        model_used=model_used,
        timing_ms=timing_ms,
    )
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
