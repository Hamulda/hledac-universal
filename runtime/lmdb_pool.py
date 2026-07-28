"""
Dedicated LMDB operation pool — extracted from role_based_pools.py.

LMDB is single-writer but supports concurrent readers. This pool provides:
- ThreadPoolExecutor with 2 workers (1 writer + 1 reader)
- asyncio.Semaphore(2) for bounded concurrency
- asyncio.Lock for submit serialization
- Timeout support via safe_wait_for

This is a focused, single-role module extracted from the monolithic
role_based_pools.py (959 LOC) which had many pools with zero callers.

INVARIANTS (Python 3.14+):
  1. Always-on: no feature flags, lazy-initialized
  2. Bounded: 2 workers max, semaphore-controlled
  3. Fail-safe: returns None on error, never raises

USAGE:
  from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

  pool = get_lmdb_pool()
  result = await pool.run_lmdb(my_lmdb_func, arg1, arg2, timeout=5.0)
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, TypeVar

from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "get_lmdb_pool",
    "run_lmdb",
    "run_lmdb_sync",
]

T = TypeVar("T")

# ------------------------------------------------------------------|
# Constants                                                        |
# ------------------------------------------------------------------|

_LMDB_WORKERS: int = 2  # 1 writer + 1 reader (LMDB write lock serializes anyway)


# ---------------------------------------------------------------------------
# Module-level singleton — lazy-initialized on first use
# ---------------------------------------------------------------------------

_pool_lock = threading.Lock()
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

    LMDB is single-writer but supports concurrent readers. Pool has 2 workers:
    1 writer + 1 reader. The asyncio.Semaphore bounds concurrency to 2.
    The asyncio.Lock serializes all submits (LMDB write lock is global).

    Design rationale:
      - Separated from worker_pool.py because LMDB has specific semantics
        (single-writer, reader parallelism) different from generic I/O
      - Direct dependency on role_based_pools would drag in MLX/DuckDB executors
      - Minimal footprint: ~150 LOC vs 959 LOC monolithic role_based_pools
    """

    __slots__ = (
        "_executor",
        "_semaphore",
        "_lock",
        "_initialized",
        "_init_lock",
    )

    def __init__(self) -> None:
        self._initialized = False
        self._init_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None

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
            # Lock created in async context; use ensure_future pattern for safety
            self._lock = asyncio.Lock()

    async def _get_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._lock is not None
        return self._lock

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
            async with await self._get_lock():
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
                except Exception:
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
        except Exception:
            return None

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the pool and optionally wait for pending work."""
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None
        self._initialized = False
