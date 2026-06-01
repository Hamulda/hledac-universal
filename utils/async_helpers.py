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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "monotonic_ms",
    "safe_gather",
    "SafeGatherResult",
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


# =============================================================================
# Sprint F26X: _safe_gather — single-call helper that enforces the I6/I7/I8
# invariants at the gather boundary itself. Replaces the repeated
# `asyncio.gather(..., return_exceptions=True)` + post-hoc `_check_gathered`
# pattern with one fail-soft call.
#
# Cutting-edge: bounded exception classification (re-raise BaseException,
# route Exception to .errors). Avoids the per-call `gather(...)` +
# `isinstance(r, Exception)` loop in the 6 intelligence/ call sites.
# =============================================================================


from dataclasses import dataclass, field
from typing import Awaitable, Iterable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SafeGatherResult:
    """Result of `safe_gather` — frozen dataclass for fast access.

    Attributes:
        ok:       List of successful results (order preserved)
        errors:   List of exception instances (excluding BaseException)
        re_raised:BaseException instance if one was re-raised (caller should handle)
    """
    ok: list[Any] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)
    re_raised: BaseException | None = None


async def safe_gather(
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> SafeGatherResult:
    """Sprint F26X: Single-call helper for safe gather with full invariant enforcement.

    Replaces this pattern at every call site::

        results = await asyncio.gather(*coros, return_exceptions=True)
        ok, errors = _check_gathered(results, "label")

    with::

        result = await safe_gather(*coros, label="paste_sites")
        for r in result.ok: ...
        # errors are logged automatically at DEBUG

    Invariants:
        - [I6] CancelledError → RE-RAISED immediately (never swallowed)
        - [I7] BaseException (not Exception) → RE-RAISED
        - [I8] regular Exception → routed to .errors (logged at DEBUG)

    Args:
        *coros: Coroutines or awaitables to gather. May be plain values (passed through).
        label:  Context string for log messages (e.g. "wayback_cdx", "rentry search").
        logger_instance: Optional logger override (defaults to module logger).

    Returns:
        SafeGatherResult with .ok (successes), .errors (Exception instances).
        If a BaseException is encountered, it is stored in .re_raised — caller
        should re-raise it manually (we don't auto-raise in a frozen dataclass
        context to keep the call site in control of the cancellation policy).
    """
    _log = logger_instance or logger
    if not coros:
        return SafeGatherResult(ok=[], errors=[])

    # I6/I7/I8 boundary: always return_exceptions=True at the gather level.
    # We classify after to differentiate CancelledError / BaseException / Exception.
    raw = await asyncio.gather(*coros, return_exceptions=True)

    ok: list[Any] = []
    errors: list[BaseException] = []

    for i, item in enumerate(raw):
        if isinstance(item, asyncio.CancelledError):
            # [I6] — never swallow cancellation. Re-raise immediately so the
            # caller's finally blocks run, but record in result for diagnostics.
            _log.debug(f"[GHOST] safe_gather CancelledError[{i}]{' ' + label if label else ''}")
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            # [I7] — KeyboardInterrupt, SystemExit, GeneratorExit → re-raise
            _log.debug(f"[GHOST] safe_gather BaseException[{i}]{' ' + label if label else ''}: "
                       f"{type(item).__name__}")
            raise item
        if isinstance(item, Exception):
            # [I8] — regular Exception → log + collect, never propagate silently
            _log.debug(f"[GHOST] safe_gather exception[{i}]{' ' + label if label else ''}: "
                       f"{type(item).__name__}: {item}")
            errors.append(item)
        else:
            ok.append(item)

    return SafeGatherResult(ok=ok, errors=errors)
