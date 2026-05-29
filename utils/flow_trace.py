"""
FlowTrace - Low-overhead data flow tracing for Hledac
======================================================

Lightweight internal flow trace layer that maps real data flow and
bottlenecks without heavy observability stack.

Env flags:
  GHOST_FLOW_TRACE=1           - enable tracing
  GHOST_FLOW_TRACE_SAMPLE_RATE - sampling rate (default 1.0)
  GHOST_FLOW_TRACE_MAX_EVENTS  - max events before flush (default 50000)

Outputs:
  - trace events JSONL
  - summary JSON

Invariants:
  - Always-on, no feature toggles for default path
  - Fail-open: tracing must never crash runtime
  - Bounded memory: no unbounded in-memory lists
  - JSONL immediate append or small bounded buffer

Sprint 8C3 schema extensions:
  - source_family, acquisition_mode, transport_family
  - content_type, bytes_in, bytes_out
  - dedup_reason, fallback_reason
  - evidence_quality_tier, corroboration_key
  - is_unindexed_candidate, is_archive_hit, is_passive_hit
  - is_hidden_service, is_decentralized_hit
  - challenge_present, challenge_type, challenge_outcome
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

# ============================================================================
# Canonical Enums (Sprint 8C3)
# ============================================================================

SOURCE_FAMILY_ENUM = frozenset({
    "indexed_search", "archive", "passive_dns", "ct_log",
    "code_repo", "breach", "social", "infra_scan",
    "hidden_service", "decentralized", "realtime_feed", "direct_web", "unknown",
})

ACQUISITION_MODE_ENUM = frozenset({
    "search", "direct_fetch", "api", "archive_lookup",
    "websocket_stream", "passive_lookup", "crawler_discovery", "replay",
})

CHALLENGE_OUTCOME_ENUM = frozenset({
    "none", "passive_clear", "interactive_pass", "fail", "loop", "abandon",
})

CHALLENGE_TYPE_ENUM = frozenset({
    "none", "captcha", "js_challenge", "cookie_wall", "rate_limit",
    "geo_block", "ua_block", "other",
})

# ============================================================================
# Configuration
# ============================================================================

TRACE_ENABLED = os.environ.get("GHOST_FLOW_TRACE", "0") == "1"
TRACE_SAMPLE_RATE = float(os.environ.get("GHOST_FLOW_TRACE_SAMPLE_RATE", "1.0"))
TRACE_MAX_EVENTS = int(os.environ.get("GHOST_FLOW_TRACE_MAX_EVENTS", "50000"))

# Bounded event buffer before forced flush
_MAX_BUFFER_SIZE = 100

# ============================================================================
# Global State (process-wide, thread-safe)
# ============================================================================

_trace_lock = threading.Lock()
_run_id: str | None = None
_session_start: float = time.time()
_event_count: int = 0
_drop_count: int = 0

# Bounded buffer - flushes to disk when full
_event_buffer: deque = deque(maxlen=_MAX_BUFFER_SIZE)

# Trace file handles (opened lazily)
_trace_jsonl_path: Path | None = None
_trace_jsonl_file = None
_trace_summary_path: Path | None = None

# Span stack for nested span tracking
_span_stack: dict[str, float] = {}  # span_id -> start_time

# Counters
_counters: dict[str, int] = {}

# ============================================================================
# Path resolution (fail-safe)
# ============================================================================

def _get_trace_root() -> Path:
    """Get trace output directory with fallbacks."""
    try:
        from ..paths import RUNS_ROOT
        root = RUNS_ROOT
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception:
        # Fallback to /tmp
        return Path("/tmp/hledac_trace")

def _get_trace_paths() -> tuple[Path, Path]:
    """Get JSONL and summary paths."""
    root = _get_trace_root()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pid = os.getpid()
    run_suffix = f"{_run_id or 'unknown'}_{ts}_{pid}"
    jsonl_path = root / f"flow_{run_suffix}.jsonl"
    summary_path = root / f"flow_{run_suffix}_summary.json"
    return jsonl_path, summary_path

# ============================================================================
# Core API
# ============================================================================

def is_enabled() -> bool:
    """Check if tracing is enabled."""
    return TRACE_ENABLED

def set_run_id(run_id: str) -> None:
    """Set the current run ID for trace correlation."""
    global _run_id
    _run_id = run_id

def _should_sample() -> bool:
    """Determine if this event should be sampled."""
    if not TRACE_ENABLED:
        return False
    if TRACE_SAMPLE_RATE >= 1.0:
        return True
    import random
    return random.random() < TRACE_SAMPLE_RATE

def trace_event(
    component: str,
    stage: str,
    event_type: str,
    item_id: str | None = None,
    url: str | None = None,
    target: str | None = None,
    status: str = "ok",
    duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Record a trace event.

    Args:
        component: Source component (e.g., "fetch_coordinator", "evidence_log")
        stage: Pipeline stage (e.g., "fetch", "gating", "evidence_flush")
        event_type: Event type (e.g., "fetch_start", "fetch_end", "exception", "drop")
        item_id: Unique item identifier if available
        url: URL if related to fetch
        target: Generic target/location identifier
        status: Event status ("ok", "error", "drop", "timeout", "retry", "fallback")
        duration_ms: Duration in milliseconds if relevant
        metadata: Additional bounded metadata dict
    """
    global _event_count, _drop_count

    if not TRACE_ENABLED:
        return

    if not _should_sample():
        return

    event = {
        "ts": time.time(),
        "run_id": _run_id or "unknown",
        "component": component,
        "stage": stage,
        "event_type": event_type,
        "item_id": item_id,
        "url": url,
        "target": target,
        "status": status,
        "duration_ms": duration_ms,
        "elapsed_ms": (time.time() - _session_start) * 1000,
        "metadata": _safe_metadata(metadata),
    }

    with _trace_lock:
        try:
            _ensure_file_open()
            if _trace_jsonl_file is not None:
                line = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
                _trace_jsonl_file.write(line + '\n')
                _event_count += 1

                # Bounded buffer flush check
                if _event_count % _MAX_BUFFER_SIZE == 0:
                    _trace_jsonl_file.flush()
        except Exception:
            _drop_count += 1
            # Fail-open: tracing error never crashes runtime

def trace_span_start(span_id: str, metadata: dict[str, Any] | None = None) -> float:
    """
    Start a trace span.

    Args:
        span_id: Unique span identifier
        metadata: Optional span metadata

    Returns:
        Start timestamp for span
    """
    if not TRACE_ENABLED:
        return 0.0

    start_time = time.time()
    with _trace_lock:
        _span_stack[span_id] = start_time

    return start_time

def trace_span_end(
    span_id: str,
    component: str,
    stage: str,
    status: str = "ok",
    metadata: dict[str, Any] | None = None,
) -> float | None:
    """
    End a trace span.

    Args:
        span_id: Span identifier (must match start)
        component: Source component
        stage: Pipeline stage
        status: Span status
        metadata: Optional metadata

    Returns:
        Duration in ms, or None if span wasn't started
    """
    if not TRACE_ENABLED:
        return None

    end_time = time.time()
    duration_ms = None

    with _trace_lock:
        start_time = _span_stack.pop(span_id, None)
        if start_time is not None:
            duration_ms = (end_time - start_time) * 1000

    if duration_ms is not None:
        trace_event(
            component=component,
            stage=stage,
            event_type="span_end",
            target=span_id,
            status=status,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    return duration_ms

def trace_counter(name: str, value: int = 1, metadata: dict[str, Any] | None = None) -> None:
    """
    Increment a named counter.

    Args:
        name: Counter name
        value: Increment value (default 1)
        metadata: Optional metadata
    """
    if not TRACE_ENABLED:
        return

    with _trace_lock:
        _counters[name] = _counters.get(name, 0) + value

def flush() -> None:
    """Flush trace buffers to disk."""
    if not TRACE_ENABLED:
        return

    with _trace_lock:
        if _trace_jsonl_file is not None:
            try:
                _trace_jsonl_file.flush()
            except Exception:
                pass

def get_summary() -> dict[str, Any]:
    """
    Generate trace summary statistics.

    Returns:
        Summary dict with counts, p50/p95 durations, top stages, etc.
    """
    if not TRACE_ENABLED:
        return {}

    # Note: This is a lightweight summary - full analysis done by analyze script
    with _trace_lock:
        return {
            "run_id": _run_id,
            "event_count": _event_count,
            "drop_count": _drop_count,
            "counters": dict(_counters),
            "session_elapsed_sec": time.time() - _session_start,
        }

# ============================================================================
# Internal helpers
# ============================================================================

def _ensure_file_open() -> None:
    """Lazily open trace files."""
    global _trace_jsonl_path, _trace_jsonl_file, _trace_summary_path

    if _trace_jsonl_file is None:
        _trace_jsonl_path, _trace_summary_path = _get_trace_paths()
        try:
            _trace_jsonl_file = open(_trace_jsonl_path, 'a', buffering=8192, encoding='utf-8')
        except Exception:
            _trace_jsonl_file = None

def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize metadata dict for trace safety."""
    if metadata is None:
        return {}

    safe = {}
    for k, v in metadata.items():
        if isinstance(v, (str, int, float, bool)):
            safe[k] = v
        elif isinstance(v, (list, tuple)):
            # Bounded list
            safe[k] = list(v)[:20]
        elif isinstance(v, dict):
            # Bounded dict
            safe[k] = {kk: vv for kk, vv in list(v.items())[:10] if isinstance(vv, (str, int, float, bool))}
        else:
            safe[k] = str(v)[:100]
    return safe

# ============================================================================
# Convenience wrappers for common patterns
# ============================================================================

def trace_fetch_start(url: str, transport: str, metadata: dict[str, Any] | None = None) -> None:
    """Trace fetch start event."""
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="fetch_start",
        url=url,
        target=transport,
        status="ok",
        metadata=metadata,
    )

def trace_fetch_end(url: str, transport: str, status: str, duration_ms: float,
                    metadata: dict[str, Any] | None = None) -> None:
    """Trace fetch end event."""
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="fetch_end",
        url=url,
        target=transport,
        status=status,
        duration_ms=duration_ms,
        metadata=metadata,
    )

def trace_dedup_decision(url: str, is_deduped: bool) -> None:
    """Trace URL dedup decision."""
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="dedup_decision",
        url=url,
        status="deduped" if is_deduped else "passed",
    )

def trace_evidence_append(event_type: str, queue_size: int, status: str,
                           metadata: dict[str, Any] | None = None) -> None:
    """Trace evidence append request."""
    trace_event(
        component="evidence_log",
        stage="evidence_append",
        event_type=event_type,
        target=f"queue_size_{queue_size}",
        status=status,
        metadata=metadata,
    )

def trace_evidence_flush(batch_size: int, flush_latency_ms: float, status: str,
                         rows_persisted: int | None = None) -> None:
    """Trace evidence flush worker batch."""
    trace_event(
        component="evidence_log",
        stage="evidence_flush",
        event_type="flush_batch",
        status=status,
        duration_ms=flush_latency_ms,
        metadata={
            "batch_size": batch_size,
            "rows_persisted": rows_persisted,
        } if rows_persisted else {"batch_size": batch_size},
    )

def trace_queue_drop(queue_name: str, queue_size: int) -> None:
    """Trace queue drop event."""
    trace_event(
        component="evidence_log",
        stage="queue",
        event_type="queue_drop",
        target=queue_name,
        status="drop",
        metadata={"queue_size": queue_size},
    )


# ============================================================================
# Sprint 8C3: Extended convenience wrappers
# ============================================================================

def trace_source_dedup_dropped(
    url: str,
    source_family: str,
    dedup_reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace when a source is dropped due to deduplication."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    meta = dict(metadata) if metadata else {}
    meta["dedup_reason"] = str(dedup_reason)[:50]
    trace_event(
        component="fetch_coordinator",
        stage="source_funnel",
        event_type="source_dedup_dropped",
        url=url,
        target=source_family,
        status="deduped",
        metadata=meta,
    )


def trace_provider_fallback(
    url: str,
    source_family: str,
    from_transport: str,
    to_transport: str,
    fallback_reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace provider fallback event (e.g., 403, 429, timeout)."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    meta = dict(metadata) if metadata else {}
    meta["from_transport"] = str(from_transport)[:30]
    meta["to_transport"] = str(to_transport)[:30]
    meta["fallback_reason"] = str(fallback_reason)[:80]
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="provider_fallback",
        url=url,
        target=to_transport,
        status="fallback",
        metadata=meta,
    )


def trace_fallback_after_403(
    url: str,
    source_family: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace fallback triggered by HTTP 403."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="fallback_after_403",
        url=url,
        target=transport,
        status="fallback",
        metadata=metadata,
    )


def trace_fallback_after_429(
    url: str,
    source_family: str,
    transport: str,
    retry_after: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace fallback triggered by HTTP 429."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    meta = dict(metadata) if metadata else {}
    if retry_after is not None:
        meta["retry_after"] = retry_after
    trace_event(
        component="fetch_coordinator",
        stage="fetch",
        event_type="fallback_after_429",
        url=url,
        target=transport,
        status="fallback",
        metadata=meta,
    )


def trace_challenge_issued(
    url: str,
    source_family: str,
    challenge_type: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace anti-bot challenge issued by server."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    if challenge_type not in CHALLENGE_TYPE_ENUM:
        challenge_type = "other"
    trace_event(
        component="fetch_coordinator",
        stage="challenge_funnel",
        event_type="challenge_issued",
        url=url,
        target=transport,
        status="challenge",
        metadata=_merge_metadata(metadata, {"challenge_type": challenge_type}),
    )


def trace_challenge_passed(
    url: str,
    source_family: str,
    challenge_type: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace anti-bot challenge passed."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    if challenge_type not in CHALLENGE_TYPE_ENUM:
        challenge_type = "other"
    trace_event(
        component="fetch_coordinator",
        stage="challenge_funnel",
        event_type="challenge_passed",
        url=url,
        target=transport,
        status="ok",
        metadata=_merge_metadata(metadata, {"challenge_type": challenge_type, "challenge_outcome": "passive_clear"}),
    )


def trace_challenge_failed(
    url: str,
    source_family: str,
    challenge_type: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace anti-bot challenge failed."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    if challenge_type not in CHALLENGE_TYPE_ENUM:
        challenge_type = "other"
    trace_event(
        component="fetch_coordinator",
        stage="challenge_funnel",
        event_type="challenge_failed",
        url=url,
        target=transport,
        status="fail",
        metadata=_merge_metadata(metadata, {"challenge_type": challenge_type, "challenge_outcome": "fail"}),
    )


def trace_challenge_loop_detected(
    url: str,
    source_family: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace challenge loop detected (same challenge repeatedly)."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="fetch_coordinator",
        stage="challenge_funnel",
        event_type="challenge_loop_detected",
        url=url,
        target=transport,
        status="loop",
        metadata=_merge_metadata(metadata, {"challenge_outcome": "loop"}),
    )


def trace_clearance_reused(
    url: str,
    source_family: str,
    transport: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace clearance cookie/session reused."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="fetch_coordinator",
        stage="challenge_funnel",
        event_type="clearance_reused",
        url=url,
        target=transport,
        status="ok",
        metadata=_merge_metadata(metadata, {"challenge_outcome": "passive_clear"}),
    )


def trace_source_accepted(
    url: str,
    source_family: str,
    acquisition_mode: str,
    content_type: str | None = None,
    bytes_in: int | None = None,
    bytes_out: int | None = None,
    is_hidden_service: bool = False,
    is_archive_hit: bool = False,
    is_passive_hit: bool = False,
    is_unindexed_candidate: bool = False,
    is_decentralized_hit: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace source accepted into evidence funnel."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    if acquisition_mode not in ACQUISITION_MODE_ENUM:
        acquisition_mode = "direct_fetch"
    meta = dict(metadata) if metadata else {}
    if content_type:
        meta["content_type"] = str(content_type)[:60]
    if bytes_in is not None:
        meta["bytes_in"] = max(0, int(bytes_in))
    if bytes_out is not None:
        meta["bytes_out"] = max(0, int(bytes_out))
    if is_hidden_service:
        meta["is_hidden_service"] = 1
    if is_archive_hit:
        meta["is_archive_hit"] = 1
    if is_passive_hit:
        meta["is_passive_hit"] = 1
    if is_unindexed_candidate:
        meta["is_unindexed_candidate"] = 1
    if is_decentralized_hit:
        meta["is_decentralized_hit"] = 1
    trace_event(
        component="fetch_coordinator",
        stage="source_funnel",
        event_type="source_accepted",
        url=url,
        target=source_family,
        status="ok",
        metadata=meta,
    )


def trace_evidence_append_ext(
    event_type: str,
    queue_size: int,
    status: str,
    source_family: str | None = None,
    evidence_quality_tier: str | None = None,
    corroboration_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace evidence append with extended Sprint 8C3 metadata."""
    meta = dict(metadata) if metadata else {}
    if source_family and source_family in SOURCE_FAMILY_ENUM:
        meta["source_family"] = source_family
    if evidence_quality_tier:
        meta["evidence_quality_tier"] = str(evidence_quality_tier)[:20]
    if corroboration_key:
        meta["corroboration_key"] = str(corroboration_key)[:100]
    trace_event(
        component="evidence_log",
        stage="evidence_append",
        event_type=event_type,
        target=f"queue_size_{queue_size}",
        status=status,
        metadata=meta,
    )


def trace_evidence_emitted(
    finding_id: str,
    source_family: str,
    evidence_quality_tier: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace evidence emitted to downstream consumer."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="evidence_log",
        stage="evidence_emitted",
        event_type="evidence_emitted",
        item_id=finding_id,
        target=source_family,
        status="ok",
        metadata=_merge_metadata(metadata, {"evidence_quality_tier": evidence_quality_tier}),
    )


def trace_evidence_corroborated(
    finding_id: str,
    source_family: str,
    corroboration_key: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace evidence corroborated by another source."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="evidence_log",
        stage="evidence_corroboration",
        event_type="evidence_corroborated",
        item_id=finding_id,
        target=source_family,
        status="ok",
        metadata=_merge_metadata(metadata, {"corroboration_key": corroboration_key}),
    )


def trace_evidence_rejected_low_quality(
    finding_id: str,
    source_family: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace evidence rejected due to low quality."""
    if source_family not in SOURCE_FAMILY_ENUM:
        source_family = "unknown"
    trace_event(
        component="evidence_log",
        stage="evidence_quality",
        event_type="evidence_rejected_low_quality",
        item_id=finding_id,
        target=source_family,
        status="rejected",
        metadata=_merge_metadata(metadata, {"reject_reason": str(reason)[:80]}),
    )


def trace_evidence_flush_persisted(
    batch_size: int,
    flush_latency_ms: float,
    status: str,
    rows_persisted: int | None = None,
    bytes_written: int | None = None,
) -> None:
    """Trace evidence flush that persisted data."""
    meta: dict[str, Any] = {"batch_size": batch_size}
    if rows_persisted is not None:
        meta["rows_persisted"] = rows_persisted
    if bytes_written is not None:
        meta["bytes_written"] = max(0, int(bytes_written))
    trace_event(
        component="evidence_log",
        stage="evidence_flush",
        event_type="evidence_flush_persisted",
        status=status,
        duration_ms=flush_latency_ms,
        metadata=meta,
    )


def trace_periodic_flow_snapshot(
    queue_depth: int,
    frontier_size: int,
    active_fetches: int,
    rss_mb: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace periodic system snapshot for flow health."""
    meta = dict(metadata) if metadata else {}
    meta["queue_depth"] = max(0, queue_depth)
    meta["frontier_size"] = max(0, frontier_size)
    meta["active_fetches"] = max(0, active_fetches)
    if rss_mb is not None:
        meta["rss_mb"] = round(max(0.0, rss_mb), 1)
    trace_event(
        component="autonomous_orchestrator",
        stage="system_snapshot",
        event_type="periodic_flow_snapshot",
        status="ok",
        metadata=meta,
    )


def trace_queue_snapshot(
    queue_name: str,
    depth: int,
    enqueue_rate: float | None = None,
    dequeue_rate: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace queue depth snapshot."""
    meta = dict(metadata) if metadata else {}
    meta["depth"] = max(0, depth)
    if enqueue_rate is not None:
        meta["enqueue_rate"] = round(max(0.0, enqueue_rate), 3)
    if dequeue_rate is not None:
        meta["dequeue_rate"] = round(max(0.0, dequeue_rate), 3)
    trace_event(
        component="autonomous_orchestrator",
        stage="queue_snapshot",
        event_type="queue_snapshot",
        target=queue_name,
        status="ok",
        metadata=meta,
    )


def trace_transport_mix_snapshot(
    transport_counts: dict[str, int],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace transport mix snapshot (curl/tor/lightpanda counts)."""
    # Safe transport counts - bounded to 20 entries
    safe_counts = {k[:30]: max(0, v) for k, v in list(transport_counts.items())[:20]}
    trace_event(
        component="autonomous_orchestrator",
        stage="transport_mix",
        event_type="transport_mix_snapshot",
        status="ok",
        metadata=_merge_metadata(metadata, {"transport_counts": safe_counts}),
    )


def trace_source_family_counts(
    family_counts: dict[str, int],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Trace source family distribution snapshot."""
    safe_counts = {k: max(0, v) for k, v in list(family_counts.items())[:30]}
    trace_event(
        component="autonomous_orchestrator",
        stage="source_funnel",
        event_type="source_family_counts",
        status="ok",
        metadata=_merge_metadata(metadata, {"family_counts": safe_counts}),
    )


# ============================================================================
# Internal helper
# ============================================================================

def _merge_metadata(
    base: dict[str, Any] | None,
    additions: dict[str, Any],
) -> dict[str, Any]:
    """Merge additions into a copy of base, or {} if base is None."""
    result = dict(base) if base else {}
    for k, v in additions.items():
        if v is not None:
            result[k] = v
    return result


# ============================================================================
# Module-level flush atexit
# ============================================================================

import atexit


def _flush_atexit() -> None:
    """Ensure trace flush on interpreter exit."""
    flush()

atexit.register(_flush_atexit)

# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Core
    'is_enabled',
    'set_run_id',
    'trace_event',
    'trace_span_start',
    'trace_span_end',
    'trace_counter',
    'flush',
    'get_summary',
    # Sprint 8C1 legacy
    'trace_fetch_start',
    'trace_fetch_end',
    'trace_dedup_decision',
    'trace_evidence_append',
    'trace_evidence_flush',
    'trace_queue_drop',
    # Sprint 8C3 enums
    'SOURCE_FAMILY_ENUM',
    'ACQUISITION_MODE_ENUM',
    'CHALLENGE_OUTCOME_ENUM',
    'CHALLENGE_TYPE_ENUM',
    # Sprint 8C3 fetch/source funnel
    'trace_source_dedup_dropped',
    'trace_provider_fallback',
    'trace_fallback_after_403',
    'trace_fallback_after_429',
    'trace_source_accepted',
    # Sprint 8C3 challenge funnel
    'trace_challenge_issued',
    'trace_challenge_passed',
    'trace_challenge_failed',
    'trace_challenge_loop_detected',
    'trace_clearance_reused',
    # Sprint 8C3 evidence funnel
    'trace_evidence_append_ext',
    'trace_evidence_emitted',
    'trace_evidence_corroborated',
    'trace_evidence_rejected_low_quality',
    'trace_evidence_flush_persisted',
    # Sprint 8C3 snapshots
    'trace_periodic_flow_snapshot',
    'trace_queue_snapshot',
    'trace_transport_mix_snapshot',
    'trace_source_family_counts',
]
