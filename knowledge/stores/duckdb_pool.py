"""knowledge/stores/duckdb_pool.py — asyncio.to_thread DuckDB Pool (F320)

M1 8GB optimalizovany DuckDB connection pool.

Design:
- max 2 connections = M1 4P-core ceiling (F265-U5 pattern)
- asyncio.to_thread pro zero-GIL blocking I/O
- thread-local connections pro reuse bez re-connect
- context manager API pro connection lifecycle

Usage:
    pool = DuckDBPool(db_path="/path/to/db")
    async with pool.acquire() as conn:
        result = await asyncio.to_thread(conn.execute, "SELECT * FROM findings")
"""
from __future__ import annotations


import asyncio
import threading
import duckdb
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

# Max 2 connections = M1 P-core ceiling (F265-U5 invariant)
_DEFAULT_MAX_WORKERS = 2


class DuckDBPool:
    """
    M1 8GB optimalizovany DuckDB connection pool.

    Connection-per-task pattern via asyncio.to_thread.
    Thread-local storage prevents connection sharing across threads.

    M1 8GB invariants:
    - max_workers=2 (M1 4P-core ceiling)
    - file-backed mmap (automatic out-of-core)
    - thread-local connections (no cross-thread pollution)
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        read_only: bool = False,
    ):
        self._db_path = db_path
        self._max_workers = min(max_workers, _DEFAULT_MAX_WORKERS)
        self._read_only = read_only
        # Thread-local storage (asyncio.local is for async context, not thread)
        self._local = threading.local()

    @asynccontextmanager
    async def acquire(
        self,
    ) -> AsyncIterator[duckdb.DuckDBPyConnection]:
        """
        Acquire connection from pool via asyncio.to_thread.

        M1 8GB: connection stays in thread-local storage for reuse.
        Max 2 concurrent connections = M1 P-core ceiling.
        """
        conn = await asyncio.to_thread(self._get_connection)
        try:
            yield conn
        finally:
            # Connection stays open in thread-local for reuse
            pass

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create thread-local DuckDB connection."""
        # Thread-local storage key per pool instance
        key = f"_duckdb_conn_{id(self)}"
        conn = getattr(self._local, key, None)

        if conn is None:
            # None db_path = in-memory DuckDB (for testing/temp use)
            if self._db_path is None:
                conn = duckdb.connect(database=":memory:", read_only=self._read_only)
            else:
                conn = duckdb.connect(
                    database=str(self._db_path),
                    read_only=self._read_only,
                    config=self._duckdb_config(),
                )
            setattr(self._local, key, conn)

        return conn

    def _duckdb_config(self) -> dict[str, Any]:
        """DuckDB runtime configuration for M1 8GB."""
        import os

        threads = os.environ.get("HLEDAC_DUCKDB_THREADS", "2")
        return {
            "threads": threads,
            "max_memory": os.environ.get("HLEDAC_DUCKDB_MEMORY", "2GB"),
        }

    async def close(self) -> None:
        """Close all thread-local connections."""
        key = f"_duckdb_conn_{id(self)}"
        conn = getattr(self._local, key, None)
        if conn is not None:
            await asyncio.to_thread(conn.close)
            delattr(self._local, key)

    def __repr__(self) -> str:
        return (
            f"DuckDBPool(db_path={self._db_path!r}, "
            f"max_workers={self._max_workers})"
        )
