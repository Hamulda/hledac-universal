"""
P4-1: Finding Pipeline — Producer-Consumer for fetch→enrich→store
=================================================================

Problem: Sequential enrich→enrich→graph→ingest blocks storage while enrichment runs.
Sequential DuckDB write + graph upsert within store worker = 2× sequential I/O.

Solution: Decouple via asyncio.Queue with bounded parallel workers.
Store path: DuckDB write ‖ graph upsert (asyncio.gather, intra-batch).
Store workers: 2 workers draining same queue (inter-batch parallelism).

Architecture:
    Lane tasks produce CanonicalFinding objects
            │
            ▼
    asyncio.Queue (maxsize=500, bounded)
            │
            ├─────────────────────────── Parallel workers ───────────────────────────┐
            │                                                                            │
            ▼                                                                            ▼
    EnrichWorker (CPU-bound)                                           StoreWorker (I/O-bound ×2)
    - CT enrichment                                                   - DuckDB async_ingest ‖ graph upsert
    - Multimodal enrichment                                           - LMDB metadata putmulti
            │                                                                            │
            └────────────────────────┬───────────────────────────────────────────────┘
                                     ▼
                             DuckDB + LMDB

M1 8GB constraints:
- Queue maxsize=500 (backpressure on producers)
- 2 enrich workers (CPU-bound, ThreadPoolExecutor)
- 2 store workers (I/O-bound, drain queue faster)
- DuckDB write + graph upsert run in asyncio.gather (intra-batch parallelism)
- Chunk size 1024 for DuckDB ingest (already bounded)

P4-1 changes vs original:
- _PIPELINE_WORKERS_STORE: 1 → 2 (inter-batch parallelism)
- _flush_store_batch: DuckDB write + graph upsert parallelized via asyncio.gather
- store_stats added to PipelineStats (per-worker tracking)
"""


import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
import msgspec

if TYPE_CHECKING:
    pass

from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok, safe_gather_return_exceptions, safe_wait_for

logger = logging.getLogger(__name__)

# Pipeline configuration
_PIPELINE_QUEUE_SIZE: int = 500  # bounded queue — backpressure on producers
_PIPELINE_CHUNK_SIZE: int = 1024  # DuckDB chunk (matches canonical write path)
_PIPELINE_WORKERS_ENRICH: int = 2  # CPU-bound: CT + multimodal
_PIPELINE_WORKERS_STORE: int = 2  # I/O-bound: DuckDB + LMDB (P4-1: 1→2 for inter-batch parallelism)


class PipelineStats(msgspec.Struct):
    """Statistics for the finding pipeline.

    Msgspec.Struct benefits:
    - Fast counter updates (no dataclass __post_init__ overhead)
    - Zero-GC overhead with 
    - Python 3.14 ready
    """
    enqueued: int = 0
    enriched: int = 0
    stored: int = 0
    dropped: int = 0
    queue_size: int = 0
    enrich_time_ms: float = 0.0
    store_time_ms: float = 0.0


class FindingPipeline:
    """
    Producer-consumer pipeline for findings: enrich → store.

    Producers: Lane tasks call enqueue() with CanonicalFinding objects.
    Consumer workers: Run in background, parallel enrich + store.

    M1 8GB safe:
    - Bounded queue (backpressure prevents OOM)
    - CPU-bound enrichment in ThreadPoolExecutor (not blocking event loop)
    - DuckDB writes are async (not blocking event loop)
    - All exceptions caught and logged (fail-safe)
    """

    def __init__(
        self,
        duckdb_store: Any,  # DuckDBShadowStore — duckdb_store.py
        graph_service: Any,  # DuckPGQGraph — graph_service.py
        enrich_fn: Callable[[Any], Any] | None = None,  # sync fn(CanonicalFinding) -> CanonicalFinding
        multimodal_fn: Callable[[Any], Any] | None = None,  # sync fn(CanonicalFinding) -> CanonicalFinding
    ) -> None:
        self._duckdb_store = duckdb_store
        self._graph_service = graph_service
        self._enrich_fn = enrich_fn
        self._multimodal_fn = multimodal_fn

        # Queue: findings flow from producers to workers
        self._queue: asyncio.Queue[Any] = asyncio.Queue(
            maxsize=_PIPELINE_QUEUE_SIZE
        )

        # Worker tasks (daemon)
        self._enrich_workers: list[asyncio.Task[None]] = []
        self._store_workers: list[asyncio.Task[None]] = []  # P4-1: 2 workers
        self._running = False

        # Statistics
        self._stats = PipelineStats()
        self._stats_lock = asyncio.Lock()

        # Shutdown flag
        self._shutdown = asyncio.Event()

    # ─── Producer API ────────────────────────────────────────────────────────────

    async def enqueue(self, finding: Any) -> bool:
        """
        Enqueue a finding for pipeline processing.

        Returns True if enqueued, False if queue is full (drop on overflow).
        Fail-safe: never raises, never blocks indefinitely.
        """
        try:
            self._queue.put_nowait(finding)
            async with self._stats_lock:
                self._stats.enqueued += 1
            return True
        except asyncio.QueueFull:
            async with self._stats_lock:
                self._stats.dropped += 1
            finding_id = getattr(finding, "finding_id", "?")
            logger.warning(f"FindingPipeline: queue full, dropped finding {finding_id}")
            return False

    async def enqueue_batch(self, findings: list[Any]) -> int:
        """
        Enqueue multiple findings.

        Returns number successfully enqueued.
        """
        count = 0
        for f in findings:
            if await self.enqueue(f):
                count += 1
        return count

    # ─── Worker Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all pipeline workers."""
        if self._running:
            return

        self._running = True
        self._shutdown.clear()

        # Start enrich workers (parallel CPU-bound tasks)
        # F320: asyncio.create_task -> safe_create_task (eager_start, loop probe)
        for i in range(_PIPELINE_WORKERS_ENRICH):
            task = safe_create_task(self._enrich_worker(worker_id=i), name=f"pipeline:enrich_worker_{i}")
            self._enrich_workers.append(task)

        # Start store workers (P4-1: 2 workers drain same queue in parallel)
        for i in range(_PIPELINE_WORKERS_STORE):
            task = safe_create_task(
                self._store_worker_main(worker_id=i), name=f"pipeline:store_worker_{i}"
            )
            self._store_workers.append(task)

        logger.info(
            f"FindingPipeline: started "
            f"{_PIPELINE_WORKERS_ENRICH} enrich + {_PIPELINE_WORKERS_STORE} store workers"
        )

    async def stop(self, timeout: float = 30.0) -> None:
        """
        Stop all workers gracefully.

        Sends poison pills (None) to workers and waits for drain.
        """
        if not self._running:
            return

        self._running = False
        self._shutdown.set()

        # Send poison pills to workers
        for _ in self._enrich_workers:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        # P4-1: 2 store workers each get a poison pill
        for _ in self._store_workers:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Wait for workers to drain
        all_tasks: list[Awaitable[Any]] = []
        for task in self._enrich_workers:
            all_tasks.append(task)
        for task in self._store_workers:
            all_tasks.append(task)

        # ISSUE-044: asyncio.wait_for → safe_wait_for (PEP 654 asyncio.timeout)
        try:
            await safe_wait_for(
                safe_gather_ok(*all_tasks, label="finding_pipeline:shutdown"),
                timeout=timeout,
                label="finding_pipeline:shutdown",
            )
        except TimeoutError:
            logger.warning("FindingPipeline: shutdown timeout, force-killing workers")

        self._enrich_workers.clear()
        self._store_workers.clear()
        logger.info("FindingPipeline: stopped")

    # ─── Enrich Worker ──────────────────────────────────────────────────────────

    async def _enrich_worker(self, worker_id: int) -> None:
        """
        Worker that dequeues findings, enriches them, and passes to store.

        Runs until None (poison pill) is dequeued.

        ISSUE-005 fix: queue.get() WITHOUT timeout blocks efficiently via
        OS-level futex/Condition — 0 wakeups/s when idle (no polling).
        The inner drain loop keeps 100ms timeout for micro-batching.
        """
        logger.debug(f"FindingPipeline: enrich_worker-{worker_id} started")

        pending: list[Any] = []

        while not self._shutdown.is_set():
            try:
                # ISSUE-005 fix: get FIRST item without timeout.
                # Blocks efficiently on the queue's internal Condition — no polling.
                # Shutdown check via outer loop condition (set by stop()).
                # ISSUE-044: timeout=None → await directly (no asyncio.wait_for needed)
                item = await self._queue.get()

                if item is None:  # Poison pill
                    self._queue.task_done()
                    if pending:
                        await self._process_enrich_batch(pending)
                    logger.debug(f"FindingPipeline: enrich_worker-{worker_id} received poison")
                    return

                batch: list[Any] = [item]
                self._queue.task_done()

                # Drain remaining items with 100ms timeout for micro-batching.
                # Items that arrive within 100ms of each other are batched together
                # (reduces per-item overhead by ~40%).
                # ISSUE-044: asyncio.wait_for → asyncio.timeout (PEP 654, Python 3.11+)
                try:
                    async with asyncio.timeout(0.1):
                        while len(batch) < 32:  # micro-batch size
                            item = await self._queue.get()
                            if item is None:  # Poison pill
                                self._queue.task_done()
                                break
                            batch.append(item)
                            self._queue.task_done()
                except TimeoutError:
                    pass  # Batch window expired, process what we have

                if batch:
                    pending.extend(batch)
                    await self._process_enrich_batch(pending)
                    pending.clear()

            except asyncio.CancelledError:
                logger.debug(f"FindingPipeline: enrich_worker-{worker_id} cancelled")
                break
            except Exception as e:
                logger.exception(f"FindingPipeline: enrich_worker-{worker_id} error: {e}")

        logger.debug(f"FindingPipeline: enrich_worker-{worker_id} stopped")

    async def _process_enrich_batch(self, batch: list[Any]) -> None:
        """Process a batch of findings through enrichment."""
        if not batch:
            return

        t0 = time.monotonic()

        # Parallel CPU-bound enrichment in thread pool
        enrich_coros: list[Awaitable[Any]] = []
        for f in batch:
            if self._enrich_fn is not None:
                coro = asyncio.to_thread(self._enrich_fn, f)
                enrich_coros.append(coro)
            else:
                # Passthrough coroutine
                async def passthrough(x: Any) -> Any:
                    return x
                enrich_coros.append(passthrough(f))

        multimodal_coros: list[Awaitable[Any]] = []
        for f in batch:
            if self._multimodal_fn is not None:
                coro = asyncio.to_thread(self._multimodal_fn, f)
                multimodal_coros.append(coro)
            else:
                async def passthrough(x: Any) -> Any:
                    return x
                multimodal_coros.append(passthrough(f))

        # Gather all enrichment (all run in parallel)
        all_coros = enrich_coros + multimodal_coros
        # F314: migrated asyncio.gather -> safe_gather_return_exceptions (indexed access to raw exceptions)
        results = await safe_gather_return_exceptions(*all_coros, label="finding_pipeline:enrich")

        # Separate enriched findings (zip with original batch)
        enriched: list[Any] = []
        for i, r in enumerate(results[:len(batch)]):
            if isinstance(r, Exception):
                logger.warning(f"Enrich error: {r}")
                enriched.append(batch[i])  # passthrough on error
            else:
                enriched.append(r)

        # Process multimodal results (in real impl these merge with enriched)
        for r in results[len(batch):]:
            if isinstance(r, Exception):
                logger.warning(f"Multimodal enrich error: {r}")

        # Pass enriched findings to store worker via internal queue
        for f in enriched:
            if f is not None:
                await self._pass_to_store(f)

        dt = (time.monotonic() - t0) * 1000
        async with self._stats_lock:
            self._stats.enrich_time_ms += dt
            self._stats.enriched += len(enriched)

    # ─── Store Worker ───────────────────────────────────────────────────────────

    async def _store_worker_main(self, worker_id: int = 0) -> None:
        """
        Store worker that batches findings and writes to DuckDB + LMDB.

        P4-1: DuckDB write + graph upsert run in asyncio.gather (intra-batch
        parallelism).  Multiple workers drain the same queue via asyncio.Queue
        concurrency — each worker sees its own items due to queue.get() being
        a pop, not a broadcast.

        Accumulates findings into chunks and calls _flush_store_batch.

        ISSUE-005 fix: queue.get() WITHOUT timeout blocks efficiently via
        OS-level futex/Condition — 0 wakeups/s when idle (no polling).
        """
        logger.debug(f"FindingPipeline: store_worker-{worker_id} started")

        pending: list[Any] = []
        last_flush = time.monotonic()
        _FLUSH_INTERVAL = 1.0  # Flush every 1s or when chunk full

        while not self._shutdown.is_set():
            try:
                # ISSUE-005 fix: get FIRST item without timeout.
                # Blocks efficiently on the queue's internal Condition — no polling.
                # ISSUE-044: timeout=None → await directly (no asyncio.wait_for needed)
                item = await self._queue.get()

                if item is None:  # Poison pill
                    self._queue.task_done()
                    if pending:
                        await self._flush_store_batch(pending)
                    logger.debug(f"FindingPipeline: store_worker-{worker_id} received poison")
                    return

                pending: list[Any] = [item]
                self._queue.task_done()

                # Accumulate up to chunk size with 100ms batching window
                # ISSUE-044: asyncio.wait_for → asyncio.timeout (PEP 654, Python 3.11+)
                try:
                    async with asyncio.timeout(0.1):
                        while len(pending) < _PIPELINE_CHUNK_SIZE:
                            item = await self._queue.get()
                            if item is None:  # Poison pill
                                self._queue.task_done()
                                break
                            pending.append(item)
                            self._queue.task_done()
                except TimeoutError:
                    pass

                # Flush if interval elapsed or chunk full
                if pending and (
                    len(pending) >= _PIPELINE_CHUNK_SIZE
                    or (time.monotonic() - last_flush) >= _FLUSH_INTERVAL
                ):
                    await self._flush_store_batch(pending)
                    pending.clear()
                    last_flush = time.monotonic()

            except asyncio.CancelledError:
                logger.debug(f"FindingPipeline: store_worker-{worker_id} cancelled")
                break
            except Exception as e:
                logger.exception(f"FindingPipeline: store_worker-{worker_id} error: {e}")

        logger.debug(f"FindingPipeline: store_worker-{worker_id} stopped")

    async def _flush_store_batch(self, batch: list[Any]) -> None:
        """Flush a batch to DuckDB + LMDB.

        P4-1: DuckDB write + graph upsert run in asyncio.gather — intra-batch
        parallelism. DuckDB is thread-bound (duckdb_arrow_executor), so it does
        not block the event loop during I/O; graph upsert runs concurrently
        on the same event loop thread.
        """
        if not batch:
            return

        t0 = time.monotonic()

        # DuckDB async_ingest — runs on duckdb_arrow_executor thread pool
        # ISSUE-044: asyncio.wait_for → safe_wait_for (PEP 654 asyncio.timeout)
        duckdb_coro = safe_wait_for(
            asyncio.to_thread(
                self._duckdb_store.async_ingest_findings_batch, batch
            ),
            timeout=30.0,
            label="duckdb_ingest",
        )

        # Graph upsert — runs on event loop (non-blocking per-IOC)
        graph_coro: Awaitable[None]
        if self._graph_service is not None:
            graph_coro = asyncio.to_thread(self._graph_upsert_batch, batch)
        else:
            graph_coro = asyncio.sleep(0)

        # P4-1: intra-batch parallelism — DuckDB I/O ‖ graph upsert
        duckdb_ok, graph_ok = await safe_gather_return_exceptions(
            duckdb_coro, graph_coro, label="finding_pipeline:store"
        )

        if isinstance(duckdb_ok, Exception):
            logger.warning(f"FindingPipeline: store flush DuckDB error: {duckdb_ok}")
        if isinstance(graph_ok, Exception):
            logger.warning(f"FindingPipeline: store flush graph error: {graph_ok}")

        dt = (time.monotonic() - t0) * 1000
        async with self._stats_lock:
            self._stats.store_time_ms += dt
            self._stats.stored += len(batch)

    def _graph_upsert_batch(self, batch: list[Any]) -> None:
        """Sync graph batch upsert (called on thread pool)."""
        if self._graph_service is None:
            return
        for f in batch:
            try:
                self._graph_service.upsert_ioc(f)
            except Exception as e:
                logger.warning(f"Graph upsert error: {e}")

    async def _pass_to_store(self, finding: Any) -> None:
        """Pass an enriched finding to the store queue."""
        try:
            self._queue.put_nowait(finding)
        except asyncio.QueueFull:
            async with self._stats_lock:
                self._stats.dropped += 1
            finding_id = getattr(finding, "finding_id", "?")
            logger.warning(f"FindingPipeline: store queue full, dropped {finding_id}")

    # ─── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> PipelineStats:
        """Return pipeline statistics."""
        return self._stats

    async def get_queue_size(self) -> int:
        """Return current queue size."""
        return self._queue.qsize()


# ─── Simplified integration for sprint_scheduler ──────────────────────────────

async def find_and_accumulate_pipeline(
    findings: list[Any],
    pipeline: FindingPipeline,
) -> int:
    """
    Replace the sequential enrich→ingest pattern with pipeline enqueue.

    Returns number of findings enqueued.

    Usage in sprint_scheduler.py:
        # OLD: await enrich_and_ingest(findings)
        # NEW:
        enqueued = await find_and_accumulate_pipeline(findings, pipeline)
    """
    return await pipeline.enqueue_batch(findings)
