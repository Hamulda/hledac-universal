"""
Async Generators Pipeline Utilities — F275

Modern streaming pipeline pro M1 8GB: constant memory místo list accumulation.
"""


import asyncio
import inspect
import typing
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass

# type aliases pro clarity
T = typing.TypeVar("T")
R = typing.TypeVar("R")


@dataclass(frozen=True, slots=True)
class BatchStats:
    """Statistics pro batch processing."""
    items_processed: int = 0
    batches_yielded: int = 0
    items_filtered: int = 0


async def async_batched(
    source: AsyncIterator[T],
    batch_size: int = 1024,
) -> AsyncGenerator[list[T]]:
    """
    Yield items from async iterator as bounded batches.

    Memory-efficient: only holds batch_size items per pending batch.

    Args:
        source: Async iterator of items
        batch_size: Max items per batch (M1 8GB: 1024 = ~5MB peak)

    Yields:
        Batches of items (list[T])
    """
    batch: list[T] = []

    async for item in source:
        batch.append(item)

        if len(batch) >= batch_size:
            yield batch
            batch = []

    if batch:
        yield batch


async def async_transform(
    source: AsyncIterator[T],
    transform: Callable[[T], R | Awaitable[R]],
    concurrency: int = 1,
) -> AsyncGenerator[R]:
    """
    Transform items from async iterator through async function.

    Args:
        source: Async iterator of input items
        transform: Sync or async function T -> R
        concurrency: Max concurrent transforms (1 = sequential)

    Yields:
        Transformed items
    """
    if concurrency == 1:
        async for item in source:
            if asyncio.iscoroutinefunction(transform):
                result = await transform(item)
            else:
                result = transform(item)
            yield result
    else:
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        pending: list[asyncio.Task[typing.Any]] = []

        async def transform_with_sem(item: T) -> R:
            async with semaphore:
                val = transform(item)
                if isinstance(val, Awaitable):
                    return await val
                return val  # type: ignore[return-value]

        async for item in source:
            task = asyncio.create_task(transform_with_sem(item))
            pending.append(task)

            if len(pending) >= concurrency:
                done, pending_set = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                pending = list(pending_set)
                for d in done:
                    try:
                        yield d.result()
                    except Exception:  # noqa: BLE001
                        pass

        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)
            for r in results:
                if not isinstance(r, Exception):
                    yield r


async def async_filter(
    source: AsyncIterator[T],
    predicate: Callable[[T], bool | Awaitable[bool]],
) -> AsyncGenerator[T]:
    """
    Filter items from async iterator through async predicate.

    Args:
        source: Async iterator of items
        predicate: Sync or async function T -> bool

    Yields:
        Items where predicate(item) is True
    """
    async for item in source:
        if asyncio.iscoroutinefunction(predicate):
            keep = await predicate(item)
        else:
            keep = predicate(item)

        if keep:
            yield item


async def async_flatmap(
    source: AsyncIterator[Iterable[T] | AsyncIterator[T]],
) -> AsyncGenerator[T]:
    """
    Flatten nested iterables/iterators into single async generator.

    Args:
        source: Async iterator of items that are themselves iterables

    Yields:
        Flattened items
    """
    async for item in source:
        if inspect.isasyncgen(item):
            async for subitem in item:  # type: ignore[arg-type]
                yield subitem
        elif isinstance(item, (list, tuple)):
            for subitem in item:
                yield subitem


async def async_chunked_pipeline(
    source: AsyncIterator[T],
    processor: Callable[[list[T]], Awaitable[list[R]]],
    batch_size: int = 1024,
    max_pending_batches: int = 2,
) -> AsyncGenerator[list[R], BatchStats]:
    """
    Pipeline: batch source items, process with async function, yield results.

    Memory model:
        - Input: AsyncIterator[T] (constant memory, 1 item in flight)
        - Batching: list[T] max batch_size items (bounded)
        - Processing: async function called per batch
        - Output: AsyncGenerator[list[R]] (streams results)

    Args:
        source: Async iterator of input items
        processor: Async function list[T] -> list[R]
        batch_size: Items per batch (default 1024 = ~5MB for findings)
        max_pending_batches: Backpressure limit (only used for concurrent batching)

    Yields:
        Lists of processed results
    """
    stats = BatchStats()

    # Simple approach: batch source, then process each batch sequentially
    # For concurrent processing, use async_transform on the batch generator
    async for batch_items in async_batched(source, batch_size):
        try:
            results = await processor(batch_items)
            yield results
            stats.batches_yielded += 1
        except Exception:
            yield []


# ============================================================================
# F275: Specific pipeline adapters for SprintScheduler -> DuckDB flow
# ============================================================================

async def findings_to_duckdb_pipeline(
    findings_source: AsyncIterator[dict],
    duckdb_store,
    batch_size: int = 1024,
    max_pending: int = 2,
) -> AsyncGenerator[list[dict], BatchStats]:
    """
    F275 canonical pipeline: Stream findings to DuckDB with quality gate.

    Replaces list-accumulation with streaming.

    Args:
        findings_source: AsyncIterator of finding dicts
        duckdb_store: DuckDBShadowStore instance
        batch_size: Findings per batch (1024 default for M1)
        max_pending: Backpressure (2 batches = ~10MB peak)

    Yields:
        Lists of ingest results
    """
    async def process_batch(findings: list[dict]) -> list[dict]:
        return await duckdb_store.async_ingest_findings_batch(findings)

    async for batch_results in async_chunked_pipeline(
        findings_source,
        process_batch,
        batch_size=batch_size,
        max_pending_batches=max_pending,
    ):
        yield batch_results


# ============================================================================
# Backpressure helpers
# ============================================================================

class BackpressureMonitor:
    """Monitor for async generator backpressure."""

    def __init__(self, name: str = "unknown"):
        self.name = name
        self.pending_count = 0
        self.max_pending = 0
        self.total_processed = 0

    def on_item_queued(self) -> None:
        self.pending_count += 1
        self.max_pending = max(self.max_pending, self.pending_count)

    def on_item_dequeued(self) -> None:
        self.pending_count -= 1
        self.total_processed += 1

    @property
    def pressure(self) -> float:
        """0.0 = idle, 1.0 = saturated."""
        if self.max_pending == 0:
            return 0.0
        return self.pending_count / self.max_pending

    def __repr__(self) -> str:
        return f"<BackpressureMonitor {self.name} pending={self.pending_count} max={self.max_pending}>"
