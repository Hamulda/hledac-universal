"""P2-3: Match Stage — Pattern matching s bounded queue.

Role: Match stage přijímá PageResult z FetchStage, provádí pattern matching,
posílá matches do EnrichStage.

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ._stage_protocol import BoundedStageQueue, StageContext
from core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_MATCH_QUEUE_IN = 64
DEFAULT_MATCH_QUEUE_OUT = 128


class MatchStage:
    """Match stage: AsyncIterator[PageResult] → AsyncIterator[tuple[PageResult, list[PatternHit]]].

    Provede pattern matching na page text a posílá (page_result, hits) do EnrichStage.

    Memory: ~64 page results v queue = ~128 MB max.
    """

    name: str = "match"

    __slots__ = (
        "_running",
    )

    def __init__(self) -> None:
        self._running = False

    async def run(
        self,
        input_queue: BoundedStageQueue[Any] | None,
        output_queue: BoundedStageQueue[Any],
        ctx: StageContext,
    ) -> None:
        """Zpracuje PageResult z input_queue, matchuje patterny, posílá do output_queue.

        Args:
            input_queue: BoundedStageQueue[PageResult]
            output_queue: BoundedStageQueue[tuple[PageResult, list[PatternHit]]]
            ctx: StageContext

        """
        self._running = True
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()

        try:
            while self._running:
                try:
                    if input_queue is None:
                        break
                    async with asyncio.timeout(5.0):
                        page_result = await input_queue.get()
                except asyncio.TimeoutError:
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Pattern match
                hits = await self._match_one(page_result)
                metrics.record_processed()

                # Put (page_result, hits) tuple do output
                await output_queue.put((page_result, hits))

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("MatchStage.run() error")
        finally:
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)

    async def _match_one(self, page_result: Any) -> list[Any]:
        """Provede pattern matching na page text."""
        try:
            from hledac.universal.utils.patterns.pattern_matcher import match_text
        except Exception:
            return []

        try:
            page_text = getattr(page_result, "text", "") or ""
            if not page_text:
                return []

            try:
                hits = match_text(page_text)
                return hits or []
            except Exception:
                return []

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("MatchStage._match_one error")
            return []

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
