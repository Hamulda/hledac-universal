"""
OpenTelemetry Infrastructure — Issue #23.
============================================

Jediný OTLPSpanExporter → lokální DuckDB analytical store (otel_spans table).

Span schema:
    trace_id, span_id, parent_span_id, name, status, status_message,
    start_time_ms, end_time_ms, duration_ms,
    attributes_json, resource_json

Indexy: trace_id, name, status, start_time_ms

Query example:
    SELECT AVG(duration_ms) FROM otel_spans WHERE name = 'fetch';

DuckDB analytical queries:
    - AVG/PERCENTILE duration per span name
    - throughput: COUNT(*) / time window
    - error rate: SUM(status != 'OK') / COUNT(*)
"""
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from opentelemetry.sdk.trace import Span, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.common import Resource
from opentelemetry.trace import Status, StatusCode
if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
__all__ = ['DuckDBSpanExporter', 'create_otel_spans_table', 'QueryBuilder']
_SPAN_COLUMNS = ['trace_id', 'span_id', 'parent_span_id', 'name', 'status', 'status_message', 'start_time_ms', 'end_time_ms', 'duration_ms', 'attributes_json', 'resource_json']

@dataclass(slots=True)
class SpanRecord:
    """Serializovaný OTel span záznam."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    status: str
    status_message: str
    start_time_ms: int
    end_time_ms: int
    duration_ms: float
    attributes_json: str
    resource_json: str
_OTEL_SPANS_CREATE_SQL = "\nCREATE TABLE IF NOT EXISTS otel_spans (\n    trace_id       TEXT NOT NULL,\n    span_id        TEXT NOT NULL,\n    parent_span_id TEXT,\n    name           TEXT NOT NULL,\n    status         TEXT NOT NULL,\n    status_message TEXT NOT NULL DEFAULT '',\n    start_time_ms  BIGINT NOT NULL,\n    end_time_ms    BIGINT NOT NULL,\n    duration_ms    DOUBLE NOT NULL,\n    attributes_json TEXT NOT NULL DEFAULT '{}',\n    resource_json  TEXT NOT NULL DEFAULT '{}'\n);\n"
_OTEL_SPANS_INDEXES = ['CREATE INDEX IF NOT EXISTS idx_otel_spans_trace_id ON otel_spans(trace_id);', 'CREATE INDEX IF NOT EXISTS idx_otel_spans_name ON otel_spans(name);', 'CREATE INDEX IF NOT EXISTS idx_otel_spans_status ON otel_spans(status);', 'CREATE INDEX IF NOT EXISTS idx_otel_spans_start ON otel_spans(start_time_ms);']

def create_otel_spans_table(conn: Any) -> None:
    """Vytvoří otel_spans tabulku + indexy v DuckDB connection."""
    cursor = conn.cursor()
    cursor.execute(_OTEL_SPANS_CREATE_SQL)
    for idx_sql in _OTEL_SPANS_INDEXES:
        cursor.execute(idx_sql)
    cursor.close()

class DuckDBSpanExporter(SpanExporter):
    """
    OTel SpanExporter → DuckDB analytical store.

    M1 8GB safe: batched writes, max 500 spans per batch, background thread.

    Always-on, fail-soft: pokud DuckDB write selže, span je zahozen
    (telemetry miss ≠ runtime failure).
    """
    __slots__ = ('_conn', '_batch', '_lock', '_shutdown', '_worker', '_max_batch_size')

    def __init__(self, conn: Any, *, max_batch_size: int=500) -> None:
        """
        Args:
            conn: DuckDB connection (duckdb.connect() nebo Connection)
            max_batch_size: Max spanů v jednom batch insert (default 500)
        """
        self._conn = conn
        self._batch: list[SpanRecord] = []
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None
        self._max_batch_size = max_batch_size

    def start(self) -> 'DuckDBSpanExporter':
        """Spustí background flush worker."""
        self._worker = threading.Thread(target=self._flush_loop, daemon=True)
        self._worker.start()
        return self

    def _flush_loop(self) -> None:
        """Background loop — flush batch každých 1s."""
        import time as _time
        while not self._shutdown.wait(1.0):
            with self._lock:
                if self._batch:
                    self._flush_batch_locked()

    def _flush_batch_locked(self) -> None:
        """Flush při drženém locku."""
        if not self._batch:
            return
        batch = self._batch[:]
        self._batch.clear()
        try:
            self._write_batch(batch)
        except Exception:
            pass

    def _write_batch(self, batch: list[SpanRecord]) -> None:
        """Batch insert do DuckDB přes executemany."""
        if not batch:
            return
        cols = ', '.join(_SPAN_COLUMNS)
        placeholders = ', '.join(['?'] * len(_SPAN_COLUMNS))
        sql = f'INSERT INTO otel_spans ({cols}) VALUES ({placeholders})'
        cursor = self._conn.cursor()
        records = [(r.trace_id, r.span_id, r.parent_span_id, r.name, r.status, r.status_message, r.start_time_ms, r.end_time_ms, r.duration_ms, r.attributes_json, r.resource_json) for r in batch]
        cursor.executemany(sql, records)
        cursor.close()

    def export(self, spans: list[Span]) -> None:
        """OTel SDK volá tuto metodu při každém dokončeném spanu."""
        if not spans:
            return
        with self._lock:
            for span in spans:
                self._batch.append(self._span_to_record(span))
            if len(self._batch) >= self._max_batch_size:
                self._flush_batch_locked()

    def _span_to_record(self, span: Span) -> SpanRecord:
        """Převede OTel Span na SpanRecord."""
        ctx = span.get_span_context()
        trace_id = format(ctx.trace_id, '032x')
        span_id = format(ctx.span_id, '016x')
        parent_id: str | None = None
        if span.parent:
            parent_id = format(span.parent.span_id, '016x')
        status_obj: Status = span.status
        if status_obj.status_code == StatusCode.OK:
            status = 'OK'
        elif status_obj.status_code == StatusCode.ERROR:
            status = 'ERROR'
        else:
            status = 'UNSET'
        start_ns = span.start_time
        end_ns = span.end_time
        duration_ms = (end_ns - start_ns) / 1000000.0
        attributes_json = json.dumps(dict(span.attributes) if span.attributes else {})
        resource_json = json.dumps({k: str(v) for k, v in span.resource.attributes.items()} if span.resource and span.resource.attributes else {})
        return SpanRecord(trace_id=trace_id, span_id=span_id, parent_span_id=parent_id, name=span.name or '', status=status, status_message=status_obj.description or '', start_time_ms=int(start_ns / 1000000), end_time_ms=int(end_ns / 1000000), duration_ms=duration_ms, attributes_json=attributes_json, resource_json=resource_json)

    def force_flush(self) -> None:
        """Synchroní flush — čeká na dokončení."""
        with self._lock:
            self._flush_batch_locked()

    def shutdown(self, timeout_ms: float=5000) -> None:
        """Ukončí background worker."""
        self._shutdown.set()
        if self._worker:
            self._worker.join(timeout_ms / 1000)
        self.force_flush()

class QueryBuilder:
    """
    Analytical queries proti otel_spans tabulce.

    Usage:
        qb = QueryBuilder(duckdb_conn)
        avg_fetch = qb.avg_duration(name="fetch")
        error_rate = qb.error_rate(since_hours=24)
    """
    __slots__ = ('_conn',)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def avg_duration(self, name: str) -> float | None:
        """SELECT AVG(duration_ms) FROM otel_spans WHERE name = ?"""
        try:
            result = self._conn.execute('SELECT AVG(duration_ms) FROM otel_spans WHERE name = ?', (name,)).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def p50_duration(self, name: str) -> float | None:
        """Percentile 50 (median) duration."""
        try:
            result = self._conn.execute('SELECT quantile_cont(duration_ms, 0.5) FROM otel_spans WHERE name = ?', (name,)).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def p99_duration(self, name: str) -> float | None:
        """Percentile 99 duration."""
        try:
            result = self._conn.execute('SELECT quantile_cont(duration_ms, 0.99) FROM otel_spans WHERE name = ?', (name,)).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def error_rate(self, *, since_hours: int=24) -> float | None:
        """Chybovost za posledních N hodin."""
        try:
            cutoff_ms = int((datetime.now(timezone.utc).timestamp() - since_hours * 3600) * 1000)
            total = self._conn.execute('SELECT COUNT(*) FROM otel_spans WHERE start_time_ms >= ?', (cutoff_ms,)).fetchone()[0]
            if not total:
                return None
            errors = self._conn.execute("SELECT COUNT(*) FROM otel_spans WHERE start_time_ms >= ? AND status = 'ERROR'", (cutoff_ms,)).fetchone()[0]
            return float(errors) / float(total)
        except Exception:
            return None

    def throughput(self, name: str, *, since_hours: int=1) -> float | None:
        """Počet spanů za hodinu."""
        try:
            cutoff_ms = int((datetime.now(timezone.utc).timestamp() - since_hours * 3600) * 1000)
            count = self._conn.execute('SELECT COUNT(*) FROM otel_spans WHERE name = ? AND start_time_ms >= ?', (name, cutoff_ms)).fetchone()[0]
            return float(count) / float(since_hours) if count else 0.0
        except Exception:
            return None

    def span_count(self, name: str | None=None) -> int:
        """Celkový počet spanů, volitelně filtrováno podle name."""
        try:
            if name:
                result = self._conn.execute('SELECT COUNT(*) FROM otel_spans WHERE name = ?', (name,)).fetchone()
            else:
                result = self._conn.execute('SELECT COUNT(*) FROM otel_spans').fetchone()
            return int(result[0]) if result else 0
        except Exception:
            return 0