"""
Issue #24: LanceDB 0.33+ multi-writer segfault fix — write queue test.
Issue M5: Multi-writer architecture with per-table queues.

Tests that the global write queue properly serializes LanceDB writes.
"""

import asyncio
from unittest.mock import MagicMock

from hledac.universal.knowledge.lancedb_store import (
    _NUM_WRITERS,
    _ensure_write_worker,
    _ensure_write_workers,
    _get_table_queue,
    _get_write_queue,
    _write_worker,
)


class TestWriteQueueBasics:
    """Test write queue module-level state and worker startup."""

    def test_queue_singleton(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """Queue is created on first access."""
        # Should not raise
        q1 = session_event_loop.run_until_complete(_get_write_queue())
        assert q1 is not None
        # Second call returns same queue
        q2 = session_event_loop.run_until_complete(_get_write_queue())
        assert q1 is q2

    def test_ensure_write_worker_idempotent(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """_ensure_write_worker can be called multiple times without error."""
        mock_table = MagicMock()
        # Should not raise even with mock
        session_event_loop.run_until_complete(_ensure_write_worker(mock_table))

    def test_write_worker_drains_queue(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """Write worker drains queue items and calls table.add."""
        mock_table = MagicMock()
        queue: asyncio.Queue[tuple[list | None, float]] = asyncio.Queue()

        async def run() -> None:
            # Put one item
            import time

            await queue.put(([{"id": "test"}], time.monotonic() + 5.0))
            # Put sentinel to stop (batch=None triggers break before stale-check)
            await queue.put((None, 0.0))  # type: ignore[arg-type]
            # Start worker (it will process the item then exit on sentinel)
            await _write_worker(mock_table, queue)

        session_event_loop.run_until_complete(run())
        # Verify table.add was called once
        assert mock_table.add.call_count == 1
        assert mock_table.add.call_args[0][0] == [{"id": "test"}]


class TestMultiWriterArchitecture:
    """M5: Tests for the multi-writer architecture with per-table queues."""

    def test_num_writers_configured(self) -> None:
        """M5: Verify 4 writers are configured by default."""
        assert _NUM_WRITERS == 4

    def test_get_table_queue_returns_shared_queue(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """M5: Same table name returns same queue instance."""

        async def run() -> bool:
            q1 = await _get_table_queue("entities")
            q2 = await _get_table_queue("entities")
            assert q1 is q2
            return True

        session_event_loop.run_until_complete(run())

    def test_different_tables_get_different_queues(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """M5: Different table names get different queues."""

        async def run() -> bool:
            q1 = await _get_table_queue("entities")
            q2 = await _get_table_queue("papers")
            assert q1 is not q2
            return True

        session_event_loop.run_until_complete(run())

    def test_multi_writer_startup(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """M5: _ensure_write_workers starts 4 writer tasks."""

        async def run() -> bool:
            await _ensure_write_workers()
            from hledac.universal.knowledge.lancedb_store import _writer_tasks

            assert len(_writer_tasks) == _NUM_WRITERS
            # All tasks should be running (not done)
            for t in _writer_tasks:
                assert not t.done()
            return True

        session_event_loop.run_until_complete(run())

    def test_legacy_ensure_write_worker_starts_pool(self, session_event_loop: asyncio.AbstractEventLoop) -> None:
        """M5: Legacy _ensure_write_worker still works and starts the pool."""
        mock_table = MagicMock()

        async def run() -> bool:
            await _ensure_write_worker(mock_table)
            from hledac.universal.knowledge.lancedb_store import _writer_tasks

            assert len(_writer_tasks) == _NUM_WRITERS
            return True

        session_event_loop.run_until_complete(run())
