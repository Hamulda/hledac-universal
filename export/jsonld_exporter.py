# hledac/universal/export/jsonld_exporter.py
# Sprint 8BJ — JSON-LD Structured Diagnostic Export
# Zero LLM / Zero model runtime / Zero network
"""
Deterministic, side-effect-free JSON-LD diagnostic exporter for ObservedRunReport.
Accepts msgspec.Struct or Mapping input. Produces stable JSON-LD output
with schema.org + ghost namespace context, ready for graph ingest and
future MLX/Outlines synthesis.
"""
import msgspec


import asyncio
import os
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from hledac.universal.utils.asyncx import parallel
from ._shared import _iso_timestamp, _safe_str, normalize_export_input  # noqa: E402  # F4.3 deduplication

try:
    import orjson as _orjson

    _HAS_ORJSON = True
except ImportError:
    _orjson = None  # type: ignore[assignment,has-type]  # orjson unavailable
    _HAS_ORJSON = False


def _json_dumps(data: Any, **kwargs: Any) -> str:
    """F4.3: Centralized JSON — orjson 3-5× faster than stdlib json."""
    if _HAS_ORJSON:
        opts = 0
        if kwargs.get("sort_keys"):
            opts |= _orjson.OPT_SORT_KEYS
        return _orjson.dumps(data, option=opts).decode("utf-8")
    import json as _j

    return _j.dumps(data, **kwargs)


from hledac.universal.security.pq_crypto import (  # noqa: E402
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
    "build_forensic_analysis_jsonld",
]

# ---------------------------------------------------------------------------
# Ghost namespace URI (local, self-hosted)
# ---------------------------------------------------------------------------
_GHOST_NS = "https://ghost-prime.ai/ns/2024/jsonld"

# Sprint F263: Bounded forensic-render budgets. Shared semantics with
# markdown_reporter — kept in lockstep for cross-format determinism.
_FORENSIC_SOURCE_TYPES: tuple[str, ...] = (
    "forensic_analysis",
    "steganography_detection",
    "digital_ghost_detection",
    "blockchain_forensics",
)
_FORENSIC_MAX_RENDER: int = 200
_FORENSIC_MAX_IOC_SAMPLE: int = 5
_FORENSIC_MAX_IOC_TYPE_LEN: int = 24
_FORENSIC_MAX_VALUE_LEN: int = 96
_FORENSIC_MAX_PAYLOAD_PARSE: int = 2048

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
        # Sprint F263: forensic analysis surface
        "forensicAnalysis": "https://schema.org/DigitalDocument",
        "forensicTotalCount": "https://schema.org/Number",
        "forensicBySourceType": "https://schema.org/ItemList",
        "forensicIOCHistogram": "https://schema.org/ItemList",
        "forensicSampleValues": "https://schema.org/ItemList",
        "forensicConfidenceMin": "https://schema.org/Number",
        "forensicConfidenceMax": "https://schema.org/Number",
        "forensicConfidenceAvg": "https://schema.org/Number",
        "forensicTruncated": "https://schema.org/Boolean",
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


# normalize_export_input — delegated to _shared (F4.3)
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
# Sprint F263: Forensic analysis JSON-LD builder
# ---------------------------------------------------------------------------
def _parse_forensic_payload_jsonld(payload: str | None) -> dict[str, str] | None:
    """
    Parse a forensic finding's payload_text into a small dict.

    Mirrors the markdown-side helper exactly to keep both formats in
    lockstep. Bounded: trims to ``_FORENSIC_MAX_PAYLOAD_PARSE`` bytes
    before parsing. Returns None on missing / unparseable input.
    """
    if not payload or not isinstance(payload, str):
        return None
    bounded = payload[:_FORENSIC_MAX_PAYLOAD_PARSE]
    if bounded.lstrip().startswith("{"):
        try:
            import json as _json
            obj = _json.loads(bounded)
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None
        out: dict[str, str] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, (str, int, float, bool)):
                out[k[:_FORENSIC_MAX_IOC_TYPE_LEN]] = str(v)[:_FORENSIC_MAX_VALUE_LEN]
        return out or None
    out2: dict[str, str] = {}
    for chunk in bounded.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        k = k.strip()[:_FORENSIC_MAX_IOC_TYPE_LEN]
        v = v.strip()[:_FORENSIC_MAX_VALUE_LEN]
        if k:
            out2[k] = v
    return out2 or None


def build_forensic_analysis_jsonld(
    findings: Iterable[Any] | None,
) -> dict[str, Any]:
    """
    Sprint F263: Render forensic findings as a JSON-LD ``ghost:ForensicAnalysis``
    entity. Reads findings in the same shape as
    :func:`markdown_reporter.aggregate_forensic_findings` and emits a
    stable, bounded dict with:

      * total count, by-source-type histogram
      * IOC histogram (sorted by count desc, then name asc)
      * sample values (≤ ``_FORENSIC_MAX_IOC_SAMPLE`` per IOC type)
      * confidence min/max/avg
      * truncation flag (bounded at ``_FORENSIC_MAX_RENDER`` findings)

    Always returns a fully-shaped dict (no ``None`` at top level) so the
    downstream ``_clean`` filter can drop only the optional sub-fields.
    Fail-safe: never raises; returns an empty forensic surface on any error.
    """
    empty: dict[str, Any] = {
        "@type": "ghost:ForensicAnalysis",
        "ghost:forensicTotalCount": 0,
        "ghost:forensicBySourceType": [],
        "ghost:forensicIOCHistogram": [],
        "ghost:forensicSampleValues": [],
        "ghost:forensicTruncated": False,
    }
    if not findings:
        return empty
    try:
        by_source: dict[str, int] = {}
        ioc_hist: dict[str, int] = {}
        samples: dict[str, list[str]] = {}
        confs: list[float] = []
        truncated = False
        seen = 0
        for f in findings:
            seen += 1
            if seen > _FORENSIC_MAX_RENDER:
                truncated = True
                break
            if not isinstance(f, dict):
                continue
            src = str(f.get("source_type", "") or "")[:64]
            if src not in _FORENSIC_SOURCE_TYPES:
                continue
            by_source[src] = by_source.get(src, 0) + 1
            try:
                c = float(f.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                c = 0.0
            if 0.0 <= c <= 1.0:
                confs.append(c)
            parsed = _parse_forensic_payload_jsonld(f.get("payload_text"))
            if not parsed:
                continue
            ioc_t = parsed.get("ioc_type", "unknown")[:_FORENSIC_MAX_IOC_TYPE_LEN]
            val = parsed.get("value", "")[:_FORENSIC_MAX_VALUE_LEN]
            if not ioc_t:
                continue
            ioc_hist[ioc_t] = ioc_hist.get(ioc_t, 0) + 1
            if val and ioc_t not in samples:
                samples[ioc_t] = []
            if val and ioc_t in samples and len(samples[ioc_t]) < _FORENSIC_MAX_IOC_SAMPLE:
                if val not in samples[ioc_t]:
                    samples[ioc_t].append(val)
        if not by_source:
            return empty
        out: dict[str, Any] = {
            "@type": "ghost:ForensicAnalysis",
            "ghost:forensicTotalCount": sum(by_source.values()),
            "ghost:forensicBySourceType": [
                {
                    "@type": "ghost:ForensicSourceCount",
                    "ghost:sourceType": src,
                    "ghost:count": by_source[src],
                }
                for src in sorted(by_source.keys())
            ],
            "ghost:forensicIOCHistogram": [
                {
                    "@type": "ghost:ForensicIOCEntry",
                    "ghost:iocType": ioc_t,
                    "ghost:count": cnt,
                }
                for ioc_t, cnt in sorted(ioc_hist.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "ghost:forensicSampleValues": [
                {
                    "@type": "ghost:ForensicIOCSample",
                    "ghost:iocType": ioc_t,
                    "ghost:values": list(vals)[:_FORENSIC_MAX_IOC_SAMPLE],
                }
                for ioc_t, vals in sorted(samples.items())
            ],
            "ghost:forensicTruncated": truncated,
        }
        if confs:
            out["ghost:forensicConfidenceMin"] = min(confs)
            out["ghost:forensicConfidenceMax"] = max(confs)
            out["ghost:forensicConfidenceAvg"] = sum(confs) / len(confs)
        return out
    except Exception:
        return empty


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
        "ghost:forensicAnalysis": build_forensic_analysis_jsonld(
            data.get("forensic_findings")
        ),
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
    P1-1: run_sync_async handles both running and non-running loop cases.
    """
    from hledac.universal.utils.sync_bridge import run_sync_async
    return run_sync_async(_maybe_sign_jsonld_async(obj))


def _sync_pq_sign_jsonld(obj: dict[str, Any]) -> dict[str, Any]:
    """
    to_thread target — runs async PQ sign in a separate thread.

    P1-1 FIX: Replaced asyncio.run() with run_sync_async().
    asyncio.run() inside run_in_executor thread is M1 Metal crash vector.
    """
    from hledac.universal.utils.sync_bridge import run_sync_async
    return run_sync_async(_maybe_sign_jsonld_async(obj))


async def _maybe_sign_jsonld_async(obj: dict[str, Any]) -> dict[str, Any]:
    """
    Async PQ signing path — gather(return_exceptions=True) on all awaits.

    Returns obj unchanged if PQ unavailable or signing fails.
    """
    try:
        # F314: migrated asyncio.gather -> parallel(policy='collect')
        _result = await parallel([_get_pq_backend_async()], taskgroup=True, policy='collect', ctx="jsonld_exporter:pq_backend")
        results = _result.ok
        error_results = _result.errors
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

        canonical: bytes = _json_dumps(
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
            "backend": backend.name,
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
    return _json_dumps(obj, indent=True, sort_keys=True, ensure_ascii=False)


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
                ts = normalize_export_input(report).get("started_ts") or normalize_export_input(report).get("finished_ts")  # noqa: E501
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
    return _json_dumps(obj, indent=True, sort_keys=True, ensure_ascii=False)
