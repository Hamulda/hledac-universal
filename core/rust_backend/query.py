# query.py — DuckDB Query domain
"""
DuckDB query execution with connection pooling.
Provides parallel and single query execution with pooled connections.

"""

from __future__ import annotations

import sqlite3
from collections import deque
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# DuckDB Read-Only Connection Pool
# =============================================================================

# ISSUE #3 FIX: Replaces per-call duckdb.connect() + conn.close() pattern.
# Pooled connections are reused, eliminating 3-5GB/s RAM churn at burst load.
_DUCKDB_POOL: dict[str, deque] = {}
_DUCKDB_POOL_LOCK = Lock()
_POOL_MAX_SIZE = 4


def _get_duckdb_module() -> Any:
    """Get DuckDB module (lazy import to avoid hard dependency)."""
    try:
        import duckdb

        return duckdb
    except ImportError:
        return None


def _acquire_ro_conn(db_path: str) -> Any:
    """Acquire read-only connection from pool."""
    global _DUCKDB_POOL, _DUCKDB_POOL_LOCK
    duckdb = _get_duckdb_module()
    if duckdb is None:
        return None

    with _DUCKDB_POOL_LOCK:
        if db_path not in _DUCKDB_POOL:
            _DUCKDB_POOL[db_path] = deque(maxlen=_POOL_MAX_SIZE)

        pool = _DUCKDB_POOL[db_path]
        if pool:
            try:
                conn = pool.popleft()
                # Test connection is still alive
                conn.execute("SELECT 1")
                return conn
            except Exception:
                pass

    # Create new connection
    try:
        conn = duckdb.connect(db_path, read_only=True)
        return conn
    except Exception:
        return None


def _pool_stats() -> dict:
    """Get pool statistics for debugging."""
    global _DUCKDB_POOL
    with _DUCKDB_POOL_LOCK:
        return {path: len(pool) for path, pool in _DUCKDB_POOL.items()}


def _pool_close_all() -> None:
    """Close all pooled connections."""
    global _DUCKDB_POOL
    with _DUCKDB_POOL_LOCK:
        for pool in _DUCKDB_POOL.values():
            for conn in pool:
                try:
                    conn.close()
                except Exception:
                    pass
        _DUCKDB_POOL.clear()


# =============================================================================
# Query Domain
# =============================================================================


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
    """Python fallback: execute single query with pooled connection."""
    conn = _acquire_ro_conn(db_path)
    if conn is None:
        return []
    try:
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    except Exception:
        return []
    finally:
        # Return connection to pool
        global _DUCKDB_POOL
        with _DUCKDB_POOL_LOCK:
            if db_path in _DUCKDB_POOL:
                _DUCKDB_POOL[db_path].append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass


def get_query_domain(ext: object | None) -> _RustQueryDomain | _PythonQueryDomain:
    """Factory: return Rust or Python QueryDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustQueryDomain(ext)
        except Exception:
            pass
    return _PythonQueryDomain()
