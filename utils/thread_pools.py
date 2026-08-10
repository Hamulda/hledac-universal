"""
Worker pools with GCD QoS and core detection for Apple Silicon M1.

MODERN-33 + MODERN-34: P/E Core Affinity System
=================================================
This module provides Python-side thread pools with proper QoS class settings.
For the definitive P/E core detection and workload-aware affinity, see:
  - Rust: rust_extensions/src/topology.rs (cached perflevel0/1)
  - Rust: rust_extensions/src/darwin_affinity.rs (Mach API affinity)
  - Python: utils/execution_optimizer.py (IntelligentResourceAllocator)

Core-to-Workload Mapping:
-------------------------
| Pool | QoS | Cores | Examples |
|------|-----|-------|----------|
| cpu_pool | USER_INITIATED (0x19) | P-cores | Aho-Corasick, deobfuscate, MLX |
| io_pool | UTILITY (0x11) | E-cores | DNS, DuckDB, telemetry |

Sprint 7A additions:
  - PersistentActorExecutor: bridge worker-thread → event-loop
  - ANE_EXECUTOR, DB_EXECUTOR, CPU_EXECUTOR named pools
"""
import asyncio
import concurrent.futures
import ctypes
import logging
import os
import threading
from collections.abc import Callable
from typing import Any
logger = logging.getLogger(__name__)

def _get_core_counts() -> dict:
    """Detekce P/E jader na Apple Silicon s fallbackem."""
    try:
        libc = ctypes.CDLL('/usr/lib/libc.dylib')
        libc.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        libc.sysctlbyname.restype = ctypes.c_int

        def sysctl_int(name: bytes) -> int:
            val = ctypes.c_uint32()
            size = ctypes.c_size_t(4)
            ret = libc.sysctlbyname(name, ctypes.byref(val), ctypes.byref(size), None, 0)
            return max(1, val.value) if ret == 0 else 4
        p = sysctl_int(b'hw.perflevel0.physicalcpu')
        e = sysctl_int(b'hw.perflevel1.physicalcpu')
        return {'p_cores': p, 'e_cores': e}
    except Exception:
        cpu_count = os.cpu_count() or 4
        return {'p_cores': cpu_count // 2, 'e_cores': cpu_count // 2}

def _set_thread_qos(qos_class: int) -> None:
    """Nastavit QoS třídu pro vlákno."""
    try:
        libpthread = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        libpthread.pthread_set_qos_class_self_np(qos_class, 0)
    except Exception:  # noqa: BLE001
        pass

def _set_io_qos() -> None:
    """MODERN-28 FIX: Nastavit UTILITY QoS pro I/O vlákna (E-cores)."""
    _set_thread_qos(0x11)  # QOS_CLASS_UTILITY

def _set_cpu_qos() -> None:
    """MODERN-28 FIX: Nastavit USER_INITIATED QoS pro CPU vlákna (P-cores)."""
    _set_thread_qos(0x19)  # QOS_CLASS_USER_INITIATED
_cores = _get_core_counts()
_io_pool: concurrent.futures.ThreadPoolExecutor | None = None
_cpu_pool: concurrent.futures.ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_ane_pool: Any | None = None
_db_pool: Any | None = None
_STACK_SIZE_BYTES = 2512000

def _apply_stack_size_guard() -> None:
    """Apply stack size limit once at module import. Must be called before any threads exist."""
    try:
        current = threading.stack_size()
        if current == 0:
            threading.stack_size(_STACK_SIZE_BYTES)
            logger.debug('[thread_pools] stack_size guard applied: %d bytes (was system default)', _STACK_SIZE_BYTES)
        elif current > _STACK_SIZE_BYTES:
            threading.stack_size(_STACK_SIZE_BYTES)
            logger.debug('[thread_pools] stack_size reduced from %d to %d bytes', current, _STACK_SIZE_BYTES)
    except Exception as exc:
        logger.debug('[thread_pools] stack_size guard failed: %s', exc)
_apply_stack_size_guard()

def get_core_counts() -> dict:
    """Vrátit počet P/E jader."""
    return _cores.copy()

def get_io_pool() -> concurrent.futures.ThreadPoolExecutor:
    """MODERN-28 FIX: Získat I/O ThreadPoolExecutor (UTILITY QoS, E-cores)."""
    global _io_pool
    if _io_pool is None:
        with _pool_lock:
            if _io_pool is None:
                _io_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_cores['e_cores'], thread_name_prefix='io_worker', initializer=_set_io_qos)
    return _io_pool

def get_cpu_pool() -> concurrent.futures.ThreadPoolExecutor:
    """MODERN-28 FIX: Získat CPU ThreadPoolExecutor (USER_INITIATED QoS, P-cores)."""
    global _cpu_pool
    if _cpu_pool is None:
        with _pool_lock:
            if _cpu_pool is None:
                _cpu_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_cores['p_cores'], thread_name_prefix='cpu_worker', initializer=_set_cpu_qos)
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
    __slots__ = tuple(('_completed_count', '_condition', '_initializer', '_lock', '_loop', '_name', '_orphaned_count', '_queue', '_shutdown_event', '_started', '_submitted_count', '_thread'))

    def __init__(self, name: str, *, initializer: Callable[[], Any] | None=None) -> None:
        """
        Args:
            name:           thread name prefix
            initializer:     callable to run once inside the worker thread (before loop)
        """
        self._name = name
        self._initializer = initializer
        self._queue: list = []
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._started = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event = threading.Event()
        self._submitted_count: int = 0
        self._completed_count: int = 0
        self._orphaned_count: int = 0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the worker thread. Must be called from the event-loop thread."""
        if self._started:
            return
        self._loop = loop
        self._started = True
        self._thread = threading.Thread(target=self._worker_loop, name=f'actor_{self._name}', daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> asyncio.Future:
        """
        Submit a synchronous job to the worker thread.

        Returns an ``asyncio.Future`` that resolves when the worker completes the job.
        The future is NOT tied to the worker lifecycle — it can be awaited independently.
        
        NEW-H5e fix: Rejects new jobs after shutdown is initiated to prevent job drops.
        Jobs submitted after shutdown() would be lost because the worker loop pops
        the sentinel first and exits, dropping any jobs queued after it.
        """
        if not self._started:
            raise RuntimeError('PersistentActorExecutor.start() must be called first')
        # NEW-H5e fix: Reject new jobs after shutdown initiated
        if self._shutdown_event.is_set():
            raise RuntimeError('PersistentActorExecutor.shutdown() already called')
        loop = self._loop
        assert loop is not None
        fut = loop.create_future()
        item = (fn, args, kwargs, fut)
        with self._lock:
            # Double-check after acquiring lock
            if self._shutdown_event.is_set():
                raise RuntimeError('PersistentActorExecutor.shutdown() already called')
            self._queue.append(item)
            self._submitted_count += 1
            self._condition.notify()
        return fut

    def shutdown(self, timeout: float | None=None) -> None:
        """
        Graceful shutdown: send sentinel, wait for thread to finish.

        Idempotent — safe to call multiple times.
        Fail-open: if thread does not join within timeout, returns (no force-kill).
        """
        if not self._started:
            return
        with self._lock:
            self._queue.append(_SENTINEL)
        self._shutdown_event.wait(timeout=timeout)

    @property
    def health(self) -> dict:
        """Return health metadata for monitoring / timeout seams."""
        return {'submitted': self._submitted_count, 'completed': self._completed_count, 'orphaned': self._orphaned_count, 'running': self._thread is not None and self._thread.is_alive()}

    def _worker_loop(self) -> None:
        """Worker thread main loop. Runs initializer once, then processes jobs."""
        try:
            if self._initializer is not None:
                self._initializer()
        except Exception:
            return
        while True:
            item: Any = None
            with self._condition:
                while not self._queue:
                    if not self._condition.wait(timeout=1.0):
                        continue
                item = self._queue.pop()
            if item is _SENTINEL:
                break
            fn, args, kwargs, fut = item
            try:
                result = fn(*args, **kwargs)
                with self._lock:
                    self._completed_count += 1
                loop = self._loop
                if loop is not None and (not loop.is_closed()):
                    loop.call_soon_threadsafe(fut.set_result, result)
            except Exception as exc:
                with self._lock:
                    self._completed_count += 1
                loop = self._loop
                if loop is not None and (not loop.is_closed()):
                    loop.call_soon_threadsafe(fut.set_exception, exc)
        self._shutdown_event.set()

def get_ane_executor() -> PersistentActorExecutor:
    """Return the ANE (Apple Neural Engine) dedicated actor executor."""
    global _ane_pool
    if _ane_pool is None:
        with _pool_lock:
            if _ane_pool is None:
                _ane_pool = PersistentActorExecutor(name='ane', initializer=lambda: _set_thread_qos(25))
    return _ane_pool

def get_db_executor() -> PersistentActorExecutor:
    """Return the database (DuckDB/Kuzu) dedicated actor executor."""
    global _db_pool
    if _db_pool is None:
        with _pool_lock:
            if _db_pool is None:
                _db_pool = PersistentActorExecutor(name='db', initializer=lambda: _set_thread_qos(17))
    return _db_pool

def get_ane_pool() -> Any:
    return get_io_pool()

def get_db_pool() -> Any:
    return get_cpu_pool()