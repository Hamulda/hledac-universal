"""
metrics_registry/_areas/http.py — HTTP Area Metrics
==============================================

HTTP metrics for fetch coordinator, circuit breaker, and request tracking.

Metric names:
- http_request_count: Total HTTP requests
- http_request_latency_ms: Request latency in milliseconds
- http_error_count: HTTP error count
- http_circuit_breaker_state: Circuit breaker state (0=closed, 1=half-open, 2=open)
- http_blocked_domains: Number of blocked domains

Usage:
    from metrics_registry._areas.http import register_area
    register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metrics_registry.registry import MetricsRegistry

# ── Metric Names ───────────────────────────────────────────────────────────────

HTTP_METRIC_NAMES = frozenset(
    [
        "http_request_count",
        "http_request_latency_ms",
        "http_error_count",
        "http_circuit_breaker_state",
        "http_blocked_domains",
    ]
)

# ── Registry ───────────────────────────────────────────────────────────────────

# ISSUE-18: Thread-safe per-registry registration tracking
_registered: dict[int, bool] = {}  # registry id -> registered status
_registered_lock = threading.Lock()


def register_area(registry: MetricsRegistry) -> None:
    """
    Register HTTP area metrics with the registry.

    Called automatically by the lazy area registry on first use.

    ISSUE-18 fix: Thread-safe per-registry tracking instead of global flag.
    """
    registry_id = id(registry)
    with _registered_lock:
        if _registered.get(registry_id, False):
            return
        _registered[registry_id] = True


def record_http_request(
    registry: MetricsRegistry,
    latency_ms: float,
    error: bool = False,
) -> None:
    """
    Record an HTTP request with latency and error status.

    Args:
        registry: MetricsRegistry instance
        latency_ms: Request latency in milliseconds
        error: Whether request resulted in error
    """
    registry.inc("http_request_count")
    registry.set_gauge("http_request_latency_ms", latency_ms)
    if error:
        registry.inc("http_error_count")


def record_circuit_breaker_state(
    registry: MetricsRegistry,
    state: str,  # 'closed', 'half_open', 'open'
) -> None:
    """
    Record circuit breaker state.

    Args:
        registry: MetricsRegistry instance
        state: Circuit breaker state
    """
    state_map = {"closed": 0, "half_open": 1, "open": 2}
    registry.set_gauge("http_circuit_breaker_state", float(state_map.get(state, 0)))


def record_blocked_domains(registry: MetricsRegistry, count: int) -> None:
    """
    Record number of blocked domains.

    Args:
        registry: MetricsRegistry instance
        count: Number of blocked domains
    """
    registry.set_gauge("http_blocked_domains", float(count))
