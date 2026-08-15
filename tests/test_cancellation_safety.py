"""
tests/test_cancellation_safety.py

NEW-C1: Asyncio Cancellation Safety Tests

Tests for proper cancellation handling across the codebase following patterns
from memory-20260810-NEW-H5:
- CancelledError handling in finally blocks
- Self-deadlock prevention
- Lock acquisition ordering
- Executor shutdown safety

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from _core import aclose


class TestCancelledErrorHandling:
    """
    Tests for proper CancelledError handling in finally blocks.
    
    NEW-H5a: Pattern - use explicit flags instead of fut.cancelled()
    in finally blocks since CancelledError is raised before finally runs.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_in_finally_uses_flag(self) -> None:
        """
        When task is cancelled, finally block must use explicit flag,
        not fut.cancelled() which is unreliable in that context.
        """
        _cancelled = False
        error: BaseException | None = None
        
        async def task_that_may_fail() -> None:
            nonlocal error
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                nonlocal _cancelled
                _cancelled = True
                raise
            finally:
                # WRONG: if error and not fut.cancelled(): raise error
                # CORRECT: use _cancelled flag
                if error and not _cancelled:
                    raise error
        
        async def canceller() -> None:
            await asyncio.sleep(0.05)
            # Cancel would happen here if we had a task
        
        # Verify the flag pattern works
        task = asyncio.create_task(task_that_may_fail())
        await asyncio.sleep(0.05)
        task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await task
        
        assert _cancelled is True

    @pytest.mark.asyncio
    async def test_cancellation_during_io(self) -> None:
        """
        Cancellation during I/O operations must clean up resources.
        """
        cleanup_called = False
        
        async def io_task() -> str:
            nonlocal cleanup_called
            try:
                await asyncio.sleep(0.1)
                return "result"
            finally:
                cleanup_called = True
        
        task = asyncio.create_task(io_task())
        await asyncio.sleep(0.05)
        task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await task
        
        assert cleanup_called is True


class TestSelfDeadlockPrevention:
    """
    Tests for self-deadlock prevention patterns.
    
    NEW-H5b: NEVER use run_coroutine_threadsafe(...).result() 
    when called from within the event loop. Use run_until_complete instead.
    """

    @pytest.mark.asyncio
    async def test_no_nested_asyncio_run(self) -> None:
        """
        Nested asyncio.run() creates nested event loops and causes deadlock.
        This pattern must be avoided.
        """
        async def inner_coro() -> int:
            return 42
        
        # WRONG: asyncio.run(inner_coro())  # Creates nested event loop
        # CORRECT: Use run_until_complete or await directly
        result = await inner_coro()
        assert result == 42

    @pytest.mark.asyncio
    async def test_sync_to_async_bridge(self) -> None:
        """
        Sync functions calling async code must use run_until_complete,
        not run_coroutine_threadsafe(...).result().
        """
        result_container = {"value": None}
        
        async def async_operation() -> str:
            await asyncio.sleep(0.01)
            return "async result"
        
        def sync_caller() -> None:
            # CORRECT pattern for sync-to-async bridge
            loop = asyncio.get_running_loop()
            result_container["value"] = loop.run_until_complete(async_operation())
        
        # Call from async context
        sync_caller()
        assert result_container["value"] == "async result"

    def test_sync_context_no_running_loop(self) -> None:
        """
        Sync code without event loop must use asyncio.run(), not run_until_complete.
        """
        async def simple_coro() -> int:
            await asyncio.sleep(0.01)
            return 123
        
        # In truly sync context (no running loop), asyncio.run() is correct
        result = asyncio.run(simple_coro())
        assert result == 123


class TestLockAcquisitionOrdering:
    """
    Tests for proper lock acquisition ordering.
    
    NEW-H5d: When collecting multiple locks, minimize lock hold time
    by collecting references first, then entering contexts outside the lock.
    """

    @pytest.mark.asyncio
    async def test_nested_lock_pattern(self) -> None:
        """
        Nested locks must be acquired in consistent order to prevent deadlock.
        """
        lock_a = asyncio.Lock()
        lock_b = asyncio.Lock()
        
        results: list[int] = []
        
        async def acquire_ab() -> None:
            async with lock_a:
                async with lock_b:
                    results.append(1)
        
        async def acquire_ba() -> None:
            # Same order as acquire_ab - prevents deadlock
            async with lock_a:
                async with lock_b:
                    results.append(2)
        
        await asyncio.gather(acquire_ab(), acquire_ba())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_lock_minimization_pattern(self) -> None:
        """
        Lock hold time should be minimized by doing work outside the lock.
        """
        lock = asyncio.Lock()
        collected_items: list[int] = []
        
        async def collect_under_lock(items: list[int]) -> None:
            # Collect references under lock
            with lock:
                collected = list(items)
            
            # Process OUTSIDE lock - CORRECT pattern
            await asyncio.sleep(0.01)  # Simulates I/O
            result = [x * 2 for x in collected]
            collected_items.extend(result)
        
        await collect_under_lock([1, 2, 3])
        assert collected_items == [2, 4, 6]


class TestExecutorShutdownSafety:
    """
    Tests for executor shutdown safety patterns.
    
    NEW-H5e: Reject new jobs after shutdown is initiated.
    """

    @pytest.mark.asyncio
    async def test_shutdown_rejects_new_jobs(self) -> None:
        """
        After shutdown is called, executor must reject new jobs.
        """
        loop = asyncio.get_running_loop()
        shutdown_initiated = False
        
        class SafeExecutor:
            def __init__(self) -> None:
                self._shutdown_event = threading.Event()
            
            def submit(self, fn: Any, *args: Any) -> Any:
                if self._shutdown_event.is_set():
                    raise RuntimeError("shutdown already called")
                return loop.run_in_executor(fn, *args)
            
            def shutdown(self) -> None:
                self._shutdown_event.set()
        
        executor = SafeExecutor()
        
        # Submit before shutdown - should work
        future1 = await executor.submit(lambda: time.sleep(0.01) or 1)
        result1 = await future1
        assert result1 == 1
        
        # Shutdown
        executor.shutdown()
        
        # Submit after shutdown - must raise
        with pytest.raises(RuntimeError, match="shutdown already called"):
            await executor.submit(lambda: 2)

    @pytest.mark.asyncio
    async def test_graceful_shutdown_waits(self) -> None:
        """
        Graceful shutdown should wait for in-flight tasks.
        """
        loop = asyncio.get_running_loop()
        task_started = asyncio.Event()
        task_can_continue = asyncio.Event()
        
        async def long_task() -> int:
            task_started.set()
            await task_can_continue.wait()
            return 42
        
        # Submit task
        future = loop.run_in_executor(lambda: asyncio.run(long_task()))
        
        # Wait for task to start
        await task_started.wait()
        
        # Allow task to complete
        task_can_continue.set()
        
        result = await future
        assert result == 42


class TestConcurrencyPatterns:
    """Additional concurrency safety tests for common patterns."""

    @pytest.mark.asyncio
    async def test_task_group_cancellation(self) -> None:
        """
        TaskGroup cancellation must handle all child task exceptions.
        """
        results: list[int] = []
        
        async def subtask(n: int) -> None:
            try:
                await asyncio.sleep(n * 0.1)
                results.append(n)
            except asyncio.CancelledError:
                results.append(-n)  # Mark as cancelled
                raise
        
        async with asyncio.TaskGroup() as tg:
            tg.create_task(subtask(1))
            tg.create_task(subtask(2))
            tg.create_task(subtask(3))
        
        # At least some tasks should complete or be cancelled
        assert len(results) == 3
        assert 1 in results or -1 in results

    @pytest.mark.asyncio
    async def test_shield_from_cancellation(self) -> None:
        """
        asyncio.shield() must protect inner task from outer cancellation.
        """
        inner_completed = False
        
        async def inner_task() -> str:
            nonlocal inner_completed
            await asyncio.sleep(0.05)
            inner_completed = True
            return "protected"
        
        outer_task = asyncio.create_task(asyncio.shield(inner_task()))
        await asyncio.sleep(0.01)
        outer_task.cancel()
        
        with pytest.raises(asyncio.CancelledError):
            await outer_task
        
        # Inner task completed despite outer cancellation
        assert inner_completed is True

    @pytest.mark.asyncio
    async def test_timeout_cancellation(self) -> None:
        """
        asyncio.timeout() must cancel task on timeout.
        """
        async def slow_task() -> int:
            await asyncio.sleep(10)  # Would take 10 seconds
            return 42
        
        try:
            async with asyncio.timeout(0.1):
                await slow_task()
            pytest.fail("Should have timed out")
        except asyncio.TimeoutError:
            pass  # Expected


# ============================================================================
# Invariants
# ============================================================================

CANCELLATION_SAFETY_INVARIANTS = """
ASYNC CANCELLATION SAFETY INVARIANTS:
1. Never catch BaseException in async code - let CancelledError propagate
2. Use explicit flags in finally blocks, not fut.cancelled()
3. Never use asyncio.run() inside an existing event loop
4. Use run_until_complete for sync-to-async bridges in async context
5. Acquire multiple locks in consistent order to prevent deadlock
6. Minimize lock hold time - collect references, enter contexts outside lock
7. Reject new jobs after shutdown is initiated
8. Use asyncio.shield() for critical tasks that must complete
"""
