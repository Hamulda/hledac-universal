"""Shared bounded worker pool — replaces scattered asyncio.to_thread() usage.

Replaces bare `asyncio.to_thread()` calls throughout the codebase with a
single, bounded, instrumentation-friendly executor.

Thread budget on M1 8GB:
  - Rayon cpu_pool:    4 threads (Rust MLX inference)
  - Rayon io_pool:     2 threads (Rust async I/O)
  - asyncio event loop: 1 thread
  - Shared pool:        4 threads (P-cores, this module)
  ─────────────────────────────────────────────────────
  Total:               11 threads (fits 8-core M1 without thrashing)

This leaves 1 P-core headroom for OS scheduler jitter.
"""
from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from typing import Callable

__all__ = [
    "SharedWorkerPool",
    "get_shared_pool",
    "cpu_bound",
    "io_bound",
]

T = TypeVar("T", default=object)

# Module-level singleton — initialised on first use (lazy).
_pool: "SharedWorkerPool | None" = None
_pool_lock = threading.Lock()


def get_shared_pool() -> "SharedWorkerPool":
    """Return the shared pool singleton, creating it on first call."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = SharedWorkerPool()
        assert _pool is not None
        return _pool


class SharedWorkerPool:
    """Singleton bounded worker pool for CPU/IO-bound sync work.

    Replaces asyncio.to_thread() calls that would otherwise hit the
    Python default executor (12 workers on M1 = unnecessary overhead).

    Sizing logic:
      M1 8GB (8 logical CPUs):
        - 4 P-cores reserved for Rayon cpu_pool  → 4 workers
        - 2 E-cores for Rayon io_pool            → shared pool gets 4
        - 1 thread for asyncio event loop
        - 1 thread headroom for OS scheduler
      Total: 4 workers, bounded.

    This class is safe to use from multiple asyncio tasks simultaneously
    because it wraps a ThreadPoolExecutor behind run_in_executor().
    """

    __slots__ = ("_executor", "_max_workers", "_active_count", "_lock")

    def __init__(self, max_workers: int | None = None) -> None:
        cpu_count = os.cpu_count() or 4
        if max_workers is None:
            # M1 8GB: 4 workers — fits alongside Rayon + event loop.
            # Cap at 6 to preserve headroom; floor at 2 for safety.
            max_workers = max(2, min(6, cpu_count - 4))
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hledac-shared",
        )
        self._active_count = 0
        self._lock = threading.Lock()

    async def run(self, func: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T:
        """Run a blocking callable on the shared executor, returning a Future.

        This is the preferred replacement for `asyncio.to_thread()`.
        Uses loop.run_in_executor() so the call is awaitable and bounded
        by max_workers.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            self._active_count += 1
        try:
            return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))
        finally:
            with self._lock:
                self._active_count -= 1

    @property
    def active_count(self) -> int:
        """Number of tasks currently running on the pool."""
        return self._active_count

    @property
    def max_workers(self) -> int:
        """Max worker threads in the pool."""
        return self._max_workers

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the pool. Call on app exit."""
        self._executor.shutdown(wait=wait)


# ------------------------------------------------------------------
# Public helpers — preferred entry points
# ------------------------------------------------------------------

async def cpu_bound(func: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T:
    """Await a CPU-bound synchronous function on the shared pool.

    Use instead of `await asyncio.to_thread(func, *args)` for any
    compute-intensive Python work (hashing, parsing, regex, etc.).
    For I/O-bound work (network, disk) prefer io_bound().
    """
    return await get_shared_pool().run(func, *args, **kwargs)


async def io_bound(func: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T:
    """Await an I/O-bound synchronous function on the shared pool.

    Use instead of `await asyncio.to_thread(func, *args)` for any
    blocking I/O (DNS, WHOIS, SSL handshake, SQLite, file I/O).
    """
    return await get_shared_pool().run(func, *args, **kwargs)
