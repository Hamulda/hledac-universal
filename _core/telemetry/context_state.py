"""
Context state via ContextVar — TaskGroup child task visibility.

Issue 8.4: Sprint phase and stealth state must be visible to all

TaskGroup child tasks without passing explicit parameters.

Issue #046: Extended with:
- _current_sprint_id: ContextVar[str] — per-sprint correlation
- _request_id: ContextVar[str] — per-fetch correlation
- _lane_metrics: ContextVar[dict] — for MLXUnifiedScheduler lane telemetry

Issue O-01: Unified TelemetryContext — aggregates all ContextVars into one
immutable struct, initialized once per sprint session in composition_root.
structlog bind_contextvars is called here so all child tasks inherit
the full sprint context automatically.

structlog integrace: structlog.contextvars.merge_contextvars processor
(viz runtime/_telemetry_setup.py) automaticky začleňuje všechny
ContextVar do log outputu — žádná další konfigurace není potřeba.
"""


import contextvars
import uuid
from typing import Any
import msgspec
from compat.msgspec_gc_compat import Struct
from _core._util import aclose


# ─── TelemetryContext (O-01) ─────────────────────────────────────────────────

class TelemetryContext(Struct, frozen=True, eq=False):
    """
    Unified telemetry context — initialized once per sprint session.

    O-01: Aggregates all ContextVars into one immutable struct.
    Passed to composition_root.build_runtime() at sprint start.
    Automatically propagated to all child tasks via safe_create_task.

    All fields are read-only — use update_* methods to create new instances.
    """

    sprint_id: str = ""
    sprint_phase: str = ""
    stealth_enabled: bool = False
    request_id: str = ""
    lane: str = "none"
    lane_latency_ms: float = 0.0
    lane_queue_depth: int = 0
    trace_id: str = ""
    span_id: str = ""

    def with_phase(self, phase: str) -> "TelemetryContext":
        """Return new instance with updated sprint_phase."""
        return TelemetryContext(
            sprint_id=self.sprint_id,
            sprint_phase=phase,
            stealth_enabled=self.stealth_enabled,
            request_id=self.request_id,
            lane=self.lane,
            lane_latency_ms=self.lane_latency_ms,
            lane_queue_depth=self.lane_queue_depth,
            trace_id=self.trace_id,
            span_id=self.span_id,
    )

    def with_sprint_id(self, sprint_id: str) -> "TelemetryContext":
        """Return new instance with updated sprint_id."""
        return TelemetryContext(
            sprint_id=sprint_id,
            sprint_phase=self.sprint_phase,
            stealth_enabled=self.stealth_enabled,
            request_id=self.request_id,
            lane=self.lane,
            lane_latency_ms=self.lane_latency_ms,
            lane_queue_depth=self.lane_queue_depth,
            trace_id=self.trace_id,
            span_id=self.span_id,
    )

    def with_request_id(self, request_id: str) -> "TelemetryContext":
        """Return new instance with updated request_id."""
        return TelemetryContext(
            sprint_id=self.sprint_id,
            sprint_phase=self.sprint_phase,
            stealth_enabled=self.stealth_enabled,
            request_id=request_id,
            lane=self.lane,
            lane_latency_ms=self.lane_latency_ms,
            lane_queue_depth=self.lane_queue_depth,
            trace_id=self.trace_id,
            span_id=self.span_id,
    )

    def with_lane(self, lane: str, latency_ms: float = 0.0, queue_depth: int = 0) -> "TelemetryContext":
        """Return new instance with updated lane metrics."""
        return TelemetryContext(
            sprint_id=self.sprint_id,
            sprint_phase=self.sprint_phase,
            stealth_enabled=self.stealth_enabled,
            request_id=self.request_id,
            lane=lane,
            lane_latency_ms=latency_ms,
            lane_queue_depth=queue_depth,
            trace_id=self.trace_id,
            span_id=self.span_id,
    )

    def with_trace(self, trace_id: str, span_id: str) -> "TelemetryContext":
        """Return new instance with updated OTel trace context."""
        return TelemetryContext(
            sprint_id=self.sprint_id,
            sprint_phase=self.sprint_phase,
            stealth_enabled=self.stealth_enabled,
            request_id=self.request_id,
            lane=self.lane,
            lane_latency_ms=self.lane_latency_ms,
            lane_queue_depth=self.lane_queue_depth,
            trace_id=trace_id,
            span_id=span_id,
    )


# Module-level singleton — initialized in init_telemetry_context()
_TELEMETRY_CONTEXT: TelemetryContext = TelemetryContext()
_CONTEXT_TOKEN: contextvars.Token[TelemetryContext] | None = None
_telemetry_context_var: contextvars.ContextVar[TelemetryContext] = contextvars.ContextVar(
    "_telemetry_context", default=TelemetryContext()
    )


def init_telemetry_context(
    sprint_id: str | None = None,
    trace_id: str = "",
    span_id: str = "",
) -> TelemetryContext:
    """
    Initialize the unified TelemetryContext for the current sprint session.

    O-01: Call once at sprint start from composition_root.build_runtime().
    All child tasks inherit this context automatically via safe_create_task
    OTel propagation + structlog bind_contextvars.

    Args:
        sprint_id: Optional sprint ID (generated if None)
        trace_id: OTel trace ID for the parent span
        span_id: OTel span ID for the parent span

    Returns:
        The initialized TelemetryContext
    """
    global _TELEMETRY_CONTEXT, _CONTEXT_TOKEN

    sid = sprint_id if sprint_id else uuid.uuid8().hex[:16]
    _TELEMETRY_CONTEXT = TelemetryContext(
        sprint_id=sid,
        trace_id=trace_id,
        span_id=span_id,
    )
    _CONTEXT_TOKEN = _telemetry_context_var.set(_TELEMETRY_CONTEXT)

    # Bind to structlog so all log calls include these fields
    _bind_telemetry_to_structlog(_TELEMETRY_CONTEXT)

    return _TELEMETRY_CONTEXT


def get_telemetry_context() -> TelemetryContext:
    """Get the current TelemetryContext (may be default/empty if not initialized)."""
    return _telemetry_context_var.get()


def reset_telemetry_context() -> None:
    """Reset TelemetryContext to empty — call at sprint end."""
    global _TELEMETRY_CONTEXT, _CONTEXT_TOKEN
    _telemetry_context_var.set(TelemetryContext())
    _TELEMETRY_CONTEXT = TelemetryContext()
    _CONTEXT_TOKEN = None


def _bind_telemetry_to_structlog(ctx: TelemetryContext) -> None:
    """Bind TelemetryContext fields to structlog contextvars."""
    try:
        import structlog
        structlog.contextvars.bind_contextvars(
            sprint_id=ctx.sprint_id,
            sprint_phase=ctx.sprint_phase,
            stealth_enabled=str(ctx.stealth_enabled),
            request_id=ctx.request_id,
            lane=ctx.lane,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
    )
    except Exception:  # noqa: BLE001
        pass  # Fail-safe: logging still works without structlog binding


# ─── Sprint phase ContextVar ────────────────────────────────────────────────

# Sprint phase ContextVar — set by SprintScheduler.phase_transition_callback
_sprint_phase_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "sprint_phase", default=""
    )


def set_sprint_phase(phase: str) -> None:
    """Set the current sprint phase for TaskGroup child task visibility."""
    _sprint_phase_var.set(phase)


def get_sprint_phase() -> str:
    """Get the current sprint phase."""
    return _sprint_phase_var.get()


# ─── Stealth layer ContextVar ───────────────────────────────────────────────

# Stealth layer ContextVar — set by SprintScheduler when stealth is enabled
_stealth_enabled_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "stealth_enabled", default=False
    )


def set_stealth_enabled(enabled: bool) -> None:
    """Set the stealth layer enabled state for TaskGroup child task visibility."""
    _stealth_enabled_var.set(enabled)


def is_stealth_enabled() -> bool:
    """Check if stealth layer is currently enabled."""
    return _stealth_enabled_var.get()


# ─── BLITZ-12: Blitz mode ContextVar ─────────────────────────────────────────

# Blitz mode — set at sprint start when duration ≤ 1800s (30 min).
# When active, ALL stealth/anti-correlation jitter delays are skipped
# because the sprint is a one-shot burst where stealth timing is irrelevant.
# Visible to all TaskGroup child tasks via contextvars propagation.
_blitz_mode_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "blitz_mode", default=False
    )


def set_blitz_mode(enabled: bool) -> None:
    """Enable/disable blitz mode — skips all stealth jitter delays.

    BLITZ-12: When enabled, per-request jitter (0.1-1.8s) is skipped
    entirely, saving 10-50s per 100 requests in short-duration sprints.
    """
    _blitz_mode_var.set(enabled)


def is_blitz_mode() -> bool:
    """Check if blitz mode is active (no stealth jitter)."""
    return _blitz_mode_var.get()


# ─── Issue #046: Sprint ID ContextVar ───────────────────────────────────────

# Per-sprint correlation ID — set once at sprint start, visible to all child tasks
_current_sprint_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_sprint_id", default=""
    )


def set_current_sprint_id(sprint_id: str) -> None:
    """Set the current sprint ID for per-sprint correlation."""
    _current_sprint_id_var.set(sprint_id)


def get_current_sprint_id() -> str:
    """Get the current sprint ID."""
    return _current_sprint_id_var.get()


def generate_sprint_id() -> str:
    """Generate a new sprint ID (UUIDv7 for time-ordering on M1).

    ISSUE-11 FIX: uuid.uuid8() does not exist in Python's uuid module.
    Changed to uuid.uuid7() (Python 3.14+) for time-ordered sprint IDs.
    """
    return uuid.uuid7().hex[:16]


# ─── Issue #046: Request ID ContextVar ──────────────────────────────────────

# Per-fetch correlation ID — set before each HTTP fetch, visible across fetch chain
# Default is empty; set per-request via set_request_id()
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
    )


def set_request_id(request_id: str | None = None) -> str:
    """Set the current request ID.

    Args:
        request_id: Optional ID to set. If None, generates a UUIDv7 hex (16 chars).

    Returns:
        The request_id that was set.
    """
    rid = request_id if request_id else uuid.uuid7().hex[:16]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    """Get the current request ID (may be empty if not set)."""
    return _request_id_var.get()


def reset_request_id() -> None:
    """Reset request ID to empty (call after fetch completes)."""
    _request_id_var.set("")


# ─── Issue #046: Lane Metrics ContextVar ───────────────────────────────────

# Lane metrics ContextVar — set by MLXUnifiedScheduler on each request
# Visible to all async tasks without explicit parameter passing
# Value: dict with keys: lane (str), latency_ms (float), queue_depth (int)
_lane_metrics_var: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "lane_metrics",
    default={"lane": "none", "latency_ms": 0.0, "queue_depth": 0},
    )


def set_lane_metrics(
    lane: str,
    latency_ms: float = 0.0,
    queue_depth: int = 0,
) -> None:
    """Set lane metrics for MLXUnifiedScheduler telemetry."""
    _lane_metrics_var.set({
        "lane": lane,
        "latency_ms": latency_ms,
        "queue_depth": queue_depth,
    })


def get_lane_metrics() -> dict[str, Any]:
    """Get current lane metrics snapshot."""
    return _lane_metrics_var.get()


def update_lane_latency(lane: str, latency_ms: float) -> None:
    """Update lane latency in place (avoids full dict replacement on hot path)."""
    current = _lane_metrics_var.get()
    if current["lane"] == lane:
        # Same lane — update in place (no ContextVar.set() call)
        current["latency_ms"] = latency_ms
    else:
        # Lane changed — full replacement
        _lane_metrics_var.set({
            "lane": lane,
            "latency_ms": latency_ms,
            "queue_depth": current.get("queue_depth", 0),
        })
