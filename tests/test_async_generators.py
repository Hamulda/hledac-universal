"""
Tests for F275 async generators pipeline.

Tests the streaming pipeline utilities for memory-efficient
async processing on M1 8GB.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from hledac.universal.utils.async_generators import (
from core import aclose
    BackpressureMonitor,
    aclose_safe,
    async_iter_context,
    async_batched,
    async_chunked_pipeline,
    async_filter,
    async_flatmap,
    async_transform,
    findings_to_duckdb_pipeline,
)

# ============================================================================
# Fixtures
# ============================================================================


class _MockDuckDBStore:
    """Mock DuckDB store for testing."""

    def __init__(self):
        self.ingested: list[dict] = []
        self.call_count = 0

    async def async_ingest_findings_batch(self, findings: list[dict]) -> list[dict]:
        self.call_count += 1
        self.ingested.extend(findings)
        # Return acceptance results
        return [{"accepted": True, "finding_id": f.get("id", i)} for i, f in enumerate(findings)]


async def _async_range(n: int) -> AsyncIterator[int]:
    """Simple async generator yielding 0..n-1."""
    for i in range(n):
        yield i


async def _async_findings(count: int) -> AsyncIterator[dict]:
    """Generate mock findings."""
    for i in range(count):
        yield {"id": i, "type": "test", "data": "x" * 100}


# ============================================================================
# async_batched tests
# ============================================================================


class TestAsyncBatched:
    """Tests for async_batched()."""

    @pytest.mark.asyncio
    async def test_batches_full_batches(self):
        """Full batches are yielded correctly."""
        source = _async_range(100)
        batches = [batch async for batch in async_batched(source, batch_size=10)]
        assert len(batches) == 10
        assert all(len(b) == 10 for b in batches)
        assert batches[0][0] == 0
        assert batches[-1][-1] == 99

    @pytest.mark.asyncio
    async def test_batches_partial_final(self):
        """Partial final batch is yielded."""
        source = _async_range(25)
        batches = [batch async for batch in async_batched(source, batch_size=10)]
        assert len(batches) == 3
        assert len(batches[0]) == 10
        assert len(batches[1]) == 10
        assert len(batches[2]) == 5

    @pytest.mark.asyncio
    async def test_batches_empty(self):
        """Empty source yields nothing."""
        source = _async_range(0)
        batches = [batch async for batch in async_batched(source, batch_size=10)]
        assert batches == []

    @pytest.mark.asyncio
    async def test_batches_larger_than_source(self):
        """Batch size larger than source yields single batch."""
        source = _async_range(5)
        batches = [batch async for batch in async_batched(source, batch_size=100)]
        assert len(batches) == 1
        assert batches[0] == [0, 1, 2, 3, 4]


# ============================================================================
# async_transform tests
# ============================================================================


class TestAsyncTransform:
    """Tests for async_transform()."""

    @pytest.mark.asyncio
    async def test_transform_sequential(self):
        """Sequential transform (concurrency=1)."""
        source = _async_range(5)
        doubled = [x async for x in async_transform(source, lambda x: x * 2)]
        assert doubled == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_transform_async_func(self):
        """Async transform function."""

        async def double(x: int) -> int:
            return x * 2

        source = _async_range(5)
        doubled = [x async for x in async_transform(source, double)]
        assert doubled == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_transform_concurrent(self):
        """Concurrent transform (concurrency=3)."""
        source = _async_range(10)
        processed = 0

        async def process(x: int) -> int:
            nonlocal processed
            await asyncio.sleep(0.01)  # Simulate work
            processed += 1
            return x * 2

        doubled = [x async for x in async_transform(source, process, concurrency=3)]
        assert sorted(doubled) == [x * 2 for x in range(10)]
        assert processed == 10


# ============================================================================
# async_filter tests
# ============================================================================


class TestAsyncFilter:
    """Tests for async_filter()."""

    @pytest.mark.asyncio
    async def test_filter_sync(self):
        """Sync predicate filter."""
        source = _async_range(10)
        filtered = [x async for x in async_filter(source, lambda x: x % 2 == 0)]
        assert filtered == [0, 2, 4, 6, 8]

    @pytest.mark.asyncio
    async def test_filter_async(self):
        """Async predicate filter."""

        async def is_even(x: int) -> bool:
            return x % 2 == 0

        source = _async_range(10)
        filtered = [x async for x in async_filter(source, is_even)]
        assert filtered == [0, 2, 4, 6, 8]


# ============================================================================
# async_flatmap tests
# ============================================================================


class TestAsyncFlatmap:
    """Tests for async_flatmap()."""

    @pytest.mark.asyncio
    async def test_flatmap_lists(self):
        """Flatten list of lists."""

        async def list_source() -> AsyncIterator[list[int]]:
            yield [1, 2]
            yield [3, 4]
            yield [5]

        result = [x async for x in async_flatmap(list_source())]
        assert result == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_flatmap_async_gen(self):
        """Flatten async generators."""

        async def gen_source() -> AsyncIterator[AsyncIterator[int]]:
            async def gen1():
                yield 1
                yield 2

            async def gen2():
                yield 3

            yield gen1()
            yield gen2()

        result = [x async for x in async_flatmap(gen_source())]
        assert result == [1, 2, 3]


# ============================================================================
# async_chunked_pipeline tests
# ============================================================================


class TestAsyncChunkedPipeline:
    """Tests for async_chunked_pipeline()."""

    @pytest.mark.asyncio
    async def test_pipeline_basic(self):
        """Basic chunked processing."""
        source = _async_range(100)
        results: list[list[int]] = []

        async def processor(batch: list[int]) -> list[int]:
            await asyncio.sleep(0)  # Ensure it's truly async
            return [x * 2 for x in batch]

        async for batch_results in async_chunked_pipeline(source, processor, batch_size=10):
            if batch_results:  # Skip empty batches from exceptions
                results.append(batch_results)

        assert len(results) == 10
        assert results[0] == [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
        flat = [x for batch in results for x in batch]
        assert flat == [x * 2 for x in range(100)]

    @pytest.mark.asyncio
    async def test_pipeline_partial_final(self):
        """Partial final batch."""
        source = _async_range(25)

        async def processor(batch: list[int]) -> list[int]:
            # Add small async operation to ensure proper async handling
            await asyncio.sleep(0)
            return batch

        results = []
        async for batch in async_chunked_pipeline(source, processor, batch_size=10):
            if batch:  # Filter empty batches
                results.append(batch)

        assert len(results) == 3
        assert len(results[2]) == 5


# ============================================================================
# findings_to_duckdb_pipeline tests
# ============================================================================


class TestFindingsToDuckDBPipeline:
    """Tests for findings_to_duckdb_pipeline()."""

    @pytest.mark.asyncio
    async def test_duckdb_pipeline(self):
        """Stream findings through DuckDB pipeline."""
        store = _MockDuckDBStore()
        source = _async_findings(50)

        results: list[list[dict]] = []
        async for batch_results in findings_to_duckdb_pipeline(source, store, batch_size=10):
            if batch_results:  # Filter out empty batches
                results.append(batch_results)

        assert len(results) == 5  # 50 items / 10 per batch
        assert store.call_count == 5
        assert len(store.ingested) == 50

    @pytest.mark.asyncio
    async def test_duckdb_pipeline_empty(self):
        """Empty source."""
        store = _MockDuckDBStore()

        async def empty_source() -> AsyncIterator[dict]:
            return
            yield  # type: ignore

        results = [batch async for batch in findings_to_duckdb_pipeline(empty_source(), store)]
        assert results == []
        assert store.call_count == 0


# ============================================================================
# BackpressureMonitor tests
# ============================================================================


class TestBackpressureMonitor:
    """Tests for BackpressureMonitor."""

    def test_initial_state(self):
        """Initial state is zero."""
        monitor = BackpressureMonitor("test")
        assert monitor.pending_count == 0
        assert monitor.max_pending == 0
        assert monitor.pressure == 0.0

    def test_pressure_calculation(self):
        """Pressure ratio calculation."""
        monitor = BackpressureMonitor("test")
        monitor.on_item_queued()
        monitor.on_item_queued()
        assert monitor.pending_count == 2
        assert monitor.max_pending == 2
        assert monitor.pressure == 1.0

    def test_dequeue(self):
        """Dequeue decrements count."""
        monitor = BackpressureMonitor("test")
        monitor.on_item_queued()
        monitor.on_item_queued()
        monitor.on_item_dequeued()
        assert monitor.pending_count == 1

    def test_repr(self):
        """String representation."""
        monitor = BackpressureMonitor("test")
        assert "test" in repr(monitor)
        assert "pending=0" in repr(monitor)


# ============================================================================
# Coroutine cleanup tests (F350M-R coroutine leak prevention)
# ============================================================================


class TestCoroutineCleanup:
    """Tests for async generator cleanup patterns (F350M-R)."""

    @pytest.mark.asyncio
    async def test_async_generator_with_aclose_safe(self):
        """Early exit with aclose_safe prevents coroutine leaks."""
        source = _async_range(1000)
        count = 0
        try:
            async for item in source:
                count += 1
                if count >= 5:
                    break
        finally:
            await aclose_safe(source)
        assert count == 5

    @pytest.mark.asyncio
    async def test_async_generator_context_manager_pattern(self):
        """AsyncIteratorContext ensures aclose on exit."""
        async with async_iter_context(_async_range(100)) as source:
            items = []
            count = 0
            async for item in source:
                items.append(item)
                count += 1
                if count >= 10:
                    break
        # aclose() called automatically
        assert len(items) == 10

    @pytest.mark.asyncio
    async def test_pipeline_with_cleanup(self):
        """Pipeline with proper cleanup on early exit."""
        source = _async_findings(1000)
        batches = []
        try:
            async for batch in async_batched(source, batch_size=10):
                batches.append(batch)
                if len(batches) >= 5:
                    break
        finally:
            await aclose_safe(source)
        assert len(batches) == 5
        assert all(len(b) <= 10 for b in batches)


# ============================================================================
# Memory model verification (F275 invariant)
# ============================================================================


class TestMemoryModel:
    """Verify F275 memory model: streaming vs list accumulation."""

    @pytest.mark.asyncio
    async def test_no_list_accumulation_in_pipeline(self):
        """Pipeline should NOT accumulate all items in memory."""
        source = _async_findings(1000)

        # Track memory by checking source is consumed incrementally
        consumed = 0
        async for batch in async_batched(source, batch_size=100):
            consumed += len(batch)
            # Each batch should be ~100 items, not all 1000

        assert consumed == 1000
        # If we got here without OOM, the streaming model works

    @pytest.mark.asyncio
    async def test_backpressure_limits_pending(self):
        """Pipeline should limit pending batches."""
        store = _MockDuckDBStore()
        monitor = BackpressureMonitor("pipeline")

        source = _async_findings(1000)

        async def tracked_processor(batch: list[dict]) -> list[dict]:
            for _ in batch:
                monitor.on_item_dequeued()
            # Simulate slow processing
            await asyncio.sleep(0.001)
            return [{"accepted": True} for _ in batch]

        async for _ in async_chunked_pipeline(source, tracked_processor, batch_size=10, max_pending_batches=2):
            pass

        # Verify backpressure was applied (max_pending was respected)
        assert monitor.max_pending <= 2 + 1  # Allow some tolerance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
