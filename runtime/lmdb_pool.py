"""
Dedicated LMDB operation pool — extracted from role_based_pools.py.

LMDB is single-writer but supports concurrent readers. This pool provides:

- ThreadPoolExecutor with 2 workers (1 writer + 1 reader)
- asyncio.Semaphore(2) for bounded concurrency
- NO asyncio.Lock — removed in S1-14 fix: LMDB readers are lock-free
  between each other; run_in_executor is thread-safe; the global write
  lock is enforced by LMDB C library, not Python.
- Timeout support via safe_wait_for
- atexit registration for clean shutdown on process exit

S1-14 Fix Rationale:
  The previous asyncio.Lock serialized ALL submits (read + write). This
  caused a convoy effect: a slow read blocked all writes, and a slow write
  blocked all reads. LMDB's MDB_RDONLY transactions are fully concurrent
  between readers — no Python-side lock is needed. The Semaphore(2) already
  bounds total concurrency to match LMDB writer limit.

This is a focused, single-role module extracted from the monolithic
role_based_pools.py (959 LOC) which had many pools with zero callers.

INVARIANTS (Python 3.14+):
  1. Always-on: no feature flags, lazy-initialized
  2. Bounded: 2 workers max, semaphore-controlled
  3. Fail-safe: returns None on error, never raises
     RES-01: lmdb.MapFullError returns LMDB_MAP_FULL sentinel (not None),
     enabling callers to distinguish map_size exhaustion from generic errors.
     Callers MUST check: ``if result is LMDB_MAP_FULL: trigger_recovery()``
  4. Clean shutdown: atexit.register ensures cleanup on process exit
  5. RES-01 telemetry: map_full events are logged at WARNING and increment
     a module-level counter (``_map_full_count``) for operator visibility.

USAGE:
  from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

  pool = get_lmdb_pool()
  result = await pool.run_lmdb(my_lmdb_func, arg1, arg2, timeout=5.0)
"""

from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, TypeVar

import asyncio

from hledac.universal._core.locks import LockCategory, make_lock
from hledac.universal.utils.asyncx import safe_wait_for
from hledac.universal.runtime._shared.lmdb_pool_helpers import _LMDB_WORKERS

import lmdb  # required project dependency — no lazy import needed here
from _core import aclose

logger = logging.getLogger(__name__)

# Sentinel value returned by run_lmdb when lmdb.MapFullError is raised.
# Distinguishes MAP_FULL from generic errors (None).
# Callers can check: `if result is LMDB_MAP_FULL: ...`
LMDB_MAP_FULL: Any = object()

# RES-01: Telemetry counter for MAP_FULL events — operator visibility
_map_full_count: int = 0

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "get_lmdb_pool",
    "run_lmdb",
    "run_lmdb_sync",
    "_LMDB_WORKERS",
    "LMDB_MAP_FULL",
    "_map_full_count",
]

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Module-level singleton — lazy-initialized on first use
# ---------------------------------------------------------------------------

_pool_lock = make_lock(LockCategory.CACHE, "lmdb_pool._pool_lock")
_pool: "LmdbPool | None" = None


def get_lmdb_pool() -> "LmdbPool":
    """Return the LMDB pool singleton, creating on first call."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = LmdbPool()
        assert _pool is not None
        return _pool


async def run_lmdb[T](
    fn: "Callable[..., T]",
    /,
    *args: Any,
    timeout: float | None = None,
    **kwargs: Any,
) -> T | None:
    """
    Convenience function: run LMDB operation on the shared pool.

    Args:
        fn: Synchronous callable that performs LMDB operation.
            Must NOT hold the write lock across await points.
        timeout: Optional timeout in seconds.
        *args, **kwargs: Arguments passed to fn.

    Returns:
        Result of fn(*args, **kwargs), or None on error/timeout.
    """
    return await get_lmdb_pool().run_lmdb(fn, *args, timeout=timeout, **kwargs)


def run_lmdb_sync[T](
    fn: "Callable[..., T]",
    /,
    *args: Any,
    **kwargs: Any,
) -> T | None:
    """
    Synchronous version of run_lmdb for non-async contexts.

    Note: Executes directly (no thread pool) because it is called from
    synchronous code paths (e.g., shutdown hooks) where the caller already
    owns the thread. Full exception shielding.
    """
    return get_lmdb_pool().run_lmdb_sync(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# LmdbPool — dedicated 2-worker pool for LMDB operations
# ---------------------------------------------------------------------------


class LmdbPool:
    """
    Dedicated thread pool for LMDB operations.

    LMDB is single-writer but supports concurrent readers. Pool has 2 workers
    (matches LMDB reader parallelism). The asyncio.Semaphore(2) bounds total
    concurrency — no asyncio.Lock is needed because LMDB's own write lock
    serializes writers, and MDB_RDONLY transactions are fully concurrent.

    S1-14 Fix: The previous asyncio.Lock was removed because it caused a
    convoy effect where a slow read blocked all writes and vice versa.
    LMDB's C-level locking makes a Python-side submit lock redundant.

    Design rationale:
      - Separated from worker_pool.py because LMDB has specific semantics
        (single-writer, reader parallelism) different from generic I/O
      - Direct dependency on role_based_pools would drag in MLX/DuckDB executors
      - Minimal footprint: ~180 LOC vs 959 LOC monolithic role_based_pools
      - atexit.register ensures cleanup on process exit (like other pools)
    """

    __slots__ = (
        "_executor",
        "_semaphore",
        "_initialized",
        "_init_lock",
        "_atexit_cb",
    )

    def __init__(self) -> None:
        self._initialized = False
        self._init_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._atexit_cb: Callable[[], None] | None = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization (double-checked locking)."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self._initialized = True
            self._executor = ThreadPoolExecutor(
                max_workers=_LMDB_WORKERS,
                thread_name_prefix="hledac-lmdb",
    )
            self._semaphore = asyncio.Semaphore(_LMDB_WORKERS)
            # Register atexit cleanup — must be done in async context would be
            # too late, so register at init time; cleanup function is idempotent.
            if self._atexit_cb is None:
                self._atexit_cb = self._shutdown
                atexit.register(self._atexit_cb)

    async def run_lmdb[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run LMDB operation on the dedicated pool.

        Args:
            fn: Synchronous callable that performs LMDB operation.
                Must NOT hold the write lock across await points.
            timeout: Optional timeout in seconds.

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout.
        """
        self._ensure_initialized()
        assert self._semaphore is not None
        assert self._executor is not None

        async with self._semaphore:
            loop = asyncio.get_running_loop()
            try:
                if timeout is not None:
                    coro = loop.run_in_executor(
                        self._executor, lambda: fn(*args, **kwargs)
    )
                    return await safe_wait_for(
                        coro, timeout=timeout, label="lmdb_pool:run"
    )
                return await loop.run_in_executor(
                    self._executor, lambda: fn(*args, **kwargs)
    )
            except lmdb.MapFullError:
                # RES-01: map_size exhausted. Do NOT return None (generic error).
                # Return LMDB_MAP_FULL sentinel so caller can distinguish this
                # specific condition and decide to retry with a larger map_size.
                global _map_full_count
                _map_full_count += 1
                logger.warning(
                    "[LMDB_MAP_FULL] map_size exhausted in %s ( cumulative=%d ) — "
                    "consider increasing GHOST_LMDB_MAX_SIZE_MB or triggering compaction. "
                    "Error: %s",
                    fn.__name__ if hasattr(fn, "__name__") else str(fn),
                    _map_full_count,
                    "MDB_MAP_FULL",
    )
                return LMDB_MAP_FULL
            except Exception as e:
                logger.debug("[LMDB_POOL] Operation failed in %s: %s", fn.__name__ if hasattr(fn, "__name__") else str(fn), e)
                return None

    def run_lmdb_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Synchronous version of run_lmdb for non-async contexts.

        Note: Executes directly (no thread pool) because it is called from
        synchronous code paths (e.g., shutdown hooks) where the caller already
        owns the thread. Full exception shielding.
        """
        self._ensure_initialized()
        try:
            return fn(*args, **kwargs)
        except lmdb.MapFullError:
            global _map_full_count
            _map_full_count += 1
            logger.warning(
                "[LMDB_MAP_FULL] map_size exhausted in %s (sync, cumulative=%d) — "
                "consider increasing GHOST_LMDB_MAX_SIZE_MB. "
                "Error: %s",
                fn.__name__ if hasattr(fn, "__name__") else str(fn),
                _map_full_count,
                "MDB_MAP_FULL",
    )
            return LMDB_MAP_FULL
        except Exception:
            return None

    def _shutdown(self, *, wait: bool = True) -> None:
        """Internal shutdown — idempotent, safe to call multiple times."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None
        self._semaphore = None
        self._initialized = False

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the pool and optionally wait for pending work."""
        # Unregister atexit so it doesn't fire twice
        if self._atexit_cb is not None:
            atexit.unregister(self._atexit_cb)
            self._atexit_cb = None
        self._shutdown(wait=wait)
