"""P2-3: Fetch Stage — URL fetch s AIMD a bounded queue.

Role: Fetch stage přijímá URLy z DedupStage, fetchuje je s AIMD rate limiting
a posílá PageResult do MatchStage přes bounded queue.


Wires existující _fetch_and_process_page() z public_fetch module.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ._stage_protocol import BoundedStageQueue, Stage, StageContext
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default queue sizes pro M1 8GB
DEFAULT_FETCH_QUEUE_IN = 32   # URLy čekající na fetch
DEFAULT_FETCH_QUEUE_OUT = 64  # PageResult čekající na match


class FetchStage:
    """Fetch stage: AsyncIterator[str(url)] → AsyncIterator[PageResult].

    Používá existující _fetch_and_process_page() z public_fetch module.
    AIMD řídí concurrency oknem — stejný pattern jako FetchCoordinator.

    Memory: při 1 000 URL a concurrency=8, ~8 URL v letu = ~64 MB max.
    """

    name: str = "fetch"

    __slots__ = (
        "_semaphore",
        "_aimd",
        "_uma_state",
        "_fetch_fn",
        "_match_fn",
        "_query",
        "_fetch_timeout_s",
        "_fetch_max_bytes",
        "_effective_concurrency",
        "_running",
    )

    def __init__(
        self,
        *,
        aimd_controller: Any | None = None,
        fetch_fn: Any | None = None,
        match_fn: Any | None = None,
        query: str = "",
        fetch_timeout_s: float = 35.0,
        fetch_max_bytes: int = 2_000_000,
        fetch_concurrency: int = 8,
        uma_state: str = "ok",
    ):
        from hledac.universal.coordinators.aimd_controllers import make_fetch_aimd

        self._aimd = aimd_controller or make_fetch_aimd()
        self._fetch_fn = fetch_fn
        self._match_fn = match_fn
        self._query = query
        self._fetch_timeout_s = fetch_timeout_s
        self._fetch_max_bytes = fetch_max_bytes
        self._uma_state = uma_state

        # Semaphore controlled by AIMD window
        effective = max(1, min(fetch_concurrency, int(self._aimd.window)))
        self._semaphore = asyncio.Semaphore(effective)
        self._effective_concurrency = effective
        self._running = False

    @property
    def aimd_window(self) -> float:
        return self._aimd.window

    async def run(
        self,
        input_queue: BoundedStageQueue[str] | None,
        output_queue: BoundedStageQueue[Any],
        ctx: StageContext,
    ) -> None:
        """Zpracuje URLy z input_queue, fetchuje je, posílá PageResult do output_queue.

        Args:
            input_queue: BoundedStageQueue[str] — URLy k fetchi
            output_queue: BoundedStageQueue[PageResult] — výsledky pro MatchStage
            ctx: StageContext

        """
        self._running = True
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()

        try:
            while self._running:
                # Get next URL s timeout
                try:
                    if input_queue is None:
                        break  # No input, end
                    async with asyncio.timeout(5.0):
                        url = await input_queue.get()
                except asyncio.TimeoutError:
                    # Check if we should exit (input empty + upstream done)
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Fetch s AIMD-gated semaphore
                result = await self._fetch_one(url, ctx)
                metrics.record_processed()

                # AIMD feedback — use ctx.uma_state for live UMA pressure (P1-8)
                # on_failure() takes no args; uma_state context is read from ctx
                if result is not None and not getattr(result, "error", None):
                    new_window, _ = await self._aimd.on_success()
                else:
                    new_window, _ = await self._aimd.on_failure()

                # Update semaphore if window changed significantly
                if abs(new_window - self._effective_concurrency) >= 1.0:
                    effective = max(1, min(int(new_window), 25))
                    if effective != self._effective_concurrency:
                        self._effective_concurrency = effective
                        # Note: can't resize Semaphore, but AIMD window affects new tasks
                    metrics.update_aimd_window(new_window)

                # Put result do output (drop if full)
                if result is not None:
                    await output_queue.put(result)

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("FetchStage.run() error")
        finally:
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)

    async def _fetch_one(self, url: str, ctx: StageContext) -> Any | None:
        """Fetch one URL s semaphore gating."""
        # Import lazily to avoid circular at module load
        from .public_fetch import _fetch_and_process_page

        hit_url = url
        hit_title = ""
        hit_snippet = ""
        hit_rank = 0
        discovery_score = None
        discovery_reason = None

        async with self._semaphore:
            try:
                result = await _fetch_and_process_page(
                    semaphore=asyncio.Semaphore(1),  # inner semaphore není potřeba
                    query=self._query or ctx.query,
                    hit_url=hit_url,
                    hit_title=hit_title,
                    hit_snippet=hit_snippet,
                    hit_rank=hit_rank,
                    fetch_timeout_s=self._fetch_timeout_s,
                    fetch_max_bytes=self._fetch_max_bytes,
                    store=ctx.store,
                    memory_manager=ctx.memory_manager,
                    session_id=ctx.session_id,
                    discovery_score=discovery_score,
                    discovery_reason=discovery_reason,
                    vector_store=ctx.vector_store,
                    graph=ctx.graph,
    )
                return result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("FetchStage._fetch_one(%s) error: %s", url[:50])
                return None

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
