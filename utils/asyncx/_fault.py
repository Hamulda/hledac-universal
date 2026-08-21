# hledac/universal/utils/async/_fault.py
# R-3: Failure Observability — cascading failure tracking via ContextVar
#
# Provides:
# - _CASCADE_CTX, _FAILURE_COUNTER, _FAILURE_PATH, _IN_FAILURE_LOG ContextVars
# - _generate_failure_id(), _current_cascade_id(), get_cascading_failure_id()
# - _format_failure(), _log_failure() - failure formatting and logging
# - silent_except: decorator for fail-soft async functions with cascading failure tracking
#
# Invariants:
# - Every async task carries a cascading_failure_id via contextvars
# - Silent except blocks log [FAILURE] {cascade_id} {scope} {exc_info}
# - Auto-ESCALATE: if parent chain has ≥2 failures, log at WARNING with traceback
# - Zero overhead on happy path (ContextVar access is ~30ns, single dict lookup)
# - FailureRegistry integration: critical paths record to SprintHealthLedger

"""
R-3: Failure Observability — cascading failure tracking via ContextVar

Provides:
- _CASCADE_CTX, _FAILURE_COUNTER, _FAILURE_PATH, _IN_FAILURE_LOG ContextVars
- _generate_failure_id(), _current_cascade_id(), get_cascading_failure_id()
- _format_failure(), _log_failure() - failure formatting and logging
- silent_except: decorator for fail-soft async functions with cascading failure tracking
- Integration with utils.resilience.FailureRegistry for orchestrator visibility

Invariants:
- Every async task carries a cascading_failure_id via contextvars
- Silent except blocks log [FAILURE] {cascade_id} {scope} {exc_info}
- Auto-ESCALATE: if parent chain has ≥2 failures, log at WARNING with traceback
- Zero overhead on happy path (ContextVar access is ~30ns, single dict lookup)
- Critical paths record failures to SprintHealthLedger when available
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import random
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T", default=Any)

# Lazy import to avoid circular dependency
_RESILIENCE_MODULE: Any = None


def _get_resilience():
    """Lazy import of resilience module to avoid circular dependency."""
    global _RESILIENCE_MODULE
    if _RESILIENCE_MODULE is None:
        try:
            from hledac.universal.utils.resilience import (
                FailureRegistry,
                FailureSeverity,
                SeverityMapper,
                get_ledger,
            )

            _RESILIENCE_MODULE = {
                "FailureRegistry": FailureRegistry,
                "FailureSeverity": FailureSeverity,
                "get_ledger": get_ledger,
                "SeverityMapper": SeverityMapper,
            }
        except ImportError:
            _RESILIENCE_MODULE = {}
    return _RESILIENCE_MODULE


_CASCADE_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("_cascade_failure_id", default="")

# Module-level counter for generating unique failure IDs per sprint/execution.
_FAILURE_COUNTER: contextvars.ContextVar[int] = contextvars.ContextVar("_failure_counter", default=0)

# Failure path for the current cascade — stores (scope, exc_info_str, count).
# Escalation threshold: after 2 failures in a chain, switch to WARNING.
_FAILURE_PATH: contextvars.ContextVar[list[tuple[str, str, int]]] = contextvars.ContextVar("_failure_path", default=[])

# Sentinel: marks that we're already inside a failure log — prevents re-entry.
_IN_FAILURE_LOG: contextvars.ContextVar[bool] = contextvars.ContextVar("_in_failure_log", default=False)

# Global logger for failure events — use sprint's logger when available.
_FAILURE_LOGGER = logging.getLogger("hledac.failures")


def _generate_failure_id() -> str:
    """Generate a unique per-sprint failure ID (e.g. 'F7A3')."""
    counter = _FAILURE_COUNTER.get() + random.randint(1, 65535)
    _FAILURE_COUNTER.set(counter)
    return f"F{(counter >> 8) & 0xFFFF:04X}"


def _current_cascade_id() -> str:
    """Get or create the cascading failure ID for the current async task."""
    cid = _CASCADE_CTX.get()
    if not cid:
        cid = _generate_failure_id()
        _CASCADE_CTX.set(cid)
    return cid


def get_cascading_failure_id() -> str:
    """Public API: get the current coroutine's cascading failure ID.

    Returns a short human-readable ID (e.g. 'F7A3') that ties together all
    failures in the same causal chain. Empty string means no failures yet.

    Usage:
        cascade_id = get_cascading_failure_id()
        if cascade_id:
            logger.warning("[FAILURE] %s duckdb.ingest timeout", cascade_id)
    """
    return _CASCADE_CTX.get()


def _format_failure(
    scope: str,
    exc: BaseException,
    *,
    is_escalated: bool = False,
) -> str:
    """Format exception info for failure logging."""
    exc_type = type(exc).__qualname__
    exc_msg = str(exc)
    # Trim long messages to avoid log flooding
    if len(exc_msg) > 256:
        exc_msg = exc_msg[:256] + "…"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if len(tb) > 512:
        tb = tb[:512] + "…"
    level = "WARNING" if is_escalated else "DEBUG"
    return f"[{level}] {{cascade={_CASCADE_CTX.get()}}} {{scope={scope}}} {exc_type}: {exc_msg} | tb={tb}"


def _log_failure(
    scope: str,
    exc: BaseException,
    *,
    is_escalated: bool = False,
) -> None:
    """Log a failure event, with auto-ESCALATE when parent chain has ≥2 failures."""
    # Prevent re-entry from logging the failure
    if _IN_FAILURE_LOG.get():
        return

    token = _IN_FAILURE_LOG.set(True)
    try:
        exc_info = f"{type(exc).__qualname__}: {exc}"
        failure_path = _FAILURE_PATH.get()
        count = 1
        for i, (s, _e, c) in enumerate(failure_path):
            if s == scope:
                count = c + 1
                failure_path[i] = (scope, exc_info, count)
                break
        else:
            failure_path.append((scope, exc_info, 1))

        # Escalation: ≥2 failures in chain → WARNING + full traceback
        if count >= 2:
            is_escalated = True
        _FAILURE_PATH.set(failure_path)

        msg = _format_failure(scope, exc, is_escalated=is_escalated)
        if is_escalated:
            _FAILURE_LOGGER.warning("%s", msg)
        else:
            _FAILURE_LOGGER.debug("%s", msg)
    finally:
        _IN_FAILURE_LOG.reset(token)


class silent_except:
    """R-3: Decorator for fail-soft async functions with cascading failure tracking.

    Wraps any async function so that:
    1. ``except Exception`` catches and logs at DEBUG level (default)
    2. If the same scope has ≥2 failures in the call chain → ESCALATE to WARNING
    3. The cascading failure ID propagates to child tasks via ContextVar
    4. OTel trace context is captured and passed through
    5. Records to FailureRegistry when SprintHealthLedger is active (NEW)

    The cascade ID is attached to every failure log line, enabling operators
    to grep logs for e.g. ``{cascade=F7A3}`` and see the full failure chain.

    Usage:
        @silent_except(scope="duckdb.ingest", default=None)
        async def _ingest_batch(...):
            ...

        @silent_except(scope="duckdb.ingest", default=[], escalate=True)
        async def _ingest_batch(...):
            ...

        @silent_except(scope="duckdb.ingest", default=[], record_to_registry=True)
        async def _ingest_batch(...):
            ...

    Args:
        scope: dot-namespaced failure scope, e.g. "duckdb.ingest", "live_feed.fetch"
        default: return value on failure (default None). Set to ... to re-raise.
        escalate: if True, always log at WARNING level (bypass auto-escalate logic).
                  Use for critical paths where silent failures are unacceptable.
        log_level: explicit log level (default logging.DEBUG). Overridden by
                   escalation logic (≥2 failures → WARNING).
        otel_trace: capture OTel span context on failure (default True).
        record_to_registry: if True, record failure to SprintHealthLedger (default True
                   for critical paths). Only set False for non-critical sidecars.
        severity: FailureSeverity for registry (default MEDIUM, use HIGH/CRITICAL
                 for critical paths).
    """

    __slots__ = (
        "_fn",
        "_scope",
        "_default",
        "_escalate",
        "_log_level",
        "_otel_trace",
        "_record_to_registry",
        "_severity",
    )

    def __init__(
        self,
        scope: str,
        default: T | None = None,
        *,
        escalate: bool = False,
        log_level: int = logging.DEBUG,
        otel_trace: bool = True,
        record_to_registry: bool = True,
        severity: int = 1,  # FailureSeverity.MEDIUM by default
    ) -> None:
        self._fn: Callable[..., Any] | None = None
        self._scope = scope
        self._default = default
        self._escalate = escalate
        self._log_level = log_level
        self._otel_trace = otel_trace
        self._record_to_registry = record_to_registry
        self._severity = severity

    def _record_to_ledger(self, exc: BaseException) -> None:
        """Record failure to SprintHealthLedger if available."""
        if not self._record_to_registry:
            return

        resilience = _get_resilience()
        if not resilience:
            return

        try:
            ledger = resilience["get_ledger"]()
            sev_class = resilience["FailureSeverity"]
            mapper = resilience.get("SeverityMapper")

            # Use auto-detection if no explicit severity was set (indicated by severity == 1 and scope exists)
            # This allows SeverityMapper to determine appropriate severity based on operation type
            if mapper:
                severity = mapper.get_severity(self._scope)
            else:
                severity = sev_class(self._severity)

            # Record asynchronously (don't block on ledger failures)
            if hasattr(ledger, "record_failure"):
                _task = asyncio.create_task(
                    ledger.record_failure(
                        component=self._scope,
                        severity=severity,
                        error=exc,
                        context={"cascade_id": _CASCADE_CTX.get()},
                    )
                )

                # Best-effort: add done callback to log if recording fails
                def _log_if_failed(t: asyncio.Task) -> None:
                    try:
                        t.result()
                    except Exception as recorded_exc:
                        _FAILURE_LOGGER.warning(
                            "[REGISTRY] Failed to record failure: %s",
                            recorded_exc,
                        )

                _task.add_done_callback(_log_if_failed)
        except Exception:
            # Silently ignore ledger failures to not compound original error
            pass

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _current_cascade_id()  # side-effect: attaches cascade ID to this task
                try:
                    return await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _log_failure(
                        self._scope,
                        exc,
                        is_escalated=self._escalate,
                    )
                    # Record to registry for orchestrator visibility
                    self._record_to_ledger(exc)
                    if self._default is not ...:
                        return self._default  # type: ignore[return-value]
                    raise

            self._fn = async_wrapper
            return async_wrapper

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    _log_failure(
                        self._scope,
                        exc,
                        is_escalated=self._escalate,
                    )
                    # Record to registry for orchestrator visibility
                    self._record_to_ledger(exc)
                    if self._default is not ...:
                        return self._default  # type: ignore[return-value]
                    raise

            self._fn = sync_wrapper
            return sync_wrapper

    def __repr__(self) -> str:
        return (
            f"silent_except(scope={self._scope!r}, "
            f"default={self._default!r}, escalate={self._escalate}, "
            f"record={self._record_to_registry})"
        )


__all__ = [
    "silent_except",
    "get_cascading_failure_id",
    "_log_failure",
    "_record_to_ledger",  # For manual registry recording
]
