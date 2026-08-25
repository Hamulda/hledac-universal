"""
ISSUE-010: Subinterpreter Pool — PEP 734 InterpreterPoolExecutor (Python 3.14.6+)

Provides true Python-level parallelism with independent GILs via subinterpreters.
Currently a STUB — activated when HLEDAC_ENABLE_SUBINTERPRETER=1 and CPython 3.14.6+.

M1 8GB considerations:
    - Each subinterpreter ~20-40 MB RSS (shared code, separate data)
    - Max 2-3 subinterpreters recommended for 8GB RAM
    - No pickle overhead (shared memory via immortal objects)
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

__all__ = [
    "is_subinterpreter_available",
    "run_in_subinterpreter",
    "run_batch_in_subinterpreter",
]

_SUBINTERPRETER_AVAILABLE: bool | None = None


def is_subinterpreter_available() -> bool:
    """
    Check if subinterpreter mode is available (CPython 3.14.6+ with PEP 734).

    Returns:
        True if InterpreterPoolExecutor is available and enabled.
    """
    global _SUBINTERPRETER_AVAILABLE

    if _SUBINTERPRETER_AVAILABLE is not None:
        return _SUBINTERPRETER_AVAILABLE

    env_val = os.environ.get("HLEDAC_ENABLE_SUBINTERPRETER", "0")
    if env_val not in ("1", "true", "yes", "on"):
        _SUBINTERPRETER_AVAILABLE = False
        return False

    # Check CPython version: need 3.14.6+
    py_version = sys.version_info
    if py_version < (3, 14, 6):
        _SUBINTERPRETER_AVAILABLE = False
        return False

    # Try to import InterpreterPoolExecutor
    try:
        from concurrent.futures import InterpreterPoolExecutor  # type: ignore[import-not-found]

        _SUBINTERPRETER_AVAILABLE = True
        return True
    except ImportError:
        _SUBINTERPRETER_AVAILABLE = False
        return False


_pool: Any = None


def _get_pool(max_workers: int | None = None) -> Any | None:
    """
    Get or create the InterpreterPoolExecutor singleton.

    Args:
        max_workers: Maximum subinterpreter count. None = adaptive for M1 8GB.

    Returns:
        InterpreterPoolExecutor instance, or None if unavailable.
    """
    global _pool

    if _pool is not None:
        return _pool

    if not is_subinterpreter_available():
        return None

    try:
        from concurrent.futures import InterpreterPoolExecutor  # type: ignore[import-not-found]

        # M1 8GB: limit to 2 subinterpreters for memory
        workers = max_workers or min(2, (os.cpu_count() or 4))
        _pool = InterpreterPoolExecutor(max_workers=workers)
        return _pool
    except Exception:
        return None


async def run_in_subinterpreter[T](
    fn: Callable[..., T],
    /,
    *args: Any,
    timeout: float | None = None,
) -> T:
    """
    Run a pure-Python function in a subinterpreter with its own GIL.

    When subinterpreter mode is enabled and available:
        - The function runs in an isolated subinterpreter with independent GIL
        - Multiple subinterpreter calls can truly run in parallel on M1 cores
        - Result is returned via shared memory (no pickle overhead)

    When disabled (default):
        - Falls back to asyncio.to_thread(fn, *args)
        - Same behavior as before, no regression

    Args:
        fn: Pure Python callable (must be importable in subinterpreter context).
        *args: Positional arguments.
        timeout: Optional deadline in seconds.

    Returns:
        Result of fn(*args).

    Raises:
        asyncio.TimeoutError: If timeout exceeded.
        RuntimeError: If subinterpreter pool unavailable and fallback fails.

    Example:
        # CPU-heavy regex work
        result = await run_in_subinterpreter(heavy_regex, html_content)
    """
    pool = _get_pool()

    if pool is None:
        # Fallback: standard asyncio.to_thread
        if timeout is not None:
            async with asyncio.timeout(timeout):
                return await asyncio.to_thread(fn, *args)
        return await asyncio.to_thread(fn, *args)

    # Subinterpreter path
    loop = asyncio.get_running_loop()

    if timeout is not None:
        async with asyncio.timeout(timeout):
            return await loop.run_in_executor(pool, fn, *args)
    return await loop.run_in_executor(pool, fn, *args)


async def run_batch_in_subinterpreter[T](
    fn: Callable[[Any], T],
    items: list[Any],
    *,
    timeout: float | None = None,
) -> list[T | None]:
    """
    Run a function over a batch of items in subinterpreters.

    Args:
        fn: Pure Python callable taking one item and returning a result.
        items: List of items to process.
        timeout: Optional deadline in seconds.

    Returns:
        List of results, same order as inputs. Errors → None for that item.
    """
    if not items:
        return []

    pool = _get_pool()

    if pool is None:
        # Fallback: process sequentially via asyncio.to_thread
        async def _run_one(item: Any) -> Any:
            try:
                if timeout is not None:
                    async with asyncio.timeout(timeout):
                        return await asyncio.to_thread(fn, item)
                return await asyncio.to_thread(fn, item)
            except Exception:
                return None

        results = await asyncio.gather(*(_run_one(item) for item in items), return_exceptions=True)
        return [None if isinstance(r, BaseException) else r for r in results]

    # Subinterpreter path
    loop = asyncio.get_running_loop()
    futures = [loop.run_in_executor(pool, fn, item) for item in items]

    if timeout is not None:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(*futures, return_exceptions=True)
    else:
        results = await asyncio.gather(*futures, return_exceptions=True)

    # Convert exceptions to None
    return [None if isinstance(r, BaseException) else r for r in results]


def shutdown_subinterpreter_pool() -> None:
    """
    Shutdown the subinterpreter pool.

    Safe to call multiple times. Pool is re-created on next use.
    Call during sprint winddown to free M1 memory.
    """
    global _pool

    if _pool is not None:
        try:
            _pool.shutdown(wait=True)
        except Exception:
            pass
        finally:
            _pool = None
