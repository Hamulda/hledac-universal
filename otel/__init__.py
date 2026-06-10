"""
Hledac Universal — OpenTelemetry instrumentation (Sprint T1).

Always-on, bounded, fail-safe distributed tracing.
Default exporter: JSON-Lines to stdout (greppable, jq-able).
Opt-in: OTLP/HTTP to HLEDAC_OTEL_ENDPOINT (default http://localhost:4318).

M1 8GB safe:
  - ~25 MB resident (opentelemetry-api + sdk)
  - Bounded span buffer: 4096 spans, FIFO eviction
  - Bounded export queue: 2048, batch 64, schedule 2s
  - Lazy OTel imports with NoOp fallback
  - Manual span wrapping only (no asyncio auto-instrumentation)

Usage:
    from otel import init_telemetry, span, instrumented

    init_telemetry()                         # safe to call multiple times

    with span("sprint.run", sprint_id=id):   # context manager
        ...

    @instrumented("acquisition.fetch")        # decorator
    async def fetch(self, url): ...
"""

from __future__ import annotations

from otel._instrumentation import (
    add_event,
    current_span_id,
    current_trace_id,
    get_tracer,
    instrumented,
    record_exception,
    set_attribute,
    set_status,
    span,
)
from otel._setup import (
    TelemetryConfig,
    get_config,
    init_telemetry,
    is_initialized,
    shutdown_telemetry,
)

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
]
