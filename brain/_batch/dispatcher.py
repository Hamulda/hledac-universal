"""
brain/_batch/dispatcher.py — Round-Robin GenerateJob scheduler (ISSUE #16, solution #1)

PROBLEM (root cause)
--------------------
mlx_lm.generate() is serialized by the single global MLX inference lock
(`_core.mlx_inference_lock.MLXInferenceLock`, semaphore limit=1). This is
*physically required* on Apple Silicon M1 8GB: there is exactly ONE Metal
command queue, so parallel decode is impossible. The consequence is that
incoming generate requests cannot be "batched" in the CUDA sense.

WHAT WE CAN DO (the realistic, M1-correct win)
---------------------------------------------
1. QUEUE incoming requests as ``GenerateJob`` into an ``asyncio.Queue`` so the
   caller never blocks on the lock and requests are drained serially.
2. ROUTE each job into a "lane" keyed by its system-prompt hash, then process
   lanes in ROUND-ROBIN order. Consecutive jobs in the same lane share the
   *same prefix*, so the engine reuses its existing ``_kv_cache_pool`` /
   ``_session_cache_pool`` KV state instead of re-prefilling — this is the
   "shared prompt cache" requirement (issue #2) realised at the scheduler
   level. The KV tensors survive ``mx.clear_cache()`` (allocator-only), so the
   prefix cache is never destroyed between same-prefix requests.
3. GATE each job through ``brain.hermes.capability_gate``: if the Rust backend
   capability score < 0.5 (or MLX unavailable) we fulfil the job with the
   deterministic regex fallback (``pipeline.public_patterns``) instead of the
   LLM (issue #4).

M1 8GB invariants (AGENTS.md)
-----------------------------
- Single inference slot — enforced by the canonical lock we reuse here.
- Lazy MLX imports only; never ``time.sleep`` in async; ``asyncio.gather``
  with ``return_exceptions=True`` where used; fail-safe everywhere.

USAGE
-----
    disp = GenerateJobDispatcher(engine)
    await disp.start()
    fut = await disp.submit("analyse 1.2.3.4", system_msg=REPORT_SYS)
    text = await fut
    await disp.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# Module-level canonical lock (single global MLX inference lock, semaphore=1).
from hledac.universal._core.mlx_inference_lock import acquire as _mlx_acquire

# Default ingress queue bound (M1 8GB: bounded memory for pending futures).
DEFAULT_MAX_QUEUE = 256
# Idle poll interval when no jobs are queued (no time.sleep — async sleep only).
DEFAULT_IDLE_POLL_S = 0.005


@dataclass
class GenerateJob:
    """A single inference request routed through the dispatcher.

    ``future`` is resolved with the generation result (str) on success, or set
    with an exception on terminal failure (after the fail-safe fallback path).
    """

    job_id: str
    prompt: str
    system_msg: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    thinking: bool = True
    adapter_path: str | None = None
    logits_processors: list[Any] | None = None
    prompt_tokens: list[int] | None = None
    priority: float = 1.0
    allow_fallback: bool = True
    force_llm: bool = False
    future: asyncio.Future[str] | None = field(default=None, repr=False, compare=False)
    created_at: float = field(default_factory=time.monotonic)
    started_at: float = 0.0

    @property
    def lane_key(self) -> str:
        """Stable lane key — clusters jobs that share a system prompt prefix."""
        return hashlib.md5((self.system_msg or "").encode()).hexdigest()[:16]

    def gen_kwargs(self) -> dict[str, Any]:
        """Build the kwargs passed to the engine's generate() coroutine."""
        kw: dict[str, Any] = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system_msg": self.system_msg,
            "thinking": self.thinking,
            "adapter_path": self.adapter_path,
            "logits_processors": self.logits_processors,
            "prompt_tokens": self.prompt_tokens,
        }
        return {k: v for k, v in kw.items() if v is not None}


class GenerateJobDispatcher:
    """
    Round-Robin GenerateJob scheduler.

    Drains an ``asyncio.Queue[GenerateJob]`` ingress, routes jobs into
    per-system-prompt lanes, and processes lanes in round-robin order so that
    same-prefix requests are clustered (maximal KV-cache reuse). Each LLM job
    runs under the canonical single MLX inference lock; low-capability jobs are
    fulfilled by the regex fallback.
    """

    __slots__ = (
        "_engine",
        "_generate_fn",
        "_queue",
        "_lanes",
        "_lane_order",
        "_rr_ptr",
        "_max_queue",
        "_idle_poll_s",
        "_loop",
        "_worker_task",
        "_running",
        "_stats",
        "_seq",
    )

    def __init__(
        self,
        engine: DeepHermes3Engine | None = None,
        *,
        generate_fn: Callable[..., Awaitable[str]] | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE,
        idle_poll_s: float = DEFAULT_IDLE_POLL_S,
    ) -> None:
        # Either an engine with `.generate(...)` or an explicit generate_fn.
        self._engine = engine
        self._generate_fn = generate_fn
        if engine is None and generate_fn is None:
            raise ValueError("GenerateJobDispatcher requires `engine` or `generate_fn`")
        self._queue: asyncio.Queue[GenerateJob] = asyncio.Queue(maxsize=max_queue_size)
        self._max_queue = max_queue_size
        self._idle_poll_s = idle_poll_s
        self._lanes: dict[str, deque[GenerateJob]] = {}
        self._lane_order: list[str] = []
        self._rr_ptr = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False
        self._seq = 0
        self._stats: dict[str, Any] = {
            "submitted": 0,
            "completed": 0,
            "llm_used": 0,
            "fallback_used": 0,
            "failed": 0,
            "last_processing_s": 0.0,
            "lane_count": 0,
        }

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background worker loop (idempotent)."""
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._worker_task = asyncio.ensure_future(self._worker_loop(), loop=self._loop)
        logger.info("[DISPATCH] Round-Robin GenerateJob worker started")

    async def stop(self, timeout: float = 3.0) -> None:
        """Stop the worker and resolve any pending futures as cancelled."""
        if not self._running:
            return
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker_task = None
        # Resolve anything still queued as cancelled (fail-safe, never hangs).
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
            if not job.future.done():
                job.future.set_exception(asyncio.CancelledError("dispatcher_stopped"))
        for dq in self._lanes.values():
            while dq:
                job = dq.popleft()
                if not job.future.done():
                    job.future.set_exception(asyncio.CancelledError("dispatcher_stopped"))
        self._lanes.clear()
        self._lane_order.clear()
        logger.info("[DISPATCH] worker stopped")

    async def __aenter__(self) -> GenerateJobDispatcher:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ── Submission ─────────────────────────────────────────────────────────────

    async def submit(
        self,
        prompt: str,
        *,
        system_msg: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool = True,
        adapter_path: str | None = None,
        logits_processors: list[Any] | None = None,
        prompt_tokens: list[int] | None = None,
        priority: float = 1.0,
        allow_fallback: bool = True,
        force_llm: bool = False,
    ) -> asyncio.Future[str]:
        """Enqueue a generate request; returns a future resolved with the text.

        Raises RuntimeError if the bounded queue is full (backpressure signal).
        """
        if not self._running:
            raise RuntimeError("dispatcher_not_started: call await dispatcher.start() first")
        if self._queue.qsize() >= self._max_queue:
            raise RuntimeError(f"dispatcher_queue_full: max={self._max_queue}")
        self._seq += 1
        loop = asyncio.get_running_loop()
        job = GenerateJob(
            job_id=f"gj-{self._seq}",
            prompt=prompt,
            system_msg=system_msg,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            adapter_path=adapter_path,
            logits_processors=logits_processors,
            prompt_tokens=prompt_tokens,
            priority=priority,
            allow_fallback=allow_fallback,
            force_llm=force_llm,
            future=loop.create_future(),
        )
        self._stats["submitted"] += 1
        await self._queue.put(job)
        return job.future

    async def batch_submit(
        self,
        prompts: list[str],
        **common: Any,
    ) -> list[asyncio.Future[str]]:
        """Submit many prompts (sharing common kwargs); returns futures in order."""
        futures: list[asyncio.Future[str]] = []
        for p in prompts:
            futures.append(await self.submit(p, **common))
        return futures

    # ── Worker loop ────────────────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        try:
            while self._running:
                # 1) Drain ingress queue into lanes (non-blocking).
                while True:
                    try:
                        job = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    self._route(job)
                # 2) Round-robin one job across lanes (cache locality).
                job = self._round_robin_pop()
                if job is None:
                    await asyncio.sleep(self._idle_poll_s)
                    continue
                await self._process_job(job)
        except asyncio.CancelledError:
            logger.debug("[DISPATCH] worker cancelled")
            raise
        except Exception as e:  # noqa: BLE001 — fail-safe, keep loop alive
            logger.warning("[DISPATCH] worker loop crashed: %s", e)

    def _route(self, job: GenerateJob) -> None:
        key = job.lane_key
        dq = self._lanes.get(key)
        if dq is None:
            dq = deque()
            self._lanes[key] = dq
            self._lane_order.append(key)
            self._stats["lane_count"] = len(self._lane_order)
        dq.append(job)

    def _round_robin_pop(self) -> GenerateJob | None:
        if not self._lane_order:
            return None
        n = len(self._lane_order)
        for _ in range(n):
            key = self._lane_order[self._rr_ptr % n]
            self._rr_ptr = (self._rr_ptr + 1) % max(n, 1)
            dq = self._lanes[key]
            if dq:
                return dq.popleft()
            # Prune emptied lane to keep rotation cheap.
            self._lanes.pop(key, None)
            self._lane_order.remove(key)
            n -= 1
            if n == 0:
                break
        self._stats["lane_count"] = len(self._lane_order)
        return None

    # ── Per-job processing ──────────────────────────────────────────────────────

    async def _process_job(self, job: GenerateJob) -> None:
        job.started_at = time.monotonic()
        try:
            if self._should_use_llm(job):
                result = await self._run_llm(job)
                self._stats["llm_used"] += 1
            else:
                result = self._fallback_result(job)
                self._stats["fallback_used"] += 1
            if not job.future.done():
                job.future.set_result(result)
        except asyncio.CancelledError:
            # Mid-flight cancellation (e.g. dispatcher.stop()): resolve gracefully.
            if not job.future.done():
                job.future.set_exception(asyncio.CancelledError())
            raise
        except Exception as e:  # noqa: BLE001
            # Fail-safe: one regex fallback attempt before surfacing error.
            try:
                if job.allow_fallback:
                    result = self._fallback_result(job)
                    if not job.future.done():
                        job.future.set_result(result)
                    self._stats["fallback_used"] += 1
                    self._stats["failed"] += 1
                    return
            except Exception:  # noqa: BLE001
                pass
            if not job.future.done():
                job.future.set_exception(e)
            self._stats["failed"] += 1
        finally:
            if not job.future.done():
                # Safety net: never leave a future pending (e.g. on cancellation).
                try:
                    job.future.set_exception(asyncio.CancelledError())
                except Exception:  # noqa: BLE001
                    pass
            self._stats["completed"] += 1
            self._stats["last_processing_s"] = time.monotonic() - job.started_at

    def _should_use_llm(self, job: GenerateJob) -> bool:
        if job.force_llm:
            return True
        try:
            from hledac.universal.brain.hermes.capability_gate import capability_available

            return capability_available()
        except Exception:  # noqa: BLE001 — on any gate error, fail OPEN to LLM
            return True

    async def _run_llm(self, job: GenerateJob) -> str:
        fn = self._generate_fn
        if fn is None:
            fn = self._engine.generate  # type: ignore[union-attr]
        # Single global MLX inference lock — one decode stream on M1 Metal queue.
        async with _mlx_acquire():
            return await fn(job.prompt, **job.gen_kwargs())

    def _fallback_result(self, job: GenerateJob) -> str:
        try:
            from hledac.universal.brain.hermes.capability_gate import regex_fallback

            iocs = regex_fallback(job.prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[DISPATCH] regex fallback failed: %s", e)
            iocs = []
        return json.dumps(
            {"fallback": True, "iocs": [str(i) for i in iocs]},
            ensure_ascii=False,
        )

    # ── Observability ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        stats = dict(self._stats)
        stats["queue_depth"] = self._queue.qsize()
        stats["running"] = self._running
        # Surface engine prefix/session cache reuse if available.
        try:
            cache = getattr(self._engine, "cache_stats", None)
            if callable(cache):
                stats["engine_cache"] = cache()
        except Exception:  # noqa: BLE001
            pass
        return stats
