"""
brain/mlx_batch_coordinator.py — Sprint G2: MLX Batch Coordinator
=========================================================




Extracted from DeepHermes3Engine to reduce complexity (91 → smaller method groups).
Manages the batch queue, worker, and batch processing pipeline.

Responsibilities:
- PriorityQueue management for structured output requests
- Batch collection with schema/length/prompt segregation
- Background batch worker lifecycle
- Backpressure via queue depth monitoring

M1 8GB invariant: bounded queue (256), adaptive flush intervals,
backpressure under high pressure (>64 items) and critical (>192 items).
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.utils.asyncx import safe_create_task


# ---------------------------------------------------------------------------
# Constants (must match DeepHermes3Engine defaults)
# ---------------------------------------------------------------------------

MAX_PENDING_FUTURES = 256  # Max pending inference futures


@dataclass(frozen=True, slots=True)
class BatchConfig:
    """Configuration for batch coordinator."""
    max_size: int = 8  # max items per batch
    default_flush_interval: float = 2.0  # seconds
    medium_pressure_depth: int = 64
    high_pressure_depth: int = 192
    age_bump_interval: int = 3  # flushes between age bumps


@dataclass(frozen=True, slots=True)
class BatchStats:
    """Telemetry counters for batch processing."""
    schema_mismatch_flushes: int = 0
    prompt_mismatch_flushes: int = 0
    length_bin_mismatch_flushes: int = 0
    backpressure_critical_cycles: int = 0
    backpressure_high_cycles: int = 0
    backpressure_deferred_low_priority: int = 0
    backpressure_skipped_low_priority: int = 0
    adaptive_flush_default_entries: int = 0
    adaptive_flush_medium_entries: int = 0
    adaptive_flush_fast_entries: int = 0


# ---------------------------------------------------------------------------
# Batch Coordinator
# ---------------------------------------------------------------------------


class MLXBatchCoordinator:
    """
    Manages MLX inference batching with priority queue and backpressure.

    Extracted from DeepHermes3Engine for better separation of concerns.
    Thread-compatible: async methods only, no shared state mutation across threads.
    """

    def __init__(self, config: BatchConfig | None = None) -> None:
        self._config = config or BatchConfig()
        self._queue: asyncio.PriorityQueue | None = None
        self._worker_task: asyncio.Task | None = None
        self._pending_futures: set[asyncio.Future] = set()
        self._tie_breaker = itertools.count()
        self._worker_shutting_down = False
        self._flush_cycle_count = 0
        self._last_age_bump = 0
        self._stats = BatchStats()

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    @property
    def queue(self) -> asyncio.PriorityQueue | None:
        """Get the batch queue (lazy initialized)."""
        return self._queue

    @property
    def queue_depth(self) -> int:
        """Current queue depth, or 0 if not initialized."""
        return self._queue.qsize() if self._queue else 0

    async def start_worker(self) -> None:
        """Start the background batch worker (lazy)."""
        if self._worker_task is not None:
            return
        self._queue = asyncio.PriorityQueue(maxsize=256)
        self._pending_futures = set()
        self._worker_shutting_down = False
        self._worker_task = safe_create_task(self._run_worker())
        self._flush_cycle_count = 0
        self._last_age_bump = 0

    async def stop_worker(self, timeout: float = 3.0) -> None:
        """Stop the background batch worker with graceful shutdown."""
        if self._worker_task is None:
            self._queue = None
            return

        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError('batch_worker_shutdown'))
        self._pending_futures.clear()
        self._worker_shutting_down = True
        self._worker_task.cancel()

        try:
            async with asyncio.timeout(timeout):
                await asyncio.shield(self._worker_task)
        except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001
            pass
        finally:
            self._worker_task = None
            self._queue = None

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def submit(
        self,
        priority: float,
        schema_key: str,
        payload: dict,
    ) -> asyncio.Future:
        """
        Submit a batch item.

        Args:
            priority: Lower = higher priority (0 = highest)
            schema_key: Schema identifier for grouping
            payload: Item payload

        Returns:
            Future that resolves when item is processed
        """
        if self._queue is None:
            await self.start_worker()

        future = asyncio.Future()
        payload_with_future = {**payload, 'future': future}

        if len(self._pending_futures) >= MAX_PENDING_FUTURES:
            done = [f for f in self._pending_futures if f.done()]
            if done:
                self._pending_futures.discard(done[0])
            else:
                raise RuntimeError('pending_futures overflow')

        self._pending_futures.add(future)

        def _safe_discard(f: asyncio.Future) -> None:
            self._pending_futures.discard(f)

        future.add_done_callback(_safe_discard)
        tie = next(self._tie_breaker)
        future._enqueue_ns = time.monotonic_ns()
        await self._queue.put((priority, tie, schema_key, payload_with_future))
        return future

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run_worker(self) -> None:
        """Background worker that collects and processes batches."""
        while True:
            try:
                await asyncio.sleep(0)  # yield for cancellation
            except asyncio.CancelledError:
                break

            if self._worker_shutting_down:
                self._cancel_pending_futures('worker_shutdown')
                break

            try:
                items, schema_key, prompt_hash, length_bin = await self._collect_batch()
                if not items:
                    continue

                self._flush_cycle_count += 1
                if self._flush_cycle_count - self._last_age_bump >= self._config.age_bump_interval:
                    self._last_age_bump = self._flush_cycle_count
                    await self._age_bump_queue()

                await self._process_batch(items)
            except asyncio.CancelledError:
                break
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Batch worker error: {e}")

    async def _collect_batch(self) -> tuple[list, Any, Any, Any]:
        """Collect batch items with backpressure."""
        flush_interval = self._current_flush_interval()

        try:
            async with asyncio.timeout(flush_interval):
                first_item = await self._queue.get()
        except TimeoutError:
            return [], None, None, None

        queue_depth = self._queue.qsize()
        pressure_tier = "normal"
        if queue_depth > self._config.high_pressure_depth:
            pressure_tier = "critical"
            self._stats.backpressure_critical_cycles += 1
        elif queue_depth > self._config.medium_pressure_depth:
            pressure_tier = "high"
            self._stats.backpressure_high_cycles += 1

        first_priority = first_item[0]
        if pressure_tier == "critical" and first_priority > 5:
            await self._queue.put(first_item)
            self._stats.backpressure_deferred_low_priority += 1
            return [], None, None, None

        items = [first_item]
        current_schema = first_item[2]
        current_prompt_hash = self._compute_hash(first_item[3].get('system_msg', ''))
        current_length_bin = self._compute_length_bin(first_item[3].get('prompt', ''))

        while len(items) < self._config.max_size:
            try:
                async with asyncio.timeout(0.01):
                    item = await self._queue.get_nowait()
            except TimeoutError:
                break

            item_priority = item[0]
            if pressure_tier in ("high", "critical") and item_priority > 5:
                self._stats.backpressure_skipped_low_priority += 1
                continue

            if item[2] != current_schema:
                await self._queue.put(item)
                self._stats.schema_mismatch_flushes += 1
                break
            if self._compute_hash(item[3].get('system_msg', '')) != current_prompt_hash:
                await self._queue.put(item)
                self._stats.prompt_mismatch_flushes += 1
                break
            if self._compute_length_bin(item[3].get('prompt', '')) != current_length_bin:
                await self._queue.put(item)
                self._stats.length_bin_mismatch_flushes += 1
                break

            items.append(item)

        return items, current_schema, current_prompt_hash, current_length_bin

    def _current_flush_interval(self) -> float:
        """Adaptive flush interval based on queue depth."""
        if self._queue is None:
            return self._config.default_flush_interval
        depth = self._queue.qsize()
        if depth > self._config.high_pressure_depth:
            return 0.5
        if depth > self._config.medium_pressure_depth:
            return 1.0
        return self._config.default_flush_interval

    async def _process_batch(self, items: list) -> None:
        """
        Process a batch of items — override in subclass or inject processor.

        Default implementation: subclasses override _process_single.
        """
        for _, _, _, payload in items:
            future = payload.get('future')
            if future and not future.done():
                try:
                    result = await self._process_single(payload)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)

    async def _process_single(self, payload: dict) -> Any:
        """Process a single item — override in subclass."""
        raise NotImplementedError

    def _compute_hash(self, text: str) -> str:
        """Compute hash for prompt segregation."""
        import hashlib
        return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:8]

    def _compute_length_bin(self, text: str) -> str:
        """Length binning for batch segregation."""
        tokens_est = len(text) // 4
        if tokens_est < 256:
            return 'short'
        elif tokens_est < 1024:
            return 'medium'
        return 'long'

    async def _age_bump_queue(self) -> None:
        """Age-bump: improve priority of waiting items."""
        if self._queue is None or self._queue.empty():
            return
        items = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for priority, tie, schema, payload in items:
            new_priority = max(0, priority - 1)
            await self._queue.put((new_priority, tie, schema, payload))

    def _cancel_pending_futures(self, reason: str) -> None:
        """Cancel all pending futures."""
        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError(reason))

    def get_stats(self) -> BatchStats:
        """Get batch processing statistics."""
        return self._stats
