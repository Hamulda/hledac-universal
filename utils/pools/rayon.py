"""
ISSUE-010: Rayon Pool — Python bindings for Rust rayon pools

Provides unified access to Rust rayon thread pools with GIL-free parallelism.
Optimized for M1 MacBook Air 8GB with topology-aware thread placement.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

__all__ = [
    "RayonPoolsAvailable",
    "run_in_cpu_pool",
    "run_in_io_pool",
    "run_in_mixed_pool",
    "run_in_cpu_pool_async",
    "run_in_io_pool_async",
    "run_in_mixed_pool_async",
]

_RAYON_AVAILABLE: bool | None = None


def RayonPoolsAvailable() -> bool:
    """Return True if Rust rayon pools are available."""
    global _RAYON_AVAILABLE
    if _RAYON_AVAILABLE is not None:
        return _RAYON_AVAILABLE

    from hledac.universal._core.rust_backend import rust

    raw = rust.raw
    if raw.cpu_pool_run is not None and raw.io_pool_run is not None and raw.mixed_pool_run is not None:
        _RAYON_AVAILABLE = True
    else:
        _RAYON_AVAILABLE = False
    return _RAYON_AVAILABLE


def run_in_cpu_pool[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Run CPU-bound function on rayon cpu_pool (4 P-cores).

    Use for: SIMD operations, xxhash parallel, quality_gate, pattern matching.

    Args:
        fn: Synchronous callable to run on the rayon pool.
        *args: Positional arguments passed to fn.
        **kwargs: Keyword arguments passed to fn.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.

    Fail-safe:
        - If rayon unavailable: logs warning, returns None
        - If fn raises: exception propagates (caller should handle)

    Example:
        # From async context:
        result = await asyncio.to_thread(run_in_cpu_pool, hash_func, data)

        # From sync context:
        result = run_in_cpu_pool(some_cpu_bound_func, arg1, arg2)
    """
    if not RayonPoolsAvailable():
        warnings.warn(
            "Rayon pools unavailable (hledac_rust_extensions not built "
            "or cpu_pool_run not exported). "
            "Falling back to direct call.",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    try:
        from hledac.universal._core.rust_backend import rust

        cpu_pool_run = rust.raw.cpu_pool_run
        return cpu_pool_run(fn, *args, **kwargs)
    except Exception:
        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"cpu_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None


def run_in_io_pool[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Run I/O-bound function on rayon io_pool (2 threads).

    Use for: DuckDB queries, graph_traverse, compress operations.

    Args:
        fn: Synchronous callable to run on the rayon pool.
        *args: Positional arguments passed to fn.
        **kwargs: Keyword arguments passed to fn.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.

    Example:
        # From async context:
        result = await asyncio.to_thread(run_in_io_pool, duckdb_query, sql)
    """
    if not RayonPoolsAvailable():
        warnings.warn(
            "Rayon pools unavailable (hledac_rust_extensions not built "
            "or io_pool_run not exported). "
            "Falling back to direct call.",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    try:
        from hledac.universal._core.rust_backend import rust

        io_pool_run = rust.raw.io_pool_run
        return io_pool_run(fn, *args, **kwargs)
    except Exception:
        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"io_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None


def run_in_mixed_pool[T](n_items: int, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Run mixed workload on rayon mixed_pool (1-2 threads, adaptive).

    Thread count is MLX Metal-aware via mx.metal.get_active_memory():
      - Metal < 2 GiB active  → threshold 16  (eager parallelism)
      - Metal 2–4 GiB active   → threshold 32  (normal)
      - Metal > 4 GiB active  → threshold 64  (conservative)

    Args:
        n_items: Number of items in batch (determines thread count).
        fn: Synchronous callable to run on the rayon pool.
        *args: Positional arguments passed to fn.
        **kwargs: Keyword arguments passed to fn.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.

    Example:
        result = await asyncio.to_thread(
            run_in_mixed_pool, len(items), ioc_extract, text
        )
    """
    if not RayonPoolsAvailable():
        warnings.warn(
            "Rayon pools unavailable (hledac_rust_extensions not built "
            "or mixed_pool_run not exported). "
            "Falling back to direct call.",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    try:
        from hledac.universal._core.rust_backend import rust

        mixed_pool_run = rust.raw.mixed_pool_run
        return mixed_pool_run(n_items, fn, *args, **kwargs)
    except Exception:
        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"mixed_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None


async def run_in_cpu_pool_async[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Async wrapper for run_in_cpu_pool.

    Uses rayon_channel.dispatch_cpu when available (~5μs submit vs ~500μs thread::spawn).
    Falls back to asyncio.to_thread otherwise.

    Args:
        fn: Synchronous callable to run.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.
    """
    try:
        from hledac.universal.utils.rayon_channel import dispatch_cpu

        return await dispatch_cpu(fn, *args, **kwargs)
    except ImportError:
        pass
    return await asyncio.to_thread(run_in_cpu_pool, fn, *args, **kwargs)


async def run_in_io_pool_async[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Async wrapper for run_in_io_pool.

    Uses rayon_channel.dispatch_io when available.
    Falls back to asyncio.to_thread otherwise.

    Args:
        fn: Synchronous callable to run.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.
    """
    try:
        from hledac.universal.utils.rayon_channel import dispatch_io

        return await dispatch_io(fn, *args, **kwargs)
    except ImportError:
        pass
    return await asyncio.to_thread(run_in_io_pool, fn, *args, **kwargs)


async def run_in_mixed_pool_async[T](n_items: int, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T | None:
    """
    Async wrapper for run_in_mixed_pool.

    Uses rayon_channel.dispatch_mixed when available.
    Falls back to asyncio.to_thread otherwise.

    Args:
        n_items: Number of items in batch (determines thread count).
        fn: Synchronous callable to run.
        *args: Positional arguments.
        **kwargs: Keyword arguments.

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable.
    """
    try:
        from hledac.universal.utils.rayon_channel import dispatch_mixed

        return await dispatch_mixed(n_items, fn, *args, **kwargs)
    except ImportError:
        pass
    return await asyncio.to_thread(run_in_mixed_pool, n_items, fn, *args, **kwargs)
