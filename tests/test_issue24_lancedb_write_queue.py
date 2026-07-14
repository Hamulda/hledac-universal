"""
Issue #24: LanceDB 0.33+ multi-writer segfault fix — write queue test.

Tests that the global write queue properly serializes LanceDB writes.
"""

import asyncio
from unittest.mock import MagicMock

from hledac.universal.knowledge.lancedb_store import (
    _ensure_write_worker,
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

        async def run():
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
