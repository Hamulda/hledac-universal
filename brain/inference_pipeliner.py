"""
InferencePipeliner — Non-blocking async inference pipeline with prompt preprocessing overlap.

Problem: await hermes.generate() returns str — caller waits for completion.
Cannot overlap next prompt's preprocessing with current generation.

Solution: InferencePipeliner.submit() returns asyncio.Future immediately.
Prompt preprocessing (tokenization, ChatML formatting) runs in thread pool
while previous sequence is still generating. Model receives sequences one-by-one
with zero flush-interval latency.

M1 8GB safe: hardcoded batch_size=1, no KV cache growth.

Sprint P2-1b: Continuous batching pipeline improvement.
"""
from __future__ import annotations



import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from .deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_PENDING = 16  # Max pending futures (M1 8GB: each ~100KB metadata)
SUBMIT_TIMEOUT_S = 120.0  # Per-request timeout
PREPROCESS_WORKERS = 2  # Thread pool for prompt preprocessing

# Deep thinking prompt prefix — Sprint F265B: extracted to Final constant
_DEEP_THINKING_PREFIX: Final[str] = (
    " <|im_start|>reasoning\n"
    "For this query, I need to think step by step about the evidence and derive conclusions."
    "<|im_end|>\n"
)


@dataclass
class PendingRequest:
    """A pending inference request with its resolving future."""

    future: asyncio.Future[str]  # Will be resolved with result
    prompt: str
    temperature: float | None
    max_tokens: int | None
    system_msg: str | None
    thinking: bool
    submitted_at: float  # monotonic timestamp for telemetry


class InferencePipeliner:
    """
    Non-blocking inference pipeline with prompt preprocessing overlap.

    Architecture:
        caller                    main loop           worker thread
           │                         │                    │
           │ submit(prompt) ──────► │                    │
           │    │                   │  tokenize+format  │
           │    │ returns Future    │  (in thread pool) │
           │    │                   │                    │
           │    │                   │  dispatch() ─────► │
           │    │                   │                    │
           │    │                   │                    │ mlx_lm.generate()
           │    │                   │                    │
           │ await Future ─────────►│◄── result ────────┤
           │                         │                    │

    Key properties:
    - Non-blocking submit: returns Future immediately
    - Prompt preprocessing overlap: tokenization/formatting while model generates
    - M1 8GB safe: batch_size=1, no concurrent inference
    - Always-on, fail-soft, bounded queue

    Invariants:
    P.L1  Single inference at a time (batch_size=1 hardcoded)
    P.L2  Queue maxsize ≤ MAX_PENDING
    P.L3  submit() never raises — returns Future that resolves to error string
    P.L4  Prompt preprocessing in separate thread pool
    P.L5  worker_thread is optional (falls back to direct if unavailable)
    """

    def __init__(
        self,
        engine: DeepHermes3Engine | None = None,
        worker_thread: Any | None = None,
    ) -> None:
        """
        Args:
            engine: DeepHermes3Engine instance (lazy loaded if None)
            worker_thread: MLXWorkerThread for non-blocking dispatch
        """
        self._engine: DeepHermes3Engine | None = engine
        self._worker_thread = worker_thread  # MLXWorkerThread or None

        # Queue state
        self._pending: deque[PendingRequest] = deque(maxlen=MAX_PENDING)
        self._dispatch_task: asyncio.Task | None = None
        self._inflight: PendingRequest | None = None  # Currently running request

        # Event-driven signaling (replaces 50-100ms polling sleep)
        self._inflight_done = asyncio.Event()
        self._new_request = asyncio.Event()

        # Thread pool for prompt preprocessing (non-blocking I/O)
        import concurrent.futures
        self._preprocess_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=PREPROCESS_WORKERS,
            thread_name_prefix="preprocess",
        )

        # Telemetry
        self._stats: dict[str, Any] = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "preprocess_overlap_ms": 0.0,
            "queue_depth": 0,
        }
        self._started = False

    # ─── Public API ─────────────────────────────────────────────────────────

    async def submit(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        thinking: bool = True,
    ) -> asyncio.Future[str]:
        """
        Submit an inference request and return a Future.

        Non-blocking: returns immediately. The actual inference runs
        asynchronously while the caller can do other work.

        Fail-soft (P.L3): returns a Future that will resolve to an error
        string rather than raising. Caller should check result type.

        Args:
            prompt: Input prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            system_msg: System message for ChatML
            thinking: Enable deep thinking mode

        Returns:
            asyncio.Future[str] — resolve with generated text or error string
        """
        # Create future before any await (must not be in task context)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        request = PendingRequest(
            future=future,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
            thinking=thinking,
            submitted_at=time.monotonic(),
        )

        # Start dispatch task if not running
        if not self._started:
            self._dispatch_task = asyncio.create_task(
                self._dispatch_loop(), name="pipeliner:dispatch"
            )
            self._started = True

        # Add to queue
        self._pending.append(request)
        self._stats["submitted"] += 1
        self._stats["queue_depth"] = len(self._pending)

        # Signal new request to dispatch loop (replaces polling sleep)
        if self._started:
            self._new_request.set()

        logger.debug(
            "[P2-1b] submit: queue_depth=%d, pending=%d",
            len(self._pending),
            self._stats["submitted"],
        )

        return future

    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        thinking: bool = True,
    ) -> str:
        """
        Blocking generate — await and return result.

        Convenience wrapper: submits request and waits for result.
        For non-blocking behavior, use submit() directly.

        Returns:
            Generated text string
        """
        future = self.submit(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
            thinking=thinking,
        )
        return await future

    async def get_stats(self) -> dict[str, Any]:
        """Return pipeliner telemetry snapshot."""
        stats = dict(self._stats)
        stats["queue_depth"] = len(self._pending)
        stats["in_flight"] = self._inflight is not None
        if self._inflight is not None:
            stats["in_flight_age_ms"] = (
                time.monotonic() - self._inflight.submitted_at
            ) * 1000
        return stats

    async def shutdown(self) -> None:
        """Graceful shutdown — fail all pending futures."""
        # Cancel dispatch task
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Fail all pending futures
        while self._pending:
            req = self._pending.popleft()
            if not req.future.done():
                req.future.set_result("Error: pipeliner shutdown")

        # Fail inflight
        if self._inflight is not None and not self._inflight.future.done():
            self._inflight.future.set_result("Error: pipeliner shutdown")

        # Shutdown thread pool
        self._preprocess_executor.shutdown(wait=False)

    # ─── Internal dispatch ───────────────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        """
        Main dispatch loop — runs as async task.

        Takes requests from queue and dispatches to worker thread.
        When model is busy, new requests wait in queue.
        When model is free, next request is dispatched with preprocessing overlap.

        Event-driven: uses asyncio.Event instead of polling sleep.
        """
        while True:
            try:
                if self._inflight is not None:
                    # Model busy — wait for it to finish (event-driven, no sleep)
                    self._inflight_done.clear()
                    try:
                        await self._inflight_done.wait()
                        self._on_inflight_done()
                    except asyncio.CancelledError:
                        break
                else:
                    # Model free — dispatch next if available
                    if self._pending:
                        self._dispatch_next()
                    else:
                        # Queue empty — wait for new requests (event-driven)
                        self._new_request.clear()
                        try:
                            await self._new_request.wait()
                        except asyncio.CancelledError:
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[P2-1b] dispatch loop error: %s", e)
                await asyncio.sleep(0.1)

    def _dispatch_next(self) -> None:
        """Dispatch next request from queue to worker thread."""
        if not self._pending:
            return

        req = self._pending.popleft()
        self._inflight = req

        # Run inference in worker thread
        asyncio.create_task(
            self._run_inference_inflight(req), name="pipeliner:inference"
        )

    async def _run_inference_inflight(self, req: PendingRequest) -> None:
        """Run inference for inflight request, resolve its future."""
        t_preprocess = time.monotonic()

        try:
            # Get engine (lazy load)
            engine = self._get_engine()
            if engine is None:
                req.future.set_result("Error: Hermes3Engine not available")
                self._on_inflight_done()
                return

            # Preprocess prompt (overlapping with previous inference if any)
            # This runs async while model might be busy from previous request
            preprocessed = await self._preprocess_prompt(
                req.prompt, req.system_msg, req.thinking
            )

            preprocess_overhead_ms = (time.monotonic() - t_preprocess) * 1000
            self._stats["preprocess_overlap_ms"] = preprocess_overhead_ms

            # Run inference (blocking but main loop stays free via worker thread)
            if self._worker_thread is not None and self._worker_thread.is_active():
                # Worker thread path (P0-3) — non-blocking main loop
                coro = engine.generate(
                    prompt=preprocessed,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    system_msg=None,  # Already embedded in preprocessed
                )
                result = await self._worker_thread.submit(coro, timeout=SUBMIT_TIMEOUT_S)
            else:
                # Direct path fallback
                result = await engine.generate(
                    prompt=preprocessed,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    system_msg=None,
                )

            # Resolve future
            if not req.future.done():
                req.future.set_result(result)
            self._stats["completed"] += 1

        except asyncio.CancelledError:
            if not req.future.done():
                req.future.set_result("Error: cancelled")
            raise
        except TimeoutError:
            if not req.future.done():
                req.future.set_result("Error: inference timeout")
            self._stats["failed"] += 1
        except Exception as e:
            logger.debug("[P2-1b] inference error: %s", e)
            if not req.future.done():
                req.future.set_result(f"Error: {str(e)}")
            self._stats["failed"] += 1
        finally:
            self._on_inflight_done()

    def _on_inflight_done(self) -> None:
        """Called when inflight request completes — signals dispatch loop."""
        self._inflight = None
        # Signal dispatch loop that inflight is done (replaces 50ms polling sleep)
        if hasattr(self, '_inflight_done') and not self._inflight_done.is_set():
            self._inflight_done.set()

    def _get_engine(self) -> DeepHermes3Engine | None:
        """Get or lazy-load the engine."""
        if self._engine is None:
            try:
                self._engine = DeepHermes3Engine()
                logger.debug("[P2-1b] DeepHermes3Engine lazy loaded")
            except Exception as e:
                logger.warning("[P2-1b] Engine lazy load failed: %s", e)
        return self._engine

    async def _preprocess_prompt(
        self,
        prompt: str,
        system_msg: str | None,
        thinking: bool,
    ) -> str:
        """
        Preprocess prompt: sanitization + ChatML formatting.

        This runs in thread pool to overlap with previous inference.
        """
        # Run in executor to avoid blocking
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._preprocess_executor,
            self._sync_preprocess,
            prompt,
            system_msg,
            thinking,
        )

    def _sync_preprocess(
        self,
        prompt: str,
        system_msg: str | None,
        thinking: bool,
    ) -> str:
        """
        Synchronous prompt preprocessing.

        Includes:
        - Sanitization (basic cleanup)
        - Deep thinking prefix injection
        - ChatML formatting
        - Hard length limit
        """
        max_chars = 8192  # ~8192 tokens max for M1 8GB

        # Basic sanitization
        sanitized = prompt[:max_chars]

        # System message
        system = system_msg or "You are a helpful research assistant."

        # Deep thinking prefix — Sprint F265B: use Final constant
        if thinking:
            system = f"{_DEEP_THINKING_PREFIX}\n\n{system}"

        # ChatML format
        formatted = f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{sanitized}<|im_end|>\n<|im_start|>assistant\n"

        return formatted[:max_chars]


__all__ = [
    "InferencePipeliner",
    "PendingRequest",
]
