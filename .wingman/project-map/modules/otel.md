# OpenTelemetry Integration

## Metadata

- **Entry Path:** modules/otel
- **Status:** current
- **Source:** otel/
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

OpenTelemetry instrumentation for tracing, metrics, and DuckDB-based span export.

## Source Paths

- `otel/`
- `otel/_duckdb_exporter.py`

## Components

| Component | Purpose |
|-----------|---------|
| `DuckDBSpanExporter` | Batch export spans to DuckDB |
| `_flush_loop` | Background flush every 1s |
| `_flush_batch_locked` | Thread-safe batch writes |

## Batch Constraints

- Max 500 spans per batch
- Batched writes to DuckDB
- Background thread for flush loop
- Fail-soft: telemetry miss ≠ crash

## Usage

```python
from hledac.universal.otel._duckdb_exporter import DuckDBSpanExporter

exporter = DuckDBSpanExporter(conn, max_batch_size=500)
exporter.start()
```

## Lazy Import Pattern

```python
# WRONG (7µs cold-start):
try:
    from otel import instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented

# CORRECT (zero-cost until first use):
from hledac.universal.utils.optional_imports import optional
_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))
```

## Related Entries

- modules/duckdb-shadow-store
- modules/monitoring-coordinator
