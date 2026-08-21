"""
metrics_registry/_areas/ml.py — ML Area Metrics
=========================================

ML metrics for MLX, model loading, and inference tracking.

Metric names:
- mlx_cache_hits: MLX cache hits
- mlx_cache_misses: MLX cache misses
- mlx_cache_size_bytes: MLX cache size in bytes
- mlx_active_memory_bytes: Active MLX memory
- mlx_peak_memory_bytes: Peak MLX memory
- mlx_cache_fragmentation_ratio: Cache fragmentation
- mlx_kernel_compilation_time_ms: Kernel compilation time
- mlx_kernel_cache_hit_rate: Kernel cache hit rate
- model_load_duration_ms: Model load duration
- model_unload_count: Model unload count
- model_load_failures: Model load failures

Usage:
    from metrics_registry._areas.ml import register_area
    register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metrics_registry.registry import MetricsRegistry

# ── Metric Names ───────────────────────────────────────────────────────────────

ML_METRIC_NAMES = frozenset(
    [
        "mlx_cache_hits",
        "mlx_cache_misses",
        "mlx_cache_size_bytes",
        "mlx_active_memory_bytes",
        "mlx_peak_memory_bytes",
        "mlx_cache_fragmentation_ratio",
        "mlx_kernel_compilation_time_ms",
        "mlx_kernel_cache_hit_rate",
        "model_load_duration_ms",
        "model_unload_count",
        "model_load_failures",
    ]
)

# ── Registry ───────────────────────────────────────────────────────────────────

# ISSUE-18: Thread-safe per-registry registration tracking
_registered: dict[int, bool] = {}  # registry id -> registered status
_registered_lock = threading.Lock()


def register_area(registry: MetricsRegistry) -> None:
    """
    Register ML area metrics with the registry.

    Called automatically by the lazy area registry on first use.

    ISSUE-18 fix: Thread-safe per-registry tracking instead of global flag.
    """
    registry_id = id(registry)
    with _registered_lock:
        if _registered.get(registry_id, False):
            return
        _registered[registry_id] = True


def record_mlx_cache_stats(
    registry: MetricsRegistry,
    hits: int,
    misses: int,
    size_bytes: int,
) -> None:
    """
    Record MLX cache statistics.

    Args:
        registry: MetricsRegistry instance
        hits: Cache hit count
        misses: Cache miss count
        size_bytes: Cache size in bytes
    """
    registry.inc("mlx_cache_hits", hits)
    registry.inc("mlx_cache_misses", misses)
    registry.set_gauge("mlx_cache_size_bytes", float(size_bytes))


def record_mlx_memory(
    registry: MetricsRegistry,
    active_bytes: int,
    peak_bytes: int,
    fragmentation: float = 0.0,
) -> None:
    """
    Record MLX memory statistics.

    Args:
        registry: MetricsRegistry instance
        active_bytes: Active memory in bytes
        peak_bytes: Peak memory in bytes
        fragmentation: Fragmentation ratio (0.0-1.0)
    """
    registry.set_gauge("mlx_active_memory_bytes", float(active_bytes))
    registry.set_gauge("mlx_peak_memory_bytes", float(peak_bytes))
    registry.set_gauge("mlx_cache_fragmentation_ratio", fragmentation)


def record_mlx_kernel_stats(
    registry: MetricsRegistry,
    compilation_time_ms: float,
    cache_hit_rate: float,
) -> None:
    """
    Record MLX kernel compilation statistics.

    Args:
        registry: MetricsRegistry instance
        compilation_time_ms: Kernel compilation time in milliseconds
        cache_hit_rate: Kernel cache hit rate (0.0-1.0)
    """
    registry.set_gauge("mlx_kernel_compilation_time_ms", compilation_time_ms)
    registry.set_gauge("mlx_kernel_cache_hit_rate", cache_hit_rate)


def record_model_load(
    registry: MetricsRegistry,
    duration_ms: float,
    success: bool,
) -> None:
    """
    Record model load operation.

    Args:
        registry: MetricsRegistry instance
        duration_ms: Load duration in milliseconds
        success: Whether load succeeded
    """
    registry.set_gauge("model_load_duration_ms", duration_ms)
    if not success:
        registry.inc("model_load_failures")
