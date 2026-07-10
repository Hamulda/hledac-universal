"""
Sprint K14-FIX: telemetry shim — re-export z local otel/ module.

Fallback pro brain/deephermes3_engine.py, coordinators/fetch_coordinator.py,
core/__main__.py které mají:
    from hledac.universal.telemetry import instrumented
(jako fallback kdyby local otel/ modul nebyl dostupný).

Protoze local otel/ JEEE dostupny, jednoduse re-exportuj jeho symboly.
"""

from otel import (
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
