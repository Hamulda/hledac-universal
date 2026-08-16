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
import asyncio
import hashlib
import itertools
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any
from hledac.universal.utils.asyncx import safe_create_task, parallel
from hledac.universal._core.constants import MLX
from hledac.universal.compat.pydantic_compat import is_pydantic_model, is_msgspec_struct
from _core import aclose
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
    __slots__ = tuple(('_age_bump_interval', '_batch_queue', '_batch_tie_breaker', '_default_flush_interval', '_ema_alpha', '_execute_callback', '_flush_cycle_count', '_high_pressure_depth', '_items_processed_since_last', '_last_age_bump', '_last_batch_finished_at', '_max_queue', '_max_size', '_medium_pressure_depth', '_pending_futures', '_settables', '_telemetry_counters', '_telemetry_ema', '_worker_shutting_down', '_worker_task'))

    def __init__(self, execute_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, Any]], max_size: int=MLX().batch_max_size, max_queue: int=MLX().batch_queue_max, default_flush_interval: float=MLX().flush_default, medium_pressure_depth: int=MLX().batch_medium_pressure_depth, high_pressure_depth: int=MLX().batch_high_pressure_depth, age_bump_interval: int=MLX().age_bump_interval, ema_alpha: float=MLX().batch_ema_alpha) -> None:
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
        self._max_size = max_size
        self._settables: set[str] = {'_max_size'}
        self._max_queue = max_queue
        self._default_flush_interval = default_flush_interval
        self._medium_pressure_depth = medium_pressure_depth
        self._high_pressure_depth = high_pressure_depth
        self._age_bump_interval = age_bump_interval
        self._ema_alpha = ema_alpha
        self._batch_queue: asyncio.PriorityQueue | None = None
        self._batch_tie_breaker: itertools.count | None = None
        self._worker_task: asyncio.Task | None = None
        self._worker_shutting_down = False
        self._pending_futures: set[asyncio.Future] = set()
        self._flush_cycle_count = 0
        self._last_age_bump = 0
        self._telemetry_ema = {'dispatch_to_result_ms': 0.0, 'batch_size': 0, 'queue_depth': 0, 'throughput_items_per_sec': 0.0}
        self._telemetry_counters = {'batch_submitted': 0, 'batch_executed': 0, 'batch_shattered': 0, 'schema_mismatch_flushes': 0, 'length_bin_mismatch_flushes': 0, 'prompt_mismatch_flushes': 0, 'adaptive_flush_default_entries': 0, 'adaptive_flush_medium_entries': 0, 'adaptive_flush_fast_entries': 0, 'age_bump_cycles': 0}
        self._last_batch_finished_at: float = 0.0
        self._items_processed_since_last: int = 0

    def set_max_size(self, new_size: int) -> None:
        """
        ISSUE-094 FIX: Propagate adaptive batch size changes from MLXBatchedExecutor.

        The PID controller in MLXBatchedExecutor._adjust_batch_size() was updating
        self._effective_batch_size but never propagating it to the scheduler's
        _max_size — making the PID loop a no-op.

        Callers: MLXBatchedExecutor.is_batch_safe() after each PID adjustment.
        """
        if not hasattr(self, '_settables') or '_max_size' not in self._settables:
            return
        self._max_size = max(1, min(256, new_size))

    async def start(self) -> None:
        """Start the batch worker (lazy start)."""
        if self._worker_task is not None:
            return
        self._batch_queue = asyncio.PriorityQueue(maxsize=self._max_queue)
        self._batch_tie_breaker = itertools.count()
        self._worker_shutting_down = False
        self._worker_task = safe_create_task(self._worker())
        logger.debug('BatchScheduler worker started')

    async def shutdown(self, timeout: float=3.0) -> None:
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
        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError('batch_scheduler_shutdown'))
        self._pending_futures.clear()
        self._worker_shutting_down = True
        self._worker_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)  # noqa: F911  # Shield patterns MUST use asyncio.wait_for
        except TimeoutError:  # noqa: BLE001
            pass
        except asyncio.CancelledError:
            self._worker_task = None
            self._batch_queue = None
            raise
        self._worker_task = None
        self._batch_queue = None
        logger.debug('BatchScheduler shutdown complete')

    async def submit(self, prompt: str, response_model: type, priority: float=1.0, temperature: float=0.1, max_tokens: int=1024, system_msg: str | None=None) -> asyncio.Future:
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
        payload = {'prompt': prompt, 'response_model': response_model, 'temperature': temperature, 'max_tokens': max_tokens, 'system_msg': system_msg, 'future': future, 'type': 'structured'}
        await self._batch_queue.put((priority, tie, schema_key, payload))
        self._telemetry_counters['batch_submitted'] += 1
        self._pending_futures.add(future)
        future.add_done_callback(lambda f: self._pending_futures.discard(f))
        return future

    def is_batch_safe(self, response_model: type, priority: float, timeout_s: float | None=None) -> bool:
        """
        Batch-safe eligibility check.

        Routing criteria:
            - schema type must be detectable (msgspec or pydantic)
            - not urgent priority (priority == 0)
            - timeout must allow for batching (>= 2x flush interval)

        Returns:
            True if should use batch queue, False for direct path
        """
        if priority == 0:
            return False
        if response_model is None:
            return False
        if timeout_s is not None and timeout_s <= self._current_flush_interval() * 2:
            return False
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)
        # ROADMAP-006: Use compat layer for type detection
        # Batch-safe if it's msgspec.Struct or Pydantic model with model_validate_json
        if is_msgspec_struct(schema_cls) or is_pydantic_model(schema_cls):
            return True
        return False

    async def flush(self, timeout: float=5.0) -> int:
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
        return {'ema': dict(self._telemetry_ema), 'counters': dict(self._telemetry_counters)}

    async def _worker(self) -> None:
        """Background worker that processes batches with boundary segregation."""
        while True:
            if self._worker_shutting_down:
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError('batch_scheduler_shutdown'))
                self._pending_futures.clear()
                break
            try:
                items = []
                current_schema_key = None
                current_prompt_hash = None
                current_length_bin = None
                flush_interval = self._current_flush_interval()
                if flush_interval >= 1.9:
                    self._telemetry_counters['adaptive_flush_default_entries'] += 1
                elif flush_interval >= 0.9:
                    self._telemetry_counters['adaptive_flush_medium_entries'] += 1
                else:
                    self._telemetry_counters['adaptive_flush_fast_entries'] += 1
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
                            if item_schema != current_schema_key:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['schema_mismatch_flushes'] += 1
                                break
                            if item_prompt_hash != current_prompt_hash:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['prompt_mismatch_flushes'] += 1
                                break
                            if item_length_bin != current_length_bin:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['length_bin_mismatch_flushes'] += 1
                                break
                            items.append(item)
                        except TimeoutError:
                            break
                except TimeoutError:
                    continue
                self._flush_cycle_count += 1
                if self._flush_cycle_count - self._last_age_bump >= self._age_bump_interval:
                    self._last_age_bump = self._flush_cycle_count
                    await self._age_bump_queue()
                    self._telemetry_counters['age_bump_cycles'] += 1
                if self._batch_queue is not None:
                    self._telemetry_ema['queue_depth'] = self._batch_queue.qsize()
                t0 = time.monotonic()
                await self._process_batch(items)
                dispatch_ms = (time.monotonic() - t0) * 1000
                self._telemetry_ema['batch_size'] = len(items)
                self._telemetry_ema['dispatch_to_result_ms'] = self._ema_alpha * dispatch_ms + (1 - self._ema_alpha) * self._telemetry_ema['dispatch_to_result_ms']
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'BatchScheduler worker error: {e}')

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
                logger.debug(f'BatchScheduler process error for schema {schema_key}: {e}')
        self._items_processed_since_last += len(items)
        now = time.monotonic()
        if self._last_batch_finished_at > 0:
            elapsed = now - self._last_batch_finished_at
            if elapsed > 0:
                is_cold = self._telemetry_ema['throughput_items_per_sec'] == 0.0
                alpha = 0.5 if is_cold else 0.3
                self._telemetry_ema['throughput_items_per_sec'] = alpha * (self._items_processed_since_last / elapsed) + (1 - alpha) * self._telemetry_ema['throughput_items_per_sec']
        elif self._telemetry_ema['throughput_items_per_sec'] == 0.0:
            self._telemetry_ema['throughput_items_per_sec'] = 0.001
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
            from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
            _batch_sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

            async def process_with_sem(payload: dict[str, Any]) -> tuple[dict, Any]:
                async with _batch_sem:
                    return (payload, await self._execute_callback(payload))
            _tasks = [process_with_sem(payload) for payload, _ in items]
            _gathered = await parallel(_tasks, taskgroup=True, policy='collect', ctx='batch_scheduler', logger_instance=logger)
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
            for payload, result in results:
                future = payload.get('future')
                if future and (not future.done()):
                    if isinstance(result, Exception):
                        future.set_exception(result)
                    else:
                        future.set_result(result)
            self._telemetry_counters['batch_executed'] += 1
        except Exception as batch_error:
            logger.debug(f'[BATCH] Batch shattered: {batch_error}')
            self._telemetry_counters['batch_shattered'] += 1
            for payload, _ in items:
                try:
                    result = await self._execute_callback(payload)
                    future = payload.get('future')
                    if future and (not future.done()):
                        future.set_result(result)
                except Exception as item_error:
                    logger.debug(f'BatchScheduler item error: {item_error}')
                    future = payload.get('future')
                    if future and (not future.done()):
                        future.set_exception(item_error)

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
        if depth > self._high_pressure_depth:
            return 0.3
        if depth > self._medium_pressure_depth:
            return 0.7
        throughput = self._telemetry_ema.get('throughput_items_per_sec', 0.0)
        if throughput <= 0.001:
            return MLX().flush_fast
        if throughput > MLX().batch_throughput_high:
            return MLX().flush_fast
        if throughput < MLX().batch_throughput_low:
            return MLX().flush_default
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