"""
R7: InterpreterPoolExecutor — subinterpreter-based CPU parallelism (Python 3.14.6+)
====================================================================================

STUB — NOT YET ACTIVATED. Gated behind ``HLEDAC_ENABLE_SUBINTERPRETER=0``.

Python 3.14.6+ introduces ``concurrent.futures.InterpreterPoolExecutor``
(PEP 756), which allows running pure Python CPU-bound work in isolated
subinterpreters with independent GILs — true Python-level parallelism
on M1 multi-core without the GIL bottleneck.

This module provides a forward-compatible wrapper that:
  1. Detects CPython 3.14.6+ and the InterpreterPoolExecutor availability
  2. Activates only when ``HLEDAC_ENABLE_SUBINTERPRETER=1`` is set
  3. Falls back to ``asyncio.to_thread`` (default ThreadPoolExecutor) otherwise
  4. Provides a ``run_in_subinterpreter(fn, *args)`` async API

WHY SUBINTERPRETERS OVER THREADS
---------------------------------
  - Each subinterpreter has its OWN GIL → true CPU parallelism
  - No pickle/serialize overhead (shared memory via immortal objects)
  - Lower memory than ProcessPoolExecutor (shared code, separate data)
  - M1 8GB: ~20-40 MB per subinterpreter (vs ~200 MB per process)

USAGE (when activated)
----------------------
  from hledac.universal.utils.subinterpreter_pool import run_in_subinterpreter

  # CPU-bound Python work (regex, JSON parse, text processing)
  result = await run_in_subinterpreter(heavy_regex_work, text_input)

CURRENT STATUS: STUB (HLEDAC_ENABLE_SUBINTERPRETER=0 by default)
----------------------------------------------------------------
  - InterpreterPoolExecutor requires CPython 3.14.6+ with free-threading
    enabled (--disable-gil build flag or PEP 703 runtime)
  - Not yet verified on macOS CPython 3.14.6 release builds
  - This module will be activated once verification is complete
  - In the meantime, CPU-bound work goes through rayon_channel dispatch
    (which is already significantly faster than asyncio.to_thread for
    GIL-releasing Rust work)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, TypeVar

from hledac.universal.utils.asyncx import safe_wait_for

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

_SUBINTERPRETER_ENABLED: bool | None = None


def _is_subinterpreter_enabled() -> bool:
    """Check if subinterpreter mode is enabled via env var + CPython version."""
    global _SUBINTERPRETER_ENABLED
    if _SUBINTERPRETER_ENABLED is not None:
        return _SUBINTERPRETER_ENABLED

    env_val = os.environ.get("HLEDAC_ENABLE_SUBINTERPRETER", "0")
    if env_val not in ("1", "true", "yes", "on"):
        _SUBINTERPRETER_ENABLED = False
        return False

    # Check CPython version: need 3.14.6+
    py_version = sys.version_info
    if py_version < (3, 14, 6):
        logger.debug(
            "subinterpreter_pool: CPython %s < 3.14.6, "
            "InterpreterPoolExecutor not available. "
            "Set HLEDAC_ENABLE_SUBINTERPRETER=0 or upgrade CPython.",
            f"{py_version.major}.{py_version.minor}.{py_version.micro}",
        )
        _SUBINTERPRETER_ENABLED = False
        return False

    # Try to import InterpreterPoolExecutor
    try:
        from concurrent.futures import InterpreterPoolExecutor  # type: ignore[import-not-found]  # noqa: F401
        _SUBINTERPRETER_ENABLED = True
        logger.info("subinterpreter_pool: InterpreterPoolExecutor available (CPython %s)", sys.version)
        return True
    except ImportError:
        logger.debug(
            "subinterpreter_pool: InterpreterPoolExecutor not found in "
            "concurrent.futures. Ensure CPython 3.14.6+ is installed with "
            "free-threading support."
        )
        _SUBINTERPRETER_ENABLED = False
        return False


# ---------------------------------------------------------------------------
# Lazy singleton — created on first use, reused across calls
# ---------------------------------------------------------------------------

_pool: Any = None


def _get_pool(max_workers: int | None = None) -> Any:
    """Get or create the InterpreterPoolExecutor singleton.

    Args:
        max_workers: Maximum subinterpreter count. None = cpu_count.
                     On M1 8GB, recommended max is 2-3 (memory constraint).

    Returns:
        InterpreterPoolExecutor instance, or None if unavailable.
    """
    global _pool
    if _pool is not None:
        return _pool

    if not _is_subinterpreter_enabled():
        return None

    try:
        from concurrent.futures import InterpreterPoolExecutor  # type: ignore[import-not-found]

        # M1 8GB: limit to 2 subinterpreters to stay within RAM budget
        # Each subinterpreter ~20-40 MB RSS (shared code, separate data)
        # 2 subinterpreters × ~40 MB = ~80 MB + 1-2 GB for MLX = fits in 8GB
        workers = max_workers or min(2, (os.cpu_count() or 4))
        _pool = InterpreterPoolExecutor(max_workers=workers)

        logger.info(
            "subinterpreter_pool: created InterpreterPoolExecutor with %d workers",
            workers,
        )
        return _pool
    except Exception:
        logger.warning("subinterpreter_pool: failed to create pool", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_in_subinterpreter(
    fn: Any,
    /,
    *args: Any,
    timeout: float | None = None,
) -> Any:
    """Run a pure-Python function in a subinterpreter with its own GIL.

    When subinterpreter mode is enabled and available:
      - The function runs in an isolated subinterpreter with independent GIL
      - Multiple subinterpreter calls can truly run in parallel on M1 cores
      - Result is returned via shared memory (no pickle overhead)

    When disabled (default):
      - Falls back to ``asyncio.to_thread(fn, *args)``
      - Same behavior as before R7, no regression

    Args:
        fn: Pure Python callable (must be importable in subinterpreter context)
        *args: Positional arguments (must be picklable when subinterpreter active)
        timeout: Optional deadline in seconds.

    Returns:
        Result of fn(*args).

    Raises:
        asyncio.TimeoutError: If timeout exceeded.
        RuntimeError: If subinterpreter pool unavailable and fallback fails.

    Example:
        # CPU-heavy regex work — runs in parallel with other tasks
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


async def run_batch_in_subinterpreters(
    fn: Any,
    items: list[Any],
    *,
    timeout: float | None = None,
) -> list[Any]:
    """Run a function over a batch of items, each in its own subinterpreter.

    When subinterpreter mode is active, items are processed in parallel
    across subinterpreters (limited by max_workers).

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
        # Fallback: process sequentially via asyncio.to_thread (no parallelism)
        async def _run_one(item: Any) -> Any:
            try:
                if timeout is not None:
                    async with asyncio.timeout(timeout):
                        return await asyncio.to_thread(fn, item)
                return await asyncio.to_thread(fn, item)
            except Exception:
                logger.debug("subinterpreter_pool: batch item failed", exc_info=True)
                return None

        return list(await asyncio.gather(*(_run_one(item) for item in items)))

    # Subinterpreter path: submit all, collect results
    loop = asyncio.get_running_loop()
    futures = [loop.run_in_executor(pool, fn, item) for item in items]

    if timeout is not None:
        results = await safe_wait_for(
            asyncio.gather(*futures, return_exceptions=True),
            timeout=timeout,
        )
    else:
        results = await asyncio.gather(*futures, return_exceptions=True)

    # Convert exceptions to None
    return [
        None if isinstance(r, BaseException) else r
        for r in results
    ]


def shutdown_pool() -> None:
    """Shutdown the subinterpreter pool.

    Safe to call multiple times. Pool is re-created on next use.
    Call during sprint winddown to free M1 memory.
    """
    global _pool
    if _pool is not None:
        try:
            _pool.shutdown(wait=True)
            logger.info("subinterpreter_pool: pool shut down")
        except Exception:
            logger.debug("subinterpreter_pool: shutdown failed", exc_info=True)
        finally:
            _pool = None


__all__ = [
    "run_in_subinterpreter",
    "run_batch_in_subinterpreters",
    "shutdown_pool",
    "_is_subinterpreter_enabled",
]
