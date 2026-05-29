# hledac/universal/utils/async_helpers.py
# Ghost Async Helpers - Gather hygiene and blocking-I/O guards
#
# Provides:
# - _check_gathered(): filter exceptions, log, ret valid results
# - Async DNS helpers using loop.getaddrinfo()
#
# Invariants enforced:
# - asyncio.gather(..., return_exceptions=True) always
# - _check_gathered() processes results after every gather call
"""
Ghost Async Helpers - Gather hygiene and blocking-I/O guards

Provides:
- _check_gathered(): filter exceptions, log, ret valid results
- Async DNS helpers using loop.getaddrinfo()

Invariants enforced:
- asyncio.gather(..., return_exceptions=True) always
- _check_gathered() processes results after every gather call
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "monotonic_ms",
]

logger = logging.getLogger(__name__)


def _check_gathered(
    results: list[Any],
    logger_instance: logging.Logger | None = None,
    ctx: str = ""
) -> tuple[list[Any], list[Any]]:
    """
    Process results from asyncio.gather(..., return_exceptions=True).

    Input:  list returned by asyncio.gather(return_exceptions=True)
    Output: (ok_results, error_results)

    Invariants enforced:
    - [I6] asyncio.CancelledError → RE-RAISED immediately (never swallowed)
    - [I7] non-Exception BaseException (KeyboardInterrupt, SystemExit) → RE-RAISED
    - [I8] regular Exception → routed to error_results (not returned as ok)

    Args:
        results: raw results from asyncio.gather(return_exceptions=True)
        logger_instance: optional logger for output (defaults to mod logger)
        ctx: optional context string for log messages (e.g. "S3 enumeration")

    Returns:
        Tuple of (ok_results, error_results)
        - ok_results: items that are not Exception instances
        - error_results: Exception instances (for logging/handling downstream)
    """
    ok_results: list[Any] = []
    error_results: list[Any] = []
    _log = logger_instance or logger

    for i, item in enumerate(results):
        if isinstance(item, asyncio.CancelledError):
            # [I6] — CancelledError must never be swallowed
            _log.debug(f"[GHOST] gather CancelledError[{i}]{' ' + ctx if ctx else ''} — re-raising")
            raise item
        if not isinstance(item, Exception):
            # Regular non-exception value — ok
            ok_results.append(item)
        else:
            # [I8] — regular Exception → route to errors
            _log.debug(f"[GHOST] gather exception[{i}]{' ' + ctx if ctx else ''}: "
                       f"{type(item).__name__}: {item}")
            error_results.append(item)

    return ok_results, error_results


async def async_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int = 0,
    type_: int = 0,
    proto: int = 0,
    timeout: float | None = None,
) -> list[tuple[int, int, int, str, Any]]:
    """
    Async wrapper around loop.getaddrinfo() with optional timeout.

    Args:
        host: hostname to resolve
        port: port number
        family: address family (0 = auto)
        type_: socket type (0 = auto)
        proto: protocol (0 = auto)
        timeout: max seconds to wait (None = use loop default)

    Returns:
        List of (family, type, proto, canonname, sockaddr) tuples
    """
    loop = asyncio.get_running_loop()
    if timeout is not None and timeout > 0:
        async with asyncio.timeout(timeout):
            return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)
    else:
        return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)


def monotonic_ms() -> float:
    """Return current monotonic time in milliseconds (float)."""
    return time.monotonic() * 1000.0
