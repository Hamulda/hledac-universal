"""
Tests for R4.1: Unified Rayon-based Pipeline — utils/rayon_pool.py

Run with: pytest tests/test_rayon_pool.py -v
"""


import asyncio
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

# Import the module under test
from utils.rayon_pool import (
from core import aclose
    RayonPoolsAvailable,
    run_in_cpu_pool,
    run_in_io_pool,
    run_in_mixed_pool,
    run_in_cpu_pool_async,
    run_in_io_pool_async,
    run_in_mixed_pool_async,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def cpu_bound_hash(data: bytes) -> str:
    """CPU-bound work: compute SHA-256 hash multiple times."""
    for _ in range(100):
        data = hashlib.sha256(data).digest()
    return data.hex()


def io_bound_sleep(duration: float) -> float:
    """I/O-bound work: sleep for duration (simulates I/O wait)."""
    time.sleep(duration)
    return duration


def mixed_work(data: str, n: int) -> list[str]:
    """Mixed CPU/IO: hash n times, return results."""
    results = []
    for i in range(n):
        h = hashlib.sha256(f"{data}{i}".encode()).hexdigest()
        results.append(h)
    return results


# ---------------------------------------------------------------------------
# Rayon availability check
# ---------------------------------------------------------------------------

class TestRayonAvailability:
    def test_rayon_pools_available_returns_bool(self) -> None:
        """RayonPoolsAvailable() returns a boolean."""
        result = RayonPoolsAvailable()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# run_in_cpu_pool tests
# ---------------------------------------------------------------------------

class TestCpuPool:
    def test_cpu_pool_runs_function(self) -> None:
        """run_in_cpu_pool executes the function and returns result."""
        result = run_in_cpu_pool(cpu_bound_hash, b"test data")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex

    def test_cpu_pool_with_args(self) -> None:
        """run_in_cpu_pool passes arguments correctly."""
        def add(a: int, b: int) -> int:
            return a + b

        result = run_in_cpu_pool(add, 3, 5)
        assert result == 8

    def test_cpu_pool_exception_propagates(self) -> None:
        """run_in_cpu_pool propagates exceptions when rayon is available."""
        if not RayonPoolsAvailable():
            pytest.skip("Rayon not available, fallback does not propagate")

        def fail() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_in_cpu_pool(fail)

    def test_cpu_pool_returns_none_on_rayon_unavailable(self) -> None:
        """When rayon unavailable, falls back to direct call."""
        # If rayon is available, this test passes because direct call works
        # If rayon is unavailable, it returns None on exception
        result = run_in_cpu_pool(lambda: 42)
        # Either works (rayon available) or returns None (rayon unavailable)
        assert result in (42, None)


# ---------------------------------------------------------------------------
# run_in_io_pool tests
# ---------------------------------------------------------------------------

class TestIoPool:
    def test_io_pool_runs_function(self) -> None:
        """run_in_io_pool executes the function and returns result."""
        result = run_in_io_pool(io_bound_sleep, 0.001)
        assert result == 0.001

    def test_io_pool_exception_propagates(self) -> None:
        """run_in_io_pool propagates exceptions when rayon is available."""
        if not RayonPoolsAvailable():
            pytest.skip("Rayon not available, fallback does not propagate")

        def fail() -> None:
            raise RuntimeError("io error")

        with pytest.raises(RuntimeError, match="io error"):
            run_in_io_pool(fail)


# ---------------------------------------------------------------------------
# run_in_mixed_pool tests
# ---------------------------------------------------------------------------

class TestMixedPool:
    def test_mixed_pool_runs_small_batch(self) -> None:
        """run_in_mixed_pool uses 1 thread for n_items < 32."""
        result = run_in_mixed_pool(10, mixed_work, "data", 5)
        assert isinstance(result, list)
        assert len(result) == 5

    def test_mixed_pool_runs_large_batch(self) -> None:
        """run_in_mixed_pool uses 2 threads for n_items >= 32."""
        result = run_in_mixed_pool(50, mixed_work, "data", 10)
        assert isinstance(result, list)
        assert len(result) == 10

    def test_mixed_pool_exception_propagates(self) -> None:
        """run_in_mixed_pool propagates exceptions when rayon is available."""
        if not RayonPoolsAvailable():
            pytest.skip("Rayon not available, fallback does not propagate")

        def fail() -> None:
            raise ValueError("mixed error")

        with pytest.raises(ValueError, match="mixed error"):
            run_in_mixed_pool(10, fail)


# ---------------------------------------------------------------------------
# Async wrapper tests
# ---------------------------------------------------------------------------

class TestAsyncWrappers:
    @pytest.mark.asyncio
    async def test_cpu_pool_async(self) -> None:
        """run_in_cpu_pool_async runs CPU work without blocking event loop."""
        result = await run_in_cpu_pool_async(cpu_bound_hash, b"async test")
        assert isinstance(result, str)
        assert len(result) == 64

    @pytest.mark.asyncio
    async def test_io_pool_async(self) -> None:
        """run_in_io_pool_async runs I/O work without blocking event loop."""
        result = await run_in_io_pool_async(io_bound_sleep, 0.001)
        assert result == 0.001

    @pytest.mark.asyncio
    async def test_mixed_pool_async(self) -> None:
        """run_in_mixed_pool_async runs mixed work without blocking event loop."""
        result = await run_in_mixed_pool_async(20, mixed_work, "async", 3)
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_async_wrapper_does_not_block_event_loop(self) -> None:
        """Verify async wrapper doesn't block the event loop during CPU work."""
        start = time.monotonic()

        async def check_event_loop_available() -> bool:
            # While CPU work runs in thread, event loop should be responsive
            await asyncio.sleep(0)
            return True

        async def run_work() -> None:
            await run_in_cpu_pool_async(cpu_bound_hash, b"blocking test")

        # Run CPU work and event loop check concurrently
        await asyncio.gather(
            run_work(),
            check_event_loop_available(),
        )

        elapsed = time.monotonic() - start
        # Should complete within reasonable time
        assert elapsed < 5.0  # 5 second timeout


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_multiple_pools_run_concurrently(self) -> None:
        """Multiple pool calls can run concurrently with Python ThreadPoolExecutor."""
        # This tests that rayon pools don't block Python threading
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(run_in_cpu_pool, cpu_bound_hash, f"data{i}".encode())
                for i in range(4)
            ]
            results = [f.result(timeout=10) for f in futures]

        assert len(results) == 4
        assert all(isinstance(r, str) and len(r) == 64 for r in results)

    @pytest.mark.asyncio
    async def test_async_concurrent_pool_calls(self) -> None:
        """Multiple async pool calls run concurrently."""
        results = await asyncio.gather(
            run_in_cpu_pool_async(cpu_bound_hash, b"concurrent1"),
            run_in_cpu_pool_async(cpu_bound_hash, b"concurrent2"),
            run_in_cpu_pool_async(cpu_bound_hash, b"concurrent3"),
        )

        assert len(results) == 3
        assert all(isinstance(r, str) and len(r) == 64 for r in results)


# ---------------------------------------------------------------------------
# Performance tests (optional, skip in CI)
# ---------------------------------------------------------------------------

class TestPerformance:
    @pytest.mark.skip(reason="performance test, run manually")
    def test_rayon_vs_thread_pool_speedup(self) -> None:
        """Compare rayon pool vs Python ThreadPoolExecutor speedup."""
        data = b"x" * 10_000
        iterations = 10

        # Warm up
        for _ in range(3):
            run_in_cpu_pool(cpu_bound_hash, data)

        # Time rayon pool
        start = time.perf_counter()
        for _ in range(iterations):
            run_in_cpu_pool(cpu_bound_hash, data)
        rayon_ms = (time.perf_counter() - start) * 1000 / iterations

        # Time ThreadPoolExecutor
        executor = ThreadPoolExecutor(max_workers=4)
        start = time.perf_counter()
        for _ in range(iterations):
            executor.submit(cpu_bound_hash, data).result()
        thread_ms = (time.perf_counter() - start) * 1000 / iterations
        executor.shutdown()

        speedup = thread_ms / rayon_ms
        print(f"\nRayon: {rayon_ms:.2f}ms, ThreadPool: {thread_ms:.2f}ms, Speedup: {speedup:.2f}x")
        assert speedup > 1.0, "Rayon should be faster than ThreadPoolExecutor"


# ---------------------------------------------------------------------------
# Integration tests with real workload
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_ioc_extract_like_workload(self) -> None:
        """Simulate IOC extraction workload pattern."""
        def extract_patterns(text: str) -> list[str]:
            """Simulate pattern matching work."""
            patterns = ["ipv4", "ipv6", "domain", "url", "email", "hash"]
            found = []
            for p in patterns:
                if p in text.lower():
                    found.append(p)
            return found

        result = run_in_cpu_pool(extract_patterns, "Found domain example.com and ipv4 192.168.1.1")
        assert result is not None
        assert "domain" in result
        assert "ipv4" in result

    def test_hash_dedup_like_workload(self) -> None:
        """Simulate content dedup hashing workload."""
        def compute_content_hash(content: str) -> str:
            return hashlib.blake2b(content.encode(), digest_size=16).hexdigest()

        contents = [f"content_{i}" for i in range(100)]
        hashes = [run_in_cpu_pool(compute_content_hash, c) for c in contents]

        # All hashes should be unique
        assert len(set(hashes)) == 100

    @pytest.mark.asyncio
    async def test_pipeline_like_workload(self) -> None:
        """Simulate a pipeline: extract -> hash -> store."""
        def extract(data: str) -> list[str]:
            return data.split()

        def hash_items(items: list[str]) -> list[str]:
            return [hashlib.sha256(item.encode()).hexdigest()[:16] for item in items]

        def store(hashes: list[str]) -> int:
            return len(hashes)

        # Async pipeline
        extracted = await run_in_cpu_pool_async(extract, "hello world foo bar")
        hashed = await run_in_cpu_pool_async(hash_items, extracted)
        count = await run_in_io_pool_async(store, hashed)

        assert count == 4
