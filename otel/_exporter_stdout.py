"""Stdout JSON exporter for OTel spans (OTLP-shaped JSON-Lines).

Writes one JSON object per line to sys.stdout (or any TextIO). Greppable, jq-able.
Format: subset of OTLP/JSON — https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding


Fail-safe: any error -> drop the bad span, continue; never raise to caller.
"""
import msgspec.json as _json
import sys
import threading
from collections.abc import Sequence
from typing import Any, TextIO
from _core import aclose
try:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
except ImportError:
    ReadableSpan = None
    SpanExporter = object
    SpanExportResult = None
_MAX_STRING = 1024
_MAX_LIST = 32
_MAX_DICT = 32

def _to_otlp_value(v: Any) -> dict[str, Any]:
    """Convert Python value -> OTLP/JSON AnyValue."""
    if v is None:
        return {'stringValue': ''}
    if isinstance(v, bool):
        return {'boolValue': v}
    if isinstance(v, int):
        if v < -2 ** 63 or v > 2 ** 63 - 1:
            v = 0
        return {'intValue': str(v)}
    if isinstance(v, float):
        if v != v:
            return {'doubleValue': 0.0}
        if v in (float('inf'), float('-inf')):
            return {'doubleValue': 0.0}
        return {'doubleValue': v}
    if isinstance(v, str):
        return {'stringValue': v[:_MAX_STRING]}
    if isinstance(v, (list, tuple)):
        return {'arrayValue': {'values': [_to_otlp_value(x) for x in list(v)[:_MAX_LIST]]}}
    if isinstance(v, dict):
        return {'kvlistValue': {'values': [{'key': str(k)[:128], 'value': _to_otlp_value(val)} for k, val in list(v.items())[:_MAX_DICT]]}}
    return {'stringValue': str(v)[:_MAX_STRING]}

def _span_to_otlp(span: Any, max_attrs: int=32) -> dict[str, Any]:
    """Convert a ReadableSpan to OTLP/JSON (span subset)."""
    ctx = span.get_span_context() if hasattr(span, 'get_span_context') else None
    parent = getattr(span, 'parent', None)
    raw_attrs = dict(span.attributes or {})
    truncated = len(raw_attrs) > max_attrs
    if truncated:
        keys = list(raw_attrs.keys())[:max_attrs]
        attrs = {k: raw_attrs[k] for k in keys}
        attrs['_otel.truncated_attrs'] = True
    else:
        attrs = raw_attrs
    otlp_attrs = [{'key': str(k)[:128], 'value': _to_otlp_value(v)} for k, v in attrs.items()]
    status = getattr(span, 'status', None)
    status_code = 'STATUS_CODE_UNSET'
    if status is not None:
        sc = getattr(status, 'status_code', None)
        if sc is not None:
            code_name = type(sc).__name__
            if code_name == 'OK':
                status_code = 'STATUS_CODE_OK'
            elif code_name == 'ERROR':
                status_code = 'STATUS_CODE_ERROR'
    events_out = []
    for ev in getattr(span, 'events', None) or []:
        events_out.append({'timeUnixNano': int(getattr(ev, 'timestamp', 0) or 0), 'name': str(getattr(ev, 'name', ''))[:128], 'attributes': [{'key': str(k)[:128], 'value': _to_otlp_value(v)} for k, v in list((getattr(ev, 'attributes', None) or {}).items())[:max_attrs]]})
    if len(events_out) > 32:
        events_out = events_out[:32]
    return {'traceId': format(ctx.trace_id, '032x') if ctx and ctx.trace_id else '0' * 32, 'spanId': format(ctx.span_id, '016x') if ctx and ctx.span_id else '0' * 16, 'parentSpanId': format(parent.span_id, '016x') if parent is not None and getattr(parent, 'span_id', None) else '', 'name': str(getattr(span, 'name', ''))[:256], 'kind': 'SPAN_KIND_INTERNAL', 'startTimeUnixNano': str(int(getattr(span, 'start_time', 0) or 0)), 'endTimeUnixNano': str(int(getattr(span, 'end_time', 0) or 0)), 'attributes': otlp_attrs, 'status': {'code': status_code}, 'events': events_out}

class StdoutJSONExporter:
    """Writes OTLP/JSON-Lines to a text stream.

    Args:
        stream: TextIO destination. Default sys.stdout.
        max_attrs: Hard cap on attribute count per span. M1 8GB safety.
        flush_each_line: Flush after every line. Default True.
    """
    __slots__ = tuple(('_exported', '_failed', '_flush_each_line', '_lock', '_max_attrs', '_stream'))

    def __init__(self, stream: TextIO | None=None, max_attrs: int=32, flush_each_line: bool=True) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stdout
        self._max_attrs = max(1, min(128, int(max_attrs)))
        self._flush_each_line = bool(flush_each_line)
        self._lock = threading.Lock()
        self._exported = 0
        self._failed = 0

    @property
    def stream(self) -> TextIO:
        return self._stream

    def export(self, spans: Sequence[Any]) -> Any:
        if not spans:
            return SpanExportResult.SUCCESS if SpanExportResult is not None else 0
        with self._lock:
            for sp in spans:
                try:
                    payload = _span_to_otlp(sp, self._max_attrs)
                    line = _json.encode(payload).decode('utf-8')
                    self._stream.write(line + '\n')
                    self._exported += 1
                except Exception:
                    self._failed += 1
                    continue
            if self._flush_each_line:
                try:
                    self._stream.flush()
                except Exception:  # noqa: BLE001
                    pass
        return SpanExportResult.SUCCESS if SpanExportResult is not None else 0

    def shutdown(self) -> None:
        with self._lock:
            try:
                self._stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def force_flush(self, timeout_millis: int=30000) -> bool:
        with self._lock:
            try:
                self._stream.flush()
            except Exception:
                return False
        return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {'exported': self._exported, 'failed': self._failed}