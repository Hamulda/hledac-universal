"""
Hledac Universal — OpenTelemetry instrumentation (Sprint T1).

BACKWARD COMPATIBILITY FACADE: Re-exports from original otel modules.

For new code, use: from _core.telemetry import init_telemetry, get_tracer, span

Fail-safe: all OTel imports are lazy. If opentelemetry-* packages are not
installed, importing this module logs a warning to stderr and exports no-op
stubs so that the rest of the application boots unchanged.
"""
from __future__ import annotations

import sys
from typing import Any
from _core import aclose

__all__ = [
    "span",
    "instrumented",
    "get_tracer",
    "add_event",
    "set_attribute",
    "set_status",
    "record_exception",
    "current_trace_id",
    "current_span_id",
    "init_telemetry",
    "shutdown_telemetry",
    "is_initialized",
    "get_config",
    "TelemetryConfig",
    # Issue #23
    "DuckDBSpanExporter",
    "QueryBuilder",
    "create_otel_spans_table",
    # E4: async context propagation
    "current_otel_context",
    "create_task_with_context",
]

# ── Helper ──────────────────────────────────────────────────────────────────

def _lazy_import_otel_attr(
    module_path: str,
    attr: str,
    fallback: Any = None,
) -> Any:
    """Try importing attr from module_path; return fallback and warn on ImportError."""
    try:
        from importlib import import_module
        mod = import_module(module_path)
        return getattr(mod, attr)
    except ImportError:
        sys.stderr.write(
            f"[telemetry] opentelemetry not installed; {attr} unavailable\n"
    )
        return fallback


def _lazy_import_hledac_otel_attr(
    module_path: str,
    attr: str,
    fallback: Any = None,
) -> Any:
    """Try importing attr from a hledac.otel submodule; return fallback and warn."""
    try:
        from importlib import import_module
        mod = import_module(module_path)
        return getattr(mod, attr)
    except ImportError:
        sys.stderr.write(
            f"[telemetry] {module_path} unavailable; {attr} unavailable\n"
    )
        return fallback


# ── Issue #23: DuckDB span exporter ────────────────────────────────────────

DuckDBSpanExporter = _lazy_import_hledac_otel_attr(
    "hledac.universal.otel._duckdb_exporter", "DuckDBSpanExporter"
    )
QueryBuilder = _lazy_import_hledac_otel_attr(
    "hledac.universal.otel._duckdb_exporter", "QueryBuilder"
    )
create_otel_spans_table = _lazy_import_hledac_otel_attr(
    "hledac.universal.otel._duckdb_exporter", "create_otel_spans_table"
    )

# ── _instrumentation ────────────────────────────────────────────────────────

add_event = _lazy_import_otel_attr("otel._instrumentation", "add_event")
current_span_id = _lazy_import_otel_attr("otel._instrumentation", "current_span_id")
current_trace_id = _lazy_import_otel_attr("otel._instrumentation", "current_trace_id")
get_tracer = _lazy_import_otel_attr("otel._instrumentation", "get_tracer")
instrumented = _lazy_import_otel_attr("otel._instrumentation", "instrumented")
record_exception = _lazy_import_otel_attr("otel._instrumentation", "record_exception")
set_attribute = _lazy_import_otel_attr("otel._instrumentation", "set_attribute")
set_status = _lazy_import_otel_attr("otel._instrumentation", "set_status")
span = _lazy_import_otel_attr("otel._instrumentation", "span")

# ── _instrumentation_asyncio ────────────────────────────────────────────────

create_task_with_context = _lazy_import_otel_attr(
    "otel._instrumentation_asyncio", "create_task_with_context"
    )
current_otel_context = _lazy_import_otel_attr(
    "otel._instrumentation_asyncio", "current_otel_context"
    )

# ── _setup ───────────────────────────────────────────────────────────────────

TelemetryConfig = _lazy_import_otel_attr("otel._setup", "TelemetryConfig")
get_config = _lazy_import_otel_attr("otel._setup", "get_config")
init_telemetry = _lazy_import_otel_attr("otel._setup", "init_telemetry")
is_initialized = _lazy_import_otel_attr("otel._setup", "is_initialized")
shutdown_telemetry = _lazy_import_otel_attr("otel._setup", "shutdown_telemetry")
