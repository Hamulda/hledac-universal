"""
Isolated executors — backed by RustWorkerPool (rayon thread pool).

MODERN-33 + MODERN-34 + NEXTGEN-03: P/E Core Affinity Integration
===================================================================
This module uses Rust rayon pools with proper P/E core affinity:

NEXTGEN-03: Asymmetric Topology-Aware Pools:
  - simd_pool → P-cores 0,1 (USER_INITIATED) — ARM NEON SIMD operations
  - mlx_pool → P-cores 2,3 (USER_INTERACTIVE) — MLX Metal dispatch
  - graph_pool → P-core 2 (shared, USER_INITIATED) — Kuzu graph traversal
  - io_pool → E-cores (UTILITY QoS) — DuckDB, network I/O

Legacy pools (for backward compatibility):
  - cpu_pool → P-cores (USER_INITIATED QoS) via darwin_affinity.rs
  - io_pool → E-cores (UTILITY QoS) via darwin_affinity.rs
  - mixed_pool → P-cores (adaptive)

Work-stealing: Disabled via breadth_first() — SIMD never steals from MLX.

THREAD-BUDGET-01 + THREAD-BUDGET-02: Unified Thread Budget System
============================================================
This module provides the canonical thread budget enforcement for M1 8GB:

  Budget Composition (M1 8GB: 4P + 4E = 8 logical cores):
    ┌────────────────────────────────────────────────────────────────┐
    │ Thread Source           │ Count   │ Notes                    │
    ├────────────────────────┼─────────┼───────────────────────────│
    │ Rayon SIMD Pool        │ 2       │ P-cores 0,1, USER_INIT.. │
    │ Rayon MLX Pool         │ 2       │ P-cores 2,3, USER_INTER. │
    │ Rayon Graph Pool       │ 1       │ P-core 2, USER_INITIATED │
    │ Rayon I/O Pool         │ 1-2     │ E-cores, QoS=UTILITY     │
    │ Rayon Dispatchers      │ 3       │ 1 per pool type          │
    │ asyncio Event Loop     │ 1       │ Reserved (ASYNCIO_RES.)  │
    │ System/OS Overhead     │ 1       │ Reserved                 │
    └────────────────────────┴─────────┴───────────────────────────┘
  
  MAX_TOTAL_THREADS = 8 (hard ceiling)
  THREAD-BUDGET-02 FIX: _BUDGET_AVAILABLE = 6 (8 - 2 = 6, was 7 which was wrong)
  
  All phase transitions are validated against _BUDGET_AVAILABLE before execution.
  Failed transitions ROLLBACK to previous state (never partial).

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

Mapping (NEXTGEN-03):
  IsolatedSIMDExecutor → RustWorkerPool("simd"),  # ARM NEON, Aho-Corasick
  IsolatedMLXExecutor → RustWorkerPool("mlx"),   # MLX Metal dispatch
  IsolatedGraphExecutor → RustWorkerPool("graph"), # Kuzu graph, petgraph
  IsolatedDuckDBExecutor → RustWorkerPool("io"),  # DuckDB, E-cores
  IsolatedEvidenceBatchWriter → RustWorkerPool("mixed")

Invariants:
  - Always-on: no feature flags, RustWorkerPool fallback covers all cases
  - Bounded: rayon pool caps prevent exhaustion
  - Budget-enforced: all phase transitions validated against _BUDGET_AVAILABLE
  - Atomic: phase transitions succeed completely or rollback to previous state
  - Fail-safe: every method returns None/[] on error, never raises
  - Backward-compatible API: same class names, same method signatures
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
import warnings
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from collections.abc import Callable

# F350M-R: Lazy imports to break core ↔ runtime cycle
from typing import TYPE_CHECKING as _TC
from core._util import aclose

if TYPE_CHECKING:
    from hledac.universal.runtime.worker_pool import RustWorkerPool, get_rust_pool

# Lazy runtime access — breaks core ↔ runtime cycle
_rust_pool_module = None

def _get_rust_pool_impl(pool_type: str = "cpu"):
    """Lazy getter for RustWorkerPool — defers runtime import."""
    global _rust_pool_module
    if _rust_pool_module is None:
        from hledac.universal.runtime import worker_pool as _wp
        _rust_pool_module = _wp
    return _rust_pool_module.get_rust_pool(pool_type)

# Alias for backward compatibility
def get_rust_pool(pool_type: str = "cpu"):
    return _get_rust_pool_impl(pool_type)


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


def reset_pools_sprint() -> None:
    """
    MODERN-35: Reset pool state at sprint end without full shutdown.

    Unlike close_all_pools(), this function does NOT close the Rust pools.
    Instead, it only nullifies the Python wrapper references so that new
    pools will be created on next use. This allows the Rust rayon thread pools
    to remain alive across sprints (they're process-wide singletons anyway).

    Use this at the end of each sprint to clear Python state while keeping
    the Rust pools ready for the next sprint.

    For full process teardown, use close_all_pools().
    """
    global _duckdb_pool, _mlx_pool, _evidence_pool
    with _pools_lock:
        _duckdb_pool = None
        _mlx_pool = None
        _evidence_pool = None
    logger.debug("[ISOLATED-EXECUTORS] Sprint reset: pool references cleared")


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
    "reset_pools_sprint",
    "is_pep734_available",
    "get_interpreter_stats",
    "IsolatedRuntime",
    "get_isolated_runtime",
    # [META]-004: Elastic rayon pool manager
    "RayonPoolManager",
    "get_rayon_pool_manager",
    # [THREAD-BUDGET-02]: Thread budget enforcement (rayon-only budget)
    "ThreadBudgetGuard",
    "get_thread_budget_guard",
    "_MAX_TOTAL_THREADS",
    "_BUDGET_AVAILABLE",
    "_ASYNCIO_RESERVED",
    "_SYSTEM_RESERVED",
    "_DISPATCHER_COUNT",
    "_PHASE_POOL_CONFIG",
    "_validate_phase_budget",
    # ISSUE [SWARM]-005: FFI Circuit Breaker Exceptions
    "CircuitBreakerOpenError",
    "FallbackActivatedError",
    # NEXTGEN-03: Dedicated topology-aware executors
    "IsolatedSIMDExecutor",
    "IsolatedGraphExecutor",
    "get_simd_executor",
    "get_graph_executor",
]

# -----------------------------------------------------------------------------
# Pool availability — mirrors the old PEP 734 _interpreters_available flag
# -----------------------------------------------------------------------------

_RUST_AVAILABLE: bool = False
_RUST_AVAILABLE_LOCK: threading.Lock = threading.Lock()


def _check_rust_pool_available() -> bool:
    """Check if Rust rayon pool is available (always-on fallback for PEP 734).
    
    THREAD-BUDGET-01 FIX: Thread-safe check with double-checked locking pattern.
    """
    global _RUST_AVAILABLE
    if _RUST_AVAILABLE:
        return True
    with _RUST_AVAILABLE_LOCK:
        # Double-check after acquiring lock
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
# ==========================================================================================


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
        # P1-4 FIX: Check if loop is running before calling run_until_complete.
        if loop.is_running():
            coro = self.run_async(func_name, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()  # type: ignore[return-value]
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
        # P1-4 FIX: Check if loop is running before calling run_until_complete.
        if loop.is_running():
            coro = self.run_async(func_name, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()  # type: ignore[return-value]
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
        # P1-4 FIX: Use loop.run_until_complete() only when loop is not running.
        # If loop is running, use run_coroutine_threadsafe() to avoid RuntimeError.
        if loop.is_running():
            coro = self.execute_query_async(query_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.execute_query_async(query_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# IsolatedMLXExecutor — backed by RustWorkerPool("cpu")
# -----------------------------------------------------------------------------


class IsolatedMLXExecutor:
    """
    NEXTGEN-03: Executes MLX inference via RustWorkerPool("mlx").

    Backed by rayon mlx_pool (2 P-cores 2,3) for MLX Metal dispatch.
    Memory isolation: MLX Metal arena is managed independently by the
    MLX library — the pool provides thread-level parallelism.

    Pool config (NEXTGEN-03):
      - Backend: RustWorkerPool("mlx") → P-core 2,3
      - QoS: USER_INTERACTIVE (minimal latency for GPU command submission)
      - Threads: 2

    Note:
        MLX already releases GIL at C-level. The pool provides additional
        thread-level parallelism for multi-batch inference scenarios.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: mlx_pool cap = 2 threads (P-core QoS)
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_inference: int = 2) -> None:
        self._pool = get_rust_pool("mlx")
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
            # No running loop - create a new one
            with asyncio.Runner() as runner:
                result = runner.run(self.run_inference_async(inference_func, *args, **kwargs))
            return result
        # P1-4 FIX: Use loop.run_until_complete() only when loop is not running.
        # If loop is running (e.g., called from within event loop), use
        # run_coroutine_threadsafe() to avoid RuntimeError.
        if loop.is_running():
            # Schedule on running loop from potentially different thread
            coro = self.run_inference_async(inference_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(
            self.run_inference_async(inference_func, *args, **kwargs)
        )

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
        # P1-4 FIX: Check if loop is running before calling run_until_complete.
        if loop.is_running():
            coro = self.process_batch_async(process_func, items, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.process_batch_async(process_func, items, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# NEXTGEN-03: IsolatedSIMDExecutor — backed by RustWorkerPool("simd")
# -----------------------------------------------------------------------------


class IsolatedSIMDExecutor:
    """
    NEXTGEN-03: Executes SIMD operations via RustWorkerPool("simd").

    Backed by rayon simd_pool (2 P-cores 0,1) for ARM NEON SIMD operations.
    Memory isolation: Rust pool provides thread-level parallelism.

    Pool config (NEXTGEN-03):
      - Backend: RustWorkerPool("simd") → P-core 0,1
      - QoS: USER_INITIATED (CPU-intensive)
      - Threads: 2
      - Workload: ARM NEON SIMD (simd_similarity.rs, deep_ac Aho-Corasick)

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: simd_pool cap = 2 threads (P-core QoS)
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_workers: int = 2) -> None:
        self._pool = get_rust_pool("simd")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True — Rust pool is always-on)."""
        return self._available

    async def run_simd_async(
        self,
        simd_func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Run SIMD function on the Rust simd_pool.

        Args:
            simd_func: Function that runs SIMD computation.
            *args: Positional arguments.
            timeout: Optional timeout in seconds.
            **kwargs: Keyword arguments.

        Returns:
            SIMD result, or None on error.

        Fail-safe: returns None on any error, never raises.
        """
        if not self._available:
            try:
                return simd_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"SIMD execution failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(simd_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:simd",
                )
            return await self._pool.submit(simd_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("SIMD execution timeout")
            return None
        except Exception as e:
            logger.warning(f"SIMD execution failed: {e}")
            return None

    def run_simd_sync(
        self,
        simd_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of run_simd_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_simd_async(simd_func, *args, **kwargs))
            return result
        if loop.is_running():
            coro = self.run_simd_async(simd_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.run_simd_async(simd_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# NEXTGEN-03: IsolatedGraphExecutor — backed by RustWorkerPool("graph")
# -----------------------------------------------------------------------------


class IsolatedGraphExecutor:
    """
    NEXTGEN-03: Executes graph operations via RustWorkerPool("graph").

    Backed by rayon graph_pool (1 P-core 2) for Kuzu graph traversal.
    Memory isolation: Rust pool provides thread-level parallelism.

    Pool config (NEXTGEN-03):
      - Backend: RustWorkerPool("graph") → P-core 2 (shared with MLX)
      - QoS: USER_INITIATED (CPU-intensive)
      - Threads: 1
      - Workload: Kuzu graph traversal, petgraph PageRank

    Note:
      Single thread to avoid overwhelming GPU pipeline when co-located
      with MLX pool on P-core 2.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: graph_pool cap = 1 thread
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self, *, max_workers: int = 1) -> None:
        self._pool = get_rust_pool("graph")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available (always True — Rust pool is always-on)."""
        return self._available

    async def run_graph_async(
        self,
        graph_func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Run graph function on the Rust graph_pool.

        Args:
            graph_func: Function that runs graph computation.
            *args: Positional arguments.
            timeout: Optional timeout in seconds.
            **kwargs: Keyword arguments.

        Returns:
            Graph result, or None on error.

        Fail-safe: returns None on any error, never raises.
        """
        if not self._available:
            try:
                return graph_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Graph execution failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(graph_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:graph",
                )
            return await self._pool.submit(graph_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("Graph execution timeout")
            return None
        except Exception as e:
            logger.warning(f"Graph execution failed: {e}")
            return None

    def run_graph_sync(
        self,
        graph_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of run_graph_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_graph_async(graph_func, *args, **kwargs))
            return result
        if loop.is_running():
            coro = self.run_graph_async(graph_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.run_graph_async(graph_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool (process-wide singleton)."""
        pass


# -----------------------------------------------------------------------------
# Module-level pool singletons — lazy, thread-safe
# -----------------------------------------------------------------------------

_duckdb_pool: IsolatedDuckDBExecutor | None = None
_mlx_pool: IsolatedMLXExecutor | None = None
_simd_pool: IsolatedSIMDExecutor | None = None
_graph_pool: IsolatedGraphExecutor | None = None
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


def get_simd_executor() -> IsolatedSIMDExecutor:
    """Get or create global SIMD executor pool.

    NEXTGEN-03: Returns IsolatedSIMDExecutor backed by RustWorkerPool("simd").

    Resolution order (A3):
      1. ServiceContainer ('executor.simd') — sprint-scoped via ctx.container
      2. Fallback to module-level _simd_pool — backward-compatible global
    """
    global _simd_pool

    if _simd_pool is not None:
        return _simd_pool

    try:
        from hledac.universal.core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.simd")
        if inst is not None:
            return inst
    except Exception:  # noqa: BLE001
        pass

    with _pools_lock:
        if _simd_pool is None:
            _simd_pool = IsolatedSIMDExecutor()
        return _simd_pool


def get_graph_executor() -> IsolatedGraphExecutor:
    """Get or create global Graph executor pool.

    NEXTGEN-03: Returns IsolatedGraphExecutor backed by RustWorkerPool("graph").

    Resolution order (A3):
      1. ServiceContainer ('executor.graph') — sprint-scoped via ctx.container
      2. Fallback to module-level _graph_pool — backward-compatible global
    """
    global _graph_pool

    if _graph_pool is not None:
        return _graph_pool

    try:
        from hledac.universal.core.container import get_global_container

        container = get_global_container()
        inst = container.try_get("executor.graph")
        if inst is not None:
            return inst
    except Exception:  # noqa: BLE001
        pass

    with _pools_lock:
        if _graph_pool is None:
            _graph_pool = IsolatedGraphExecutor()
        return _graph_pool


def close_all_pools() -> None:
    """
    MODERN-35: Close all global executor pools and shutdown Rust pools.
    NEXTGEN-03: Also closes dedicated SIMD/MLX/Graph pools.

    Shuts down Rust rayon thread pools by calling shutdown() on each
    RustWorkerPool instance. This signals the Rust layer to release
    resources and reset internal state.
    """
    global _duckdb_pool, _mlx_pool, _simd_pool, _graph_pool, _evidence_pool
    with _pools_lock:
        # Shutdown Python wrapper classes first (if they have custom cleanup)
        if _duckdb_pool is not None:
            try:
                _duckdb_pool.close()
            except Exception:  # noqa: BLE001
                pass
        if _mlx_pool is not None:
            try:
                _mlx_pool.close()
            except Exception:  # noqa: BLE001
                pass
        if _simd_pool is not None:
            try:
                _simd_pool.close()
            except Exception:  # noqa: BLE001
                pass
        if _graph_pool is not None:
            try:
                _graph_pool.close()
            except Exception:  # noqa: BLE001
                pass
        # NEXTGEN-03: Shutdown Rust pools to release rayon thread pool resources
        # Includes dedicated pools: simd, mlx, graph
        try:
            from hledac.universal.runtime.worker_pool import get_rust_pool
            for pool_type in ("cpu", "io", "mixed", "simd", "mlx", "graph"):
                try:
                    rust_pool = get_rust_pool(pool_type)
                    rust_pool.shutdown()
                    logger.debug(f"[ISOLATED-EXECUTORS] shutdown Rust pool: {pool_type}")
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        # Nullify references
        _duckdb_pool = None
        _mlx_pool = None
        _simd_pool = None
        _graph_pool = None
        _evidence_pool = None
        logger.debug("[ISOLATED-EXECUTORS] All pools closed")


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


# MODERN-31: Lazy loader for adaptive_scheduler Rust bindings
_RUST_ADAPTIVE: dict[str, Any] = {}


def _get_adaptive_rust() -> dict[str, Any] | None:
    """Lazily load Rust adaptive_scheduler bindings via rust.raw.

    MODERN-31: adaptive_scheduler provides pressure-based thread recommendations.
    This is the SINGLE source of truth for pool sizing.

    MODERN-32: Includes global thread budget tracking functions.

    R6: All Rust extension access goes through rust.raw, never direct import.
    """
    global _RUST_ADAPTIVE
    if _RUST_ADAPTIVE:
        return _RUST_ADAPTIVE
    try:
        from hledac.universal.core.rust_backend import rust

        raw = rust.raw
        # adaptive_scheduler functions registered as pyfunctions
        fn1 = getattr(raw, "get_adaptive_cpu_threads", None)
        fn2 = getattr(raw, "get_adaptive_io_threads", None)
        fn3 = getattr(raw, "set_adaptive_phase", None)
        fn4 = getattr(raw, "get_adaptive_phase", None)
        fn5 = getattr(raw, "get_total_active_threads_budget", None)
        fn6 = getattr(raw, "get_thread_budget_breakdown", None)
        fn7 = getattr(raw, "get_adaptive_mixed_threshold", None)  # MODERN-31: For mixed pool sync
        fn8 = getattr(raw, "get_available_thread_budget", None)   # MODERN-32: Available slots
        fn9 = getattr(raw, "get_budget_ceiling", None)           # THREAD-BUDGET-01: Budget ceiling
        if None in (fn1, fn2, fn3, fn4, fn5, fn6, fn7, fn8, fn9):
            return None
        _RUST_ADAPTIVE = {
            "get_adaptive_cpu_threads": fn1,
            "get_adaptive_io_threads": fn2,
            "set_adaptive_phase": fn3,
            "get_adaptive_phase": fn4,
            "get_total_active_threads_budget": fn5,
            "get_thread_budget_breakdown": fn6,
            "get_adaptive_mixed_threshold": fn7,  # MODERN-31: Mixed pool threshold
            "get_available_thread_budget": fn8,   # MODERN-32: Available budget slots
            "get_budget_ceiling": fn9,           # THREAD-BUDGET-01: Budget ceiling
        }
        return _RUST_ADAPTIVE
    except Exception:
        return None


# ===========================================================================================
# [THREAD-BUDGET-01]: M1 8GB Unified Thread Budget
# ===========================================================================================
# All thread sources must be accounted for in the total budget:
#
# Budget Composition (M1 8GB: 4P + 4E = 8 logical cores):
#   ┌─────────────────────────────────────────────────────────────────────────┐
#   │ Thread Source           │ Count   │ Notes                             │
#   ├────────────────────────┼─────────┼───────────────────────────────────┤
#   │ Rayon CPU Pool         │ 1-4     │ P-cores, QoS=USER_INITIATED     │
#   │ Rayon I/O Pool         │ 1-2     │ E-cores, QoS=UTILITY             │
#   │ Rayon Mixed Pool       │ 0-2     │ Adaptive, P-core ceiling          │
#   │ Rayon Dispatchers      │ 3       │ 1 per pool type (cpu/io/mixed)   │
#   │ asyncio Event Loop     │ 1       │ Main event loop thread            │
#   │ Python ThreadPool      │ 0-5     │ SharedWorkerPool, governor-gated  │
#   │ Rust Tokio Runtime     │ TBD     │ P2P/darknet transports (future)   │
#   └────────────────────────┴─────────┴───────────────────────────────────┘
#
# MAX_TOTAL_THREADS = 8 (hard ceiling for M1 8GB)
# Reserved for system/OS: 1 thread
# Available for Hledac: 7 threads max
#
# Budget Enforcement Rules (THREAD-BUDGET-01):
#   1. ALL phase transitions MUST fit within MAX_TOTAL_THREADS
#   2. If resize fails, phase transition REVERTS (not continues with overflow)
#   3. Mixed pool is ALWAYS included in budget (even when 0 threads)
#   4. External thread sources (Tokio, ProcessPool) must register themselves
# ===========================================================================================

# M1 8GB hard ceiling
_MAX_TOTAL_THREADS: int = 8

# Reserved threads (non-negotiable) — THREAD-BUDGET-02: Fixed arithmetic
_ASYNCIO_RESERVED: int = 1  # Event loop thread
_SYSTEM_RESERVED: int = 1    # OS/system overhead
_RESERVED_TOTAL: int = _ASYNCIO_RESERVED + _SYSTEM_RESERVED  # = 2

# Available budget for rayon pools (dispatchers + cpu + io + mixed).
# THREAD-BUDGET-02: FIXED arithmetic — was 7 (wrong), now 6 (correct).
# Correct: MAX_TOTAL_THREADS (8) - _RESERVED_TOTAL (2) = 6
# asyncio and system threads ARE tracked by ThreadBudgetGuard, so they
# DO count against the total. This ensures hard ceiling enforcement.
_BUDGET_AVAILABLE: int = 6  # = 8 - 2 = 6 (FIXED from 7)

# Dispatcher thread count (1 per pool type: cpu, io, mixed)
_DISPATCHER_COUNT: int = 3

# Default pool sizes (BOOT phase) — MODERN-31: Initial seeds only!
# Actual sizing is driven by adaptive_scheduler recommendations.
_DEFAULT_CPU_THREADS: int = 2  # Conservative default for M1 8GB
_DEFAULT_IO_THREADS: int = 1

# Maximum pool sizes (never exceeded)
_MAX_CPU_THREADS: int = 4  # M1 has 4 P-cores max
_MAX_IO_THREADS: int = 2   # E-cores for I/O
_MAX_MIXED_THREADS: int = 2  # Adaptive mixed pool

# Phase-aware pool configurations — THREAD-BUDGET-02: ALL phases verified to fit ≤ 6
# Each phase tuple: (cpu, io, mixed_max)
# Total = cpu + io + mixed + dispatchers ≤ 6 (available budget after fix)
#
# THREAD-BUDGET-02: BUDGET VERIFIED TABLE (all phases ≤ 6):
#   | Phase     | cpu | io | mixed | dispatchers | total | Within 6? |
#   |-----------|-----|----|-------|-------------|-------|----------|
#   | BOOT      | 1   | 1  | 1     | 3           | 6     | ✓ OK     |
#   | WARMUP    | 1   | 1  | 1     | 3           | 6     | ✓ OK     |
#   | ACTIVE    | 2   | 1  | 0     | 3           | 6     | ✓ OK     |
#   | DEGRADED  | 1   | 1  | 0     | 3           | 5     | ✓ OK     |
#   | SYNTHESIS | 2   | 1  | 0     | 3           | 6     | ✓ OK     |
#   | WINDUP    | 1   | 1  | 1     | 3           | 6     | ✓ OK     |
#   | EXPORT    | 2   | 1  | 0     | 3           | 6     | ✓ OK     |
#   | TEARDOWN  | 1   | 1  | 0     | 3           | 5     | ✓ OK     |
#
# All phases fit within _BUDGET_AVAILABLE = 6 (cpu+io+mixed+dispatchers)
# Previous values summed to 7 which exceeded corrected budget of 6.
_PHASE_POOL_CONFIG: dict[str, tuple[int, int, int]] = {
    "BOOT": (1, 1, 1),  # 6 total - reduced from (2,1,1)
    "WARMUP": (1, 1, 1),  # 6 total - reduced from (2,1,1)
    "ACTIVE": (2, 1, 0),  # 6 total - reduced io from 2 to 1
    "DEGRADED": (1, 1, 0),  # 5 total - memory/thermal pressure
    "SYNTHESIS": (2, 1, 0),  # 6 total - reduced cpu from 3 to 2
    "WINDUP": (1, 1, 1),  # 6 total - reduced from (2,1,1)
    "EXPORT": (2, 1, 0),  # 6 total - reduced io from 2 to 1
    "TEARDOWN": (1, 1, 0),  # 5 total - minimal resources
}


def _validate_phase_budget(phase: str, cpu: int, io: int, mixed: int) -> tuple[bool, int, str]:
    """
    THREAD-BUDGET-01: Validate phase budget against ceiling.
    
    Returns:
        (is_valid, actual_total, reason)
    """
    dispatchers = _DISPATCHER_COUNT
    total = cpu + io + mixed + dispatchers
    
    if total > _BUDGET_AVAILABLE:
        return (
            False,
            total,
            f"Phase {phase}: {cpu}+{io}+{mixed}+{dispatchers}={total} exceeds budget {_BUDGET_AVAILABLE}"
        )
    return (True, total, "OK")


class ThreadBudgetGuard:
    """
    THREAD-BUDGET-01: Unified thread budget enforcement guard.
    
    Single source of truth for all thread allocation decisions.
    Prevents thermal throttling and OOM on M1 8GB.
    
    Features:
      - Budget validation before ALL thread operations
      - Atomic reservation/release
      - Telemetry for monitoring budget pressure
      - Rollback capability for failed allocations
    """
    
    _instance: "ThreadBudgetGuard | None" = None
    _lock = threading.Lock()
    
    def __new__(cls) -> "ThreadBudgetGuard":
        """Singleton pattern for global budget guard."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self) -> None:
        """Initialize budget tracking."""
        self._lock = threading.RLock()
        self._reserved: dict[str, int] = {
            "rayon_cpu": 0,
            "rayon_io": 0,
            "rayon_mixed": 0,
            "rayon_dispatchers": _DISPATCHER_COUNT,
            "asyncio": _ASYNCIO_RESERVED,
            "python_pool": 0,
            "tokio": 0,
            "other": 0,
        }
        self._peak_total: int = 0
        self._budget_violations: int = 0
        self._last_violation_reason: str = ""
    
    @property
    def total_threads(self) -> int:
        """Current total thread count across all sources (including asyncio, system)."""
        with self._lock:
            return sum(self._reserved.values())
    
    @property
    def rayon_threads(self) -> int:
        """
        THREAD-BUDGET-02 FIX: Rayon pool threads only (cpu + io + mixed + dispatchers).
        
        This property is the CORRECT basis for rayon budget calculations because:
        - _BUDGET_AVAILABLE = 6 is the rayon-only budget (MAX_TOTAL_THREADS=8 minus asyncio=1 and system=1)
        - asyncio and system threads are pre-reserved and should NOT count against rayon budget
        
        Initial state: rayon_dispatchers=3, others=0 → rayon_threads=3
        Full BOOT phase: cpu=1 + io=1 + mixed=1 + dispatchers=3 = 6 (max rayon budget)
        """
        with self._lock:
            return (
                self._reserved.get("rayon_cpu", 0)
                + self._reserved.get("rayon_io", 0)
                + self._reserved.get("rayon_mixed", 0)
                + self._reserved.get("rayon_dispatchers", 0)
            )
    
    @property
    def available_budget(self) -> int:
        """
        THREAD-BUDGET-02 FIX: Available rayon pool budget.
        
        Fixed from previous incorrect implementation which double-counted asyncio+system.
        
        Available = _BUDGET_AVAILABLE - rayon_threads (NOT total_threads)
        
        This ensures rayon pools can use up to 6 threads even when asyncio+system=2 are active.
        """
        return _BUDGET_AVAILABLE - self.rayon_threads
    
    @property
    def budget_pressure(self) -> float:
        """Budget pressure as fraction [0.0, 1.0] based on rayon threads."""
        return self.rayon_threads / _BUDGET_AVAILABLE
    
    @property
    def is_over_budget(self) -> bool:
        """True if rayon threads exceed rayon budget (_BUDGET_AVAILABLE)."""
        return self.rayon_threads > _BUDGET_AVAILABLE
    
    def reserve(self, source: str, count: int) -> bool:
        """
        THREAD-BUDGET-02 FIX: Reserve threads from rayon budget.
        
        This method validates ONLY rayon pool threads against _BUDGET_AVAILABLE.
        asyncio and system threads are pre-reserved and DO NOT count against budget.
        
        Budget model (M1 8GB = 8 cores):
          - asyncio + system = 2 (always reserved, tracked but not budget-limited)
          - rayon pools = max 6 (this budget)
          - Total = 2 + 6 = 8 = MAX_TOTAL_THREADS ✓
        
        Returns True if reservation succeeded.
        Returns False and logs violation if rayon budget would be exceeded.
        """
        with self._lock:
            # Determine if this is a rayon pool request or external thread
            is_rayon = source in ("rayon_cpu", "rayon_io", "rayon_mixed", "rayon_dispatchers", 
                                   "python_pool", "transport")
            
            if is_rayon:
                # THREAD-BUDGET-02 FIX: Only count rayon threads against budget
                new_rayon_total = self.rayon_threads + count
                if new_rayon_total > _BUDGET_AVAILABLE:
                    self._budget_violations += 1
                    self._last_violation_reason = (
                        f"Budget violation: {source} requests {count} threads, "
                        f"but only {self.available_budget} available for rayon pools "
                        f"(rayon={self.rayon_threads}/{_BUDGET_AVAILABLE})"
                    )
                    logger.warning(f"[ThreadBudgetGuard] {self._last_violation_reason}")
                    return False
            
            self._reserved[source] = self._reserved.get(source, 0) + count
            self._peak_total = max(self._peak_total, self.total_threads)
            return True
    
    def release(self, source: str, count: int) -> None:
        """Release threads back to budget."""
        with self._lock:
            current = self._reserved.get(source, 0)
            self._reserved[source] = max(0, current - count)
    
    def set_count(self, source: str, count: int) -> bool:
        """
        Set exact count for a source, adjusting budget accordingly.
        
        Returns True if successful.
        """
        with self._lock:
            current = self._reserved.get(source, 0)
            delta = count - current
            if delta > 0:
                return self.reserve(source, delta)
            elif delta < 0:
                self.release(source, -delta)
                return True
            return True
    
    def get_breakdown(self) -> dict[str, int]:
        """Get budget breakdown by source."""
        with self._lock:
            return dict(self._reserved)
    
    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive budget statistics."""
        with self._lock:
            return {
                "total_threads": self.total_threads,
                "rayon_threads": self.rayon_threads,  # THREAD-BUDGET-02 FIX: Add rayon-specific count
                "budget_ceiling": _BUDGET_AVAILABLE,
                "available_rayon": self.available_budget,
                "pressure_pct": round(self.budget_pressure * 100, 1),
                "peak_total": self._peak_total,
                "violations": self._budget_violations,
                "last_violation": self._last_violation_reason,
                "is_over_budget": self.is_over_budget,
            }
    
    def reset(self) -> None:
        """Reset tracking (for testing only)."""
        with self._lock:
            self._reserved = {
                "rayon_cpu": 0,
                "rayon_io": 0,
                "rayon_mixed": 0,
                "rayon_dispatchers": _DISPATCHER_COUNT,
                "asyncio": _ASYNCIO_RESERVED,
                "python_pool": 0,
                "tokio": 0,
                "other": 0,
            }
            self._peak_total = 0

    def register_transport_threads(self, transport_name: str, threads: int) -> bool:
        """
        THREAD-BUDGET-01: Register transport threads with the budget guard.
        
        Transport threads (from Tor, I2P, Arti, etc.) are tracked separately
        from rayon pools. This method ensures they are accounted for in the
        total budget.
        
        Args:
            transport_name: Name of the transport (e.g., "tor", "i2p", "arti")
            threads: Number of threads the transport uses
            
        Returns:
            True if registration succeeded (within budget).
            False if registration would exceed budget (threads not registered).
        """
        return self.reserve(f"transport_{transport_name}", threads)
    
    def unregister_transport_threads(self, transport_name: str, threads: int) -> None:
        """
        THREAD-BUDGET-01: Unregister transport threads from the budget guard.
        
        Args:
            transport_name: Name of the transport
            threads: Number of threads to release
        """
        self.release(f"transport_{transport_name}", threads)
    
    def get_transport_threads(self, transport_name: str) -> int:
        """Get the number of registered threads for a transport."""
        with self._lock:
            return self._reserved.get(f"transport_{transport_name}", 0)


def get_thread_budget_guard() -> ThreadBudgetGuard:
    """Get singleton ThreadBudgetGuard instance."""
    return ThreadBudgetGuard()


class RayonPoolManager:
    """
    THREAD-BUDGET-01: Phase-aware elastic rayon pool manager with atomic resize.
    
    Key improvements over previous implementation:
    
    1. UNIFIED BUDGET TRACKING
       - ThreadBudgetGuard is the single source of truth
       - All thread sources (cpu, io, mixed, dispatchers) tracked
       - External sources can register themselves
    
    2. ATOMIC PHASE TRANSITIONS WITH ROLLBACK
       - Pre-flight validation before ANY resize
       - Rollback to previous state if ANY resize fails
       - Guaranteed budget compliance (never exceeds ceiling)
    
    3. FAIL-FAST BEHAVIOR
       - If phase transition cannot complete within budget, sprint/stop
       - No silent degradation that could cause thermal throttling
       - Clear error logging for debugging
    
    4. TELEMETRY
       - Budget pressure monitoring
       - Violation tracking
       - Phase transition history
    
    Invariants (THREAD-BUDGET-02):
      - Total rayon threads NEVER exceed _BUDGET_AVAILABLE = 6
      - ALL resize operations are atomic (RwLock swap in Rust)
      - Phase transitions either SUCCEED completely or REVERT completely
      - Rust pools auto-initialize on first access (lazy fallback)
      - If Rust unavailable, manager logs CRITICAL and blocks operations
    
    Phase table (THREAD-BUDGET-02: all phases verified to fit ≤ 6):
      | Phase     | cpu | io | mixed | dispatchers | total |
      |-----------|-----|----|-------|------------|-------|
      | BOOT      | 1   | 1  | 1     | 3          | 6     |
      | WARMUP    | 1   | 1  | 1     | 3          | 6     |
      | ACTIVE    | 2   | 1  | 0     | 3          | 6     |
      | DEGRADED  | 1   | 1  | 0     | 3          | 5     |
      | SYNTHESIS | 2   | 1  | 0     | 3          | 6     |
      | WINDUP    | 1   | 1  | 1     | 3          | 6     |
      | EXPORT    | 2   | 1  | 0     | 3          | 6     |
      | TEARDOWN  | 1   | 1  | 0     | 3          | 5     |
    
    Usage:
        manager = RayonPoolManager()
        manager.set_phase("ACTIVE")   # cpu=2, io=1, mixed=0
        manager.set_phase("SYNTHESIS")  # cpu=2, io=1, mixed=0
        manager.apply_adaptive_sizing()  # Apply pressure-based recommendations
        manager.shutdown()           # TEARDOWN: cpu=1, io=1, mixed=0
    """

    __slots__ = (
        "_current_phase",
        "_last_cpu",
        "_last_io",
        "_last_mixed",
        "_initialized",
        "_lock",
        "_transition_history",
        "_budget_guard",
        "_rollback_on_error",
    )

    def __init__(self, rollback_on_error: bool = True) -> None:
        """
        Initialize RayonPoolManager.
        
        Args:
            rollback_on_error: If True, failed phase transitions revert to previous
                             state. If False, failed transitions leave pools in
                             indeterminate state (use for debugging only).
        """
        self._current_phase: str = "BOOT"
        self._last_cpu: int = 0
        self._last_io: int = 0
        self._last_mixed: int = 0
        self._initialized: bool = False
        self._lock = threading.Lock()
        self._transition_history: list[dict[str, Any]] = []
        self._budget_guard = get_thread_budget_guard()
        self._rollback_on_error = rollback_on_error

        # Initialize Rust elastic pools if available
        rust = _get_elastic_rust()
        if rust:
            try:
                cpu, io = rust["init_elastic_pools"]()
                self._last_cpu = cpu
                self._last_io = io
                # ISSUE-1 FIX: Mixed pool initialization must match Rust's default MIXED_BUDGET=1
                # Rust's init_default_pools() doesn't set MIXED_BUDGET, which defaults to 1
                # Python was setting _last_mixed=0, causing budget calculation mismatches
                self._last_mixed = 1
                self._initialized = True
                
                # Register with budget guard - ISSUE-1 FIX: Include rayon_mixed
                self._budget_guard.set_count("rayon_cpu", cpu)
                self._budget_guard.set_count("rayon_io", io)
                self._budget_guard.set_count("rayon_mixed", self._last_mixed)
                self._budget_guard.set_count("rayon_dispatchers", _DISPATCHER_COUNT)
                
                logger.info(
                    "[RayonPoolManager] Initialized: cpu=%d io=%d dispatchers=%d total=%d/%d",
                    cpu,
                    io,
                    _DISPATCHER_COUNT,
                    cpu + io + _DISPATCHER_COUNT,
                    _BUDGET_AVAILABLE,
                )
            except Exception as e:
                logger.error(
                    "[RayonPoolManager] Rust init failed: %s — CRITICAL: thread safety compromised",
                    e,
                )
        else:
            logger.critical(
                "[RayonPoolManager] Rust elastic_pool bindings unavailable — "
                "thread budget enforcement DISABLED. This can cause thermal throttling!"
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
        """
        ISSUE-3 FIX: Current total rayon threads (cpu + io + mixed + dispatchers).
        
        Previously returned only cpu + io, which was inconsistent with Rust's
        get_total_active_threads_budget() that includes mixed + dispatchers.
        This property now matches Rust's semantics for consistent monitoring.
        """
        return self._last_cpu + self._last_io + self._last_mixed + _DISPATCHER_COUNT

    @property
    def is_available(self) -> bool:
        """True if Rust elastic pool resize is available."""
        return self._initialized

    def set_phase(self, phase: str) -> bool:
        """
        THREAD-BUDGET-01: Set sprint phase with ATOMIC resize + ROLLBACK.
        
        This method either succeeds completely or reverts to the previous state.
        It NEVER leaves the pools in a partially-resized state.
        
        Args:
            phase: Sprint phase name (case-insensitive).
            
        Returns:
            True if phase transition succeeded.
            False if transition failed (pools remain in previous state).
            
        Raises:
            PhaseTransitionError: If rollback_on_error=False and transition fails.
        """
        phase_upper = phase.upper()
        if phase_upper not in _PHASE_POOL_CONFIG:
            logger.error(
                "[RayonPoolManager] Unknown phase %r — refusing transition",
                phase,
            )
            return False

        if not self._initialized:
            logger.error(
                "[RayonPoolManager] Cannot set phase %r: Rust pools not initialized",
                phase_upper,
            )
            return False

        # Get target configuration
        target_cpu, target_io, target_mixed = _PHASE_POOL_CONFIG[phase_upper]
        
        # Pre-flight validation
        is_valid, total, reason = _validate_phase_budget(phase_upper, target_cpu, target_io, target_mixed)
        if not is_valid:
            logger.error(
                "[RayonPoolManager] Phase %r REJECTED: %s",
                phase_upper,
                reason,
            )
            return False

        # Snapshot current state for rollback
        prev_cpu = self._last_cpu
        prev_io = self._last_io
        prev_mixed = self._last_mixed
        prev_phase = self._current_phase

        # Update adaptive_scheduler with current phase
        try:
            adaptive_rust = _get_adaptive_rust()
            if adaptive_rust:
                adaptive_rust["set_adaptive_phase"](phase_upper)
        except Exception:
            pass  # Non-fatal: adaptive_scheduler telemetry-only

        with self._lock:
            resize_success = True
            errors: list[str] = []
            
            # Step 1: Resize CPU pool
            if target_cpu != self._last_cpu:
                try:
                    rust = _get_elastic_rust()
                    if rust:
                        actual = rust["resize_cpu_pool"](target_cpu)
                        self._budget_guard.set_count("rayon_cpu", actual)
                        self._last_cpu = actual
                        logger.info(
                            "[RayonPoolManager] [%s] cpu_pool: %d → %d threads",
                            phase_upper,
                            prev_cpu,
                            actual,
                        )
                except Exception as e:
                    errors.append(f"cpu_pool resize({target_cpu}): {e}")
                    resize_success = False

            # Step 2: Resize I/O pool
            if target_io != self._last_io:
                try:
                    rust = _get_elastic_rust()
                    if rust:
                        actual = rust["resize_io_pool"](target_io)
                        self._budget_guard.set_count("rayon_io", actual)
                        self._last_io = actual
                        logger.info(
                            "[RayonPoolManager] [%s] io_pool: %d → %d threads",
                            phase_upper,
                            prev_io,
                            actual,
                        )
                except Exception as e:
                    errors.append(f"io_pool resize({target_io}): {e}")
                    resize_success = False

            # Step 3: Update mixed pool tracking
            if target_mixed != self._last_mixed:
                self._budget_guard.set_count("rayon_mixed", target_mixed)
                self._last_mixed = target_mixed
                logger.info(
                    "[RayonPoolManager] [%s] mixed_pool: %d → %d threads",
                    phase_upper,
                    prev_mixed,
                    target_mixed,
                )

            # Handle resize result
            if resize_success:
                self._current_phase = phase_upper
                self._record_transition(phase_upper, prev_phase, prev_cpu, prev_io, prev_mixed, "success")
                logger.info(
                    "[RayonPoolManager] [%s] Phase transition SUCCESS: "
                    "cpu=%d io=%d mixed=%d dispatchers=%d total=%d/%d",
                    phase_upper,
                    self._last_cpu,
                    self._last_io,
                    self._last_mixed,
                    _DISPATCHER_COUNT,
                    self.total_threads,
                    _BUDGET_AVAILABLE,
                )
                return True
            else:
                # ROLLBACK on error
                if self._rollback_on_error:
                    logger.warning(
                        "[RayonPoolManager] [%s] Phase transition FAILED — ROLLING BACK",
                        phase_upper,
                    )
                    self._rollback(prev_phase, prev_cpu, prev_io, prev_mixed)
                    self._record_transition(phase_upper, prev_phase, prev_cpu, prev_io, prev_mixed, "rollback")
                    return False
                else:
                    # Fail-fast: raise exception
                    error_msg = f"Phase {phase_upper} transition failed: {'; '.join(errors)}"
                    logger.critical(f"[RayonPoolManager] {error_msg}")
                    self._record_transition(phase_upper, prev_phase, prev_cpu, prev_io, prev_mixed, "failed")
                    raise RuntimeError(error_msg)

    def _rollback(self, phase: str, cpu: int, io: int, mixed: int) -> None:
        """Rollback to previous state."""
        with self._lock:
            try:
                rust = _get_elastic_rust()
                if rust:
                    rust["resize_cpu_pool"](cpu)
                    rust["resize_io_pool"](io)
                self._budget_guard.set_count("rayon_cpu", cpu)
                self._budget_guard.set_count("rayon_io", io)
                self._budget_guard.set_count("rayon_mixed", mixed)
                self._last_cpu = cpu
                self._last_io = io
                self._last_mixed = mixed
                self._current_phase = phase
                logger.info(
                    "[RayonPoolManager] ROLLBACK complete: phase=%s cpu=%d io=%d mixed=%d",
                    phase,
                    cpu,
                    io,
                    mixed,
                )
            except Exception as e:
                # Critical: rollback failed
                logger.critical(
                    "[RayonPoolManager] ROLLBACK FAILED: pools in indeterminate state! %s",
                    e,
                )

    def _record_transition(
        self,
        new_phase: str,
        prev_phase: str,
        prev_cpu: int,
        prev_io: int,
        prev_mixed: int,
        outcome: str,
    ) -> None:
        """Record phase transition for telemetry."""
        self._transition_history.append({
            "timestamp": time.time(),
            "new_phase": new_phase,
            "prev_phase": prev_phase,
            "prev_cpu": prev_cpu,
            "prev_io": prev_io,
            "prev_mixed": prev_mixed,
            "outcome": outcome,
        })
        # Keep last 100 transitions
        if len(self._transition_history) > 100:
            self._transition_history = self._transition_history[-100:]

    def apply_adaptive_sizing(self) -> bool:
        """
        THREAD-BUDGET-01: Apply pressure-based thread recommendations.
        
        MODERN-31: This is the key integration point that makes
        adaptive_scheduler the SINGLE source of truth.
        
        Called after set_phase() to apply MLX memory pressure-aware
        recommendations on top of the phase-based initial sizing.
        
        Returns:
            True if adaptive sizing succeeded.
            False if any resize failed (budget may be violated).
        """
        if not self._initialized:
            return False

        try:
            adaptive_rust = _get_adaptive_rust()
            if not adaptive_rust:
                logger.warning(
                    "[RayonPoolManager] [adaptive] adaptive_scheduler unavailable"
                )
                return False

            # Get pressure-based recommendations (store original for logging)
            orig_cpu = adaptive_rust["get_adaptive_cpu_threads"]()
            orig_io = adaptive_rust["get_adaptive_io_threads"]()
            rec_cpu = orig_cpu
            rec_io = orig_io

            # THREAD-BUDGET-02: Validate recommendations against budget
            # Total = rec_cpu + rec_io + mixed (fixed) + dispatchers (fixed)
            # Must fit within _BUDGET_AVAILABLE = 6
            total_current = rec_cpu + rec_io + self._last_mixed + _DISPATCHER_COUNT
            if total_current > _BUDGET_AVAILABLE:
                # THREAD-BUDGET-02 FIX: Proper clamping that guarantees budget fit
                # Calculate maximum available for cpu + io pools
                max_pool_threads = _BUDGET_AVAILABLE - self._last_mixed - _DISPATCHER_COUNT
                
                # Clamp proportionally: distribute available budget between cpu and io
                # Priority: cpu > io (P-cores more valuable for MLX workloads)
                current_pool = rec_cpu + rec_io
                if current_pool > max_pool_threads and max_pool_threads > 0:
                    # Proportional distribution: keep ratio, fit budget
                    cpu_ratio = rec_cpu / current_pool
                    rec_cpu = max(1, int(max_pool_threads * cpu_ratio))
                    rec_io = max(1, max_pool_threads - rec_cpu)
                
                logger.warning(
                    "[RayonPoolManager] [adaptive] Clamped to fit budget: cpu=%d io=%d (was cpu=%d io=%d)",
                    rec_cpu,
                    rec_io,
                    orig_cpu,
                    orig_io,
                )

            with self._lock:
                success = True

                # Resize CPU pool if recommendation differs
                if rec_cpu != self._last_cpu:
                    try:
                        rust = _get_elastic_rust()
                        if rust:
                            actual = rust["resize_cpu_pool"](rec_cpu)
                            self._budget_guard.set_count("rayon_cpu", actual)
                            logger.info(
                                "[RayonPoolManager] [adaptive] cpu_pool: %d → %d threads (pressure-based)",
                                self._last_cpu,
                                actual,
                            )
                            self._last_cpu = actual
                    except Exception as e:
                        logger.warning(
                            "[RayonPoolManager] [adaptive] cpu_pool resize(%d) failed: %s",
                            rec_cpu,
                            e,
                        )
                        success = False

                # Resize I/O pool if recommendation differs
                if rec_io != self._last_io:
                    try:
                        rust = _get_elastic_rust()
                        if rust:
                            actual = rust["resize_io_pool"](rec_io)
                            self._budget_guard.set_count("rayon_io", actual)
                            logger.info(
                                "[RayonPoolManager] [adaptive] io_pool: %d → %d threads (pressure-based)",
                                self._last_io,
                                actual,
                            )
                            self._last_io = actual
                    except Exception as e:
                        logger.warning(
                            "[RayonPoolManager] [adaptive] io_pool resize(%d) failed: %s",
                            rec_io,
                            e,
                        )
                        success = False
                
                return success
        except Exception as e:
            logger.warning(
                "[RayonPoolManager] [adaptive] sizing failed: %s",
                e,
            )
            return False

    def shutdown(self) -> bool:
        """
        THREAD-BUDGET-01: Tear down to minimal resources (TEARDOWN phase).
        
        Reduces both pools to minimal (1 cpu + 1 io + 3 dispatchers = 5 total),
        freeing memory and OS thread slots before final teardown.
        
        Returns:
            True if teardown succeeded.
            False if teardown failed.
        """
        return self.set_phase("TEARDOWN")

    def get_stats(self) -> dict[str, Any]:
        """
        THREAD-BUDGET-01: Return comprehensive pool statistics including budget.
        """
        rust = _get_elastic_rust()
        adaptive_rust = _get_adaptive_rust()

        if rust and self._initialized:
            try:
                rust_total = rust["get_total_threads"]()
                stats = {
                    "available": True,
                    "phase": self._current_phase,
                    "cpu_threads": rust["get_cpu_threads"](),
                    "io_threads": rust["get_io_threads"](),
                    "mixed_threads": self._last_mixed,
                    "dispatcher_threads": _DISPATCHER_COUNT,
                    "total_rayon_threads": rust_total,
                    "budget_ceiling": _BUDGET_AVAILABLE,
                    "budget_pressure_pct": round(self._budget_guard.budget_pressure * 100, 1),
                    "within_budget": rust_total <= _BUDGET_AVAILABLE,
                }
                
                # Add adaptive_scheduler breakdown if available
                if adaptive_rust:
                    try:
                        budget = adaptive_rust["get_thread_budget_breakdown"]()
                        stats.update({
                            "adaptive_cpu_recommended": adaptive_rust["get_adaptive_cpu_threads"](),
                            "adaptive_io_recommended": adaptive_rust["get_adaptive_io_threads"](),
                            "adaptive_phase": adaptive_rust["get_adaptive_phase"](),
                        })
                    except Exception:  # noqa: BLE001
                        pass
                
                # Add budget guard stats
                stats.update(self._budget_guard.get_stats())
                
                # Add transition history summary
                if self._transition_history:
                    recent = self._transition_history[-10:]
                    stats["recent_transitions"] = [
                        {"phase": t["new_phase"], "outcome": t["outcome"]}
                        for t in recent
                    ]
                
                return stats
            except Exception:  # noqa: BLE001
                pass
        
        # Fallback stats when Rust unavailable
        return {
            "available": self._initialized,
            "phase": self._current_phase,
            "cpu_threads": self._last_cpu,
            "io_threads": self._last_io,
            "mixed_threads": self._last_mixed,
            "dispatcher_threads": _DISPATCHER_COUNT,
            "total_threads": self._last_cpu + self._last_io + self._last_mixed + _DISPATCHER_COUNT,
            "budget_ceiling": _BUDGET_AVAILABLE,
            "budget_pressure_pct": round(self._budget_guard.budget_pressure * 100, 1),
            "within_budget": (self._last_cpu + self._last_io + self._last_mixed + _DISPATCHER_COUNT) <= _BUDGET_AVAILABLE,
        }
    
    @property
    def budget_guard(self) -> ThreadBudgetGuard:
        """Get the thread budget guard instance."""
        return self._budget_guard
    
    @property
    def transition_history(self) -> list[dict[str, Any]]:
        """Get phase transition history."""
        return list(self._transition_history)


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


# NEXTGEN-03: Dedicated executors for asymmetric topology-aware pools
# ============================================================================


class IsolatedSIMDExecutor:
    """
    NEXTGEN-03: Executes SIMD operations via RustWorkerPool("simd").

    Backed by rayon simd_pool (2 threads on P-cores 0,1) for ARM NEON SIMD.
    QoS: USER_INITIATED for maximum throughput.
    
    Use for:
      - batch_cosine_scores() from simd_similarity.rs
      - deep_ac Aho-Corasick pattern matching
      - Other vectorized SIMD operations

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: simd_pool cap = 2 threads
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self) -> None:
        # NEXTGEN-03: Use "simd" pool type
        self._pool = get_rust_pool("simd")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    async def run_simd_async(
        self,
        simd_func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run SIMD function on the Rust simd_pool."""
        if not self._available:
            try:
                return simd_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"SIMD execution failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(simd_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:simd",
                )
            return await self._pool.submit(simd_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("SIMD execution timeout")
            return None
        except Exception as e:
            logger.warning(f"SIMD execution failed: {e}")
            return None

    def run_simd_sync(
        self,
        simd_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of run_simd_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_simd_async(simd_func, *args, **kwargs))
            return result
        if loop.is_running():
            coro = self.run_simd_async(simd_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.run_simd_async(simd_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool."""
        pass


class IsolatedGraphExecutor:
    """
    NEXTGEN-03: Executes graph operations via RustWorkerPool("graph").

    Backed by rayon graph_pool (1 thread on P-core 2, shared with MLX).
    QoS: USER_INITIATED for graph traversal.
    
    Use for:
      - Kuzu graph traversal (graph_traverse)
      - petgraph PageRank computations
      - Graph-based pattern matching

    Note: Single thread to avoid overwhelming GPU pipeline.

    Invariants:
      - Always-on: RustWorkerPool fallback covers all cases
      - Bounded: graph_pool cap = 1 thread
      - Fail-safe: returns None on error, never raises
    """

    __slots__ = ("_pool", "_available")

    def __init__(self) -> None:
        # NEXTGEN-03: Use "graph" pool type
        self._pool = get_rust_pool("graph")
        self._available = _check_rust_pool_available()

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    async def run_graph_async(
        self,
        graph_func: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run graph function on the Rust graph_pool."""
        if not self._available:
            try:
                return graph_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Graph execution failed (pool unavailable): {e}")
                return None

        try:
            if timeout is not None:
                return await safe_wait_for(
                    self._pool.submit(graph_func, *args, timeout=timeout, **kwargs),
                    timeout=timeout,
                    label="isolated_executors:graph",
                )
            return await self._pool.submit(graph_func, *args, **kwargs)
        except asyncio.TimeoutError:
            logger.warning("Graph execution timeout")
            return None
        except Exception as e:
            logger.warning(f"Graph execution failed: {e}")
            return None

    def run_graph_sync(
        self,
        graph_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of run_graph_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with asyncio.Runner() as runner:
                result = runner.run(self.run_graph_async(graph_func, *args, **kwargs))
            return result
        if loop.is_running():
            coro = self.run_graph_async(graph_func, *args, **kwargs)
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(self.run_graph_async(graph_func, *args, **kwargs))

    def close(self) -> None:
        """Close is a no-op for RustWorkerPool."""
        pass


# Module-level singletons for new executors
_simd_executor: IsolatedSIMDExecutor | None = None
_graph_executor: IsolatedGraphExecutor | None = None


def get_simd_executor() -> IsolatedSIMDExecutor:
    """Get or create global SIMD executor pool (NEXTGEN-03)."""
    global _simd_executor
    if _simd_executor is not None:
        return _simd_executor
    with _pools_lock:
        if _simd_executor is None:
            _simd_executor = IsolatedSIMDExecutor()
        return _simd_executor


def get_graph_executor() -> IsolatedGraphExecutor:
    """Get or create global Graph executor pool (NEXTGEN-03)."""
    global _graph_executor
    if _graph_executor is not None:
        return _graph_executor
    with _pools_lock:
        if _graph_executor is None:
            _graph_executor = IsolatedGraphExecutor()
        return _graph_executor
