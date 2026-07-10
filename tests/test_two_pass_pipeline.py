"""
Tests for utils/two_pass_pipeline.py — Issue 2.5

Covers:
- Single TaskGroup with producer → Queue → consumer pipeline
- Backpressure when queue is full (maxsize=512)
- PEP 634 structural pattern matching in consumer
- Fail-safe: returns [] on error
- M1 8GB invariants: bounded queue, no unbounded growth
"""


import asyncio
from typing import Any

import pytest

from hledac.universal.utils.two_pass_pipeline import (
    PipelineStats,
    TwoPassPipeline,
    TwoPassPipelineConfig,
    consumer_fn_to_thread,
)


class TestTwoPassPipeline:
    """Test TwoPassPipeline: single TaskGroup with queue backpressure."""

    @pytest.mark.asyncio
    async def test_basic_pipeline(self) -> None:
        """Producer items → consumer → results."""

        async def producer() -> list[int]:
            return [1, 2, 3, 4, 5]

        def consumer(x: int) -> int:
            return x * 2

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=consumer,
        )
        results = await pipeline.run()
        assert sorted(results) == [2, 4, 6, 8, 10]
        assert pipeline.stats.produced == 5
        assert pipeline.stats.consumed == 5

    @pytest.mark.asyncio
    async def test_empty_producer(self) -> None:
        """Empty producer returns empty list."""
        pipeline = TwoPassPipeline(
            producer_coro=asyncio.sleep(0, []),
            consumer_fn=lambda x: x,
        )
        results = await pipeline.run()
        assert results == []

    @pytest.mark.asyncio
    async def test_consumer_concurrency(self) -> None:
        """Multiple consumer workers drain the queue concurrently."""

        async def producer() -> list[int]:
            return list(range(100))

        def consumer(x: int) -> int:
            return x

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=consumer,
            config=TwoPassPipelineConfig(
                consumer_concurrency=4,
                queue_size=16,
            ),
        )
        results = await pipeline.run()
        assert len(results) == 100
        assert pipeline.stats.consumed == 100

    @pytest.mark.asyncio
    async def test_backpressure_queue_size(self) -> None:
        """Queue respects maxsize=512 (never grows beyond bound)."""
        # Use a trivial async producer that completes immediately
        async def producer() -> list[int]:
            return list(range(1000))

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=lambda x: x,
            config=TwoPassPipelineConfig(queue_size=512),
        )
        # Queue should never exceed 512
        assert pipeline._queue.maxsize == 512
        await pipeline.run()

    @pytest.mark.asyncio
    async def test_fail_safe_on_consumer_error(self) -> None:
        """Consumer error returns partial results, not exception."""

        async def producer() -> list[int]:
            return [1, 2, 3, 4, 5]

        def consumer(x: int) -> int:
            if x == 3:
                raise ValueError("bad")
            return x * 2

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=consumer,
            config=TwoPassPipelineConfig(consumer_concurrency=2),
        )
        results = await pipeline.run()
        assert len(results) <= 5
        assert pipeline.stats.consumer_errors >= 1

    @pytest.mark.asyncio
    async def test_pep634_structural_match_on_dict(self) -> None:
        """PEP 634 structural pattern match classifies dict items."""

        async def producer() -> list[dict[str, Any]]:
            return [
                {"type": "arxiv", "id": "1"},
                {"type": "crossref", "id": "2"},
                {"other": "data"},
            ]

        seen_types: list[str] = []

        def consumer(item: dict[str, Any]) -> dict[str, Any]:
            # PEP 634 structural pattern match
            match item:
                case {"type": t} if t in ("arxiv", "crossref"):
                    seen_types.append(t)
                case {"other": _}:
                    seen_types.append("other")
            return item

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=consumer,
        )
        await pipeline.run()
        assert "arxiv" in seen_types
        assert "crossref" in seen_types
        assert "other" in seen_types

    @pytest.mark.asyncio
    async def test_streaming_variant(self) -> None:
        """Streaming pipeline feeds items as they arrive."""

        async def producer() -> list[int]:
            return [10, 20, 30]

        def consumer(x: int) -> int:
            return x + 1

        pipeline = TwoPassPipeline(
            producer_coro=producer(),
            consumer_fn=consumer,
        )
        results = await pipeline.run()
        assert sorted(results) == [11, 21, 31]

    @pytest.mark.asyncio
    async def test_stats_tracking(self) -> None:
        """Pipeline stats are tracked correctly."""
        pipeline = TwoPassPipeline(
            producer_coro=asyncio.sleep(0, [1, 2, 3]),
            consumer_fn=lambda x: x,
            config=TwoPassPipelineConfig(label="test"),
        )
        await pipeline.run()
        stats = pipeline.stats
        assert stats.produced == 3
        assert stats.consumed == 3
        assert stats.producer_errors == 0
        assert stats.consumer_errors == 0
        d = stats.to_dict()
        assert d["produced"] == 3
        assert d["consumed"] == 3


class TestConsumerFnToThread:
    """Test consumer_fn_to_thread: GIL-released CPU-bound work."""

    @pytest.mark.asyncio
    async def test_basic(self) -> None:
        """CPU-bound function runs and releases GIL."""

        def cpu_heavy(x: int) -> int:
            return x * 2

        results = await consumer_fn_to_thread(cpu_heavy, [1, 2, 3], batch_size=2)
        assert sorted(results) == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        """Empty input returns empty list."""
        results = await consumer_fn_to_thread(lambda x: x, [])
        assert results == []

    @pytest.mark.asyncio
    async def test_error_suppression(self) -> None:
        """Errors in consumer are suppressed, not raised."""
        def bad(x: int) -> int:
            if x == 2:
                raise RuntimeError("skip")
            return x

        results = await consumer_fn_to_thread(bad, [1, 2, 3])
        assert 1 in results
        assert 3 in results
        assert 2 not in results

    @pytest.mark.asyncio
    async def test_large_batch_chunking(self) -> None:
        """Large batches are chunked correctly."""

        def identity(x: int) -> int:
            return x

        large_list = list(range(200))
        results = await consumer_fn_to_thread(identity, large_list, batch_size=64)
        assert len(results) == 200
        assert results == large_list
