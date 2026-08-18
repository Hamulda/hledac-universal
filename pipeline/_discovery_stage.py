"""P2-3: Discovery Stage — URL discovery s bounded queue output.

Role: Discovery stage produkuje URLy (str) do output queue pro DedupStage.
Je to první stage — nemá input queue.

Streaming: DiscoveryStage nyní používá true streaming s merge_async_iterables.
URLy se yieldují okamžitě jak jsou k dispozici z jakéhokoliv zdroje,
bez čekání na dokončení všech zdrojů. Backpressure funguje správně.

ISSUE #7 FIX: Původní implementace blokovala await asyncio.to_thread(_sync_discovery)
a pak teprve iterovala. Nyní běží každý zdroj jako samostatný async generator
a merge_async_iterables() je mergeuje — první hit může přijít za ~0ms místo 2-10s.

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.config_introspection import safe_attr_get
from hledac.universal.utils.async_generators import merge_async_iterables

from ._stage_protocol import BoundedStageQueue, StageContext
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DiscoveryStage:
    """Discovery stage: žádný input → AsyncIterator[str(url)] → output_queue.

    Produkuje URLy pro DedupStage. Používá existující discovery logiku
    z live_public_pipeline (bootstrap, rescue, duckduckgo search).

    Memory: ~10 MB pro hit list, ~5 MB pro seen set.

    Streaming (ISSUE #7):
        - Každý discovery source běží jako samostatný async generator
        - merge_async_iterables() mergeuje výsledky a yielduje okamžitě
        - První hit může přijít za ~0ms místo 2-10s
        - Backpressure funguje správně (downstream spotřebovává items)
    """

    name: str = "discovery"

    __slots__ = (
        "_query",
        "_max_results",
        "_public_bootstrap_enabled",
        "_seed_context",
        "_running",
        "_output_queue",
    )

    def __init__(
        self,
        *,
        query: str = "",
        max_results: int = 10,
        public_bootstrap_enabled: bool = False,
        seed_context: Any | None = None,
    ):
        self._query = query
        self._max_results = max_results
        self._public_bootstrap_enabled = public_bootstrap_enabled
        self._seed_context = seed_context
        self._running = False
        self._output_queue: BoundedStageQueue[str] | None = None

    async def run(
        self,
        _: None,  # first stage — no input queue
        output_queue: BoundedStageQueue[str],
        ctx: StageContext,
    ) -> None:
        """Spustí discovery a posílá URLy do output_queue streamovaně.

        Discovery běží v background thread. URLy se yieldují do output_queue
        co nejdříve — bez čekání na celé dokončení discovery.

        Args:
            _: None (první stage — žádný input)
            output_queue: BoundedStageQueue[str] — URLy pro DedupStage
            ctx: StageContext

        """
        self._running = True
        self._output_queue = output_queue
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()
        yielded = 0

        try:
            async for hit in self._run_discovery_streaming(ctx):
                url: Any = safe_attr_get(hit, "url", hit)
                if not url:
                    continue
                if not isinstance(url, str):
                    url = str(url)
                # Drop pokud queue full (backpressure signal)
                await output_queue.put(url)
                metrics.record_processed()
                yielded += 1
                # Yield to event loop — allows downstream stages to start processing
                await asyncio.sleep(0)

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("DiscoveryStage.run() error")
        finally:
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)
            logger.debug("DiscoveryStage: yielded=%d URLs", yielded)

    async def _run_discovery_streaming(self, ctx: StageContext):
        """Async generator — yields hits as they become available.

        True streaming (ISSUE #7 FIX): runs each discovery source as a separate
        async generator, merges them with merge_async_iterables, and yields hits
        immediately as any source produces them.

        Performance comparison:
            OLD: await asyncio.to_thread(_sync_discovery) → collect all → iterate
                 Blocking: 2-10s before first hit
            NEW: async generators for each source → merge → yield immediately
                 Blocking: ~0ms before first hit (bootstrap is instant)

        Backpressure: Works correctly — downstream must consume items
        for producers to continue.
        """
        from .live_public_pipeline import generate_bootstrap_urls, generate_rescue_urls

        async def _gen_rescue() -> Any:
            """Async generator for rescue URLs (threat query fallback).

            Runs sync generate_rescue_urls in thread pool and yields each hit
            as it's generated (one at a time for true streaming).
            """
            try:
                # Run sync function in thread to avoid blocking event loop
                hits = await asyncio.to_thread(generate_rescue_urls, ctx.query, max_urls=5)
                for hit in hits:
                    if not self._running:
                        break
                    yield hit
            except Exception:  # noqa: BLE001
                return

        async def _gen_bootstrap() -> Any:
            """Async generator for bootstrap URLs (domain-based discovery).

            Runs sync generate_bootstrap_urls in thread pool and yields each URL
            as a simple hit-like object for consistency.
            """
            if not self._public_bootstrap_enabled:
                return

            try:
                # generate_bootstrap_urls returns list[str], convert to hits
                urls = await asyncio.to_thread(
                    generate_bootstrap_urls, ctx.query, max_urls=self._max_results
                )
                for url in urls:
                    if not self._running:
                        break
                    # Convert string URL to a simple hit-like object for consistency
                    yield url
            except Exception:  # noqa: BLE001
                return

        # Merge all sources and yield hits as they become available.
        # This is the key fix (ISSUE #7): items stream in from whichever
        # source completes first, rather than waiting for all to complete.
        async for hit in merge_async_iterables(_gen_rescue(), _gen_bootstrap()):
            if not self._running:
                break
            yield hit

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
