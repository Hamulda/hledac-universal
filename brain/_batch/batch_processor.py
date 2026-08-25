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
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

# NOTE: These are absolute imports within the hledac.universal package
# This is the correct pattern for modules within the package hierarchy
from hledac.universal.utils.asyncx import safe_wait_for
from hledac.universal.utils._patterns import collect_results_async


# _core is at the project root level
from hledac.universal._core import aclose

logger = logging.getLogger(__name__)


class BatchPriority(Enum):
    """Batch item priority levels."""
    HIGH = 1.0
    MEDIUM = 0.5
    LOW = 0.25


@dataclass(slots=True)
class BatchItem:
    """
    Single item in a batch queue.

    Extracted from DeepHermes3Engine._submit_structured_batch payload.
    """
    id: str
    prompt: str
    priority: BatchPriority = BatchPriority.MEDIUM
    timeout: float = 60.0
    future: asyncio.Future | None = field(default=None, init=False)
    created_at: float = field(default_factory=time.time, init=False)
    result: Any = field(default=None, init=False, repr=False)

    def __lt__(self, other: BatchItem) -> bool:
        """Compare by priority for heap queue."""
        if not isinstance(other, BatchItem):
            return NotImplemented
        return self.priority.value > other.priority.value


@dataclass(slots=True)
class BatchConfig:
    """
    Batch processing configuration.

    M1 8GB: Adaptive limits based on memory pressure.
    """
    max_size: int = 32
    high_pressure_max_size: int = 8
    medium_pressure_max_size: int = 16
    flush_interval: float = 0.1
    high_pressure_flush_interval: float = 0.5
    medium_pressure_flush_interval: float = 0.25
    timeout: float = 60.0


class BatchProcessor:
    """
    Batch queue processor with adaptive sizing.

    Extracted from DeepHermes3Engine._batch_worker logic.
    Supports priority queue and adaptive batch sizing based on memory pressure.

    M1 8GB: Dynamically adjusts batch size based on Metal memory pressure.
    """
    __slots__ = (
        "_queue", "_lock", "_config", "_semaphore",
        "_closed", "_results", "_high_pressure"
    )

    def __init__(
        self,
        config: BatchConfig | None = None,
        max_concurrent: int = 4,
    ) -> None:
        self._config = config or BatchConfig()
        self._queue: list[BatchItem] = []
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._closed = False
        self._results: dict[str, Any] = {}
        self._high_pressure = False

    async def submit(
        self,
        item: BatchItem,
    ) -> asyncio.Future[Any]:
        """
        Submit a batch item for processing.

        Args:
            item: Batch item with prompt and priority

        Returns:
            asyncio.Future that resolves with the result
        """
        async with self._lock:
            if self._closed:
                raise RuntimeError("BatchProcessor is closed")

            future = asyncio.Future()
            item.future = future
            self._queue.append(item)
            self._queue.sort()
            return future

    async def _should_flush(self) -> bool:
        """Determine if batch should flush now."""
        if len(self._queue) == 0:
            return False

        item = self._queue[0]
        pressure = self._get_memory_pressure()

        self._high_pressure = pressure > 0.7

        if self._high_pressure:
            max_size = self._config.high_pressure_max_size
        elif pressure > 0.4:
            max_size = self._config.medium_pressure_max_size
        else:
            max_size = self._config.max_size

        return len(self._queue) >= max_size

    def _get_memory_pressure(self) -> float:
        """Get current memory pressure (0.0 - 1.0)."""
        try:
            from hledac.universal.brain._metal.metal_device import get_metal_device
            device = get_metal_device()
            stats = device.get_stats()
            return min(stats.active_gb / 2.0, 1.0)
        except Exception:
            return 0.5

    def current_flush_interval(self) -> float:
        """
        Calculate adaptive flush interval.

        M1 8GB: Faster flush under memory pressure.
        """
        pressure = self._get_memory_pressure()
        if self._high_pressure:
            return self._config.high_pressure_flush_interval
        elif pressure > 0.4:
            return self._config.medium_pressure_flush_interval
        return self._config.flush_interval

    async def flush(self) -> list[BatchItem]:
        """
        Flush and return items for processing.

        Returns:
            List of batch items to process
        """
        async with self._lock:
            if not self._queue:
                return []

            items = self._queue[: self._config.max_size]
            self._queue = self._queue[self._config.max_size:]
            return items

    async def process_batch(
        self,
        items: list[BatchItem],
        process_fn: Callable[..., Any],
    ) -> None:
        """
        Process a batch of items.

        Args:
            items: Batch items to process
            process_fn: Async function to process each item
        """
        async def _process_one(item: BatchItem) -> None:
            async with self._semaphore:
                try:
                    result = await safe_wait_for(
                        process_fn(item.prompt),
                        timeout=item.timeout,
                    )
                    item.result = result
                    if item.future and not item.future.done():
                        item.future.set_result(result)
                except TimeoutError:
                    if item.future and not item.future.done():
                        item.future.set_exception(
                            TimeoutError(f"Batch item {item.id} timed out")
                        )
                except Exception as e:
                    if item.future and not item.future.done():
                        item.future.set_exception(e)

        await collect_results_async(items, _process_one)

    async def close(self) -> None:
        """Close the processor and cancel pending items."""
        async with self._lock:
            self._closed = True
            for item in self._queue:
                if item.future and not item.future.done():
                    item.future.cancel()
            self._queue.clear()
            await aclose(self._semaphore)

    def get_stats(self) -> dict[str, Any]:
        """Get processor statistics."""
        return {
            "queue_size": len(self._queue),
            "closed": self._closed,
            "high_pressure": self._high_pressure,
            "memory_pressure": self._get_memory_pressure(),
        }
