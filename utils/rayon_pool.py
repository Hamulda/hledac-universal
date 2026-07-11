"""
R4.1: Unified Rayon-based Pipeline — Python bindings for Rust rayon pools
===========================================================================

Unified execution layer: Python asyncio → Rust rayon (cpu_pool) → CPU-bound
                                            → Rust rayon (io_pool)  → I/O-bound

ARCHITECTURE
------------
  asyncio loop (main thread)
      │
      ├── run_in_cpu_pool(fn, *args)  → rayon cpu_pool (4 P-cores)
      │                                   Used for: SIMD, hashing, pattern match
      │
      ├── run_in_io_pool(fn, *args)   → rayon io_pool (2 threads)
      │                                   Used for: DuckDB, graph_traverse
      │
      └── run_in_mixed_pool(n, fn, *args) → rayon mixed_pool (1-2 threads)
                                            Used for: IOC extract, url_ops, simhash

WHY RAYON OVER PYTHON ThreadPoolExecutor
----------------------------------------
  1. GIL-free parallelism — true concurrent CPU on M1 (4P + 4E cores)
  2. Work-stealing scheduler — optimal load balancing for uneven workloads
  3. M1 cache-friendly — shared address space, zero-copy data transfer
  4. macOS QoS integration — E-cores auto-assigned for I/O-bound threads
  5. Memory overhead: rayon ~0 KB vs ThreadPoolExecutor ~few MB

MIGRATION PATH
--------------
  PŘED:  result = await asyncio.get_running_loop().run_in_executor(
              CPU_EXECUTOR, cpu_bound_func, args)

  PO:     from utils.rayon_pool import run_in_cpu_pool
          result = await run_in_cpu_pool(cpu_bound_func, args)

  ALTERNATIVNĚ (přímo v asyncio):
  PO:     result = await asyncio.to_thread(run_in_cpu_pool, cpu_bound_func, args)

  Poznámka: run_in_cpu_pool JE synchronous — volá rayon pool.install() uvnitř.
  Pro async integraci použij asyncio.to_thread() jako wrapper.

POOL SELECTION GUIDE
--------------------
  ┌──────────────────────────────────────────────────────────────────┐
  │ Workload              │ Pool           │ Threads │ Modules       │
  ├───────────────────────┼───────────────┼─────────┼──────────────┤
  │ CPU-bound (SIMD/hot)  │ run_in_cpu_pool│  4     │ quality_gate, │
  │                       │               │         │ xxhash_par,   │
  │                       │               │         │ simd_similarity│
  │ I/O-bound (DuckDB)    │ run_in_io_pool │  2     │ graph_traverse,│
  │                       │               │         │ compress      │
  │ Mixed (IOC extract)   │ run_in_mixed_pool│ 1-2  │ url_ops,     │
  │                       │               │         │ ioc_fast,     │
  │                       │               │         │ simhash       │
  └──────────────────────────────────────────────────────────────────┘

M1 8GB CALIBRATION (F270, 2026-06-25)
--------------------------------------
  - CPU-bound threshold: 32 items (was 64 for 2-thread)
  - I/O-bound threshold: 64 items (DuckDB conn setup amortized)
  - Chunk: 4 threads × 32 items = 128 (CPU-bound)
  - Chunk: 2 threads × 64 items = 128 (I/O-bound)

FAIL-SAFE INVARIANTS
-------------------
  - Every pool function returns None on exception (never raises)
  - All pools are process-wide singletons (LazyLock, thread-safe)
  - Pool thread names: hledac-cpu-{0..3}, hledac-io-{0..1}
  - Stack size: 1.5 MiB per thread (prevents stack overflow on deep recursion)

USAGE EXAMPLES
--------------
  # Synchronous call (from async context)
  result = await asyncio.to_thread(run_in_cpu_pool, hash_func, data)

  # Direct synchronous call (from sync context)
  result = run_in_cpu_pool(pattern_match, text, patterns)

  # I/O-bound (DuckDB operations)
  result = await asyncio.to_thread(run_in_io_pool, duckdb_query, sql)

  # Mixed workload with adaptive threading
  result = await asyncio.to_thread(run_in_mixed_pool, len(items), ioc_extract, text)

TESTING
-------
  Tests in: tests/test_rayon_pool.py
  Run with: pytest tests/test_rayon_pool.py -v
"""



import sys
from typing import Any, TypeVar
from collections.abc import Callable

__all__ = [
    "run_in_cpu_pool",
    "run_in_io_pool",
    "run_in_mixed_pool",
    "RayonPoolsAvailable",
]

# ------------------------------------------------------------------|
# Lazy import — rayon pools loaded on first use, not at module load  |
# ------------------------------------------------------------------|

_RAYON_AVAILABLE: bool | None = None


def _check_rayon_availability() -> bool:
    """Check if Rust rayon extension is available (not all builds have it)."""
    global _RAYON_AVAILABLE
    if _RAYON_AVAILABLE is not None:
        return _RAYON_AVAILABLE
    try:
        from hledac_rust_extensions import (
            cpu_pool_run,
            io_pool_run,
            mixed_pool_run,
        )
        _RAYON_AVAILABLE = True
    except ImportError:
        _RAYON_AVAILABLE = False
    return _RAYON_AVAILABLE


def RayonPoolsAvailable() -> bool:
    """Return True if Rust rayon pools are available."""
    return _check_rayon_availability()


# ------------------------------------------------------------------|
# Type variables                                                    |
# ------------------------------------------------------------------|

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


# ------------------------------------------------------------------|
# CPU-bound pool — 4 P-cores for SIMD/hot CPU workloads            |
# ------------------------------------------------------------------|


def run_in_cpu_pool[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:  # type: ignore[type-arg]
    """
    Run CPU-bound function on rayon cpu_pool (4 P-cores).

    Use for: SIMD operations, xxhash parallel, quality_gate, pattern matching.

    Args:
        fn: Synchronous callable to run on the rayon pool
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Fail-safe:
        - If rayon unavailable: logs warning, returns None
        - If fn raises: exception propagates (caller should handle)

    Example:
        # From async context:
        result = await asyncio.to_thread(run_in_cpu_pool, hash_func, data)

        # From sync context:
        result = run_in_cpu_pool(some_cpu_bound_func, arg1, arg2)
    """
    if not _check_rayon_availability():
        import warnings

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
        from hledac_rust_extensions import cpu_pool_run

        return cpu_pool_run(fn, *args, **kwargs)
    except Exception:
        # Fail-safe: never let rayon errors propagate to caller
        import warnings

        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"cpu_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)  # type: ignore[return-value]
        except Exception:
            return None  # type: ignore[return-value]


# ------------------------------------------------------------------|
# I/O-bound pool — 2 threads for DuckDB/graph_traverse              |
# ------------------------------------------------------------------|


def run_in_io_pool[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:  # type: ignore[type-arg]
    """
    Run I/O-bound function on rayon io_pool (2 threads).

    Use for: DuckDB queries, graph_traverse, compress operations.

    Args:
        fn: Synchronous callable to run on the rayon pool
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Fail-safe:
        - If rayon unavailable: logs warning, returns None
        - If fn raises: exception propagates (caller should handle)

    Example:
        # From async context:
        result = await asyncio.to_thread(run_in_io_pool, duckdb_query, sql)

        # From sync context:
        result = run_in_io_pool(read_duckdb, query)
    """
    if not _check_rayon_availability():
        import warnings

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
        from hledac_rust_extensions import io_pool_run

        return io_pool_run(fn, *args, **kwargs)
    except Exception:
        import warnings

        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"io_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)  # type: ignore[return-value]
        except Exception:
            return None  # type: ignore[return-value]


# ------------------------------------------------------------------|
# Mixed pool — adaptive 1-2 threads based on batch size             |
# ------------------------------------------------------------------|


def run_in_mixed_pool[T](n_items: int, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:  # type: ignore[type-arg]
    """
    Run mixed workload on rayon mixed_pool (1-2 threads, adaptive).

    Thread count is MLX Metal-aware via mx.metal.get_active_memory():
      - Metal < 2 GiB active  → threshold 16  (eager parallelism)
      - Metal 2–4 GiB active   → threshold 32  (normal, F270 calibration)
      - Metal > 4 GiB active  → threshold 64  (conservative, sequential)
    Eliminates pool spawn overhead (~0.5ms) for small batches.

    Use for: IOC extract, url_ops, simhash, html_parse workloads.

    Args:
        n_items: Number of items in batch (determines thread count)
        fn: Synchronous callable to run on the rayon pool
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Fail-safe:
        - If rayon unavailable: logs warning, returns None
        - If fn raises: exception propagates (caller should handle)

    Example:
        # Batch size adaptive:
        result = await asyncio.to_thread(
            run_in_mixed_pool, len(items), ioc_extract, text
        )
    """
    if not _check_rayon_availability():
        import warnings

        warnings.warn(
            "Rayon pools unavailable (hledac_rust_extensions not built "
            "or mixed_pool_run not exported). "
            "Falling back to direct call.",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)  # type: ignore[return-value]
        except Exception:
            return None  # type: ignore[return-value]

    try:
        from hledac_rust_extensions import mixed_pool_run

        return mixed_pool_run(n_items, fn, *args, **kwargs)
    except Exception:
        import warnings

        fn_name = getattr(fn, "__name__", repr(fn))
        warnings.warn(
            f"mixed_pool_run failed for {fn_name}, falling back to direct call",
            RuntimeWarning,
            stacklevel=2,
        )
        try:
            return fn(*args, **kwargs)  # type: ignore[return-value]
        except Exception:
            return None  # type: ignore[return-value]


# ------------------------------------------------------------------|
# Asyncio convenience wrappers                                      |
# ------------------------------------------------------------------|

import asyncio  # noqa: E402
import warnings as _warnings  # noqa: E402


async def run_in_cpu_pool_async(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """
    Async wrapper for run_in_cpu_pool.

    Runs CPU-bound fn on rayon cpu_pool without blocking the asyncio event loop.

    Args:
        fn: Synchronous callable to run
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Example:
        result = await run_in_cpu_pool_async(hash_func, data)
    """
    return await asyncio.to_thread(run_in_cpu_pool, fn, *args, **kwargs)


async def run_in_io_pool_async(
    fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """
    Async wrapper for run_in_io_pool.

    Runs I/O-bound fn on rayon io_pool without blocking the asyncio event loop.

    Args:
        fn: Synchronous callable to run
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Example:
        result = await run_in_io_pool_async(duckdb_query, sql)
    """
    return await asyncio.to_thread(run_in_io_pool, fn, *args, **kwargs)


async def run_in_mixed_pool_async(
    n_items: int, fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """
    Async wrapper for run_in_mixed_pool.

    Runs mixed workload fn on rayon mixed_pool without blocking the asyncio event loop.

    Args:
        n_items: Number of items in batch (determines thread count)
        fn: Synchronous callable to run
        *args: Positional arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None if pool unavailable

    Example:
        result = await run_in_mixed_pool_async(len(items), ioc_extract, text)
    """
    return await asyncio.to_thread(run_in_mixed_pool, n_items, fn, *args, **kwargs)


# ------------------------------------------------------------------|
# Backward compatibility shims                                       |
# ------------------------------------------------------------------|

async def run_in_rayon_pool[T](
    fn: Callable[..., T], *args: Any, **kwargs: Any
) -> T | None:
    """
    DEPRECATED: Use run_in_cpu_pool_async or run_in_io_pool_async.

    Generic rayon pool runner — dispatches based on function name heuristic.
    Kept for backward compatibility during migration.

    Args:
        fn: Function to run (CPU-bound assumed)
        *args: Arguments passed to fn
        **kwargs: Keyword arguments passed to fn

    Returns:
        Result of fn(*args, **kwargs), or None
    """
    _warnings.warn(
        "run_in_rayon_pool is deprecated. "
        "Use run_in_cpu_pool_async or run_in_io_pool_async directly.",
        DeprecationWarning,
        stacklevel=2,
    )
    return await run_in_cpu_pool_async(fn, *args, **kwargs)
