"""
DuckDB Span Exporter — Issue #23.
==================================




OTLPSpanExporter → lokální DuckDB analytical store (otel_spans table).

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

import hashlib
import threading  # DuckDBSpanExporter background flush thread
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry.sdk.trace import Span
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

# WIRING_COMPLETE I2: Lock-free Rust counters for hot-path telemetry
try:
    from hledac_rust_extensions import hledac_rust_extensions as _rust_ext

    _RUST_AVAILABLE = hasattr(_rust_ext, "create_counter")
    if _RUST_AVAILABLE:
        _SAMPLER_TOTAL = _rust_ext.create_counter("otel_sampler_total")
        _SAMPLER_FILTERED = _rust_ext.create_counter("otel_sampler_filtered")
        _SAMPLER_EXPORTED = _rust_ext.create_counter("otel_sampler_exported")
        _DUCKDB_EXPORTER_BATCH_SIZE = _rust_ext.create_histogram("otel_duckdb_batch_size")
    else:
        _RUST_AVAILABLE = False
        _SAMPLER_TOTAL = None
        _SAMPLER_FILTERED = None
        _SAMPLER_EXPORTED = None
        _DUCKDB_EXPORTER_BATCH_SIZE = None
except ImportError:
    _RUST_AVAILABLE = False
    _SAMPLER_TOTAL = None
    _SAMPLER_FILTERED = None
    _SAMPLER_EXPORTED = None
    _DUCKDB_EXPORTER_BATCH_SIZE = None

# orjson fallback — 5-10× faster than stdlib json, M1 optimized
try:
    import orjson

    def _json_dumps(data: Any) -> str:
        return orjson.dumps(data).decode("utf-8")

except ImportError:
    import json as _stdlib_json

    def _json_dumps(data: Any) -> str:
        return _stdlib_json.dumps(data)


if TYPE_CHECKING:
    pass

__all__ = [
    "DuckDBSpanExporter",
    "SamplingSpanProcessor",
    "create_otel_spans_table",
    "QueryBuilder",
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_SPAN_COLUMNS = [
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "status",
    "status_message",
    "start_time_ms",
    "end_time_ms",
    "duration_ms",
    "attributes_json",
    "resource_json",
]

_OTEL_SPANS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS otel_spans (
    trace_id       TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    parent_span_id TEXT,
    name           TEXT NOT NULL,
    status         TEXT NOT NULL,
    status_message TEXT NOT NULL DEFAULT '',
    start_time_ms  BIGINT NOT NULL,
    end_time_ms    BIGINT NOT NULL,
    duration_ms    DOUBLE NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    resource_json  TEXT NOT NULL DEFAULT '{}'
);
"""

_OTEL_SPANS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_otel_spans_trace_id ON otel_spans(trace_id);",
    "CREATE INDEX IF NOT EXISTS idx_otel_spans_name ON otel_spans(name);",
    "CREATE INDEX IF NOT EXISTS idx_otel_spans_status ON otel_spans(status);",
    "CREATE INDEX IF NOT EXISTS idx_otel_spans_start ON otel_spans(start_time_ms);",
]


def create_otel_spans_table(conn: Any) -> None:
    """Vytvoří otel_spans tabulku + indexy v DuckDB connection."""
    cursor = conn.cursor()
    cursor.execute(_OTEL_SPANS_CREATE_SQL)
    for idx_sql in _OTEL_SPANS_INDEXES:
        cursor.execute(idx_sql)
    cursor.close()


# --------------------------------------------------------------------------- #
# SamplingSpanProcessor — TEL-01: Filter high-frequency spans before export
# --------------------------------------------------------------------------- #


class SamplingSpanProcessor:
    """
    SpanProcessor wrapper that applies sampling rules before passing spans downstream.

    M1 8GB safe: reduces DuckDB write volume by filtering high-frequency spans.

    Always-on, fail-soft: on any error, passes spans through unchanged.

    Sampling rules:
      1. ERROR spans → always export
      2. Slow spans (>slow_span_threshold_ms) → always export (performance signal)
      3. High-frequency prefixes (configurable via HLEDAC_OTEL_SKIP_PREFIXES) →
         trace-id-ratio sampling at HLEDAC_OTEL_SPAN_SAMPLE_RATE (default 0.1 = 10%)
      4. All other spans → export

    Telemetry metrics:
      - _total_count: all spans seen by this processor
      - _filtered_count: spans dropped by sampling
      - _exported_count: spans passed through

    Usage:
        processor = SamplingSpanProcessor(
            next_processor=BatchSpanProcessor(DuckDBSpanExporter(...)),
            sample_rate=0.1,
    )
        provider.add_span_processor(processor)
    """

    __slots__ = (
        "_next",
        "_sample_rate",
        "_skip_prefixes",
        "_slow_span_threshold_ms",
    )

    def __init__(
        self,
        next_processor: Any,
        *,
        sample_rate: float = 0.1,
        slow_span_threshold_ms: float = 100.0,
        skip_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        self._next = next_processor
        self._sample_rate = sample_rate
        self._slow_span_threshold_ms = slow_span_threshold_ms
        if skip_prefixes is not None:
            self._skip_prefixes = skip_prefixes
        else:
            self._skip_prefixes = (
                "fetch.",
                "http.",
                "db.",
                "lmdb.",
                "cache.",
                "duckdb.",
            )

    def on_start(self, span: Span) -> None:
        self._next.on_start(span)

    def on_end(self, span: Span) -> None:
        """Called by OTel SDK when a span ends. Apply sampling rules."""
        # WIRING_COMPLETE I2: Lock-free counter increment (no lock needed)
        if _SAMPLER_TOTAL is not None:
            _SAMPLER_TOTAL.inc()
        try:
            if self._should_export(span):
                self._next.on_end(span)
                # WIRING_COMPLETE I2: Lock-free counter increment (no lock needed)
                if _SAMPLER_EXPORTED is not None:
                    _SAMPLER_EXPORTED.inc()
            else:
                # WIRING_COMPLETE I2: Lock-free counter increment (no lock needed)
                if _SAMPLER_FILTERED is not None:
                    _SAMPLER_FILTERED.inc()
        except Exception:
            # Fail-soft: pass through on error
            try:
                self._next.on_end(span)
                # WIRING_COMPLETE I2: Lock-free counter increment (no lock needed)
                if _SAMPLER_EXPORTED is not None:
                    _SAMPLER_EXPORTED.inc()
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self, timeout_ms: float = 5_000) -> None:
        if hasattr(self._next, "shutdown"):
            self._next.shutdown(timeout_ms)

    def force_flush(self, timeout_ms: float = 5_000) -> None:
        if hasattr(self._next, "force_flush"):
            self._next.force_flush(timeout_ms)

    def _should_export(self, span: Span) -> bool:
        """Determine if span should be exported based on sampling rules."""
        # Rule 1: ERROR spans always export
        status: Status = span.status
        if status.status_code == StatusCode.ERROR:
            return True

        # Rule 2: Slow spans (>{slow_span_threshold_ms}ms) always export
        start_ns = span.start_time or 0
        end_ns = span.end_time or 0
        duration_ms = (end_ns - start_ns) / 1_000_000.0
        if duration_ms > self._slow_span_threshold_ms:
            return True

        # Rule 3: High-frequency prefix → trace-id-ratio sampling
        span_name = span.name or ""
        if span_name.startswith(self._skip_prefixes):
            return self._trace_id_sampled(span)

        # Rule 4: All other spans export
        return True

    def _trace_id_sampled(self, span: Span) -> bool:
        """Deterministic trace-id-ratio sampling for span consistency."""
        try:
            ctx = span.get_span_context()
            trace_id_bytes = ctx.trace_id.to_bytes(16, "big")
            name_bytes = (span.name or "").encode("utf-8")
            hash_input = trace_id_bytes + name_bytes
            hash_val = int.from_bytes(hashlib.sha256(hash_input).digest()[:8], "big")
            return (hash_val % 1000) < (self._sample_rate * 1000)
        except Exception:
            return True  # Fail-safe: export on error

    @property
    def stats(self) -> dict[str, int]:
        """Return sampling statistics (WIRING_COMPLETE I2: reads from Rust counters)."""
        if _RUST_AVAILABLE and _SAMPLER_TOTAL is not None:
            total, _ = _SAMPLER_TOTAL.get()
            filtered, _ = _SAMPLER_FILTERED.get() if _SAMPLER_FILTERED else (0, 0)
            exported, _ = _SAMPLER_EXPORTED.get() if _SAMPLER_EXPORTED else (0, 0)
            return {
                "total": total,
                "filtered": filtered,
                "exported": exported,
            }
        return {"total": 0, "filtered": 0, "exported": 0}


# --------------------------------------------------------------------------- #
# DuckDBSpanExporter
# --------------------------------------------------------------------------- #


class DuckDBSpanExporter(SpanExporter):
    """
    OTel SpanExporter → DuckDB analytical store.

    M1 8GB safe: batched writes, max 500 spans per batch, background thread.

    Always-on, fail-soft: pokud DuckDB write selže, span je zahozen
    (telemetry miss ≠ runtime failure).
    """

    __slots__ = (
        "_conn",
        "_batch",
        "_lock",
        "_shutdown",
        "_worker",
        "_max_batch_size",
    )

    def __init__(self, conn: Any, *, max_batch_size: int = 500) -> None:
        self._conn = conn
        self._batch: list[dict] = []
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None
        self._max_batch_size = max_batch_size

    def start(self) -> DuckDBSpanExporter:
        """Spustí background flush worker."""
        self._worker = threading.Thread(target=self._flush_loop, daemon=True)
        self._worker.start()
        return self

    def _flush_loop(self) -> None:
        """Background loop — flush batch každých 1s."""

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
        except Exception:  # noqa: BLE001
            pass  # fail-soft: telemetry miss ≠ crash

    def _write_batch(self, batch: list[dict]) -> None:
        """Batch insert do DuckDB přes executemany."""
        if not batch:
            return
        cols = ", ".join(_SPAN_COLUMNS)
        placeholders = ", ".join(["?"] * len(_SPAN_COLUMNS))
        sql = f"INSERT INTO otel_spans ({cols}) VALUES ({placeholders})"
        cursor = self._conn.cursor()
        records = [
            (
                r["trace_id"],
                r["span_id"],
                r["parent_span_id"],
                r["name"],
                r["status"],
                r["status_message"],
                r["start_time_ms"],
                r["end_time_ms"],
                r["duration_ms"],
                r["attributes_json"],
                r["resource_json"],
            )
            for r in batch
        ]
        cursor.executemany(sql, records)
        cursor.close()

    def export(self, spans: Any) -> Any:
        """OTel SDK volá tuto metodu při každém dokončeném spanu."""
        if not spans:
            return SpanExportResult.SUCCESS if SpanExportResult else 0
        try:
            with self._lock:
                for span in spans:
                    self._batch.append(self._span_to_record(span))
                if len(self._batch) >= self._max_batch_size:
                    self._flush_batch_locked()
            return SpanExportResult.SUCCESS if SpanExportResult else 0
        except Exception:
            return SpanExportResult.FAILURE if SpanExportResult else 1

    def _span_to_record(self, span: Span) -> dict:
        """Převede OTel Span na dict záznam."""
        ctx = span.get_span_context()
        trace_id = format(ctx.trace_id, "032x")
        span_id = format(ctx.span_id, "016x")

        parent_id: str | None = None
        if span.parent:
            parent_id = format(span.parent.span_id, "016x")

        status_obj: Status = span.status
        if status_obj.status_code == StatusCode.OK:
            status = "OK"
        elif status_obj.status_code == StatusCode.ERROR:
            status = "ERROR"
        else:
            status = "UNSET"

        start_ns = span.start_time or 0
        end_ns = span.end_time or 0
        duration_ms = (end_ns - start_ns) / 1_000_000.0  # type: ignore[operator]

        attributes_json = _json_dumps(dict(span.attributes) if span.attributes else {})
        resource_json = _json_dumps(
            {k: str(v) for k, v in span.resource.attributes.items()}
            if span.resource and span.resource.attributes
            else {}
        )

        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_id,
            "name": span.name or "",
            "status": status,
            "status_message": status_obj.description or "",
            "start_time_ms": int(start_ns / 1_000_000),  # type: ignore[operator]
            "end_time_ms": int(end_ns / 1_000_000),  # type: ignore[operator]
            "duration_ms": duration_ms,
            "attributes_json": attributes_json,
            "resource_json": resource_json,
        }

    def _force_flush_batch(self) -> None:
        """Synchroní flush — čeká na dokončení."""
        with self._lock:
            self._flush_batch_locked()

    def shutdown(self, timeout_ms: float = 5_000) -> None:
        """Ukončí background worker."""
        self._shutdown.set()
        if self._worker:
            self._worker.join(timeout_ms / 1000)
        self._flush_batch_locked()


# --------------------------------------------------------------------------- #
# QueryBuilder — Analytical Queries
# --------------------------------------------------------------------------- #


class QueryBuilder:
    """
    Analytical queries proti otel_spans tabulce.

    Usage:
        qb = QueryBuilder(duckdb_conn)
        avg_fetch = qb.avg_duration(name="fetch")
        error_rate = qb.error_rate(since_hours=24)
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def avg_duration(self, name: str) -> float | None:
        """SELECT AVG(duration_ms) FROM otel_spans WHERE name = ?"""
        try:
            result = self._conn.execute(
                "SELECT AVG(duration_ms) FROM otel_spans WHERE name = ?",
                (name,),
            ).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def p50_duration(self, name: str) -> float | None:
        """Percentile 50 (median) duration."""
        try:
            result = self._conn.execute(
                "SELECT quantile_cont(duration_ms, 0.5) FROM otel_spans WHERE name = ?",
                (name,),
            ).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def p99_duration(self, name: str) -> float | None:
        """Percentile 99 duration."""
        try:
            result = self._conn.execute(
                "SELECT quantile_cont(duration_ms, 0.99) FROM otel_spans WHERE name = ?",
                (name,),
            ).fetchone()
            return float(result[0]) if result and result[0] is not None else None
        except Exception:
            return None

    def error_rate(self, *, since_hours: int = 24) -> float | None:
        """Chybovost za posledních N hodin."""
        try:
            cutoff_ms = int((datetime.now(UTC).timestamp() - since_hours * 3600) * 1000)
            total = self._conn.execute(
                "SELECT COUNT(*) FROM otel_spans WHERE start_time_ms >= ?",
                (cutoff_ms,),
            ).fetchone()[0]
            if not total:
                return None
            errors = self._conn.execute(
                "SELECT COUNT(*) FROM otel_spans WHERE start_time_ms >= ? AND status = 'ERROR'",
                (cutoff_ms,),
            ).fetchone()[0]
            return float(errors) / float(total)
        except Exception:
            return None

    def throughput(self, name: str, *, since_hours: int = 1) -> float | None:
        """Počet spanů za hodinu."""
        try:
            cutoff_ms = int((datetime.now(UTC).timestamp() - since_hours * 3600) * 1000)
            count = self._conn.execute(
                "SELECT COUNT(*) FROM otel_spans WHERE name = ? AND start_time_ms >= ?",
                (name, cutoff_ms),
            ).fetchone()[0]
            return float(count) / float(since_hours) if count else 0.0
        except Exception:
            return None

    def span_count(self, name: str | None = None) -> int:
        """Celkový počet spanů, volitelně filtrováno podle name."""
        try:
            if name:
                result = self._conn.execute("SELECT COUNT(*) FROM otel_spans WHERE name = ?", (name,)).fetchone()
            else:
                result = self._conn.execute("SELECT COUNT(*) FROM otel_spans").fetchone()
            return int(result[0]) if result else 0
        except Exception:
            return 0
