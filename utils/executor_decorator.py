"""utils/executor_decorator.py — R-2: @offload_to() typed-executor decorator.

Provides:
  @offload_to("pool_name") — decorátor pro async funkce běžící v named pool

  get_named_pool()         — lazy singleton pro každý named pool

Pool sizing (M1 8GB, total ≤ 8 threads):
  cpu_io_pool       = 4  — SharedWorkerPool (I/O: WHOIS, SSL, SQLite, file I/O)
  cpu_blocking_pool = 2  — ThreadPoolExecutor (CPU-bound Python: regex, parsing)
  mlx_pool          = 1  — Rust rayon (MLX inference, Metal)
  ane_pool          = 1  — Rust rayon (CoreML/ANE)
  duckdb_pool       = 2  — Rust rayon (DuckDB, graph, compress)

Thread budget check:
  sum(pools) = 4+2+1+1+2 = 10 threads → cpu_blocking_pool uses 2 shared workers
  from SharedWorkerPool adaptive (governed by M1ResourceGovernor).

Usage:
  @offload_to("cpu_io_pool")
  async def my_async_fn(...) -> Result:
      ...

  # Or with explicit pool:
  result = await offload_to("mlx_pool", sync_fn, arg1, arg2)

Compile-time check (ruff rule R-2):
  Žádný loop.run_in_executor(None, ...) mimo utils/executor_decorator.py.
  RUFF005 = R-2 executor-none violation.

Invariant table (test name → validated property):
  test_offload_to_cpu_io_pool      → get_named_pool("cpu_io_pool").max_workers == 4
  test_offload_to_mlx_pool_type    → get_named_pool("mlx_pool").pool_type == "cpu"
  test_offload_to_duckdb_pool_type → get_named_pool("duckdb_pool").pool_type == "io"
  test_offload_to_unknown_raises   → raises KeyError for unknown pool name
  test_decorator_preserves_sig     → inspect.signature matches original
  test_decorator_with_timeout     → timeout argument passed through
"""

from __future__ import annotations

import asyncio
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, overload
from collections.abc import Callable, Awaitable

from hledac.universal._core.config.m1_air_config import M1AirConfig
from _core import aclose
from _core.locks import LockCategory, register_lock

__all__ = [
    "offload_to",
    "get_named_pool",
    "NamedPool",
    "POOL_NAMES",
]

# ─────────────────────────────────────────────────────────────────────────────
# Pool registry
# ─────────────────────────────────────────────────────────────────────────────

POOL_NAMES = frozenset([
    "cpu_io_pool",
    "cpu_blocking_pool",
    "mlx_pool",
    "ane_pool",
    "duckdb_pool",
])

# Module-level singletons — lazy initialization on first use.
_pools: dict[str, NamedPool] = {}
_pools_lock = threading.Lock()
register_lock(LockCategory.CONFIG, _pools_lock, "utils.executor_decorator._pools_lock")


class NamedPool:
    """Wrapper kolem SharedWorkerPool nebo RustWorkerPool s named pool semantics.

    Properties:
      name:      pool identifier ("cpu_io_pool", "mlx_pool", ...)
      pool_type: rayon pool type pro RustWorkerPool ("cpu", "io", "mixed")
      max_workers: max threads (governed by M1ResourceGovernor pro Python pools)
    """

    __slots__ = ("_name", "_pool_type", "_max_workers", "_executor")

    def __init__(self, name: str) -> None:
        self._name = name
        if name == "mlx_pool":
            self._pool_type = "cpu"
            self._max_workers = M1AirConfig.mlx_pool
        elif name == "ane_pool":
            self._pool_type = "cpu"
            self._max_workers = M1AirConfig.ane_pool
        elif name == "duckdb_pool":
            self._pool_type = "io"
            self._max_workers = M1AirConfig.duckdb_pool
        elif name == "cpu_io_pool":
            self._pool_type = "mixed"  # adaptive SharedWorkerPool
            self._max_workers = M1AirConfig.cpu_io_pool
        elif name == "cpu_blocking_pool":
            self._pool_type = "mixed"  # adaptive SharedWorkerPool
            self._max_workers = M1AirConfig.cpu_blocking_pool
        else:
            raise KeyError(f"Unknown pool name: {name!r}. Valid: {POOL_NAMES}")

        # Lazy executor — created on first run()
        self._executor: ThreadPoolExecutor | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def pool_type(self) -> str:
        return self._pool_type

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix=f"hledac-{self._name}",
    )
        return self._executor

    async def run(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run blocking func on this pool's executor, awaitable."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._get_executor(),
            functools.partial(func, *args, **kwargs),
    )

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None


def get_named_pool(name: str) -> NamedPool:
    """Return singleton NamedPool for name, creating on first call."""
    if name not in POOL_NAMES:
        raise KeyError(f"Unknown pool: {name!r}. Valid: {POOL_NAMES}")
    if name not in _pools:
        with _pools_lock:
            if name not in _pools:
                _pools[name] = NamedPool(name)
    return _pools[name]


# ─────────────────────────────────────────────────────────────────────────────
# @offload_to() decorator
# ─────────────────────────────────────────────────────────────────────────────

@overload
def offload_to(
    pool: str,
    func: Callable[..., Any],
    /,
    *args: Any,
    timeout: float | None = None,
) -> Awaitable[Any]:
    ...


@overload
def offload_to(
    pool: str,
    /,
) -> Callable[[Callable[..., Any]], Callable[..., Awaitable[Any]]]:
    ...


async def _run_in_pool(
    pool: NamedPool, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], timeout: float | None
) -> Any:
    """Execute fn(*args, **kwargs) on pool's executor, return awaitable."""
    loop = asyncio.get_running_loop()
    coro = loop.run_in_executor(
        pool._get_executor(),
        functools.partial(fn, *args, **kwargs),
    )
    if timeout is not None:
        from hledac.universal.utils.asyncx import safe_wait_for
        return await safe_wait_for(coro, timeout=timeout, label=f"offload:{pool.name}")
    return await coro


def offload_to(
    pool: str,
    func: Callable[..., Any] | None = None,
    /,
    *args: Any,
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Awaitable[Any]]] | Awaitable[Any]:
    """Decorator/function to offload blocking sync calls to named pool.

    Usage:
        @offload_to("cpu_io_pool")
        async def my_async_fn(arg1, arg2):
            return sync_blocking_call(arg1, arg2)

        # Or as a function call (func + positional args after):
        result = await offload_to("mlx_pool", sync_fn, arg1, arg2)

    Args:
        pool:    Pool name — one of POOL_NAMES
        func:    Sync callable to offload (when used as function, not decorator)
        *args:   Positional args passed to func (when func provided)
        timeout: Optional timeout in seconds

    Raises:
        KeyError: If pool name is not in POOL_NAMES

    Note:
        Uses functools.partial instead of lambda to avoid per-call closure allocation.
        Thread-stack RAM: ~1 MB/thread × max_workers per pool, bounded by M1ResourceGovernor.
    """
    _pool = get_named_pool(pool)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*a: Any, **kw: Any) -> Any:
            return await _run_in_pool(_pool, fn, a, kw, timeout)

        return wrapper

    if func is not None:
        # Direct call: offload_to("pool", fn, arg1, arg2) → return awaitable
        return _run_in_pool(_pool, func, args, {}, timeout)

    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown all pools (call on app exit)
# ─────────────────────────────────────────────────────────────────────────────

def shutdown_all_pools() -> None:
    """Shutdown all NamedPool executors. Call on app exit."""
    global _pools
    with _pools_lock:
        for pool in _pools.values():
            pool.shutdown()
        _pools.clear()
