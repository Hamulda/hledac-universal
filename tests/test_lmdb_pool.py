"""
Test LmdbPool — dedicated LMDB operation pool
==============================================

Tests for runtime/lmdb_pool.py — extracted from legacy role_based_pools.

Run with: pytest tests/test_lmdb_pool.py -v
"""

import asyncio
import threading

import pytest


class TestLmdbPool:
    """Test LmdbPool initialization and operations."""

    def test_get_lmdb_pool_returns_singleton(self) -> None:
        """get_lmdb_pool returns the same instance on repeated calls."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool1 = get_lmdb_pool()
        pool2 = get_lmdb_pool()
        assert pool1 is pool2

    def test_lmdb_pool_lazy_initialization(self) -> None:
        """LmdbPool does not initialize executor until first use."""
        from hledac.universal.runtime.lmdb_pool import LmdbPool

        pool = LmdbPool()
        assert not pool._initialized
        # Trigger initialization via run_lmdb_sync
        pool.run_lmdb_sync(lambda: None)
        assert pool._initialized

    @pytest.mark.asyncio
    async def test_run_lmdb_basic(self) -> None:
        """run_lmdb executes function and returns result."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def compute(x: int) -> int:
            return x * 2

        result = await pool.run_lmdb(compute, 21)
        assert result == 42

    @pytest.mark.asyncio
    async def test_run_lmdb_with_args(self) -> None:
        """run_lmdb passes positional and keyword arguments correctly."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def add(a: int, b: int, factor: int = 1) -> int:
            return (a + b) * factor

        result = await pool.run_lmdb(add, 10, 20, factor=3)
        assert result == 90

    @pytest.mark.asyncio
    async def test_run_lmdb_returns_none_on_exception(self) -> None:
        """run_lmdb returns None when function raises."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def fail() -> None:
            raise RuntimeError("test error")

        result = await pool.run_lmdb(fail)
        assert result is None

    def test_run_lmdb_sync_basic(self) -> None:
        """run_lmdb_sync executes function and returns result."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def compute(x: int) -> int:
            return x + 100

        result = pool.run_lmdb_sync(compute, 50)
        assert result == 150

    def test_run_lmdb_sync_returns_none_on_exception(self) -> None:
        """run_lmdb_sync returns None when function raises."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def fail() -> None:
            raise ValueError("sync error")

        result = pool.run_lmdb_sync(fail)
        assert result is None

    @pytest.mark.asyncio
    async def test_run_lmdb_timeout(self) -> None:
        """run_lmdb respects timeout parameter."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def slow() -> int:
            import time
            time.sleep(0.5)
            return 1

        result = await pool.run_lmdb(slow, timeout=0.01)
        assert result is None  # timed out

    def test_lmdb_pool_singleton_per_thread(self) -> None:
        """Each thread gets its own pool instance (module-level singleton is per-thread)."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        results: dict[int, object] = {}
        errors: dict[int, str] = {}

        def check_pool() -> None:
            tid = threading.current_thread().ident
            assert tid is not None
            try:
                results[tid] = get_lmdb_pool()
            except Exception as e:
                errors[tid] = str(e)

        t1 = threading.Thread(target=check_pool)
        t2 = threading.Thread(target=check_pool)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == 2, f"Expected 2 threads, got {len(results)}"

    @pytest.mark.asyncio
    async def test_concurrent_lmdb_calls(self) -> None:
        """Multiple concurrent run_lmdb calls execute without interference."""
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()

        def increment(x: int) -> int:
            return x + 1

        # Run 10 concurrent increments
        tasks = [pool.run_lmdb(increment, i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        assert sorted(r for r in results if r is not None) == list(range(1, 11))


class TestLmdbPoolModuleFunctions:
    """Test module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_run_lmdb_function(self) -> None:
        """run_lmdb module function delegates to pool."""
        from hledac.universal.runtime.lmdb_pool import run_lmdb

        def compute(x: int) -> int:
            return x * 3

        result = await run_lmdb(compute, 7)
        assert result == 21

    def test_run_lmdb_sync_function(self) -> None:
        """run_lmdb_sync module function delegates to pool."""
        from hledac.universal.runtime.lmdb_pool import run_lmdb_sync

        def compute(x: int) -> int:
            return x + 50

        result = run_lmdb_sync(compute, 50)
        assert result == 100


class TestLmdbPoolShutdown:
    """Test shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_cleans_executor(self) -> None:
        """shutdown sets _initialized to False and clears executor."""
        from hledac.universal.runtime.lmdb_pool import LmdbPool

        pool = LmdbPool()
        pool.run_lmdb_sync(lambda: None)  # trigger init
        assert pool._initialized

        pool.shutdown(wait=False)
        assert not pool._initialized
        assert pool._executor is None
        assert pool._semaphore is None
        assert pool._lock is None
