"""Shared bounded worker pool — replaces scattered asyncio.to_thread() usage.

Replaces bare `asyncio.to_thread()` calls throughout the codebase with a
single, bounded, instrumentation-friendly executor.



Thread budget on M1 8GB:
  - Rayon cpu_pool:    4 threads (Rust MLX inference)
  - Rayon io_pool:     2 threads (Rust async I/O)
  - asyncio event loop: 1 thread
  - Shared pool:        adaptive (governed by M1ResourceGovernor)
  ─────────────────────────────────────────────────────
  Total:               11 threads max (fits 8-core M1 without thrashing)

ISSUE #014: Adaptive worker count based on M1ResourceGovernor.
  - max_workers dynamically derived from UMA state via ConcurrencyPreset
  - emergency: 0 workers, critical: 1, warn: 3, soft_warn/ok: 5
  - Reconfiguration is lazy (on next run() after state change)
  - Thread-stack RAM: ~1 MB/thread × N — bounded by governor

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

# TEL-02: Lazy import — OTel context capture for trace propagation across Rust boundary.
# Falls back to a no-op when OTel is not installed (safe for all code paths).
try:
    from hledac.universal.utils.async_helpers import current_otel_context
except ImportError:
    # Fallback no-op when OTel instrumentation is absent.
    def current_otel_context() -> dict | None:
        return None

# ISSUE #014: Memory-aware — uses sample_uma_status() + ConcurrencyPreset at runtime
try:
    from hledac.universal.core.resource_governor import (
        ConcurrencyPreset,
        sample_uma_status,
    )
    _GOVERNOR_AVAILABLE = True
except ImportError:
    _GOVERNOR_AVAILABLE = False

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

    ISSUE #014: Adaptive sizing via M1ResourceGovernor.
      - max_workers derived from UMA state via ConcurrencyPreset
      - emergency: 0 workers, critical: 1, warn: 3, soft_warn/ok: 5
      - Lazy reconfiguration: executor swapped only when state changes
        and no tasks are currently running (safe, no mid-job disruption)
      - Thread-stack RAM: ~1 MB/thread × N workers — bounded by governor

    This class is safe to use from multiple asyncio tasks simultaneously
    because it wraps a ThreadPoolExecutor behind run_in_executor().
    """

    __slots__ = (
        "_executor",
        "_max_workers",
        "_active_count",
        "_async_lock",
        "_last_state",
        "_executor_lock",
    )

    def __init__(self, max_workers: int | None = None) -> None:
        cpu_count = os.cpu_count() or 4
        if max_workers is None:
            # ISSUE #014: Start with conservative default.
            # Governor will adjust dynamically on first run().
            max_workers = max(2, min(6, cpu_count - 4))
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hledac-shared",
        )
        self._active_count = 0
        self._async_lock: asyncio.Lock | None = None
        self._last_state: str | None = None
        # Protects executor swap (thread-safe reconfiguration)
        self._executor_lock = threading.Lock()

    def _compute_governed_workers(self) -> int:
        """Compute target worker count from M1ResourceGovernor, falling back to static config."""
        if not _GOVERNOR_AVAILABLE:
            cpu_count = os.cpu_count() or 4
            return max(2, min(6, cpu_count - 4))
        # Use sample_uma_status() — canonical UMA sampling API
        try:
            from hledac.universal.core.resource_governor import sample_uma_status

            uma = sample_uma_status()
            preset = ConcurrencyPreset.from_state(uma.state)
            return preset.max_workers
        except Exception:
            cpu_count = os.cpu_count() or 4
            return max(2, min(6, cpu_count - 4))

    def _should_reconfigure(self, target_workers: int) -> bool:
        """Return True if executor should be reconfigured to target_workers."""
        if self._last_state is None:
            return True
        # Reconfigure when governor signals a state change
        return target_workers != self._max_workers

    def _reconfigure_executor(self, target_workers: int) -> None:
        """Swap executor for a new one with target_workers. Thread-safe."""
        with self._executor_lock:
            old_executor = self._executor
            self._executor = ThreadPoolExecutor(
                max_workers=target_workers,
                thread_name_prefix="hledac-shared",
            )
            self._max_workers = target_workers
            # Give in-flight tasks a chance to complete before shutting down old executor
            old_executor.shutdown(wait=False)

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

        ISSUE #014: Reconfigures executor dynamically based on M1ResourceGovernor
        state. When UMA state changes (e.g., soft_warn → warn), the executor
        is swapped to a new ThreadPoolExecutor with the appropriate worker count.
        In-flight tasks are never disrupted — reconfiguration is lazy.

        Args:
            func: The blocking callable to run.
            timeout: Optional timeout in seconds. If None, runs without timeout.
                A TimeoutError is raised if the callable does not complete in time.

        Note: functools.partial is used instead of a lambda to avoid
        allocating a new closure object on every call.
        """
        # ISSUE #014: Lazy reconfiguration — check governor on each run()
        target_workers = self._compute_governed_workers()
        if target_workers == 0:
            # Emergency mode: no workers available, run inline to prevent OOM
            # This is better than crashing with ThreadPoolExecutor(max_workers=0)
            self._last_state = "emergency"
            if timeout is not None:
                return await safe_wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout, label="worker_pool_emergency")
            return await asyncio.to_thread(func, *args, **kwargs)

        if self._should_reconfigure(target_workers):
            self._reconfigure_executor(target_workers)
            self._last_state = "governed"  # mark as governor-settled

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
    """Check if Rust rayon channel-dispatch extension is available.

    ISSUE 3.1: Preferuje rayon_submit_channel (crossbeam-channel dispatch)
    před starým rayon_submit (thread::spawn per task).
    Kanálová dispatch: ~5μs/task vs ~500μs/task (thread::spawn overhead).
    """
    global _RUST_AVAILABLE
    if _RUST_AVAILABLE is not None:
        return _RUST_AVAILABLE
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    raw = rust.raw
    if raw.rayon_submit_channel is not None and raw.rayon_join_channel is not None and raw.rayon_abort_channel is not None:
        _RUST_AVAILABLE = True
    else:
        _RUST_AVAILABLE = False
    return _RUST_AVAILABLE


class RustWorkerPool:
    """Pool backed by Rust rayon ThreadPool — M1 P-core QoS aware.

    Provides cancelable asyncio.Future via rayon channel dispatch.

    pool_type:
      "cpu"   → rayon cpu_pool (4 P-cores): SIMD, hashing, pattern match
      "io"    → rayon io_pool (2 threads): DuckDB, graph_traverse, compress
      "mixed" → rayon mixed_pool (adaptive 1-2 threads): IOC extract, url_ops

    Fail-safe: if Rust extension unavailable, falls back to SharedWorkerPool
    (ThreadPoolExecutor) automatically.

    ISSUE 3.1: Uses rayon_submit_channel (crossbeam-channel dispatch to existing
    rayon pool dispatcher — žádný thread::spawn per task).
    ~5μs/task vs ~500μs/task thread::spawn overhead.

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
        """Return True if Rust rayon channel dispatch extension is available."""
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
        """Submit work to the rayon pool via channel dispatch, returning an awaitable.

        ISSUE 3.1: Uses rayon_submit_channel (crossbeam-channel → existing rayon pool
        dispatcher, žádný thread::spawn per task). ~5μs/task vs ~500μs/task.

        Cancellation: Future.cancel() → rayon_abort_channel(handle) → cancel_flag set.

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
                f"Rust rayon channel dispatch unavailable, falling back to SharedWorkerPool "
                f"for {self._pool_type} pool",
                RuntimeWarning,
                stacklevel=2,
            )
            return await get_shared_pool().run(fn, *args, timeout=timeout, **kwargs)

        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        rayon_submit_channel = rust.raw.rayon_submit_channel
        rayon_join_channel = rust.raw.rayon_join_channel
        rayon_abort_channel = rust.raw.rayon_abort_channel

        async_lock = await self._get_async_lock()
        async with async_lock:
            self._active_count += 1

        # TEL-02: Capture OTel trace context before crossing into Rust rayon pool.
        # current_otel_context() returns {trace_id, span_id} as HEX STRINGS (not ints!)
        # from otel._instrumentation.current_trace_id() which does format(ctx.trace_id, "032x").
        # We parse them to integers here so Rust receives u128 as expected.
        # Guard: "0"*32 / "0"*16 are truthy strings in Python — filter them out.
        otel_ctx = current_otel_context()
        if otel_ctx:
            trace_id_raw = otel_ctx.get("trace_id")
            span_id_raw = otel_ctx.get("span_id")
            # Parse hex strings to integers; filter "0"*N all-zeros as "no trace"
            try:
                trace_id: int | None = int(trace_id_raw, 16) if trace_id_raw and trace_id_raw != "0" * 32 else None
            except (ValueError, TypeError):
                trace_id = None
            try:
                span_id: int | None = int(span_id_raw, 16) if span_id_raw and span_id_raw != "0" * 16 else None
            except (ValueError, TypeError):
                span_id = None
        else:
            trace_id = None
            span_id = None

        loop = asyncio.get_running_loop()

        def _do_submit() -> int:
            """Run in asyncio-to_thread worker: submit work to rayon dispatcher and return handle."""
            # TEL-02: Pass trace_id/span_id as u128 for cross-language trace propagation.
            return rayon_submit_channel(
                self._pool_type,
                n_items,
                fn,
                args,
                trace_id,
                span_id,
            )

        try:
            # Submit to rayon dispatcher via channel in background thread, get opaque handle
            handle: int = await loop.run_in_executor(None, _do_submit)

            async def _await_result() -> T:
                """Wait for rayon task to complete via rayon_join_channel."""
                try:
                    result = await asyncio.to_thread(rayon_join_channel, handle, None)
                    return result  # type: ignore[return-value]
                except RuntimeError as e:
                    if "aborted" in str(e).lower() or "timed out" in str(e).lower():
                        raise RuntimeError(
                            f"Rayon {self._pool_type} task was aborted: {e}"
                        ) from None
                    raise

            if timeout is not None:
                async with asyncio.timeout(timeout):
                    return await _await_result()
            return await _await_result()

        finally:
            # Abort the rayon task on any exit path (timeout, cancellation, error).
            # rayon_abort_channel is safe to call even if the task already completed
            # (condvar already notified, no-op after result is set).
            # This prevents dispatcher thread leaks on asyncio.timeout fires.
            try:
                from hledac.universal.core.rust_backend import rust
                _abort = rust.raw.rayon_abort_channel
                if _abort is not None:
                    _abort(handle)
            except Exception:
                pass  # Best-effort — don't mask original errors
            async with async_lock:
                self._active_count -= 1

    def submit_sync(self, fn: "Callable[..., T]", /, *args: Any, n_items: int = 0) -> T | None:
        """Synchronous submit — blocks until complete. For use in non-async contexts.

        ISSUE 3.1: Uses rayon_submit_channel (crossbeam-channel dispatch).

        Falls back to direct call if Rust unavailable.
        """
        if not self._check_available():
            try:
                return fn(*args)
            except Exception:
                return None

        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        rayon_submit_channel = rust.raw.rayon_submit_channel

        handle = rayon_submit_channel(self._pool_type, n_items, fn, args)
        try:
            return rayon_join_channel(handle, None)
        except RuntimeError as e:
            if "aborted" in str(e).lower() or "timed out" in str(e).lower():
                raise RuntimeError(
                    f"Rayon {self._pool_type} task was aborted: {e}"
                ) from None
            raise
        except Exception:
            # Best-effort abort on unexpected errors (e.g. Rust panic).
            # rayon_join_channel has already waited via condvar,
            # so this is truly best-effort — the thread is already done.
            try:
                rayon_abort_channel(handle)
            except Exception:
                pass
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
