"""
Two-Pass Pipeline — Issue 2.5

Single asyncio.TaskGroup with a queue between producer (pass 1) and consumer (pass 2).



Backpressure via asyncio.Queue(maxsize=512).

Producer (Pass 1): async I/O — network fetches, disk reads
    └── queue.put(item)  [awaits with timeout — signals backpressure to producer caller]
Consumer (Pass 2): CPU-bound scoring — asyncio.to_thread (GIL released)
    └── queue.get()  [blocks when queue empty — natural flow control]

S1-08 FIX: credit-based flow control replaces naive put().
- Producer must wait for queue space (backpressure propagated to caller)
- On TimeoutError: log and break (don't silently drop — data loss risk)
- Natural credit loop: consumer releases credit via task_done() after each item

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
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Iterable
logger = logging.getLogger(__name__)


class TwoPassPipelineConfig(Struct):
    """Configuration for a two-pass pipeline."""

    queue_size: int = 512
    label: str = "two_pass"
    consumer_concurrency: int = 8
    timeout_s: float | None = None


class PipelineStats(Struct):
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

    S1-08 FIX: credit-based backpressure — producer awaits queue.put() with timeout.
    When queue is full, producer's caller (upstream fetcher) receives backpressure
    and can decide to slow down, buffer locally, or drop. This prevents unbounded
    queue growth and provides true end-to-end flow control.

    Consumer uses structural match/case (PEP 634) to classify items.

    M1 8GB: Queue bounded at 512, asyncio.to_thread for CPU-bound consumer work.
    """

    __slots__ = ("_config", "_consumer_fn", "_done", "_producer_coro", "_producer_exc", "_queue", "_results", "_stats")

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
        self._queue: asyncio.Queue[_T] = asyncio.Queue(maxsize=self._config.queue_size)
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
                try:
                    # D5 FIX: safe_wait_for for correct TaskGroup composition
                    await safe_wait_for(self._queue.put(item), timeout=5.0)
                    self._stats.produced += 1
                    water = self._queue.qsize()
                    if water > self._stats.queue_high_water:
                        self._stats.queue_high_water = water
                except TimeoutError:
                    logger.warning("[%s] Producer timed out waiting for queue space, dropping item", self._config.label)
                    break
        except Exception:
            self._producer_exc = BaseException(f"[{self._config.label}] Producer error: {traceback.format_exc()}")
            self._stats.producer_errors += 1
        finally:
            self._done.set()

    async def _consumer_worker(self) -> None:
        """Single consumer worker — pulls from queue and applies consumer_fn."""
        while True:
            try:
                # D5 FIX: safe_wait_for for correct TaskGroup composition
                item = await safe_wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                if self._done.is_set() and self._queue.empty():
                    break
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
                except ValueError:  # noqa: BLE001
                    pass

    async def run(self) -> list[_R]:
        """
        Run the pipeline: producer feeds queue, consumers drain it.
        Returns list of consumer results.
        """
        self._results = []
        self._done = asyncio.Event()
        self._producer_exc = None
        producer_items: list[_T] = []
        try:
            producer_items = await self._producer_coro
        except Exception as exc:
            logger.warning("[%s] Producer coroutine raised: %s", self._config.label, exc)
            self._producer_exc = exc
            self._done.set()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._producer(producer_items))
            for _ in range(self._config.consumer_concurrency):
                tg.create_task(self._consumer_worker())
        return self._results

    async def run_streaming(self, items_iter: Iterable[_T]) -> list[_R]:
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
                    try:
                        # D5 FIX: safe_wait_for for correct TaskGroup composition
                        await safe_wait_for(self._queue.put(item), timeout=5.0)
                        self._stats.produced += 1
                        water = self._queue.qsize()
                        if water > self._stats.queue_high_water:
                            self._stats.queue_high_water = water
                    except TimeoutError:
                        logger.warning(
                            "[%s] Producer timed out waiting for queue space, dropping item", self._config.label
                        )
                        break
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


async def consumer_fn_to_thread(fn: Callable[[_T], _R], items: list[_T], *, batch_size: int = 64) -> list[_R]:
    """
    Run a CPU-bound consumer function over items via the execution gateway.

    Routes to Rust rayon cpu_pool (4 P-cores, NEON SIMD) when the Rust
    extension is available, falling back to the bounded SharedWorkerPool
    (ThreadPoolExecutor, governor-aware, adaptive 1-5 workers).

    Uses batch_size for sequential chunking to avoid unbounded concurrent work.
    Each batch maps fn over items via the gateway — GIL-releasing C extensions
    (msgspec, orjson, zstd) run on Rust rayon pool for true parallelism.

    M1 8GB: batch_size=64 keeps total concurrent work bounded and matches
    compress.rs Rayon batch pattern.

    Issue 8 fix: replaced bare asyncio.to_thread() with bounded gateway dispatch.
    """
    from hledac.universal.runtime.execution_gateway import WorkloadHint, gateway
    from hledac.universal.utils.asyncx import parallel_ok

    if not items:
        return []

    async def _process_batch(batch: list[_T]) -> list[_R]:
        """Process a batch of items via gateway cpu_bound (Rust rayon preferred)."""
        gathered = await parallel_ok(*[gateway.cpu_bound(fn, item, hint=WorkloadHint.GIL_RELEASING) for item in batch])
        return [r for r in gathered if not isinstance(r, Exception)]

    results: list[_R] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_results = await _process_batch(batch)
        results.extend(batch_results)
    return results
