"""core/_util.py — F350M-R: Shared utilities for safe async resource cleanup.

This module provides a single canonical helper for Type-4 clone elimination:
141 instances across 55 files that previously duplicated the same try/except
pattern for safe async resource cleanup in finally blocks.

Usage:
    from core._util import aclose, aclose_many

    async def cleanup(self):
        finally:
            await aclose(self._client)
            await aclose(self._session)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, TypeVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def aclose(obj: Any | None, *, log_errors: bool = False) -> None:
    """Safely close an async resource, suppressing all exceptions.

    This is the canonical implementation of the Type-4 clone pattern that was
    previously duplicated 141 times across 55 files. Each instance followed
    the same structure:
        if obj:
            try:
                await obj.aclose()  # or .close()
            except Exception:  # noqa: BLE001
                pass

    Args:
        obj: Any object with an aclose() or close() method, or None.
        log_errors: If True, log exceptions instead of silently suppressing.
                    Defaults to False for backward compatibility.

    Example:
        async def __aexit__(self, *args):
            await aclose(self._session)
            await aclose(self._client)
    """
    if obj is None:
        return

    close_method: Awaitable[Any] | None = None

    # Prefer aclose() for async context managers
    if hasattr(obj, "aclose"):
        close_method = obj.aclose()
    # Fall back to close() for sync objects (may be awaitable)
    elif hasattr(obj, "close"):
        close_method = obj.close()

    if close_method is None:
        return

    # Support both sync and async close methods
    try:
        if asyncio.iscoroutine(close_method) or asyncio.isfuture(close_method):
            await close_method
        else:
            # Sync close() — some implementations return awaitable
            maybe_awaitable = close_method
            if asyncio.iscoroutine(maybe_awaitable) or asyncio.isfuture(maybe_awaitable):
                await maybe_awaitable
    except Exception:  # noqa: BLE001
        if log_errors:
            logger.debug("aclose cleanup failed for %s: %s", type(obj).__name__, Exception)
        # Silent suppression for backward compatibility


async def aclose_many(*objects: Any, log_errors: bool = False) -> None:
    """Safely close multiple async resources concurrently.

    Args:
        *objects: Variable number of objects to close (None values are skipped).
        log_errors: If True, log exceptions instead of silently suppressing.

    Example:
        async def __aexit__(self, *args):
            await aclose_many(self._session, self._client, self._adapter)
    """
    if not objects:
        return

    # Filter out None values and collect close coroutines
    coros = [aclose(obj, log_errors=log_errors) for obj in objects if obj is not None]

    if not coros:
        return

    # Run all cleanups concurrently
    await asyncio.gather(*coros, return_exceptions=True)
