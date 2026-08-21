"""
Async Generators Pipeline Utilities — F275

Modern streaming pipeline pro M1 8GB: constant memory místo list accumulation.

"""

import asyncio
import inspect
import typing
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable

from compat.msgspec_gc_compat import Struct

T = typing.TypeVar("T", default=object)
R = typing.TypeVar("R", default=object)


class BatchStats(Struct, frozen=True):
    """Statistics pro batch processing."""

    items_processed: int = 0
    batches_yielded: int = 0
    items_filtered: int = 0


async def async_batched[T](source: AsyncIterator[T], batch_size: int = 1024) -> AsyncGenerator[list[T]]:
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
    try:
        async for item in source:
            batch.append(item)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
    finally:
        # PEP 479: Cleanup when generator is abandoned mid-stream
        batch.clear()
        batch = None


async def async_transform[T, R](
    source: AsyncIterator[T], transform: Callable[[T], R | Awaitable[R]], concurrency: int = 1
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
            if inspect.iscoroutinefunction(transform):
                result = await transform(item)
            else:
                result = transform(item)
            yield result
    else:
        from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore

        semaphore = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)
        pending: set[asyncio.Task[typing.Any]] = set()

        async def transform_with_sem(item: T) -> R:
            async with semaphore:
                val = transform(item)
                if isinstance(val, Awaitable):
                    return await val
                return val

        async with asyncio.TaskGroup() as tg:
            async for item in source:
                task = tg.create_task(transform_with_sem(item), eager_start=True)
                pending.add(task)
                if len(pending) >= concurrency:
                    # ISSUE-15: asyncio.wait(FIRST_COMPLETED) → first_completed helper
                    winner_task: asyncio.Task[Any]
                    _, winner_task = await first_completed(*pending)
                    pending.discard(winner_task)
                    try:
                        yield winner_task.result()
                    except Exception:  # noqa: BLE001
                        pass
            # Drain all remaining tasks with gather — runs inside the TaskGroup
            # scope so all tasks complete before cancellation on scope exit.
            remaining = await asyncio.gather(*pending, return_exceptions=True)
            for r in remaining:
                if isinstance(r, Exception):
                    pass
                else:
                    yield r


async def async_filter[T](
    source: AsyncIterator[T], predicate: Callable[[T], bool | Awaitable[bool]]
) -> AsyncGenerator[T]:
    """
    Filter items from async iterator through async predicate.

    Args:
        source: Async iterator of items
        predicate: Sync or async function T -> bool

    Yields:
        Items where predicate(item) is True
    """
    try:
        async for item in source:
            if inspect.iscoroutinefunction(predicate):
                keep = await predicate(item)
            else:
                keep = predicate(item)
            if keep:
                yield item
    finally:
        # PEP 479: Cleanup when generator is abandoned mid-stream
        pass  # No local state to cleanup


async def async_flatmap[T](source: AsyncIterator[Iterable[T] | AsyncIterator[T]]) -> AsyncGenerator[T]:
    """
    Flatten nested iterables/iterators into single async generator.

    Args:
        source: Async iterator of items that are themselves iterables

    Yields:
        Flattened items
    """
    try:
        async for item in source:
            if inspect.isasyncgen(item):
                async for subitem in item:
                    yield subitem
            elif isinstance(item, (list, tuple)):
                for subitem in item:
                    yield subitem
    finally:
        # PEP 479: Cleanup when generator is abandoned mid-stream
        pass  # Source is consumed by iteration


async def async_chunked_pipeline[T, R](
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
    try:
        async for batch_items in async_batched(source, batch_size):
            try:
                results = await processor(batch_items)
                yield results
                stats.batches_yielded += 1
            except Exception:
                yield []
    finally:
        # PEP 479: Cleanup when generator is abandoned mid-stream
        stats = None


async def findings_to_duckdb_pipeline(
    findings_source: AsyncIterator[dict], duckdb_store, batch_size: int = 1024, max_pending: int = 2
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

    try:
        async for batch_results in async_chunked_pipeline(
            findings_source, process_batch, batch_size=batch_size, max_pending_batches=max_pending
        ):
            yield batch_results
    finally:
        # PEP 479: Cleanup when generator is abandoned mid-stream
        pass  # No local state to cleanup


class BackpressureMonitor:
    """Monitor for async generator backpressure."""

    __slots__ = ("max_pending", "name", "pending_count", "total_processed")

    def __init__(self, name: str = "unknown") -> None:
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


async def aclose_safe(agen: AsyncIterator) -> None:
    """
    Safely close an async generator, ignoring AlreadyClosedError.

    Pattern for preventing coroutine leaks when breaking early from
    `async for` loops:

    ```python
    gen = async_range_slow(1000)
    try:
        async for item in gen:
            if stop_condition:
                break
    finally:
        await aclose_safe(gen)  # Prevents memory leak
    ```

    Args:
        agen: Async iterator to close
    """
    try:
        if hasattr(agen, "aclose"):
            await agen.aclose()
    except AttributeError, StopAsyncIteration, RuntimeError:  # noqa: BLE001
        # Already closed or doesn't support aclose
        pass


class AsyncIteratorContext:
    """
    Context manager for async iterators that ensures cleanup.

    Usage:
    ```python
    async with AsyncIteratorContext(async_range_slow(1000)) as agen:
        async for item in agen:
            if stop_condition:
                break
    # aclose() called automatically on exit
    ```
    """

    __slots__ = ("_agen",)

    def __init__(self, agen: AsyncIterator) -> None:
        self._agen = agen

    def __aiter__(self):
        return self._agen

    async def __aenter__(self) -> AsyncIterator:
        return self._agen

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await aclose_safe(self._agen)
        return False  # Don't suppress exceptions


def async_iter_context(agen: AsyncIterator) -> AsyncIteratorContext:
    """Create a context manager wrapper for an async iterator."""
    return AsyncIteratorContext(agen)


async def merge_async_iterables[T](*sources: AsyncIterator[T]) -> AsyncGenerator[T]:
    """
    Merge multiple async iterables into one, yielding items as they become available.

    True streaming: yields items immediately when any source produces them.
    Uses asyncio.TaskGroup for structured concurrency.

    Architecture:
        - Each source runs concurrently via separate tasks
        - Uses an unbounded queue to collect results from all sources
        - Yields immediately when any source produces an item (no batching)

    Args:
        *sources: Variable number of async iterators to merge

    Yields:
        Items from any source as they become available

    Example:
        async def gen1():
            yield 1
            yield 2
        async def gen2():
            yield 3
            yield 4

        async for item in merge_async_iterables(gen1(), gen2()):
            print(item)  # Yields as fast as sources produce (e.g., 1, 3, 2, 4)

    Backpressure:
        - Downstream must consume items to unblock producers
        - Queue is unbounded but bounded by event loop scheduling
        - Uses await asyncio.sleep(0) to yield control between items
    """
    if not sources:
        return

    results_queue: asyncio.Queue[tuple[int, T]] = asyncio.Queue()
    # Track completed sources
    completed_count = 0
    total_sources = len(sources)

    async def source_worker(source_idx: int, source: AsyncIterator[T]) -> None:
        """Worker that drains a single source and puts items in queue."""
        nonlocal completed_count
        try:
            async for item in source:
                await results_queue.put((source_idx, item))
        except asyncio.CancelledError:  # noqa: BLE001
            raise
        except Exception:  # noqa: BLE001
            # Swallow exceptions from individual sources
            pass
        finally:
            completed_count += 1

    async with asyncio.TaskGroup() as tg:
        for idx, source in enumerate(sources):
            tg.create_task(source_worker(idx, source), eager_start=True)

        # Consumer: yield items as they arrive from any source
        while completed_count < total_sources:
            try:
                # Use wait_for with timeout to check for completion
                source_idx, item = await asyncio.wait_for(results_queue.get(), timeout=0.01)
                yield item
                # Yield to event loop - allows downstream to process
                await asyncio.sleep(0)
            except TimeoutError:
                # No items available, continue checking for completion
                continue
            except asyncio.CancelledError:  # noqa: BLE001
                raise

        # Drain remaining items from queue
        while not results_queue.empty():
            try:
                _, item = results_queue.get_nowait()
                yield item
            except asyncio.QueueEmpty:
                break

    # TaskGroup exits when all tasks complete or any raises
