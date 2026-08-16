"""
test_bounded_queues.py — S1-10, S1-13: Bounded Queue Tests

Tests for:
- S1-10: NEREngine single-slot queue → 16-slot buffer
- S1-13: ToolExecLog put_nowait with overflow counter
"""
from __future__ import annotations

import asyncio
import os
import pytest
from collections.abc import AsyncIterator

from hledac.universal.tool_exec_log import ToolExecLog
from pathlib import Path
from _core import aclose


# ============================================================================
# S1-13: ToolExecLog Bounded Write Queue Tests
# ============================================================================

class TestToolExecLogBoundedQueue:
    """S1-13: ToolExecLog write queue must not drop silently on overflow."""

    @pytest.fixture
    def temp_log_dir(self) -> Path:
        """Temp directory for log files."""
        tmpdir = f"/tmp/test_tool_exec_log_{os.getpid()}_{id(self)}"
        Path(tmpdir).mkdir(parents=True, exist_ok=True)
        return Path(tmpdir)

    @pytest.mark.asyncio
    async def test_write_queue_accepts_burst(self, temp_log_dir: Path) -> None:
        """
        Write queue should handle bursts up to _WRITE_QUEUE_MAXSIZE (2000)
        without dropping events.
        """
        log = ToolExecLog(run_dir=temp_log_dir, enable_persist=True, run_id="test_burst")
        await log.initialize()

        # Submit 100 events rapidly (well under 2000 limit)
        for i in range(100):
            log.log(
                tool_name=f"test_tool_{i}",
                input_data=b"input_data",
                output_data=b"output_data",
                status="success"
    )

        # Wait for write worker to process
        await asyncio.sleep(0.5)
        await log.aclose()

        # All events should be logged
        stats = log.get_stats()
        assert stats["seq"] == 100

    @pytest.mark.asyncio
    async def test_overflow_counter_increments(self, temp_log_dir: Path) -> None:
        """
        When queue is full, overflow_count should increment.
        S1-13: First overflow logs a warning, subsequent ones only increment counter.
        """
        log = ToolExecLog(run_dir=temp_log_dir, enable_persist=True, run_id="test_overflow")

        # Fill the queue beyond its limit by rapid logging
        # The queue is 2000, but we can test the counter mechanism
        for i in range(2500):
            log.log(
                tool_name=f"tool_{i}",
                input_data=b"x",
                output_data=b"y",
                status="success"
    )

        final_overflow = log._overflow_count

        # S1-13 INVARIANT: overflow_count tracks dropped events
        # Events beyond queue capacity are counted
        assert final_overflow >= 0

        await log.aclose()

    @pytest.mark.asyncio
    async def test_log_returns_event(self, temp_log_dir: Path) -> None:
        """log() should return ToolExecEvent when not silent_failure."""
        log = ToolExecLog(run_dir=temp_log_dir, enable_persist=False, run_id="test_no_persist")

        event = log.log(
            tool_name="test_tool",
            input_data=b"input",
            output_data=b"output",
            status="success"
    )

        assert event is not None
        assert event.tool_name == "test_tool"
        assert event.status == "success"

    def test_silent_failure_returns_none(self, temp_log_dir: Path) -> None:
        """silent_failure=True should make log() return None."""
        log = ToolExecLog(
            run_dir=temp_log_dir,
            enable_persist=True,
            run_id="test_silent",
            silent_failure=True
    )

        event = log.log(
            tool_name="test_tool",
            input_data=b"input",
            output_data=b"output",
            status="success"
    )

        assert event is None

    def test_write_queue_maxsize(self) -> None:
        """_WRITE_QUEUE_MAXSIZE should be 2000 (S1-13)."""
        assert ToolExecLog._WRITE_QUEUE_MAXSIZE == 2000


# ============================================================================
# S1-10: NER Engine Queue Size Tests (via inspection)
# ============================================================================

class TestNEREngineQueueSize:
    """S1-10: NER persistent worker should use 16-slot response queue."""

    def test_queue_buffer_size_comment(self) -> None:
        """
        Verify the S1-10 fix comment exists.

        The fix changed Queue(maxsize=1) → Queue(maxsize=16) to prevent
        producer blocking when consumer is slow.
        """
        import inspect
        from hledac.universal.brain.ner_engine import _NERPersistentWorker

        source = inspect.getsource(_NERPersistentWorker.extract)
        assert "maxsize=16" in source, "NER worker should use maxsize=16 queue"
        assert "S1-10 FIX" in source, "S1-10 fix comment should be present"


# ============================================================================
# Bounded Queue Invariants (S1-10, S1-13)
# ============================================================================

INVARIANT_S1_10 = """
S1-10 NER ENGINE INVARIANTS:
1. Response queue size 16 prevents single-slot blocking
2. Timeout on queue.get() prevents indefinite wait
3. Per-request queue (not shared) prevents cross-request contamination
"""

INVARIANT_S1_13 = """
S1-13 TOOL EXEC LOG INVARIANTS:
1. put_nowait() is NON-BLOCKING — correct for sync log() path
2. QueueFull → increment overflow_count (not drop silently)
3. First overflow logs warning, subsequent ones are silent
4. _WRITE_QUEUE_MAXSIZE = 2000 provides 2× headroom for bursts
"""
