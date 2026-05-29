"""Hermetic tests for ResourceAwareScheduler task lifecycle."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from hledac.universal.coordinators.resource_allocator import (
    IntelligentResourceAllocator,
    Priority,
    ResourceAwareScheduler,
    ResourceRequest,
)


class TestResourceAwareSchedulerTaskLifecycle:
    """Tests for ResourceAwareScheduler task lifecycle management."""

    @pytest.fixture
    def mock_allocator(self):
        allocator = MagicMock(spec=IntelligentResourceAllocator)
        allocator.request_resources = AsyncMock(return_value=True)
        allocator.release_resources = AsyncMock()
        return allocator

    @pytest.fixture
    def scheduler(self, mock_allocator):
        return ResourceAwareScheduler(mock_allocator)

    @pytest.mark.asyncio
    async def test_schedule_task_saves_task_to_registry(self, scheduler, mock_allocator):
        """Test that schedule_task stores task in _tasks registry."""
        task_id = "test_task_1"
        task_func = AsyncMock(return_value="result")

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        result = await scheduler.schedule_task(task_id, task_func, request)

        assert result is True
        assert task_id in scheduler._tasks
        assert scheduler.active_task_count == 1

    @pytest.mark.asyncio
    async def test_task_removed_after_completion(self, scheduler, mock_allocator):
        """Test that task is removed from registry after completion."""
        task_id = "test_task_2"
        completed = asyncio.Event()

        async def slow_task():
            completed.set()
            return "done"

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, slow_task, request)
        await completed.wait()

        # Allow callback to fire
        await asyncio.sleep(0.05)

        assert task_id not in scheduler._tasks
        assert scheduler.active_task_count == 0

    @pytest.mark.asyncio
    async def test_exception_in_task_does_not_create_orphan(self, scheduler, mock_allocator):
        """Test that exception in task releases resources and removes from registry."""
        task_id = "test_task_3"

        async def failing_task():
            raise ValueError("task failed")

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, failing_task, request)

        # Wait for task to complete and callback to fire
        await asyncio.sleep(0.1)

        assert task_id not in scheduler._tasks
        mock_allocator.release_resources.assert_called_once_with(task_id)

    @pytest.mark.asyncio
    async def test_cancelled_error_not_swallowed_in_execute_task(self, scheduler, mock_allocator):
        """Test that CancelledError is re-raised in _execute_task."""
        task_id = "test_task_4"
        cancel_raised = asyncio.Event()

        async def cancellable_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancel_raised.set()
                raise

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, cancellable_task, request)
        task = scheduler._tasks[task_id]
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.05)
        assert task_id not in scheduler._tasks

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_running_tasks(self, scheduler, mock_allocator):
        """Test that shutdown waits for running tasks to complete."""
        task_id = "test_task_5"
        task_completed = asyncio.Event()

        async def long_task():
            await asyncio.sleep(0.1)
            task_completed.set()
            return "done"

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, long_task, request)
        assert scheduler.active_task_count == 1

        # Shutdown should wait for task
        await scheduler.shutdown(timeout=5.0)

        assert task_completed.is_set()
        assert scheduler.active_task_count == 0

    @pytest.mark.asyncio
    async def test_shutdown_timeout_cancels_tasks(self, scheduler, mock_allocator):
        """Test that shutdown cancels tasks after timeout."""
        task_id = "test_task_6"

        async def very_long_task():
            await asyncio.sleep(100)
            return "done"

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, very_long_task, request)

        # Shutdown with very short timeout
        await scheduler.shutdown(timeout=0.05)

        # Task should be cancelled and cleaned up
        assert scheduler.active_task_count == 0
        mock_allocator.release_resources.assert_called()

    @pytest.mark.asyncio
    async def test_schedule_task_refuses_after_shutdown(self, scheduler, mock_allocator):
        """Test that schedule_task returns False after shutdown."""
        # Trigger shutdown
        await scheduler.shutdown()

        task_id = "test_task_7"
        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        result = await scheduler.schedule_task(task_id, AsyncMock(), request)

        assert result is False

    @pytest.mark.asyncio
    async def test_done_callback_logs_non_cancelled_exceptions(self, scheduler, mock_allocator, caplog):
        """Test that done_callback logs exceptions that aren't CancelledError."""
        task_id = "test_task_8"
        caplog.set_level(logging.ERROR)

        async def bad_task():
            raise RuntimeError("intentional error")

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, bad_task, request)
        await asyncio.sleep(0.1)

        # Verify error was logged with the message
        assert any("intentional error" in record.message for record in caplog.records)
        assert task_id not in scheduler._tasks

    @pytest.mark.asyncio
    async def test_active_task_count_property(self, scheduler, mock_allocator):
        """Test active_task_count returns correct count."""
        assert scheduler.active_task_count == 0

        task1_id = "task_1"
        task2_id = "task_2"

        request1 = ResourceRequest(
            task_id=task1_id, task_name="t1", priority=Priority.MEDIUM,
            cpu_cores=1.0, memory_gb=1.0
        )
        request2 = ResourceRequest(
            task_id=task2_id, task_name="t2", priority=Priority.MEDIUM,
            cpu_cores=1.0, memory_gb=1.0
        )

        await scheduler.schedule_task(task1_id, AsyncMock(), request1)
        assert scheduler.active_task_count == 1

        await scheduler.schedule_task(task2_id, AsyncMock(), request2)
        assert scheduler.active_task_count == 2

    @pytest.mark.asyncio
    async def test_release_resources_called_after_task_completes(self, scheduler, mock_allocator):
        """Test that release_resources is always called after task completes."""
        task_id = "test_task_9"
        task_completed = asyncio.Event()

        async def completing_task():
            task_completed.set()
            return "done"

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, completing_task, request)
        await task_completed.wait()
        await asyncio.sleep(0.05)  # callback

        mock_allocator.release_resources.assert_called_once_with(task_id)

    @pytest.mark.asyncio
    async def test_release_resources_called_after_task_exception(self, scheduler, mock_allocator):
        """Test that release_resources is called even when task raises exception."""
        task_id = "test_task_10"

        async def failing_task():
            raise ValueError("fail")

        request = ResourceRequest(
            task_id=task_id,
            task_name="test",
            priority=Priority.MEDIUM,
            cpu_cores=1.0,
            memory_gb=1.0,
        )

        await scheduler.schedule_task(task_id, failing_task, request)
        await asyncio.sleep(0.1)  # task completes + callback

        mock_allocator.release_resources.assert_called_once_with(task_id)
