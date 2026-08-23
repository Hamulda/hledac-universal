"""
ISSUE-010: Thread Pool — ThreadPoolExecutor wrapper

Provides bounded ThreadPoolExecutor pools with adaptive sizing based on
M1ResourceGovernor memory pressure state.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from _core.lock_registry import LockCategory, auto_register

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

__all__ = [
    "get_thread_pool",
    "run_in_thread_pool",
    "run_in_thread_pool_async",
]

_DEFAULT_MAX_WORKERS = 3  # M1 8GB: conservative default


def _get_max_workers_from_governor() -> int:
    """Get current max_workers from M1ResourceGovernor state."""
    try:
        import psutil

        from hledac.universal._core.resource_governor import (
            ConcurrencyPreset,
            evaluate_uma_state,
        )

        mem = psutil.virtual_memory()
        system_used_gib = mem.used / 1024**3
        state = evaluate_uma_state(system_used_gib)
        preset = ConcurrencyPreset.from_state(state)
        return preset.max_workers
    except Exception:
        return _DEFAULT_MAX_WORKERS


_thread_pool: ThreadPoolExecutor | None = None


@auto_register(LockCategory.CACHE)
def _thread_pool_lock() -> threading.Lock:
    """Module-level lock for ThreadPoolExecutor singleton factory."""
    return threading.Lock()


def get_thread_pool(max_workers: int | None = None) -> ThreadPoolExecutor:
    """
    Get or create the shared ThreadPoolExecutor.

    Args:
        max_workers: Max threads. None = adaptive from resource governor.

    Returns:
        ThreadPoolExecutor instance.
    """
    global _thread_pool

    if _thread_pool is not None:
        return _thread_pool

    with _thread_pool_lock():
        if _thread_pool is not None:
            return _thread_pool

        workers = max_workers or _get_max_workers_from_governor()
        _thread_pool = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="hledac_thread",
        )

    return _thread_pool


def run_in_thread_pool[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run a function in the shared thread pool synchronously.

    Note: For async contexts, prefer run_in_thread_pool_async() or asyncio.to_thread().

    Args:
        fn: Synchronous callable to run.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Result of fn(*args, **kwargs).

    Raises:
        Exception from fn if it raises.
    """
    pool = get_thread_pool()
    future = pool.submit(fn, *args, **kwargs)
    return future.result()


async def run_in_thread_pool_async[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run a function in the shared thread pool asynchronously.

    Uses asyncio.to_thread() for optimal event loop integration.

    Args:
        fn: Synchronous callable to run.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Result of fn(*args, **kwargs).

    Example:
        result = await run_in_thread_pool_async(read_file, path)
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


@asynccontextmanager
async def thread_pool_context():
    """
    Async context manager for bounded thread pool access with semaphore.

    Provides concurrency control for thread pool operations.

    Example:
        async with thread_pool_context() as pool:
            result = await asyncio.to_thread(pool.submit, heavy_func, arg)
    """
    pool = get_thread_pool()
    semaphore = asyncio.Semaphore(_get_max_workers_from_governor())

    await semaphore.acquire()
    try:
        yield pool
    finally:
        semaphore.release()


def shutdown_thread_pool(wait: bool = True) -> None:
    """
    Shutdown the shared thread pool.

    Args:
        wait: If True, wait for pending work to complete.

    Note:
        Call during sprint winddown to free M1 memory.
    """
    global _thread_pool

    with _thread_pool_lock():
        if _thread_pool is not None:
            _thread_pool.shutdown(wait=wait)
            _thread_pool = None
