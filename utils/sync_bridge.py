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


async def to_thread_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout: float | None = None,
    **kwargs: Any,
) -> T:
    """
    Run a blocking sync function in a dedicated thread pool with optional timeout.

    Combines the bounded pool safety of `to_thread()` with deadline-aware
    cancellation via `asyncio.timeout()` (Python 3.11+, PEP 657).

    Args:
        func: Synchronous function to run
        *args: Positional arguments passed to func
        timeout: Maximum seconds to wait. None = no timeout (use with caution).
                 On timeout, raises asyncio.TimeoutError.
        **kwargs: Keyword arguments passed to func

    Returns:
        The result of func(*args, **kwargs)

    Raises:
        asyncio.TimeoutError: If timeout is set and operation exceeds deadline

    Example:
        # 5-second timeout on blocking I/O:
        result = await to_thread_with_timeout(blocking_read, fd, buf, timeout=5.0)

        # No timeout (relies on caller to cancel):
        result = await to_thread_with_timeout(heavy_computation, data)

    M1 invariant:
        Uses the same bounded pool as `to_thread()` — never the default
        executor (32 workers on M1).
    """
    pool = _get_dedicated_thread_pool()
    loop = asyncio.get_running_loop()

    if timeout is None:
        return await loop.run_in_executor(pool, lambda: func(*args, **kwargs))

    # Use asyncio.timeout for deadline-aware cancellation (PEP 657, Python 3.11+)
    # This properly cancels the Future when timeout fires, preventing resource leaks
    async with asyncio.timeout(timeout):
        return await loop.run_in_executor(pool, lambda: func(*args, **kwargs))


# ── Rust rayon pool bridge ─────────────────────────────────────────────────────
# Drop-in async wrapper for Rust rayon pool (pool_run.rs).
# Uses asyncio.to_thread(rayon_join) + asyncio.timeout for cancel-aware waits.
#
# Usage: await to_thread_rayon("io", heavy_func, args, timeout=30.0)
# Instead of: await asyncio.to_thread(heavy_func, *args)  [unbounded, no timeout]
#
# Pool types: "cpu" (CPU-bound SIMD), "io" (I/O-bound), "mixed" (adaptive 1-2 threads)


async def _rayon_join_async(handle: int, timeout: float | None = None) -> Any:
    """
    Async wrapper around rayon_join_channel with optional timeout.

    ISSUE 3.1: Uses rayon_join_channel (crossbeam-channel dispatch).
    Runs in a thread via asyncio.to_thread so the event loop remains responsive.
    asyncio.timeout provides deadline-aware cancellation.
    """
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        rayon_join_channel = rust.raw.rayon_join_channel
    except ImportError:
        # Fallback: rayon not compiled — propagate the import error as RuntimeError
        raise RuntimeError(
            "hledac_rust_extensions.rayon_join_channel unavailable (not compiled). "
            "Run: cd rust_extensions && maturin develop"
        ) from None

    loop = asyncio.get_running_loop()

    def _join() -> Any:
        return rayon_join_channel(handle, timeout)

    if timeout is None:
        return await loop.run_in_executor(None, _join)

    # asyncio.timeout fires asyncio.CancelledError when deadline expires.
    # rayon_join_channel raises RuntimeError on timeout (worker still running).
    # We convert it to asyncio.TimeoutError so callers get proper exception.
    try:
        return await loop.run_in_executor(None, _join)
    except asyncio.CancelledError:
        raise asyncio.TimeoutError(f"rayon_join_channel timed out after {timeout}s") from None


async def to_thread_rayon(
    pool_type: str,
    func: Callable[..., T],
    args: tuple[Any, ...],
    *,
    timeout: float | None = None,
) -> T:
    """
    Run a Python callable on the Rust rayon pool via channel dispatch with optional timeout.

    ISSUE 3.1: Uses rayon_submit_channel (crossbeam-channel → existing rayon pool
    dispatcher, žádný thread::spawn per task). ~5μs/task vs ~500μs/task.

    This is the preferred replacement for asyncio.to_thread() when:
    1. The function releases the GIL internally (I/O, or nested asyncio.to_thread)
    2. You need better throughput than Python ThreadPoolExecutor provides
    3. Deadline-aware cancellation is required (asyncio.timeout integration)

    Args:
        pool_type: "cpu" (CPU-bound, 4 P-cores) | "io" (I/O-bound, 2 threads) | "mixed" (adaptive 1-2)
        func: Python callable (must release GIL during execution)
        args: Tuple of arguments to pass to func
        timeout: Maximum seconds. None = no timeout.

    Returns:
        The result of func(*args)

    Raises:
        asyncio.TimeoutError: If timeout exceeded and task was cancelled
        RuntimeError: If rayon pool unavailable (not compiled)

    Example:
        # CPU-heavy work with 30s deadline on rayon CPU pool:
        result = await to_thread_rayon("cpu", simd_batch_process, (data,), timeout=30.0)

        # I/O-bound work on rayon IO pool, no timeout:
        result = await to_thread_rayon("io", blocking_http_fetch, (url,))

    Note:
        Pool type "cpu" uses 4 P-cores; "io" uses 2 threads.
        For Python-native parallelism (no GIL release), use InterpreterPoolExecutor
        instead (py314_executors.py, PEP 756).
    """
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        rayon_submit_channel = rust.raw.rayon_submit_channel
        rayon_join_channel = rust.raw.rayon_join_channel
    except ImportError:
        raise RuntimeError(
            "hledac_rust_extensions.rayon_submit_channel unavailable (not compiled). "
            "Run: cd rust_extensions && maturin develop"
        ) from None

    # Submit to rayon pool via channel — returns opaque handle for join/abort
    handle: int = rayon_submit_channel(pool_type, len(args), func, args)

    try:
        return await _rayon_join_async(handle, timeout=timeout)
    except BaseException:
        # On any exit (timeout, error, cancellation), abort the rayon task
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal.core.rust_backend import rust
            rayon_abort_channel = rust.raw.rayon_abort_channel
            rayon_abort_channel(handle)
        except BaseException:
            pass  # Best-effort abort — don't mask the original exception
        raise

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

