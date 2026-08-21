"""
brain/hermes/batch.py — Batch Processing
======================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- PriorityQueueAdapter for BatchProcessor integration
- Batch submission and processing
- Structured batch execution

M1 8GB: Adaptive batch sizing based on memory pressure.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from brain._batch.batch_processor import (
    BatchConfig,
    BatchItem,
    BatchProcessor,
    BatchStats,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

T = TypeVar("T")


class PriorityQueueAdapter:
    """
    PEP 698: Bridges BatchProcessor API to DeepHermes3Engine PriorityQueue.

    Allows external BatchProcessor consumers to interact with DeepHermes3Engine's
    existing PriorityQueue-based batching without rewriting the batch worker.

    Args:
        engine: DeepHermes3Engine instance
    """

    __slots__ = ("_engine", "_config", "_batch_processor")

    def __init__(self, engine) -> None:
        self._engine = engine
        self._config = BatchConfig(
            max_size=engine._batch_max_size,
            default_flush_interval=engine._batch_default_flush_interval,
        )
        self._batch_processor = BatchProcessor(self._config)

    async def submit(self, item: BatchItem) -> asyncio.Future:
        """
        Submit BatchItem to engine's PriorityQueue (converted to 4-tuple).

        Args:
            item: BatchItem to submit

        Returns:
            Future for the result
        """
        tie = next(itertools.count())
        schema_key = item.response_model.__name__ if item.response_model else "None"
        payload = {
            "type": "structured",
            "prompt": item.prompt,
            "response_model": item.response_model,
            "future": item.future,
        }
        await self._engine._batch_queue.put((item.priority, tie, schema_key, payload))
        return item.future

    @property
    def queue_size(self) -> int:
        """Current queue depth."""
        return self._engine._batch_queue.qsize()

    def get_stats(self) -> BatchStats:
        """Get batch statistics from wrapped processor."""
        return self._batch_processor.get_stats()


async def submit_structured_batch[T](
    engine,
    prompt: str,
    response_model: type[T],
    priority: float = 1.0,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    system_msg: str | None = None,
) -> asyncio.Future[T]:
    """
    Submit a structured batch item to the engine's queue.

    Args:
        engine: DeepHermes3Engine instance
        prompt: Prompt for generation
        response_model: Pydantic/msgspec model for structured output
        priority: Batch priority (higher = earlier execution)
        temperature: Sampling temperature
        max_tokens: Maximum tokens to generate
        system_msg: Optional system message

    Returns:
        Future that resolves to the structured result
    """
    loop = asyncio.get_running_loop()
    # ISSUE-11: name= param for better async diagnostics (Python 3.14+)
    future = loop.create_future(name="hermes:batch:response")

    item = BatchItem(
        prompt=prompt,
        response_model=response_model,
        priority=priority,
        temperature=temperature,
        max_tokens=max_tokens,
        system_msg=system_msg,
    )

    # Ensure batch worker is running
    await engine._ensure_batch_worker()

    # Submit via adapter if available, otherwise directly to queue
    if engine._batch_adapter is not None:
        return await engine._batch_adapter.submit(item)

    # Direct queue submission fallback
    tie = next(itertools.count())
    schema_key = response_model.__name__
    payload = {
        "type": "structured",
        "prompt": prompt,
        "response_model": response_model,
        "future": future,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "system_msg": system_msg,
    }
    await engine._batch_queue.put((priority, tie, schema_key, payload))

    return future


def get_batch_config(engine) -> BatchConfig:
    """
    Get BatchConfig from engine settings.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        BatchConfig instance
    """
    return BatchConfig(
        max_size=engine._batch_max_size,
        default_flush_interval=engine._batch_default_flush_interval,
    )


def compute_length_bin(prompt: str) -> str:
    """
    Compute length bin for batch scheduling.

    Args:
        prompt: Prompt string

    Returns:
        Length bin: "short", "medium", or "long"
    """
    token_estimate = len(prompt) // 4

    if token_estimate < 256:
        return "short"
    elif token_estimate < 1024:
        return "medium"
    else:
        return "long"


def compute_system_prompt_hash(system_msg: str | None) -> str:
    """
    Compute hash for system prompt caching.

    Args:
        system_msg: System message

    Returns:
        Hash string
    """
    from hledac.universal.utils.hash import xxh3_64_hex

    if system_msg is None:
        return "none"

    return xxh3_64_hex(system_msg)


def is_batch_safe(
    engine,
    response_model: Any,
    priority: float,
    stream: bool,
    timeout_s: float | None,
) -> bool:
    """
    Check if batch processing is safe given current state.

    Args:
        engine: DeepHermes3Engine instance
        response_model: Expected response model
        priority: Request priority
        stream: Whether streaming is requested
        timeout_s: Request timeout

    Returns:
        True if batch processing is safe
    """
    if engine._model is None:
        return False

    # Streaming cannot be batched
    if stream:
        return False

    queue_size = engine._batch_queue.qsize() if engine._batch_queue else 0
    max_size = engine._batch_max_size

    if queue_size >= max_size:
        return False

    metal_pressure = engine._get_metal_cache_pressure()
    if metal_pressure > 0.9:
        return False

    return True


def current_flush_interval(engine) -> float:
    """
    Get adaptive flush interval based on memory pressure.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        Flush interval in seconds
    """
    base_interval = engine._batch_default_flush_interval
    metal_pressure = engine._get_metal_cache_pressure()

    # Reduce interval under memory pressure
    if metal_pressure > 0.8:
        return base_interval * 0.5
    elif metal_pressure > 0.6:
        return base_interval * 0.75

    return base_interval


async def batch_worker_loop(engine) -> None:
    """
    Batch worker loop - standalone function for engine delegation.

    Args:
        engine: DeepHermes3Engine instance
    """
    import asyncio

    while not engine._closed:
        try:
            items = await asyncio.wait_for(collect_batch_items(engine), timeout=engine._batch_default_flush_interval)
            if items:
                await process_batch_items(engine, items)
        except TimeoutError:
            pass
        except Exception as e:
            logger.error(f"[BATCH] Worker error: {e}")


async def collect_batch_items(engine) -> list:
    """
    Collect batch of items - standalone function for engine delegation.

    Args:
        engine: DeepHermes3Engine instance

    Returns:
        List of batch items
    """
    items = []
    while len(items) < engine._batch_max_size:
        try:
            item = engine._batch_queue.get_nowait()
            items.append(item)
        except asyncio.QueueEmpty:
            break
    return items


async def process_batch_items(engine, items: list) -> None:
    """
    Process batch of items - standalone function for engine delegation.

    Args:
        engine: DeepHermes3Engine instance
        items: List of batch items
    """
    for priority, _tie, _schema_key, payload in items:
        try:
            result = await engine.generate_structured(
                prompt=payload["prompt"],
                response_model=payload["response_model"],
                priority=priority,
            )
            payload["future"].set_result(result)
        except Exception as e:
            payload["future"].set_exception(e)
