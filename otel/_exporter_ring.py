"""Ring buffer exporter: store spans in BoundedRing for inspection/tests."""

import threading
from collections.abc import Sequence
from typing import Any

try:
    from opentelemetry.sdk.trace.export import SpanExportResult
except ImportError:
    SpanExportResult = None

# WIRING_COMPLETE I2: Lock-free Rust counters for hot-path telemetry
try:
    from hledac_rust_extensions import hledac_rust_extensions as _rust_ext

    _RUST_AVAILABLE = hasattr(_rust_ext, "create_counter")
    if _RUST_AVAILABLE:
        _EXPORTED_COUNTER = _rust_ext.create_counter("otel_ring_exported")
        _FAILED_COUNTER = _rust_ext.create_counter("otel_ring_failed")
    else:
        _RUST_AVAILABLE = False
        _EXPORTED_COUNTER = None
        _FAILED_COUNTER = None
except ImportError:
    _RUST_AVAILABLE = False
    _EXPORTED_COUNTER = None
    _FAILED_COUNTER = None


def _to_record(span: Any, max_attrs: int) -> dict[str, Any]:
    """Compress ReadableSpan -> bounded dict (M1 8GB friendly)."""
    ctx = span.get_span_context() if hasattr(span, "get_span_context") else None
    trace_id = format(ctx.trace_id, "032x") if ctx is not None and ctx.trace_id else "0" * 32
    span_id = format(ctx.span_id, "016x") if ctx is not None and ctx.span_id else "0" * 16
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
        "status": str(getattr(status, "status_code", "UNSET")) if status is not None else "UNSET",
    }


class RingBufferExporter:
    """Stores span summaries in a BoundedRing.

    Test-friendly: every span ends up addressable in the ring by (trace_id, span_id).
    Bounded: ring evicts oldest when full.

    WIRING_COMPLETE I2: Uses Rust lock-free counters for exported/failed stats.
    threading.Lock still protects ring.put() for thread-safe OrderedDict access.
    """

    __slots__ = ("_lock", "_max_attrs", "_ring")

    def __init__(self, ring: Any, max_attrs: int = 32) -> None:
        self._ring = ring
        self._max_attrs = max(1, min(128, int(max_attrs)))
        self._lock = threading.Lock()

    def export(self, spans: Sequence[Any]) -> Any:
        if not spans:
            return SpanExportResult.SUCCESS if SpanExportResult is not None else 0
        exported_count = 0
        failed_count = 0
        with self._lock:
            for sp in spans:
                try:
                    rec = _to_record(sp, self._max_attrs)
                    self._ring.put((rec["trace_id"], rec["span_id"]), rec)
                    exported_count += 1
                except Exception:
                    failed_count += 1
        # WIRING_COMPLETE I2: Lock-free counter increments after lock release
        # Rust MPSC is lock-free on sender side - no GIL contention
        if _EXPORTED_COUNTER is not None and exported_count > 0:
            _EXPORTED_COUNTER.add(exported_count, 0)
        if _FAILED_COUNTER is not None and failed_count > 0:
            _FAILED_COUNTER.add(failed_count, 0)
        return SpanExportResult.SUCCESS if SpanExportResult is not None else 0

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def stats(self) -> dict[str, int]:
        # WIRING_COMPLETE I2: Read from Rust counters (lock-free)
        if _RUST_AVAILABLE and _EXPORTED_COUNTER is not None and _FAILED_COUNTER is not None:
            exported, _ = _EXPORTED_COUNTER.get()
            failed, _ = _FAILED_COUNTER.get()
            return {"exported": exported, "failed": failed}
        return {"exported": 0, "failed": 0}
