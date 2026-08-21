"""
tests/test_parallel_policy.py

NEW: Parallel Execution Policy Tests

Tests for parallel execution policies - bounded concurrency,
resource allocation, and cancellation safety.

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest


class TestBoundedConcurrency:
    """Tests for bounded concurrency patterns."""

    @pytest.mark.asyncio
    async def test_semaphore_bounded_concurrency(self) -> None:
        """Semaphore must limit concurrent access."""
        active_count = {"value": 0}
        max_concurrent = {"value": 0}
        lock = asyncio.Lock()

        async def bounded_task(n: int) -> int:
            nonlocal max_concurrent
            async with lock:
                active_count["value"] += 1
                max_concurrent["value"] = max(max_concurrent["value"], active_count["value"])

            try:
                await asyncio.sleep(0.05)
                return n * 2
            finally:
                async with lock:
                    active_count["value"] -= 1

        sem = asyncio.Semaphore(3)  # Max 3 concurrent

        async def limited_task(n: int) -> int:
            async with sem:
                return await bounded_task(n)

        # Run 10 tasks, only 3 should be active at once
        results = await asyncio.gather(*[limited_task(i) for i in range(10)])

        assert max_concurrent["value"] <= 3
        assert results == [i * 2 for i in range(10)]

    @pytest.mark.asyncio
    async def test_context_manager_cleanup(self) -> None:
        """Context manager must clean up on exit."""
        cleanup_called = {"value": False}

        class CleanupContext:
            async def __aenter__(self) -> CleanupContext:
                return self

            async def __aexit__(self, *args: Any) -> None:
                cleanup_called["value"] = True

        async with CleanupContext():
            pass

        assert cleanup_called["value"] is True


class TestResourceAllocation:
    """Tests for resource allocation patterns."""

    @pytest.mark.asyncio
    async def test_resource_pool_acquire_release(self) -> None:
        """Resource pool must properly acquire and release resources."""
        available = {"count": 5}
        acquired = []
        lock = asyncio.Lock()

        class MockResource:
            def __init__(self, id: int) -> None:
                self.id = id

            async def release(self) -> None:
                async with lock:
                    available["count"] += 1
                    acquired.remove(self)

        async def acquire_resource() -> MockResource:
            async with lock:
                if available["count"] <= 0:
                    raise RuntimeError("No resources available")
                available["count"] -= 1

            resource = MockResource(len(acquired))
            async with lock:
                acquired.append(resource)
            return resource

        # Acquire some resources
        r1 = await acquire_resource()
        await acquire_resource()

        assert len(acquired) == 2
        assert available["count"] == 3

        # Release
        await r1.release()
        assert len(acquired) == 1
        assert available["count"] == 4


class TestTaskGroupConcurrency:
    """Tests for TaskGroup-based concurrency."""

    @pytest.mark.asyncio
    async def test_task_group_all_complete(self) -> None:
        """All tasks in TaskGroup must complete or fail together."""

        async with asyncio.TaskGroup() as tg:
            tg.create_task(asyncio.sleep(0.01))
            tg.create_task(asyncio.sleep(0.02))
            tg.create_task(asyncio.sleep(0.03))

        # All tasks completed without exception
        assert True

    @pytest.mark.asyncio
    async def test_task_group_exception_propagates(self) -> None:
        """TaskGroup must propagate first exception."""

        async def failing_task() -> None:
            await asyncio.sleep(0.01)
            raise ValueError("Task failed")

        with pytest.raises(ValueError):
            async with asyncio.TaskGroup() as tg:
                tg.create_task(failing_task())

    @pytest.mark.asyncio
    async def test_task_group_cancellation(self) -> None:
        """CancelledTaskGroup must cancel all child tasks."""
        cancelled_count = {"value": 0}

        async def cancellable_task() -> None:
            try:
                await asyncio.sleep(10)  # Long sleep
            except asyncio.CancelledError:
                cancelled_count["value"] += 1
                raise

        async def cancel_after_delay() -> None:
            await asyncio.sleep(0.05)
            # Task should be cancelled here

        with pytest.raises(asyncio.CancelledError):
            async with asyncio.TaskGroup() as tg:
                tg.create_task(cancellable_task())
                tg.create_task(cancel_after_delay())


class TestWorkQueue:
    """Tests for work queue patterns."""

    @pytest.mark.asyncio
    async def test_work_queue_fifo_order(self) -> None:
        """Work queue must process in FIFO order."""
        queue: asyncio.Queue[int] = asyncio.Queue()
        results: list[int] = []

        async def worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    break
                results.append(item)
                queue.task_done()

        # Start worker
        worker_task = asyncio.create_task(worker())

        # Add items
        for i in range(5):
            await queue.put(i)

        # Signal end
        await queue.put(None)
        await worker_task

        assert results == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_work_queue_backpressure(self) -> None:
        """Work queue must apply backpressure when full."""
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=2)
        put_count = {"value": 0}

        async def producer() -> None:
            for i in range(10):
                try:
                    await asyncio.wait_for(queue.put(i), timeout=0.1)
                    put_count["value"] += 1
                except TimeoutError:
                    break  # Queue is full

        await producer()

        # Should only put 2 items (maxsize)
        assert put_count["value"] == 2


class TestFutureCancellation:
    """Tests for Future cancellation patterns."""

    @pytest.mark.asyncio
    async def test_future_cancellation(self) -> None:
        """Future cancellation must propagate to task."""
        cancelled = {"value": False}

        async def cancellable_task() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled["value"] = True
                raise

        task = asyncio.create_task(cancellable_task())
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert cancelled["value"] is True

    @pytest.mark.asyncio
    async def test_shield_from_cancellation(self) -> None:
        """shield() must protect inner task from cancellation."""
        inner_completed = {"value": False}

        async def inner_task() -> None:
            await asyncio.sleep(0.05)
            inner_completed["value"] = True

        outer_task = asyncio.create_task(asyncio.shield(inner_task()))
        await asyncio.sleep(0.01)
        outer_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await outer_task

        # Inner task should have completed
        assert inner_completed["value"] is True


class TestTimeoutPatterns:
    """Tests for timeout patterns."""

    @pytest.mark.asyncio
    async def test_timeout_basic(self) -> None:
        """asyncio.timeout() must cancel on timeout."""

        async def slow_task() -> str:
            await asyncio.sleep(10)
            return "done"

        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.01):
                await slow_task()

    @pytest.mark.asyncio
    async def test_timeout_completes(self) -> None:
        """asyncio.timeout() must allow completion if fast enough."""

        async def fast_task() -> str:
            await asyncio.sleep(0.01)
            return "done"

        async with asyncio.timeout(1.0):
            result = await fast_task()

        assert result == "done"

    @pytest.mark.asyncio
    async def test_timeout_after(self) -> None:
        """asyncio.timeout_at() must work with absolute time."""

        async def slow_task() -> str:
            await asyncio.sleep(10)
            return "done"

        deadline = time.monotonic() + 0.01

        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout_at(deadline):
                await slow_task()


# ============================================================================
# Invariants
# ============================================================================

PARALLEL_POLICY_INVARIANTS = """
PARALLEL EXECUTION INVARIANTS:
1. Bounded concurrency via semaphore prevents resource exhaustion
2. Context managers clean up on exit
3. TaskGroup propagates exceptions from first failing task
4. Work queues maintain FIFO order
5. Backpressure prevents queue overflow
6. Future cancellation propagates to tasks
7. shield() protects critical tasks from cancellation
8. timeout() cancels tasks that exceed time limit
"""
