"""
Context state via ContextVar — TaskGroup child task visibility.

Issue 8.4: Sprint phase and stealth state must be visible to all
TaskGroup child tasks without passing explicit parameters.

Issue #046: Extended with:
- _current_sprint_id: ContextVar[str] — per-sprint correlation
- _request_id: ContextVar[str] — per-fetch correlation
- _lane_metrics: ContextVar[dict] — for MLXUnifiedScheduler lane telemetry

structlog integrace: structlog.contextvars.merge_contextvars processor
(viz runtime/_telemetry_setup.py) automaticky začleňuje všechny
ContextVar do log outputu — žádná další konfigurace není potřeba.
"""
from __future__ import annotations


import contextvars
import uuid
from typing import Any


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
    """Generate a new sprint ID (UUID8 for time-ordering on M1)."""
    return uuid.uuid8().hex[:16]


# ─── Issue #046: Request ID ContextVar ──────────────────────────────────────

# Per-fetch correlation ID — set before each HTTP fetch, visible across fetch chain
# Default is empty; set per-request via set_request_id()
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def set_request_id(request_id: str | None = None) -> str:
    """Set the current request ID.

    Args:
        request_id: Optional ID to set. If None, generates a UUID8 hex (16 chars).

    Returns:
        The request_id that was set.
    """
    rid = request_id if request_id else uuid.uuid8().hex[:16]
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
