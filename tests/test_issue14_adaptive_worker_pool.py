"""
ISSUE #014: Adaptive worker count based on M1ResourceGovernor.
Tests that SharedWorkerPool dynamically adjusts max_workers based on UMA state.
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from hledac.universal.runtime.worker_pool import (
from core import aclose
    SharedWorkerPool,
    get_shared_pool,
    _GOVERNOR_AVAILABLE,
)


class TestAdaptiveWorkerPool:
    """ISSUE #014: Memory-aware adaptive worker pool tests."""

    @pytest.fixture
    def fresh_pool(self):
        """Create a fresh pool for each test."""
        pool = SharedWorkerPool()
        yield pool
        pool.shutdown()

    def test_fresh_pool_has_default_workers(self, fresh_pool):
        """Fresh pool starts with conservative default."""
        # Default is cpu_count - 4, clamped to [2, 6]
        assert fresh_pool._max_workers >= 2
        assert fresh_pool._max_workers <= 6

    def test_pool_has_executor_lock(self, fresh_pool):
        """Pool has executor_lock for thread-safe reconfiguration."""
        assert hasattr(fresh_pool, "_executor_lock")
        assert isinstance(fresh_pool._executor_lock, threading.Lock)

    def test_pool_has_last_state(self, fresh_pool):
        """Pool tracks last_state for reconfiguration decisions."""
        assert hasattr(fresh_pool, "_last_state")
        # Initial state is None (triggers first reconfiguration)
        assert fresh_pool._last_state is None

    def test_should_reconfigure_on_first_run(self, fresh_pool):
        """First run should always trigger reconfiguration."""
        assert fresh_pool._should_reconfigure(3) is True
        assert fresh_pool._should_reconfigure(5) is True

    def test_should_not_reconfigure_same_workers(self, fresh_pool):
        """No reconfiguration when worker count unchanged."""
        fresh_pool._max_workers = 5
        fresh_pool._last_state = "governed"
        # Same workers → no reconfigure needed
        assert fresh_pool._should_reconfigure(5) is False

    def test_should_reconfigure_different_workers(self, fresh_pool):
        """Reconfiguration when worker count changes."""
        fresh_pool._max_workers = 5
        fresh_pool._last_state = "governed"
        # Different workers → reconfigure needed
        assert fresh_pool._should_reconfigure(3) is True
        assert fresh_pool._should_reconfigure(0) is True

    def test_compute_governed_workers_fallback(self):
        """Fallback to static calculation when governor unavailable."""
        with patch("hledac.universal.runtime.worker_pool._GOVERNOR_AVAILABLE", False):
            # Force re-create to use fallback
            pool = SharedWorkerPool()
            try:
                workers = pool._compute_governed_workers()
                assert 2 <= workers <= 6
            finally:
                pool.shutdown()

    def test_reconfigure_executor_creates_new_executor(self, fresh_pool):
        """Reconfiguration swaps to new ThreadPoolExecutor."""
        old_executor = fresh_pool._executor

        fresh_pool._reconfigure_executor(2)

        # Executor should be different instance
        assert fresh_pool._executor is not old_executor
        # Worker count updated
        assert fresh_pool._max_workers == 2
        # Old executor has shutdown (wait=False)
        # Note: can't easily test this without more mocking

    @pytest.mark.asyncio
    async def test_run_uses_current_executor(self, fresh_pool):
        """Run executes work on current executor."""
        results = []

        def work():
            results.append(threading.current_thread().name)
            return len(results)

        # Run several tasks concurrently
        tasks = [fresh_pool.run(work) for _ in range(5)]
        await asyncio.gather(*tasks)

        # All tasks completed
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_governor_available_flag(self):
        """_GOVERNOR_AVAILABLE is a boolean."""
        assert isinstance(_GOVERNOR_AVAILABLE, bool)


class TestGetSharedPoolSingleton:
    """Singleton behavior tests."""

    def test_get_shared_pool_returns_same_instance(self):
        """Multiple calls return same pool instance."""
        pool1 = get_shared_pool()
        pool2 = get_shared_pool()
        assert pool1 is pool2
        # Cleanup
        pool1.shutdown()

    def test_shutdown_resets_singleton(self):
        """Shutdown resets singleton for fresh creation."""
        pool1 = get_shared_pool()
        pool1.shutdown()

        pool2 = get_shared_pool()
        assert pool2 is not pool1  # New instance
        # Cleanup
        pool2.shutdown()
