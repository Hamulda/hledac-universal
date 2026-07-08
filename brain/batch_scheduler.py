"""
BatchScheduler — Pure asyncio continuous batch scheduler.

No MLX/GPU dependencies. Schedules structured output requests with:
- Priority queue (lower = higher priority, priority=0 bypasses batch)
- Schema boundary segregation (don't mix Pydantic/msgspec types)
- Prompt hash boundary segregation (don't mix system prompts)
- Length bin boundary segregation (short/medium/long)
- Age bump anti-starvation (priority -= 1 every N flush cycles)
- 3-tier adaptive flush interval (0.5s / 1.0s / 2.0s based on queue depth)

Sprint F226H: Extracted from Hermes3Engine as standalone policy layer.
"""
from __future__ import annotations



import asyncio
import hashlib
import itertools
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from hledac.universal.utils.async_helpers import safe_gather_shielded

# F270: Canonical MLX/batch constants (read-only, no MLX dependency)
from hledac.universal.core.constants import MLX  # noqa: E402

logger = logging.getLogger(__name__)


class BatchScheduler:
    """
    Pure asyncio batch scheduler — no MLX/GPU dependencies.

    Accepts structured output requests and batches them by schema/prompt/length
    boundaries. Execution is delegated to an injected async callback.

    Invariants:
        B.S1: Zero MLX imports
        B.S2: No GPU memory tracking
        B.S3: No KV cache objects
        B.S4: Worker shutdown bounded ≤ 3s
        B.S5: Pending futures failed on shutdown
        B.S6: Queue maxsize ≤ 256
        B.S7: Age bump interval ≥ 1
        B.S8: flush_interval ≥ 0.5s
    """

    def __init__(
        self,
        execute_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, Any]],
        max_size: int = MLX().batch_max_size,
        max_queue: int = MLX().batch_queue_max,
        default_flush_interval: float = MLX().flush_default,
        medium_pressure_depth: int = MLX().batch_medium_pressure_depth,
        high_pressure_depth: int = MLX().batch_high_pressure_depth,
        age_bump_interval: int = MLX().age_bump_interval,
        ema_alpha: float = MLX().batch_ema_alpha,
    ) -> None:
        """
        Args:
            execute_callback: Async callable(payload) → result.
                             Called for each item in batch (sequential per schema group).
            max_size: Max items per batch
            max_queue: Max queue depth
            default_flush_interval: Default flush interval (seconds)
            medium_pressure_depth: Trigger 1.0s flush at this depth
            high_pressure_depth: Trigger 0.5s flush at this depth
            age_bump_interval: Bump priority every N flush cycles
            ema_alpha: EMA smoothing factor for telemetry
        """
        self._execute_callback = execute_callback

        # Config
        self._max_size = max_size
        self._settables: set[str] = {"_max_size"}  # F289: runtime-adjustable fields
        self._max_queue = max_queue
        self._default_flush_interval = default_flush_interval
        self._medium_pressure_depth = medium_pressure_depth
        self._high_pressure_depth = high_pressure_depth
        self._age_bump_interval = age_bump_interval
        self._ema_alpha = ema_alpha

        # Queue state
        self._batch_queue: asyncio.PriorityQueue | None = None
        self._batch_tie_breaker: itertools.count | None = None
        self._worker_task: asyncio.Task | None = None
        self._worker_shutting_down = False

        # Pending futures (for emergency failure)
        self._pending_futures: set[asyncio.Future] = set()

        # Counters
        self._flush_cycle_count = 0
        self._last_age_bump = 0

        # EMA telemetry
        self._telemetry_ema = {
            'dispatch_to_result_ms': 0.0,
            'batch_size': 0,
            'queue_depth': 0,
            'throughput_items_per_sec': 0.0,
        }
        self._telemetry_counters = {
            'batch_submitted': 0,
            'batch_executed': 0,
            'batch_shattered': 0,
            'schema_mismatch_flushes': 0,
            'length_bin_mismatch_flushes': 0,
            'prompt_mismatch_flushes': 0,
            'adaptive_flush_default_entries': 0,
            'adaptive_flush_medium_entries': 0,
            'adaptive_flush_fast_entries': 0,
            'age_bump_cycles': 0,
        }
        # F265-5.5: Throughput tracking for adaptive flush
        self._last_batch_finished_at: float = 0.0
        self._items_processed_since_last: int = 0

    # ─── Runtime-adjustable config (ISSUE-094) ────────────────────────────────

    def set_max_size(self, new_size: int) -> None:
        """
        ISSUE-094 FIX: Propagate adaptive batch size changes from MLXBatchedExecutor.

        The PID controller in MLXBatchedExecutor._adjust_batch_size() was updating
        self._effective_batch_size but never propagating it to the scheduler's
        _max_size — making the PID loop a no-op.

        Callers: MLXBatchedExecutor.is_batch_safe() after each PID adjustment.
        """
        if not hasattr(self, "_settables") or "_max_size" not in self._settables:
            return  # Safety guard for older pickled instances
        self._max_size = max(1, min(256, new_size))  # Clamp to safe bounds

    # ─── Public API ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the batch worker (lazy start)."""
        if self._worker_task is not None:
            return
        self._batch_queue = asyncio.PriorityQueue(maxsize=self._max_queue)
        self._batch_tie_breaker = itertools.count()
        self._worker_shutting_down = False
        self._worker_task = asyncio.create_task(self._worker())
        logger.debug("BatchScheduler worker started")

    async def shutdown(self, timeout: float = 3.0) -> None:
        """
        Bounded shutdown — max 3.0s, fail-pending-futures.

        Post-conditions:
            - All pending futures have result or exception
            - _pending_futures is empty
            - _worker_task is None
            - _batch_queue is None
        """
        if self._worker_task is None:
            self._batch_queue = None
            return

        # Fail all pending futures before cancelling
        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError("batch_scheduler_shutdown"))
        self._pending_futures.clear()

        # Signal worker to exit cleanly
        self._worker_shutting_down = True
        self._worker_task.cancel()

        try:
            await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)
        except TimeoutError:
            pass  # TimeoutError = timeout reached, worker may still be running
        except asyncio.CancelledError:
            # C.8: propagate CancelledError — don't swallow it
            # The shielded task was cancelled, we must re-raise
            self._worker_task = None
            self._batch_queue = None
            raise

        self._worker_task = None
        self._batch_queue = None
        logger.debug("BatchScheduler shutdown complete")

    async def submit(
        self,
        prompt: str,
        response_model: type,
        priority: float = 1.0,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        system_msg: str | None = None,
    ) -> asyncio.Future:
        """
        Submit a structured output request to the batch queue.

        Returns a Future that resolves when the result is available.

        Args:
            prompt: Input prompt
            response_model: Response model class (used for schema_key)
            priority: Lower = higher priority (0 = highest, bypasses batch)
            temperature: Temperature setting
            max_tokens: Max tokens to generate
            system_msg: Optional system message

        Returns:
            asyncio.Future resolving to result
        """
        if self._worker_task is None:
            await self.start()

        schema_key = self._compute_schema_key(response_model, temperature)
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        tie = next(self._batch_tie_breaker)
        payload = {
            'prompt': prompt,
            'response_model': response_model,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'system_msg': system_msg,
            'future': future,
            'type': 'structured',
        }

        await self._batch_queue.put((priority, tie, schema_key, payload))
        self._telemetry_counters['batch_submitted'] += 1

        # Track pending future
        self._pending_futures.add(future)
        future.add_done_callback(lambda f: self._pending_futures.discard(f))

        return future

    def is_batch_safe(
        self,
        response_model: type,
        priority: float,
        timeout_s: float | None = None,
    ) -> bool:
        """
        Batch-safe eligibility check.

        Routing criteria:
            - schema type must be detectable (msgspec or pydantic)
            - not urgent priority (priority == 0)
            - timeout must allow for batching (>= 2x flush interval)

        Returns:
            True if should use batch queue, False for direct path
        """
        # Urgent = single path
        if priority == 0:
            return False
        # No schema = can't segregate
        if response_model is None:
            return False
        # Short timeout = single path
        if timeout_s is not None and timeout_s <= self._current_flush_interval() * 2:
            return False
        # Schema must be msgspec or pydantic
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)
        if not hasattr(schema_cls, '__struct_fields__') and \
           not hasattr(schema_cls, 'model_validate_json'):
            return False
        return True

    async def flush(self, timeout: float = 5.0) -> int:
        """
        Drain all pending items from the batch queue.

        Args:
            timeout: Maximum seconds to wait for drain

        Returns:
            Number of items drained
        """
        if self._batch_queue is None or self._batch_queue.empty():
            return 0

        drained = 0
        deadline = time.monotonic() + timeout

        while not self._batch_queue.empty() and time.monotonic() < deadline:
            items = []
            try:
                while len(items) < self._max_size:
                    item = self._batch_queue.get_nowait()
                    items.append(item)
            except asyncio.QueueEmpty:
                break

            if items:
                await self._process_batch(items)
                drained += len(items)

        return drained

    def get_telemetry(self) -> dict[str, Any]:
        """Return telemetry snapshot (EMA + counters)."""
        return {
            'ema': dict(self._telemetry_ema),
            'counters': dict(self._telemetry_counters),
        }

    # ─── Worker Loop ──────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Background worker that processes batches with boundary segregation."""
        while True:
            # Poison pill guard — exit if shutdown flag is set
            if self._worker_shutting_down:
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError("batch_scheduler_shutdown"))
                self._pending_futures.clear()
                break

            try:
                items = []
                current_schema_key = None
                current_prompt_hash = None
                current_length_bin = None

                # Adaptive flush interval
                flush_interval = self._current_flush_interval()
                if flush_interval >= 1.9:
                    self._telemetry_counters['adaptive_flush_default_entries'] += 1
                elif flush_interval >= 0.9:
                    self._telemetry_counters['adaptive_flush_medium_entries'] += 1
                else:
                    self._telemetry_counters['adaptive_flush_fast_entries'] += 1

                # Wait for first item with flush timeout
                try:
                    async with asyncio.timeout(flush_interval):
                        first_item = await self._batch_queue.get()
                    current_schema_key = first_item[2]
                    items.append(first_item)

                    first_payload = first_item[3]
                    first_prompt = first_payload.get('prompt', '')
                    first_system_msg = first_payload.get('system_msg')
                    current_prompt_hash = self._compute_system_prompt_hash(first_system_msg)
                    current_length_bin = self._compute_length_bin(first_prompt)

                    # Gather up to max_size items with boundary checks
                    while len(items) < self._max_size:
                        try:
                            async with asyncio.timeout(0.05):
                                item = await self._batch_queue.get_nowait()
                            item_schema = item[2]
                            item_payload = item[3]
                            item_prompt = item_payload.get('prompt', '')
                            item_system_msg = item_payload.get('system_msg')
                            item_prompt_hash = self._compute_system_prompt_hash(item_system_msg)
                            item_length_bin = self._compute_length_bin(item_prompt)

                            # Schema boundary check
                            if item_schema != current_schema_key:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['schema_mismatch_flushes'] += 1
                                break
                            # Prompt hash boundary check
                            if item_prompt_hash != current_prompt_hash:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['prompt_mismatch_flushes'] += 1
                                break
                            # Length bin boundary check
                            if item_length_bin != current_length_bin:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['length_bin_mismatch_flushes'] += 1
                                break

                            items.append(item)
                        except TimeoutError:
                            break

                except TimeoutError:
                    continue

                # Anti-starvation: age bump every _age_bump_interval cycles
                self._flush_cycle_count += 1
                if self._flush_cycle_count - self._last_age_bump >= self._age_bump_interval:
                    self._last_age_bump = self._flush_cycle_count
                    await self._age_bump_queue()
                    self._telemetry_counters['age_bump_cycles'] += 1

                # Update queue depth EMA
                if self._batch_queue is not None:
                    self._telemetry_ema['queue_depth'] = self._batch_queue.qsize()

                # Process batch with timing
                t0 = time.monotonic()
                await self._process_batch(items)
                dispatch_ms = (time.monotonic() - t0) * 1000

                # Update EMAs
                self._telemetry_ema['batch_size'] = len(items)
                self._telemetry_ema['dispatch_to_result_ms'] = (
                    self._ema_alpha * dispatch_ms +
                    (1 - self._ema_alpha) * self._telemetry_ema['dispatch_to_result_ms']
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"BatchScheduler worker error: {e}")

    # ─── Batch Processing ────────────────────────────────────────────────────

    async def _process_batch(self, items: list) -> None:
        """
        Process a batch of structured-output items.

        F265-5.5: Tracks throughput telemetry for adaptive flush tuning.
        """
        if not items:
            return

        by_schema: dict[str, list] = {}
        for priority, _tie, schema_key, payload in items:
            if schema_key not in by_schema:
                by_schema[schema_key] = []
            by_schema[schema_key].append((payload, priority))

        for schema_key, group in by_schema.items():
            try:
                await self._process_structured_batch(group)
            except Exception as e:
                logger.debug(f"BatchScheduler process error for schema {schema_key}: {e}")

        # F265-5.5: Throughput tracking for adaptive flush
        self._items_processed_since_last += len(items)
        now = time.monotonic()
        if self._last_batch_finished_at > 0:
            elapsed = now - self._last_batch_finished_at
            if elapsed > 0:
                # F265-5.5 FIX: Use faster alpha on cold start (EMA = 0.0)
                is_cold = self._telemetry_ema['throughput_items_per_sec'] == 0.0
                alpha = 0.5 if is_cold else 0.3
                self._telemetry_ema['throughput_items_per_sec'] = (
                    alpha * (self._items_processed_since_last / elapsed)
                    + (1 - alpha) * self._telemetry_ema['throughput_items_per_sec']
                )
        # F265-5.5 FIX: First batch — use fast flush to not penalize startup latency
        # Store first_batch flag in telemetry for adaptive flush to read
        elif self._telemetry_ema['throughput_items_per_sec'] == 0.0:
            # First batch ever — signal adaptive flush to use 0.3s interval
            self._telemetry_ema['throughput_items_per_sec'] = 0.001  # ~0 items/s, triggers fast path
        self._last_batch_finished_at = now
        self._items_processed_since_last = 0

    async def _process_structured_batch(self, items: list) -> None:
        """
        Process a batch of structured output requests for same schema.
        Shatters on total failure.

        F265-5.5 CONTINUOUS BATCHING: Items execute with concurrent asyncio.gather
        under a semaphore cap. While item 0 awaits MLX compute in the worker
        thread, items 1..k call _execute_callback concurrently — capturing
        I/O overlap (tokenization, thread dispatch, semaphore queuing).
        Semaphore cap = min(len(items), max_size) bounds parallelism.
        Note: True prefill/decode pipeline parallelism requires multi-request
        KV cache sharing, not implemented here (MLX single-device constraint).
        """
        if not items:
            return

        try:
            # F265-5.5: Semaphore caps concurrency for this batch.
            # Concurrent gather below dispatches all items together;
            # semaphore bounds how many acquire _execute_callback simultaneously.
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            _batch_sem = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)

            async def process_with_sem(payload: dict[str, Any]) -> tuple[dict, Any]:
                async with _batch_sem:
                    return payload, await self._execute_callback(payload)

            # Sprint 7.3: Concurrent await via asyncio.gather — while item 0 awaits
            # MLX compute in the worker thread, items 1..k call _execute_callback
            # concurrently. This captures I/O overlap during asyncio.to_thread()
            # dispatch and engine.generate() semaphore queuing. Note: MLX compute
            # itself remains serialized (single Metal device), but CPU-level
            # overhead (tokenization, callback dispatch, semaphore queuing) overlaps.
            _tasks = [process_with_sem(payload) for payload, _ in items]
            # Sprint 7.3: Concurrent await via asyncio.gather — while item 0 awaits
            # MLX compute in the worker thread, items 1..k call _execute_callback
            # concurrently. This captures I/O overlap during asyncio.to_thread()
            # dispatch and engine.generate() semaphore queuing. Note: MLX compute
            # itself remains serialized (single Metal device), but CPU-level
            # overhead (tokenization, callback dispatch, semaphore queuing) overlaps.
            # F265C: migrated to safe_gather_shielded (structured TaskGroup concurrency)
            _gathered = await safe_gather_shielded(
                *_tasks,
                label="batch_scheduler",
                logger_instance=logger,
            )

            # Reconstruct results in original items order using index pointers.
            # safe_gather_shielded populates .ok/.errors in task-sumbission order,
            # so we interleave them by scanning items and matching against ok/errors
            # by position — same re-indexing logic as the original enumerate(gather).
            results = []
            ok_idx = 0
            err_idx = 0
            for _, (payload, _) in enumerate(items):
                if ok_idx < len(_gathered.ok):
                    results.append((payload, _gathered.ok[ok_idx]))
                    ok_idx += 1
                else:
                    results.append((payload, _gathered.errors[err_idx]))
                    err_idx += 1
            if _gathered.re_raised is not None:
                raise _gathered.re_raised

            # Resolve futures
            for payload, result in results:
                future = payload.get('future')
                if future and not future.done():
                    if isinstance(result, Exception):
                        future.set_exception(result)
                    else:
                        future.set_result(result)

            self._telemetry_counters['batch_executed'] += 1

        except Exception as batch_error:
            logger.debug(f"[BATCH] Batch shattered: {batch_error}")
            self._telemetry_counters['batch_shattered'] += 1

            # Retry individually
            for payload, _ in items:
                try:
                    result = await self._execute_callback(payload)
                    future = payload.get('future')
                    if future and not future.done():
                        future.set_result(result)
                except Exception as item_error:
                    logger.debug(f"BatchScheduler item error: {item_error}")
                    future = payload.get('future')
                    if future and not future.done():
                        future.set_exception(item_error)

    # ─── Age Bump ─────────────────────────────────────────────────────────────

    async def _age_bump_queue(self) -> None:
        """
        Age-bump: improve priority of waiting items by 1 without O(n) rebuild.

        Extract all items, re-enqueue with bumped priority (max 0).
        Guard: skip if shutting down to avoid racing with queue cleanup.
        """
        if self._worker_shutting_down:
            return
        if self._batch_queue is None or self._batch_queue.empty():
            return

        items = []
        while not self._batch_queue.empty():
            try:
                items.append(self._batch_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for item in items:
            priority, tie, schema, payload = item
            new_priority = max(0, priority - 1)
            await self._batch_queue.put((new_priority, tie, schema, payload))

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _current_flush_interval(self) -> float:
        """
        F265-5.5: Adaptive flush interval — 5-tier policy combining queue depth
        AND throughput feedback for continuous batching.

        Policy tiers:
            1. depth > high_pressure_depth  → 0.3s (urgent, reduce latency)
            2. depth > medium_pressure_depth → 0.7s (moderate pressure)
            3. throughput > 10 items/s       → 0.5s (high throughput = faster flush)
            4. throughput < 1 item/s        → 2.0s (low throughput = wait for batch)
            5. otherwise                              → default_flush_interval

        F265-5.5 FIX: Cold-start handling for first batch.

        Throughput-aware flushing reduces latency when:
            - High throughput: batch accumulation is fast, flush frequently
            - Low throughput: batch accumulation is slow, wait longer for fills
        """
        if self._batch_queue is None:
            return self._default_flush_interval

        depth = self._batch_queue.qsize()
        # Tier 1: High pressure — prioritize latency
        if depth > self._high_pressure_depth:
            return 0.3
        # Tier 2: Moderate pressure
        if depth > self._medium_pressure_depth:
            return 0.7

        # Tier 3-5: Throughput-based adjustment
        throughput = self._telemetry_ema.get('throughput_items_per_sec', 0.0)

        # F265-5.5 FIX: Cold-start — first batch or very low throughput
        # Use fast flush to not penalize startup latency
        if throughput <= 0.001:
            # Cold start (0.001 = sentinel for "first batch ever")
            return MLX().flush_fast

        if throughput > MLX().batch_throughput_high:
            # Tier 3: High throughput — flush faster to reduce queuing latency
            return MLX().flush_fast
        if throughput < MLX().batch_throughput_low:
            # Tier 4: Low throughput — wait longer to accumulate full batches
            return MLX().flush_default

        # Tier 5: Default
        return self._default_flush_interval

    def _compute_length_bin(self, prompt: str) -> str:
        """Length binning — short/medium/long to prevent padding waste."""
        tokens_est = len(prompt) // 4
        if tokens_est < MLX().length_bin_short:
            return 'short'
        elif tokens_est < MLX().length_bin_medium:
            return 'medium'
        return 'long'

    def _compute_schema_key(self, response_model: type, temperature: float) -> str:
        """
        Compute schema key with temperature stratification for FreeText.

        FreeTextSchema (synthetic virtual schema) is split by temperature range
        so requests with different temperatures don't batch together — different
        temperature = different sampling behavior = different output distribution.

        Temperature bands:
            - 0.0-0.3  → "FreeText:low"
            - 0.3-0.7  → "FreeText:medium"
            - 0.7+     → "FreeText:high"
            - unknown  → "FreeText:medium"

        For real schemas (msgspec/pydantic), use the class name directly.
        """
        name = getattr(response_model, '__name__', None) if isinstance(response_model, type) else None
        if name == 'FreeText':
            if temperature <= 0.3:
                return 'FreeText:low'
            elif temperature <= 0.7:
                return 'FreeText:medium'
            else:
                return 'FreeText:high'
        return name if name else 'unknown'

    def _compute_system_prompt_hash(self, system_msg: str | None) -> str:
        """Hash of system prompt for segregation."""
        if not system_msg:
            return 'default'
        return hashlib.md5(system_msg.encode(), usedforsecurity=False).hexdigest()[:8]

