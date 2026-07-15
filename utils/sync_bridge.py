"""
sync_bridge — Safe async bridge for sync→async calls (CLI tools, signal handlers).

Replaces ad-hoc asyncio.run() / loop.run_until_complete() patterns that are
M1 crash vectors when called from within a running event loop.

ARCHITECTURE
============
asyncio.run() cannot be called from a running event loop → RuntimeError.
This module provides two safe patterns:

  1. run_sync_async(coro) — Run coroutine from sync code
     - If no running loop: asyncio.Runner().run(coro) [new loop, PEP 654]
     - If running loop: asyncio.run_coroutine_threadsafe(coro, running_loop).result()

  2. to_thread(sync_fn, *args) — Run sync function in dedicated thread
     - For truly sync functions that need to be called from async code
     - Uses a bounded cached thread pool, not the default executor

M1 CRASH VECTORS (CLAUDE.md invariants)
========================================
• asyncio.run() inside ThreadPoolExecutor → M1 crash
• asyncio.run() inside a running event loop → RuntimeError
• loop.run_until_complete() on running loop → RuntimeError (Python 3.10+)
• loop.run_in_executor(None, ...) → unbounded ThreadPoolExecutor (32 workers on M1)

SOLUTION: Domain-specific bounded executors (see domain_executors.py)
"""
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Coroutine, TypeVar, cast
T = TypeVar('T')

def run_sync_async(coro: Awaitable[T]) -> T:
    """
    Safely run a coroutine from synchronous code.

    Handles the tricky cases:
    - No running loop → asyncio.Runner().run(coro) [PEP 654, Python 3.11+]
    - Running loop → asyncio.run_coroutine_threadsafe(coro, running_loop).result()

    Args:
        coro: An awaitable (coroutine, task, or future)

    Returns:
        The result of the coroutine

    Raises:
        RuntimeError: If called from within an async context that cannot be bridged
        Exception: Any exception from the coroutine

    Example:
        # In sync function called from CLI or signal handler:
        result = run_sync_async(my_async_function(arg1, arg2))

        # In async function (should await directly instead):
        result = await my_async_function(arg1, arg2)
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — use asyncio.Runner (PEP 654, Python 3.11+).
        # Runner manages loop lifecycle internally and ensures cleanup.
        with asyncio.Runner() as runner:
            return runner.run(coro)
    # Running loop: delegate to it via threadsafe submission.
    future = asyncio.run_coroutine_threadsafe(cast(Coroutine, coro), running_loop)
    return future.result()

@functools.cache
def _get_dedicated_thread_pool(max_workers: int=4) -> ThreadPoolExecutor:
    """Cached dedicated thread pool for sync→async bridge operations."""
    return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='sync-bridge-pool')

async def to_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a blocking sync function in a dedicated thread pool.

    Preferred over `loop.run_in_executor(None, func)` because:
    - Uses a bounded pool (default: 4 workers) instead of unbounded (32 on M1)
    - Avoids context-switch thrashing on M1 8GB

    Args:
        func: Synchronous function to run
        *args, **kwargs: Arguments to pass to func

    Returns:
        The result of func(*args, **kwargs)

    Note:
        For CPU-bound work on Python 3.14+, consider InterpreterPoolExecutor
        from concurrent.futures (PEP 756) — see py314_executors.py
    """
    pool = _get_dedicated_thread_pool()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, lambda: func(*args, **kwargs))

def run_in_executor_safe(executor: ThreadPoolExecutor | None, func: Callable[..., T], *args: Any) -> T:
    """
    Synchronous version of loop.run_in_executor for use in sync contexts.

    When you need to run an executor call from sync code without blocking
    the event loop, use this. Prefer async `to_thread()` when possible.

    Args:
        executor: ThreadPoolExecutor or None (uses default)
        func: Function to execute
        *args: Arguments

    Returns:
        The result of func(*args)
    """
    if executor is None:
        return func(*args)
    future = executor.submit(func, *args)
    return future.result()

