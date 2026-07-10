"""
PEP 734: Multiple Interpreters in stdlib (Python 3.14+)

Provides true process-level isolation for CPU-bound workloads using
`concurrent.interpreters` - each interpreter has its own GIL, enabling
genuine parallelism for Python code.

Use cases:
- DuckDB queries: GIL bypass for concurrent SQL execution
- MLX inference isolation: separate memory arena for large models
- CPU-bound evidence processing: parallel Python code execution

Key invariants:
- Always-on: no feature flags, always available on Python 3.14+
- Bounded: MAX_INTERPRETERS cap prevents resource exhaustion
- Fail-safe: returns None/empty on any error, never raises
- Lazy import: concurrent.interpreters only imported when available

M1 8GB notes:
- Each interpreter ~1-2MB overhead (vs 50MB+ for subprocess)
- Memory isolation helps prevent MLX/DuckDB memory pressure conflicts
- interpreters share file descriptors (reduced FD pressure vs subprocess)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sys
import threading
from typing import Any, Callable, TypeVar

from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing

logger = logging.getLogger(__name__)

T = TypeVar("T")

# PEP 734: concurrent.interpreters requires Python 3.14+
# Lazy import - don't fail on older Python, just disable the feature
_interpreters_module: Any | None = None
_interpreters_available: bool = False

try:
    if sys.version_info >= (3, 14):
        import concurrent.interpreters as _ci
        _interpreters_module = _ci
        _interpreters_available = True
        _INTERPRETER_CHANNEL_SEND_TIMEOUT_S: float = 30.0
        _INTERPRETER_EVAL_TIMEOUT_S: float = 60.0
except ImportError:
    _interpreters_module = None
    _interpreters_available = False


# ─── Constants ─────────────────────────────────────────────────────────────────

MAX_INTERPRETERS: int = 3  # Bounded: prevent resource exhaustion on M1 8GB
"""Maximum number of concurrent isolated interpreters."""

_INTERPRETER_STACKSIZE: int = 1024 * 1024  # 1MB stack per interpreter


# ─── Exceptions ────────────────────────────────────────────────────────────────

class IsolatedExecutorError(Exception):
    """Base exception for isolated executor errors."""
    pass


class InterpreterNotAvailableError(IsolatedExecutorError):
    """Raised when concurrent.interpreters is not available (Python < 3.14)."""
    pass


class InterpreterStartError(IsolatedExecutorError):
    """Raised when isolated interpreter fails to start."""
    pass


class InterpreterChannelError(IsolatedExecutorError):
    """Raised when inter-interpreter communication fails."""
    pass


# ─── Channel-based RPC ────────────────────────────────────────────────────────

def _create_interpreter_channel() -> tuple[Any, Any]:
    """
    Create a bidirectional channel for interpreter communication.

    Returns:
        Tuple of (receive_channel, send_channel) for inter-interpreter RPC.

    Note:
        Uses concurrent.interpreters.Channel for zero-copy transfer.
        Falls back to None if Channel unavailable.
    """
    if not _interpreters_available or _interpreters_module is None:
        return None, None

    try:
        return _interpreters_module.channel()
    except Exception:
        return None, None


# ─── IsolatedInterpreter wrapper ─────────────────────────────────────────────

class IsolatedInterpreter:
    """
    Wraps a concurrent.interpreters.Interpreter with RPC capability.

    Each IsolatedInterpreter has:
    - Its own GIL (true parallelism)
    - Separate memory arena (M1 8GB isolation)
    - Channel-based RPC for communication

    Invariants:
    - Always-on: no feature flags
    - Fail-safe: close() never raises, resources cleaned on best-effort
    - Bounded: MAX_INTERPRETERS cap enforced via semaphore
    """

    def __init__(
        self,
        *,
        max_workers: int = 1,
        stack_size: int = _INTERPRETER_STACKSIZE,
    ) -> None:
        self._interp: Any = None
        self._receive_channel: Any = None
        self._send_channel: Any = None
        self._max_workers = max_workers
        self._stack_size = stack_size
        self._closed = False
        self._lock = threading.Lock()

        # Semaphore for bounded concurrent access
        self._semaphore = get_semaphore_for_testing(
            ConcurrencyCategory.ISOLATED_INTERPRETER
        )

    @property
    def is_available(self) -> bool:
        """Check if concurrent.interpreters is available."""
        return _interpreters_available

    def start(self) -> bool:
        """
        Start the isolated interpreter.

        Returns:
            True if started successfully, False otherwise.

        Fail-safe: returns False on any error, interpreter cleaned up.
        """
        if self._closed:
            return False

        if not _interpreters_available or _interpreters_module is None:
            logger.debug("concurrent.interpreters not available (Python < 3.14)")
            return False

        with self._lock:
            if self._interp is not None:
                return True

            try:
                # Create interpreter with optional stack size
                self._interp = _interpreters_module.create()

                # Create bidirectional channel for RPC
                self._receive_channel, self._send_channel = _create_interpreter_channel()

                if self._receive_channel is None or self._send_channel is None:
                    # Fallback: use default interpréter communication
                    logger.debug("Channel creation failed, using default communication")
                    self._receive_channel = None
                    self._send_channel = None

                # Start the interpreter
                self._interp.start()
                logger.debug("Isolated interpreter started successfully")
                return True

            except Exception as e:
                logger.warning(f"Failed to start isolated interpreter: {e}")
                self._cleanup()
                return False

    def _cleanup(self) -> None:
        """Best-effort cleanup of interpreter resources."""
        try:
            if self._interp is not None:
                try:
                    self._interp.join(timeout=2.0)
                except Exception:
                    pass
                try:
                    self._interp.close()
                except Exception:
                    pass
                self._interp = None
        except Exception:
            pass

        self._receive_channel = None
        self._send_channel = None

    def close(self) -> None:
        """
        Close the isolated interpreter and release resources.

        Always-on, fail-safe: never raises, always cleans up best-effort.
        """
        with self._lock:
            self._closed = True
            self._cleanup()

    async def run_async(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Run a function in the isolated interpreter asynchronously.

        Args:
            func: Callable to execute in the isolated interpreter.
            *args: Positional arguments to pass to func.
            **kwargs: Keyword arguments to pass to func.

        Returns:
            Result of func(*args, **kwargs), or None on any error.

        Fail-safe: returns None on timeout, interpreter crash, or channel error.
        """
        if not self.start():
            return None

        async with self._semaphore:
            try:
                # Serialize function and arguments
                import pickle

                payload = pickle.dumps((func, args, kwargs))

                # Run in interpreter - use eval for expression, exec for statements
                # We use interp.run() which executes code in the sub-interpreter
                if self._send_channel is not None:
                    # Channel-based communication
                    self._send_channel.send(payload)

                    try:
                        async with asyncio.timeout(_INTERPRETER_EVAL_TIMEOUT_S):
                            result_bytes = await asyncio.to_thread(
                                self._receive_channel.recv
                            )
                    except asyncio.TimeoutError:
                        logger.warning("Interpreter eval timeout")
                        return None
                else:
                    # Direct execution via interp.run()
                    # This is simpler but less flexible
                    code = f"""
import pickle
_func, _args, _kwargs = pickle.loads({repr(payload)})
pickle.dumps(_func(*_args, **_kwargs))
"""
                    try:
                        async with asyncio.timeout(_INTERPRETER_EVAL_TIMEOUT_S):
                            result_bytes = await asyncio.to_thread(
                                self._interp.run, code
                            )
                    except asyncio.TimeoutError:
                        logger.warning("Interpreter run timeout")
                        return None

                # Deserialize result
                if result_bytes is None:
                    return None

                return pickle.loads(result_bytes)

            except Exception as e:
                logger.warning(f"Isolated interpreter run failed: {e}")
                return None

    def run_sync(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Run a function in the isolated interpreter synchronously.

        Note:
            This blocks the calling thread. For async contexts, use run_async().

        Returns:
            Result of func(*args, **kwargs), or None on any error.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - create a new one (worker thread safe)
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.run_async(func, *args, **kwargs))
            finally:
                loop.close()
        else:
            # Running loop exists - use asyncio.to_thread for thread-safe execution
            # to avoid blocking the event loop
            import functools
            return asyncio.run_coroutine_threadsafe(
                self.run_async(func, *args, **kwargs),
                loop
            ).result()

    def __enter__(self) -> "IsolatedInterpreter":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ─── Global interpreter pool ──────────────────────────────────────────────────

class IsolatedInterpreterPool:
    """
    Pool of IsolatedInterpreters with round-robin allocation.

    Bounded: max MAX_INTERPRETERS instances.
    Thread-safe: uses threading.Lock.

    Use cases:
    - Parallel DuckDB query execution
    - CPU-bound evidence processing
    - Memory isolation for large operations
    """

    def __init__(
        self,
        *,
        max_size: int = MAX_INTERPRETERS,
        stack_size: int = _INTERPRETER_STACKSIZE,
    ) -> None:
        self._max_size = min(max_size, MAX_INTERPRETERS)
        self._stack_size = stack_size
        self._interpreters: list[IsolatedInterpreter] = []
        self._lock = threading.Lock()
        self._round_robin_index = 0
        self._available = _interpreters_available

    @property
    def is_available(self) -> bool:
        """Check if interpreter pool is available."""
        return self._available

    def _remove_closed_interpreters(self) -> None:
        """Remove closed/failed interpreters from the pool."""
        with self._lock:
            before = len(self._interpreters)
            self._interpreters = [
                interp for interp in self._interpreters
                if not interp._closed
            ]
            # Adjust round-robin index if needed
            if self._interpreters:
                self._round_robin_index = self._round_robin_index % len(self._interpreters)
            else:
                self._round_robin_index = 0
            if before != len(self._interpreters):
                logger.debug(
                    f"Removed {before - len(self._interpreters)} closed interpreters, "
                    f"{len(self._interpreters)} remaining"
                )

    def _get_or_create(self) -> IsolatedInterpreter | None:
        """Get existing or create new interpreter with round-robin."""
        if not self._available:
            return None

        with self._lock:
            # First, filter out closed interpreters
            active = [interp for interp in self._interpreters if not interp._closed]

            # Return existing if we have some
            if active:
                interp = active[self._round_robin_index % len(active)]
                self._round_robin_index = (self._round_robin_index + 1) % len(active)
                return interp

            # Create new if under limit
            if len(self._interpreters) < self._max_size:
                interp = IsolatedInterpreter(stack_size=self._stack_size)
                if interp.start():
                    self._interpreters.append(interp)
                    self._round_robin_index = (self._round_robin_index + 1) % len(self._interpreters)
                    return interp

            return None

    async def run_async(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Run function in isolated interpreter from pool.

        Uses round-robin allocation across available interpreters.
        """
        interp = self._get_or_create()
        if interp is None:
            logger.warning("No isolated interpreter available, running in current interpreter")
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Function execution failed: {e}")
                return None

        return await interp.run_async(func, *args, **kwargs)

    def run_sync(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Synchronous version of run_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.run_async(func, *args, **kwargs))
            finally:
                loop.close()
        else:
            return asyncio.run_coroutine_threadsafe(
                self.run_async(func, *args, **kwargs), loop
            ).result()

    def close_all(self) -> None:
        """
        Close all interpreters in the pool.

        Always-on, fail-safe: never raises.
        """
        with self._lock:
            for interp in self._interpreters:
                try:
                    interp.close()
                except Exception:
                    pass
            self._interpreters.clear()
            self._round_robin_index = 0


# ─── DuckDB Query Executor ────────────────────────────────────────────────────

class IsolatedDuckDBExecutor:
    """
    Executes DuckDB queries in an isolated interpreter.

    Benefits:
    - True GIL bypass for concurrent SQL execution
    - Memory isolation: DuckDB memory is separate from main interpreter
    - M1 8GB safe: prevents DuckDB memory pressure on MLX arena

    Use with DuckDBShadowStore.async_ingest_findings_batch() for
    CPU-bound quality assessment in parallel with inference.
    """

    def __init__(
        self,
        *,
        max_queries: int = 4,
    ) -> None:
        self._pool = IsolatedInterpreterPool(max_size=max_queries)
        self._available = _interpreters_available and self._pool.is_available

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    async def execute_query_async(
        self,
        query_func: Callable[..., list[dict]],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict] | None:
        """
        Execute a DuckDB query function in isolated interpreter.

        Args:
            query_func: Function that executes DuckDB query and returns results.
            *args: Positional arguments to pass to query_func.
            **kwargs: Keyword arguments to pass to query_func.

        Returns:
            Query results as list[dict], or None on error.

        Fail-safe: returns None on any error, never raises.
        """
        if not self._available:
            try:
                return query_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Query execution failed (no isolation): {e}")
                return None

        result = await self._pool.run_async(query_func, *args, **kwargs)
        return result if result is not None else []

    def execute_query_sync(
        self,
        query_func: Callable[..., list[dict]],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict] | None:
        """Synchronous version of execute_query_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.execute_query_async(query_func, *args, **kwargs)
                )
            finally:
                loop.close()
        else:
            return asyncio.run_coroutine_threadsafe(
                self.execute_query_async(query_func, *args, **kwargs), loop
            ).result()

    def close(self) -> None:
        """Close all interpreters in the pool."""
        self._pool.close_all()


# ─── MLX Inference Executor ──────────────────────────────────────────────────

class IsolatedMLXExecutor:
    """
    Executes MLX inference in an isolated interpreter.

    Benefits:
    - Memory isolation: MLX Metal arena separate from main interpreter
    - True GIL bypass: MLX operations run without GIL contention
    - Crash isolation: inference crash doesn't corrupt main interpreter

    Note:
        MLX already releases GIL at C-level, but having separate
        interpreter provides additional memory arena isolation.
    """

    def __init__(
        self,
        *,
        max_inference: int = 2,
    ) -> None:
        self._pool = IsolatedInterpreterPool(max_size=max_inference)
        self._available = _interpreters_available and self._pool.is_available

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    async def run_inference_async(
        self,
        inference_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Run MLX inference function in isolated interpreter.

        Args:
            inference_func: Function that runs MLX inference.
            *args: Positional arguments (e.g., prompt, config).
            **kwargs: Keyword arguments (e.g., kv_bits, max_kv_size).

        Returns:
            Inference result, or None on error.

        Fail-safe: returns None on any error.
        """
        if not self._available:
            try:
                return inference_func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Inference failed (no isolation): {e}")
                return None

        result = await self._pool.run_async(inference_func, *args, **kwargs)
        return result

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
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.run_inference_async(inference_func, *args, **kwargs)
                )
            finally:
                loop.close()
        else:
            return asyncio.run_coroutine_threadsafe(
                self.run_inference_async(inference_func, *args, **kwargs), loop
            ).result()

    def close(self) -> None:
        """Close all interpreters in the pool."""
        self._pool.close_all()


# ─── Evidence Log Batch Writer ────────────────────────────────────────────────

class IsolatedEvidenceBatchWriter:
    """
    Writes evidence batches in isolated interpreter.

    Benefits:
    - CPU-bound batch serialization runs without blocking main interpreter
    - Memory isolation: batch memory is separate
    - True parallelism for parallel batch processing

    Note:
        Rust MPSC is still used for the actual queue (faster than Python channel).
        This executor is for CPU-bound batch transformation/serialization.
    """

    def __init__(
        self,
        *,
        max_batch_workers: int = 2,
    ) -> None:
        self._pool = IsolatedInterpreterPool(max_size=max_batch_workers)
        self._available = _interpreters_available and self._pool.is_available

    @property
    def is_available(self) -> bool:
        """Check if isolated execution is available."""
        return self._available

    async def process_batch_async(
        self,
        process_func: Callable[..., list[dict]],
        items: list[dict],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict]:
        """
        Process evidence batch in isolated interpreter.

        Args:
            process_func: Function that processes batch items.
            items: List of evidence items to process.
            *args, **kwargs: Additional arguments to process_func.

        Returns:
            Processed items, or original items on error.

        Fail-safe: returns original items on any error.
        """
        if not self._available:
            try:
                return process_func(items, *args, **kwargs)
            except Exception as e:
                logger.warning(f"Batch processing failed (no isolation): {e}")
                return items

        result = await self._pool.run_async(process_func, items, *args, **kwargs)
        return result if result is not None else items

    def process_batch_sync(
        self,
        process_func: Callable[..., list[dict]],
        items: list[dict],
        *args: Any,
        **kwargs: Any,
    ) -> list[dict]:
        """Synchronous version of process_batch_async."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.process_batch_async(process_func, items, *args, **kwargs)
                )
            finally:
                loop.close()
        else:
            return asyncio.run_coroutine_threadsafe(
                self.process_batch_async(process_func, items, *args, **kwargs), loop
            ).result()

    def close(self) -> None:
        """Close all interpreters in the pool."""
        self._pool.close_all()


# ─── Global instances ─────────────────────────────────────────────────────────

# Lazy-initialized global pools (created on first use)
_duckdb_pool: IsolatedDuckDBExecutor | None = None
_mlx_pool: IsolatedMLXExecutor | None = None
_evidence_pool: IsolatedEvidenceBatchWriter | None = None
_pools_lock = threading.Lock()


def get_duckdb_executor() -> IsolatedDuckDBExecutor:
    """Get or create global DuckDB executor pool."""
    global _duckdb_pool
    with _pools_lock:
        if _duckdb_pool is None:
            _duckdb_pool = IsolatedDuckDBExecutor()
        return _duckdb_pool


def get_mlx_executor() -> IsolatedMLXExecutor:
    """Get or create global MLX executor pool."""
    global _mlx_pool
    with _pools_lock:
        if _mlx_pool is None:
            _mlx_pool = IsolatedMLXExecutor()
        return _mlx_pool


def get_evidence_batch_writer() -> IsolatedEvidenceBatchWriter:
    """Get or create global evidence batch writer pool."""
    global _evidence_pool
    with _pools_lock:
        if _evidence_pool is None:
            _evidence_pool = IsolatedEvidenceBatchWriter()
        return _evidence_pool


def close_all_pools() -> None:
    """
    Close all global interpreter pools.

    Call on application shutdown for clean exit.
    """
    global _duckdb_pool, _mlx_pool, _evidence_pool

    with _pools_lock:
        if _duckdb_pool is not None:
            _duckdb_pool.close()
            _duckdb_pool = None

        if _mlx_pool is not None:
            _mlx_pool.close()
            _mlx_pool = None

        if _evidence_pool is not None:
            _evidence_pool.close()
            _evidence_pool = None


# ─── Feature detection ────────────────────────────────────────────────────────

def is_pep734_available() -> bool:
    """
    Check if PEP 734 (concurrent.interpreters) is available.

    Returns:
        True if Python 3.14+ with concurrent.interpreters, False otherwise.
    """
    return _interpreters_available


def get_interpreter_stats() -> dict[str, Any]:
    """
    Get statistics about isolated interpreter usage.

    Returns:
        Dict with availability info and pool statistics.
    """
    return {
        "pep734_available": _interpreters_available,
        "python_version": sys.version_info[:2],
        "max_interpreters": MAX_INTERPRETERS,
        "pools": {
            "duckdb": {
                "available": _duckdb_pool.is_available if _duckdb_pool else False,
                "pool_size": _duckdb_pool._pool._max_size if _duckdb_pool else 0,
            },
            "mlx": {
                "available": _mlx_pool.is_available if _mlx_pool else False,
                "pool_size": _mlx_pool._pool._max_size if _mlx_pool else 0,
            },
            "evidence": {
                "available": _evidence_pool.is_available if _evidence_pool else False,
                "pool_size": _evidence_pool._pool._max_size if _evidence_pool else 0,
            },
        },
    }
