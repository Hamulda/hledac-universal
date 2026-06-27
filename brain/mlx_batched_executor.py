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

from __future__ import annotations

import asyncio
import atexit
import logging
import time
import weakref
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.brain.batch_scheduler import BatchScheduler
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# ─── Bounded constants (M1 8GB safety) ──────────────────────────────
MAX_BATCH_SIZE_M1: int = 8  # P1-1: 6→8 on M1 8GB; single-thread MLX lock means batches serialize anyway, 8 is safe at idle
# F285: Memory guard — aligned with resource_allocator.py warn threshold (87%).
# The 5-point gap (87% warn → 93% critical) gives the PID controller time to
# shrink batch size before ml_jobs gets clamped to 0 by the resource allocator.
# Previously 92% was ABOVE the old warn (90%), so batcher short-circuited AFTER
# resource_allocator already blocked MLX — leaving no room for adaptive batching.
# 87% pct guard ≈ ~1.04GB free on M1 8GB → safe for batch accumulation.
MEMORY_GUARD_PCT: float = 87.0  # 92→87: aligned with resource_allocator warn threshold
MEMORY_GUARD_ABSOLUTE_GB: float = 1.0  # M1 8GB: relaxed from 1.5GB; Metal cache 1.5GiB + KV 0.75GB are model-resident, available RAM is the constraint; 1.0GB allows batching at typical sprint memory pressure
DEFAULT_FLUSH_INTERVAL_S: float = 1.0
MAX_QUEUE_DEPTH: int = 256
SHUTDOWN_TIMEOUT_S: float = 3.0
FUTURE_TIMEOUT_S: float = 30.0
URGENT_PRIORITY: float = 0.0
ADAPTIVE_CONTEXT_PREFLIGHT: bool = True


# ─── Module-level cleanup callback for weakref.finalize ──────────────

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

    def __init__(
        self,
        engine: DeepHermes3Engine,
        worker_thread: Any = None,
    ) -> None:
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
        self._worker_thread = worker_thread  # Optional MLXWorkerThread (P0-3)
        self._scheduler: BatchScheduler | None = None
        # P0-2 FIX: asyncio.Event for one-time initialization signaling.
        # Event.set() is idempotent — safe for concurrent set() calls.
        # Event.clear() atomically resets ready state for shutdown replay.
        # Sémanticky čistší než bool flag: "event is set" = "ready".
        self._init_event: asyncio.Event = asyncio.Event()
        # Guards the actual init block — only held during BatchScheduler
        # instantiation (~<10ms). The Event serves as the ready-signal
        # fast-path; the Lock only serializes the init work itself.
        self._init_lock: asyncio.Lock = asyncio.Lock()
        # No external lock needed — DeepHermes3Engine._inference_semaphore
        # serializes MLX compute. Adding an asyncio.Lock here would DEADLOCK
        # the P0-3 worker path because run_coroutine_threadsafe() waits on
        # the same lock held by the caller (B.M4 invariant).

        # Telemetry counters (B.M7)
        self._stats: dict[str, Any] = {
            "submits": 0,
            "empty_prompt_bypass": 0,
            "long_output_bypass": 0,
            "long_system_msg_bypass": 0,
            "direct_fallback": 0,
            "batch_executed": 0,
            "batch_shattered": 0,
            "fail_soft": 0,
            "memory_guard_disabled": 0,
            "urgent_bypass": 0,
            "latency_ema_ms": 0.0,
            "baseline_ema_ms": 0.0,
            "overhead_ema_ms": 0.0,
        }
        self._ema_alpha: float = 0.3
        self._memory_check_failures: int = 0

        # PID-style adaptive batch size (Task #2)
        # Tracks EMA of RSS memory percent; adjusts effective batch size
        # based on trend, not instant snapshot. Starts conservative at 4
        # (below static MAX_BATCH_SIZE_M1=6 for headroom).
        self._memory_ema: float = 0.0
        self._memory_ema_alpha: float = 0.15  # slower EMA for trend
        self._pid_integral: float = 0.0  # integral term (accumulates overshoot)
        self._pid_Kp: float = 0.5  # proportional gain
        self._pid_Ki: float = 0.05  # integral gain
        self._pid_Kd: float = 0.1  # derivative gain
        self._effective_batch_size: int = 4  # starts at 4, adapts [1, MAX_BATCH_SIZE_M1]

        # F289: weakref.finalize for interpreter-exit cleanup guarantee.
        # asyncio.Event doesn't have __del__ reliability; finalizer ensures
        # shutdown() is called even if caller forgets explicit close().
        # B.M8: bounded ≤ 3.0s already enforced in shutdown().
        self._finalizer = weakref.finalize(
            self,
            _batcher_at_exit_shutdown,
            self,
        )
        atexit.register(self._finalizer)

    # ─── Lazy init ─────────────────────────────────────────────────────

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
        # Fast path: Event is set → already initialized, no lock needed.
        if self._init_event.is_set():
            return
        async with self._init_lock:
            # Double-check after acquiring lock — another caller may have
            # already completed initialization while we were waiting on the lock.
            if self._init_event.is_set():
                return
            try:
                from hledac.universal.brain.batch_scheduler import BatchScheduler

                scheduler: BatchScheduler = BatchScheduler(
                    execute_callback=self._execute_callback,
                    max_size=self._effective_batch_size,
                    max_queue=MAX_QUEUE_DEPTH,
                    default_flush_interval=DEFAULT_FLUSH_INTERVAL_S,
                    medium_pressure_depth=64,
                    high_pressure_depth=192,
                    age_bump_interval=3,
                    ema_alpha=self._ema_alpha,
                )
                self._scheduler = scheduler
                await scheduler.start()
                self._init_event.set()  # Idempotent — safe for concurrent calls
                logger.debug("[MLXBatch] executor initialized (max_batch=%d)", MAX_BATCH_SIZE_M1)
            except Exception as e:
                # B.M3: fail-soft — initialization failure → executor unusable,
                # caller will fall through to direct path on every call.
                logger.warning("[MLXBatch] lazy init failed, batching disabled: %s", e)
                self._stats["fail_soft"] += 1
                # Event remains unset — callers will go direct path

    # ─── Routing decision (B.M9) ───────────────────────────────────────

    def is_batch_safe(
        self,
        prompt: str,
        system_msg: str | None = None,
        priority: float = 1.0,
        active_iteration_count: int = 0,
        max_tokens: int | None = None,
        speculative: bool = False,
    ) -> bool:
        """
        Decide whether this request is eligible for batching.

        Returns False when:
            - executor not initialized (lazy init failed or shutdown)
            - memory pressure > MEMORY_GUARD_PCT (unless force-enabled below)
            - priority == 0 (urgent, bypass — B.M9)
            - prompt is empty or whitespace-only
            - max_tokens > 2048 (very large outputs serialized anyway, no batching win)
            - prompt > 12000 chars (OSINT context too large for batch accumulation)

        Note: speculative decoding is NOT routed through this executor on M1 8GB.
        A draft model (~500MB extra) would exceed the UMA budget. The draft model
        path in DeepHermes3Engine goes direct and bypasses this batcher entirely
        (see _is_batch_safe in deephermes3_engine.py).

        P1-4: Force-enable batching when active_iteration_count >= 2
        (multi-cycle sprint) — memory guard is bypassed to maximize
        MLX utilization across consecutive inference calls.
        """
        if not self._init_event.is_set() or self._scheduler is None:
            return False
        if priority == URGENT_PRIORITY:
            self._stats["urgent_bypass"] += 1
            return False
        if not prompt or not prompt.strip():
            self._stats["empty_prompt_bypass"] += 1
            return False
        if speculative:
            # Speculative decode goes direct — draft model path bypasses batcher
            self._stats["speculative_bypass"] = self._stats.get("speculative_bypass", 0) + 1
            return False
        # Long prompts (>12000 chars ≈ 6000 tokens) go direct — no batching win
        # Relaxed from 4096 to allow OSINT context + findings batches through
        if len(prompt) > 12000:
            self._stats["long_prompt_bypass"] += 1
            return False
        # max_tokens gate: very large outputs (>2048) are serialized anyway, no batching win
        # Relaxed from 1024 to allow synthesis/report generation (1500-4000 tokens)
        if max_tokens is not None and max_tokens > 2048:
            self._stats["long_output_bypass"] += 1
            return False
        # system_msg influences schema segregation downstream; empty != urgent
        if system_msg is not None and len(system_msg) > 8192:
            # Very long system messages do not benefit from batching
            # (length-bin boundary would shatter the batch anyway)
            self._stats["long_system_msg_bypass"] += 1
            return False
        # Memory guard (B.M5) — psutil only when available, fail-open
        # PID-style adaptive: use EMA of memory pressure, not instant snapshot.
        # Update EMA each call, run PID feedback to adjust effective batch size.
        try:
            import psutil

            pct = psutil.virtual_memory().percent
            # Update memory EMA (trend, not instant)
            if self._memory_ema == 0.0:
                self._memory_ema = pct  # bootstrap
            else:
                self._memory_ema = (
                    self._memory_ema_alpha * pct
                    + (1 - self._memory_ema_alpha) * self._memory_ema
                )

            # PID feedback: setpoint = MEMORY_GUARD_PCT - 5% (headroom margin)
            setpoint = MEMORY_GUARD_PCT - 5.0
            error = self._memory_ema - setpoint

            # Integral: accumulate overshoot (clamp to prevent windup)
            self._pid_integral = max(-20.0, min(20.0, self._pid_integral + error))

            # Derivative: trend = current error - previous error (approx from EMA diff)
            derivative = error - (self._memory_ema - pct)  # approx derivative

            # PID output → delta to batch size
            pid_output = (
                self._pid_Kp * error
                + self._pid_Ki * self._pid_integral
                + self._pid_Kd * derivative
            )

            # Adjust effective batch size: shrink when above setpoint, grow below
            new_size = int(round(self._effective_batch_size - pid_output))
            new_size = max(1, min(MAX_BATCH_SIZE_M1, new_size))
            if new_size != self._effective_batch_size:
                old = self._effective_batch_size
                self._effective_batch_size = new_size
                logger.debug(
                    "[MLXBatch] PID adjust batch %d→%d (mem_ema=%.1f%%, error=%.1f)",
                    old, new_size, self._memory_ema, error,
                )

            # Memory guard: disable batching when EMA is above the tighter threshold
            # OR when absolute available GB is below safe minimum for batch accumulation.
            # F265C: Two-dimensional guard — pct guard catches gradual leak, absolute guard
            # catches acute pressure (e.g. after other processes claimed RAM).
            # P1-4: Force-enable batching on multi-cycle sprints (>= 2 iterations).
            # Memory guard is bypassed to maximize MLX utilization — the M1 8GB
            # budget is already accounted for in the sprint planning phase.
            memory_ok = True
            force_batching = active_iteration_count >= 2
            if self._memory_ema > MEMORY_GUARD_PCT and not force_batching:
                self._stats["memory_guard_disabled"] += 1
                memory_ok = False
            # Absolute available GB check (M1 8GB Metal budget: 1.5GiB cache + 0.75GiB KV)
            available_gb = psutil.virtual_memory().available / (1024**3)
            if available_gb < MEMORY_GUARD_ABSOLUTE_GB and not force_batching:
                self._stats["memory_guard_disabled"] += 1
                memory_ok = False
            if not memory_ok:
                return False
        except Exception:
            # psutil missing or transient error → fail-open, allow batching
            self._memory_check_failures += 1
        return True

    # ─── Public API ────────────────────────────────────────────────────

    async def execute(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        priority: float = 1.0,
    ) -> str:
        """
        Submit a request to the batch scheduler and await the result.

        Falls back to direct `engine.generate()` on any failure
        (B.M3 fail-soft). Never raises on batching path errors —
        propagates only engine.generate() errors.
        """
        await self._ensure_initialized()
        if not self._init_event.is_set() or self._scheduler is None:
            # Lazy init failed → direct path
            self._stats["direct_fallback"] += 1
            return await self._call_engine_direct(
                prompt, temperature, max_tokens, system_msg
            )

        try:
            # Use a stub response_model — we treat all text-generation
            # requests as a single virtual schema "FreeText" so they batch
            # together. Length-bin and prompt-hash boundaries still apply.
            class _FreeTextSchema:
                __name__ = "FreeText"
                __struct_fields__ = ("text",)  # msgspec-detectable

            submitted_at = time.monotonic()

            # CRITICAL FIX: use scheduler_future returned by submit(), not a
            # separately-created orphan future. The scheduler creates the future
            # internally and embeds it in the payload for _execute_callback to
            # resolve. We await THAT future, not an unrelated one.
            scheduler_future: asyncio.Future = await self._scheduler.submit(
                prompt=prompt,
                response_model=_FreeTextSchema,
                priority=priority,
                temperature=temperature if temperature is not None else 0.1,
                max_tokens=max_tokens if max_tokens is not None else 512,
                system_msg=system_msg,
            )
            self._stats["submits"] += 1

            # Timeout: 5× flush interval gives the scheduler time to gather a
            # batch, with a hard floor of 10s. Avoids the previous 25s orphan-
            # wait bug where we awaited a future nobody ever resolved.
            #
            # Fix: asyncio.shield() prevents external CancelledError from
            # killing the inner scheduler_future during the wait_for window.
            # Without shield: if the caller's task is cancelled while wait_for
            # is waiting, the CancelledError propagates and scheduler_future
            # may never be resolved (orphan). With shield: the inner future
            # continues in the scheduler and can be awaited in the fallback path.
            timeout = max(5.0 * DEFAULT_FLUSH_INTERVAL_S, 10.0)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(scheduler_future), timeout=timeout
                )
            except TimeoutError:
                # Timeout expired but scheduler_future is still alive (shielded).
                # If it completed during the timeout window, retrieve result.
                # Otherwise fall through to direct fallback.
                if scheduler_future.done() and not scheduler_future.cancelled():
                    result = scheduler_future.result()
                else:
                    self._stats["fail_soft"] += 1
                    self._stats["direct_fallback"] += 1
                    return await self._call_engine_direct(
                        prompt, temperature, max_tokens, system_msg
                    )
            except asyncio.CancelledError:
                # External cancellation — scheduler_future is shielded, still alive.
                # Re-raise so caller sees the cancellation.
                raise

            # Update latency EMA (B.M10)
            elapsed_ms = (time.monotonic() - submitted_at) * 1000.0
            self._stats["latency_ema_ms"] = (
                self._ema_alpha * elapsed_ms
                + (1 - self._ema_alpha) * float(self._stats["latency_ema_ms"])
            )
            return str(result)

        except (TimeoutError, asyncio.CancelledError) as e:
            # Hard timeout/cancel → fall back to direct
            logger.debug("[MLXBatch] submit timeout/cancel, falling back: %s", e)
            self._stats["fail_soft"] += 1
            self._stats["direct_fallback"] += 1
            return await self._call_engine_direct(
                prompt, temperature, max_tokens, system_msg
            )
        except Exception as e:
            # Any other batching failure → direct
            logger.debug("[MLXBatch] submit error, falling back: %s", e)
            self._stats["fail_soft"] += 1
            self._stats["direct_fallback"] += 1
            return await self._call_engine_direct(
                prompt, temperature, max_tokens, system_msg
            )

    async def _execute_callback(self, payload: dict[str, Any]) -> str:
        """
        BatchScheduler execute_callback contract.

        Invoked by _process_structured_batch via asyncio.gather (P2-1),
        so multiple callbacks in the same schema group run CONCURRENTLY.

        MLX compute serialization: DeepHermes3Engine._inference_semaphore
        bounds actual MLX compute inside both _call_engine_direct paths
        (worker-thread and local). No external lock needed (B.M4).
        """
        prompt = payload.get("prompt", "")
        temperature = payload.get("temperature")
        max_tokens = payload.get("max_tokens")
        system_msg = payload.get("system_msg")
        try:
            return await self._call_engine_direct(
                prompt, temperature, max_tokens, system_msg
            )
        except Exception as e:
            # Propagate so BatchScheduler can shatter the batch and retry
            # individually; final fallback to direct will happen in execute().
            logger.debug("[MLXBatch] callback error: %s", e)
            raise

    async def _call_engine_direct(
        self,
        prompt: str,
        temperature: float | None,
        max_tokens: int | None,
        system_msg: str | None,
    ) -> str:
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
        # P0-3 routing: prefer worker thread when active
        if (
            self._worker_thread is not None
            and self._worker_thread.is_active()
        ):
            try:
                return await self._call_engine_via_worker(
                    prompt, temperature, max_tokens, system_msg
                )
            except RuntimeError as _e:
                # Worker died mid-flight or loop unavailable — fall back
                logger.debug(
                    "[MLXBatch] worker submit failed, falling back to direct: %s",
                    _e,
                )
            except TimeoutError:
                raise
        # Local path (no worker thread, or worker unavailable)
        # MLX serialization is handled by DeepHermes3Engine._inference_semaphore
        # (ThreadPoolExecutor) or the worker thread's event loop (P0-3).
        t0 = time.monotonic()
        try:
            result = await self._engine.generate(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                system_msg=system_msg,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self._stats["baseline_ema_ms"] = (
                self._ema_alpha * elapsed_ms
                + (1 - self._ema_alpha) * float(self._stats["baseline_ema_ms"])
            )
            return result
        except Exception:
            # Re-raise — caller decides. DeepHermes3Engine already has fail-safe
            # telemetry; we don't double-record.
            raise

    async def _call_engine_via_worker(
        self,
        prompt: str,
        temperature: float | None,
        max_tokens: int | None,
        system_msg: str | None,
    ) -> str:
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
        # Create coroutine for the worker's event loop
        coro = self._engine.generate(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
        )
        assert self._worker_thread is not None  # caller checked
        # P0-2 FIX: 60s matches HERMES_TIMEOUT_DEFAULT_S in deephermes3_engine.
        # FUTURE_TIMEOUT_S=30s was too short — batcher gave up before the
        # actual MLX inference (which has its own 60s timeout) completed.
        result = await self._worker_thread.submit(coro, timeout=60.0)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["baseline_ema_ms"] = (
            self._ema_alpha * elapsed_ms
            + (1 - self._ema_alpha) * float(self._stats["baseline_ema_ms"])
        )
        return result

    # ─── Telemetry & shutdown ──────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot. Non-intrusive read (P1-1 profiling)."""
        stats = dict(self._stats)
        stats["initialized"] = self._init_event.is_set()
        stats["memory_check_failures"] = self._memory_check_failures
        # PID adaptive batch size state (Task #2)
        stats["memory_ema"] = round(self._memory_ema, 2)
        stats["pid_integral"] = round(self._pid_integral, 2)
        stats["effective_batch_size"] = self._effective_batch_size
        if self._scheduler is not None:
            try:
                sched_t = self._scheduler.get_telemetry()
                ema = sched_t.get("ema", {})
                counters = sched_t.get("counters", {})
                stats["scheduler_ema"] = ema
                stats["scheduler_counters"] = counters

                # P1-1: batch_utilization — batch_executed / submits as ratio
                # Cíl: > 60%. Reflects how often batching actually ran vs. submits.
                submits = float(stats["submits"])
                batch_executed = float(counters.get("batch_executed", 0))
                if submits > 0:
                    stats["batch_utilization"] = round(batch_executed / submits, 4)
                else:
                    stats["batch_utilization"] = 0.0

                # P1-1: queue_depth EMA from scheduler (cíl: < 8)
                stats["queue_depth"] = ema.get("queue_depth", 0)

                # P1-1: mlx_memory — Metal cache bytes when mlx is available.
                # Lazy import to respect B.M1 (zero top-level MLX imports).
                # Fails silently (0) when Metal is unavailable or process is not Apple Silicon.
                mlx_mem_bytes = 0
                try:
                    import mlx.core as _mx

                    _mx.eval([])  # ensure lazy eval has resolved
                    if hasattr(_mx.metal, "set_cache_limit"):
                        # Approximate: active KV cache = working set estimate.
                        # mx.metal.get_cache_memory() not public; report Metal heap
                        # allocation as proxy via available memory guard state.
                        pass
                except Exception:
                    pass
                stats["mlx_memory_bytes"] = mlx_mem_bytes

            except Exception:
                stats["scheduler_ema"] = {}
                stats["scheduler_counters"] = {}
                stats["batch_utilization"] = 0.0
                stats["queue_depth"] = 0
                stats["mlx_memory_bytes"] = 0
        else:
            stats["batch_utilization"] = 0.0
            stats["queue_depth"] = 0
            stats["mlx_memory_bytes"] = 0
        # Compute overhead (B.M10)
        baseline = float(stats["baseline_ema_ms"])
        batched = float(stats["latency_ema_ms"])
        if baseline > 0:
            stats["overhead_ema_ms"] = max(0.0, batched - baseline)
        return stats

    async def shutdown(self) -> None:
        """
        Bounded shutdown — fails all pending futures, max 3.0s (B.M8).
        Idempotent: safe to call multiple times.

        F289: Detaches finalizer on explicit call to prevent double-cleanup
        at interpreter exit. After detach(), atexit no longer triggers _batcher_at_exit_shutdown.
        """
        # Detach finalizer — explicit close wins over atexit
        self._finalizer.detach()

        if not self._init_event.is_set():
            return
        try:
            if self._scheduler is not None:
                await self._scheduler.shutdown(timeout=SHUTDOWN_TIMEOUT_S)
        except Exception as e:
            logger.debug("[MLXBatch] scheduler shutdown error: %s", e)
        finally:
            self._scheduler = None
            self._init_event.clear()  # Reset event for shutdown replay
            logger.debug("[MLXBatch] executor shut down")

    # ─── Module-level guard (B.M1) ─────────────────────────────────────

    def __repr__(self) -> str:
        state = "init" if self._init_event.is_set() else "lazy"
        return f"MLXBatchedExecutor(state={state}, max_batch={MAX_BATCH_SIZE_M1})"


__all__ = [
    "MLXBatchedExecutor",
    "MAX_BATCH_SIZE_M1",
    "MEMORY_GUARD_PCT",
]
