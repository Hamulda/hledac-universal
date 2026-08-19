"""P2-3: Dedup Stage — URL deduplication s RotatingBloomFilter + B5 Rust pipeline.

Role: Dedup stage přijímá URLy z DiscoveryStage, deduplikuje je přes RotatingBloomFilter,
posílá unikátní URLy do FetchStage.

B5 Pipeline Compose Integration:
- Uses pipeline_filter_async("has_scheme") to filter valid URLs
- Uses pipeline_map_async("strip") to normalize URLs before dedup
- Batch processing with 100 items/batch bound for M1 8GB safety
- Zero-alloc pipeline composition via asyncio.to_thread

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

# B5. Pipeline Compose — imports with graceful fallback
try:
    from rust_extensions.wiring.pipeline_compose_wiring import (
        BATCH_SIZE,
        pipeline_filter_async,
        pipeline_map_async,
        pipeline_batch_stats_async,
    )
except ImportError:
    # Fallback when Rust extension unavailable
    BATCH_SIZE = 100

    async def pipeline_filter_async(items, fn_name):
        return items

    async def pipeline_map_async(items, fn_name):
        return items

    async def pipeline_batch_stats_async(items):
        return None


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
        "_batch_buffer",
    )

    def __init__(self, *, capacity: int = 10_000) -> None:
        self._bloom: Any = None
        self._seen: dict[str, None] = {}  # LRU: odstraně nejstarší při capacity
        self._capacity = capacity
        self._running = False
        self._batch_buffer: list[str] = []  # B5. Buffer for batch processing

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

        B5 Pipeline Compose:
        - Buffers URLs in batches of BATCH_SIZE (100 for M1 8GB)
        - Uses pipeline_filter_async("has_scheme") to filter valid URLs
        - Uses pipeline_map_async("strip") to normalize URLs
        - Zero-alloc via asyncio.to_thread to Rust pipeline_compose

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
        batch_count = 0

        try:
            while self._running:
                try:
                    if input_queue is None:
                        break
                    async with asyncio.timeout(5.0):
                        url = await input_queue.get()
                except TimeoutError:
                    # Flush remaining buffer on timeout
                    if self._batch_buffer:
                        batch_count += 1
                        flushed = await self._process_batch_b5(output_queue, metrics)
                        seen_count += flushed
                        self._batch_buffer.clear()
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue
                except asyncio.CancelledError:
                    break

                # Add to batch buffer
                self._batch_buffer.append(url)

                # Process batch when full
                if len(self._batch_buffer) >= BATCH_SIZE:
                    batch_count += 1
                    flushed = await self._process_batch_b5(output_queue, metrics)
                    seen_count += flushed
                    self._batch_buffer.clear()

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("DedupStage.run() error")
        finally:
            # Flush remaining buffer
            if self._batch_buffer:
                batch_count += 1
                flushed = await self._process_batch_b5(output_queue, metrics)
                seen_count += flushed
                self._batch_buffer.clear()

            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)
            logger.debug(
                "DedupStage: seen=%d, dedup=%d, batches=%d",
                seen_count, dedup_count, batch_count,
            )

    async def _process_batch_b5(
        self,
        output_queue: BoundedStageQueue[str],
        metrics: Any,
    ) -> int:
        """B5. Process a batch of URLs through pipeline_compose.

        Pipeline: pipeline_filter_async("has_scheme") → pipeline_map_async("strip")
        Then runs dedup on processed URLs.

        Args:
            output_queue: Queue to send unique URLs to
            metrics: Stage metrics

        Returns:
            Number of unique URLs sent to output_queue
        """
        if not self._batch_buffer:
            return 0

        batch = self._batch_buffer
        seen_count_batch = 0

        try:
            # B5. Filter valid URLs (has_scheme) via asyncio.to_thread
            valid_urls: list[str] = await pipeline_filter_async(batch, "has_scheme")

            # B5. Normalize URLs (strip) via asyncio.to_thread
            if valid_urls:
                normalized_urls: list[str] = await pipeline_map_async(
                    valid_urls, "strip"
                )
                # Filter empty strings after strip
                normalized_urls = [u for u in normalized_urls if u]
            else:
                normalized_urls = []

            # Run dedup on processed URLs
            for url in normalized_urls:
                is_new = self._check_and_add(url)
                if is_new:
                    seen_count_batch += 1
                    await output_queue.put(url)
                    metrics.record_processed()
                else:
                    metrics.record_dropped()

        except Exception:  # noqa: BLE001
            # Fallback: process without B5
            for url in batch:
                is_new = self._check_and_add(url)
                if is_new:
                    seen_count_batch += 1
                    await output_queue.put(url)
                    metrics.record_processed()
                else:
                    metrics.record_dropped()

        return seen_count_batch

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
