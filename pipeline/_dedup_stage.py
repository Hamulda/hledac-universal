"""P2-3: Dedup Stage — URL deduplication s RotatingBloomFilter.

Role: Dedup stage přijímá URLy z DiscoveryStage, deduplikuje je přes RotatingBloomFilter,
posílá unikátní URLy do FetchStage.

"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    from ._stage_protocol import BoundedStageQueue, StageContext

logger = logging.getLogger(__name__)

_MAX_FALLBACK_DEDUP = 10_000  # Bounded — CLAUDE.md invariant

# Module-level lazy cache for deduper factory (avoids global statement)
_DEDUPER_FACTORY: Any = object()  # sentinel: not yet tried


def _load_deduper_factory() -> Any:
    """Load deduper factory with graceful fallback. Called once."""
    try:
        from ._deduper import make_run_deduper  # noqa: PLC0415
    except Exception:
        return None
    else:
        return make_run_deduper


class DedupStage:
    """Dedup stage: AsyncIterator[str(url)] → AsyncIterator[str(url)].

    Používá RotatingBloomFilter pro dedup — existující pattern v codebase.
    URL dedup pouze přes RotatingBloomFilter (CLAUDE.md invariant).

    Memory: ~1 MB pro BloomFilter (10K items).

    Fallback: LRU dict (bounded) — nikdy unbounded set.
    """

    name: str = "dedup"

    __slots__ = (
        "_bloom",
        "_capacity",
        "_running",
        "_seen",
    )

    def __init__(self, *, capacity: int = 10_000) -> None:
        self._bloom: Any = None
        self._seen: dict[str, None] = {}  # LRU: odstraně nejstarší při capacity
        self._capacity = capacity
        self._running = False

    def _get_deduper(self) -> Any:
        """Lazily initialize the deduper, returning None on failure."""
        if self._bloom is None:
            # Try module-level cache first
            global _DEDUPER_FACTORY  # noqa: PLW0603
            if _DEDUPER_FACTORY is object():
                _DEDUPER_FACTORY = _load_deduper_factory()
            if _DEDUPER_FACTORY is not None:
                self._bloom = _DEDUPER_FACTORY()
        return self._bloom

    async def run(
        self,
        input_queue: BoundedStageQueue[str] | None,
        output_queue: BoundedStageQueue[str],
        ctx: StageContext,
    ) -> None:
        """Deduplikuje URLy z input_queue, posílá unikátní do output_queue.

        Args:
            input_queue: BoundedStageQueue[str] — URLy z DiscoveryStage
            output_queue: BoundedStageQueue[str] — unikátní URLy pro FetchStage
            ctx: StageContext

        """
        self._running = True
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()
        seen_count = 0
        dedup_count = 0

        try:
            while self._running:
                try:
                    if input_queue is None:
                        break
                    async with asyncio.timeout(5.0):
                        url = await input_queue.get()
                except TimeoutError:
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Dedup check
                is_new = self._check_and_add(url)
                if is_new:
                    seen_count += 1
                    # Put do output (drop if full)
                    await output_queue.put(url)
                    metrics.record_processed()
                else:
                    dedup_count += 1
                    metrics.record_dropped()

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("DedupStage.run() error")
        finally:
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)
            logger.debug(
                "DedupStage: seen=%d, dedup=%d", seen_count, dedup_count,
            )

    def _check_and_add(self, url: str) -> bool:
        """Check if URL is new and add to dedup structure.

        Returns:
            True if URL is new (wasn't seen before).
            False if URL was already seen (duplicate).

        """
        try:
            deduper = self._get_deduper()
            if deduper is not None:
                return deduper.is_new(url)
        except Exception:  # noqa: BLE001
            pass

        # Fallback: bounded LRU dict (evict oldest when over capacity)
        if url in self._seen:
            return False
        if len(self._seen) >= self._capacity:
            # Evict oldest ~10%
            evict_count = max(1, self._capacity // 10)
            for _ in range(evict_count):
                try:
                    self._seen.pop(next(iter(self._seen)))
                except StopIteration:
                    break
        self._seen[url] = None
        return True

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
