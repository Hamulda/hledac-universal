"""
metrics_registry/_areas/sprint.py — Sprint Area Metrics
=============================================

Sprint budget metrics for tracking time and resource consumption.

Metric names:
- sprint_budget_elapsed_ms: Elapsed sprint budget
- sprint_budget_remaining_ms: Remaining sprint budget
- sprint_budget_phase: Current sprint phase
- sprint_phase_duration_avg_ms: Average phase duration
- sprint_phase_duration_p50_ms: Phase duration p50
- sprint_phase_duration_p95_ms: Phase duration p95

Usage:
    from metrics_registry._areas.sprint import register_area
    register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metrics_registry.registry import MetricsRegistry

# ── Metric Names ───────────────────────────────────────────────────────────────

SPRINT_METRIC_NAMES = frozenset([
    'sprint_budget_elapsed_ms',
    'sprint_budget_remaining_ms',
    'sprint_budget_phase',
    'sprint_phase_duration_avg_ms',
    'sprint_phase_duration_p50_ms',
    'sprint_phase_duration_p95_ms',
])

# ── Registry ───────────────────────────────────────────────────────────────────

# ISSUE-18: Thread-safe per-registry registration tracking
_registered: dict[int, bool] = {}  # registry id -> registered status
_registered_lock = threading.Lock()


def register_area(registry: "MetricsRegistry") -> None:
    """
    Register Sprint area metrics with the registry.

    Called automatically by the lazy area registry on first use.
    
    ISSUE-18 fix: Thread-safe per-registry tracking instead of global flag.
    """
    registry_id = id(registry)
    with _registered_lock:
        if _registered.get(registry_id, False):
            return
        _registered[registry_id] = True


def record_sprint_budget(
    registry: "MetricsRegistry",
    elapsed_ms: float,
    remaining_ms: float,
    phase: str,
) -> None:
    """
    Record sprint budget consumption.

    Args:
        registry: MetricsRegistry instance
        elapsed_ms: Elapsed time in milliseconds
        remaining_ms: Remaining time in milliseconds
        phase: Current sprint phase name
    """
    registry.set_gauge('sprint_budget_elapsed_ms', elapsed_ms)
    registry.set_gauge('sprint_budget_remaining_ms', remaining_ms)
    registry.set_gauge('sprint_budget_phase', float(hash(phase) % 1000))


def record_phase_duration(
    registry: "MetricsRegistry",
    avg_ms: float | None = None,
    p50_ms: float | None = None,
    p95_ms: float | None = None,
) -> None:
    """
    Record phase duration statistics.

    Args:
        registry: MetricsRegistry instance
        avg_ms: Average duration in milliseconds
        p50_ms: p50 duration in milliseconds
        p95_ms: p95 duration in milliseconds
    """
    if avg_ms is not None:
        registry.set_gauge('sprint_phase_duration_avg_ms', avg_ms)
    if p50_ms is not None:
        registry.set_gauge('sprint_phase_duration_p50_ms', p50_ms)
    if p95_ms is not None:
        registry.set_gauge('sprint_phase_duration_p95_ms', p95_ms)
