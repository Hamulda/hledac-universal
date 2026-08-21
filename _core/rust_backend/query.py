# query.py — DuckDB Query domain
"""
DuckDB query execution with connection pooling.
Provides parallel and single query execution with pooled connections.

ISSUE-04: Now uses core.duckdb_pool as the canonical RO pool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# ISSUE-04: Use canonical pool instead of inline pool
from hledac.universal._core.duckdb_pool import (
    close_all_pools,
    duckdb_ro_acquire,
    get_pool_stats,
)

# DEPRECATED: Inline pool replaced by core.duckdb_pool
# Keeping for backward compatibility until all callers migrate


def _get_duckdb_module() -> Any:
    """Get DuckDB module (lazy import to avoid hard dependency)."""
    try:
        import duckdb

        return duckdb
    except ImportError:
        return None


def _acquire_ro_conn(db_path: str) -> Any:
    """
    Acquire read-only connection from canonical pool.

    ISSUE-04: Now delegates to duckdb_pool.duckdb_ro_acquire().
    This ensures:
    - Bounded pool size from resource_governor
    - Health validation on acquire
    - M1 8GB safe defaults
    """
    return duckdb_ro_acquire(db_path)


def _pool_stats() -> dict:
    """Get pool statistics for debugging."""
    return get_pool_stats()


def _pool_close_all() -> None:
    """Close all pooled connections."""
    close_all_pools()


class _RustQueryDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def parallel_duckdb_queries(self, db_path: str, queries: list[str]) -> list[dict[str, Any]]:
        """Execute queries in parallel via Rust rayon thread pool."""
        return self._ext.query_duckdb_parallel(db_path, queries)

    def query_duckdb(self, db_path: str, sql: str) -> list[dict[str, Any]]:
        """Execute single DuckDB query."""
        return self._ext.query_duckdb_single(db_path, sql)

    def drop_query_connections(self) -> None:
        """Drop all cached DuckDB connections."""
        self._ext.query_drop_connections()


class _PythonQueryDomain:
    __slots__ = ()

    def parallel_duckdb_queries(self, db_path: str, queries: list[str]) -> list[dict[str, Any]]:
        """Python fallback: execute queries sequentially with pooled connections."""
        return _python_parallel_duckdb_queries(db_path, queries)

    def query_duckdb(self, db_path: str, sql: str) -> list[dict[str, Any]]:
        """Python fallback: execute single query with pooled connection."""
        return _python_query_duckdb(db_path, sql)

    def drop_query_connections(self) -> None:
        """Python fallback: close all pooled connections."""
        _pool_close_all()


def _python_parallel_duckdb_queries(db_path: str, queries: list[str]) -> list[dict[str, Any]]:
    """Python fallback: execute queries sequentially."""
    results = []
    for sql in queries:
        result = _python_query_duckdb(db_path, sql)
        results.append(result)
    return results


def _python_query_duckdb(db_path: str, sql: str) -> list[dict[str, Any]]:
    """
    Python fallback: execute single query with pooled connection.

    ISSUE-04: Uses canonical duckdb_pool. Connections are automatically
    returned to the pool on context exit.
    """
    conn = _acquire_ro_conn(db_path)
    if conn is None:
        return []
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row, strict=False)) for row in rows]
    except Exception:
        return []
    # Connection stays in pool for reuse (no explicit return needed)


def get_query_domain(ext: object | None) -> _RustQueryDomain | _PythonQueryDomain:
    """Factory: return Rust or Python QueryDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustQueryDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonQueryDomain()
