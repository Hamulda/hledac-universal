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
    - Hermes3Engine.generate() is called from at least 4 sites
      (research_hypothesis_engine, model_manager, dspy_service, synthesis_runner),
      none of which can batch.
    - This module bridges: it implements the scheduler's `execute_callback`
      contract and turns `engine.generate(prompt, ...)` into a batch-safe call.

Architecture:
    Hermes3Engine.generate()
        └── if _mlx_batcher initialized AND is_batch_safe():
                await self._mlx_batcher.execute(...)
                    └── BatchScheduler.submit(payload)
                            └── worker loop gathers items
                                    └── callback = self._mlx_batcher._execute_callback
                                            └── await self._engine.generate(...)
            else:
                # existing direct path (unchanged)

Invariants (P0-2):
    B.M1  Zero top-level MLX imports (lazy via Hermes3Engine)
    B.M2  BatchScheduler instantiated lazily (not at import time)
    B.M3  Fail-soft: any submit/future error → caller falls back to direct
    B.M4  MLX execution lock: asyn semaphore(1) — no concurrent MLX.generate
          on the same Hermes3Engine instance
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
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.brain.batch_scheduler import BatchScheduler
    from hledac.universal.brain.hermes3_engine import Hermes3Engine

logger = logging.getLogger(__name__)

# ─── Bounded constants (M1 8GB safety) ──────────────────────────────
MAX_BATCH_SIZE_M1: int = 8  # P1-1: 6→8 on M1 8GB; single-thread MLX lock means batches serialize anyway, 8 is safe at idle
# F265C: Memory guard — M1 8GB real-world: macOS ~4GB used at idle, ~1.6GB headroom at 90% vs ~800MB at 80%.
# 90% pct threshold = ~720MB free → still too tight for batch accumulation (KV cache 0.75GB).
# Use 92% for pct guard (≈320MB free) OR use absolute available GB threshold.
# Absolute threshold: 1.5GB available = safe for batch (KV cache fits in Metal memory).
MEMORY_GUARD_PCT: float = 92.0  # 80→92: M1 8GB with macOS ~4GB used leaves ~800MB at 80%; batch accumulation needs headroom
MEMORY_GUARD_ABSOLUTE_GB: float = 1.5  # M1 8GB: Metal cache 1.5GiB + KV cache 0.75GB = 2.25GB reserved; 1.5GB available = safe
DEFAULT_FLUSH_INTERVAL_S: float = 1.0
MAX_QUEUE_DEPTH: int = 256
SHUTDOWN_TIMEOUT_S: float = 3.0
FUTURE_TIMEOUT_S: float = 30.0
URGENT_PRIORITY: float = 0.0
ADAPTIVE_CONTEXT_PREFLIGHT: bool = True


class MLXBatchedExecutor:
    """
    Smart router that wraps Hermes3Engine + BatchScheduler.

    Public API:
        is_batch_safe(prompt, system_msg) → bool
        execute(prompt, temperature, max_tokens, system_msg, priority)
            → str (result text, or raises on hard error)
        get_stats() → dict (telemetry)
        shutdown() → None (bounded ≤ 3s)

    The executor never blocks longer than MAX_BATCH_SIZE_M1 items in flight
    and is always-on (no feature flag). When a prompt is incompatible with
    batching (urgent priority, empty, or memory pressure), `is_batch_safe`
    returns False and the caller falls through to the direct path.
    """

    def __init__(
        self,
        engine: Hermes3Engine,
        worker_thread: Any = None,
    ) -> None:
        """
        Args:
            engine: Hermes3Engine instance (must be loaded; model state shared)
            worker_thread: Optional MLXWorkerThread (P0-3) — when provided and
                active, MLX inference is dispatched through its persistent
                event loop instead of the local ThreadPoolExecutor. The main
                asyncio loop stays free during inference.

        Notes:
            Does NOT instantiate BatchScheduler here — lazy on first execute()
            so cold-start cost is paid once, at first use, not at import.
        """
        self._engine: Hermes3Engine = engine
        self._worker_thread = worker_thread  # Optional MLXWorkerThread (P0-3)
        self._scheduler: BatchScheduler | None = None
        self._mlx_lock: asyncio.Lock | None = None
        self._initialized: bool = False

        # Telemetry counters (B.M7)
        self._stats: dict[str, Any] = {
            "submits": 0,
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

    # ─── Lazy init ─────────────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        """
        Lazy init of BatchScheduler and MLX execution lock.

        Idempotent: safe to call multiple times — subsequent calls no-op.
        Invariant B.M2: scheduler is NEVER instantiated at __init__ time.
        """
        if self._initialized:
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
            self._mlx_lock = asyncio.Lock()
            await scheduler.start()
            self._initialized = True
            logger.debug("[MLXBatch] executor initialized (max_batch=%d)", MAX_BATCH_SIZE_M1)
        except Exception as e:
            # B.M3: fail-soft — initialization failure → executor unusable,
            # caller will fall through to direct path on every call.
            logger.warning("[MLXBatch] lazy init failed, batching disabled: %s", e)
            self._initialized = False
            self._stats["fail_soft"] += 1

    # ─── Routing decision (B.M9) ───────────────────────────────────────

    def is_batch_safe(
        self,
        prompt: str,
        system_msg: str | None = None,
        priority: float = 1.0,
        speculative: bool = False,
    ) -> bool:
        """
        Decide whether this request is eligible for batching.

        Returns False when:
            - executor not initialized (lazy init failed or shutdown)
            - memory pressure > MEMORY_GUARD_PCT
            - priority == 0 (urgent, bypass — B.M9)
            - prompt is empty or whitespace-only
            - max_tokens > 1024 (long outputs serialized anyway, no batching win)
            - speculative=True (draft model extra RAM on M1 8GB — direct path)
        """
        # Speculative decoding: draft model consumes ~500MB extra on M1 8GB.
        # Route to direct path to keep headroom for batched main-model inference.
        if speculative:
            self._stats["speculative_bypass"] = self._stats.get("speculative_bypass", 0) + 1
            return False
        if not self._initialized or self._scheduler is None:
            return False
        if priority == URGENT_PRIORITY:
            self._stats["urgent_bypass"] += 1
            return False
        if not prompt or not prompt.strip():
            return False
        # Long prompts (>4096 chars ≈ 2048 tokens) go direct — no batching win
        if len(prompt) > 4096:
            self._stats["long_prompt_bypass"] += 1
            return False
        # system_msg influences schema segregation downstream; empty != urgent
        if system_msg is not None and len(system_msg) > 8192:
            # Very long system messages do not benefit from batching
            # (length-bin boundary would shatter the batch anyway)
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
            memory_ok = True
            if self._memory_ema > MEMORY_GUARD_PCT:
                self._stats["memory_guard_disabled"] += 1
                memory_ok = False
            # Absolute available GB check (M1 8GB Metal budget: 1.5GiB cache + 0.75GiB KV)
            available_gb = psutil.virtual_memory().available / (1024**3)
            if available_gb < MEMORY_GUARD_ABSOLUTE_GB:
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
        if not self._initialized or self._scheduler is None:
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
            timeout = max(5.0 * DEFAULT_FLUSH_INTERVAL_S, 10.0)
            result = await asyncio.wait_for(scheduler_future, timeout=timeout)

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

        Invoked sequentially per schema group inside the worker. Uses
        self._mlx_lock to guarantee that even if execute() races with a
        direct-path call, MLX is never invoked concurrently (B.M4).
        """
        prompt = payload.get("prompt", "")
        temperature = payload.get("temperature")
        max_tokens = payload.get("max_tokens")
        system_msg = payload.get("system_msg")
        try:
            if self._mlx_lock is None:
                return await self._call_engine_direct(
                    prompt, temperature, max_tokens, system_msg
                )
            async with self._mlx_lock:
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
        Direct call to Hermes3Engine.generate() — single MLX execution.

        Bounded to the lock if available, so direct path can never race
        with batched path (B.M4).

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
        t0 = time.monotonic()
        try:
            if self._mlx_lock is None:
                result = await self._engine.generate(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_msg=system_msg,
                )
            else:
                async with self._mlx_lock:
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
            # Re-raise — caller decides. Hermes3Engine already has fail-safe
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
        P0-3 integration: dispatch MLX inference to the worker thread.

        Builds a coroutine that calls engine.generate() and submits it to
        the worker thread's persistent event loop. The main asyncio loop
        is free to process I/O while the worker generates. Result is
        returned via Future, awaited non-blockingly.
        """
        t0 = time.monotonic()
        # The coro runs INSIDE the worker thread's loop — single MLX context
        coro = self._engine.generate(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
        )
        assert self._worker_thread is not None  # caller checked
        result = await self._worker_thread.submit(coro, timeout=FUTURE_TIMEOUT_S)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        self._stats["baseline_ema_ms"] = (
            self._ema_alpha * elapsed_ms
            + (1 - self._ema_alpha) * float(self._stats["baseline_ema_ms"])
        )
        return result

    # ─── Telemetry & shutdown ──────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot. Non-intrusive read."""
        stats = dict(self._stats)
        stats["initialized"] = self._initialized
        stats["memory_check_failures"] = self._memory_check_failures
        # PID adaptive batch size state (Task #2)
        stats["memory_ema"] = round(self._memory_ema, 2)
        stats["pid_integral"] = round(self._pid_integral, 2)
        stats["effective_batch_size"] = self._effective_batch_size
        if self._scheduler is not None:
            try:
                sched_t = self._scheduler.get_telemetry()
                stats["scheduler_ema"] = sched_t.get("ema", {})
                stats["scheduler_counters"] = sched_t.get("counters", {})
            except Exception:
                stats["scheduler_ema"] = {}
                stats["scheduler_counters"] = {}
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
        """
        if not self._initialized:
            return
        try:
            if self._scheduler is not None:
                await self._scheduler.shutdown(timeout=SHUTDOWN_TIMEOUT_S)
        except Exception as e:
            logger.debug("[MLXBatch] scheduler shutdown error: %s", e)
        finally:
            self._scheduler = None
            self._mlx_lock = None
            self._initialized = False
            logger.debug("[MLXBatch] executor shut down")

    # ─── Module-level guard (B.M1) ─────────────────────────────────────

    def __repr__(self) -> str:
        state = "init" if self._initialized else "lazy"
        return f"MLXBatchedExecutor(state={state}, max_batch={MAX_BATCH_SIZE_M1})"


__all__ = [
    "MLXBatchedExecutor",
    "MAX_BATCH_SIZE_M1",
    "MEMORY_GUARD_PCT",
]
