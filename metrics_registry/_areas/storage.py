"""
metrics_registry/_areas/storage.py — Storage Area Metrics
==================================================

Storage metrics for DuckDB and disk I/O tracking.

Metric names:
- duckdb_ingest_latency_ms: DuckDB ingest latency
- duckdb_query_latency_ms: DuckDB query latency
- duckdb_connection_count: Active DuckDB connections
- duckdb_active_queries: Active queries

Usage:
    from metrics_registry._areas.storage import register_area
    register_area(registry)

Sprint ISSUE-16 (2026-08-18)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metrics_registry.registry import MetricsRegistry

# ── Metric Names ───────────────────────────────────────────────────────────────

STORAGE_METRIC_NAMES = frozenset(
    [
        "duckdb_ingest_latency_ms",
        "duckdb_query_latency_ms",
        "duckdb_connection_count",
        "duckdb_active_queries",
    ]
)

# ── Registry ───────────────────────────────────────────────────────────────────

# ISSUE-18: Thread-safe per-registry registration tracking
_registered: dict[int, bool] = {}  # registry id -> registered status
_registered_lock = threading.Lock()


def register_area(registry: MetricsRegistry) -> None:
    """
    Register Storage area metrics with the registry.

    Called automatically by the lazy area registry on first use.

    ISSUE-18 fix: Thread-safe per-registry tracking instead of global flag.
    """
    registry_id = id(registry)
    with _registered_lock:
        if _registered.get(registry_id, False):
            return
        _registered[registry_id] = True


def record_duckdb_ingest(
    registry: MetricsRegistry,
    latency_ms: float,
    row_count: int = 0,
) -> None:
    """
    Record DuckDB ingest operation.

    Args:
        registry: MetricsRegistry instance
        latency_ms: Ingest latency in milliseconds
        row_count: Number of rows ingested
    """
    registry.set_gauge("duckdb_ingest_latency_ms", latency_ms)


def record_duckdb_query(
    registry: MetricsRegistry,
    latency_ms: float,
) -> None:
    """
    Record DuckDB query operation.

    Args:
        registry: MetricsRegistry instance
        latency_ms: Query latency in milliseconds
    """
    registry.set_gauge("duckdb_query_latency_ms", latency_ms)


def record_duckdb_pool_stats(
    registry: MetricsRegistry,
    connection_count: int,
    active_queries: int,
) -> None:
    """
    Record DuckDB connection pool statistics.

    Args:
        registry: MetricsRegistry instance
        connection_count: Number of active connections
        active_queries: Number of active queries
    """
    registry.set_gauge("duckdb_connection_count", float(connection_count))
    registry.set_gauge("duckdb_active_queries", float(active_queries))
