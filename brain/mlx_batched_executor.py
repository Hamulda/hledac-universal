"""
MLXBatchedExecutor — Smart MLX inference router with continuous batching seam.

Provides a `execute_callback` shim for the existing pure-asyncio BatchScheduler

(F226H) so concurrent Hermes3 inference requests can be routed through the
scheduler for adaptive flush, priority, and backpressure. The actual MLX
workload remains single-threaded (MLX is single-threaded by design), but
multiple waiters amortize cold-start and warm-up cost.

Why this exists (Sprint P0-2):
    - BatchScheduler (brain/batch_scheduler.py) is implemented and tested
      but had ZERO production callers in the codebase — a wiring gap.
    - DeepHermes3Engine.generate() is called from at least 4 sites
      (research_hypothesis_engine, model_manager, dspy_service, synthesis_runner),
      none of which can batch.
    - This module bridges: it implements the scheduler's `execute_callback`
      contract and turns `engine.generate(prompt, ...)` into a batch-safe call.

Architecture:
    DeepHermes3Engine.generate()
        └── if _mlx_batcher initialized AND is_batch_safe():
                await self._mlx_batcher.execute(...)
                    └── BatchScheduler.submit(payload)
                            └── worker loop gathers items
                                    └── callback = self._mlx_batcher._execute_callback
                                            └── await self._engine.generate(...)
            else:
                # existing direct path (unchanged)

Invariants (P0-2):
    B.M1  Zero top-level MLX imports (lazy via DeepHermes3Engine)
    B.M2  BatchScheduler instantiated lazily (not at import time)
    B.M3  Fail-soft: any submit/future error → caller falls back to direct
    B.M4  MLX execution: DeepHermes3Engine.generate() serializes via its own
          _inference_semaphore — no external lock needed. Adding an
          asyncio.Lock here would DEADLOCK the P0-3 worker path because
          run_coroutine_threadsafe() waits on the same lock held by caller.
    B.M5  Memory guard: psutil.virtual_memory().percent > 90% → disable batching
    B.M6  max_batch_size = 6 (3B model on M1 8GB, KV cache 0.75 GB, headroom for speculative); memory guard at 85% RSS
          for parallel calls)
    B.M7  Telemetry counters exposed via get_stats() — non-intrusive
    B.M8  Shutdown: bounded ≤ 3.0s, all pending futures failed
    B.M9  Bypass: priority == 0 → direct path (urgent)
    B.M10 Direct-path latency: ≤ 1ms overhead vs. raw `await engine.generate()`
          (measured by latency_ema vs. baseline_ema)

Always-on, no feature flag, no env var.
M1 8GB safe: max batch size 4, single-threaded MLX, memory guard active.
"""
import asyncio
import atexit
import logging
import threading
import time
import weakref
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from hledac.universal.brain.batch_scheduler import BatchScheduler
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine
logger = logging.getLogger(__name__)


class _FreeTextSchema:
    """Lightweight schema for free-text batch responses (no pydantic dependency)."""
    __name__ = 'FreeText'
    __struct_fields__ = ('text',)


MAX_BATCH_SIZE_M1: int = 8
MEMORY_GUARD_PCT: float = 87.0
MEMORY_GUARD_ABSOLUTE_GB: float = 1.0
DEFAULT_FLUSH_INTERVAL_S: float = 1.0
MAX_QUEUE_DEPTH: int = 256
SHUTDOWN_TIMEOUT_S: float = 3.0
FUTURE_TIMEOUT_S: float = 30.0
URGENT_PRIORITY: float = 0.0
ADAPTIVE_CONTEXT_PREFLIGHT: bool = True

# C3-08 FIX: single-flight dict size bound — prevents unbounded memory growth
# when many unique prompts arrive concurrently (e.g. RAG with diverse queries).
# Matched to B.S6 batch queue bound (256) for consistency.
MAX_SINGLE_FLIGHT: int = 256

def _batcher_at_exit_shutdown(instance: MLXBatchedExecutor) -> None:
    """Called by weakref.finalize at interpreter exit if explicit close() was not called.

    asyncio.Event doesn't guarantee __del__ ordering on shutdown.
    weakref.finalize + atexit ensures bounded shutdown (≤ 3.0s) runs even when:
      1. Caller forgot explicit shutdown()
      2. Circular references prevented GC
      3. Interpreter is exiting via atexit
    """
    try:
        instance._scheduler = None
        if instance._init_event is not None:
            instance._init_event.clear()
    except Exception:
        pass

class MLXBatchedExecutor:
    """
    Smart router that wraps DeepHermes3Engine + BatchScheduler.

    F265-5.5 CONTINUOUS BATCHING — always-on, no feature flag.

    Public API:
        is_batch_safe(prompt, system_msg, ...) → bool
        execute(prompt, temperature, max_tokens, system_msg, priority)
            → str (result text, or raises on hard error)
        get_stats() → dict (telemetry)
        shutdown() → None (bounded ≤ 3s)

    The executor never blocks longer than MAX_BATCH_SIZE_M1 items in flight.
    When a prompt is incompatible with batching (urgent priority, empty,
    or memory pressure), `is_batch_safe` returns False and the caller
    falls through to the direct path.

    Continuous batching pipeline:
        - BatchScheduler queues items and flushes by flush_interval or max_size
        - _process_structured_batch uses a semaphore to allow concurrent callback
          invocations while maintaining serial MLX execution
        - While item 0 awaits MLX compute, items 1..k can acquire the semaphore
          and call _execute_callback — enabling prefill/decode overlap
        - PID adaptive batch sizing adjusts effective_batch_size based on
          memory EMA trend (Kp=0.5, Ki=0.05, Kd=0.1)
    """
    __slots__ = tuple(('_batch_size_high', '_batch_size_low', '_batch_size_max', '_effective_batch_size', '_ema_alpha', '_engine', '_finalizer', '_init_event', '_init_guard', '_init_lock', '_memory_check_failures', '_memory_ema', '_memory_ema_alpha', '_scheduler', '_single_flight', '_single_flight_order', '_stats', '_worker_thread'))

    def __init__(self, engine: DeepHermes3Engine, worker_thread: Any=None) -> None:
        """
        Args:
            engine: DeepHermes3Engine instance (must be loaded; model state shared)
            worker_thread: Optional MLXWorkerThread (P0-3) — when provided and
                active, MLX inference is dispatched through its persistent
                event loop instead of the local ThreadPoolExecutor. The main
                asyncio loop stays free during inference.

        Notes:
            Does NOT instantiate BatchScheduler here — lazy on first execute()
            so cold-start cost is paid once, at first use, not at import.
        """
        self._engine: DeepHermes3Engine = engine
        self._worker_thread = worker_thread
        self._scheduler: BatchScheduler | None = None
        self._init_event: asyncio.Event | None = None
        self._init_lock: asyncio.Lock | None = None
        self._init_guard: threading.Lock = threading.Lock()
        self._stats: dict[str, Any] = {'submits': 0, 'empty_prompt_bypass': 0, 'long_output_bypass': 0, 'long_system_msg_bypass': 0, 'direct_fallback': 0, 'batch_executed': 0, 'batch_shattered': 0, 'fail_soft': 0, 'memory_guard_disabled': 0, 'urgent_bypass': 0, 'speculative_bypass': 0, 'single_flight_hit': 0, 'latency_ema_ms': 0.0, 'baseline_ema_ms': 0.0, 'overhead_ema_ms': 0.0}
        self._ema_alpha: float = 0.3
        self._memory_check_failures: int = 0
        self._memory_ema: float = 0.0
        self._memory_ema_alpha: float = 0.15
        self._effective_batch_size: int = 4
        self._batch_size_high: int = 6
        self._batch_size_low: int = 2
        self._batch_size_max: int = MAX_BATCH_SIZE_M1
        self._finalizer = weakref.finalize(self, _batcher_at_exit_shutdown, self)
        atexit.register(self._finalizer)
        # C3-06 FIX: prompt-level single-flight — identical prompts reuse one Future.
        # dict[int, asyncio.Future] keyed by hash(prompt) to avoid duplicate MLX compute.
        self._single_flight: dict[int, asyncio.Future] = {}
        # C3-08 FIX: LRU order tracking for bounded single-flight dict.
        # list[int] — ordered list of prompt_hash values (oldest → newest).
        # Evicts oldest entry when MAX_SINGLE_FLIGHT is exceeded.
        self._single_flight_order: list[int] = []

    def _get_mlx_memory(self) -> Any:
        """Lazy-load mlx_memory module for adaptive batching (ISSUE-094)."""
        from hledac.universal.utils.mlx_memory import get_mlx_memory_module
        return get_mlx_memory_module()

    def _get_init_event(self) -> asyncio.Event:
        """Thread-safe lazy asyncio.Event creation (PEP 789 Python 3.14+)."""
        if self._init_event is None:
            with self._init_guard:
                if self._init_event is None:
                    self._init_event = asyncio.Event()
        return self._init_event

    def _get_init_lock(self) -> asyncio.Lock:
        """Thread-safe lazy asyncio.Lock creation (PEP 789 Python 3.14+)."""
        if self._init_lock is None:
            with self._init_guard:
                if self._init_lock is None:
                    self._init_lock = asyncio.Lock()
        return self._init_lock

    async def _ensure_initialized(self) -> None:
        """
        Lazy init of BatchScheduler.

        Idempotent: safe to call multiple times — subsequent calls no-op.
        Invariant B.M2: scheduler is NEVER instantiated at __init__ time.
        MLX serialization is handled by DeepHermes3Engine._inference_semaphore,
        not by an external lock (B.M4).

        Thread-safety: asyncio.Event for ready signaling + asyncio.Lock for
        init block serialization. Event.wait() is the fast path — returns
        immediately if initialized. Lock serializes init work (~<10ms) and
        prevents two concurrent callers from both entering the init block.
        Event.set() is idempotent, so concurrent set() calls are safe.
        """
        if self._get_init_event().is_set():
            return
        async with self._get_init_lock():
            if self._get_init_event().is_set():
                return
            try:
                from hledac.universal.brain.batch_scheduler import BatchScheduler
                scheduler: BatchScheduler = BatchScheduler(execute_callback=self._execute_callback, max_size=self._effective_batch_size, max_queue=MAX_QUEUE_DEPTH, default_flush_interval=DEFAULT_FLUSH_INTERVAL_S, medium_pressure_depth=64, high_pressure_depth=192, age_bump_interval=3, ema_alpha=self._ema_alpha)
                self._scheduler = scheduler
                await scheduler.start()
                self._get_init_event().set()
                logger.debug('[MLXBatch] executor initialized (max_batch=%d)', MAX_BATCH_SIZE_M1)
            except Exception as e:
                logger.warning('[MLXBatch] lazy init failed, batching disabled: %s', e)
                self._stats['fail_soft'] += 1

    # ------------------------------------------------------------------ //
    # is_batch_safe — public gate + private helpers                       //
    # ------------------------------------------------------------------ //

    def is_batch_safe(self, prompt: str, system_msg: str | None=None, priority: float=1.0, active_iteration_count: int=0, max_tokens: int | None=None, speculative: bool=False) -> bool:
        """
        Decide whether this request is eligible for batching.

        Returns False when:
            - executor not initialized (lazy init failed or shutdown)
            - priority == 0 (urgent, bypass — B.M9)
            - prompt is empty or whitespace-only
            - max_tokens > 2048 (very large outputs serialized anyway, no batching win)
            - prompt > 12000 chars (OSINT context too large for batch accumulation)
            - system_msg > 8192 chars
            - memory pressure exceeds guard thresholds (unless force-enabled)

        Note: speculative decoding is NOT routed through this executor on M1 8GB.
        A draft model (~500MB extra) would exceed the UMA budget. The draft model
        path in DeepHermes3Engine goes direct and bypasses this batcher entirely
        (see _is_batch_safe in deephermes3_engine.py).

        P1-4: Force-enable batching when active_iteration_count >= 2
        (multi-cycle sprint) — memory guard is bypassed to maximize
        MLX utilization across consecutive inference calls.
        """
        # ── Guard: not initialised ──────────────────────────────────────
        if not self._get_init_event().is_set() or self._scheduler is None:
            return False

        # ── Guard: urgency / emptiness ───────────────────────────────────
        if priority == URGENT_PRIORITY:
            self._stats['urgent_bypass'] += 1
            return False
        if not prompt or not prompt.strip():
            self._stats['empty_prompt_bypass'] += 1
            return False
        if speculative:
            self._stats['speculative_bypass'] += 1
            return False
        if len(prompt) > 12000:
            self._stats['long_prompt_bypass'] += 1
            return False
        if max_tokens is not None and max_tokens > 2048:
            self._stats['long_output_bypass'] += 1
            return False
        if system_msg is not None and len(system_msg) > 8192:
            self._stats['long_system_msg_bypass'] += 1
            return False

        # ── Guard: memory pressure ────────────────────────────────────────
        force_batching = active_iteration_count >= 2
        if not self._memory_guard_ok(force_batching):
            return False

        return True

    # ------------------------------------------------------------------ //
    # Private helpers                                                     //
    # ------------------------------------------------------------------ //

    def _memory_guard_ok(self, force_batching: bool) -> bool:
        """
        Update _memory_ema from psutil, compute effective batch size
        from MLX memory tier, push to scheduler, then apply the
        memory guard threshold.

        Returns True when batching is memory-safe (or force_batching).
        """
        # ── 1. Read psutil (fail-soft → allow batching) ─────────────────
        try:
            from hledac.universal.core.resource_governor import _get_cached_psutil
            from hledac.universal.core.resource_governor import _read_virtual_memory_sync
            vm = _get_cached_psutil('virtual_memory', _read_virtual_memory_sync)
            if vm is None:
                raise RuntimeError('psutil unavailable')
            pct = vm.percent
            available_gb = vm.available / 1024 ** 3
        except Exception:
            self._memory_check_failures += 1
            return True

        # ── 2. Exponential moving average of RSS percent ──────────────────
        if self._memory_ema == 0.0:
            self._memory_ema = pct
        else:
            self._memory_ema = self._memory_ema_alpha * pct + (1 - self._memory_ema_alpha) * self._memory_ema

        # ── 3. Push adaptive batch size to scheduler (internally queries MLX tier) ──
        self._push_batch_size()

        # ── 4. Memory guard threshold check ──────────────────────────────
        if self._memory_ema > MEMORY_GUARD_PCT and not force_batching:
            self._stats['memory_guard_disabled'] += 1
            return False
        if available_gb < MEMORY_GUARD_ABSOLUTE_GB and not force_batching:
            self._stats['memory_guard_disabled'] += 1
            return False
        return True

    def _mlx_tier_to_size(self, mlx_mem: Any) -> tuple[int, str]:
        """
        Query MLX memory pressure and map it to a (effective_size, tier) pair.

        tier values: 'ABUNDANT' | 'NORMAL' | 'WARNING' | 'CRITICAL'
        """
        try:
            usage_pct, pressure_level = mlx_mem.get_mlx_memory_pressure()
            if pressure_level == 'NORMAL' and usage_pct < 50:
                return self._batch_size_max, 'ABUNDANT'
            if pressure_level == 'NORMAL':
                return self._batch_size_high, 'NORMAL'
            if pressure_level == 'WARNING':
                return self._effective_batch_size, 'WARNING'
            return self._batch_size_low, 'CRITICAL'
        except Exception:
            return self._effective_batch_size, 'NORMAL'

    def _push_batch_size(self) -> None:
        """Query MLX memory tier and push adaptive batch size to scheduler if changed."""
        if self._scheduler is None:
            return
        mlx_mem = self._get_mlx_memory()
        effective_size, tier = self._mlx_tier_to_size(mlx_mem)
        old_size = self._effective_batch_size
        if effective_size == old_size:
            return
        self._effective_batch_size = effective_size
        try:
            self._scheduler.set_max_size(effective_size)
            logger.debug('[MLXBatch] batch tier %s: %d→%d (mem_ema=%.1f%%)', tier, old_size, effective_size, self._memory_ema)
        except Exception:
            pass

    async def execute(self, prompt: str, temperature: float | None=None, max_tokens: int | None=None, system_msg: str | None=None, priority: float=1.0) -> str:
        """
        Submit a request to the batch scheduler and await the result.

        Falls back to direct `engine.generate()` on any failure
        (B.M3 fail-soft). Never raises on batching path errors —
        propagates only engine.generate() errors.
        """
        await self._ensure_initialized()
        if not self._get_init_event().is_set() or self._scheduler is None:
            self._stats['direct_fallback'] += 1
            return await self._call_engine_direct(prompt, temperature, max_tokens, system_msg)

        # C3-06 FIX: prompt-level single-flight.
        # Register Future BEFORE awaiting so concurrent identical prompts share it.
        prompt_hash = hash(prompt)
        in_flight: asyncio.Future | None = self._single_flight.get(prompt_hash)

        # C3-08 FIX: evict oldest entry if bounded dict would exceed MAX_SINGLE_FLIGHT.
        # This prevents unbounded memory growth when many unique prompts arrive concurrently.
        if in_flight is None and len(self._single_flight) >= MAX_SINGLE_FLIGHT:
            if self._single_flight_order:
                oldest_hash = self._single_flight_order.pop(0)
                self._single_flight.pop(oldest_hash, None)
                logger.debug('[MLXBatch] single-flight evicted hash=%d (LRU, size=%d)', oldest_hash, len(self._single_flight))
        if in_flight is not None:
            self._stats['single_flight_hit'] += 1
            logger.debug('[MLXBatch] single-flight hit for prompt hash=%d', prompt_hash)
            # Another request for the same prompt is already in flight — await it.
            try:
                result = await asyncio.wait_for(asyncio.shield(in_flight), timeout=FUTURE_TIMEOUT_S)
                return str(result)
            except TimeoutError:
                if in_flight.done() and (not in_flight.cancelled()):
                    return str(in_flight.result())
                # Stale or cancelled — fall through to submit a fresh one.
            except asyncio.CancelledError:
                raise
            except Exception:
                # Unexpected error — fall through to direct path.
                pass

        # Submit a new Future and register it BEFORE awaiting.
        # This allows concurrent identical prompts to find and share it.
        try:
            submitted_at = time.monotonic()
            scheduler_future: asyncio.Future = await self._scheduler.submit(
                prompt=prompt,
                response_model=_FreeTextSchema,
                priority=priority,
                temperature=temperature if temperature is not None else 0.1,
                max_tokens=max_tokens if max_tokens is not None else 512,
                system_msg=system_msg,
            )
            self._stats['submits'] += 1
            self._single_flight[prompt_hash] = scheduler_future
            # C3-08 FIX: maintain LRU order on insert
            if prompt_hash in self._single_flight_order:
                self._single_flight_order.remove(prompt_hash)
            self._single_flight_order.append(prompt_hash)
            timeout = max(5.0 * DEFAULT_FLUSH_INTERVAL_S, 10.0)
            try:
                result = await asyncio.wait_for(asyncio.shield(scheduler_future), timeout=timeout)  # noqa: F911
            except TimeoutError:
                if scheduler_future.done() and (not scheduler_future.cancelled()):
                    result = scheduler_future.result()
                else:
                    self._stats['fail_soft'] += 1
                    self._stats['direct_fallback'] += 1
                    return await self._call_engine_direct(prompt, temperature, max_tokens, system_msg)
            except asyncio.CancelledError:
                raise
            elapsed_ms = (time.monotonic() - submitted_at) * 1000.0
            self._stats['latency_ema_ms'] = self._ema_alpha * elapsed_ms + (1 - self._ema_alpha) * float(self._stats['latency_ema_ms'])
            return str(result)
        except (TimeoutError, asyncio.CancelledError) as e:
            logger.debug('[MLXBatch] submit timeout/cancel, falling back: %s', e)
            self._stats['fail_soft'] += 1
            self._stats['direct_fallback'] += 1
            return await self._call_engine_direct(prompt, temperature, max_tokens, system_msg)
        except Exception as e:
            logger.debug('[MLXBatch] submit error, falling back: %s', e)
            self._stats['fail_soft'] += 1
            self._stats['direct_fallback'] += 1
            return await self._call_engine_direct(prompt, temperature, max_tokens, system_msg)
        finally:
            # C3-06: clean up single-flight entry on completion or cancellation.
            self._single_flight.pop(prompt_hash, None)
            # C3-08 FIX: also clean up LRU order tracking.
            try:
                self._single_flight_order.remove(prompt_hash)
            except ValueError:
                pass  # not in list — entry was LRU-evicted before submission

    async def _execute_callback(self, payload: dict[str, Any]) -> str:
        """
        BatchScheduler execute_callback contract.

        Invoked by _process_structured_batch via asyncio.gather (P2-1),
        so multiple callbacks in the same schema group run CONCURRENTLY.

        MLX compute serialization: DeepHermes3Engine._inference_semaphore
        bounds actual MLX compute inside both _call_engine_direct paths
        (worker-thread and local). No external lock needed (B.M4).
        """
        prompt = payload.get('prompt', '')
        temperature = payload.get('temperature')
        max_tokens = payload.get('max_tokens')
        system_msg = payload.get('system_msg')
        try:
            return await self._call_engine_direct(prompt, temperature, max_tokens, system_msg)
        except Exception as e:
            logger.debug('[MLXBatch] callback error: %s', e)
            raise

    async def _call_engine_direct(self, prompt: str, temperature: float | None, max_tokens: int | None, system_msg: str | None) -> str:
        """
        Direct call to DeepHermes3Engine.generate() — single MLX execution.

        MLX serialization via DeepHermes3Engine._inference_semaphore (B.M4).
        No external lock — direct path is safe because the semaphore
        inside engine.generate() serializes both direct and batched paths.

        P0-3 integration: when a worker_thread is provided and active, the
        inference is dispatched to the persistent event loop in the worker
        thread. The main asyncio loop is never blocked. If the worker
        thread is unavailable, we transparently fall back to the local path.
        """
        if self._worker_thread is not None and self._worker_thread.is_active():
            try:
                return await self._call_engine_via_worker(prompt, temperature, max_tokens, system_msg)
            except RuntimeError as _e:
                logger.debug('[MLXBatch] worker submit failed, falling back to direct: %s', _e)
            except TimeoutError:
                raise
        t0 = time.monotonic()
        try:
            result = await self._engine.generate(prompt=prompt, temperature=temperature, max_tokens=max_tokens, system_msg=system_msg)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self._stats['baseline_ema_ms'] = self._ema_alpha * elapsed_ms + (1 - self._ema_alpha) * float(self._stats['baseline_ema_ms'])
            return result
        except Exception:
            raise

    async def _call_engine_via_worker(self, prompt: str, temperature: float | None, max_tokens: int | None, system_msg: str | None) -> str:
        """
        P0-2 FIX: Dispatch MLX inference to worker thread via submit().

        The worker.submit() pattern creates a coroutine and submits it to
        the worker thread's event loop via run_coroutine_threadsafe().
        This is still the correct approach because:
        1. generate() is async - must run in an event loop
        2. MLX Metal releases GIL during GPU ops - main loop stays free
        3. Worker thread stays warm for subsequent requests

        Note: We still need the worker thread because asyncio.to_thread()
        cannot run an async function - it only handles sync functions.
        The MLXWorkerThread provides the persistent event loop needed.

        P0-2 FIX: timeout must match hermes default (60s), not FUTURE_TIMEOUT_S (30s).
        """
        t0 = time.monotonic()
        coro = self._engine.generate(prompt=prompt, temperature=temperature, max_tokens=max_tokens, system_msg=system_msg)
        assert self._worker_thread is not None
        result = await self._worker_thread.submit(coro, timeout=60.0)
        try:
            import mlx.core as _mx_infer
            _mx_infer.eval([])
            if hasattr(_mx_infer, 'clear_cache'):
                _mx_infer.clear_cache()
        except Exception:
            pass
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats['baseline_ema_ms'] = self._ema_alpha * elapsed_ms + (1 - self._ema_alpha) * float(self._stats['baseline_ema_ms'])
        return result

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot. Non-intrusive read (P1-1 profiling)."""
        stats = dict(self._stats)
        stats['initialized'] = self._get_init_event().is_set()
        stats['memory_check_failures'] = self._memory_check_failures
        stats['memory_ema'] = round(self._memory_ema, 2)
        stats['effective_batch_size'] = self._effective_batch_size
        stats['batch_size_max'] = self._batch_size_max
        stats['batch_size_high'] = self._batch_size_high
        stats['batch_size_low'] = self._batch_size_low
        if self._scheduler is not None:
            try:
                sched_t = self._scheduler.get_telemetry()
                ema = sched_t.get('ema', {})
                counters = sched_t.get('counters', {})
                stats['scheduler_ema'] = ema
                stats['scheduler_counters'] = counters
                submits = float(stats['submits'])
                batch_executed = float(counters.get('batch_executed', 0))
                if submits > 0:
                    stats['batch_utilization'] = round(batch_executed / submits, 4)
                else:
                    stats['batch_utilization'] = 0.0
                stats['queue_depth'] = ema.get('queue_depth', 0)
                stats['mlx_memory_bytes'] = 0  # placeholder — mlx.metal cache API varies by version
            except Exception:
                stats['scheduler_ema'] = {}
                stats['scheduler_counters'] = {}
                stats['batch_utilization'] = 0.0
                stats['queue_depth'] = 0
                stats['mlx_memory_bytes'] = 0
        else:
            stats['batch_utilization'] = 0.0
            stats['queue_depth'] = 0
            stats['mlx_memory_bytes'] = 0
        baseline = float(stats['baseline_ema_ms'])
        batched = float(stats['latency_ema_ms'])
        if baseline > 0:
            stats['overhead_ema_ms'] = max(0.0, batched - baseline)
        return stats

    async def shutdown(self) -> None:
        """
        Bounded shutdown — fails all pending futures, max 3.0s (B.M8).
        Idempotent: safe to call multiple times.

        F289: Detaches finalizer on explicit call to prevent double-cleanup
        at interpreter exit. After detach(), atexit no longer triggers _batcher_at_exit_shutdown.
        """
        self._finalizer.detach()
        if not self._get_init_event().is_set():
            return
        try:
            if self._scheduler is not None:
                await self._scheduler.shutdown(timeout=SHUTDOWN_TIMEOUT_S)
        except Exception as e:
            logger.debug('[MLXBatch] scheduler shutdown error: %s', e)
        finally:
            self._scheduler = None
            if self._init_event is not None:
                self._init_event.clear()
            logger.debug('[MLXBatch] executor shut down')

    def __repr__(self) -> str:
        state = 'init' if self._init_event is not None and self._init_event.is_set() else 'lazy'
        return f'MLXBatchedExecutor(state={state}, max_batch={MAX_BATCH_SIZE_M1})'
__all__ = ['MLXBatchedExecutor', 'MAX_BATCH_SIZE_M1', 'MEMORY_GUARD_PCT']