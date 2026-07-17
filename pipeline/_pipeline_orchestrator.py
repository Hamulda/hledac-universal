"""
P2-3: Pipeline Orchestrator — TaskGroup-based Stage Chain
=========================================================

Role: Orchestruje všechny stages v AsyncIterator[Stage] řetězci s TaskGroup
na stage boundaries a bounded queues mezi nimi.

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
    """
    TaskGroup-based pipeline orchestrator.

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
        from coordinators.aimd_controllers import (
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
        """
        Spustí celý pipeline s TaskGroup na stage boundaries.

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
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        # P1-8: cancel adapter task
        if self._adapter_task is not None and not self._adapter_task.done():
            self._adapter_task.cancel()
            try:
                await self._adapter_task
            except asyncio.CancelledError:
                pass
            except Exception:
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
    """
    Convenience function — run public pipeline with TaskGroup stages.

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
