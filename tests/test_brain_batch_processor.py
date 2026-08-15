"""
tests/test_brain_batch_processor.py — BatchProcessor Unit Tests
==============================================================

Dedikované testy pro brain/_batch/batch_processor.py.
Testuje: BatchProcessor, BatchItem, BatchConfig, BatchStats, BatchPriority.

M1 8GB invariant: Adaptive batch sizing by memory pressure.
"""

from __future__ import annotations

import asyncio
import pytest
import time
from core import aclose


class TestBatchPriority:
    """Test BatchPriority enum."""

    def test_high_priority_value(self) -> None:
        """Test HIGH priority has highest value."""
        from brain._batch.batch_processor import BatchPriority

        assert BatchPriority.HIGH.value == 1.0

    def test_medium_priority_value(self) -> None:
        """Test MEDIUM priority value."""
        from brain._batch.batch_processor import BatchPriority

        assert BatchPriority.MEDIUM.value == 0.5

    def test_low_priority_value(self) -> None:
        """Test LOW priority has lowest value."""
        from brain._batch.batch_processor import BatchPriority

        assert BatchPriority.LOW.value == 0.25

    def test_priority_ordering(self) -> None:
        """Test priority enum ordering."""
        from brain._batch.batch_processor import BatchPriority

        assert BatchPriority.HIGH.value > BatchPriority.MEDIUM.value
        assert BatchPriority.MEDIUM.value > BatchPriority.LOW.value


class TestBatchItem:
    """Test BatchItem dataclass."""

    def test_batch_item_default(self) -> None:
        """Test BatchItem with default values."""
        from brain._batch.batch_processor import BatchItem

        item = BatchItem(id="test-1", prompt="Hello")
        assert item.id == "test-1"
        assert item.prompt == "Hello"
        assert item.response_model is None
        assert item.priority == 0.5
        assert item.timeout == 30.0
        assert item.created_at > 0
        assert item.future is None

    def test_batch_item_full(self) -> None:
        """Test BatchItem with all fields."""
        from brain._batch.batch_processor import BatchItem

        item = BatchItem(
            id="test-2",
            prompt="Hello",
            response_model=str,
            priority=1.0,
            timeout=60.0,
            created_at=1234567890.0,
            future=None,
        )
        assert item.id == "test-2"
        assert item.response_model is str
        assert item.priority == 1.0
        assert item.timeout == 60.0
        assert item.created_at == 1234567890.0

    def test_batch_item_lt(self) -> None:
        """Test BatchItem priority comparison (higher priority first)."""
        from brain._batch.batch_processor import BatchItem

        item_high = BatchItem(id="high", prompt="H", priority=1.0)
        item_low = BatchItem(id="low", prompt="L", priority=0.25)

        # Higher priority should sort as "less than" (for heapq)
        assert item_high < item_low


class TestBatchConfig:
    """Test BatchConfig dataclass."""

    def test_batch_config_defaults(self) -> None:
        """Test BatchConfig default values."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig()
        assert config.max_size == 10
        assert config.default_flush_interval == 0.5
        assert config.high_pressure_max_size == 3
        assert config.medium_pressure_max_size == 6
        assert config.age_bump_interval == 5.0
        assert config.tie_breaker == 1e-6

    def test_batch_config_custom(self) -> None:
        """Test BatchConfig with custom values."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig(
            max_size=20,
            default_flush_interval=1.0,
            high_pressure_max_size=5,
            medium_pressure_max_size=10,
        )
        assert config.max_size == 20
        assert config.default_flush_interval == 1.0
        assert config.high_pressure_max_size == 5


class TestBatchStats:
    """Test BatchStats dataclass."""

    def test_batch_stats_defaults(self) -> None:
        """Test BatchStats default values."""
        from brain._batch.batch_processor import BatchStats

        stats = BatchStats()
        assert stats.processed_count == 0
        assert stats.failed_count == 0
        assert stats.queue_depth == 0
        assert stats.avg_latency_ms == 0.0
        assert stats.last_flush_at == 0.0

    def test_batch_stats_custom(self) -> None:
        """Test BatchStats with custom values."""
        from brain._batch.batch_processor import BatchStats

        stats = BatchStats(
            processed_count=100,
            failed_count=5,
            queue_depth=10,
            avg_latency_ms=150.5,
            last_flush_at=1234567890.0,
        )
        assert stats.processed_count == 100
        assert stats.failed_count == 5
        assert stats.queue_depth == 10


class TestBatchProcessorInit:
    """Test BatchProcessor initialization."""

    def test_init_default_config(self) -> None:
        """Test BatchProcessor with default config."""
        from brain._batch.batch_processor import BatchProcessor

        processor = BatchProcessor()
        assert processor._config.max_size == 10
        assert processor._queue == []
        assert processor._worker_task is None
        assert processor._shutting_down is False

    def test_init_custom_config(self) -> None:
        """Test BatchProcessor with custom config."""
        from brain._batch.batch_processor import BatchProcessor, BatchConfig

        config = BatchConfig(max_size=5)
        processor = BatchProcessor(config)
        assert processor._config.max_size == 5


class TestBatchProcessorQueue:
    """Test BatchProcessor queue operations."""

    @pytest.mark.asyncio
    async def test_submit_increases_queue_depth(self) -> None:
        """Test submit() adds item to queue."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem

        processor = BatchProcessor()
        item = BatchItem(id="test-1", prompt="Hello")

        await processor.submit(item)

        assert len(processor._queue) == 1
        assert processor._queue[0].id == "test-1"

    @pytest.mark.asyncio
    async def test_submit_multiple_items(self) -> None:
        """Test multiple submit() calls."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem

        processor = BatchProcessor()
        for i in range(5):
            item = BatchItem(id=f"test-{i}", prompt=f"Prompt {i}")
            await processor.submit(item)

        assert len(processor._queue) == 5

    @pytest.mark.asyncio
    async def test_submit_adds_to_queue(self) -> None:
        """Test submit() adds item to queue (max_size enforced by worker, not submit)."""
        from brain._batch.batch_processor import BatchProcessor, BatchConfig, BatchItem

        config = BatchConfig(max_size=3)
        processor = BatchProcessor(config)

        for i in range(5):
            item = BatchItem(id=f"test-{i}", prompt=f"Prompt {i}")
            await processor.submit(item)

        # submit() always adds - max_size is enforced by worker during flush
        assert len(processor._queue) == 5
        assert all(isinstance(item, BatchItem) for item in processor._queue)


class TestBatchProcessorStats:
    """Test BatchProcessor statistics."""

    def test_stats_initial(self) -> None:
        """Test initial stats are zeroed."""
        from brain._batch.batch_processor import BatchProcessor

        processor = BatchProcessor()
        assert processor._stats.processed_count == 0
        assert processor._stats.failed_count == 0
        assert processor._stats.queue_depth == 0

    @pytest.mark.asyncio
    async def test_stats_after_submit(self) -> None:
        """Test stats update after submit."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem

        processor = BatchProcessor()
        item = BatchItem(id="test-1", prompt="Hello")
        await processor.submit(item)

        stats = processor.get_stats()
        assert stats.queue_depth == 1


class TestBatchProcessorShutdown:
    """Test BatchProcessor shutdown behavior."""

    def test_shutting_down_default_false(self) -> None:
        """Test _shutting_down is False initially."""
        from brain._batch.batch_processor import BatchProcessor

        processor = BatchProcessor()
        assert processor._shutting_down is False

    @pytest.mark.asyncio
    async def test_shutdown_sets_flag(self) -> None:
        """Test shutdown() sets _shutting_down flag."""
        from brain._batch.batch_processor import BatchProcessor

        processor = BatchProcessor()
        await processor.shutdown()
        assert processor._shutting_down is True


class TestBatchProcessorM1Bounds:
    """M1 8GB invariant tests."""

    def test_default_max_size_is_bounded(self) -> None:
        """INVARIANT: default max_size must be reasonable for M1 8GB."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig()
        # Should not be unbounded
        assert config.max_size > 0
        assert config.max_size <= 100  # Reasonable upper bound for M1

    def test_high_pressure_max_smaller_than_default(self) -> None:
        """INVARIANT: high pressure max_size < default max_size."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig()
        assert config.high_pressure_max_size < config.max_size

    def test_medium_pressure_max_between_high_and_default(self) -> None:
        """INVARIANT: medium pressure max_size is between high and default."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig()
        assert config.high_pressure_max_size <= config.medium_pressure_max_size
        assert config.medium_pressure_max_size <= config.max_size


class TestBatchItemRetryCount:
    """F-05: Test BatchItem retry_count field."""

    def test_retry_count_default_zero(self) -> None:
        """F-05: retry_count defaults to 0."""
        from brain._batch.batch_processor import BatchItem

        item = BatchItem(id="test-1", prompt="Hello")
        assert item.retry_count == 0

    def test_retry_count_can_be_set(self) -> None:
        """F-05: retry_count can be set."""
        from brain._batch.batch_processor import BatchItem

        item = BatchItem(id="test-1", prompt="Hello", retry_count=2)
        assert item.retry_count == 2


class TestBatchConfigMaxItemRetries:
    """F-05: Test max_item_retries config."""

    def test_max_item_retries_default(self) -> None:
        """F-05: max_item_retries defaults to 2."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig()
        assert config.max_item_retries == 2

    def test_max_item_retries_custom(self) -> None:
        """F-05: max_item_retries can be customized."""
        from brain._batch.batch_processor import BatchConfig

        config = BatchConfig(max_item_retries=5)
        assert config.max_item_retries == 5


class TestBatchStatsRetryFields:
    """F-05: Test BatchStats retry fields."""

    def test_retried_count_default(self) -> None:
        """F-05: retried_count defaults to 0."""
        from brain._batch.batch_processor import BatchStats

        stats = BatchStats()
        assert stats.retried_count == 0

    def test_sharded_count_default(self) -> None:
        """F-05: sharded_count defaults to 0."""
        from brain._batch.batch_processor import BatchStats

        stats = BatchStats()
        assert stats.sharded_count == 0

    def test_retried_count_in_stats(self) -> None:
        """F-05: get_stats includes retried_count."""
        from brain._batch.batch_processor import BatchProcessor

        processor = BatchProcessor()
        stats = processor.get_stats()
        assert stats.retried_count == 0
        assert stats.sharded_count == 0


class TestShardRetryIntegration:
    """F-05: Integration tests for shard retry behavior."""

    @pytest.mark.asyncio
    async def test_successful_batch_no_retry(self) -> None:
        """F-05: Successful batch doesn't trigger shard retry."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem, BatchConfig

        class TestProcessor(BatchProcessor):
            async def _process_batch(self, items):
                return [{"ok": True}] * len(items)

        config = BatchConfig(max_item_retries=2)
        processor = TestProcessor(config)
        item = BatchItem(id="test-1", prompt="Hello")

        future = await processor.submit(item)
        result = await future

        stats = processor.get_stats()
        assert stats.sharded_count == 0
        assert stats.retried_count == 0
        assert result == {"ok": True}
        await processor.shutdown()

    @pytest.mark.asyncio
    async def test_batch_exception_triggers_shard_retry(self) -> None:
        """F-05: Batch exception triggers shard retry."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem, BatchConfig

        class FailingBatchProcessor(BatchProcessor):
            async def _process_batch(self, items):
                raise RuntimeError("Batch failure")

            async def _process_single(self, item):
                return {"item": item.id, "retried": True}

        config = BatchConfig(max_item_retries=2)
        processor = FailingBatchProcessor(config)
        item = BatchItem(id="test-1", prompt="Hello")

        future = await processor.submit(item)
        result = await future

        stats = processor.get_stats()
        assert stats.sharded_count == 1
        assert stats.retried_count == 0  # Single item succeeded on first individual try
        assert result == {"item": "test-1", "retried": True}
        await processor.shutdown()

    @pytest.mark.asyncio
    async def test_item_exhausted_retries(self) -> None:
        """F-05: Item exhausting retries sets failed_count."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem, BatchConfig

        class AlwaysFailProcessor(BatchProcessor):
            async def _process_batch(self, items):
                raise RuntimeError("Batch failure")

            async def _process_single(self, item):
                raise ValueError(f"Item {item.id} always fails")

        config = BatchConfig(max_item_retries=2)
        processor = AlwaysFailProcessor(config)
        item = BatchItem(id="test-1", prompt="Hello")

        future = await processor.submit(item)

        # Should get exception after exhausting retries
        with pytest.raises(ValueError, match="Item test-1 always fails"):
            await future

        stats = processor.get_stats()
        assert stats.sharded_count == 1
        assert stats.retried_count == 2  # 2 retries after initial failure
        assert stats.failed_count == 1
        await processor.shutdown()

    @pytest.mark.asyncio
    async def test_flush_uses_shard_retry(self) -> None:
        """F-05: flush() also uses shard retry."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem, BatchConfig

        class FailingFlushProcessor(BatchProcessor):
            async def _process_batch(self, items):
                raise RuntimeError("Flush batch failure")

            async def _process_single(self, item):
                return {"flushed": item.id}

        config = BatchConfig(max_item_retries=1)
        processor = FailingFlushProcessor(config)
        item = BatchItem(id="test-1", prompt="Hello")
        await processor.submit(item)

        processed = await processor.flush()

        stats = processor.get_stats()
        assert stats.sharded_count == 1
        assert processed == 1  # Item processed individually
        await processor.shutdown()

    @pytest.mark.asyncio
    async def test_retry_count_increments_per_attempt(self) -> None:
        """F-05: retry_count increments on each retry attempt."""
        from brain._batch.batch_processor import BatchProcessor, BatchItem, BatchConfig

        attempt_counts: dict[str, int] = {}

        class CountingRetryProcessor(BatchProcessor):
            async def _process_batch(self, items):
                raise RuntimeError("Batch failure")

            async def _process_single(self, item):
                attempt_counts[item.id] = attempt_counts.get(item.id, 0) + 1
                if attempt_counts[item.id] < 3:
                    raise ValueError(f"Attempt {attempt_counts[item.id]} failed")
                return {"item": item.id, "attempts": attempt_counts[item.id]}

        config = BatchConfig(max_item_retries=2)
        processor = CountingRetryProcessor(config)
        item = BatchItem(id="retry-test", prompt="Hello")

        future = await processor.submit(item)
        result = await future

        assert attempt_counts["retry-test"] == 3
        assert item.retry_count == 2  # Final retry_count after exhausting retries
        assert result == {"item": "retry-test", "attempts": 3}
        await processor.shutdown()
