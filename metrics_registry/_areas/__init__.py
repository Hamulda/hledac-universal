"""
metrics_registry/_areas/__init__.py — Area Registry Helpers
======================================================

Area modules that register metrics lazily on first use.

Each area has its own metrics that are registered when the area
module is first imported. This prevents all areas from being
loaded at startup.

Areas:
- http: HTTP metrics (fetch coordinator, circuit breaker)
- ml: ML metrics (MLX, model loading)
- storage: Storage metrics (DuckDB)
- sprint: Sprint budget metrics
- pipeline: Pipeline stage metrics

Usage:
    from metrics_registry._areas import http, ml, storage, sprint, pipeline

    # Each area can be imported and used independently
    http.register_area(registry)
    ml.register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

# Re-export area modules for easy import
from metrics_registry._areas import http, ml, pipeline, sprint, storage

__all__ = ["http", "ml", "storage", "sprint", "pipeline"]
