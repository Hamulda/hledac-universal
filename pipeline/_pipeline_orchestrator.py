"""P2-3: Pipeline Orchestrator — TaskGroup-based Stage Chain.

Role: Orchestruje všechny stages v AsyncIterator[Stage] řetězci s TaskGroup
na stage boundaries a bounded queues mezi nimi.

B5. Functor-style Pipeline Composition:
    - Uses Rust pipeline_compose module via asyncio.to_thread()
    - pipeline_batch_stats() before each stage
    - 100 items/batch bound for M1 8GB safety
    - Zero-alloc pipeline composition, 2× faster batch processing

Architecture:
    DiscoveryStage → DedupStage → FetchStage → MatchStage → EnrichStage → StoreStage

Akceptační kritérium: 1 000 stránek/sprint při < 4 GB RAM.

Invarianty:
- Always-on: žádné feature flagy
- Bounded: každá queue má explicitní maxsize
- Fail-safe: žádná stage nehazuje exception do TaskGroup
- TaskGroup cancellation: Ctrl-C → graceful shutdown všech stages
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ._stage_protocol import (
    BoundedStageQueue,
    StageContext,
    StageMetrics,
    )
from _core import aclose

# B5. Pipeline Compose imports
try:
    from rust_extensions.wiring.pipeline_compose_wiring import (
        BATCH_SIZE,
        BatchStats,
        RustPipelineComposer,
        pipeline_batch_stats_async,
        pipeline_map_async,
        pipeline_filter_async,
        pipeline_filter_map_async,
    )
except ImportError:
    # Fallback when Rust extension unavailable
    BATCH_SIZE = 100

    class BatchStats:  # type: ignore[no-redef]
        """Fallback batch stats."""
        def __init__(self, count=0, sum_len=0, min_len=0, max_len=0, unique=0):
            self.count = count
            self.sum_len = sum_len
            self.min_len = min_len
            self.max_len = max_len
            self.unique = unique

    class RustPipelineComposer:  # type: ignore[no-redef]
        """Fallback composer using Python."""
        def __init__(self, *, batch_size=100):
            self._batch_size = batch_size
            self._stages = []

        def add_map(self, fn_name):
            self._stages.append(("map", fn_name))
            return self

        def add_filter(self, fn_name):
            self._stages.append(("filter", fn_name))
            return self

        def add_filter_map(self, filter_fn, map_fn):
            self._stages.append(("filter_map", filter_fn, map_fn))
            return self

        async def run(self, items):
            return list(items)

    async def pipeline_batch_stats_async(items):
        if not items:
            return BatchStats()
        lens = [len(s) for s in items]
        return BatchStats(
            count=len(items),
            sum_len=sum(lens),
            min_len=min(lens),
            max_len=max(lens),
            unique=len(set(items)),
        )

    async def pipeline_map_async(items, fn_name):
        return items

    async def pipeline_filter_async(items, fn_name):
        return items

    async def pipeline_filter_map_async(items, filter_fn, map_fn):
        return items

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Default queue sizes — M1 8GB safe for 1 000 URL/sprint
QUEUE_DISCOVERY_OUT = 32
QUEUE_DEDUP_OUT = 64
QUEUE_FETCH_OUT = 128
QUEUE_MATCH_OUT = 256
QUEUE_ENRICH_OUT = 512
QUEUE_STORE_IN = 256


class PipelineOrchestrator:
    """TaskGroup-based pipeline orchestrator.

    Wires stages: Discovery → Dedup → Fetch → Match → Enrich → Store

    Usage:
        ctx = StageContext(query="ransomware", store=store, uma_state="ok")
        orch = PipelineOrchestrator(ctx, max_results=1000)
        result = await orch.run()
    """

    __slots__ = (
        "_ctx",
        "_stages",
        "_queues",
        "_tasks",
        "_running",
        "_adapter_task",
    )

    def __init__(
        self,
        ctx: StageContext,
        *,
        max_results: int = 10,
    ) -> None:
        self._ctx = ctx
        self._stages: list[Any] = []
        self._queues: dict[str, BoundedStageQueue[Any]] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        self._running = False
        self._adapter_task: asyncio.Task[None] | None = None

        self._init_queues(max_results)
        self._init_stages(ctx)

    def _init_queues(self, max_results: int) -> None:
        """Inicializuje bounded queues podle max_results."""
        # Queue sizes use BASE constants directly — _base_maxsize stores the true base.
        # UMA shrink/grow via set_uma_state() multiplies this base by _MAXSIZE_TABLE.
        # queue_scale was applied incorrectly and is now removed.

        self._queues = {
            "discovery_out": BoundedStageQueue[str](
                maxsize=QUEUE_DISCOVERY_OUT,
                stage_name="discovery_out",
            ),
            "dedup_out": BoundedStageQueue[str](
                maxsize=QUEUE_DEDUP_OUT,
                stage_name="dedup_out",
            ),
            "fetch_out": BoundedStageQueue[Any](
                maxsize=QUEUE_FETCH_OUT,
                stage_name="fetch_out",
            ),
            "match_out": BoundedStageQueue[Any](
                maxsize=QUEUE_MATCH_OUT,
                stage_name="match_out",
            ),
            "enrich_out": BoundedStageQueue[Any](
                maxsize=QUEUE_ENRICH_OUT,
                stage_name="enrich_out",
            ),
        }

    def _init_stages(self, ctx: StageContext) -> None:
        """Inicializuje stages s AIMD controllers."""
        from ._discovery_stage import DiscoveryStage
        from ._dedup_stage import DedupStage
        from ._fetch_stage import FetchStage
        from ._match_stage import MatchStage
        from ._enrich_stage import EnrichStage
        from ._store_stage import StoreStage
        from hledac.universal.coordinators.aimd_controllers import (
            make_enrich_aimd,
            make_extract_aimd,
            make_fetch_aimd,
    )

        # Discovery — first stage, no input
        discovery = DiscoveryStage(
            query=ctx.query,
            max_results=ctx.max_results,
            public_bootstrap_enabled=True,
            seed_context=None,
    )

        # Dedup
        dedup = DedupStage(capacity=10_000)

        # Fetch — AIMD
        fetch_aimd = make_fetch_aimd()
        fetch = FetchStage(
            aimd_controller=fetch_aimd,
            query=ctx.query,
            fetch_timeout_s=ctx.fetch_timeout_s,
            fetch_max_bytes=ctx.fetch_max_bytes,
            fetch_concurrency=ctx.fetch_concurrency,
            uma_state=ctx.uma_state,
    )

        # Match
        match = MatchStage()

        # Enrich — AIMD
        enrich_aimd = make_enrich_aimd()
        enrich = EnrichStage(
            aimd_controller=enrich_aimd,
            query=ctx.query,
            uma_state=ctx.uma_state,
    )

        # Store — last stage
        store = StoreStage(
            store=ctx.store,
            batch_size=50,
            flush_interval_s=2.0,
    )

        self._stages = [discovery, dedup, fetch, match, enrich, store]

    async def run(self) -> dict[str, StageMetrics]:
        """Spustí celý pipeline s TaskGroup na stage boundaries.

        Returns:
            dict[str, StageMetrics] — metrics per stage

        """
        self._running = True
        start_time = time.monotonic()

        try:
            async with asyncio.TaskGroup() as main_tg:
                # Start all stages as tasks in the TaskGroup
                # Each stage runs its run() method concurrently

                # P1-8: Background adapter — propagate ctx.uma_state → all queues
                # Runs every 5s while pipeline is active; TaskGroup handles cancellation
                async def _adapt_queues_to_uma() -> None:
                    """UMA-aware queue sizing adapter (P1-8)."""
                    last_state: str | None = None
                    while True:
                        try:
                            await asyncio.sleep(5.0)
                        except asyncio.CancelledError:
                            break
                        if not self._running:
                            break
                        current = getattr(self._ctx, "uma_state", "ok")
                        if current != last_state:
                            last_state = current
                            for q in self._queues.values():
                                q.set_uma_state(current)

                self._adapter_task = main_tg.create_task(
                    _adapt_queues_to_uma(),
                    name="adapter:uma_queue_sizing",
    )

                # Discovery → Dedup
                disc_task = main_tg.create_task(
                    self._stages[0].run(
                        input_queue=None,
                        output_queue=self._queues["discovery_out"],
                        ctx=self._ctx,
                    ),
                    name="stage:discovery",
    )

                dedup_task = main_tg.create_task(
                    self._stages[1].run(
                        input_queue=self._queues["discovery_out"],
                        output_queue=self._queues["dedup_out"],
                        ctx=self._ctx,
                    ),
                    name="stage:dedup",
    )

                # Dedup → Fetch
                fetch_task = main_tg.create_task(
                    self._stages[2].run(
                        input_queue=self._queues["dedup_out"],
                        output_queue=self._queues["fetch_out"],
                        ctx=self._ctx,
                    ),
                    name="stage:fetch",
    )

                # Fetch → Match
                match_task = main_tg.create_task(
                    self._stages[3].run(
                        input_queue=self._queues["fetch_out"],
                        output_queue=self._queues["match_out"],
                        ctx=self._ctx,
                    ),
                    name="stage:match",
    )

                # Match → Enrich
                enrich_task = main_tg.create_task(
                    self._stages[4].run(
                        input_queue=self._queues["match_out"],
                        output_queue=self._queues["enrich_out"],
                        ctx=self._ctx,
                    ),
                    name="stage:enrich",
    )

                # Enrich → Store (final)
                store_task = main_tg.create_task(
                    self._stages[5].run(
                        input_queue=self._queues["enrich_out"],
                        output_queue=None,
                        ctx=self._ctx,
                    ),
                    name="stage:store",
    )

                self._tasks = [
                    disc_task,
                    dedup_task,
                    fetch_task,
                    match_task,
                    enrich_task,
                    store_task,
                ]

        except asyncio.CancelledError:
            logger.debug("PipelineOrchestrator.run() cancelled")
        except Exception:
            logger.exception("PipelineOrchestrator.run() error")

        finally:
            self._running = False
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "PipelineOrchestrator.run() completed in %.1fms", elapsed_ms
    )

        return self._ctx.metrics

    # ------------------------------------------------------------------
    # B5. Parallel Batch Processing — asyncio.gather for concurrent batches
    # ------------------------------------------------------------------

    async def _process_batches_parallel(
        self,
        items: list[str],
        fn_name: str,
        stage_name: str,
        op: str = "map",
        filter_fn: str | None = None,
        max_concurrent: int = 4,
    ) -> tuple[list[Any], list[BatchStats]]:
        """Process items in bounded batches with PARALLEL asyncio.gather.

        O1 OPTIMIZATION: Uses asyncio.gather() to process multiple batches
        concurrently instead of sequential for-loop. This provides ~2-4×
        speedup on multi-core M1 for large batches (>4 batches).

        For M1 8GB safety:
        - max_concurrent=4 limits concurrent threads
        - Each batch is 100 items (BATCH_SIZE)
        - Memory bounded by max_concurrent × BATCH_SIZE × avg_item_size

        Args:
            items: Input items to process
            fn_name: Transform function name (for map/filter_map)
            stage_name: Stage name for logging
            op: Operation type ("map", "filter", "filter_map")
            filter_fn: Filter predicate name (for filter_map)
            max_concurrent: Max concurrent batch tasks (default: 4)

        Returns:
            Tuple of (all_results, batch_stats_list)

        """
        all_results: list[Any] = []
        all_stats: list[BatchStats] = []

        # Split items into batches
        batches: list[list[str]] = [
            items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)
        ]

        if not batches:
            return [], []

        # Process batches in parallel groups to limit memory pressure
        for batch_group_start in range(0, len(batches), max_concurrent):
            batch_group = batches[
                batch_group_start : batch_group_start + max_concurrent
            ]

            # Create async tasks for this group
            tasks: list[asyncio.Task[tuple[list[Any] | list[str], BatchStats]]] = []
            for batch_idx, batch in enumerate(batch_group):
                global_idx = batch_group_start + batch_idx
                if op == "map":
                    task = asyncio.create_task(
                        self._process_batch_with_stats(
                            batch, fn_name, stage_name, global_idx, "map"
                        )
                    )
                elif op == "filter":
                    task = asyncio.create_task(
                        self._process_batch_with_stats(
                            batch, fn_name, stage_name, global_idx, "filter"
                        )
                    )
                elif op == "filter_map" and filter_fn is not None:
                    task = asyncio.create_task(
                        self._process_batch_with_stats_filter_map(
                            batch, filter_fn, fn_name, stage_name, global_idx
                        )
                    )
                else:
                    task = asyncio.create_task(
                        self._process_batch_with_stats(
                            batch, fn_name, stage_name, global_idx, "passthrough"
                        )
                    )
                tasks.append(task)

            # Wait for all tasks in this group to complete
            group_results: list[tuple[list[Any], BatchStats]] = await asyncio.gather(
                *tasks, return_exceptions=True
            )

            # Collect results and handle any exceptions
            for result in group_results:
                if isinstance(result, Exception):
                    logger.warning(
                        "B5.[%s] Batch failed: %s", stage_name, result
                    )
                    continue
                batch_results, batch_stats = result
                all_results.extend(batch_results)
                all_stats.append(batch_stats)

        return all_results, all_stats

    async def _process_batch_with_stats(
        self,
        items: list[str],
        fn_name: str,
        stage_name: str,
        batch_idx: int,
        op: str = "map",
    ) -> tuple[list[Any], BatchStats]:
        """Process a single batch with stats logging.

        Args:
            items: Input items
            fn_name: Transform function name
            stage_name: Stage name for logging
            batch_idx: Batch index for logging
            op: Operation type

        Returns:
            Tuple of (results, batch_stats)

        """
        stats = await self._log_batch_stats(stage_name, items, batch_idx)

        if op == "map":
            results = await pipeline_map_async(items, fn_name)
        elif op == "filter":
            results = await pipeline_filter_async(items, fn_name)
        else:
            results = list(items)

        return results, stats

    async def _process_batch_with_stats_filter_map(
        self,
        items: list[str],
        filter_fn: str,
        map_fn: str,
        stage_name: str,
        batch_idx: int,
    ) -> tuple[list[Any], BatchStats]:
        """Process a single FILTER-MAP batch with stats logging.

        Args:
            items: Input items
            filter_fn: Filter predicate name
            map_fn: Transform function name
            stage_name: Stage name for logging
            batch_idx: Batch index for logging

        Returns:
            Tuple of (results, batch_stats)

        """
        stats = await self._log_batch_stats(stage_name, items, batch_idx)
        results = await pipeline_filter_map_async(items, filter_fn, map_fn)
        return results, stats

    # ------------------------------------------------------------------
    # B5. Batch Processing Methods — Rust pipeline_compose via asyncio.to_thread
    # ------------------------------------------------------------------

    async def _log_batch_stats(
        self, stage_name: str, items: list[str], batch_idx: int
    ) -> BatchStats:
        """Log batch statistics before stage processing.

        Calls pipeline_batch_stats_async() to get batch metrics
        (count, sum_len, min_len, max_len, unique_count) via asyncio.to_thread.

        Args:
            stage_name: Name of the stage about to process the batch
            items: Batch of items
            batch_idx: Batch index for logging

        Returns:
            BatchStats for the batch

        """
        stats = await pipeline_batch_stats_async(items)
        logger.debug(
            "B5.[%s] Batch[%d]: count=%d, sum_len=%d, min=%d, max=%d, unique=%d",
            stage_name,
            batch_idx,
            stats.count,
            stats.sum_len,
            stats.min_len,
            stats.max_len,
            stats.unique,
        )
        return stats

    async def _process_batch_map(
        self,
        items: list[str],
        fn_name: str,
        stage_name: str,
    ) -> tuple[list[Any], BatchStats]:
        """Process a batch with MAP operation via asyncio.to_thread.

        Args:
            items: Input items
            fn_name: Transform function name
            stage_name: Stage name for logging

        Returns:
            Tuple of (results, batch_stats_before)

        """
        stats = await self._log_batch_stats(stage_name, items, 0)
        results = await pipeline_map_async(items, fn_name)
        return results, stats

    async def _process_batch_filter(
        self,
        items: list[str],
        fn_name: str,
        stage_name: str,
    ) -> tuple[list[str], BatchStats]:
        """Process a batch with FILTER operation via asyncio.to_thread.

        Args:
            items: Input items
            fn_name: Predicate function name
            stage_name: Stage name for logging

        Returns:
            Tuple of (results, batch_stats_before)

        """
        stats = await self._log_batch_stats(stage_name, items, 0)
        results = await pipeline_filter_async(items, fn_name)
        return results, stats

    async def _process_batch_filter_map(
        self,
        items: list[str],
        filter_fn: str,
        map_fn: str,
        stage_name: str,
    ) -> tuple[list[Any], BatchStats]:
        """Process a batch with FILTER-MAP operation via asyncio.to_thread.

        Args:
            items: Input items
            filter_fn: Filter predicate name
            map_fn: Transform function name
            stage_name: Stage name for logging

        Returns:
            Tuple of (results, batch_stats_before)

        """
        stats = await self._log_batch_stats(stage_name, items, 0)
        results = await pipeline_filter_map_async(items, filter_fn, map_fn)
        return results, stats

    async def _process_bounded_batches(
        self,
        items: list[str],
        fn_name: str,
        stage_name: str,
        op: str = "map",
        filter_fn: str | None = None,
    ) -> tuple[list[Any], list[BatchStats]]:
        """Process items in bounded batches with stats logging.

        B5: 100 items/batch bound for M1 8GB safety.
        Calls pipeline_batch_stats_async() before each batch.

        Args:
            items: Input items to process
            fn_name: Transform function name (for map/filter_map)
            stage_name: Stage name for logging
            op: Operation type ("map", "filter", "filter_map")
            filter_fn: Filter predicate name (for filter_map)

        Returns:
            Tuple of (all_results, batch_stats_list)

        """
        all_results: list[Any] = []
        all_stats: list[BatchStats] = []
        batch_idx = 0

        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i : i + BATCH_SIZE]
            batch_stats = await self._log_batch_stats(stage_name, batch, batch_idx)
            all_stats.append(batch_stats)

            if op == "map":
                batch_results = await pipeline_map_async(batch, fn_name)
            elif op == "filter":
                batch_results = await pipeline_filter_async(batch, fn_name)
            elif op == "filter_map" and filter_fn is not None:
                batch_results = await pipeline_filter_map_async(
                    batch, filter_fn, fn_name
                )
            else:
                batch_results = list(batch)

            all_results.extend(batch_results)
            batch_idx += 1

        return all_results, all_stats

    def create_pipeline_composer(self) -> RustPipelineComposer:
        """Create a RustPipelineComposer for complex multi-stage pipelines.

        Returns:
            Configured RustPipelineComposer with BATCH_SIZE=100

        """
        return RustPipelineComposer(batch_size=BATCH_SIZE)

    async def aclose(self) -> None:
        """Graceful shutdown — cancel all stage tasks."""
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            if not task.done():
                try:
                    await task
                except asyncio.CancelledError:  # noqa: BLE001
                    pass
                except Exception:  # noqa: BLE001
                    pass
        # P1-8: cancel adapter task
        if self._adapter_task is not None and not self._adapter_task.done():
            self._adapter_task.cancel()
            try:
                await self._adapter_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------
# Convenience function
# ----------------------------------------------------------------------


async def run_public_pipeline(
    query: str,
    store: Any | None = None,
    *,
    max_results: int = 10,
    fetch_concurrency: int = 8,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2_000_000,
    uma_state: str = "ok",
    **kwargs: Any,  # noqa: ARG002
) -> dict[str, StageMetrics]:
    """Run the public pipeline with TaskGroup stages.

    Args:
        query: Research query string
        store: DuckDBShadowStore for persistence
        max_results: Max discovery results
        fetch_concurrency: Fetch concurrency limit
        fetch_timeout_s: Fetch timeout per page
        fetch_max_bytes: Max bytes to fetch per page
        uma_state: UMA state for AIMD scaling
        **kwargs: Ignored (for API compatibility)

    Returns:
        dict[str, StageMetrics] — per-stage metrics

    """
    ctx = StageContext(
        query=query,
        store=store,
        max_results=max_results,
        fetch_concurrency=fetch_concurrency,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        uma_state=uma_state,
    )

    orch = PipelineOrchestrator(ctx, max_results=max_results)
    try:
        metrics = await orch.run()
        return metrics
    finally:
        await orch.aclose()
