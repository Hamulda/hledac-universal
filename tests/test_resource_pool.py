"""
tests/test_resource_pool.py — R-1: Centralized Resource Pool Tests

Sprint R-1 (2026-07-18)
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from hledac.universal.core.resource_pool import (
from core import aclose
    PoolKind,
    _DuckDBPool,
    _CPUPool,
    get_pool_stats,
    run_in_io_pool,
    run_in_blocking_pool,
    with_resource,
    with_resource_async,
    resize_cpu_pools,
)


# =============================================================================
# DuckDB Pool Tests
# =============================================================================


class TestDuckDBPool:
    """Test DuckDB connection pool."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary DuckDB database."""
        import duckdb
        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
            db_path = f.name
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE test(id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1), (2), (3)")
        conn.close()
        yield db_path
        import os
        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_pool_acquire_release(self, temp_db: str) -> None:
        """Test basic acquire/release cycle."""
        pool = _DuckDBPool(max_size=2, max_absolute=4)
        conn, path = pool.acquire(temp_db, read_only=True)
        assert conn is not None
        assert path == temp_db
        result = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
        assert result == 3
        pool.release(conn, path)

    def test_pool_reuse(self, temp_db: str) -> None:
        """Test that connections are reused."""
        pool = _DuckDBPool(max_size=2, max_absolute=4)
        conn1, path1 = pool.acquire(temp_db, read_only=True)
        pool.release(conn1, path1)
        conn2, path2 = pool.acquire(temp_db, read_only=True)
        assert conn1 is conn2  # Same connection reused
        pool.release(conn2, path2)

    def test_pool_max_size(self, temp_db: str) -> None:
        """Test pool respects max size."""
        pool = _DuckDBPool(max_size=2, max_absolute=4)
        conn1, path1 = pool.acquire(temp_db, read_only=True)
        conn2, path2 = pool.acquire(temp_db, read_only=True)
        assert conn1 is not conn2  # Different connections
        pool.release(conn1, path1)
        pool.release(conn2, path2)

    def test_pool_health_check(self, temp_db: str) -> None:
        """Test health check on acquire."""
        pool = _DuckDBPool(max_size=2, max_absolute=4, health_check=True)
        conn, path = pool.acquire(temp_db, read_only=True)
        # Close the connection manually to make it stale
        conn.close()
        # Acquire again should create new connection
        conn2, path2 = pool.acquire(temp_db, read_only=True)
        assert conn2 is not None
        pool.release(conn2, path2)

    def test_pool_context_manager(self, temp_db: str) -> None:
        """Test with_resource context manager."""
        with with_resource(PoolKind.DUCKDB_RO, temp_db) as conn:
            result = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
            assert result == 3

    def test_pool_stats(self, temp_db: str) -> None:
        """Test pool statistics tracking."""
        pool = _DuckDBPool(max_size=2, max_absolute=4)
        conn, path = pool.acquire(temp_db, read_only=True)
        pool.release(conn, path)
        assert pool.stats.acquire_count == 1
        assert pool.stats.release_count == 1
        assert pool.stats.pool_hits == 0  # First acquire is a miss
        conn2, path2 = pool.acquire(temp_db, read_only=True)
        assert pool.stats.pool_hits == 1  # Second acquire reuses
        pool.release(conn2, path2)

    def test_pool_concurrent(self, temp_db: str) -> None:
        """Test concurrent access to pool."""
        pool = _DuckDBPool(max_size=4, max_absolute=8)
        results: list[int] = []
        lock = threading.Lock()

        def worker() -> None:
            conn, path = pool.acquire(temp_db, read_only=True)
            try:
                result = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
                with lock:
                    results.append(result)
            finally:
                pool.release(conn, path)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker) for _ in range(8)]
            for f in futures:
                f.result()

        assert len(results) == 8
        assert all(r == 3 for r in results)


# =============================================================================
# CPU Pool Tests
# =============================================================================


class TestCPUPool:
    """Test CPU thread pools."""

    def test_run_in_io_pool(self) -> None:
        """Test run_in_io_pool helper."""
        def heavy_computation() -> int:
            return sum(range(1000))

        result = asyncio.run(run_in_io_pool(heavy_computation))
        assert result == sum(range(1000))

    def test_run_in_blocking_pool(self) -> None:
        """Test run_in_blocking_pool helper."""
        def blocking_io() -> str:
            time.sleep(0.01)
            return "done"

        result = asyncio.run(run_in_blocking_pool(blocking_io))
        assert result == "done"

    def test_resize_cpu_pools(self) -> None:
        """Test CPU pool resize."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class MockPreset:
            max_workers: int = 2

        initial_stats = get_pool_stats()
        initial_io_max = initial_stats.cpu_io_max

        resize_cpu_pools(MockPreset(max_workers=2))
        stats = get_pool_stats()
        assert stats.cpu_io_max == 2
        assert stats.cpu_blocking_max == 1  # Half of 2


# =============================================================================
# Integration Tests
# =============================================================================


class TestResourcePoolIntegration:
    """Integration tests for resource pool."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary DuckDB database."""
        import duckdb
        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
            db_path = f.name
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE test(id INTEGER, value TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        conn.close()
        yield db_path
        import os
        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_mixed_pool_usage(self, temp_db: str) -> None:
        """Test using multiple pool types together."""
        # DuckDB pool
        with with_resource(PoolKind.DUCKDB_RO, temp_db) as conn:
            result = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
            assert result == 3

        # CPU pool
        def cpu_task() -> int:
            return 42

        result = asyncio.run(run_in_io_pool(cpu_task))
        assert result == 42

    def test_async_context_manager(self, temp_db: str) -> None:
        """Test async context manager for CPU pools."""
        async def use_pool() -> int:
            async with with_resource_async(PoolKind.CPU_IO) as executor:
                result = await asyncio.get_event_loop().run_in_executor(
                    executor, lambda: 123
                )
                return result
            return 0

        result = asyncio.run(use_pool())
        assert result == 123

    def test_global_pool_stats(self) -> None:
        """Test global pool statistics."""
        stats = get_pool_stats()
        assert hasattr(stats, "duckdb_ro")
        assert hasattr(stats, "duckdb_rw")
        assert hasattr(stats, "cpu_io_max")
        assert hasattr(stats, "cpu_blocking_max")


# =============================================================================
# Stress Tests
# =============================================================================


class TestResourcePoolStress:
    """Stress tests for resource pool."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary DuckDB database."""
        import duckdb
        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
            db_path = f.name
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE test(id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.close()
        yield db_path
        import os
        try:
            os.unlink(db_path)
        except Exception:
            pass

    def test_high_concurrency(self, temp_db: str) -> None:
        """Test pool under high concurrency."""
        pool = _DuckDBPool(max_size=4, max_absolute=8)
        results: list[bool] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                conn, path = pool.acquire(temp_db, read_only=True)
                try:
                    result = conn.execute("SELECT COUNT(*) FROM test").fetchone()[0]
                    with lock:
                        results.append(result == 1)
                finally:
                    pool.release(conn, path)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker) for _ in range(32)]
            for f in futures:
                f.result()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 32
        assert all(results)

    def test_rapid_acquire_release(self, temp_db: str) -> None:
        """Test rapid acquire/release cycles."""
        pool = _DuckDBPool(max_size=2, max_absolute=4)
        for _ in range(100):
            conn, path = pool.acquire(temp_db, read_only=True)
            pool.release(conn, path)
        assert pool.stats.acquire_count == 100
        assert pool.stats.release_count == 100
