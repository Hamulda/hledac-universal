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
import time
from typing import Any, Callable, Coroutine, Dict, Optional, Set, Type

import logging

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
        execute_callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]],
        max_size: int = 8,
        max_queue: int = 256,
        default_flush_interval: float = 2.0,
        medium_pressure_depth: int = 64,
        high_pressure_depth: int = 192,
        age_bump_interval: int = 3,
        ema_alpha: float = 0.3,
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
        self._max_queue = max_queue
        self._default_flush_interval = default_flush_interval
        self._medium_pressure_depth = medium_pressure_depth
        self._high_pressure_depth = high_pressure_depth
        self._age_bump_interval = age_bump_interval
        self._ema_alpha = ema_alpha

        # Queue state
        self._batch_queue: Optional[asyncio.PriorityQueue] = None
        self._tie_breaker: Optional[itertools.count] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._worker_shutting_down = False

        # Pending futures (for emergency failure)
        self._pending_futures: Set[asyncio.Future] = set()

        # Counters
        self._flush_cycle_count = 0
        self._last_age_bump = 0

        # EMA telemetry
        self._telemetry_ema = {
            'enqueue_to_dispatch_ms': 0.0,
            'dispatch_to_result_ms': 0.0,
            'batch_size': 0,
            'queue_depth': 0,
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
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        self._worker_task = None
        self._batch_queue = None
        logger.debug("BatchScheduler shutdown complete")

    async def submit(
        self,
        prompt: str,
        response_model: Type,
        priority: float = 1.0,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        system_msg: Optional[str] = None,
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

        schema_key = response_model.__name__ if hasattr(response_model, '__name__') else 'unknown'
        future: asyncio.Future = asyncio.get_event_loop().create_future()

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
        response_model: Type,
        priority: float,
        timeout_s: Optional[float] = None,
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

    def get_telemetry(self) -> Dict[str, Any]:
        """Return telemetry snapshot (EMA + counters)."""
        return {
            'ema': dict(self._telemetry_ema),
            'counters': dict(self._telemetry_counters),
        }

    # ─── Worker Loop ──────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Background worker that processes batches with boundary segregation."""
        tie_breaker = itertools.count()

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
                    first_item = await asyncio.wait_for(
                        self._batch_queue.get(),
                        timeout=flush_interval
                    )
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
                            item = await asyncio.wait_for(
                                self._batch_queue.get_nowait(),
                                timeout=0.01
                            )
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
                        except asyncio.TimeoutError:
                            break

                except asyncio.TimeoutError:
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
        """Process a batch of structured-output items."""
        if not items:
            return

        by_schema: Dict[str, list] = {}
        for priority, tie, schema_key, payload in items:
            if schema_key not in by_schema:
                by_schema[schema_key] = []
            by_schema[schema_key].append((payload, priority))

        for schema_key, group in by_schema.items():
            try:
                await self._process_structured_batch(group)
            except Exception as e:
                logger.debug(f"BatchScheduler process error for schema {schema_key}: {e}")

    async def _process_structured_batch(self, items: list) -> None:
        """
        Process a batch of structured output requests for same schema.
        Shatters on total failure.
        """
        try:
            results = []
            for payload, _ in items:
                result = await self._execute_callback(payload)
                results.append(result)

            # Resolve futures
            for payload, result in zip([p for p, _ in items], results):
                future = payload.get('future')
                if future and not future.done():
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
        """
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
        Adaptive flush interval — 3-tier policy based on queue depth.

        - depth > _high_pressure_depth  → 0.5s (fast)
        - depth > _medium_pressure_depth → 1.0s (medium)
        - otherwise                      → _default_flush_interval
        """
        if self._batch_queue is None:
            return self._default_flush_interval
        depth = self._batch_queue.qsize()
        if depth > self._high_pressure_depth:
            return 0.5
        if depth > self._medium_pressure_depth:
            return 1.0
        return self._default_flush_interval

    def _compute_length_bin(self, prompt: str) -> str:
        """Length binning — short/medium/long to prevent padding waste."""
        tokens_est = len(prompt) // 4
        if tokens_est < 256:
            return 'short'
        elif tokens_est < 1024:
            return 'medium'
        return 'long'

    def _compute_system_prompt_hash(self, system_msg: Optional[str]) -> str:
        """Hash of system prompt for segregation."""
        if not system_msg:
            return 'default'
        return hashlib.md5(system_msg.encode(), usedforsecurity=False).hexdigest()[:8]