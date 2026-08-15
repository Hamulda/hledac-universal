"""
core/mlx_inference_lock.py — MLX inference lock + async-safe MLXWorker.

Issue M-04 (MoE synchronně volá mlx_lm.load() a generate() uvnitř async funkcí):

    brain/moe_router.py _load_expert a _generate_with_expert volají
    synchroní mlx_lm.load() / mlx_lm.generate() přímo uvnitř async def.
    Event loop je frozen 1-60s — žádná jiná coroutine nemůže běžet.

ŘEŠENÍ — MLXWorker (DCLP, always-on, fail-safe):
    Kombinuje MLXWorkerThread (vlastní vlákno + event loop) s
    asyncio.Semaphore(max_active_experts) pro M1 Metal single-stream throttling.

    Všechny MLX operace (load, generate) jdou přes:
        await worker.run_sync(mlx_lm.load, model_path)
        await worker.run_sync(mlx_lm.generate, model, tokenizer, prompt=..., ...)

    Event loop je FREE během celé operace — jiné coroutines běží.

M1 8GB constraints:
    - max_active_experts=1 (Metal single-stream, only one MLX op at a time)
    - Lazy worker start (first use, not at import) — ISSUE-014 safe
    - mx.eval([]) + mx.clear_cache() po load() pro správu Metal paměti

PYTHON 3.14 KOMPATIBILITA:
    - Žádné asyncio.Lock/Semaphore() při modul importu (lazy init)
    - DCLP pattern pro všechny async primitivy

Author: M-04 (F350M-R)
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar
from _core._util import aclose

if TYPE_CHECKING:
    from collections.abc import Coroutine

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# ==============================================================================
# MLXWorker — asyncio-safe MLX operations (ISSUE-014 safe, lazy init)
# ==============================================================================

# Module-level worker instance — lazily initialized (ISSUE-014 pattern)
_MLX_WORKER: MLXWorker | None = None
_MLX_WORKER_LOCK: threading.Lock = threading.Lock()


class MLXWorker:
    """
    asyncio-safe MLX worker — run sync mlx_lm.load() / generate() without
    freezing the event loop.

    Combines:
    - MLXWorkerThread: dedicated thread + event loop for MLX operations
    - asyncio.Semaphore: limits concurrent MLX ops (M1: 1 = single-stream)

    Thread model:
        - ONE worker thread, OWN event loop (from MLXWorkerThread)
        - Semaphore gates entry (max_active_experts permits)
        - Main asyncio loop stays FREE during MLX operations

    M1 8GB: max_active_experts=1 (Metal single-stream), mx.eval([]) +
    mx.clear_cache() after load(), bounded semaphore acquire/release.

    Always-on, fail-safe, lazy init (ISSUE-014).
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
        """
        Initialize MLXWorker. Does NOT start the thread (lazy start).

        Args:
            name: Thread name
            max_active_experts: Maximum concurrent MLX operations.
                               M1 Metal single-stream: must be 1.
        """
        self._name = name
        self._max_active = max_active_experts
        # ISSUE-014: None at import, created in _ensure_started()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False
        self._stopped = False
        # Semaphore created lazily in _get_semaphore (ISSUE-014)
        self._semaphore: asyncio.Semaphore | None = None
        # DCLP lock for thread-safe semaphore creation
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
            # F300S-FIX: Initialize Metal stream in worker thread
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
        """Lazily start the worker thread (ISSUE-014 safe)."""
        if self._started:
            return
        with threading.Lock():
            # DCLP: re-check after acquiring lock (another thread may have started)
            if self._started:
                return  # second check — another thread won the race
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
        """Lazily create the semaphore after worker is running (ISSUE-014 safe).

        Thread-safe DCLP pattern: check without lock, then acquire lock
        and re-check before creating.
        """
        if self._semaphore is None:
            with self._semaphore_lock:
                # DCLP: re-check after acquiring lock
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

        The main asyncio loop is FREE while this runs — other coroutines
        can execute normally.

        Args:
            fn:    Synchronous function to run
                  (e.g. mlx_lm.load, mlx_lm.generate)
            *args: Positional args passed to fn
            **kwargs: Keyword args passed to fn

        Returns:
            The return value of fn(*args, **kwargs)

        Raises:
            RuntimeError: Worker unavailable
            asyncio.TimeoutError: Operation exceeded 120s
        """
        sem = self._get_semaphore()
        loop = self._get_loop()

        await sem.acquire()
        busy = True

        async def _call() -> _T:
            return fn(*args, **kwargs)

        cf_future: Any = None
        try:
            cf_future = asyncio.run_coroutine_threadsafe(_call(), loop)
            bridge = asyncio.wrap_future(cf_future)
            async with asyncio.timeout(120.0):
                return await bridge
        except asyncio.TimeoutError:
            if cf_future is not None:
                try:
                    cf_future.cancel()
                except Exception:  # noqa: BLE001
                    pass
            raise RuntimeError("mlx_worker_timeout: operation exceeded 120s")
        finally:
            if busy:
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

        Args:
            fn:    Synchronous function to run
            *args: Positional args for fn
            **kwargs: Keyword args for fn

        Returns:
            Result of fn(*args, **kwargs)
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


# ==============================================================================
# Module-level convenience API (DCLP singleton)
# ==============================================================================

def _get_mlx_worker() -> MLXWorker:
    """Get or create the module-level MLXWorker singleton (DCLP pattern)."""
    global _MLX_WORKER
    if _MLX_WORKER is None:
        with _MLX_WORKER_LOCK:
            if _MLX_WORKER is None:
                _MLX_WORKER = MLXWorker(name="mlx-inference", max_active_experts=1)
                logger.debug("[MLXInferenceLock] MLXWorker singleton created")
    return _MLX_WORKER


async def run_in_mlx_worker(
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> _T:
    """
    Run a sync blocking function in the MLX worker without freezing the event loop.

    Convenience wrapper around MLXWorker.run_sync().

    Args:
        fn:    Synchronous function (e.g. mlx_lm.load, mlx_lm.generate)
        *args: Positional args for fn
        **kwargs: Keyword args for fn

    Returns:
        Result of fn(*args, **kwargs)

    Example:
        model, tokenizer = await run_in_mlx_worker(mlx_lm.load, model_path)
        response = await run_in_mlx_worker(
            mlx_lm.generate, model, tokenizer, prompt=..., temp=0.3
        )
    """
    worker = _get_mlx_worker()
    return await worker.run_sync(fn, *args, **kwargs)


# ==============================================================================
# LEGACY exports (threading.Lock for non-async contexts)
# ==============================================================================

_MLX_INFERENCE_LOCK: threading.Lock | None = None
_MLX_INFERENCE_THREAD_LOCK: threading.Lock = threading.Lock()


def _get_mlx_inference_lock() -> threading.Lock:
    """
    Return the legacy threading lock for MLX inference (DCLP pattern).
    Use run_in_mlx_worker() for async contexts.
    """
    global _MLX_INFERENCE_LOCK
    if _MLX_INFERENCE_LOCK is None:
        with _MLX_INFERENCE_THREAD_LOCK:
            if _MLX_INFERENCE_LOCK is None:
                _MLX_INFERENCE_LOCK = threading.Lock()
    return _MLX_INFERENCE_LOCK


def mlx_inference_lock_context(
    fn: Callable[..., _T],
) -> Callable[..., _T]:
    """
    Legacy decorator that wraps a sync MLX function with a threading lock.
    For async contexts, use run_in_mlx_worker() instead.
    """
    def wrapper(*args: object, **kwargs: object) -> _T:
        lock = _get_mlx_inference_lock()
        with lock:
            return fn(*args, **kwargs)  # type: ignore[return-value]
    return wrapper  # type: ignore[return-value]


async def mlx_inference_lock_aio(
    fn: Callable[..., _T],
    *args: object,
    **kwargs: object,
) -> _T:
    """
    Legacy async helper — acquire lock and call sync inference via to_thread.
    For new code, use run_in_mlx_worker() instead.
    """
    lock = _get_mlx_inference_lock()

    def _locked_call() -> _T:
        with lock:
            return fn(*args, **kwargs)  # type: ignore[return-value]

    return await asyncio.to_thread(_locked_call)


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # New MLXWorker API (preferred for async contexts)
    "MLXWorker",
    "run_in_mlx_worker",
    "_get_mlx_worker",
    # Legacy exports
    "_get_mlx_inference_lock",
    "mlx_inference_lock_context",
    "mlx_inference_lock_aio",
]
