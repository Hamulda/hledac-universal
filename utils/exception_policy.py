"""
Exception Policy — centralized exception handling for Hledac Universal.

Architecture (P1-01):


    - HOT_PATH (fetching, IOC extraction, evidence log): re_raise=False, always log with exc_info
    - COLD_PATH (initialization, shutdown): re_raise=True, surface to caller
    - NEVER bare `except: pass` — use explicit exception types
    - NEVER `except Exception: pass` without logging — silent corruption!

Severity Integration (Issue #8):
    - ExceptionPolicy now supports severity parameter
    - Severity levels: P0_CRITICAL, P1_ERROR, P2_WARNING, P3_INFO, P4_DEBUG
    - P0 never suppressed, always re-raised
    - P1-P4 rate-limited to reduce log flooding
    - See utils.exception_severity for full severity system

M1 8GB notes:
    - traceback allocation is 50-200 KB per exception
    - HOT_PATH uses logger.debug() to avoid flooding the ring buffer
    - COLD_PATH uses logger.warning() for operator visibility

Usage:
    from hledac.universal.utils.exception_policy import ExceptionPolicy, exc_info

    # HOT PATH — fail-soft, log and continue
    try:
        result = await fetch(url)
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        ExceptionPolicy.handle(e, context=f"fetch({url})", re_raise=False)
        result = None

    # With severity classification
    from hledac.universal.utils.exception_policy import Severity
    try:
        await db.write(data)
    except Exception as e:
        ExceptionPolicy.handle(e, context="db.write", severity=Severity.P0_CRITICAL)

    # Cold path with severity
    try:
        await setup_lmdb()
    except Exception as e:
        ExceptionPolicy.handle(e, context="lmdb_init", re_raise=True, severity=Severity.P1_ERROR)

    # Inline helper for common pattern
    from hledac.universal.utils.exception_policy import gexc
    with gexc(OSError, "file_open"):
        f = open(path)

References:
    - GHOST_INVARIANTS: "no silent except"
    - PYTHON314_MODERNIZATION_AUDIT: ~80% are intentional fail-safe fallbacks
    - Ruff BLE001: blind-except detection
    - Issue #8: Exception Handler Saturation & Diagnostic Blindness
"""

import asyncio
import logging
import sys
import typing
from typing import Final
from _core import aclose

# Lazy import to avoid circular dependency - Severity is only loaded when accessed
_Severity: type | None = None


def _get_severity() -> type:
    global _Severity
    if _Severity is None:
        from hledac.universal.utils.exception_severity import Severity as _S
        _Severity = _S
    return _Severity

class _SeverityPlaceholder:
    """Placeholder for Severity when not imported."""
    pass

if typing.TYPE_CHECKING:
    from hledac.universal.utils.exception_severity import Severity as SeverityLevel
    from collections.abc import Sequence

__all__ = [
    "ExceptionPolicy",
    "HOT_PATH",
    "COLD_PATH",
    "exc_info",
    "gexc",
    "is_hot_path",
    # Severity levels (re-exported for convenience)
    "Severity",
    "SeverityLevel",
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
        severity: "SeverityLevel | None" = None,
        cascade_id: str = "",
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
            severity: Severity classification (P0-P4). If provided, uses
                      severity-based rate-limiting and structured event emission.
            cascade_id: Correlation ID for tracing across operations.
        """
        # Get severity
        if severity is None:
            severity_obj = None
        else:
            severity_obj = severity

        # Determine log level from severity if not explicitly set
        if log_level is None:
            if severity_obj is not None:
                log_level = severity_obj.log_level
            else:
                log_level = (
                    ExceptionPolicy.HOT_PATH_LOG_LEVEL
                    if not re_raise
                    else ExceptionPolicy.COLD_PATH_LOG_LEVEL
    )

        # P0 always re-raises
        if severity_obj is not None and severity_obj.name == "P0_CRITICAL":
            tag = f"[CRIT] {context}" if context else "[CRIT]"
            _logger.error("%s: %s: %s", tag, type(e).__name__, e, exc_info=True)
            raise e

        # Rate-limiting for P1-P4
        if severity_obj is not None:
            from hledac.universal.utils.silent_except_v2 import _get_bucket
            bucket = _get_bucket(context or "unknown", severity_obj)
            if not bucket.try_acquire():
                return  # Rate-limited, skip log

            # Record event for diagnostics
            try:
                from hledac.universal.utils.exception_diagnostics import get_diagnostics
                from hledac.universal.utils.exception_severity import ExceptionEvent
                import time
                import uuid

                # Extract location from traceback
                file = ""
                line = 0
                try:
                    if exc_info:
                        exc_tb = sys.exc_info()[2]
                        if exc_tb:
                            tb = exc_tb
                            while tb.tb_next:
                                tb = tb.tb_next
                            file = tb.tb_frame.f_code.co_filename or ""
                            line = tb.tb_lineno or 0
                except Exception:  # noqa: BLE001
                    pass  # Location extraction failure is non-critical

                event = ExceptionEvent(
                    event_id=uuid.uuid7().hex[:12],
                    cascade_id=cascade_id,
                    severity=severity_obj,
                    scope=context or "unknown",
                    category=context.split('.')[0] if context and '.' in context else context or "unknown",
                    exc_type=type(e).__name__,
                    exc_message=str(e)[:200],
                    exc_hash=f"{type(e).__name__}:{str(e)[:100]}",
                    timestamp=time.time(),
                    first_seen=time.time(),
                    count=1,
                    suppressed_count=0,
                    re_raised=bool(re_raise),
                    outcome="re_raised" if re_raise else "swallowed",
                    file=file,
                    line=line,
    )
                get_diagnostics().record(event)
            except Exception:  # noqa: BLE001
                pass  # Diagnostics failure should not affect main flow

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
        from hledac.universal.utils.exception_policy import gexc

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


# ── Severity re-export for convenience ────────────────────────────────────

# Lazy load Severity to avoid circular dependency
def __getattr__(name: str) -> typing.Any:
    if name == "Severity":
        return _get_severity()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
