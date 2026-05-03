"""
Ghost Async Helpers - Gather hygiene and blocking-I/O guards
========================================================

Provides:
- _check_gathered(): filter exceptions, log, return valid results
- Async DNS helpers using loop.getaddrinfo()

Invariants enforced:
- asyncio.gather(..., return_exceptions=True) always
- loop.getaddrinfo() for DNS (never socket.getaddrinfo in async)
- time.monotonic() for intervals/cooldowns
- asyncio.timeout() preferred over wait_for()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


def _check_gathered(
    results: List[Any],
    logger_instance: Optional[logging.Logger] = None,
    context: str = ""
) -> List[Any]:
    """
    Filter gather results: separate exceptions from valid values.

    All exceptions are logged (debug level) and only non-exception
    values are returned. This prevents Exception objects from leaking
    into downstream code that expects actual results.

    Args:
        results: raw results from asyncio.gather(return_exceptions=True)
        logger_instance: optional logger for output (defaults to module logger)
        context: optional context string for log messages (e.g. "S3 enumeration")

    Returns:
        List of non-exception results only
    """
    _log = logger_instance or logger
    valid: List[Any] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            _log.debug(f"[GHOST] gather exception[{i}]{' '+context if context else ''}: "
                       f"{type(result).__name__}: {result}")
        else:
            valid.append(result)
    return valid


async def async_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int = 0,
    type_: int = 0,
    proto: int = 0,
    timeout: Optional[float] = 5.0,
) -> List[Any]:
    """
    Async DNS resolution via loop.getaddrinfo().

    Never use socket.getaddrinfo() directly in async code -
    it blocks the event loop. Use this helper instead.

    Args:
        host: hostname to resolve
        port: port number
        family: address family (0 = auto)
        type_: socket type (0 = auto)
        proto: protocol (0 = auto)
        timeout: resolution timeout in seconds

    Returns:
        List of (family, type, proto, canonname, sockaddr) tuples
    """
    loop = asyncio.get_running_loop()
    if timeout is not None and timeout > 0:
        async with asyncio.timeout(timeout):
            return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)
    return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)


def monotonic_ms() -> float:
    """Return current monotonic time in milliseconds (float)."""
    return time.monotonic() * 1000.0


__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "monotonic_ms",
]
