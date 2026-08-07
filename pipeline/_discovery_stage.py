"""P2-3: Discovery Stage — URL discovery s bounded queue output.

Role: Discovery stage produkuje URLy (str) do output queue pro DedupStage.
Je to první stage — nemá input queue.

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.config_introspection import safe_attr_get

from ._stage_protocol import BoundedStageQueue, StageContext

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DiscoveryStage:
    """Discovery stage: žádný input → AsyncIterator[str(url)] → output_queue.

    Produkuje URLy pro DedupStage. Používá existující discovery logiku
    z live_public_pipeline (bootstrap, rescue, duckduckgo search).

    Memory: ~10 MB pro hit list, ~5 MB pro seen set.
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

        Runs sync discovery in background thread and yields hits incrementally.
        """
        from .live_public_pipeline import generate_bootstrap_urls, generate_rescue_urls

        def _sync_discovery() -> list[Any]:
            hits: list[Any] = []
            try:
                try:
                    rescue_hits = generate_rescue_urls(ctx.query, max_urls=5)
                    hits.extend(rescue_hits)
                except Exception:  # noqa: BLE001
                    pass

                if self._public_bootstrap_enabled:
                    try:
                        bootstrap_hits = generate_bootstrap_urls(
                            ctx.query, max_urls=self._max_results
                        )
                        hits.extend(bootstrap_hits)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            return hits

        try:
            hits = await asyncio.to_thread(_sync_discovery)
            for hit in hits or []:
                if not self._running:
                    break
                yield hit
        except Exception:
            return

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
