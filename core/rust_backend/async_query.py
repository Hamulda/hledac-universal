# async_query.py — DuckDB async query domain
"""
Rust-backed DuckDB query functions — O(1) connection pool, race-free execution.

rust_async_query: single query via to_thread
rust_async_query_batch: parallel N queries via rayon (each worker opens
    its own :memory: connection — :memory: is thread-safe in DuckDB)

M1 8GB: max_connections=4 cap, parking_lot::Mutex for lock-held-throughout
ISSUE-013: recv_timeout prevents lost updates and connection leaks.
"""

from __future__ import annotations

import asyncio
from typing import Any


def get_domain() -> "AsyncQueryDomain":
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "rust_async_query", None)
    if _probe is None:
        msg = "hledac_rust_extensions.rust_async_query not available"
        raise ImportError(msg)
    return AsyncQueryDomain(_ext)


class AsyncQueryDomain:
    """Rust DuckDB async query functions."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def query(self, sql: str) -> list[list[str]]:
        """Execute a single SQL query and return rows as list of string lists.

        Runs via spawn_blocking (to_thread). Timeout-aware via mpsc channel.

        Args:
            sql: SQL query string

        Returns:
            List of rows, each row is a list of strings.
            Empty list on error.
        """
        return self._ext.rust_async_query(sql)

    def query_with_params(
        self, sql: str, params: list[Any]
    ) -> list[list[str]]:
        """Execute a parameterized SQL query.

        Args:
            sql: SQL query with $1, $2, ... placeholders
            params: list of Python values (str, int, float)

        Returns:
            List of rows as string lists.
        """
        return self._ext.rust_async_query_with_params(sql, params)

    def query_batch(self, sqls: list[str]) -> list[list[list[str]]]:
        """Execute N SQL queries in rayon parallel.

        Each worker opens its own DuckDB :memory: connection.
        For real DB files, each worker re-opens the same file.

        Args:
            sqls: list of SQL query strings

        Returns:
            List of result sets (one per query).
        """
        return self._ext.rust_async_query_batch(sqls)

    async def query_async(self, sql: str) -> list[list[str]]:
        """Async wrapper — runs rust_async_query in a thread pool.

        Args:
            sql: SQL query string

        Returns:
            List of rows as string lists.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.query, sql)

    async def query_batch_async(
        self, sqls: list[str]
    ) -> list[list[list[str]]]:
        """Async wrapper — runs rust_async_query_batch in a thread pool.

        Args:
            sqls: list of SQL query strings

        Returns:
            List of result sets.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.query_batch, sqls)


# ---------------------------------------------------------------------------
# Python fallback — duckdb stdlib connections
# ---------------------------------------------------------------------------

class PythonFallbackAsyncQueryDomain:
    """Pure-Python fallback using duckdb directly."""

    __slots__ = ("_conn",)

    def __init__(self, db_path: str = ":memory:") -> None:
        import duckdb

        self._conn = duckdb.connect(db_path)

    def query(self, sql: str) -> list[list[str]]:
        result = self._conn.execute(sql).fetchall()
        return [[str(c) for c in row] for row in result]

    def query_with_params(
        self, sql: str, params: list[Any]
    ) -> list[list[str]]:
        result = self._conn.execute(sql, params).fetchall()
        return [[str(c) for c in row] for row in result]

    def query_batch(self, sqls: list[str]) -> list[list[list[str]]]:
        return [self.query(sql) for sql in sqls]
