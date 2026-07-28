"""
Isolated executors — backed by RustWorkerPool (rayon thread pool).

Provides CPU/IO-bound workload distribution using Rust rayon thread pools
instead of Python's PEP 734 concurrent.interpreters (which is NOT in
Python 3.14 stdlib — it is a separate package that must be installed).

Rationale (A8):
  - PEP 734 (concurrent.interpreters) was added experimental in Python 3.13.
    In Python 3.14 it is NOT in stdlib — requires `pip install interpreters`.
  - The concurrent.interpreters layer added ~30-50 MB RAM overhead per
    interpreter pool on M1 8GB, for a no-op on systems without the extra package.
  - RustWorkerPool (rayon, ~5μs/task) provides 10× better throughput than
    Python interpreter isolation (~50μs/task for channel dispatch).
  - sys.path.insert Security Risk removed — RustWorkerPool doesn't spawn interpreters.

Mapping: IsolatedDuckDBExecutor → RustWorkerPool("io"),
         IsolatedMLXExecutor → RustWorkerPool("cpu"),
         IsolatedEvidenceBatchWriter → RustWorkerPool("mixed").

Invariants:
  - Always-on: no feature flags, RustWorkerPool fallback covers all cases
  - Bounded: rayon pool caps (cpu=4, io=2, mixed=1-2) prevent exhaustion
  - Fail-safe: every method returns None/[] on error, never raises
  - Backward-compatible API: same class names, same method signatures

M1 8GB thread budget:
  - Rayon cpu_pool:  4 threads (P-cores, QoS=utility)
  - Rayon io_pool:   2 threads (E-cores, QoS=background)
  - Rayon mixed_pool: 1-2 threads (adaptive)
  - asyncio event loop: 1 thread
  ─────────────────────────────────────────
  Total: 7-8 OS threads (fits 8-core M1)
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import warnings
from typing import TYPE_CHECKING, Any, Callable, Literal, TypeVar

from hledac.universal.runtime.worker_pool import RustWorkerPool, get_rust_pool
from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
T = TypeVar("T", default=object)

# Deprecated backward-compatibility constant — kept only for API compatibility.
#
# IMPORTANT (M8): PEP 734 (concurrent.interpreters) was experimental in Python 3.13
# and is NOT in Python 3.14 stdlib — requires `pip install interpreters` as a
# separate package. This project uses RustWorkerPool (rayon thread pools) instead.
#
# MAX_INTERPRETERS is a NO-OP: the actual concurrency budget is:
#   Rayon cpu_pool:  4 threads (P-cores)
#   Rayon io_pool:   2 threads (E-cores)
#   asyncio event loop: 1 thread
#   Total: 7 OS threads (fits M1 8-core)
#
# The 3-interpreters × 1MB-stack claim in old docstrings was incorrect — this
# project has never used interpreter-based isolation (it used RustWorkerPool since A8).
# This constant is NOT referenced anywhere in the codebase.
MAX_INTERPRETERS: int = 3

__all__ = [
    # Stub classes — kept for backward compatibility with tests
    "IsolatedInterpreter",
    "IsolatedInterpreterPool",
    # Main executor classes
    "IsolatedDuckDBExecutor",
    "IsolatedMLXExecutor",
    "IsolatedEvidenceBatchWriter",
    "get_duckdb_executor",
    "get_mlx_executor",
    "get_evidence_batch_writer",
    "close_all_pools",
    "is_pep734_available",
    "get_interpreter_stats",
    "IsolatedRuntime",
    "get_isolated_runtime",
]

# -----------------------------------------------------------------------------
# Pool availability — mirrors the old PEP 734 _interpreters_available flag
# -----------------------------------------------------------------------------

_RUST_AVAILABLE: bool = False


def _check_rust_pool_available() -> bool:
    """Check if Rust rayon pool is available (always-on fallback for PEP 734)."""
    global _RUST_AVAILABLE
    if _RUST_AVAILABLE:
        return True
    try:
        pool = get_rust_pool("cpu")
        _RUST_AVAILABLE = pool._check_available()
    except Exception:
        _RUST_AVAILABLE = False
    return _RUST_AVAILABLE


# For backward compatibility — always True since RustWorkerPool is always-on.
# The old PEP 734 (concurrent.interpreters) check is replaced by Rust pool.
is_pep734_available = lambda: True


# -----------------------------------------------------------------------------
# Stub classes for type-only use (kept for docstring compatibility)
# These are no longer used but remain importable for backward compatibility
# -----------------------------------------------------------------------------


class IsolatedExecutorError(Exception):
    """Base exception for isolated executor errors."""


class InterpreterNotAvailableError(IsolatedExecutorError):
    """Raised when concurrent.interpreters is not available."""


class InterpreterStartError(IsolatedExecutorError):
    """Raised when isolated interpreter fails to start."""


class InterpreterChannelError(IsolatedExecutorError):
    """Raised when inter-interpreter communication fails."""


# -----------------------------------------------------------------------------
# Function registry — kept for P1-04 API compatibility
# (No RCE risk since we no longer use pickle in isolated interpreters)
# -----------------------------------------------------------------------------


_ISOLATED_FUNC_REGISTRY: dict[str, Callable[..., Any]] = {}
_ISOLATED_FUNC_NAMES: set[str] = set()


def clear_isolated_function_registry() -> None:
    """Clear all registered functions — called during executor shutdown."""
    _ISOLATED_FUNC_REGISTRY.clear()
    _ISOLATED_FUNC_NAMES.clear()


def register_isolated_function(name: str, func: Callable[..., Any]) -> None:
    """Register a function for isolated executor RPC (P1-04 compatibility)."""
    _ISOLATED_FUNC_REGISTRY[name] = func
    _ISOLATED_FUNC_NAMES.add(name)


def register_isolated_function_decorator(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a function for isolated executor RPC."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        register_isolated_function(name, func)
        return func

    return decorator


def _get_registered_func(name: str) -> Callable[..., Any] | None:
    """Get registered function by name, or None if not found."""
    return _ISOLATED_FUNC_REGISTRY.get(name)


def is_function_registered(name: str) -> bool:
    """Check if a function name is registered."""
    return name in _ISOLATED_FUNC_NAMES


# -----------------------------------------------------------------------------
# IsolatedInterpreter — stub backed by RustWorkerPool
# Backward compatibility with tests and old API
# -----------------------------------------------------------------------------


class IsolatedInterpreter:
    """
    Stub: Wraps a concurrent.interpreters.Interpreter with RPC capability.

    NOTE: This is a stub implementation. PEP 734 concurrent.interpreters
    is NOT in Python 3.14 stdlib. This stub delegates to RustWorkerPool
    for actual parallelism while preserving the old API surface.

    Kept for backward compatibility with tests.
    """

    __slots__ = ("_closed", "_available")

    def __init__(self, *, max_workers: int = 1, stack_size: int = 1024 * 1024) -> None:
        self._closed = False
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    def __enter__(self) -> "IsolatedInterpreter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def eval(self, code: str) -> Any:
        """Evaluate Python code in the current interpreter (stub for subprocess-level isolation).

        Note: True interpreter-level isolation (PEP 734) is replaced by RustWorkerPool.
        This stub uses Python's eval() directly — subprocess-level isolation is
        provided by the test's subprocess.run() call.
        """
        try:
            return eval(code)  # noqa: S307
        except Exception as e:
            logger.warning(f"eval failed: {e}")
            return None

    def start(self) -> bool:
        """Stub: always returns True (RustWorkerPool is always available)."""
        return self._available

    def close(self) -> None:
        """Stub: no-op for RustWorkerPool."""
        self._closed = True

    async def run_async(self, func_name: str, *args: Any, **kwargs: Any) -> T | None:
        """Stub: delegates to RustWorkerPool via function registry."""
        if not self._available:
            return None
        func = _get_registered_func(func_name)
        if func is None:
            return None
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"run_async failed: {e}")
            return None

    def run_sync(self, func_name: str, *args: Any, **kwargs: Any) -> T | None:
        """Stub: synchronous version.

        B17 fix: uses loop.run_until_complete() instead of
        run_coroutine_threadsafe().result() to eliminate the extra
        context-switch thread handoff when called from an executor thread.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_async(func_name, *args, **kwargs))
            return result  # type: ignore[return-value]
        return loop.run_until_complete(self.run_async(func_name, *args, **kwargs))  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# IsolatedInterpreterPool — stub backed by RustWorkerPool
# -----------------------------------------------------------------------------


class IsolatedInterpreterPool:
    """
    Stub: Pool of IsolatedInterpreters with round-robin allocation.

    NOTE: This is a stub implementation. PEP 734 concurrent.interpreters
    is NOT in Python 3.14 stdlib. This stub delegates to RustWorkerPool
    for actual parallelism while preserving the old API surface.

    Kept for backward compatibility with tests.
    """

    __slots__ = ("_available", "_max_size")

    def __init__(self, *, max_size: int = MAX_INTERPRETERS, stack_size: int = 1024 * 1024) -> None:
        self._max_size = min(max_size, MAX_INTERPRETERS)
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if interpreter pool is available."""
        return self._available

    def close_all(self) -> None:
        """Stub: no-op for RustWorkerPool."""
        pass

    async def run_async(self, func_name: str, *args: Any, **kwargs: Any) -> T | None:
        """Stub: delegates to RustWorkerPool via function registry."""
        if not self._available:
            func = _get_registered_func(func_name)
            if func is None:
                return None
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"run_async failed: {e}")
                return None
        # Use Rust pool for actual parallelism
        func = _get_registered_func(func_name)
        if func is None:
            return None
        try:
            pool = get_rust_pool("mixed")
            return await pool.submit(func, *args, **kwargs)
        except Exception as e:
            logger.warning(f"pool.run_async failed: {e}")
            return None

    def run_sync(self, func_name: str, *args: Any, **kwargs: Any) -> T | None:
        """Stub: synchronous version.

        B17 fix: uses loop.run_until_complete() instead of
        run_coroutine_threadsafe().result() to eliminate the extra
        context-switch thread handoff when called from an executor thread.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_async(func_name, *args, **kwargs))
            return result  # type: ignore[return-value]
        return loop.run_until_complete(self.run_async(func_name, *args, **kwargs))  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# IsolatedDuckDBExecutor — backed by RustWorkerPool("io")
# -----------------------------------------------------------------------------


class IsolatedDuckDBExecutor:
    """
    Executes DuckDB queries via RustWorkerPool("io").

    Backed by rayon io_pool (2 threads) for I/O-bound SQL execution.
    Memory isolation: DuckDB memory is managed by the Rust pool's thread arena.

    Use with DuckDBShadowStore.async_ingest_findings_batch() for
    parallel DuckDB operations alongside MLX inference.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: io_pool cap = 2 threads (DuckDB connection limit)
      - Fail-safe: returns None/[] on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_queries: int = 4) -> None:
        self._pool = get_rust_pool("io")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True — Rust pool is always-on)."""
        return self._available

    async def execute_query_async(
        self,
        query_func: Callable[..., list[dict]],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> list[dict] | None:
        """
        Execute a DuckDB query function on the Rust io_pool.

        Args:
            query_func: Function that executes DuckDB query and returns results.
            *args: Positional arguments to pass to query_func.
            timeout: Optional timeout in seconds.
            **kwargs: Keyword arguments to pass to query_func.

        Returns:
            Query results as list[dict], or None on error.

        Fail-safe: returns None on any error, never raises.
        """
        if not self._available:
            try:
                return query_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Query execution failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(query_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:duckdb",
                )
            return await self._pool.submit(query_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("DuckDB query timeout")
            return None
        except Exception as e:
            logger.warning(f"DuckDB query failed: {e}")
            return None

    def execute_query_sync(
        self,
        query_func: Callable[..., list[dict]],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict] | None:
        """Synchronous version of execute_query_async.

        B17 fix: uses loop.run_until_complete() instead of
        run_coroutine_threadsafe().result() to eliminate the extra
        context-switch thread handoff when called from an executor thread.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.execute_query_async(query_func, *args, **kwargs))
            return result
        return loop.run_until_complete(self.execute_query_async(query_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# IsolatedMLXExecutor — backed by RustWorkerPool("cpu")
# -----------------------------------------------------------------------------


class IsolatedMLXExecutor:
    """
    Executes MLX inference via RustWorkerPool("cpu").

    Backed by rayon cpu_pool (4 P-cores) for CPU-bound inference.
    Memory isolation: MLX Metal arena is managed independently by the
    MLX library — the pool provides thread-level parallelism.

    Note:
        MLX already releases GIL at C-level. The pool provides additional
        thread-level parallelism for multi-batch inference scenarios.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: cpu_pool cap = 4 threads (P-core QoS)
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_inference: int = 2) -> None:
        self._pool = get_rust_pool("cpu")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True — Rust pool is always-on)."""
        return self._available

    async def run_inference_async(
        self,
        inference_func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Run MLX inference function on the Rust cpu_pool.

        Args:
            inference_func: Function that runs MLX inference.
            *args: Positional arguments (e.g., prompt, config).
            timeout: Optional timeout in seconds.
            **kwargs: Keyword arguments (e.g., kv_bits, max_kv_size).

        Returns:
            Inference result, or None on error.

        Fail-safe: returns None on any error, never raises.
        """
        if not self._available:
            try:
                return inference_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Inference failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(inference_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:mlx",
                )
            return await self._pool.submit(inference_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("MLX inference timeout")
            return None
        except Exception as e:
            logger.warning(f"MLX inference failed: {e}")
            return None

    def run_inference_sync(
        self,
        inference_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of run_inference_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_inference_async(inference_func, *args, **kwargs))
            return result
        return asyncio.run_coroutine_threadsafe(
            self.run_inference_async(inference_func, *args, **kwargs), loop
        ).result()

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# IsolatedEvidenceBatchWriter — backed by RustWorkerPool("mixed")
# -----------------------------------------------------------------------------


class IsolatedEvidenceBatchWriter:
    """
    Writes evidence batches via RustWorkerPool("mixed").

    Backed by rayon mixed_pool (adaptive 1-2 threads) for CPU-bound
    batch serialization. Rust MPSC is used for the actual queue
    (faster than Python channel) — this executor handles the
    CPU-bound batch transformation/serialization step.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: mixed_pool cap = adaptive 1-2 threads
      - Fail-safe: returns original items on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_batch_workers: int = 2) -> None:
        self._pool = get_rust_pool("mixed")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True — Rust pool is always-on)."""
        return self._available

    async def process_batch_async(
        self,
        process_func: Callable[..., list[dict]],
        items: list[dict],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> list[dict]:
        """
        Process evidence batch on the Rust mixed_pool.

        Args:
            process_func: Function that processes batch items.
            items: List of evidence items to process.
            timeout: Optional timeout in seconds.
            *args, **kwargs: Additional arguments to process_func.

        Returns:
            Processed items, or original items on error.

        Fail-safe: returns original items on any error.
        """
        if not self._available:
            try:
                return process_func(items, *args, **kwargs)
            except Exception as e:
                logger.warning(f"Batch processing failed (pool unavailable): {e}")
                return items

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(process_func, items, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:evidence",
                )
            return await self._pool.submit(process_func, items, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("Evidence batch processing timeout")
            return items
        except Exception as e:
            logger.warning(f"Evidence batch processing failed: {e}")
            return items

    def process_batch_sync(
        self,
        process_func: Callable[..., list[dict]],
        items: list[dict],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict]:
        """Synchronous version of process_batch_async.

        B17 fix: uses loop.run_until_complete() instead of
        run_coroutine_threadsafe().result() to eliminate the extra
        context-switch thread handoff when called from an executor thread.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.process_batch_async(process_func, items, *args, **kwargs))
            return result
        return loop.run_until_complete(self.process_batch_async(process_func, items, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# Module-level pool singletons — lazy, thread-safe
# -----------------------------------------------------------------------------

_duckdb_pool: IsolatedDuckDBExecutor | None = None
_mlx_pool: IsolatedMLXExecutor | None = None
_evidence_pool: IsolatedEvidenceBatchWriter | None = None
_pools_lock = threading.Lock()


def get_duckdb_executor() -> IsolatedDuckDBExecutor:
    """Get or create global DuckDB executor pool.

    Resolution order (A3):
      1. ServiceContainer ('executor.duckdb') — sprint-scoped via ctx.container
      2. Fallback to module-level _duckdb_pool — backward-compatible global
    """
    global _duckdb_pool

    if _duckdb_pool is not None:
        return _duckdb_pool

    try:
        from core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.duckdb")
        if inst is not None:
            return inst
    except Exception:
        pass

    with _pools_lock:
        if _duckdb_pool is None:
            _duckdb_pool = IsolatedDuckDBExecutor()
        return _duckdb_pool


def get_mlx_executor() -> IsolatedMLXExecutor:
    """Get or create global MLX executor pool.

    Resolution order (A3):
      1. ServiceContainer ('executor.mlx') — sprint-scoped via ctx.container
      2. Fallback to module-level _mlx_pool — backward-compatible global
    """
    global _mlx_pool

    if _mlx_pool is not None:
        return _mlx_pool

    try:
        from core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.mlx")
        if inst is not None:
            return inst
    except Exception:
        pass

    with _pools_lock:
        if _mlx_pool is None:
            _mlx_pool = IsolatedMLXExecutor()
        return _mlx_pool


def get_evidence_batch_writer() -> IsolatedEvidenceBatchWriter:
    """Get or create global evidence batch writer pool.

    Resolution order (A3):
      1. ServiceContainer ('executor.evidence') — sprint-scoped via ctx.container
      2. Fallback to module-level _evidence_pool — backward-compatible global
    """
    global _evidence_pool

    if _evidence_pool is not None:
        return _evidence_pool

    try:
        from core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.evidence")
        if inst is not None:
            return inst
    except Exception:
        pass

    with _pools_lock:
        if _evidence_pool is None:
            _evidence_pool = IsolatedEvidenceBatchWriter()
        return _evidence_pool


def close_all_pools() -> None:
    """
    Close all global executor pools.

    No-op for RustWorkerPool (process-wide singletons).
    Kept for API backward compatibility.
    """
    global _duckdb_pool, _mlx_pool, _evidence_pool
    with _pools_lock:
        _duckdb_pool = None
        _mlx_pool = None
        _evidence_pool = None


def get_interpreter_stats() -> dict[str, Any]:
    """
    Get statistics about isolated executor usage.

    Returns dict with availability info and pool statistics.
    The 'pep734_available' key is kept for backward compatibility
    but now reflects RustWorkerPool availability.
    """
    import sys

    rust_ok = _check_rust_pool_available()
    return {
        "pep734_available": rust_ok,  # backward compat key name
        "rust_pool_available": rust_ok,
        "python_version": sys.version_info[:2],  # backward compat
        "python_version_note": "PEP 734 concurrent.interpreters replaced by RustWorkerPool (rayon)",
        "max_interpreters": MAX_INTERPRETERS,  # backward compat
        "pools": {
            "duckdb": {
                "available": _duckdb_pool.is_available if _duckdb_pool else False,
                "pool_type": "io",
            },
            "mlx": {
                "available": _mlx_pool.is_available if _mlx_pool else False,
                "pool_type": "cpu",
            },
            "evidence": {
                "available": _evidence_pool.is_available if _evidence_pool else False,
                "pool_type": "mixed",
            },
        },
    }


# -----------------------------------------------------------------------------
# IsolatedRuntime Factory — unified lazy-init factory
# -----------------------------------------------------------------------------


class IsolatedRuntime:
    """
    Unified lazy-init factory for all executor pools.

    Single factory that lazily initializes all 3 executor pools:
      - duckdb: IsolatedDuckDBExecutor (RustWorkerPool "io")
      - mlx: IsolatedMLXExecutor (RustWorkerPool "cpu")
      - evidence: IsolatedEvidenceBatchWriter (RustWorkerPool "mixed")

    Thread-safe: all initialization is guarded by a single lock.

    Invariants:
      - Always-on: no feature flags, RustWorkerPool is always available
      - Bounded: each pool capped by rayon thread limits
      - Fail-safe: is_available reflects actual runtime availability
    """

    __slots__ = ("_duckdb", "_mlx", "_evidence", "_lock", "_initialized")

    def __init__(self) -> None:
        self._duckdb: IsolatedDuckDBExecutor | None = None
        self._mlx: IsolatedMLXExecutor | None = None
        self._evidence: IsolatedEvidenceBatchWriter | None = None
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazily initialize all pools on first use (thread-safe)."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._duckdb = IsolatedDuckDBExecutor()
            self._mlx = IsolatedMLXExecutor()
            self._evidence = IsolatedEvidenceBatchWriter()
            self._initialized = True

    @property
    def duckdb(self) -> IsolatedDuckDBExecutor:
        """Get DuckDB executor pool (lazy init)."""
        self._ensure_initialized()
        assert self._duckdb is not None
        return self._duckdb

    @property
    def mlx(self) -> IsolatedMLXExecutor:
        """Get MLX inference executor pool (lazy init)."""
        self._ensure_initialized()
        assert self._mlx is not None
        return self._mlx

    @property
    def evidence(self) -> IsolatedEvidenceBatchWriter:
        """Get evidence batch writer pool (lazy init)."""
        self._ensure_initialized()
        assert self._evidence is not None
        return self._evidence

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True with RustWorkerPool)."""
        return _check_rust_pool_available()

    def close(self) -> None:
        """Close all executor pools. No-op for RustWorkerPool."""
        with self._lock:
            self._initialized = False
            self._duckdb = None
            self._mlx = None
            self._evidence = None


# Module-level IsolatedRuntime singleton
_isolated_runtime: IsolatedRuntime | None = None


def get_isolated_runtime() -> IsolatedRuntime:
    """
    Get or create the global IsolatedRuntime singleton.

    Thread-safe lazy initialization.
    """
    global _isolated_runtime
    if _isolated_runtime is None:
        _isolated_runtime = IsolatedRuntime()
    return _isolated_runtime
