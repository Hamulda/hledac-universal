"""
ISSUE-010: Pool Protocol — Unified abstraction for all execution pools

Defines the abstract interfaces that all pool implementations must satisfy.
Provides type-safe pool selection and resource management.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from concurrent.futures import Executor

T = TypeVar("T")


class PoolType(Enum):
    """Canonical pool type identifiers."""

    # Rust rayon pools (GIL-free CPU parallelism)
    CPU = auto()  # CPU-bound: SIMD, hashing, pattern matching (4 P-cores)
    IO = auto()  # I/O-bound: DuckDB, graph traversal (2 threads)
    MIXED = auto()  # Adaptive: IOC extract, url_ops, simhash (1-2 threads)
    SIMD = auto()  # ARM NEON SIMD operations (2 P-cores)
    MLX = auto()  # MLX Metal inference (2 P-cores)
    GRAPH = auto()  # Graph traversal (Kuzu, petgraph) (1 P-core)

    # Python-native pools
    THREAD = auto()  # ThreadPoolExecutor for sync I/O
    SUBINTERPRETER = auto()  # InterpreterPoolExecutor (Python 3.14.6+)

    @property
    def is_rayon(self) -> bool:
        """True if this pool type uses Rust rayon."""
        return self in {
            PoolType.CPU,
            PoolType.IO,
            PoolType.MIXED,
            PoolType.SIMD,
            PoolType.MLX,
            PoolType.GRAPH,
        }

    @property
    def is_python_native(self) -> bool:
        """True if this pool type is Python-native."""
        return self in {
            PoolType.THREAD,
            PoolType.SUBINTERPRETER,
        }

    @property
    def description(self) -> str:
        """Human-readable description of the pool type."""
        descriptions = {
            PoolType.CPU: "Rayon CPU pool (4 P-cores, GIL-free)",
            PoolType.IO: "Rayon I/O pool (2 threads, blocking I/O)",
            PoolType.MIXED: "Rayon mixed pool (1-2 threads, adaptive)",
            PoolType.SIMD: "Rayon SIMD pool (2 P-cores, ARM NEON)",
            PoolType.MLX: "Rayon MLX pool (2 P-cores, Metal GPU)",
            PoolType.GRAPH: "Rayon graph pool (1 P-core, Kuzu/petgraph)",
            PoolType.THREAD: "ThreadPoolExecutor (sync I/O fallback)",
            PoolType.SUBINTERPRETER: "InterpreterPoolExecutor (Python 3.14.6+, native GIL)",
        }
        return descriptions.get(self, "Unknown pool type")


@dataclass(frozen=True, slots=True)
class PoolStats:
    """Aggregated pool statistics."""

    pool_type: PoolType
    name: str
    available: bool
    thread_count: int = 0
    queue_depth: int = 0
    active_tasks: int = 0

    # Memory (for accelerator pools)
    memory_mb: float = 0.0

    # Utilization metrics
    utilization_pct: float = 0.0
    total_runs: int = 0
    total_errors: int = 0


class SyncPoolProtocol(ABC):
    """Protocol for synchronous thread-based pools."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the pool backend is available."""
        ...

    @abstractmethod
    def run_sync[T](self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
        """Run a synchronous function on the pool.

        Args:
            fn: Synchronous callable to run.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of fn(*args, **kwargs), or None on error.
        """
        ...

    @abstractmethod
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the pool.

        Args:
            wait: If True, wait for pending work to complete.
        """
        ...


class AsyncPoolProtocol(ABC):
    """Protocol for async-aware pools with context managers."""

    @abstractmethod
    async def run_async[T](self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
        """Run a synchronous function on the pool without blocking the event loop.

        Args:
            fn: Synchronous callable to run.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of fn(*args, **kwargs), or None on error.
        """
        ...

    @abstractmethod
    async def __aenter__(self) -> AsyncPoolProtocol:
        """Async context manager entry."""
        ...

    @abstractmethod
    async def __aexit__(self, *exc_info: Any) -> None:
        """Async context manager exit."""
        ...


class PoolProtocol(SyncPoolProtocol, AsyncPoolProtocol):
    """Combined protocol for pools that support both sync and async execution.

    All pools in this module should implement this protocol for consistency.
    """

    @property
    @abstractmethod
    def pool_type(self) -> PoolType:
        """Return the pool type identifier."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the pool name for logging."""
        ...

    @abstractmethod
    def get_stats(self) -> PoolStats:
        """Get current pool statistics."""
        ...


# ---------------------------------------------------------------------------
# Pool registry for lazy initialization
# ---------------------------------------------------------------------------

_pool_registry: dict[PoolType, PoolProtocol] = {}


def register_pool(pool_type: PoolType, pool: PoolProtocol) -> None:
    """Register a pool instance for lazy lookup."""
    _pool_registry[pool_type] = pool


def get_pool(pool_type: PoolType) -> PoolProtocol | None:
    """Get a registered pool by type.

    Returns None if the pool is not registered or not available.
    """
    return _pool_registry.get(pool_type)


def list_registered_pools() -> list[tuple[PoolType, str, bool]]:
    """List all registered pools with availability status."""
    return [
        (pt, _pool_registry[pt].name, _pool_registry[pt].is_available())
        for pt in _pool_registry
    ]
