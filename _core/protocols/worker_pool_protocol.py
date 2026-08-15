"""
Worker Pool Protocol — breaks core ↔ runtime dependency cycle.

PEP 544 Protocol defining RustWorkerPool interface without importing runtime module.
Core modules import from here instead of runtime.worker_pool.

F350M-R: Dependency cycle elimination
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    T = object

# Pool types matching runtime/worker_pool.py
PoolType = Literal["cpu", "io", "mixed", "simd", "mlx", "graph"]


class WorkerPoolStats(Protocol):
    """Stats protocol for worker pool monitoring."""

    @property
    def active_workers(self) -> int: ...
    @property
    def total_tasks(self) -> int: ...
    @property
    def completed_tasks(self) -> int: ...
    @property
    def failed_tasks(self) -> int: ...


@runtime_checkable
class RustWorkerPoolProtocol(Protocol):
    """
    Protocol for Rust-backed worker pool.

    Matches the interface of runtime/worker_pool.py::RustWorkerPool
    without creating a circular import dependency.
    """

    pool_type: PoolType

    async def run(self, coro: Any) -> Any:
        """Submit coroutine to pool, return result."""
        ...

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """Graceful shutdown with timeout."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        ...

    async def submit(self, coro: Any, *, timeout_s: float | None = None) -> Any:
        """Submit coroutine with optional timeout."""
        ...


@runtime_checkable  
class SharedWorkerPoolProtocol(Protocol):
    """
    Protocol for Python ThreadPoolExecutor-backed worker pool.

    Matches the interface of runtime/worker_pool.py::SharedWorkerPool
    """

    async def run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run synchronous function in thread pool."""
        ...

    async def shutdown(self, timeout_s: float = 10.0) -> None:
        """Graceful shutdown."""
        ...

    def get_max_workers(self) -> int:
        """Return max worker count."""
        ...


# Lazy import helper — breaks the cycle
def get_rust_pool(pool_type: PoolType = "cpu") -> RustWorkerPoolProtocol:
    """
    Lazy getter for RustWorkerPool.
    
    Import is deferred until first call, breaking the core ↔ runtime cycle.
    M1 8GB: This avoids loading runtime.worker_pool at cold-start.
    """
    from hledac.universal.runtime.worker_pool import get_rust_pool as _impl
    return _impl(pool_type)


def get_shared_pool() -> SharedWorkerPoolProtocol:
    """Lazy getter for SharedWorkerPool."""
    from hledac.universal.runtime.worker_pool import get_shared_pool as _impl
    return _impl()
