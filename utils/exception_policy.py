"""
Exception Policy — centralized exception handling for Hledac Universal.

Architecture (P1-01):
    - HOT_PATH (fetching, IOC extraction, evidence log): re_raise=False, always log with exc_info
    - COLD_PATH (initialization, shutdown): re_raise=True, surface to caller
    - NEVER bare `except: pass` — use explicit exception types
    - NEVER `except Exception: pass` without logging — silent corruption!

M1 8GB notes:
    - traceback allocation is 50-200 KB per exception
    - HOT_PATH uses logger.debug() to avoid flooding the ring buffer
    - COLD_PATH uses logger.warning() for operator visibility

Usage:
    from utils.exception_policy import ExceptionPolicy, exc_info

    # HOT PATH — fail-soft, log and continue
    try:
        result = await fetch(url)
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        ExceptionPolicy.handle(e, context=f"fetch({url})", re_raise=False)
        result = None

    # COLD PATH — re-raise to caller
    try:
        await setup_lmdb()
    except Exception as e:
        ExceptionPolicy.handle(e, context="lmdb_init", re_raise=True)

    # Inline helper for common pattern
    from utils.exception_policy import gexc
    with gexc(OSError, "file_open"):
        f = open(path)

References:
    - GHOST_INVARIANTS: "no silent except"
    - PYTHON314_MODERNIZATION_AUDIT: ~80% are intentional fail-safe fallbacks
    - Ruff BLE001: blind-except detection
"""
from __future__ import annotations

import asyncio
import logging
import sys
import typing
from typing import Final

if typing.TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ExceptionPolicy",
    "HOT_PATH",
    "COLD_PATH",
    "exc_info",
    "gexc",
    "is_hot_path",
]


# ── Policy constants ────────────────────────────────────────────────────────

HOT_PATH: Final[bool] = False  # log + continue (hot: fetching, IOC, evidence)
COLD_PATH: Final[bool] = True  # re-raise to caller (cold: init, shutdown)


# ── Site classification ─────────────────────────────────────────────────────

def _is_hot_path_caller() -> bool:
    """
    Heuristic: detect hot-path caller frames.

    Walks the call stack and returns True if any frame looks like
    a hot-path function (fetching, IOC extraction, evidence log, etc.).

    This is a rough heuristic — prefer explicit HOT_PATH/COLD_PATH in new code.
    """
    frame = sys._getframe(2)  # noqa: SLF001 — internal, documented use
    while frame is not None:
        name = frame.f_code.co_name.lower()
        if any(kw in name for kw in ("fetch", "extract", "parse", "dedup", "evidence")):
            return True
        frame = frame.f_back
    return False


def is_hot_path() -> bool:
    """Runtime check: is the current call site in a hot path?"""
    return _is_hot_path_caller()


# ── Core handler ───────────────────────────────────────────────────────────

class ExceptionPolicy:
    """
    Centralized exception handling policy.

    Defaults:
        HOT_PATH re_raise=False (log + continue)
        COLD_PATH re_raise=True (surface to caller)

    Exc_info semantics:
        - exc_info=True by default (captures stack trace for debugging)
        - exc_info=False only when: (a) exception is expected/minor, (b) perf critical

    Usage:
        ExceptionPolicy.handle(e, context="fetch_public_serp", re_raise=False)
        ExceptionPolicy.handle(e, context="duckdb_write", exc_info=False)  # expected: row missing
    """

    # Hot-path: log at DEBUG, don't re-raise
    HOT_PATH_RE_RAISE: Final[bool] = False
    HOT_PATH_LOG_LEVEL: Final[int] = logging.DEBUG

    # Cold-path: log at WARNING, re-raise
    COLD_PATH_RE_RAISE: Final[bool] = True
    COLD_PATH_LOG_LEVEL: Final[int] = logging.WARNING

    @staticmethod
    def handle(
        e: BaseException,
        *,
        context: str = "",
        re_raise: bool | None = None,
        exc_info: bool = True,
        log_level: int | None = None,
    ) -> None:
        """
        Handle an exception according to policy.

        Args:
            e: The caught exception.
            context: Human-readable context string (e.g. "fetch(url)", "duckdb_write").
                     Shown in log output.
            re_raise: Override the default policy behavior.
                     None = use is_hot_path() heuristic.
            exc_info: Include stack trace in log (default: True).
                      Set False only for expected/minor exceptions where
                      stack trace would be noise.
            log_level: Override the default log level.
        """
        if log_level is None:
            log_level = (
                ExceptionPolicy.HOT_PATH_LOG_LEVEL
                if not re_raise
                else ExceptionPolicy.COLD_PATH_LOG_LEVEL
            )

        # Format: [EXC] context: ExceptionType: message
        tag = f"[EXC] {context}" if context else "[EXC]"
        # Use exc_info only when beneficial and not suppressed
        _logger.log(log_level, "%s: %s: %s", tag, type(e).__name__, e, exc_info=exc_info)

        if re_raise:
            raise e


# ── Module-level logger (lazy, cached) ───────────────────────────────────

_logger_cache: dict[str, logging.Logger] = {}
_logger = logging.getLogger("exception_policy")


# ── Convenience helpers ───────────────────────────────────────────────────

def exc_info(
    *exc_types: type[BaseException],
    context: str = "",
    re_raise: bool | None = None,
) -> typing.ContextManager[None]:
    """
    PEP 654-style exception group filter with structured logging.

    Usage:
        with exc_info(OSError, asyncio.CancelledError, context="lmdb_cleanup"):
            cleanup_lmdb_lock()

    Returns a context manager that:
        - Filters to only the specified exception types
        - Logs the exception with context + type + message
        - Does NOT re-raise (hot-path by default)
        - Suppresses the exception (like contextlib.suppress)

    Equivalent to:
        try:
            ...
        except (OSError, asyncio.CancelledError) as e:
            ExceptionPolicy.handle(e, context=context, re_raise=False)
    """
    return _ExcInfoContext(exc_types, context=context, re_raise=re_raise)


def gexc(
    __exc: type[BaseException],
    __name: str,
    *rest: type[BaseException],
) -> typing.ContextManager[None]:
    """
    Short alias for exc_info() — inline hot-path exception guard.

    Usage:
        from utils.exception_policy import gexc

        with gexc(OSError, "file_open"):
            f = open(path)

    Equivalent to:
        with exc_info(OSError, context="file_open"):
            f = open(path)
    """
    all_types = (__exc, *rest)
    return _ExcInfoContext(all_types, context=__name, re_raise=False)


class _ExcInfoContext:
    """
    Internal context manager for exc_info() / gexc().

    Wraps contextlib.suppress but adds structured logging.
    """

    __slots__ = ("_exc_types", "_context", "_re_raise", "_suppressed")

    def __init__(
        self,
        exc_types: tuple[type[BaseException], ...],
        *,
        context: str,
        re_raise: bool | None,
    ) -> None:
        self._exc_types = exc_types
        self._context = context
        self._re_raise = re_raise
        self._suppressed: BaseException | None = None

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: typing.Any,
    ) -> bool:
        if exc_val is None:
            return True  # No exception

        # Check if this exception type should be handled
        if not self._exc_types:
            # Empty tuple = catch everything (bare except: parity)
            match = True
        else:
            match = isinstance(exc_val, self._exc_types)

        if not match:
            return False  # Re-raise unknown exception

        self._suppressed = exc_val

        # Determine re_raise: exc_info() suppresses by default (re_raise=False).
        # Explicit re_raise=True from cold-path callers overrides.
        re_raise = self._re_raise if self._re_raise is not None else False

        # Log with structured info
        tag = f"[EXC] {self._context}" if self._context else "[EXC]"
        log_level = (
            ExceptionPolicy.HOT_PATH_LOG_LEVEL
            if not re_raise
            else ExceptionPolicy.COLD_PATH_LOG_LEVEL
        )
        _logger.log(
            log_level,
            "%s: %s: %s",
            tag,
            type(exc_val).__name__,
            exc_val,
            exc_info=True,
        )

        if re_raise:
            raise exc_val

        return True  # Suppress
