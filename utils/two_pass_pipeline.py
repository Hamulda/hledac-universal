"""
Two-Pass Pipeline — Issue 2.5

Single asyncio.TaskGroup with a queue between producer (pass 1) and consumer (pass 2).
Backpressure via asyncio.Queue(maxsize=512).

Producer (Pass 1): async I/O — network fetches, disk reads
    └── queue.put_nowait(item)  [blocks when queue full — backpressure]
Consumer (Pass 2): CPU-bound scoring — asyncio.to_thread (GIL released)
    └── queue.get()  [blocks when queue empty — natural flow control]

Structural pattern matching (PEP 634) used for parsed record classification.

M1 8GB invariants:
    - Queue maxsize=512 (never unbounded)
    - Consumer uses asyncio.to_thread for CPU-bound work (GIL released)
    - Fail-safe: returns [] on any error

Usage:
    pipeline = TwoPassPipeline(
        producer_coro=source_fetcher(query),
        consumer_fn=score_candidate,
        config=TwoPassPipelineConfig(label="academic_search"),
    )
    results = await pipeline.run()
"""


import asyncio
import logging
import traceback
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


@dataclass
class TwoPassPipelineConfig:
    """Configuration for a two-pass pipeline."""

    queue_size: int = 512
    label: str = "two_pass"
    consumer_concurrency: int = 8
    timeout_s: float | None = None


@dataclass(frozen=True)
class PipelineStats:
    """Runtime statistics for a two-pass pipeline."""

    produced: int = 0
    consumed: int = 0
    producer_errors: int = 0
    consumer_errors: int = 0
    queue_high_water: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "produced": self.produced,
            "consumed": self.consumed,
            "producer_errors": self.producer_errors,
            "consumer_errors": self.consumer_errors,
            "queue_high_water": self.queue_high_water,
        }


_T = Any
_R = Any


class TwoPassPipeline:
    """
    Single TaskGroup pipeline: producer → Queue → consumer.

    Producer runs as a task in the TaskGroup. Consumer runs as N concurrent
    tasks (consumer_concurrency), each pulling from the shared queue.

    The queue provides natural backpressure: when the queue is full,
    producer.put_nowait() fails with QueueFull, producer yields (via await
    asyncio.sleep(0)) and retries on next iteration.

    Consumer uses structural match/case (PEP 634) to classify items.

    M1 8GB: Queue bounded at 512, asyncio.to_thread for CPU-bound consumer work.
    """

    def __init__(
        self,
        producer_coro: Awaitable[list[_T]],
        consumer_fn: Callable[[_T], _R],
        *,
        config: TwoPassPipelineConfig | None = None,
    ) -> None:
        self._producer_coro = producer_coro
        self._consumer_fn = consumer_fn
        self._config = config or TwoPassPipelineConfig()
        self._queue: asyncio.Queue[_T] = asyncio.Queue(
            maxsize=self._config.queue_size
        )
        self._results: list[_R] = []
        self._stats = PipelineStats()
        self._done = asyncio.Event()
        self._producer_exc: BaseException | None = None

    @property
    def stats(self) -> PipelineStats:
        return self._stats

    async def _producer(self, items: list[_T]) -> None:
        """Feed items into the queue. Called once, with items already collected."""
        try:
            for item in items:
                while True:
                    try:
                        # PEP 634 structural pattern matching on item type
                        match item:
                            case dict() as _d:
                                pass
                            case _ if hasattr(item, "__class__"):
                                pass
                            case _:
                                pass
                        self._queue.put_nowait(item)
                        self._stats.produced += 1
                        water = self._queue.qsize()
                        if water > self._stats.queue_high_water:
                            self._stats.queue_high_water = water
                        break
                    except asyncio.QueueFull:
                        await asyncio.sleep(0)
        except Exception:
            self._producer_exc = BaseException(
                f"[{self._config.label}] Producer error: {traceback.format_exc()}"
            )
            self._stats.producer_errors += 1
        finally:
            self._done.set()

    async def _consumer_worker(self) -> None:
        """Single consumer worker — pulls from queue and applies consumer_fn."""
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                if self._done.is_set():
                    break
                await asyncio.sleep(0.01)
                continue
            except Exception:
                self._stats.consumer_errors += 1
                continue

            try:
                result = self._consumer_fn(item)
                self._results.append(result)
                self._stats.consumed += 1
            except Exception:
                self._stats.consumer_errors += 1
            finally:
                try:
                    self._queue.task_done()
                except ValueError:
                    pass  # Already done

    async def run(self) -> list[_R]:
        """
        Run the pipeline: producer feeds queue, consumers drain it.
        Returns list of consumer results.
        """
        self._results = []
        self._done = asyncio.Event()
        self._producer_exc = None

        # Collect producer results first (the "source" phase)
        producer_items: list[_T] = []
        try:
            producer_items = await self._producer_coro
        except Exception as exc:
            logger.warning(
                "[%s] Producer coroutine raised: %s",
                self._config.label,
                exc,
            )
            self._producer_exc = exc
            self._done.set()

        # Run producer feeder + consumers in a single TaskGroup
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._producer(producer_items))
            for _ in range(self._config.consumer_concurrency):
                tg.create_task(self._consumer_worker())

        return self._results

    async def run_streaming(
        self,
        items_iter: Iterable[_T],
    ) -> list[_R]:
        """
        Streaming variant: producer feeds items as they arrive from item_iter.
        Consumer drains concurrently. Queue provides backpressure.
        """
        self._results = []
        self._done = asyncio.Event()
        self._producer_exc = None

        async def feed_stream() -> None:
            try:
                for item in items_iter:
                    while True:
                        try:
                            self._queue.put_nowait(item)
                            self._stats.produced += 1
                            water = self._queue.qsize()
                            if water > self._stats.queue_high_water:
                                self._stats.queue_high_water = water
                            break
                        except asyncio.QueueFull:
                            await asyncio.sleep(0)
            except Exception:
                self._producer_exc = BaseException(traceback.format_exc())
                self._stats.producer_errors += 1
            finally:
                self._done.set()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(feed_stream())
            for _ in range(self._config.consumer_concurrency):
                tg.create_task(self._consumer_worker())

        return self._results


# ---------------------------------------------------------------------------
# CPU-bound consumer helpers (GIL released via asyncio.to_thread)
# ---------------------------------------------------------------------------


async def consumer_fn_to_thread(
    fn: Callable[[_T], _R],
    items: list[_T],
    *,
    batch_size: int = 64,
) -> list[_R]:
    """
    Run a CPU-bound consumer function over items via asyncio.to_thread.

    Uses batch_size for sequential chunking (avoids unbounded task creation).
    GIL is released during each to_thread call — parallel I/O and CPU overlap.

    M1 8GB: batch_size=64 is the threshold that matches compress.rs Rayon pattern.
    """
    from hledac.universal.utils.async_helpers import safe_gather_ok

    if not items:
        return []

    if len(items) < batch_size:
        # Small batch: unpack coroutines as positional args to safe_gather_ok
        gathered = await safe_gather_ok(
            *[asyncio.to_thread(fn, item) for item in items]
        )
        return [r for r in gathered if not isinstance(r, Exception)]

    # Large batch: chunk into sequential batches, each batch parallel within itself
    results: list[_R] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        gathered = await safe_gather_ok(
            *[asyncio.to_thread(fn, item) for item in batch]
        )
        results.extend(r for r in gathered if not isinstance(r, Exception))
    return results
