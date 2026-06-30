"""
MLXWorkerThread — Dedicated thread with persistent event loop for MLX inference.

Pattern: thread-per-loop, single Metal context, single MLX model state.

Why this exists (Sprint P0-3):
    The current code uses `loop.run_in_executor(self._inference_executor, sync_fn)`
    to offload blocking mlx_lm.generate() calls to a ThreadPoolExecutor.
    While the executor is non-blocking from the thread pool's perspective, the
    main asyncio loop is parked in `await asyncio.wait_for(future, timeout=...)`
    for the entire inference duration (~1-30s for 50-200 tokens). During that
    window the main loop cannot service other coroutines (HTTP fetch, DB
    ingest, scheduled sidecars).

    MLXWorkerThread fixes this:
        1. A dedicated background thread runs `loop.run_forever()` from start
        2. MLX model state (loaded weights, KV cache, prompt cache, warm-up)
           lives INSIDE the worker thread and persists across all inference
           calls — no per-request teardown
        3. Hlavní asyncio loop submits via `asyncio.run_coroutine_threadsafe`,
           returning a Future that the main loop can `await` non-blockingly
        4. While inference runs in the worker, the main loop is FREE to
           process HTTP/DB/IO coroutines

M1 8GB safety:
    - Single thread, single MLX context, single model — no shared state
    - Lazy start (worker thread is created on first submit, not at import)
    - Fail-soft: if thread start fails or crashes, submit() raises and
      caller falls back to ThreadPoolExecutor path
    - Bounded shutdown: ≤ 5.0s, no orphan tasks

Invariants (P0-3):
    M.T1  Single thread, single model — no concurrent MLX in worker
    M.T2  Lazy start: thread created on first submit(), not at __init__
    M.T3  Fail-soft: start failure or thread death → submit raises
          RuntimeError("mlx_worker_unavailable: ...")
    M.T4  Bounded shutdown ≤ 5.0s
    M.T5  request_count telemetry (B.M7-style)
    M.T6  Default submit timeout 60s (matches P1F-A hermes timeout)
    M.T7  is_alive() check on every submit (replaces zombie thread)
    M.T8  Daemon thread — never blocks process exit

Always-on, no feature flag, no env var.
M1 8GB safe.
"""


import asyncio
import atexit
import logging
import threading
import time
import weakref
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

# ─── Bounded constants ──────────────────────────────────────────────────
DEFAULT_SUBMIT_TIMEOUT_S: float = 60.0
THREAD_START_TIMEOUT_S: float = 5.0
SHUTDOWN_TIMEOUT_S: float = 5.0
WORKER_THREAD_NAME: str = "mlx-worker"


# ─── Module-level cleanup callback for weakref.finalize ──────────────

def _worker_at_exit_shutdown(instance: MLXWorkerThread) -> None:
    """Called by weakref.finalize at interpreter exit if explicit shutdown() was not called.

    MLXWorkerThread is a daemon thread (M.T8) but the event loop
    and asyncio internals may not clean up properly at exit.
    weakref.finalize + atexit ensures bounded cleanup (≤ 5.0s) runs even when:
      1. Caller forgot explicit shutdown()
      2. Thread was never started (lazy start)
      3. Interpreter is exiting via atexit

    This is synchronous (runs in main thread) — we signal the worker
    loop to stop and join the thread with timeout.
    """
    try:
        if instance._loop is not None and not instance._loop.is_closed():
            try:
                instance._loop.call_soon_threadsafe(instance._loop.stop)
            except Exception:
                pass
        if instance._thread is not None and instance._thread.is_alive():
            instance._thread.join(timeout=SHUTDOWN_TIMEOUT_S)
    except Exception:
        pass


class MLXWorkerThread:
    """
    Dedicated background thread with persistent event loop.

    Public API:
        start()                       — idempotent, lazy
        async submit(coro, timeout)   — schedule coro in worker loop
        shutdown(timeout=5.0)         — bounded stop
        is_active()                   — True if thread alive
        get_stats()                   — telemetry snapshot

    Thread model:
        - ONE daemon thread, OWN event loop (`loop.run_forever()`)
        - Hlavní asyncio loop stays free during inference
        - Submission via `asyncio.run_coroutine_threadsafe()` is the
          canonical way to schedule a coroutine on another loop
        - `asyncio.wrap_future()` bridges concurrent.futures.Future
          to asyncio.Future for non-blocking await

    Fail-soft:
        - If start() fails, submit() raises immediately
        - If thread dies mid-flight, submit() raises (caller falls back)
        - If submit times out, we cancel the future but cannot interrupt
          MLX mid-generation (single-threaded MLX, no preemption)
    """

    def __init__(self, name: str = WORKER_THREAD_NAME) -> None:
        """MLXWorkerThread constructor — does NOT start the thread (M.T2)."""
        self._name = name
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: threading.Event = threading.Event()
        self._stopped: bool = False
        self._failed: bool = False
        self._failure_reason: str | None = None
        self._request_count: int = 0
        self._inflight_count: int = 0
        self._peak_inflight: int = 0
        self._start_time: float | None = None
        self._lock: threading.Lock = threading.Lock()
        # P0-2 FIX: busy flag — worker is single-MLX-context (M.T1). When
        # MLX is in-generation, _busy=True prevents new requests from queueing
        # behind an occupied worker. Without this, requests pile up in the
        # worker's loop and timeout even though the worker is alive.
        self._busy: bool = False

        # P0: SPSC queue for fast-path submission (crossbeam-channel, ~2-5ns send).
        # Falls back to run_coroutine_threadsafe if queue is full or unavailable.
        self._spsc_sender: Any = None
        self._spsc_receiver_ptr: int = 0

        # F289: weakref.finalize for interpreter-exit cleanup guarantee.
        # MLXWorkerThread is a daemon thread (M.T8) but the event loop
        # and asyncio internals may not clean up properly at exit without
        # explicit shutdown. finalizer ensures bounded cleanup (≤ 5.0s).
        self._finalizer = weakref.finalize(
            self,
            _worker_at_exit_shutdown,
            self,
        )
        atexit.register(self._finalizer)

    # ─── SPSC Queue (P0) ─────────────────────────────────────────────

    def _init_spsc(self) -> None:
        """Initialize SPSC queue for fast-path submission.

        Creates a Rust-backed crossbeam-channel queue for ~2-5ns send
        from the main asyncio thread to the MLX worker thread.
        Falls back silently if Rust extension unavailable or queue full.
        """
        try:
            from hledac.universal.core.rust_backend import rust
            if not rust.is_available:
                return
            pair, sender = rust.spsc.SPSCQueuePair()
            # take_receiver() must be called from the worker thread
            # We store the pair object and call take_receiver in the worker
            self._spsc_pair = pair
            self._spsc_sender = sender
            logger.debug("[MLXWorker] SPSC queue initialized")
        except Exception as _e:
            logger.debug("[MLXWorker] SPSC queue init failed: %s", _e)

    # ─── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Start the worker thread and its event loop. Idempotent.

        Lazy per M.T2: thread is created on first start() call, never
        at __init__ time. Subsequent calls are no-ops.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._failed:
                # Don't retry — once failed, stays failed until shutdown()
                return
            self._ready = threading.Event()
            self._stopped = False
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self._name,
                daemon=True,  # M.T8: never block process exit
            )
            try:
                self._thread.start()
            except Exception as e:
                self._failed = True
                self._failure_reason = f"thread_start_failed: {e}"
                logger.warning("[MLXWorker] start failed: %s", self._failure_reason)
                return
            # Wait for loop to be ready (M.T3 fail-fast on timeout)
            if not self._ready.wait(timeout=THREAD_START_TIMEOUT_S):
                self._failed = True
                self._failure_reason = "thread_ready_timeout"
                logger.warning(
                    "[MLXWorker] thread did not become ready in %.1fs",
                    THREAD_START_TIMEOUT_S,
                )
                return
            if self._failed:
                logger.warning(
                    "[MLXWorker] thread failed during startup: %s",
                    self._failure_reason,
                )
                return
            self._start_time = time.monotonic()
            logger.debug(
                "[MLXWorker] started thread %s (id=%s)",
                self._thread.name,
                self._thread.ident,
            )
            # P0: Initialize SPSC queue after thread is ready
            self._init_spsc()

    def _run_loop(self) -> None:
        """
        Worker thread main: create + run event loop forever.

        Set the ready event AFTER the loop is created and assigned, so
        submit() can immediately schedule coroutines.

        F300S-FIX: Initialize Metal stream in worker thread so that MLX
        inference (which runs in this thread via run_coroutine_threadsafe)
        has stream affinity correct. Without this, get_metal_stream_context()
        caches the stream in the main thread's TLS, but mlx_lm.generate()
        runs in the worker thread — causing "Stream(gpu,1) not in current thread".
        """
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            # F300S-FIX: Prime Metal stream in this thread (worker) so that
            # subsequent mlx_lm.generate() calls have the correct stream context.
            # Entering the context materializes mx.stream(gpu) in worker thread TLS.
            try:
                from hledac.universal.utils.mlx_memory import get_metal_stream_context
                _stream_ctx = get_metal_stream_context()
                _stream_ctx.__enter__()
            except Exception:
                pass
            # Signal main thread that we're ready
            self._ready.set()
            # Run until stop() is called
            loop.run_forever()
        except Exception as e:
            self._failed = True
            self._failure_reason = f"loop_crashed: {e}"
            logger.warning("[MLXWorker] loop crashed: %s", e)
        finally:
            # Drain pending tasks gracefully
            try:
                if loop is not None and not loop.is_closed():
                    # Cancel and await all remaining tasks
                    pending = [
                        t for t in asyncio.all_tasks(loop=loop) if not t.done()
                    ]
                    for t in pending:
                        t.cancel()
                    if pending:
                        try:
                            # F314: asyncio.gather -> safe_gather_fire_and_forget
                            # Results discarded (fire-and-forget), just graceful cancellation.
                            loop.run_until_complete(
                                safe_gather_fire_and_forget(*pending, label="mlx_worker:shutdown")
                            )
                        except Exception:
                            pass
                    loop.close()
            except Exception as e:
                logger.debug("[MLXWorker] cleanup error: %s", e)
            self._loop = None

    def is_active(self) -> bool:
        """True if worker thread is alive, loop is running, and not busy.

        M.T7: also checks _stopped flag — once shutdown() is called, the
        worker is considered inactive even if the thread is still alive
        (e.g. blocked in MLX inference). This prevents callers from
        routing new requests to a worker that is already shutting down.

        P0-2 FIX: also checks _busy flag — single-MLX-context worker (M.T1)
        can only handle one inference at a time. If busy, is_active returns
        False so caller falls through to main-thread path without waiting.
        """
        if self._stopped:
            return False
        if self._thread is None:
            return False
        if not self._thread.is_alive():
            return False
        if self._failed:
            return False
        if self._loop is None or self._loop.is_closed():
            return False
        if self._busy:
            return False
        return True

    # ─── Public submit API ────────────────────────────────────────────

    async def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        timeout: float = DEFAULT_SUBMIT_TIMEOUT_S,
    ) -> Any:
        """
        Schedule a coroutine on the worker thread's event loop.

        Returns the result of `coro`. Raises:
            RuntimeError  — worker not started, failed, or thread dead
            TimeoutError  — coro did not complete in `timeout` seconds

        IMPORTANT: MLX inference cannot be interrupted mid-generation
        (single-threaded, no preemption). On timeout we cancel the
        asyncio.Task wrapper, but the underlying MLX call will continue
        to completion. The result is therefore silently discarded.
        """
        # Lazy start (M.T2)
        if self._thread is None:
            self.start()
        if self._failed or not self.is_active():
            raise RuntimeError(
                f"mlx_worker_unavailable: {self._failure_reason or 'not_active'}"
            )
        # P0-2 FIX: set busy flag — worker is single-MLX-context (M.T1).
        # Must be set BEFORE scheduling to prevent concurrent submissions.
        self._busy = True
        # Schedule on worker loop
        try:
            assert self._loop is not None
            cf_future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError as e:
            # Loop was closed between is_active() and run_coroutine_threadsafe
            self._failed = True
            self._failure_reason = f"loop_unavailable: {e}"
            self._busy = False
            raise RuntimeError(f"mlx_worker_unavailable: {self._failure_reason}")  # noqa: B904
        # Track request count (M.T5)
        self._request_count += 1
        self._inflight_count += 1
        if self._inflight_count > self._peak_inflight:
            self._peak_inflight = self._inflight_count
        try:
            # Bridge concurrent.futures.Future → asyncio.Future for non-blocking await.
            # asyncio.wrap_future() does NOT accept a timeout arg — use wait_for().
            bridge = asyncio.wrap_future(cf_future)
            return await asyncio.wait_for(bridge, timeout=timeout)
        except TimeoutError:
            # Try to cancel the underlying task — may not succeed if MLX is mid-flight
            try:
                cf_future.cancel()
            except Exception:
                pass
            raise
        finally:
            self._inflight_count -= 1
            # P0-2 FIX: clear busy flag — worker is ready for next request
            self._busy = False

    # ─── Shutdown ─────────────────────────────────────────────────────

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_S) -> None:
        """
        Bounded shutdown of worker thread.

        Idempotent. After shutdown, the worker is unusable — caller must
        instantiate a new MLXWorkerThread if needed. M.T4: max 5.0s.

        F289: Detaches finalizer on explicit call to prevent double-cleanup
        at interpreter exit. After detach(), atexit no longer triggers
        _worker_at_exit_shutdown.
        """
        # Detach finalizer — explicit close wins over atexit
        self._finalizer.detach()

        with self._lock:
            if self._thread is None:
                return
            if self._stopped:
                return
            self._stopped = True
            self._busy = False  # P0-2 FIX: worker is stopped, not busy
        # Signal loop to stop
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception as e:
                logger.debug("[MLXWorker] stop signal failed: %s", e)
        # Wait for thread to exit
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            # Force-mark failed; thread will exit on process shutdown
            self._failed = True
            self._failure_reason = "shutdown_timeout"
            logger.warning(
                "[MLXWorker] thread did not exit in %.1fs; marking failed", timeout
            )
        else:
            logger.debug("[MLXWorker] shutdown complete")
        self._thread = None
        self._loop = None
        # P0: Clean up SPSC resources
        self._spsc_sender = None
        self._spsc_pair = None

    # ─── Telemetry ────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot. Non-intrusive read."""
        stats: dict[str, Any] = {
            "active": self.is_active(),
            "failed": self._failed,
            "failure_reason": self._failure_reason,
            "request_count": self._request_count,
            "inflight_count": self._inflight_count,
            "peak_inflight": self._peak_inflight,
            "busy": self._busy,
            "thread_alive": (
                self._thread is not None and self._thread.is_alive()
            ),
            "thread_name": self._thread.name if self._thread is not None else None,
            "thread_id": self._thread.ident if self._thread is not None else None,
        }
        if self._start_time is not None:
            stats["uptime_s"] = time.monotonic() - self._start_time
        else:
            stats["uptime_s"] = 0.0
        # P0: SPSC queue stats
        if self._spsc_sender is not None:
            stats["spsc_available"] = True
            stats["spsc_available_slots"] = self._spsc_sender.available_slots()
            stats["spsc_has_space"] = self._spsc_sender.has_space()
        else:
            stats["spsc_available"] = False
        return stats

    # ─── Dunder ───────────────────────────────────────────────────────

    def __repr__(self) -> str:
        if self._failed:
            return f"MLXWorkerThread(failed={self._failure_reason!r})"
        if not self.is_active():
            return "MLXWorkerThread(state=stopped)"
        return (
            f"MLXWorkerThread(active, requests={self._request_count}, "
            f"inflight={self._inflight_count})"
        )


__all__ = [
    "MLXWorkerThread",
    "DEFAULT_SUBMIT_TIMEOUT_S",
    "THREAD_START_TIMEOUT_S",
    "SHUTDOWN_TIMEOUT_S",
]
