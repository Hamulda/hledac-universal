"""Ring buffer exporter: store spans in BoundedRing for inspection/tests."""
from __future__ import annotations

import threading
from typing import Any, Sequence

try:
    from opentelemetry.sdk.trace.export import (  # type: ignore
        SpanExportResult,
    )
except ImportError:  # pragma: no cover
    SpanExportResult = None  # type: ignore


def _to_record(span: Any, max_attrs: int) -> dict[str, Any]:
    """Compress ReadableSpan -> bounded dict (M1 8GB friendly)."""
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    trace_id = (
        format(ctx.trace_id, "032x") if ctx is not None and ctx.trace_id else "0" * 32
    )
    span_id = (
        format(ctx.span_id, "016x") if ctx is not None and ctx.span_id else "0" * 16
    )
    start = int(getattr(span, "start_time", 0) or 0)
    end = int(getattr(span, "end_time", 0) or 0)
    raw_attrs = dict(getattr(span, "attributes", None) or {})
    attrs = {k: raw_attrs[k] for k in list(raw_attrs.keys())[:max_attrs]}
    status = getattr(span, "status", None)
    return {
        "name": str(getattr(span, "name", ""))[:256],
        "trace_id": trace_id,
        "span_id": span_id,
        "start_time": start,
        "end_time": end,
        "duration_ns": max(0, end - start),
        "attributes": attrs,
        "status": str(getattr(status, "status_code", "UNSET"))
        if status is not None
        else "UNSET",
    }


class RingBufferExporter:
    """Stores span summaries in a BoundedRing.

    Test-friendly: every span ends up addressable in the ring by (trace_id, span_id).
    Bounded: ring evicts oldest when full.
    """

    def __init__(self, ring: Any, max_attrs: int = 32) -> None:
        self._ring = ring
        self._max_attrs = max(1, min(128, int(max_attrs)))
        self._lock = threading.Lock()
        self._exported = 0
        self._failed = 0

    def export(self, spans: Sequence[Any]) -> Any:
        if not spans:
            return SpanExportResult.SUCCESS if SpanExportResult is not None else 0
        with self._lock:
            for sp in spans:
                try:
                    rec = _to_record(sp, self._max_attrs)
                    self._ring.put((rec["trace_id"], rec["span_id"]), rec)
                    self._exported += 1
                except Exception:
                    self._failed += 1
        return SpanExportResult.SUCCESS if SpanExportResult is not None else 0

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "exported": self._exported,
                "failed": self._failed,
            }
