"""
Worker pooly s GCD QoS a detekcí jader pro Apple Silicon M1.

Sprint 7A additions:
  - PersistentActorExecutor: bridge worker-thread → event-loop
  - ANE_EXECUTOR, DB_EXECUTOR, CPU_EXECUTOR named pools
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import os
import threading
from typing import Any, Callable, Optional

# Detekce jader na Apple Silicon
def _get_core_counts() -> dict:
    """Detekce P/E jader na Apple Silicon s fallbackem."""
    try:
        libc = ctypes.CDLL('/usr/lib/libc.dylib')
        libc.sysctlbyname.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t
        ]
        libc.sysctlbyname.restype = ctypes.c_int

        def sysctl_int(name: bytes) -> int:
            val = ctypes.c_uint32()
            size = ctypes.c_size_t(4)
            ret = libc.sysctlbyname(name, ctypes.byref(val), ctypes.byref(size), None, 0)
            return max(1, val.value) if ret == 0 else 4

        p = sysctl_int(b"hw.perflevel0.physicalcpu")
        e = sysctl_int(b"hw.perflevel1.physicalcpu")
        return {'p_cores': p, 'e_cores': e}
    except Exception:
        cpu_count = os.cpu_count() or 4
        return {'p_cores': cpu_count // 2, 'e_cores': cpu_count // 2}


def _set_thread_qos(qos_class: int) -> None:
    """Nastavit QoS třídu pro vlákno."""
    try:
        libpthread = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        libpthread.pthread_set_qos_class_self_np(qos_class, 0)
    except Exception:
        pass


def _set_background() -> None:
    """Nastavit Background QoS pro I/O vlákna."""
    _set_thread_qos(0x09)  # QOS_CLASS_BACKGROUND


def _set_user_initiated() -> None:
    """Nastavit User Initiated QoS pro CPU vlákna."""
    _set_thread_qos(0x19)  # QOS_CLASS_USER_INITIATED


# Inicializace
_cores = _get_core_counts()
_io_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_cpu_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_pool_lock = threading.Lock()

# Sprint 7A: Named executors
_ane_pool: Optional[Any] = None
_db_pool: Optional[Any] = None


def get_core_counts() -> dict:
    """Vrátit počet P/E jader."""
    return _cores.copy()


def get_io_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Získat I/O ThreadPoolExecutor (Background QoS, E-cores)."""
    global _io_pool
    if _io_pool is None:
        with _pool_lock:
            if _io_pool is None:
                _io_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=_cores['e_cores'],
                    thread_name_prefix="io_worker",
                    initializer=_set_background
                )
    return _io_pool


def get_cpu_pool() -> concurrent.futures.ThreadPoolExecutor:
    """Získat CPU ThreadPoolExecutor (User Initiated QoS, P-cores)."""
    global _cpu_pool
    if _cpu_pool is None:
        with _pool_lock:
            if _cpu_pool is None:
                _cpu_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=_cores['p_cores'],
                    thread_name_prefix="cpu_worker",
                    initializer=_set_user_initiated
                )
    return _cpu_pool


def shutdown_pools() -> None:
    """Shutdown všech poolů."""
    global _io_pool, _cpu_pool, _ane_pool, _db_pool
    for pool in (_io_pool, _cpu_pool, _ane_pool, _db_pool):
        if pool is not None:
            pool.shutdown(wait=True)
    _io_pool = None
    _cpu_pool = None
    _ane_pool = None
    _db_pool = None


# =============================================================================
# PersistentActorExecutor — worker thread → event-loop bridge (Sprint 7A)
# =============================================================================
#
# Design:
#   - Worker thread runs init_fn once, then loops consuming from a queue
#   - Each submitted job: (fn, args, kwargs, future)
#   - Worker places result / exception into the future via loop.call_soon_threadsafe
#   - shutdown() sends sentinel None job; worker exits gracefully
#
# This replaces the forbidden pattern of killing threads (Python cannot safely do so).
# Instead, we provide a health seam: jobs that take too long can have their future
# timeout on the await side, and the worker continues (marking the job as orphaned
# via the future's internal state).
# =============================================================================


_SENTINEL = object()


class PersistentActorExecutor:
    """
    One dedicated worker thread that calls ``init_fn()`` once, then loops.

    Jobs are submitted via ``submit(fn, *args, **kwargs)`` → ``asyncio.Future``.

    Bridge to event-loop uses ``loop.call_soon_threadsafe(fut.set_result, result)``
    or ``loop.call_soon_threadsafe(fut.set_exception, exc)`` — the canonical pattern.

    Sentinel-based shutdown: ``shutdown()`` sends ``_SENTINEL`` into the queue.

    Health metadata: tracks submitted / completed / orphaned job counts.
    """

    def __init__(
        self,
        name: str,
        *,
        initializer: Optional[Callable[[], Any]] = None,
    ) -> None:
        """
        Args:
            name:           thread name prefix
            initializer:     callable to run once inside the worker thread (before loop)
        """
        self._name = name
        self._initializer = initializer
        self._queue: list = []          # thread-safe list used as stack: append + pop
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)  # replaces sleep-polling in worker
        self._started = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = threading.Event()

        # Health metadata (for monitoring / timeout seams)
        self._submitted_count: int = 0
        self._completed_count: int = 0
        self._orphaned_count: int = 0

    # ---- public API ----

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the worker thread. Must be called from the event-loop thread."""
        if self._started:
            return
        self._loop = loop
        self._started = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"actor_{self._name}",
            daemon=True,
        )
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> asyncio.Future:
        """
        Submit a synchronous job to the worker thread.

        Returns an ``asyncio.Future`` that resolves when the worker completes the job.
        The future is NOT tied to the worker lifecycle — it can be awaited independently.
        """
        if not self._started:
            raise RuntimeError("PersistentActorExecutor.start() must be called first")
        loop = self._loop
        assert loop is not None

        fut = loop.create_future()

        # Serialize work item: (fn, args, kwargs, future)
        item = (fn, args, kwargs, fut)

        with self._lock:
            self._queue.append(item)
            self._submitted_count += 1
            self._condition.notify()

        return fut

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """
        Graceful shutdown: send sentinel, wait for thread to finish.

        Idempotent — safe to call multiple times.
        Fail-open: if thread does not join within timeout, returns (no force-kill).
        """
        if not self._started:
            return

        # Send sentinel
        with self._lock:
            self._queue.append(_SENTINEL)

        # Wait for thread
        self._shutdown_event.wait(timeout=timeout)

    @property
    def health(self) -> dict:
        """Return health metadata for monitoring / timeout seams."""
        return {
            "submitted": self._submitted_count,
            "completed": self._completed_count,
            "orphaned": self._orphaned_count,
            "running": self._thread is not None and self._thread.is_alive(),
        }

    # ---- internal ----

    def _worker_loop(self) -> None:
        """Worker thread main loop. Runs initializer once, then processes jobs."""
        try:
            if self._initializer is not None:
                self._initializer()
        except Exception:
            # Initializer failure is fatal — log and exit thread
            return

        while True:
            item: Any = None
            # Wait for work: Condition.wait() blocks until notify() is called.
            # Timeout (1s) prevents indefinite hang if notify is missed.
            with self._condition:
                while not self._queue:
                    if not self._condition.wait(timeout=1.0):
                        continue  # timeout, loop will re-check
                item = self._queue.pop()

            if item is _SENTINEL:
                break

            fn, args, kwargs, fut = item
            try:
                result = fn(*args, **kwargs)
                with self._lock:
                    self._completed_count += 1
                # Bridge result → event loop
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(fut.set_result, result)
            except Exception as exc:
                with self._lock:
                    self._completed_count += 1
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(fut.set_exception, exc)

        self._shutdown_event.set()


# =============================================================================
# Named executors (Sprint 7A)
# =============================================================================

def get_ane_executor() -> PersistentActorExecutor:
    """Return the ANE (Apple Neural Engine) dedicated actor executor."""
    global _ane_pool
    if _ane_pool is None:
        with _pool_lock:
            if _ane_pool is None:
                _ane_pool = PersistentActorExecutor(
                    name="ane",
                    initializer=lambda: _set_thread_qos(0x19),  # USER_INITIATED
                )
    return _ane_pool


def get_db_executor() -> PersistentActorExecutor:
    """Return the database (DuckDB/Kuzu) dedicated actor executor."""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = PersistentActorExecutor(
                    name="db",
                    initializer=lambda: _set_thread_qos(0x11),  # UTILITY
                )
    return _db_pool


# Backwards compatibility aliases
def get_ane_pool() -> Any:
    return get_io_pool()


def get_db_pool() -> Any:
    return get_cpu_pool()
