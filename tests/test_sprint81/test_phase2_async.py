"""
Tests for Sprint 81 - Fáze 2: Bounded Concurrency
=================================================

Test bounded_map, map_as_completed, TaskResult
"""

import asyncio

import pytest


class TestTaskResult:
    """Test TaskResult dataclass."""

    def test_task_result_success(self):
        """Test successful task result."""
        from hledac.universal.utils.async_utils import TaskResult

        result = TaskResult(index=0, value="test_value")
        assert result.success is True
        assert result.value == "test_value"
        assert result.error is None

    def test_task_result_error(self):
        """Test failed task result."""
        from hledac.universal.utils.async_utils import TaskResult

        error = ValueError("test error")
        result = TaskResult(index=1, value=None, error=error)
        assert result.success is False
        assert result.value is None
        assert result.error == error


class TestBoundedMap:
    """Test bounded_map function."""

    @pytest.mark.asyncio
    async def test_bounded_map_basic(self):
        """Test basic bounded_map functionality."""
        from hledac.universal.utils.async_utils import bounded_map

        async def slow_task(x: int) -> int:
            await asyncio.sleep(0.01)
            return x * 2

        tasks = [
            (slow_task, (i,), {}) for i in range(5)
        ]

        results = await bounded_map(tasks, max_concurrent=3)
        assert results == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_bounded_map_preserves_order(self):
        """Test that results maintain input order."""
        from hledac.universal.utils.async_utils import bounded_map

        async def task_with_delay(x: int) -> int:
            await asyncio.sleep(0.05 - x * 0.01)  # Reverse delay
            return x

        tasks = [
            (task_with_delay, (i,), {}) for i in range(5)
        ]

        results = await bounded_map(tasks, max_concurrent=3)
        assert results == [0, 1, 2, 3, 4]  # Order preserved!

    @pytest.mark.asyncio
    async def test_bounded_map_max_concurrent(self):
        """Test max_concurrent parameter."""
        from hledac.universal.utils.async_utils import bounded_map

        max_seen = 0
        current = 0

        async def track_concurrency(x: int) -> int:
            nonlocal max_seen, current
            current += 1
            max_seen = max(max_seen, current)
            await asyncio.sleep(0.02)
            current -= 1
            return x

        tasks = [
            (track_concurrency, (i,), {}) for i in range(6)
        ]

        await bounded_map(tasks, max_concurrent=2)
        assert max_seen <= 2

    @pytest.mark.asyncio
    async def test_bounded_map_retry(self):
        """Test retry functionality."""
        from hledac.universal.utils.async_utils import bounded_map

        attempts = []

        async def flaky_task(x: int) -> int:
            attempts.append(x)
            if len([a for a in attempts if a == x]) < 2:
                raise ValueError("temporary failure")
            return x * 2

        tasks = [(flaky_task, (0,), {})]
        results = await bounded_map(tasks, max_retries=3, max_concurrent=1)
        assert results[0] == 0


class TestMapAsCompleted:
    """Test map_as_completed function."""

    @pytest.mark.asyncio
    async def test_map_as_completed_order(self):
        """Test results come as completed, not in order."""
        from hledac.universal.utils.async_utils import map_as_completed

        async def task_with_delay(x: int) -> int:
            await asyncio.sleep(0.05 * (5 - x))  # Reverse order completion
            return x

        tasks = [
            (task_with_delay, (i,), {}) for i in range(5)
        ]

        # Collect results
        results = []
        async for idx, val in map_as_completed(tasks, max_concurrent=2):
            results.append((idx, val))

        # Should have all 5 results
        assert len(results) == 5
        indices = [r[0] for r in results]
        assert sorted(indices) == [0, 1, 2, 3, 4]


class TestBoundedGather:
    """Test bounded_gather function."""

    @pytest.mark.asyncio
    async def test_bounded_gather_basic(self):
        """Test basic bounded_gather."""
        from hledac.universal.utils.async_utils import bounded_gather

        async def task(x: int) -> int:
            return x * 2

        coros = [task(i) for i in range(3)]
        results = await bounded_gather(*coros, max_concurrent=2)
        assert results == [0, 2, 4]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
