"""
metrics_registry/_areas/pipeline.py — Pipeline Area Metrics
=================================================

Pipeline stage metrics for tracking processing stages.

Metric names:
- stage_latency_ms_{name}: Stage latency in milliseconds
- stage_items_in_{name}: Items input to stage
- stage_items_out_{name}: Items output from stage
- stage_errors_{name}: Stage errors
- pipeline_stage_count: Number of pipeline stages
- pipeline_total_latency_ms: Total pipeline latency

Dynamic metrics (stage-specific):
- stage_latency_ms_*: Per-stage latency
- stage_items_in_*: Per-stage input count
- stage_items_out_*: Per-stage output count
- stage_errors_*: Per-stage error count

Usage:
    from metrics_registry._areas.pipeline import register_area
    register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metrics_registry.registry import MetricsRegistry

# ── Metric Names ───────────────────────────────────────────────────────────────

PIPELINE_METRIC_NAMES = frozenset(
    [
        "pipeline_stage_count",
        "pipeline_total_latency_ms",
    ]
)

# Dynamic stage metric names are validated by prefix in MetricsRegistry

# ── Registry ───────────────────────────────────────────────────────────────────

# ISSUE-18: Thread-safe per-registry registration tracking
_registered: dict[int, bool] = {}  # registry id -> registered status
_registered_lock = threading.Lock()


def register_area(registry: MetricsRegistry) -> None:
    """
    Register Pipeline area metrics with the registry.

    Called automatically by the lazy area registry on first use.

    ISSUE-18 fix: Thread-safe per-registry tracking instead of global flag.
    """
    registry_id = id(registry)
    with _registered_lock:
        if _registered.get(registry_id, False):
            return
        _registered[registry_id] = True


def record_stage_timing(
    registry: MetricsRegistry,
    stage_name: str,
    latency_ms: float,
    items_in: int = 0,
    items_out: int = 0,
    error: bool = False,
) -> None:
    """
    Record pipeline stage timing.

    Args:
        registry: MetricsRegistry instance
        stage_name: Name of the pipeline stage
        latency_ms: Stage execution time in milliseconds
        items_in: Number of items input to stage
        items_out: Number of items output from stage
        error: Whether stage resulted in an error
    """
    registry.set_gauge(f"stage_latency_ms_{stage_name}", latency_ms)
    registry.set_gauge(f"stage_items_in_{stage_name}", float(items_in))
    registry.set_gauge(f"stage_items_out_{stage_name}", float(items_out))
    if error:
        registry.inc(f"stage_errors_{stage_name}")


def record_pipeline_summary(
    registry: MetricsRegistry,
    stage_count: int,
    total_latency_ms: float,
) -> None:
    """
    Record pipeline summary statistics.

    Args:
        registry: MetricsRegistry instance
        stage_count: Total number of pipeline stages
        total_latency_ms: Total pipeline latency in milliseconds
    """
    registry.set_gauge("pipeline_stage_count", float(stage_count))
    registry.set_gauge("pipeline_total_latency_ms", total_latency_ms)
