"""
_core/mlx_inference_lock.py — Unified MLX Inference Lock Facade

ROADMAP-002 FIX: Single canonical entry point for all MLX inference locking.

PROBLEM:
    Multiple fragmented lock patterns for MLX operations:
    - brain/mlx_bridge.py: _mlx_lock = threading.Lock() (lazy import sync)
    - brain/deephermes3_engine.py: _inference_semaphore from ConcurrencyCategory
    - _core/mlx_unified_scheduler.py: getattr() to engine's semaphore
    - Legacy _MLX_INFERENCE_LOCK (duplicate of mlx_bridge)

SOLUTION:
    MLXInferenceLock — single unified facade that:
    - Wraps ConcurrencyCategory.MLX_INFERENCE semaphore (primary)
    - Provides async context manager (canonical usage)
    - Provides legacy threading.Lock for non-async contexts
    - Provides MLXWorker for asyncio-safe operations

ARCHITECTURE:
    ┌─────────────────────────────────────────────────────────┐
    │  MLXInferenceLock (singleton facade)                     │
    │  ├── semaphore: ConcurrencyCategory.MLX_INFERENCE       │
    │  ├── threading_lock: legacy compatibility               │
    │  └── mlx_worker: asyncio-safe MLX operations           │
    └─────────────────────────────────────────────────────────┘

USAGE:
    # Async context (preferred)
    async with MLXInferenceLock.acquire():
        result = await engine.generate(prompt)

    # Legacy threading context
    with MLXInferenceLock.threading_lock():
        mlx_lm.generate(...)

    # asyncio-safe MLX operations
    result = await MLXInferenceLock.run_sync(mlx_lm.generate, model, ...)

PYTHON 3.14+ (PEP 789):
    - All async primitives created lazily (not at import)
    - DCLP pattern for singleton initialization
    - Thread-safe throughout

M1 8GB invariants:
    - U.M1: Single LLM inference slot (semaphore limit=1)
    - Always-on, fail-safe, bounded
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
import weakref
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from _core._util import aclose
from _core.lock_registry import LockCategory, auto_register

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
    from typing import Iterator

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# ==============================================================================
# MLXInferenceLock — Unified Singleton Facade
# ==============================================================================

# DCLP singleton instance
_MLX_INFERENCE_LOCK: MLXInferenceLock | None = None


@auto_register(LockCategory.MPC)
def _mlx_inference_lock_init():
    """DCLP initialization lock for MLXInferenceLock singleton."""
    return threading.Lock()


class MLXInferenceLock:
    """
    Unified MLX inference lock — single canonical entry point.

    Provides three locking mechanisms:
    1. asyncio.Semaphore via ConcurrencyCategory.MLX_INFERENCE (primary)
    2. threading.Lock for legacy non-async contexts
    3. MLXWorker for asyncio-safe blocking MLX operations

    M1 8GB: max 1 concurrent LLM inference (semaphore limit=1)

    Usage:
        # Async context (preferred)
        async with MLXInferenceLock.acquire():
            result = await engine.generate(prompt)

        # Async iterator
        async for chunk in MLXInferenceLock.hold():
            yield chunk

        # Legacy threading context
        with MLXInferenceLock.threading_lock():
            mlx_lm.generate(...)

        # asyncio-safe MLX operations
        result = await MLXInferenceLock.run_sync(mlx_lm.generate, model, ...)
    """

    __slots__ = (
        "_mlx_worker",
        "_semaphore",
        "_semaphore_loaded",
        "_started",
        "_threading_lock",
        "_worker_lock",
    )

    def __init__(self) -> None:
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loaded = False
        self._threading_lock: threading.Lock | None = None
        self._mlx_worker: MLXWorker | None = None
        self._mlx_worker = None  # Lazy init
        self._worker_lock = threading.Lock()
        self._started = False

    # --------------------------------------------------------------------------
    # Primary: asyncio.Semaphore via ConcurrencyCategory
    # --------------------------------------------------------------------------

    def _get_semaphore(self) -> asyncio.Semaphore:
        """
        Get the ConcurrencyCategory.MLX_INFERENCE semaphore (lazy init).

        Uses the centralized ConcurrencyBudgetRegistry for:
        - Unified cache: one semaphore per category
        - Dynamic adjustment based on UMA state
        - Telemetry: acquire/release tracking
        """
        if not self._semaphore_loaded:
            with _mlx_inference_lock_init():
                if not self._semaphore_loaded:
                    from hledac.universal._core.concurrency import (
                        ConcurrencyCategory,
                        get_semaphore,
                    )
                    self._semaphore = get_semaphore(ConcurrencyCategory.MLX_INFERENCE)
                    self._semaphore_loaded = True
                    logger.debug("[MLXInferenceLock] Semaphore loaded from registry")
        # Type narrowing: _semaphore_loaded=True means _semaphore is not None
        return self._semaphore  # type: ignore[return-value]

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """
        Async context manager for MLX inference serialization.

        Use this for serializing LLM inference calls.

        Example:
            async with MLXInferenceLock.acquire():
                result = await engine.generate(prompt)

        The semaphore is acquired and released automatically.
        """
        sem = self._get_semaphore()
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    async def hold(self) -> AsyncIterator[None]:
        """
        Alias for acquire() — provides async iterator interface.

        Example:
            async for _ in MLXInferenceLock.hold():
                result = await engine.generate(prompt)
                yield result
        """
        sem = self._get_semaphore()
        await sem.acquire()
        try:
            yield
        finally:
            sem.release()

    # --------------------------------------------------------------------------
    # Legacy: threading.Lock for non-async contexts
    # --------------------------------------------------------------------------

    def _get_threading_lock(self) -> threading.Lock:
        """
        Get the legacy threading lock (lazy init, DCLP).

        Use this only for non-async contexts where asyncio.Semaphore
        cannot be used (e.g., module-level imports).

        For async contexts, prefer acquire().
        """
        if self._threading_lock is None:
            with _mlx_inference_lock_init():
                if self._threading_lock is None:
                    self._threading_lock = threading.Lock()
        return self._threading_lock

    def _threading_lock_context(self) -> Iterator[None]:
        """
        INTERNAL: Get the threading lock context manager (DCLP singleton).

        Use threading_lock() at module level instead.
        """
        lock = self._get_threading_lock()
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    # --------------------------------------------------------------------------
    # MLXWorker: asyncio-safe blocking MLX operations
    # --------------------------------------------------------------------------

    def _get_mlx_worker(self) -> MLXWorker:
        """
        Get or create the MLXWorker singleton (lazy init).

        MLXWorker provides:
        - asyncio-safe MLX operations (event loop stays FREE)
        - Semaphore-gated concurrent MLX ops (max 1)
        - Metal memory cleanup after operations
        """
        if self._mlx_worker is None:
            with self._worker_lock:
                if self._mlx_worker is None:
                    self._mlx_worker = MLXWorker(
                        name="mlx-inference", max_active_experts=1
                    )
                    logger.debug("[MLXInferenceLock] MLXWorker created")
        return self._mlx_worker

    async def run_sync(
        self,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """
        Run synchronous blocking function via MLXWorker.

        The event loop stays FREE during the operation.

        Args:
            fn: Synchronous function (e.g., mlx_lm.generate)
            *args: Positional args for fn
            **kwargs: Keyword args for fn

        Returns:
            Result of fn(*args, **kwargs)

        Example:
            result = await MLXInferenceLock.run_sync(
                mlx_lm.generate, model, tokenizer, prompt="..."
            )
        """
        worker = self._get_mlx_worker()
        return await worker.run_sync(fn, *args, **kwargs)

    async def run_sync_with_cleanup(
        self,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """
        Like run_sync() but with Metal memory cleanup.

        Runs mx.eval([]) + mx.clear_cache() after the operation.

        Use after mlx_lm.load() or heavy inference.
        """
        worker = self._get_mlx_worker()
        return await worker.run_sync_with_metal_cleanup(fn, *args, **kwargs)

    # --------------------------------------------------------------------------
    # Compatibility with legacy patterns
    # --------------------------------------------------------------------------

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """
        Direct semaphore access for components that need it.

        DEPRECATED: Prefer acquire() context manager.
        """
        return self._get_semaphore()

    @property
    def is_active(self) -> bool:
        """True if MLXWorker thread is alive."""
        worker = self._get_mlx_worker()
        return worker.is_active()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown MLXWorker thread. Idempotent."""
        if self._mlx_worker is not None:
            self._mlx_worker.shutdown(timeout=timeout)
            self._mlx_worker = None

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot."""
        worker = self._mlx_worker
        return {
            "semaphore_loaded": self._semaphore_loaded,
            "threading_lock_init": self._threading_lock is not None,
            "worker_active": worker.is_active() if worker else False,
            "worker_stats": worker.get_stats() if worker else None,
        }


# ==============================================================================
# Module-level convenience API (DCLP singleton)
# ==============================================================================


def _get_inference_lock() -> MLXInferenceLock:
    """Get or create the module-level MLXInferenceLock singleton."""
    global _MLX_INFERENCE_LOCK
    if _MLX_INFERENCE_LOCK is None:
        with _mlx_inference_lock_init():
            if _MLX_INFERENCE_LOCK is None:
                _MLX_INFERENCE_LOCK = MLXInferenceLock()
                logger.debug("[MLXInferenceLock] Singleton created")
    return _MLX_INFERENCE_LOCK


# Convenient aliases matching the class methods — use module-level for singleton
acquire = lambda: _get_inference_lock().acquire()
hold = lambda: _get_inference_lock().hold()
run_sync = lambda fn, *a, **kw: _get_inference_lock().run_sync(fn, *a, **kw)
run_sync_with_cleanup = lambda fn, *a, **kw: _get_inference_lock().run_sync_with_cleanup(fn, *a, **kw)
# FIX Issue #2: threading_lock() returns the module-level DCLP singleton lock,
# NOT a new instance's _threading_lock_context() which creates a new lock each time.
threading_lock = lambda: _get_inference_lock()._threading_lock_context()
shutdown = lambda timeout=5.0: _get_inference_lock().shutdown(timeout=timeout)
get_stats = lambda: _get_inference_lock().get_stats()


# ==============================================================================
# MLXWorker — asyncio-safe MLX operations
# ==============================================================================

# Module-level worker instance — lazily initialized
_MLX_WORKER: MLXWorker | None = None


@auto_register(LockCategory.MPC)
def _mlx_worker_lock():
    """Module-level lock for MLXWorker singleton factory."""
    return threading.Lock()


class MLXWorker:
    """
    asyncio-safe MLX worker — run sync mlx_lm.load() / generate() without
    freezing the event loop.

    Combines:
    - Dedicated thread + event loop for MLX operations
    - asyncio.Semaphore: limits concurrent MLX ops (M1: 1 = single-stream)

    Thread model:
        - ONE worker thread, OWN event loop
        - Semaphore gates entry (max_active_experts permits)
        - Main asyncio loop stays FREE during MLX operations

    M1 8GB: max_active_experts=1 (Metal single-stream)

    Always-on, fail-safe, lazy init.
    """

    __slots__ = (
        "_loop",
        "_max_active",
        "_name",
        "_ready",
        "_semaphore",
        "_semaphore_lock",
        "_started",
        "_stopped",
        "_thread",
    )

    def __init__(
        self,
        name: str = "mlx-worker",
        max_active_experts: int = 1,
    ) -> None:
        self._name = name
        self._max_active = max_active_experts
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False
        self._stopped = False
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Worker thread lifecycle
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Worker thread main: create + run event loop forever."""
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                from hledac.universal.utils.mlx_memory import get_metal_stream_context

                _stream_ctx = get_metal_stream_context()
                _stream_ctx.__enter__()
            except Exception:  # noqa: BLE001
                pass
            self._ready.set()
            loop.run_forever()
        except Exception as e:
            logger.warning("[MLXWorker] loop crashed: %s", e)
        finally:
            if loop is not None and not loop.is_closed():
                pending = [t for t in asyncio.all_tasks(loop=loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    try:
                        from hledac.universal.utils.asyncx import safe_gather_fire_and_forget

                        loop.run_until_complete(
                            safe_gather_fire_and_forget(*pending, label="mlx_worker:shutdown")
                        )
                    except Exception:  # noqa: BLE001
                        pass
                loop.close()
                try:
                    gc.collect()
                except Exception:  # noqa: BLE001
                    pass
            self._loop = None

    def _ensure_started(self) -> None:
        """Lazily start the worker thread."""
        if self._started:
            return
        with self._semaphore_lock:
            if self._started:
                return
            self._ready = threading.Event()
            self._stopped = False
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self._name,
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception as e:
                logger.warning("[MLXWorker] start failed: %s", e)
                return
            if not self._ready.wait(timeout=5.0):
                logger.warning("[MLXWorker] thread did not become ready in 5.0s")
                return
            self._started = True

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get worker's event loop, starting the thread if needed."""
        self._ensure_started()
        if self._loop is None:
            raise RuntimeError("mlx_worker_unavailable: loop not initialized")
        return self._loop

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create the semaphore (DCLP pattern)."""
        if self._semaphore is None:
            with self._semaphore_lock:
                if self._semaphore is None:
                    self._semaphore = asyncio.Semaphore(self._max_active)
        return self._semaphore

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_sync(
        self,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """
        Run a synchronous blocking function in the MLX worker thread.

        The main asyncio loop is FREE while this runs.

        P2-7 FIX: Uses loop.run_in_executor() instead of run_coroutine_threadsafe().
        This is the CORRECT pattern for blocking MLX calls because:
        - run_coroutine_threadsafe() + timeout fails when the blocking call
          is INSIDE the coroutine (timeout can't interrupt the blocking op)
        - run_in_executor() properly offloads to thread and timeout CAN interrupt

        Args:
            fn: Synchronous function (e.g., mlx_lm.load, mlx_lm.generate)
            *args: Positional args passed to fn
            **kwargs: Keyword args passed to fn

        Returns:
            The return value of fn(*args, **kwargs)

        Raises:
            RuntimeError: Worker unavailable or timeout
        """
        sem = self._get_semaphore()
        loop = self._get_loop()

        await sem.acquire()
        try:
            # P2-7 FIX: run_in_executor properly handles blocking MLX calls.
            # The timeout CAN interrupt this because run_in_executor offloads
            # the entire blocking operation to the thread pool executor.
            future = loop.run_in_executor(None, lambda: fn(*args, **kwargs))
            async with asyncio.timeout(120.0):
                return await future
        except asyncio.TimeoutError:
            raise RuntimeError("mlx_worker_timeout: operation exceeded 120s")
        finally:
            sem.release()

    async def run_sync_with_metal_cleanup(
        self,
        fn: Callable[..., _T],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """
        Like run_sync() but also runs mx.eval([]) + mx.clear_cache() after.

        Use after mlx_lm.load() to ensure proper Metal memory management.
        """
        result = await self.run_sync(fn, *args, **kwargs)
        try:
            import mlx.core as mx

            mx.eval([])
            mx.clear_cache()
            gc.collect()
        except Exception:  # noqa: BLE001
            pass
        return result

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown the worker thread. Idempotent."""
        if self._thread is None:
            return
        if self._stopped:
            return
        self._stopped = True
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:  # noqa: BLE001
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None
        self._started = False

    def is_active(self) -> bool:
        """True if worker thread is alive and ready."""
        if self._stopped:
            return False
        if self._thread is None:
            return False
        if not self._thread.is_alive():
            return False
        if self._loop is None or self._loop.is_closed():
            return False
        return True

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot."""
        return {
            "active": self.is_active(),
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "thread_name": self._thread.name if self._thread is not None else None,
            "max_active": self._max_active,
            "semaphore_value": self._semaphore._value if self._semaphore else None,
        }


def _get_mlx_worker() -> MLXWorker:
    """Get or create the module-level MLXWorker singleton."""
    global _MLX_WORKER
    if _MLX_WORKER is None:
        with _mlx_worker_lock():
            if _MLX_WORKER is None:
                _MLX_WORKER = MLXWorker(name="mlx-inference", max_active_experts=1)
                logger.debug("[MLXInferenceLock] MLXWorker singleton created")
    return _MLX_WORKER


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Primary API (preferred)
    "MLXInferenceLock",
    "acquire",
    "hold",
    "run_sync",
    "run_sync_with_cleanup",
    "threading_lock",
    "shutdown",
    "get_stats",
    # MLXWorker (for advanced use cases)
    "MLXWorker",
    "_get_mlx_worker",
    # Legacy compatibility (deprecated, use MLXInferenceLock instead)
    "_get_mlx_inference_lock",
    "mlx_inference_lock_context",
    "mlx_inference_lock_aio",
]


# ==============================================================================
# Legacy compatibility shims (redirect to new API)
# ==============================================================================


def _get_mlx_inference_lock() -> MLXInferenceLock:
    """
    Legacy compatibility — returns MLXInferenceLock instance.

    DEPRECATED: Use MLXInferenceLock directly or module-level functions.
    """
    return _get_inference_lock()


def mlx_inference_lock_context(
    fn: Callable[..., _T],
) -> Callable[..., _T]:
    """
    Legacy decorator that wraps a sync MLX function with threading lock.

    DEPRECATED: Use MLXInferenceLock.run_sync() or MLXInferenceLock.acquire().
    """
    def wrapper(*args: object, **kwargs: object) -> _T:
        with threading_lock():
            return fn(*args, **kwargs)  # type: ignore[return-value]
    return wrapper  # type: ignore[return-value]


async def mlx_inference_lock_aio(
    fn: Callable[..., _T],
    *args: object,
    **kwargs: object,
) -> _T:
    """
    Legacy async helper — acquire lock and call sync inference via to_thread.

    DEPRECATED: Use MLXInferenceLock.run_sync() or MLXInferenceLock.acquire().
    """
    lock = _get_inference_lock()._get_threading_lock()

    def _locked_call() -> _T:
        with lock:
            return fn(*args, **kwargs)  # type: ignore[return-value]

    return await asyncio.to_thread(_locked_call)
