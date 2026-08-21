"""
Silent Exception Helper v2 — Severity-aware exception handling.

Enhanced version of silent_except_helper with:
  - Severity classification integration
  - Rate-limiting for repeated exceptions
  - Structured event emission
  - Backward compatibility with existing patterns

Usage (3 modern styles):

    # 1. Context manager with severity
    from hledac.universal.utils.silent_except_v2 import silenced, Severity

    with silenced(OSError, name="cleanup_lock", severity=Severity.P2_WARNING):
        cleanup_stale_lock()

    # 2. Function-level severity decorator
    from hledac.universal.utils.silent_except_v2 import silence_with_severity

    @silence_with_severity(ValueError, Severity.P2_WARNING, name="parse_legacy")
    def parse_legacy(raw: str) -> dict | None:
        return json.loads(raw)["field"]

    # 3. Direct severity logging
    from hledac.universal.utils.silent_except_v2 import severity_swallow

    try:
        risky_op()
    except Exception as e:
        severity_swallow(e, "risky_op", Severity.P3_INFO)

Design:
  - P0 never suppressed (fail-closed for critical errors)
  - P1-P2 aggregated with rate-limiting
  - P3-P4 sampled to reduce noise
  - Event registry for diagnostic visibility

M1 8GB notes:
  - Uses slots dataclass for minimal memory
  - Token bucket for efficient rate-limiting
  - Weakref for automatic cleanup
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from hledac.universal.utils.exception_severity import (
    ExceptionEvent,
    Severity,
)

__all__ = [
    "silenced",
    "silence_with_severity",
    "severity_swallow",
    "safe_swallow",
    "create_exception_event",
    "Severity",
    "TokenBucket",
]

# ── Backward Compatibility Layer ────────────────────────────────────────────

# Map old pass-through comments to severity levels
# Ordered by specificity - longer patterns should come first
_SEVERITY_HINTS: dict[str, Severity] = {
    # Best-effort patterns (P3/P4)
    "best-effort; prewrite is advisory": Severity.P4_DEBUG,
    "best-effort; concat failure": Severity.P3_INFO,
    "best-effort; count; non-critical": Severity.P4_DEBUG,
    "best-effort; advisory only": Severity.P4_DEBUG,
    "best-effort; DLQ unavailable; non-critical": Severity.P4_DEBUG,
    "best-effort; DuckDB scan; non-critical": Severity.P4_DEBUG,
    "best-effort; DuckDB init; non-critical": Severity.P4_DEBUG,
    "best-effort; export failure; non-critical": Severity.P4_DEBUG,
    "best-effort; vacuum failure; non-critical": Severity.P4_DEBUG,
    "best-effort; rowgroup count; non-critical": Severity.P4_DEBUG,
    "best-effort; row count; non-critical": Severity.P4_DEBUG,
    "best-effort; PyArrow read; non-critical": Severity.P4_DEBUG,
    "best-effort; PyArrow count; non-critical": Severity.P4_DEBUG,
    "best-effort; lock failure; non-critical": Severity.P4_DEBUG,
    "best-effort; cleanup failure; non-critical": Severity.P4_DEBUG,
    "best-effort; httpfs load; non-critical": Severity.P4_DEBUG,
    "best-effort; remote read; non-critical": Severity.P4_DEBUG,
    "best-effort; postgres load; non-critical": Severity.P4_DEBUG,
    "best-effort; QoS hinting; non-critical": Severity.P4_DEBUG,
    "best-effort; non-critical": Severity.P4_DEBUG,
    "best-effort": Severity.P3_INFO,
    # Fail-safe patterns (P2)
    "fail-safe; winddown continues": Severity.P2_WARNING,
    "fail-safe": Severity.P2_WARNING,
    # Fail-open patterns (P3/P4)
    "fail-open; let WAL scan fail naturally": Severity.P3_INFO,
    "fail-open; pyarrow unavailable": Severity.P4_DEBUG,
    "fail-open: accept on any error": Severity.P3_INFO,
    "fail-open": Severity.P3_INFO,
    # Degraded patterns (P2)
    "degraded; will use :memory:": Severity.P2_WARNING,
    "degraded": Severity.P2_WARNING,
    # Fail-soft patterns (P3/P4)
    "fail-soft on batch operation": Severity.P4_DEBUG,
    "fail-soft overall": Severity.P4_DEBUG,
    "fail-soft": Severity.P3_INFO,
    # Non-critical (P4)
    "non-critical": Severity.P4_DEBUG,
}


def _parse_severity_from_comment(comment: str | None) -> Severity | None:
    """Parse severity hint from exception comment (for backward compat)."""
    if not comment:
        return None
    comment_lower = comment.lower()
    for hint, severity in _SEVERITY_HINTS.items():
        if hint in comment_lower:
            return severity
    return None


# ── Module-level logger cache ──────────────────────────────────────────────

_LOGGER_CACHE: dict[str, logging.Logger] = {}
_BUCKETS: dict[str, TokenBucket] = {}
_BUCKETS_LOCK = __import__("threading").Lock()


@dataclass(slots=True)
class TokenBucket:
    """Minimal token bucket for rate-limiting."""

    tokens: float = 10.0
    max_tokens: float = 10.0
    refill_rate: float = 5.0
    last_refill: float = 0.0

    def __post_init__(self) -> None:
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def try_acquire(self) -> bool:
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def _get_bucket(scope: str, severity: Severity) -> TokenBucket:
    """Get or create a rate-limit bucket for a scope."""
    key = f"{scope}:{severity.name}"
    with _BUCKETS_LOCK:
        if key not in _BUCKETS:
            if severity == Severity.P0_CRITICAL:
                bucket = TokenBucket(max_tokens=999, refill_rate=999)
            elif severity == Severity.P1_ERROR:
                bucket = TokenBucket(max_tokens=20, refill_rate=5)
            elif severity == Severity.P2_WARNING:
                bucket = TokenBucket(max_tokens=10, refill_rate=2)
            elif severity == Severity.P3_INFO:
                bucket = TokenBucket(max_tokens=5, refill_rate=1)
            else:  # P4_DEBUG
                bucket = TokenBucket(max_tokens=2, refill_rate=0.5)
            _BUCKETS[key] = bucket
        return _BUCKETS[key]


def _get_logger(name: str) -> logging.Logger:
    """Lazy logger lookup with caching."""
    cached = _LOGGER_CACHE.get(name)
    if cached is not None:
        return cached
    logger = logging.getLogger(name)
    _LOGGER_CACHE[name] = logger
    return logger


# ── Core Functions ──────────────────────────────────────────────────────────


def silenced(
    *exc_types: type[BaseException],
    name: str,
    severity: Severity = Severity.P3_INFO,
    level: int | None = None,
    logger: logging.Logger | None = None,
    cascade_id: str = "",
) -> Iterator[None]:
    """
    Context manager: suppress + severity-classified log on first hit.

    Enhanced replacement for `except: pass` with severity classification.

    Usage::

        from hledac.universal.utils.silent_except_v2 import silenced, Severity

        with silenced(OSError, asyncio.CancelledError,
                      name="cleanup_lock", severity=Severity.P2_WARNING):
            cleanup_stale_lock()

    Args:
        *exc_types: Exception types to suppress
        name: Identifier for the call site
        severity: Severity level for rate-limiting and logging
        level: Override log level (default: from severity)
        logger: Pre-resolved logger override
        cascade_id: Correlation ID for tracing

    Yields:
        None. Exception is suppressed if it matches exc_types.
    """
    log = logger or _get_logger(name)
    effective_level = level if level is not None else severity.log_level
    bucket = _get_bucket(name, severity)

    try:
        yield
    except exc_types as exc:  # type: ignore[misc]
        # P0 always re-raised
        if severity == Severity.P0_CRITICAL:
            raise

        if bucket.try_acquire():
            log.log(
                effective_level,
                f"{severity.tag} silenced: {name} | {type(exc).__name__}: {exc}",
                exc_info=(severity in (Severity.P0_CRITICAL, Severity.P1_ERROR)),
                extra={
                    "scope": name,
                    "severity": severity.name,
                    "exc_type": type(exc).__name__,
                    "cascade_id": cascade_id,
                },
            )


def silence_with_severity(
    *exc_types: type[BaseException],
    severity: Severity = Severity.P3_INFO,
    name: str | None = None,
    logger: logging.Logger | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | None]]:
    """
    Decorator: wrap a function with severity-classified exception silencing.

    Usage::

        from hledac.universal.utils.silent_except_v2 import silence_with_severity, Severity

        @silence_with_severity(ValueError, Severity.P2_WARNING, name="parse_legacy")
        def parse_legacy(raw: str) -> dict | None:
            return json.loads(raw)["field"]

    Args:
        *exc_types: Exception types to silence
        severity: Severity level for rate-limiting
        name: Identifier (auto-detected from function if None)
        logger: Pre-resolved logger override

    Returns:
        Decorator that wraps the function.
    """

    def decorator(fn: Callable[_P, _R]) -> Callable[_P, _R | None]:
        _name = name or fn.__qualname__
        _logger = logger or _get_logger(_name)
        bucket = _get_bucket(_name, severity)

        @functools.wraps(fn)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R | None:
            try:
                return fn(*args, **kwargs)
            except exc_types as exc:  # type: ignore[misc]
                # P0 always re-raised
                if severity == Severity.P0_CRITICAL:
                    raise

                # Rate-limited log
                if bucket.try_acquire():
                    _logger.log(
                        severity.log_level,
                        f"{severity.tag} silenced: {_name} | {type(exc).__name__}: {exc}",
                    )
                return None

        return wrapper

    return decorator


def severity_swallow(
    exc: BaseException,
    name: str,
    severity: Severity = Severity.P3_INFO,
    logger: logging.Logger | None = None,
    cascade_id: str = "",
) -> None:
    """
    Log and suppress an exception with severity classification.

    Drop-in replacement for `except Exception: pass` with severity-aware logging.

    Usage::

        from hledac.universal.utils.silent_except_v2 import severity_swallow, Severity

        try:
            risky_op()
        except Exception as e:
            severity_swallow(e, "risky_op", Severity.P2_WARNING)

    Args:
        exc: The caught exception
        name: Identifier for the call site
        severity: Severity level for rate-limiting
        logger: Pre-resolved logger override
        cascade_id: Correlation ID for tracing
    """
    log = logger or _get_logger(name)
    bucket = _get_bucket(name, severity)

    # P0 always re-raised
    if severity == Severity.P0_CRITICAL:
        raise exc

    # Rate-limited log
    if bucket.try_acquire():
        log.log(
            severity.log_level,
            f"{severity.tag} swallowed: {name} | {type(exc).__name__}: {exc}",
            exc_info=(severity in (Severity.P0_CRITICAL, Severity.P1_ERROR)),
            extra={
                "scope": name,
                "severity": severity.name,
                "exc_type": type(exc).__name__,
                "cascade_id": cascade_id,
            },
        )


# ── Backward Compatibility ─────────────────────────────────────────────────


def safe_swallow(
    site_name: str,
    logger: logging.Logger | None = None,
    level: int = logging.DEBUG,
    exc: BaseException | None = None,
) -> None:
    """
    Backward-compatible wrapper for silent_except_helper.safe_swallow.

    Automatically infers severity from logger level:
      DEBUG → P4_DEBUG
      INFO → P3_INFO
      WARNING → P2_WARNING
      ERROR → P1_ERROR
      CRITICAL → P0_CRITICAL

    Usage (legacy style):
        from hledac.universal.utils.silent_except_helper import safe_swallow

        try:
            risky_op()
        except Exception as e:
            safe_swallow("risky_op", logger, exc=e)
    """
    # Infer severity from log level
    level_to_severity = {
        logging.DEBUG: Severity.P4_DEBUG,
        logging.INFO: Severity.P3_INFO,
        logging.WARNING: Severity.P2_WARNING,
        logging.ERROR: Severity.P1_ERROR,
        logging.CRITICAL: Severity.P0_CRITICAL,
    }
    severity = level_to_severity.get(level, Severity.P4_DEBUG)

    if exc is not None:
        severity_swallow(exc, site_name, severity, logger)
    else:
        # No exception to log, just use bucket
        bucket = _get_bucket(site_name, severity)
        if bucket.try_acquire():
            log = logger or _get_logger(site_name)
            log.log(level, f"silent-except swallowed: {site_name}")


# ── Structured Event Helpers ───────────────────────────────────────────────


def create_exception_event(
    exc: BaseException,
    scope: str,
    severity: Severity = Severity.P3_INFO,
    cascade_id: str = "",
    **extra: str,
) -> ExceptionEvent:
    """
    Create a structured exception event.

    Usage::

        event = create_exception_event(
            exc=e,
            scope="fetch.public_url",
            severity=Severity.P1_ERROR,
            cascade_id="req-123"
        )

    Args:
        exc: The exception
        scope: Operation identifier
        severity: Severity level
        cascade_id: Correlation ID
        **extra: Additional fields for the event

    Returns:
        ExceptionEvent with all fields populated
    """
    import uuid

    exc_hash = f"{type(exc).__name__}:{str(exc)[:100]}"
    now = time.time()

    return ExceptionEvent(
        event_id=uuid.uuid7().hex[:12],
        cascade_id=cascade_id,
        severity=severity,
        scope=scope,
        category=scope.split(".")[0] if "." in scope else scope,
        exc_type=type(exc).__name__,
        exc_message=str(exc)[:200],
        exc_hash=exc_hash,
        timestamp=now,
        first_seen=now,
        count=1,
        suppressed_count=0,
        re_raised=False,
        outcome="swallowed",
        file=sys._getframe(1).f_code.co_filename if hasattr(sys, "_getframe") else "",
        line=sys._getframe(1).f_lineno if hasattr(sys, "_getframe") else 0,
    )
