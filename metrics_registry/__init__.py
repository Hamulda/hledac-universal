"""
metrics_registry/ — ISSUE-16 Lazy Area-Based Metrics Package
============================================================

Modern refactoring of the monolithic metrics_registry.py into a package
with lazy registration per area (HTTP, ML, Storage, Sprint, Pipeline).

Design Goals:
1. Lazy import - metrics registered only when first used
2. Area-based organization - each area has its own module
3. Backward compatibility - existing code works unchanged
4. M1 8GB safety - bounded storage maintained

Usage:
    # Lazy import (metrics registered on first use)
    from metrics_registry import MetricsRegistry
    from metrics_registry._areas import http, ml, storage, sprint, pipeline

    # Direct import (area registered immediately)
    from metrics_registry._areas.http import register_http_metrics
    register_http_metrics(registry)

Backward Compatibility:
    # Old import still works
    from metrics_registry import MetricsRegistry, create_metrics_registry

    # Old module import still works (redirects to package)
    import metrics_registry
    registry = metrics_registry.create_metrics_registry(...)

Package Structure:
    metrics_registry/
        __init__.py       - Package init + lazy area registry
        _core.py           - Core components (LRUCache, TTLCache, AsyncBatchFlusher)
        registry.py        - Main MetricsRegistry class
        _areas/
            __init__.py    - Area registry helpers
            http.py        - HTTP metrics (fetch coordinator, circuit breaker)
            ml.py          - ML metrics (MLX, model loading)
            storage.py     - Storage metrics (DuckDB, disk)
            sprint.py      - Sprint budget metrics
            pipeline.py    - Pipeline stage metrics

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

# Core exports (backward compatible)
from metrics_registry._core import (
    _GRAMMAR_KEYS,
    METRIC_NAMES,
    LRUCache,
    MetricSnapshot,
    TTLCache,
    _AsyncBatchFlusher,
    _BoundedCounter,
)
from metrics_registry.registry import (
    MetricsRegistry,
    create_metrics_registry,
    get_metrics_registry,
)

__all__ = [
    # Core
    "TTLCache",
    "LRUCache",
    "METRIC_NAMES",
    "MetricSnapshot",
    "_AsyncBatchFlusher",
    "_BoundedCounter",
    "_GRAMMAR_KEYS",
    # Main class
    "MetricsRegistry",
    "create_metrics_registry",
    "get_metrics_registry",
    # Areas (lazy - import modules, not functions)
    "_areas",
]


class _LazyAreaRegistry:
    """
    Lazy area registry that registers metrics on first use.

    ISSUE-16: Prevents all areas from being imported at startup.
    Only imports the area module when metrics from that area are first used.
    """

    __slots__ = ("_registry", "_registered_areas")

    def __init__(self, registry: MetricsRegistry) -> None:
        self._registry = registry
        self._registered_areas: set[str] = set()

    def _ensure_area(self, area_name: str) -> None:
        """Ensure area is registered, import module if needed."""
        if area_name in self._registered_areas:
            return

        import importlib

        try:
            module = importlib.import_module(f"metrics_registry._areas.{area_name}")
            # Call register function if exists
            if hasattr(module, "register_area"):
                module.register_area(self._registry)
            self._registered_areas.add(area_name)
        except ImportError:
            pass  # Area module not available

    def register_http_metrics(self) -> None:
        """Register HTTP area metrics."""
        self._ensure_area("http")

    def register_ml_metrics(self) -> None:
        """Register ML area metrics."""
        self._ensure_area("ml")

    def register_storage_metrics(self) -> None:
        """Register Storage area metrics."""
        self._ensure_area("storage")

    def register_sprint_metrics(self) -> None:
        """Register Sprint area metrics."""
        self._ensure_area("sprint")

    def register_pipeline_metrics(self) -> None:
        """Register Pipeline area metrics."""
        self._ensure_area("pipeline")


# Attach lazy registry as a property on MetricsRegistry
def _get_lazy_area_registry(self: MetricsRegistry) -> _LazyAreaRegistry:
    """Get or create lazy area registry for this instance."""
    if not hasattr(self, "_lazy_area_registry"):
        self._lazy_area_registry = _LazyAreaRegistry(self)
    return self._lazy_area_registry


# Monkey-patch MetricsRegistry to add lazy area registry
MetricsRegistry.lazy = property(_get_lazy_area_registry)
