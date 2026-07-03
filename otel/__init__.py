"""
Hledac Universal — OpenTelemetry instrumentation (Sprint T1).

BACKWARD COMPATIBILITY FACADE: Re-exports from original otel modules.

For new code, use: from core.telemetry import init_telemetry, get_tracer, span
"""

# Original otel modules (kept for backward compatibility)
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
