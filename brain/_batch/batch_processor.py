"""
batch_processor.py — Batch Processor
====================================

PEP 698: Extracted from DeepHermes3Engine batch processing.
Handles structured batch execution with response model support.

Extracted from:
- _submit_structured_batch()
- _batch_worker()
- _process_batch()
- _process_structured_batch()
- _execute_structured_batch()
- _run_structured_single()

M1 8GB: Adaptive batch sizing based on memory pressure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable
from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


class BatchPriority(Enum):
    """Batch item priority levels."""
    HIGH = 1.0
    MEDIUM = 0.5
    LOW = 0.25


@dataclass
class BatchItem:
    """
    Single item in a batch queue.

    Extracted from DeepHermes3Engine._submit_structured_batch payload.
    """
    id: str
    prompt: str
    response_model: type | None = None
    priority: float = 0.5
    timeout: float = 30.0
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future | None = None

    def __lt__(self, other: BatchItem) -> bool:
        """Compare by priority for heapq ordering."""
        return self.priority > other.priority  # Higher priority first


@dataclass
class BatchConfig:
    """Configuration for batch processing."""
    max_size: int = 10
    default_flush_interval: float = 0.5  # seconds
    high_pressure_max_size: int = 3
    medium_pressure_max_size: int = 6
    age_bump_interval: float = 5.0  # seconds
    tie_breaker: float = 1e-6  # For priority ties


@dataclass
class BatchStats:
    """Batch processing statistics."""
    processed_count: int = 0
    failed_count: int = 0
    queue_depth: int = 0
    avg_latency_ms: float = 0.0
    last_flush_at: float = 0.0


class BatchProcessor:
    """
    Structured batch processor with priority queuing.

    Extracted from DeepHermes3Engine to:
    1. Isolate batch queue management
    2. Enable independent testing
    3. Provide clean interface for batch operations

    M1 8GB: Adaptive sizing based on memory pressure.
    """

    def __init__(self, config: BatchConfig | None = None) -> None:
        self._config = config or BatchConfig()
        self._queue: list[BatchItem] = []
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None
        self._shutting_down = False
        self._stats = BatchStats()
        self._last_age_bump = time.time()
        self._flush_cycle = 0
        self._telemetry: dict[str, Any] = {}

    @property
    def queue_size(self) -> int:
        """Current queue depth."""
        return len(self._queue)

    def get_stats(self) -> BatchStats:
        """Get current batch statistics."""
        return BatchStats(
            processed_count=self._stats.processed_count,
            failed_count=self._stats.failed_count,
            queue_depth=len(self._queue),
            avg_latency_ms=self._stats.avg_latency_ms,
            last_flush_at=self._stats.last_flush_at,
        )

    def compute_length_bin(self, prompt: str) -> str:
        """
        Categorize prompt by length for batch optimization.

        Returns:
            "short" (<256 tokens), "medium" (256-1024), "long" (>1024)
        """
        token_estimate = len(prompt) // 4  # Rough estimate
        if token_estimate < 256:
            return "short"
        elif token_estimate < 1024:
            return "medium"
        return "long"

    def is_batch_safe(
        self,
        response_model: type | None,
        priority: float,
        stream: bool,
        timeout: float,
    ) -> bool:
        """
        Determine if item can be added to current batch.

        Args:
            response_model: Expected response model type
            priority: Item priority
            stream: Whether streaming is required
            timeout: Request timeout

        Returns:
            True if safe to batch
        """
        if self._shutting_down:
            return False

        if stream:
            return len(self._queue) < 2  # Streaming is exclusive

        if timeout < 5.0:
            return len(self._queue) < 3  # Short timeout = small batch

        # Check memory pressure adaptive limits
        memory_pressure = self._get_memory_pressure()
        if memory_pressure > 0.8:
            max_size = self._config.high_pressure_max_size
        elif memory_pressure > 0.5:
            max_size = self._config.medium_pressure_max_size
        else:
            max_size = self._config.max_size

        return len(self._queue) < max_size

    def _get_memory_pressure(self) -> float:
        """Get current memory pressure (0.0 - 1.0)."""
        try:
            from hledac.universal.brain._metal.metal_device import get_metal_device
            device = get_metal_device()
            stats = device.get_stats()
            # Map to 0-1 range (2GB = 1.0)
            return min(stats.active_gb / 2.0, 1.0)
        except Exception:
            return 0.5  # Default medium pressure

    def current_flush_interval(self) -> float:
        """
        Calculate adaptive flush interval.

        M1 8GB: Faster flush under memory pressure.
        """
        pressure = self._get_memory_pressure()

        if pressure > 0.8:
            return self._config.default_flush_interval * 0.5  # Fast flush
        elif pressure > 0.5:
            return self._config.default_flush_interval * 0.75
        return self._config.default_flush_interval

    async def submit(
        self,
        item: BatchItem,
    ) -> asyncio.Future:
        """
        Submit item to batch queue.

        Args:
            item: Batch item to process

        Returns:
            Future that resolves with result
        """
        item.future = asyncio.get_event_loop().create_future()

        async with self._lock:
            self._queue.append(item)
            # Keep queue sorted by priority (heapq max-heap)
            self._queue.sort(key=lambda x: x.priority, reverse=True)

        # Trigger worker if not running
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

        return item.future

    async def flush(self, timeout: float = 5.0) -> int:
        """
        Force flush current batch.

        Args:
            timeout: Maximum time to wait

        Returns:
            Number of items processed
        """
        async with self._lock:
            items = self._queue.copy()
            self._queue.clear()
            self._stats.last_flush_at = time.time()

        if not items:
            return 0

        processed = 0
        for item in items:
            try:
                await safe_wait_for(
                    self._process_single(item),
                    timeout=item.timeout,
                )
                processed += 1
            except Exception as e:
                logger.warning(f'[BatchProcessor] Item {item.id} failed: {e}')
                if item.future and not item.future.done():
                    item.future.set_exception(e)

        return processed

    async def _worker(self) -> None:
        """Background worker that processes batches."""
        while not self._shutting_down:
            try:
                # Wait for flush interval or queue threshold
                interval = self.current_flush_interval()
                await asyncio.sleep(interval)

                # Age bump for stale items
                self._age_bump_queue()

                # Process current batch
                async with self._lock:
                    if not self._queue:
                        break
                    items = self._queue.copy()
                    self._queue.clear()

                for item in items:
                    try:
                        result = await safe_wait_for(
                            self._process_single(item),
                            timeout=item.timeout,
                        )
                        if item.future and not item.future.done():
                            item.future.set_result(result)
                        self._stats.processed_count += 1
                    except Exception as e:
                        if item.future and not item.future.done():
                            item.future.set_exception(e)
                        self._stats.failed_count += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'[BatchProcessor] Worker error: {e}')

    async def _process_single(self, item: BatchItem) -> Any:
        """
        Process single batch item.

        Override this method for custom processing logic.
        """
        # Default: just return the prompt (placeholder)
        # Real implementation would call inference engine
        return {"prompt": item.prompt, "processed": True}

    def _age_bump_queue(self) -> None:
        """
        Age bump stale items to prevent starvation.

        Items not processed for > age_bump_interval get priority boost.
        """
        now = time.time()
        if now - self._last_age_bump < self._config.age_bump_interval:
            return

        self._last_age_bump = now
        self._flush_cycle += 1

        # Boost priority of stale items
        for item in self._queue:
            age = now - item.created_at
            if age > self._config.age_bump_interval:
                item.priority = min(item.priority * 1.1, 1.0)

    async def shutdown(self, timeout: float = 3.0) -> None:
        """
        Graceful shutdown.

        Args:
            timeout: Maximum time to wait
        """
        self._shutting_down = True

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await safe_wait_for(self._worker_task, timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Flush remaining items
        await self.flush(timeout=timeout)