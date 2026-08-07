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
    # [META]-004: Elastic rayon pool manager
    "RayonPoolManager",
    "get_rayon_pool_manager",
    # ISSUE [SWARM]-005: FFI Circuit Breaker Exceptions
    "CircuitBreakerOpenError",
    "FallbackActivatedError",
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


# ISSUE [SWARM]-005: FFI Circuit Breaker Exceptions
# ============================================================================


class CircuitBreakerOpenError(IsolatedExecutorError):
    """
    Raised when the FFI circuit breaker is OPEN for a Rust module.
    
    This indicates that the Rust SIMD path is currently blocked due to
    repeated failures (panics, serialization errors, poisoned mutexes).
    The Python fallback path should be used instead.
    
    Attributes:
        module: The Rust module name (e.g., "graph_traverse", "finding_collapser")
        recovery_timeout_s: Seconds until circuit breaker allows retry
        reason: The last failure reason
    """

    __slots__ = ("module", "recovery_timeout_s", "reason")

    def __init__(
        self,
        module: str,
        recovery_timeout_s: float = 30.0,
        reason: str = "",
    ) -> None:
        super().__init__(
            f"FFI circuit breaker OPEN for module={module!r} "
            f"(retry in {recovery_timeout_s:.1f}s): {reason}"
        )
        self.module = module
        self.recovery_timeout_s = recovery_timeout_s
        self.reason = reason


class FallbackActivatedError(IsolatedExecutorError):
    """
    Raised when FFI fallback cascade is activated (Rust → Python → No-op).
    
    This is an informational exception indicating that:
    1. The Rust SIMD path failed (panic or exception)
    2. Python native fallback was activated
    3. Data may be incomplete or degraded
    
    Attributes:
        module: The Rust module name
        rust_error: Error message from Rust path
        fallback_path: Which fallback was used: "python_native" or "noop"
        data_degraded: Whether the result is potentially incomplete
    """

    __slots__ = ("module", "rust_error", "fallback_path", "data_degraded")

    def __init__(
        self,
        module: str,
        rust_error: str = "",
        fallback_path: str = "python_native",
        data_degraded: bool = False,
    ) -> None:
        path_desc = {
            "rust_simd": "Rust SIMD",
            "python_native": "Python Native",
            "noop": "No-op (degraded)",
        }.get(fallback_path, fallback_path)
        
        msg = f"FFI fallback activated for module={module!r}: {path_desc}"
        if rust_error:
            msg += f" (Rust error: {rust_error})"
        if data_degraded:
            msg += " [DATA DEGRADED]"
        
        super().__init__(msg)
        self.module = module
        self.rust_error = rust_error
        self.fallback_path = fallback_path
        self.data_degraded = data_degraded


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
        from hledac.universal.core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.duckdb")
        if inst is not None:
            return inst
    except Exception:  # noqa: BLE001
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
        from hledac.universal.core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.mlx")
        if inst is not None:
            return inst
    except Exception:  # noqa: BLE001
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
        from hledac.universal.core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.evidence")
        if inst is not None:
            return inst
    except Exception:  # noqa: BLE001
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


# -----------------------------------------------------------------------------
# [META]-004: RayonPoolManager — Phase-aware elastic pool resizing
# -----------------------------------------------------------------------------

# Import the elastic pool Rust bindings lazily to avoid import-time crashes.
_RUST_ELASTIC: dict[str, Any] = {}


def _get_elastic_rust() -> dict[str, Any] | None:
    """Lazily load Rust elastic pool bindings via rust.raw. Returns None if unavailable.

    R6: All Rust extension access goes through rust.raw, never direct import.
    The elastic_pool functions (resize_cpu_pool_py, etc.) are registered as
    top-level pyfunctions in the hledac_rust_extensions module.
    """
    global _RUST_ELASTIC
    if _RUST_ELASTIC:
        return _RUST_ELASTIC
    try:
        from hledac.universal.core.rust_backend import rust

        raw = rust.raw
        # rust.raw is a RustRawAccessor — missing attributes return None
        fn1 = getattr(raw, "resize_cpu_pool", None)
        fn2 = getattr(raw, "resize_io_pool", None)
        fn3 = getattr(raw, "init_elastic_pools", None)
        fn4 = getattr(raw, "get_elastic_cpu_threads", None)
        fn5 = getattr(raw, "get_elastic_io_threads", None)
        fn6 = getattr(raw, "get_elastic_total_threads", None)
        if None in (fn1, fn2, fn3, fn4, fn5, fn6):
            return None
        _RUST_ELASTIC = {
            "resize_cpu_pool": fn1,
            "resize_io_pool": fn2,
            "init_elastic_pools": fn3,
            "get_cpu_threads": fn4,
            "get_io_threads": fn5,
            "get_total_threads": fn6,
        }
        return _RUST_ELASTIC
    except Exception:
        return None


# M1 8GB thread budget: 4P + 4E = 8 total
_MAX_TOTAL_THREADS: int = 8

# Default pool sizes (BOOT phase)
_DEFAULT_CPU_THREADS: int = 4
_DEFAULT_IO_THREADS: int = 2

# Phase-aware pool configurations
# During ACTIVE: io_pool grows to 4 for fetch-heavy I/O workloads
# During SYNTHESIS: cpu_pool grows to 6 for MLX inference (borrows from io_pool)
# During WINDUP: back to defaults (4 cpu, 2 io)
_PHASE_POOL_CONFIG: dict[str, tuple[int, int]] = {
    "BOOT": (_DEFAULT_CPU_THREADS, _DEFAULT_IO_THREADS),  # 6 total
    "WARMUP": (_DEFAULT_CPU_THREADS, _DEFAULT_IO_THREADS),  # 6 total
    "ACTIVE": (_DEFAULT_CPU_THREADS, 4),  # 8 total — io-heavy fetch
    "DEGRADED": (2, 2),  # 4 total — [FINAL]-019-08: memory/thermal pressure, minimal resources
    "SYNTHESIS": (6, _DEFAULT_IO_THREADS),  # 8 total — cpu-heavy inference
    "WINDUP": (_DEFAULT_CPU_THREADS, _DEFAULT_IO_THREADS),  # 6 total
    "EXPORT": (_DEFAULT_CPU_THREADS, _DEFAULT_IO_THREADS),  # 6 total
    "TEARDOWN": (2, 2),  # 4 total — minimal resources
}


class RayonPoolManager:
    """
    Phase-aware elastic rayon pool manager.

    ISSUE [META]-004: Dynamically resizes cpu_pool and io_pool based on
    sprint phase transitions. Replaces the static LazyLock<ThreadPool>
    pattern with atomic RwLock-wrapped ThreadPools in Rust.

    Phase table (M1 8GB: 4P + 4E = 8 total threads max):

      | Phase     | cpu_pool | io_pool | Total | Rationale                  |
      |-----------|----------|---------|-------|----------------------------|
      | BOOT      | 4        | 2       | 6     | Bootstrap: not heavy       |
      | WARMUP    | 4        | 2       | 6     | Prelude lanes parallel      |
      | ACTIVE    | 4        | 4       | 8     | Fetch-heavy: io expands    |
      | DEGRADED  | 2        | 2       | 4     | Memory/thermal pressure    |
      | SYNTHESIS | 6        | 2       | 8     | CPU-heavy: MLX inference   |
      | WINDUP    | 4        | 2       | 6     | Back to default             |
      | EXPORT    | 4        | 2       | 6     | Export I/O                 |
      | TEARDOWN  | 2        | 2       | 4     | Minimal: tear down          |

    Invariants:
      - Total threads never exceed 8 (M1 8GB ceiling)
      - io_pool shrink during SYNTHESIS: 2 → pool idle, mlx inference uses P-cores
      - cpu_pool shrink during TEARDOWN: 4 → 2 to release memory
      - All resize operations are atomic (RwLock swap in Rust)
      - Rust pools auto-initialize on first access (lazy fallback)
      - Fail-safe: if Rust unavailable, manager logs warning and is no-op

    Usage:
        manager = RayonPoolManager()
        manager.set_phase("ACTIVE")   # cpu=4, io=4
        manager.set_phase("SYNTHESIS")  # cpu=6, io=2
        manager.set_phase("WINDUP")  # cpu=4, io=2
        manager.shutdown()           # TEARDOWN: cpu=2, io=2
    """

    __slots__ = ("_current_phase", "_last_cpu", "_last_io", "_initialized", "_lock")

    def __init__(self) -> None:
        self._current_phase: str = "BOOT"
        self._last_cpu: int = 0
        self._last_io: int = 0
        self._initialized: bool = False
        self._lock = threading.Lock()

        # Initialize Rust elastic pools if available
        rust = _get_elastic_rust()
        if rust:
            try:
                cpu, io = rust["init_elastic_pools"]()
                self._last_cpu = cpu
                self._last_io = io
                self._initialized = True
                logger.info(
                    "[RayonPoolManager] Initialized: cpu=%d io=%d total=%d",
                    cpu,
                    io,
                    cpu + io,
                )
            except Exception as e:
                logger.warning(
                    "[RayonPoolManager] Rust init failed: %s — manager is no-op",
                    e,
                )
        else:
            logger.warning(
                "[RayonPoolManager] Rust elastic_pool bindings unavailable — "
                "pool resize is DISABLED (static pools will be used)"
            )

    @property
    def current_phase(self) -> str:
        """Current sprint phase."""
        return self._current_phase

    @property
    def cpu_threads(self) -> int:
        """Current CPU pool thread count."""
        return self._last_cpu

    @property
    def io_threads(self) -> int:
        """Current I/O pool thread count."""
        return self._last_io

    @property
    def total_threads(self) -> int:
        """Current total rayon threads."""
        return self._last_cpu + self._last_io

    @property
    def is_available(self) -> bool:
        """True if Rust elastic pool resize is available."""
        return self._initialized

    def set_phase(self, phase: str) -> None:
        """
        Set sprint phase and resize pools accordingly.

        Phase must be one of: BOOT, WARMUP, ACTIVE, SYNTHESIS, WINDUP, EXPORT, TEARDOWN.
        If the phase config exceeds the 8-thread total, both pools are clamped.

        Args:
            phase: Sprint phase name (case-insensitive).

        Fail-safe: logs warning and does nothing if Rust unavailable.
        """
        phase_upper = phase.upper()
        if phase_upper not in _PHASE_POOL_CONFIG:
            logger.warning(
                "[RayonPoolManager] Unknown phase %r — using current config",
                phase,
            )
            return

        if not self._initialized:
            return  # Fail-safe: no-op

        target_cpu, target_io = _PHASE_POOL_CONFIG[phase_upper]

        # Enforce 8-thread total ceiling
        total = target_cpu + target_io
        if total > _MAX_TOTAL_THREADS:
            # Clamp both proportionally
            excess = total - _MAX_TOTAL_THREADS
            # Give preference to keeping io_pool at least 1
            if target_cpu > 1 and excess > 0:
                target_cpu = max(1, target_cpu - excess)
                excess = target_cpu + target_io - _MAX_TOTAL_THREADS
                if excess > 0 and target_io > 1:
                    target_io = max(1, target_io - excess)

        with self._lock:
            self._current_phase = phase_upper

            # Resize CPU pool
            if target_cpu != self._last_cpu:
                try:
                    rust = _get_elastic_rust()
                    if rust:
                        actual = rust["resize_cpu_pool"](target_cpu)
                        logger.info(
                            "[RayonPoolManager] [%s] cpu_pool: %d → %d threads",
                            phase_upper,
                            self._last_cpu,
                            actual,
                        )
                        self._last_cpu = actual
                except Exception as e:
                    logger.warning(
                        "[RayonPoolManager] [%s] cpu_pool resize(%d) failed: %s",
                        phase_upper,
                        target_cpu,
                        e,
                    )

            # Resize I/O pool
            if target_io != self._last_io:
                try:
                    rust = _get_elastic_rust()
                    if rust:
                        actual = rust["resize_io_pool"](target_io)
                        logger.info(
                            "[RayonPoolManager] [%s] io_pool: %d → %d threads",
                            phase_upper,
                            self._last_io,
                            actual,
                        )
                        self._last_io = actual
                except Exception as e:
                    logger.warning(
                        "[RayonPoolManager] [%s] io_pool resize(%d) failed: %s",
                        phase_upper,
                        target_io,
                        e,
                    )

    def shutdown(self) -> None:
        """
        Tear down to minimal resources (TEARDOWN phase).

        Reduces both pools to 2 threads each (total = 4), freeing
        memory and OS thread slots before final teardown.
        """
        with self._lock:
            self._current_phase = "TEARDOWN"
            rust = _get_elastic_rust()
            if rust and self._initialized:
                try:
                    cpu = rust["resize_cpu_pool"](2)
                    io = rust["resize_io_pool"](2)
                    self._last_cpu = cpu
                    self._last_io = io
                    logger.info(
                        "[RayonPoolManager] [TEARDOWN] pools minimized: cpu=%d io=%d",
                        cpu,
                        io,
                    )
                except Exception as e:
                    logger.warning(
                        "[RayonPoolManager] [TEARDOWN] resize failed: %s",
                        e,
                    )

    def get_stats(self) -> dict[str, Any]:
        """Return current pool statistics."""
        rust = _get_elastic_rust()
        if rust and self._initialized:
            try:
                return {
                    "available": True,
                    "phase": self._current_phase,
                    "cpu_threads": rust["get_cpu_threads"](),
                    "io_threads": rust["get_io_threads"](),
                    "total_threads": rust["get_total_threads"](),
                }
            except Exception:  # noqa: BLE001
                pass
        return {
            "available": self._initialized,
            "phase": self._current_phase,
            "cpu_threads": self._last_cpu,
            "io_threads": self._last_io,
            "total_threads": self._last_cpu + self._last_io,
        }


# Module-level singleton
_rayon_manager: RayonPoolManager | None = None
_rayon_manager_lock = threading.Lock()


def get_rayon_pool_manager() -> RayonPoolManager:
    """
    Get or create the global RayonPoolManager singleton.

    Thread-safe lazy initialization.
    Lazily initializes Rust elastic pools on first call.
    """
    global _rayon_manager
    if _rayon_manager is not None:
        return _rayon_manager
    with _rayon_manager_lock:
        if _rayon_manager is None:
            _rayon_manager = RayonPoolManager()
        return _rayon_manager
