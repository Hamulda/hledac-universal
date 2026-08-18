"""
metrics_registry.py — ISSUE-16 Backward Compatibility Module
=======================================================

This module is maintained for backward compatibility.
It redirects all imports to the new metrics_registry/ package.

New usage (recommended):
    from metrics_registry import MetricsRegistry
    from metrics_registry.areas import http, ml, storage, sprint, pipeline

Old usage (still works):
    import metrics_registry
    registry = metrics_registry.create_metrics_registry(...)

Package Structure:
    metrics_registry/
        __init__.py       - Package init + lazy area registry
        _core.py           - Core components (LRUCache, TTLCache, AsyncBatchFlusher)
        registry.py        - Main MetricsRegistry class
        areas/
            __init__.py    - Area registry helpers
            http.py        - HTTP metrics
            ml.py          - ML metrics
            storage.py     - Storage metrics
            sprint.py      - Sprint metrics
            pipeline.py    - Pipeline metrics

Sprint ISSUE-16 (2026-08-18) - Package refactor
"""

from __future__ import annotations

# Redirect all imports to the new package
from metrics_registry import (
    TTLCache,
    LRUCache,
    METRIC_NAMES,
    MetricSnapshot,
    _AsyncBatchFlusher,
    _BoundedCounter,
    _GRAMMAR_KEYS,
)
from metrics_registry.registry import (
    MetricsRegistry,
    create_metrics_registry,
    get_metrics_registry,
)

# Backward-compatible module-level singleton
_metrics_registry_singleton: MetricsRegistry | None = None


def get_metrics_registry() -> MetricsRegistry:
    """Get or create the module-level singleton MetricsRegistry."""
    global _metrics_registry_singleton
    if _metrics_registry_singleton is None:
        _metrics_registry_singleton = MetricsRegistry(
            run_dir=__import__("pathlib").Path('/tmp/hledac_metrics'),
            run_id='default',
        )
    return _metrics_registry_singleton


# Re-export for backward compatibility
__all__ = [
    'MetricsRegistry',
    'create_metrics_registry',
    'get_metrics_registry',
    'METRIC_NAMES',
    'MetricSnapshot',
    '_BoundedCounter',
    '_GRAMMAR_KEYS',
    'TTLCache',
    'LRUCache',
    '_AsyncBatchFlusher',
]
