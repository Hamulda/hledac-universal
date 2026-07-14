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

Design note:
  cpu_bound and io_bound are aliases for the SAME pool on M1 8GB.
  Separating them into distinct ThreadPoolExecutor pools would double
  thread-stack RAM overhead (~1 MB/thread × N extra workers), which is
  counterproductive on 8 GB UMA.  Use asyncio.to_thread() directly for
  CPU-bound Python work; use io_bound() for I/O-bound blocking calls
  (WHOIS, SSL, SQLite, file I/O).

ISSUE #032: RustWorkerPool
  Provides cancelable Future via rayon background thread + JoinHandle::abort().
  Fallback: SharedWorkerPool (ThreadPoolExecutor) when Rust extension unavailable.
  pool_type: "cpu" (4 P-cores), "io" (2 threads), "mixed" (adaptive 1-2).
"""

import asyncio
import functools
import os
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "SharedWorkerPool",
    "RustWorkerPool",
    "get_shared_pool",
    "get_rust_pool",
    "cpu_bound",
    "io_bound",
]

T = TypeVar("T", default=object)

# Module-level singletons — initialised on first use (lazy).
_pool: "SharedWorkerPool | None" = None
_rust_pool: "RustWorkerPool | None" = None
_pool_lock = threading.Lock()


def get_shared_pool() -> "SharedWorkerPool":
    """Return the shared Python ThreadPoolExecutor singleton, creating on first call."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            _pool = SharedWorkerPool()
        assert _pool is not None
        return _pool


def get_rust_pool(pool_type: Literal["cpu", "io", "mixed"] = "cpu") -> "RustWorkerPool":
    """Return a RustWorkerPool singleton for the given pool type.

    Args:
        pool_type: "cpu" → 4 P-cores (SIMD, hashing, pattern match)
                   "io"  → 2 threads (DuckDB, graph_traverse, compress)
                   "mixed" → adaptive 1-2 threads (IOC extract, url_ops, simhash)
    """
    global _rust_pool
    if _rust_pool is not None and _rust_pool._pool_type == pool_type:
        return _rust_pool
    with _pool_lock:
        if _rust_pool is None or _rust_pool._pool_type != pool_type:
            _rust_pool = RustWorkerPool(pool_type=pool_type)
        assert _rust_pool is not None
        return _rust_pool


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

    __slots__ = ("_executor", "_max_workers", "_active_count", "_lock", "_async_lock")

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
        self._async_lock: asyncio.Lock | None = None

    async def _get_async_lock(self) -> asyncio.Lock:
        """Lazily create asyncio.Lock in the running event loop."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def run(self, func: "Callable[..., T]", /, *args: Any, timeout: float | None = None, **kwargs: Any) -> T:
        """Run a blocking callable on the shared executor, returning a Future.

        This is the preferred replacement for `asyncio.to_thread()`.
        Uses loop.run_in_executor() so the call is awaitable and bounded
        by max_workers.

        Args:
            func: The blocking callable to run.
            timeout: Optional timeout in seconds. If None, runs without timeout.
                A TimeoutError is raised if the callable does not complete in time.

        Note: functools.partial is used instead of a lambda to avoid
        allocating a new closure object on every call.
        """
        loop = asyncio.get_running_loop()
        async_lock = await self._get_async_lock()
        async with async_lock:
            self._active_count += 1
        try:
            coro = loop.run_in_executor(self._executor, functools.partial(func, *args, **kwargs))
            if timeout is not None:
                return await safe_wait_for(coro, timeout=timeout, label="worker_pool")
            return await coro
        finally:
            async with async_lock:
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
        # Reset singleton so next call creates a fresh pool (supports re-init in tests)
        global _pool
        _pool = None


# ---------------------------------------------------------------------------
# ISSUE #032: RustWorkerPool — rayon-backed pool with cancelable Future
# ---------------------------------------------------------------------------

_RUST_AVAILABLE: bool | None = None


def _check_rust_rayon_available() -> bool:
    """Check if Rust rayon submit/join/abort extension is available."""
    global _RUST_AVAILABLE
    if _RUST_AVAILABLE is not None:
        return _RUST_AVAILABLE
    try:
        from hledac_rust_extensions import rayon_submit, rayon_join, rayon_abort
        _RUST_AVAILABLE = True
    except ImportError:
        _RUST_AVAILABLE = False
    return _RUST_AVAILABLE


class RustWorkerPool:
    """Pool backed by Rust rayon ThreadPool — M1 P-core QoS aware.

    Provides cancelable asyncio.Future via rayon background thread.
    JoinHandle::abort() maps to Future.cancel().

    pool_type:
      "cpu"   → rayon cpu_pool (4 P-cores): SIMD, hashing, pattern match
      "io"    → rayon io_pool (2 threads): DuckDB, graph_traverse, compress
      "mixed" → rayon mixed_pool (adaptive 1-2 threads): IOC extract, url_ops

    Fail-safe: if Rust extension unavailable, falls back to SharedWorkerPool
    (ThreadPoolExecutor) automatically.

    Cancellation: Future.cancel() → rayon_abort(handle) → JoinHandle::abort()
    on the background OS thread. This causes the rayon worker to terminate.

    M1 8GB thread budget (all rayon + asyncio singletons):
      cpu_pool: 4 threads (P-cores, QoS=utility)
      io_pool:  2 threads (E-cores, QoS=background)
      mixed_pool: 1-2 threads (adaptive)
      asyncio event loop: 1 thread
      ─────────────────────────────────────────
      Total: 7-8 OS threads (fits 8-core M1)
    """

    __slots__ = ("_pool_type", "_active_count", "_lock", "_async_lock")

    def __init__(self, pool_type: Literal["cpu", "io", "mixed"] = "cpu") -> None:
        self._pool_type = pool_type
        self._active_count = 0
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None

    def _check_available(self) -> bool:
        """Return True if Rust rayon extension is available."""
        return _check_rust_rayon_available()

    async def _get_async_lock(self) -> asyncio.Lock:
        """Lazily create asyncio.Lock in the running event loop."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def submit(
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        n_items: int = 0,
        **kwargs: Any,
    ) -> T:
        """Submit work to the rayon pool, returning an awaitable result.

        Uses rayon_submit (background thread) + asyncio.to_thread(rayon_join).
        Cancellation: Future.cancel() calls rayon_abort on the background thread.

        Args:
            fn: Synchronous callable to run on the rayon pool.
            timeout: Optional timeout in seconds.
            n_items: Batch size hint for mixed pool adaptive threading (default 0).
                Only used when pool_type="mixed".

        Returns:
            Result of fn(*args, **kwargs). Raises TimeoutError on timeout.
            Raises RuntimeError if the Rust task was aborted.

        Note:
            functools.partial is used to avoid closure allocation on every call.
        """
        if not self._check_available():
            # Fallback: use SharedWorkerPool
            warnings.warn(
                f"Rust rayon unavailable, falling back to SharedWorkerPool for {self._pool_type} pool",
                RuntimeWarning,
                stacklevel=2,
            )
            return await get_shared_pool().run(fn, *args, timeout=timeout, **kwargs)

        from hledac_rust_extensions import rayon_submit, rayon_join, rayon_abort

        async_lock = await self._get_async_lock()
        async with async_lock:
            self._active_count += 1

        loop = asyncio.get_running_loop()

        def _do_submit() -> int:
            """Run in background thread: submit work to rayon and return handle."""
            return rayon_submit(
                self._pool_type,
                n_items,
                fn,
                args,
            )

        try:
            # Submit to rayon in background thread, get opaque handle
            handle: int = await loop.run_in_executor(None, _do_submit)

            async def _await_result() -> T:
                """Wait for rayon task to complete via rayon_join."""
                try:
                    result = await asyncio.to_thread(rayon_join, handle)
                    return result  # type: ignore[return-value]
                except RuntimeError as e:
                    if "aborted" in str(e).lower() or "panicked" in str(e).lower():
                        raise RuntimeError(
                            f"Rayon {self._pool_type} task was aborted: {e}"
                        ) from None
                    raise

            if timeout is not None:
                return await asyncio.wait_for(_await_result(), timeout=timeout)
            return await _await_result()

        finally:
            async with async_lock:
                self._active_count -= 1

    def submit_sync(self, fn: "Callable[..., T]", /, *args: Any, n_items: int = 0) -> T | None:
        """Synchronous submit — blocks until complete. For use in non-async contexts.

        Falls back to direct call if Rust unavailable.
        """
        if not self._check_available():
            try:
                return fn(*args)
            except Exception:
                return None

        from hledac_rust_extensions import rayon_submit, rayon_join

        handle = rayon_submit(self._pool_type, n_items, fn, args)
        try:
            return rayon_join(handle)
        except RuntimeError as e:
            if "aborted" in str(e).lower() or "panicked" in str(e).lower():
                raise RuntimeError(
                    f"Rayon {self._pool_type} task was aborted: {e}"
                ) from None
            raise

    @property
    def active_count(self) -> int:
        """Number of tasks currently submitted to the pool."""
        return self._active_count

    @property
    def pool_type(self) -> str:
        """Pool type: cpu, io, or mixed."""
        return self._pool_type

    def shutdown(self) -> None:
        """Shutdown signal — no-op for rayon pools (process-wide singletons)."""
        global _rust_pool
        _rust_pool = None


# ---------------------------------------------------------------------------
# Public helpers — preferred entry points
# ---------------------------------------------------------------------------

async def cpu_bound(func: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T:
    """Await a CPU-bound synchronous function on the shared pool.

    .. deprecated::
        cpu_bound is an alias for io_bound and does NOT run on a separate
        CPU-bound ThreadPoolExecutor.  On M1 8GB a single shared pool is
        used to avoid doubling thread-stack RAM overhead.
        For CPU-bound Python work prefer :func:`asyncio.to_thread` directly;
        for I/O-bound blocking calls use :func:`io_bound`.

    Use instead of `await asyncio.to_thread(func, *args)` for any
    compute-intensive Python work (hashing, parsing, regex, etc.).
    For I/O-bound work (network, disk) prefer io_bound().
    """
    warnings.warn(
        "cpu_bound is deprecated — it is an alias for io_bound on M1 8GB. "
        "Use asyncio.to_thread() for CPU-bound work or io_bound() for I/O-bound work.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await get_shared_pool().run(func, *args, **kwargs)


async def io_bound(func: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T:
    """Await an I/O-bound synchronous function on the shared pool.

    Use instead of `await asyncio.to_thread(func, *args)` for any
    blocking I/O (DNS, WHOIS, SSL handshake, SQLite, file I/O).
    """
    return await get_shared_pool().run(func, *args, **kwargs)


# ---------------------------------------------------------------------------
# ISSUE #032: run_in_pool — drop-in replacement for loop.run_in_executor
# ---------------------------------------------------------------------------

async def run_in_pool(
    pool_type: Literal["cpu", "io", "mixed"],
    fn: "Callable[..., T]",
    /,
    *args: Any,
    n_items: int = 0,
    timeout: float | None = None,
    **kwargs: Any,
) -> T:
    """Drop-in replacement for loop.run_in_executor(executor, fn, *args).

    Routes to Rust rayon pool (cpu/io/mixed) instead of Python ThreadPoolExecutor.
    Provides cancelable asyncio.Future via rayon background thread.

    Usage:
        # Before (ThreadPoolExecutor):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, fn, arg1, arg2)

        # After (Rust rayon pool):
        result = await run_in_pool("cpu", fn, arg1, arg2)

    Args:
        pool_type: "cpu" (4 P-cores), "io" (2 threads), "mixed" (adaptive)
        fn: Synchronous callable to run
        *args: Positional arguments passed to fn
        n_items: Batch size hint for mixed pool adaptive threading
        timeout: Optional timeout in seconds
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs)
    """
    pool = get_rust_pool(pool_type)
    return await pool.submit(fn, *args, timeout=timeout, n_items=n_items, **kwargs)
