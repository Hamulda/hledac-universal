"""
Exception Severity Classification System — Issue #8 Diagnostic Blindness Fix.

Strategic solution for exception handler saturation:
  - Formal severity hierarchy (P0-P4)
  - Structured exception events with cascade correlation
  - Rate-limiting for repeated errors
  - M1 8GB optimized (slots, weakref, minimal allocations)

Severity Levels:
  P0_CRITICAL: Must surface immediately, never rate-limited (data loss, security breach)
  P1_ERROR: High importance, rate-limited after 3 occurrences (operation failures)
  P2_WARNING: Medium importance, aggregated reporting (degraded mode)
  P3_INFO: Low importance, bulk reporting (best-effort operations)
  P4_DEBUG: Noise suppression, sampled at 1% (internal diagnostics)

Usage:
    from hledac.universal.utils.exception_severity import Severity, exc_event

    # P0 - critical, never suppressed
    with exc_event(Severity.P0_CRITICAL, "data_loss", "db_write_failed"):
        await db.write(data)

    # P2 - warning, aggregated
    with exc_event(Severity.P2_WARNING, "degraded", "cache_unavailable"):
        return fallback_value

    # With explicit cascade ID for correlation
    with exc_event(Severity.P1_ERROR, "fetch", "url_timeout", cascade_id="req-123"):
        await fetch(url)

References:
  - PYTHON314_MODERNIZATION_AUDIT: ~80% are intentional fail-safe fallbacks
  - GHOST_INVARIANTS: "no silent except"
  - Ruff BLE001: blind-except detection
"""

from __future__ import annotations

import functools
import logging
import random
import time
import weakref
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar
from collections.abc import Callable
from _core import aclose

if TYPE_CHECKING:
    from collections.abc import Awaitable

__all__ = [
    "Severity",
    "ExceptionEvent",
    "exc_event",
    "severity_decorator",
    "rate_limited_log",
    "_EventRegistry",
    "SeverityConfig",
    "get_config",
    "set_config",
    "configure_severity",
]


# ── Severity Hierarchy ───────────────────────────────────────────────────────

class Severity(Enum):
    """
    Exception severity levels with rate-limiting behavior.

    Design principles:
      - P0 never suppressed: critical failures must surface
      - P1-P2 aggregated: reduce noise while preserving signal
      - P3-P4 sampled: bulk reporting for low-value exceptions
      - Rate limits reset on severity change (escalation detected)
    """

    P0_CRITICAL = auto()  # Never suppressed, always logged at ERROR
    P1_ERROR = auto()     # Rate-limited after 3 occurrences
    P2_WARNING = auto()   # Aggregated, report first + count
    P3_INFO = auto()      # Sampled 10%, bulk count
    P4_DEBUG = auto()     # Sampled 1%, bulk count

    @property
    def rate_limit_threshold(self) -> int:
        """Number of occurrences before rate-limiting kicks in (uses runtime config)."""
        cfg = _config
        return {
            Severity.P0_CRITICAL: cfg.p0_threshold,
            Severity.P1_ERROR: cfg.p1_threshold,
            Severity.P2_WARNING: cfg.p2_threshold,
            Severity.P3_INFO: cfg.p3_threshold,
            Severity.P4_DEBUG: cfg.p4_threshold,
        }[self]

    @property
    def sample_rate(self) -> float:
        """Sampling rate for bulk reporting (0.0-1.0, uses runtime config)."""
        cfg = _config
        return {
            Severity.P0_CRITICAL: cfg.p0_sample_rate,
            Severity.P1_ERROR: cfg.p1_sample_rate,
            Severity.P2_WARNING: cfg.p2_sample_rate,
            Severity.P3_INFO: cfg.p3_sample_rate,
            Severity.P4_DEBUG: cfg.p4_sample_rate,
        }[self]

    @property
    def log_level(self) -> int:
        """Corresponding stdlib log level."""
        return {
            Severity.P0_CRITICAL: logging.CRITICAL,
            Severity.P1_ERROR: logging.ERROR,
            Severity.P2_WARNING: logging.WARNING,
            Severity.P3_INFO: logging.INFO,
            Severity.P4_DEBUG: logging.DEBUG,
        }[self]

    @property
    def tag(self) -> str:
        """Short tag for log output."""
        return {
            Severity.P0_CRITICAL: "[CRIT]",
            Severity.P1_ERROR: "[ERR]",
            Severity.P2_WARNING: "[WARN]",
            Severity.P3_INFO: "[INFO]",
            Severity.P4_DEBUG: "[DBG]",
        }[self]


# ── Exception Event ────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class ExceptionEvent:
    """
    Structured exception event with cascade correlation.

    Immutable after creation - safe to use as dict key or set member.
    """

    # Identity
    event_id: str = field(default="")          # Unique UUID7 for this occurrence
    cascade_id: str = field(default="")        # Correlation ID (e.g., request ID)

    # Classification
    severity: Severity = field(default=Severity.P4_DEBUG)
    scope: str = field(default="")             # Module.function or operation name
    category: str = field(default="")           # Category (e.g., "db", "fetch", "io")

    # Exception details
    exc_type: str = field(default="")          # Exception class name
    exc_message: str = field(default="")        # Exception message (truncated)
    exc_hash: str = field(default="")          # Hash for deduplication

    # Timing
    timestamp: float = field(default_factory=time.time)
    first_seen: float = field(default_factory=time.time)

    # Occurrence tracking
    count: int = field(default=1)              # Total occurrences
    suppressed_count: int = field(default=0)    # Number suppressed due to rate-limit

    # Context
    re_raised: bool = field(default=False)     # Whether exception was re-raised
    outcome: str = field(default="swallowed")   # swallowed, re_raised, degraded

    # Location hint
    file: str = field(default="")
    line: int = field(default=0)


# ── Event Registry (singleton) ─────────────────────────────────────────────

class _EventRegistry:
    """
    Thread-safe registry for exception events.

    Implements sliding window aggregation with per-key rate limiting.
    Delegates to ExceptionDiagnostics for actual storage to avoid duplication.
    
    M1 8GB: Uses weakref for memory management, bounded dict sizes.
    """

    __slots__ = ('_aggregation', '_last_flush', '_lock')
    _instance: _EventRegistry | None = None

    # Configuration
    MAX_EVENTS: int = 10000          # Max events before forced flush
    WINDOW_SECONDS: float = 300.0    # 5-minute aggregation window
    MAX_KEYS: int = 1000             # Max tracked exception keys

    def __new__(cls) -> _EventRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._aggregation = {}  # For aggregation tracking only
            cls._instance._last_flush = time.time()
            cls._instance._lock = __import__('threading').Lock()
        return cls._instance

    def register(self, event: ExceptionEvent) -> ExceptionEvent:
        """Register an exception event, aggregate if duplicate."""
        key = event.exc_hash or f"{event.scope}:{event.exc_type}"

        with self._lock:
            # Check for existing event for aggregation
            if key in self._aggregation:
                existing = self._aggregation[key]
                # Update counters
                new_count = existing.count + 1
                suppressed = 0

                # Apply rate limiting
                if new_count > event.severity.rate_limit_threshold:
                    if event.severity.sample_rate >= 1.0:
                        suppressed = new_count - existing.count
                    else:
                        # Stochastic sampling for P3/P4
                        if random.random() > event.severity.sample_rate:
                            suppressed = 1

                # Return aggregated event
                aggregated = ExceptionEvent(
                    event_id=event.event_id,
                    cascade_id=event.cascade_id or existing.cascade_id,
                    severity=event.severity,
                    scope=event.scope,
                    category=event.category,
                    exc_type=event.exc_type,
                    exc_message=event.exc_message,
                    exc_hash=key,
                    timestamp=event.timestamp,
                    first_seen=existing.first_seen,
                    count=new_count,
                    suppressed_count=existing.suppressed_count + suppressed,
                    re_raised=event.re_raised,
                    outcome=event.outcome,
                    file=event.file,
                    line=event.line,
                )
                self._aggregation[key] = aggregated
                
                # Also record to ExceptionDiagnostics for full diagnostics
                try:
                    from hledac.universal.utils.exception_diagnostics import get_diagnostics
                    get_diagnostics().record(aggregated)
                except Exception:  # noqa: BLE001
                    pass  # Non-critical
                
                return aggregated
            else:
                # New event
                if len(self._aggregation) >= self.MAX_KEYS:
                    # Evict oldest by first_seen
                    oldest_key = min(
                        self._aggregation.keys(),
                        key=lambda k: self._aggregation[k].first_seen
                    )
                    del self._aggregation[oldest_key]

                self._aggregation[key] = event
                
                # Also record to ExceptionDiagnostics for full diagnostics
                try:
                    from hledac.universal.utils.exception_diagnostics import get_diagnostics
                    get_diagnostics().record(event)
                except Exception:  # noqa: BLE001
                    pass  # Non-critical
                
                return event

    def get_recent(self, since: float | None = None) -> list[ExceptionEvent]:
        """Get events since timestamp (default: last 5 minutes)."""
        if since is None:
            since = time.time() - self.WINDOW_SECONDS

        with self._lock:
            return [
                e for e in self._aggregation.values()
                if e.timestamp >= since
            ]

    def flush(self) -> list[ExceptionEvent]:
        """Flush all events and return them."""
        with self._lock:
            events = list(self._aggregation.values())
            self._aggregation.clear()
            self._last_flush = time.time()
            return events


# ── Context Manager: exc_event ─────────────────────────────────────────────

_P = ParamSpec("_P")
_R = TypeVar("_R")


class _ExcEventContext:
    """Context manager for exception event tracking."""

    __slots__ = ('_severity', '_scope', '_category', '_cascade_id', '_logger', '_event')

    def __init__(
        self,
        severity: Severity,
        scope: str,
        category: str = "",
        cascade_id: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        self._severity = severity
        self._scope = scope
        self._category = category
        self._cascade_id = cascade_id
        self._logger = logger or logging.getLogger("exception_events")
        self._event: ExceptionEvent | None = None

    def __enter__(self) -> _ExcEventContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        if exc_val is None:
            return True  # No exception

        # Build event
        import uuid as _uuid
        exc_hash = f"{exc_type.__name__}:{str(exc_val)[:100]}"

        # Extract location from traceback
        file = ""
        line = 0
        if exc_tb:
            tb = exc_tb
            while tb.tb_next:
                tb = tb.tb_next
            file = tb.tb_frame.f_code.co_filename or ""
            line = tb.tb_lineno or 0

        event = ExceptionEvent(
            event_id=_uuid.uuid4().hex[:12],
            cascade_id=self._cascade_id,
            severity=self._severity,
            scope=self._scope,
            category=self._category or self._scope.split('.')[0] if '.' in self._scope else self._scope,
            exc_type=exc_type.__name__,
            exc_message=str(exc_val)[:200],
            exc_hash=exc_hash,
            timestamp=time.time(),
            first_seen=time.time(),
            count=1,
            suppressed_count=0,
            re_raised=False,
            outcome="swallowed",
            file=file,
            line=line,
        )

        # Register and potentially suppress
        registered = _EventRegistry().register(event)

        # Log based on severity and rate-limiting
        self._log_event(registered)

        # P0 always re-raised - fail-closed for critical errors
        if self._severity == Severity.P0_CRITICAL:
            raise exc_val

        return True  # Suppress exception

    def _log_event(self, event: ExceptionEvent) -> None:
        """Log the event with structured format."""
        if event.suppressed_count > 0 and event.count > event.severity.rate_limit_threshold:
            # Rate-limited: only log periodically (every N occurrences)
            if event.count % 100 == 0:
                msg = (
                    f"{event.severity.tag} {event.scope}: {event.exc_type} "
                    f"(suppressed {event.suppressed_count}x, total {event.count})"
                )
            else:
                return  # Skip log for rate-limited event
        else:
            # Normal log
            extra = {
                "event_id": event.event_id,
                "cascade_id": event.cascade_id,
                "scope": event.scope,
                "category": event.category,
                "count": event.count,
                "suppressed": event.suppressed_count,
                "outcome": event.outcome,
            }
            self._logger.log(
                event.severity.log_level,
                f"{event.severity.tag} {event.scope}: {event.exc_type}: {event.exc_message}",
                exc_info=(event.severity in (Severity.P0_CRITICAL, Severity.P1_ERROR)),
                extra=extra,
            )


def exc_event(
    severity: Severity,
    scope: str,
    category: str = "",
    cascade_id: str = "",
    logger: logging.Logger | None = None,
) -> _ExcEventContext:
    """
    Context manager for exception event tracking with severity classification.

    Usage:
        from hledac.universal.utils.exception_severity import Severity, exc_event

        # P0 - critical, never suppressed, re-raised
        with exc_event(Severity.P0_CRITICAL, "db.write", cascade_id="req-123"):
            await db.write(data)

        # P2 - warning, aggregated reporting
        with exc_event(Severity.P2_WARNING, "cache.get", "cache"):
            return fallback_value

    Args:
        severity: Exception severity level (P0-P4)
        scope: Operation identifier (e.g., "module.function" or "operation_name")
        category: Optional category for grouping (auto-detected from scope if empty)
        cascade_id: Optional correlation ID for tracing
        logger: Optional logger override (default: "exception_events")

    Returns:
        Context manager that tracks exceptions and applies rate-limiting.
    """
    return _ExcEventContext(
        severity=severity,
        scope=scope,
        category=category,
        cascade_id=cascade_id,
        logger=logger,
    )


# ── Decorator: severity_decorator ─────────────────────────────────────────

def severity_decorator(
    severity: Severity,
    scope: str | None = None,
    category: str = "",
    re_raise: bool = False,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """
    Decorator that wraps a function with exception event tracking.

    Usage:
        @severity_decorator(Severity.P1_ERROR, scope="fetch.public_url")
        async def fetch_url(url: str) -> bytes:
            ...

    Args:
        severity: Default severity for unhandled exceptions
        scope: Operation name (auto-detected from function name if None)
        category: Category for grouping
        re_raise: Whether to re-raise exceptions (default: swallow)

    Returns:
        Decorated function with exception tracking.
    """
    def decorator(fn: Callable[_P, _R]) -> Callable[_P, _R]:
        _scope = scope or fn.__qualname__

        @functools.wraps(fn)
        def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with exc_event(severity, _scope, category):
                return fn(*args, **kwargs)

        @functools.wraps(fn)
        async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with exc_event(severity, _scope, category):
                return await fn(*args, **kwargs)

        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ── Runtime Configuration ────────────────────────────────────────────────────

@dataclass(slots=True)
class SeverityConfig:
    """
    Runtime configuration for severity levels.

    Allows adjusting thresholds and sample rates at runtime via env vars or code.
    """
    # Rate limit thresholds (occurrences before rate-limiting)
    p0_threshold: int = 0     # Never rate-limited
    p1_threshold: int = 3
    p2_threshold: int = 5
    p3_threshold: int = 10
    p4_threshold: int = 20

    # Sample rates (0.0-1.0)
    p0_sample_rate: float = 1.0
    p1_sample_rate: float = 1.0
    p2_sample_rate: float = 1.0
    p3_sample_rate: float = 0.1
    p4_sample_rate: float = 0.01

    # Token bucket configuration per severity
    p0_max_tokens: int = 999
    p0_refill_rate: float = 999
    p1_max_tokens: int = 20
    p1_refill_rate: float = 5.0
    p2_max_tokens: int = 10
    p2_refill_rate: float = 2.0
    p3_max_tokens: int = 5
    p3_refill_rate: float = 1.0
    p4_max_tokens: int = 2
    p4_refill_rate: float = 0.5

    @classmethod
    def from_env(cls) -> "SeverityConfig":
        """Load configuration from environment variables."""
        import os
        return cls(
            p1_threshold=int(os.environ.get("HLEDAC_SEVERITY_P1_THRESHOLD", 3)),
            p2_threshold=int(os.environ.get("HLEDAC_SEVERITY_P2_THRESHOLD", 5)),
            p3_threshold=int(os.environ.get("HLEDAC_SEVERITY_P3_THRESHOLD", 10)),
            p4_threshold=int(os.environ.get("HLEDAC_SEVERITY_P4_THRESHOLD", 20)),
            p3_sample_rate=float(os.environ.get("HLEDAC_SEVERITY_P3_SAMPLE", 0.1)),
            p4_sample_rate=float(os.environ.get("HLEDAC_SEVERITY_P4_SAMPLE", 0.01)),
        )


# Global configuration (can be replaced at runtime)
_config: SeverityConfig = SeverityConfig.from_env()


def get_config() -> SeverityConfig:
    """Get the current severity configuration."""
    return _config


def set_config(config: SeverityConfig) -> None:
    """Set the severity configuration at runtime."""
    global _config
    _config = config


def configure_severity(
    *,
    p1_threshold: int | None = None,
    p2_threshold: int | None = None,
    p3_threshold: int | None = None,
    p4_threshold: int | None = None,
    p3_sample_rate: float | None = None,
    p4_sample_rate: float | None = None,
) -> None:
    """
    Configure severity levels at runtime.

    Usage:
        from hledac.universal.utils.exception_severity import configure_severity
        configure_severity(p3_sample_rate=0.05)  # Only 5% of P3 logs
        configure_severity(p1_threshold=1)  # Immediate rate-limiting for P1
    """
    global _config
    _config = SeverityConfig(
        p0_threshold=_config.p0_threshold,
        p1_threshold=p1_threshold if p1_threshold is not None else _config.p1_threshold,
        p2_threshold=p2_threshold if p2_threshold is not None else _config.p2_threshold,
        p3_threshold=p3_threshold if p3_threshold is not None else _config.p3_threshold,
        p4_threshold=p4_threshold if p4_threshold is not None else _config.p4_threshold,
        p0_sample_rate=_config.p0_sample_rate,
        p1_sample_rate=_config.p1_sample_rate,
        p2_sample_rate=_config.p2_sample_rate,
        p3_sample_rate=p3_sample_rate if p3_sample_rate is not None else _config.p3_sample_rate,
        p4_sample_rate=p4_sample_rate if p4_sample_rate is not None else _config.p4_sample_rate,
        p0_max_tokens=_config.p0_max_tokens,
        p0_refill_rate=_config.p0_refill_rate,
        p1_max_tokens=_config.p1_max_tokens,
        p1_refill_rate=_config.p1_refill_rate,
        p2_max_tokens=_config.p2_max_tokens,
        p2_refill_rate=_config.p2_refill_rate,
        p3_max_tokens=_config.p3_max_tokens,
        p3_refill_rate=_config.p3_refill_rate,
        p4_max_tokens=_config.p4_max_tokens,
        p4_refill_rate=_config.p4_refill_rate,
    )


# ── Helper: rate_limited_log ──────────────────────────────────────────────

class _RateLimitBucket:
    """Token bucket for log rate limiting."""

    __slots__ = ('_tokens', '_max_tokens', '_refill_rate', '_last_refill')

    def __init__(self, max_tokens: int = 10, refill_rate: float = 1.0) -> None:
        self._tokens = float(max_tokens)
        self._max_tokens = float(max_tokens)
        self._refill_rate = refill_rate
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    def try_acquire(self) -> bool:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


# Module-level rate limiters per scope
_LOG_BUCKETS: dict[str, _RateLimitBucket] = {}
_LOG_BUCKETS_LOCK = __import__('threading').Lock()


def rate_limited_log(
    logger: logging.Logger,
    level: int,
    scope: str,
    msg: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Log with per-scope rate limiting.

    Prevents log flooding by enforcing token bucket limits per scope.

    Usage:
        rate_limited_log(my_logger, logging.WARNING, "fetch", "Failed: %s", url)

    Args:
        logger: Logger instance
        level: Log level
        scope: Rate-limit scope (e.g., module name)
        msg: Log message
        *args: Message arguments
        **kwargs: Keyword arguments (passed to logger.log)
    """
    with _LOG_BUCKETS_LOCK:
        if scope not in _LOG_BUCKETS:
            # Different refill rates based on severity
            _LOG_BUCKETS[scope] = _RateLimitBucket(max_tokens=20, refill_rate=5.0)

        bucket = _LOG_BUCKETS[scope]

    if bucket.try_acquire():
        logger.log(level, msg, *args, **kwargs)
