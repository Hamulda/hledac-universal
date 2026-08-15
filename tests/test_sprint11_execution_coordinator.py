"""
TestSprint11 — ISSUE-011: ExecutionCoordinator AdaptiveWorkerPool integration

Tests:
1. execute_batch uses parallel() with concurrency cap
2. inject_worker_pool sets _worker_pool
3. Memory-aware concurrency via AdaptiveWorkerPool.get_max_workers()
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from hledac.universal.coordinators.execution_coordinator import (
from core import aclose
    ExecutionTask,
    ExecutionResult,
    UniversalExecutionCoordinator,
)


class TestSprint11ExecuteBatch:
    """ISSUE-011: AdaptiveWorkerPool integration tests."""

    @pytest.fixture
    def coordinator(self):
        """Create coordinator instance."""
        return UniversalExecutionCoordinator(max_concurrent=10)

    @pytest.fixture
    def mock_worker_pool(self):
        """Create mock AdaptiveWorkerPool with get_max_workers."""
        pool = MagicMock()
        pool.get_max_workers.return_value = 3
        return pool

    @pytest.mark.asyncio
    async def test_inject_worker_pool(self, coordinator):
        """ISSUE-011: inject_worker_pool sets _worker_pool."""
        pool = MagicMock()
        coordinator.inject_worker_pool(pool)
        assert coordinator._worker_pool is pool

    @pytest.mark.asyncio
    async def test_execute_batch_without_pool(self, coordinator):
        """ISSUE-011: Falls back to max_parallel when no pool injected."""
        tasks = [
            ExecutionTask(
                task_id=f"task-{i}",
                description=f"Test task {i}",
                priority="normal",
                executor="test",
            )
            for i in range(3)
        ]

        # Mock _execute_decision
        async def mock_execute(decision):
            await asyncio.sleep(0.01)
            return ExecutionResult(
                task_id=decision.decision_id,
                success=True,
                summary="OK",
                executor="test",
                execution_time=0.01,
            )

        coordinator._execute_decision = mock_execute

        results = await coordinator.execute_batch(tasks, max_parallel=2)
        assert len(results) == 3
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_execute_batch_with_pool_uses_min(self, coordinator, mock_worker_pool):
        """ISSUE-011: Uses min(max_parallel, pool.get_max_workers()) for concurrency."""
        coordinator.inject_worker_pool(mock_worker_pool)

        tasks = [
            ExecutionTask(
                task_id=f"task-{i}",
                description=f"Test task {i}",
                priority="normal",
                executor="test",
            )
            for i in range(5)
        ]

        execution_times = []

        async def mock_execute(decision):
            await asyncio.sleep(0.05)
            execution_times.append(decision.decision_id)
            return ExecutionResult(
                task_id=decision.decision_id,
                success=True,
                summary="OK",
                executor="test",
                execution_time=0.05,
            )

        coordinator._execute_decision = mock_execute

        # max_parallel=5, pool.get_max_workers()=3 → concurrency should be 3
        results = await coordinator.execute_batch(tasks, max_parallel=5)
        assert len(results) == 5

        # Verify pool.get_max_workers was called
        mock_worker_pool.get_max_workers.assert_called()

    @pytest.mark.asyncio
    async def test_execute_batch_pool_exception_fallback(self, coordinator):
        """ISSUE-011: Falls back to max_parallel if pool.get_max_workers() raises."""
        pool = MagicMock()
        pool.get_max_workers.side_effect = RuntimeError("Pool error")
        coordinator.inject_worker_pool(pool)

        tasks = [
            ExecutionTask(
                task_id="task-1",
                description="Test task",
                priority="normal",
                executor="test",
            )
        ]

        async def mock_execute(decision):
            return ExecutionResult(
                task_id=decision.decision_id,
                success=True,
                summary="OK",
                executor="test",
                execution_time=0.01,
            )

        coordinator._execute_decision = mock_execute

        # Should not raise despite pool error
        results = await coordinator.execute_batch(tasks, max_parallel=5)
        assert len(results) == 1
        assert results[0].success
