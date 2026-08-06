"""P2-3: Store Stage — CanonicalFinding storage s bounded queue.

Role: Store stage přijímá CanonicalFinding z EnrichStage,
odesílá je do DuckDB přes store.submit_findings() s bounded queue.

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ._stage_protocol import BoundedStageQueue, StageContext

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_STORE_QUEUE_IN = 128  # CanonicalFinding čekající na store


class StoreStage:
    """Store stage: AsyncIterator[CanonicalFinding] → (stored to DuckDB).

    Poslední stage — přijímá CanonicalFinding, odesílá je do DuckDBShadowStore.
    Bounded queue: maxsize=128, drop na overflow (fail-safe, neblokuje).

    Memory: ~128 findings × ~2 KB = ~256 KB max.
    """

    name: str = "store"

    __slots__ = (
        "_store",
        "_batch_size",
        "_flush_interval_s",
        "_running",
        "_batch",
        "_last_flush",
    )

    def __init__(
        self,
        *,
        store: Any | None = None,
        batch_size: int = 50,
        flush_interval_s: float = 2.0,
    ):
        self._store = store
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._running = False
        self._batch: list[Any] = []
        self._last_flush: float = 0.0

    async def run(
        self,
        input_queue: BoundedStageQueue[Any] | None,
        output_queue: BoundedStageQueue[Any] | None,
        ctx: StageContext,
    ) -> None:
        _ = output_queue  # unused — last stage, no output queue
        """
        Zpracuje CanonicalFinding z input_queue, store je do DuckDB.

        Args:
            input_queue: BoundedStageQueue[CanonicalFinding]
            output_queue: None (poslední stage)
            ctx: StageContext
        """
        self._running = True
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()
        stored_count = 0
        dropped_count = 0

        try:
            while self._running:
                try:
                    if input_queue is None:
                        break
                    async with asyncio.timeout(2.0):
                        finding = await input_queue.get()
                except asyncio.TimeoutError:
                    # Periodic flush
                    if self._batch:
                        flushed = await self._flush_batch()
                        stored_count += flushed
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Add to batch
                self._batch.append(finding)
                metrics.record_processed()

                # Flush if batch full or interval elapsed
                should_flush = (
                    len(self._batch) >= self._batch_size
                    or (time.monotonic() - self._last_flush) >= self._flush_interval_s
                )
                if should_flush and self._batch:
                    flushed = await self._flush_batch()
                    stored_count += flushed

        except asyncio.CancelledError:
            pass
        except Exception:
            metrics.record_error()
            logger.exception("StoreStage.run() error")
        finally:
            # Final flush
            if self._batch:
                flushed = await self._flush_batch()
                stored_count += flushed
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)
            logger.debug("StoreStage: stored=%d, dropped=%d", stored_count, dropped_count)

    async def _flush_batch(self) -> int:
        """Flush accumulated batch to DuckDB. Returns count stored."""
        if not self._batch:
            return 0

        batch_to_store = self._batch[:]
        self._batch = []
        self._last_flush = time.monotonic()

        if self._store is None:
            return 0

        try:
            await self._store.submit_findings(batch_to_store)
            return len(batch_to_store)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("StoreStage._flush_batch error: %s")
            return 0

    async def aclose(self) -> None:
        """Graceful shutdown — flush remaining batch."""
        self._running = False
        if self._batch:
            await self._flush_batch()
