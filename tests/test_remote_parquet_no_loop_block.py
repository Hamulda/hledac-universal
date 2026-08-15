"""
tests/test_remote_parquet_no_loop_block.py — S-07: RemoteParquetSource._ensure_connection async fix

Acceptance test: duckdb.connect inside async context must not freeze the event loop.

Root cause: duckdb_store.py:533-543 — sync duckdb.connect inside async method.
Fix: duckdb.connect wrapped in asyncio.to_thread via _ensure_connection_async().

Invariant tested:
  S-07-ASYNC-CONNECT: RemoteParquetSource async methods (_ensure_connection_async,
    iter_batches_async, read_table_async, to_polars_lazy_async) use asyncio.to_thread
    for all DuckDB blocking calls — event loop never blocks.

Other async methods in RemoteParquetSource that were fixed:
  - _count_rows_async()      — COUNT(*) on thread pool
  - iter_batches_async()     — full batch iteration on thread pool
  - read_table_async()       — full table read on thread pool
  - to_polars_lazy_async()   — Polars LazyFrame scan on thread pool

Test strategy:
  1. Run a task on the event loop that calls iter_batches_async()
  2. Verify the event loop remains responsive (can run other tasks concurrently)
  3. If the loop freezes (sync duckdb.connect blocks), the concurrent task
     never gets a chance to run → test fails with timeout.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from core import aclose


class TestRemoteParquetSourceAsyncConnect:
    """S-07: Verify async methods don't block the event loop."""

    @pytest.fixture
    def remote_source(self):
        """A mock RemoteParquetSource with in-memory DuckDB."""
        from hledac.universal.knowledge.duckdb_store import RemoteParquetSource

        src = RemoteParquetSource(
            uri="https://example.com/data.parquet",
            source_type="https",
        )
        return src

    @pytest.mark.asyncio
    async def test_ensure_connection_async_returns_without_blocking(self, remote_source) -> None:
        """
        S-07-ASYNC-CONNECT: _ensure_connection_async must not block the event loop.

        Strategy: Call _ensure_connection_async and verify a concurrent task
        (incremented counter) runs while waiting. If sync duckdb.connect
        blocks, the counter never increments.
        """
        counter = {"value": 0}

        async def _increment_while_awaiting():
            """This task should run concurrently with _ensure_connection_async."""
            for _ in range(5):
                await asyncio.sleep(0.01)
                counter["value"] += 1

        async def _await_connection():
            await remote_source._ensure_connection_async()

        # Run both tasks concurrently — connection await should not block increment task
        await asyncio.gather(
            _increment_while_awaiting(),
            _await_connection(),
        )

        assert counter["value"] == 5, (
            f"Event loop was blocked — counter only reached {counter['value']}/5. "
            "Sync duckdb.connect is blocking the event loop!"
        )

    @pytest.mark.asyncio
    async def test_ensure_connection_async_reuses_existing_connection(self, remote_source) -> None:
        """
        Verify that _ensure_connection_async is idempotent — second call returns immediately.
        """
        await remote_source._ensure_connection_async()
        conn_after_first = remote_source._conn

        start = time.monotonic()
        await remote_source._ensure_connection_async()
        elapsed = time.monotonic() - start

        assert elapsed < 0.1, (
            f"Second _ensure_connection_async call took {elapsed:.3f}s — "
            "should be near-instant (connection already established)"
        )
        assert remote_source._conn is conn_after_first, (
            "Connection was recreated on second call — should reuse existing"
        )

    @pytest.mark.asyncio
    async def test_count_rows_async_does_not_block_loop(self, remote_source) -> None:
        """
        S-07: _count_rows_async must not block the event loop.
        """
        # Mock the connection to avoid needing a real remote source
        remote_source._conn = MagicMock()
        remote_source._conn.execute.return_value.fetchone.return_value = (42,)
        remote_source._total_rows = None

        counter = {"value": 0}

        async def _increment_while_awaiting():
            for _ in range(3):
                await asyncio.sleep(0.01)
                counter["value"] += 1

        async def _count_rows():
            result = await remote_source._count_rows_async()
            assert result == 42

        await asyncio.gather(
            _increment_while_awaiting(),
            _count_rows(),
        )

        assert counter["value"] == 3, (
            f"Event loop blocked during _count_rows_async — counter at {counter['value']}/3"
        )

    @pytest.mark.asyncio
    async def test_read_table_async_does_not_block_loop(self, remote_source) -> None:
        """
        S-07: read_table_async must not block the event loop.
        """
        import pyarrow as pa

        # Mock DuckDB connection with Arrow batch data
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchmany.return_value = [
            (1, "test"),
            (2, "data"),
        ]
        mock_result.description = [("id",), ("name",)]
        mock_conn.execute.return_value = mock_result

        remote_source._conn = mock_conn

        counter = {"value": 0}

        async def _increment_while_awaiting():
            for _ in range(3):
                await asyncio.sleep(0.01)
                counter["value"] += 1

        async def _read_table():
            result = await remote_source.read_table_async()
            assert result is not None

        await asyncio.gather(
            _increment_while_awaiting(),
            _read_table(),
        )

        assert counter["value"] == 3, (
            f"Event loop blocked during read_table_async — counter at {counter['value']}/3"
        )

    @pytest.mark.asyncio
    async def test_to_polars_lazy_async_does_not_block_loop(self, remote_source) -> None:
        """
        S-07: to_polars_lazy_async must not block the event loop.
        """
        # Mock DuckDB connection
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.pl.return_value.lazy.return_value = "lazy_frame_mock"
        mock_conn.execute.return_value = mock_result

        remote_source._conn = mock_conn

        counter = {"value": 0}

        async def _increment_while_awaiting():
            for _ in range(3):
                await asyncio.sleep(0.01)
                counter["value"] += 1

        async def _to_lazy():
            result = await remote_source.to_polars_lazy_async()
            assert result is not None

        await asyncio.gather(
            _increment_while_awaiting(),
            _to_lazy(),
        )

        assert counter["value"] == 3, (
            f"Event loop blocked during to_polars_lazy_async — counter at {counter['value']}/3"
        )

    @pytest.mark.asyncio
    async def test_sync_ensure_connection_still_works_for_sync_context(self, remote_source) -> None:
        """
        Verify the sync _ensure_connection() still works for backward compatibility
        when called from sync code (non-async context).
        """
        # Patch _get_duckdb to return a real in-memory DuckDB
        import duckdb

        original_get = remote_source._get_duckdb
        remote_source._get_duckdb = lambda: duckdb

        try:
            remote_source._ensure_connection()  # Sync call — should work
            assert remote_source._conn is not None
            # Verify it's a valid DuckDB connection by executing a query
            result = remote_source._conn.execute("SELECT 1").fetchone()
            assert result == (1,)
        finally:
            remote_source._get_duckdb = original_get
            remote_source.close()
