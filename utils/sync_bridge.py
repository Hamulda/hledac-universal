"""
sync_bridge — Safe async bridge for sync→async calls (CLI tools, signal handlers).

Replaces ad-hoc asyncio.run() / loop.run_until_complete() patterns that are
M1 crash vectors when called from within a running event loop.

ARCHITECTURE
============
asyncio.run() cannot be called from a running event loop → RuntimeError.
This module provides two safe patterns:

  1. run_sync_async(coro) — Run coroutine from sync code
     - If no running loop: asyncio.run(coro) [new loop]
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
import contextlib
import functools
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Coroutine, TypeVar, cast

T = TypeVar("T")

# Dedicated thread for bridge operations — avoids event loop conflicts
_BRIDGE_THREAD: threading.Thread | None = None
_BRIDGE_LOOP: asyncio.AbstractEventLoop | None = None
_BRIDGE_LOCK = threading.Lock()


def _get_bridge_loop() -> asyncio.AbstractEventLoop:
    """Get or create the dedicated bridge loop."""
    global _BRIDGE_THREAD, _BRIDGE_LOOP

    with _BRIDGE_LOCK:
        if _BRIDGE_LOOP is not None and _BRIDGE_LOOP.is_running():
            return _BRIDGE_LOOP

        if _BRIDGE_THREAD is not None and _BRIDGE_THREAD.is_alive():
            # Bridge thread exists but loop isn't running — close and recreate
            if _BRIDGE_LOOP is not None:
                try:
                    _BRIDGE_LOOP.close()
                except Exception:
                    pass

        # Create new bridge loop in a dedicated thread
        _BRIDGE_LOOP = asyncio.new_event_loop()
        _BRIDGE_THREAD = threading.Thread(
            target=_bridge_loop_runner,
            args=(_BRIDGE_LOOP,),
            daemon=True,
            name="sync-bridge",
        )
        _BRIDGE_THREAD.start()
        return _BRIDGE_LOOP


def _bridge_loop_runner(loop: asyncio.AbstractEventLoop) -> None:
    """Run the bridge event loop (blocking, runs until shutdown)."""
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        try:
            loop.close()
        except Exception:
            pass


def run_sync_async(coro: Awaitable[T]) -> T:
    """
    Safely run a coroutine from synchronous code.

    Handles the tricky cases:
    - No running loop → asyncio.run(coro) [new loop]
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
        # No running loop — safe to use asyncio.run
        return asyncio.run(coro)

    # Running loop detected — marshal to bridge loop
    future = asyncio.run_coroutine_threadsafe(cast(Coroutine, coro), running_loop)
    return future.result()


@functools.cache
def _get_dedicated_thread_pool(max_workers: int = 4) -> ThreadPoolExecutor:
    """Cached dedicated thread pool for sync→async bridge operations."""
    return ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="sync-bridge-pool",
    )


async def to_thread(
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
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


def run_in_executor_safe(
    executor: ThreadPoolExecutor | None,
    func: Callable[..., T],
    *args: Any,
) -> T:
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


class SyncBridgeContext:
    """
    Context manager for safe sync→async bridging with lifecycle management.

    Usage:
        bridge = SyncBridgeContext()
        with bridge:
            result = bridge.run(my_async_function, arg1, arg2)
    """

    def __init__(self, max_workers: int = 4):
        self._pool: ThreadPoolExecutor | None = None
        self._max_workers = max_workers

    def __enter__(self) -> "SyncBridgeContext":
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="sync-bridge-ctx",
        )
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def run(self, coro: Awaitable[T]) -> T:
        """Run coroutine within this context."""
        return run_sync_async(coro)
