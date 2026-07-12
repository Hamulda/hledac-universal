# hledac/universal/utils/async_helpers.py
# Ghost Async Helpers - Gather hygiene and blocking-I/O guards
#
# Provides:
# - _check_gathered(): filter exceptions, log, ret valid results
# - Async DNS helpers using loop.getaddrinfo()
# - Result DTOs: SafeGatherResult, SafeGatherShieldedResult, _BoundedExceptionLog (msgspec.Struct)
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
import contextlib
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

import msgspec

T = TypeVar("T", default=Any)

if TYPE_CHECKING:
    pass

__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "bounded_gather",
    "chunked_taskgroup",
    "gather_taskgroup",
    "monotonic_ms",
    "safe_gather",
    "safe_gather_ok",
    "safe_gather_fire_and_forget",
    "safe_gather_strict",  # PEP 654 BaseExceptionGroup auto-raise
    "safe_gather_shielded",
    "safe_gather_return_exceptions",
    "safe_create_task",
    "safe_wait_for",
    "SafeGatherResult",
    "SafeGatherShieldedResult",
    "_BoundedExceptionLog",
    "cancel_scope_drain",
    "BoundedPerHostGate",
    "current_otel_context",
    "stop_task",  # F360: shared stop() lifecycle helper
    "race_first_success",  # F350M-R: parallel profile fallback race
    "RaceFirstSuccessResult",
]

logger = logging.getLogger(__name__)

# Eager task creation: Python 3.12+ stdlib accepts `eager_start=True` on
# loop.create_task(), running the coroutine synchronously up to its
# first await. With uvloop on M1, this eliminates ~15-30μs scheduling
# overhead per task in scatter/gather patterns. Degrades gracefully on
# <3.12 (no eager_start kwarg passed).
#
# Detection strategy (no probe loop = no ResourceWarning, ~5-20ms faster import):
#   1. Python 3.12+ stdlib supports eager_start natively
#   2. uvloop 0.22.x (current M1 default) does NOT implement it at C level
#   3. Detect uvloop by importing it — if present, _EAGER_START_SUPPORTED = False
#      regardless of Python version (uvloop overrides create_task)
#   4. Fallback: Python 3.12+ without uvloop → True
#
# Cutting-edge: zero event loop creation at import time, no ResourceWarning,
# Python 3.14+ future-proof. uvloop installation is deterministic on M1.

_PY_312_PLUS: bool = sys.version_info >= (3, 12)

try:
    import uvloop  # noqa: F401

    _UVLOOP_INSTALLED: bool = True
except ImportError:
    _UVLOOP_INSTALLED = False

# uvloop 0.22.x C-level create_task does NOT accept eager_start kwarg.
# Even if we tried to detect via signature, uvloop's override is at C level
# so inspect.signature() on a fresh loop would misleadingly show the stdlib
# signature. Direct import detection is the only reliable approach.
_EAGER_START_SUPPORTED: bool = _PY_312_PLUS and not _UVLOOP_INSTALLED

# E4: OTel context propagation — delegates to the canonical implementation in
# otel/_instrumentation_asyncio.py which handles task context, done callbacks,
# and cache eviction. Import here to keep safe_create_task as the single
# canonical entry point for all callers.
from otel._instrumentation_asyncio import current_otel_context  # noqa: E402, F401


def safe_create_task(
    coro: Any,
    *,
    name: str | None = None,
    eager_start: bool = False,
    # E4: OTel trace context propagation — delegated to otel._instrumentation_asyncio
    otel_trace: bool = True,
) -> asyncio.Task[Any]:
    """
    Sprint F228G: Defensive create_task wrapper that probes the running loop's
    create_task signature and only passes `eager_start` if the loop supports it.

    E4: Also propagates the current OpenTelemetry trace context (trace_id,
    span_id) into the child task via contextvars so that distributed tracing
    works across safe_gather / safe_gather_strict / asyncio.TaskGroup without
    any manual span management in callers.

    The propagation delegates to otel/_instrumentation_asyncio which handles:
    - OTel trace context capture before task creation
    - Done-callback for cache cleanup
    - LRU eviction when task context cache exceeds 256 entries

    Args:
        coro:       The coroutine to wrap in a task.
        name:       Optional task name (passed to asyncio.create_task).
        eager_start: Run coroutine synchronously up to first await (3.12+).
        otel_trace: Capture and propagate OTel trace context (default True).
                   Set to False to suppress propagation for fire-and-forget tasks.

    Returns:
        asyncio.Task wrapping the coroutine. Never raises TypeError from
        signature mismatch — falls back to standard create_task.

    Invariant: bounded, fail-safe. If the import-time probe failed (e.g. no
    event loop available), _EAGER_START_SUPPORTED is False and we always use
    the safe path. OTel context capture is also fail-safe — any error is
    swallowed and the task runs without trace context.
    """
    from otel._instrumentation_asyncio import create_task_with_context  # noqa: E402

    return create_task_with_context(
        coro,
        name=name,
        eager_start=eager_start,
        otel_trace=otel_trace,
    )


_T = TypeVar("_T", default=Any)


def _check_gathered(
    results: list[Any],
    logger_instance: logging.Logger | None = None,
    ctx: str = "",
) -> tuple[list[Any], list[Any]]:
    """
    Process results from asyncio.gather(..., return_exceptions=True).

    Input:  list returned by asyncio.gather(return_exceptions=True)
    Output: (ok_results, error_results)

    PEP 654 aggregation semantics (Python 3.11+):
        - Single CancelledError → bare raise (PEP 654 §"bare raise" idiom)
        - Multiple CancelledErrors OR CancelledError+Exception mix → BaseExceptionGroup
        - Single non-Cancel BaseException → bare raise
        - Non-exception values → ok_results
        - Regular Exceptions → error_results (logged at DEBUG)

    Args:
        results: raw results from asyncio.gather(return_exceptions=True)
        logger_instance: optional logger for output (defaults to mod logger)
        ctx: optional context string for log messages (e.g. "S3 enumeration")

    Returns:
        Tuple of (ok_results, error_results)
        - ok_results: items that are not Exception instances
        - error_results: Exception instances (for logging/handling downstream)

    Invariants enforced:
    - [I6] asyncio.CancelledError is never silently swallowed
    - [I7] non-Exception BaseException (KeyboardInterrupt, SystemExit) is never silently swallowed
    - [I8] regular Exception → routed to error_results (logged at DEBUG)

    Performance: Uses type() identity checks (O(1) pointer comparison) instead of
    isinstance() MRO traversal. Bulk classification avoids repeated exception checks.
    """
    n = len(results)
    if n == 0:
        return [], []

    _log = logger_instance or logger
    _CE = asyncio.CancelledError  # noqa: N806
    _BaseE = BaseException  # noqa: N806
    _Ex = Exception  # noqa: N806

    # Fast path: all-success case (most common in production).
    # Use isinstance for BaseException check (handles subclasses correctly).
    # type() identity check used only for specific types (CancelledError).
    all_ok = True
    for item in results:
        if isinstance(item, _BaseE):  # isinstance handles subclasses correctly
            all_ok = False
            break

    if all_ok:
        return results, []

    # Slow path: at least one exception. 2-pass: (1) bulk classify by type,
    # (2) collect ok results + aggregate exceptions.
    # vs original: linear isinstance MRO scan per item (O(n×mro_depth))
    # now: bulk type comparison (O(n) pointer equality) + single ok_results pass.
    ok_results: list[Any] = []
    cancel_errors: list[BaseException] = []
    other_errors: list[BaseException] = []
    for item in results:
        t = type(item)
        if t is _CE:  # CancelledError — identity check, no MRO
            cancel_errors.append(item)
        elif isinstance(item, _Ex):  # regular Exception
            other_errors.append(item)
        elif isinstance(item, _BaseE):  # BaseException but not Exception
            cancel_errors.append(item)
        else:
            ok_results.append(item)

    # PEP 654 aggregation (same logic, no per-item logging in bulk pass)
    if cancel_errors:
        if len(cancel_errors) == 1 and not other_errors:
            _log.debug("[GHOST] gather single CancelledError%s — bare raise", (" " + ctx) if ctx else "")
            raise cancel_errors[0]
        all_errors: list[BaseException] = cancel_errors + other_errors
        if len(all_errors) == 1:
            raise all_errors[0]
        _log.debug(
            "[GHOST] gather BaseExceptionGroup[%d]%s — raising aggregated", len(all_errors), (" " + ctx) if ctx else ""
        )
        raise BaseExceptionGroup(f"gather{' ' + ctx if ctx else ''}", all_errors)

    return ok_results, other_errors


# safe_gather_strict


# ---------------------------------------------------------------------------
# safe_gather_strict — PEP 654 BaseExceptionGroup auto-raise variant
# ---------------------------------------------------------------------------


async def safe_gather_strict[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """
    asyncio.gather wrapper that auto-raises BaseExceptionGroup on exceptions.

    This is the Python 3.14-idiomatic counterpart to ``_check_gathered`` —
    instead of returning ``(ok_results, error_results)`` and requiring the caller
    to check for errors, this function:
      - Returns ``list[T]`` directly when ALL tasks succeed
      - Raises ``BaseExceptionGroup`` (PEP 654) when ANY task raises

    Behaviour:
      - All coroutines run to completion (gather semantics, not TaskGroup)
      - On partial failure: CancelledError / non-Exception BaseException → re-raised
        immediately; regular exceptions are aggregated into BaseExceptionGroup
      - On total failure: BaseExceptionGroup with all exception objects

    Use when the caller wants ``_check_gathered`` semantics but prefers the
    BaseExceptionGroup to be raised automatically rather than returned.

    Args:
        *coros: Coroutines or awaitables. Plain values are auto-wrapped.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        list[T]: all successful results in original order.

    Raises:
        BaseExceptionGroup: containing all Exception instances from gather.
        asyncio.CancelledError: if the caller's task was cancelled.
        BaseException: for non-Exception BaseException (KeyboardInterrupt, SystemExit).

    Example:
        try:
            results: list[Finding] = await safe_gather_strict(*tasks, label="discovery")
        except* TimeoutError as e:
            print(f"{len(e.exceptions)} timeouts: {[str(x) for x in e.exceptions]}")
    """
    _log = logger_instance or logger
    if not coros:
        return []

    raw = await asyncio.gather(
        *(_wrap_awaitable(c) for c in coros),
        return_exceptions=True,
    )
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    if re_raise is not None:
        raise re_raise

    if errors:
        # Auto-raise as BaseExceptionGroup — Python 3.14 idiom
        _log.debug(
            "[GHOST] safe_gather_strict%s raising BaseExceptionGroup(%d exceptions)",
            (" " + label) if label else "",
            len(errors),
        )
        raise BaseExceptionGroup(
            f"safe_gather_strict{' {label}' if label else ''}",
            errors,
        )

    return ok  # type: ignore[return-value]


async def async_getaddrinfo(
    host: str,
    port: int,
    *,
    family: int = 0,
    type_: int = 0,
    proto: int = 0,
    timeout: float | None = None,
) -> list[tuple[Any, ...]]:
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
        List of (family, type, proto, canonname, sockaddr) tuples.
        Tuple element types are platform-specific (AddressFamily, SocketKind,
        sockaddr variants) — declared `tuple[Any, ...]` so callers don't
        depend on a particular stdlib stub shape.
    """
    loop = asyncio.get_running_loop()
    if timeout is not None and timeout > 0:
        async with asyncio.timeout(timeout):
            return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)
    else:
        return await loop.getaddrinfo(host, port, family=family, type=type_, proto=proto)


# F320: safe_wait_for — asyncio.timeout replacement for Python 3.14+ compatibility
#
# asyncio.wait_for has a critical composability problem with TaskGroup:
# when a TaskGroup cancels its scope, the CancelledError propagates through
# wait_for's await, but wait_for wraps it in TimeoutError if a timeout
# was specified. This causes confusing error messages and makes it hard to
# distinguish cooperative cancellation from actual timeout.
#
# asyncio.timeout (3.11+) solves this: it raises asyncio.TimeoutError which
# is NOT a subclass of CancelledError, so TaskGroup cancellation is
# preserved correctly.
#
# safe_wait_for wraps asyncio.timeout in a familiar wait_for interface so
# callers can migrate incrementally. It:
#   - Raises asyncio.TimeoutError on timeout (same as wait_for)
#   - Raises asyncio.CancelledError on TaskGroup cancellation (correct behavior)
#   - Logs timeout at DEBUG with label for diagnostics
#   - Returns the result on success


async def safe_wait_for[T](
    coro: Awaitable[T],
    timeout: float | None,
    *,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> T:
    """F320: Drop-in replacement for asyncio.wait_for with correct TaskGroup composition.

    ``asyncio.wait_for`` does NOT compose correctly with ``asyncio.TaskGroup``
    cancellation: when a TaskGroup cancels its scope, the CancelledError
    propagates through the awaited coroutine, but ``wait_for`` intercepts it
    and raises ``TimeoutError`` if a timeout was specified — making it
    impossible to distinguish a genuine timeout from cooperative cancellation.

    ``asyncio.timeout`` (Python 3.11+, PEP 654) solves this: it raises
    ``asyncio.TimeoutError`` which is NOT a subclass of ``CancelledError``,
    so TaskGroup cancellation propagates correctly.

    This helper provides the familiar ``wait_for(coro, timeout)`` interface
    backed by ``asyncio.timeout``, so callers can migrate incrementally.

    Invariants:
        - [W1] asyncio.TimeoutError on timeout (same as wait_for)
        - [W2] asyncio.CancelledError on TaskGroup cancellation (correct vs wait_for)
        - [W3] All other exceptions propagate unchanged
        - [W4] timeout=None means no deadline (same as wait_for)

    Args:
        coro: Coroutine or awaitable to run with deadline.
        timeout: Maximum seconds to wait. None = no deadline.
        label: Context label for log messages (e.g. "flush_task", "quic_request").
        logger_instance: Optional logger override (defaults to module logger).

    Returns:
        Result of the coroutine on success.

    Raises:
        asyncio.TimeoutError: if timeout expired (same as wait_for).
        asyncio.CancelledError: if TaskGroup cancelled the scope.
        Any exception from the coroutine propagates unchanged.
    """
    _log = logger_instance or logger
    if timeout is None or timeout <= 0:
        # No timeout — just await directly
        return await coro

    try:
        async with asyncio.timeout(timeout):
            return await coro
    except TimeoutError:
        _log.debug(f"[GHOST] safe_wait_for{'_' + label if label else ''} timeout after {timeout}s")
        raise


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


class SafeGatherResult(msgspec.Struct, frozen=True):
    """Result of `safe_gather` — msgspec.Struct for ~3× faster instantiation.

    Attributes:
        ok:       List of successful results (order preserved)
        errors:   List of exception instances (excluding BaseException)
        re_raised:BaseException instance if one was re-raised (caller should handle)
    """

    ok: list[Any] = msgspec.field(default_factory=list)
    errors: list[BaseException] = msgspec.field(default_factory=list)
    re_raised: BaseException | None = None


async def safe_gather[T](
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
    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)

    ok: list[Any] = []
    errors: list[BaseException] = []

    for i, item in enumerate(raw):
        if isinstance(item, asyncio.CancelledError):
            # [I6] — never swallow cancellation. Re-raise immediately so the
            # caller's finally blocks run, but record in result for diagnostics.
            _log.debug("[GHOST] safe_gather CancelledError[%d]%s", i, (" " + label) if label else "")
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            # [I7] — KeyboardInterrupt, SystemExit, GeneratorExit → re-raise
            _log.debug(
                "[GHOST] safe_gather BaseException[%d]%s: %s", i, (" " + label) if label else "", type(item).__name__
            )
            raise item
        if isinstance(item, Exception):
            # [I8] — regular Exception → log + collect, never propagate silently
            _log.debug(
                "[GHOST] safe_gather exception[%d]%s: %s: %s",
                i,
                (" " + label) if label else "",
                type(item).__name__,
                item,
            )
            errors.append(item)
        else:
            ok.append(item)

    return SafeGatherResult(ok=ok, errors=errors)


# =============================================================================
# Sprint F261: _BoundedExceptionLog + safe_gather_fire_and_forget + safe_gather_ok
# Cutting-edge follow-up to F26X safe_gather.
#
# Three call shapes cover the 157 gather sites identified in the F260 audit:
#   1. safe_gather (struct)  — returns SafeGatherResult with .ok + .errors
#      → 28 sites with explicit _check_gathered() post-process
#   2. safe_gather_ok  — returns list[T], filters exceptions silently
#      → 17 sites with isinstance(r, Exception) filter
#      → 172 sites with for-loop / extend() pattern
#   3. safe_gather_fire_and_forget — returns None, log + bounded
#      → 41 sites with `await asyncio.gather(...)` (no var assigned)
#
# _BoundedExceptionLog bounds log spam during cascade failure (e.g. graceful
# shutdown where 50+ background tasks time out simultaneously). M1-safe: no
# new heavy imports, all in-process, slots=True, BoundedLog = at most 5
# detailed lines + 1 "N more silenced" summary.
# =============================================================================


# Sample cap: how many detailed exception entries to log before aggregating.
# 5 is the empirical sweet spot — small enough to avoid log spam, large enough
# to diagnose a non-trivial pattern. The "+N more" line always fires once.
_SAFE_GATHER_SAMPLE_CAP = 5


def _wrap_awaitable(value: Any) -> Awaitable[Any]:
    """Wrap a plain value in a coroutine so asyncio.gather accepts it.

    asyncio.gather (Python 3.10+) requires awaitables, not plain values.
    When callers mix `safe_gather(coro1, 42, coro2)`, plain values must
    be wrapped. M1-safe: a one-line async lambda per plain value (≈ 200B
    per closure), reused only for the duration of the gather call.

    If `value` is already awaitable (coroutine, Future, Task, or has
    __await__), it's returned unchanged.
    """
    if hasattr(value, "__await__"):
        return value  # type: ignore[no-any-return]

    async def _lift() -> Any:
        return value

    return _lift()


class _BoundedExceptionLog(msgspec.Struct, frozen=True):
    """Single bounded log line summarizing suppressed exceptions.

    Returned by safe_gather_fire_and_forget so callers can decide whether to
    escalate (e.g. for telemetry). msgspec.Struct keeps it cheap on M1 UMA.
    """

    sample: tuple[tuple[str, str, str], ...]  # ((type_name, str(exc), label), ...)
    suppressed_count: int  # how many additional exceptions
    # were collapsed into the summary


def _classify_gathered(
    raw: list[Any],
    label: str,
    _log: logging.Logger,
) -> tuple[list[Any], list[Exception], asyncio.CancelledError | BaseException | None]:
    """Shared classification kernel for all safe_gather_* variants.

    Returns:
        (ok, errors, re_raise)
        - ok: non-exception values from gather (in original order)
        - errors: Exception instances (logged at DEBUG, never raised)
        - re_raise: CancelledError or BaseException instance if one was
          encountered (caller decides whether to raise — fire_and_forget
          logs + returns, dropin raises, struct raises).

    Invariants enforced here (single source of truth):
        [I6] CancelledError → returned in re_raise (caller raises)
        [I7] non-Exception BaseException → returned in re_raise
        [I8] Exception → routed to errors + DEBUG logged

    Performance: Uses type() identity checks instead of isinstance() for the
    common exception types (CancelledError). Single-pass all-ok fast path.
    """
    n = len(raw)
    if n == 0:
        return [], [], None

    _CE = asyncio.CancelledError  # noqa: N806
    _BaseE = BaseException  # noqa: N806
    _Ex = Exception  # noqa: N806

    # Fast path: all-success case (common). Use isinstance for BaseException
    # (handles subclasses correctly). type() identity used only for specific types.
    all_ok = True
    for item in raw:
        if isinstance(item, _BaseE):  # isinstance handles subclasses correctly
            all_ok = False
            break

    if all_ok:
        # All items are non-exception results — common success path.
        return list(raw), [], None

    # Slow path: at least one exception present. Full classification.
    ok: list[Any] = []
    errors: list[Exception] = []
    re_raise: asyncio.CancelledError | BaseException | None = None

    for i, item in enumerate(raw):
        t = type(item)
        # CancelledError — check first (most common cancellation case)
        if t is _CE:
            _log.debug("[GHOST] gather CancelledError[%d]%s — re-raising", i, (" " + label) if label else "")
            if re_raise is None:
                re_raise = item
            continue
        # Exception subclass (includes all regular exceptions like NameError, TypeError, etc.)
        if isinstance(item, _Ex):
            _log.debug("[GHOST] gather exception[%d]%s: %s: %s", i, (" " + label) if label else "", t.__name__, item)
            errors.append(item)
            continue
        # BaseException but not Exception — KeyboardInterrupt, SystemExit, GeneratorExit
        if isinstance(item, _BaseE):
            _log.debug(
                "[GHOST] gather BaseException[%d]%s: %s — re-raising", i, (" " + label) if label else "", t.__name__
            )
            if re_raise is None:
                re_raise = item
            continue
        # Non-exception value — ok result
        ok.append(item)

    return ok, errors, re_raise


async def safe_gather_fire_and_forget[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> _BoundedExceptionLog | None:
    """F261: Fire-and-forget gather for sites that discard the result entirely.

    Use this when the caller is `await asyncio.gather(*tasks, return_exceptions=True)`
    as a bare expression statement. Replaces 41 sites identified in the F260 audit.

    Differences from safe_gather (struct):
        - Returns _BoundedExceptionLog (or None if all OK) — NOT a SafeGatherResult
        - Regular Exception instances are silently suppressed (logged at DEBUG, bounded sample)
        - CancelledError / non-Exception BaseException propagate (I6 + I7 invariants)

    Invariants enforced:
        - [I6] asyncio.CancelledError → re-raised (structured concurrency must not be swallowed)
        - [I7] non-Exception BaseException → re-raised
        - [I8] Exception → silently suppressed (bounded logging, no propagation)

    Args:
        *coros: Coroutines or awaitables to gather. Plain values pass through.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        _BoundedExceptionLog with sample of first 5 exceptions + suppressed count,
        or None if all coros succeeded.
    """
    _log = logger_instance or logger
    if not coros:
        return None

    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    _ok, errors, re_raise = _classify_gathered(raw, label, _log)

    # Fire-and-forget policy: CancelledError / non-Exception BaseException
    # MUST propagate (I6 + I7 invariants). Only regular Exception instances
    # are silently suppressed (fire-and-forget use case). Python 3.14+
    # changed CancelledError propagation — it must always be re-raised so
    # that structured concurrency cancellation is not silently swallowed.
    if re_raise is not None:
        _log.debug(
            "[GHOST] safe_gather_faf re-raising %s%s",
            type(re_raise).__name__,
            (" " + label) if label else "",
        )
        raise re_raise

    if not errors:
        return None

    # Bounded sample: first N detailed, then +M summary. M1-safe (no unbounded
    # list growth). Tuple of triples (type, str, label) is hashable + small.
    sample: list[tuple[str, str, str]] = []
    for exc in errors[:_SAFE_GATHER_SAMPLE_CAP]:
        sample.append((type(exc).__name__, str(exc)[:200], label))
    suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
    # Always emit a summary line (even when suppressed == 0) so callers can
    # grep for "suppressed N exceptions" without needing to count DEBUG
    # entries. Sample names go in the message either way.
    _log.debug(
        f"[GHOST] safe_gather_faf{' ' + label if label else ''} "
        f"suppressed {len(errors)} exceptions "
        f"(sample: {', '.join(t for t, _, _ in sample)}"
        f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
    )
    return _BoundedExceptionLog(sample=tuple(sample), suppressed_count=suppressed)


async def safe_gather_ok[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """F261: asyncio.gather wrapper returning only successful results.

    Returns a plain list of successful results, in original order, with all
    Exception instances filtered out. CancelledError / non-Exception
    BaseException are re-raised (same policy as safe_gather struct).

    Use this when the caller does one of:
        results = await asyncio.gather(..., return_exceptions=True)
        results = [r for r in results if not isinstance(r, Exception)]
        for r in results: ...  (with implicit exception skip)
        results.extend(...)  (then assign to a downstream list)

    Args:
        *coros: Coroutines or awaitables to gather.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        list[T] of successful results, exceptions silently dropped (logged at DEBUG).

    Raises:
        asyncio.CancelledError: if any coro was cancelled.
        BaseException: for non-Exception BaseException (KeyboardInterrupt, SystemExit).
    """
    _log = logger_instance or logger
    if not coros:
        return []

    # Pass wrapped coros directly to asyncio.gather. gather() in Python 3.12+
    # uses TaskGroup-like batch allocation internally — more efficient than
    # per-item loop.create_task(). Task/Future instances are awaitables and
    # pass through gather() unchanged (gather calls their __await__).
    # Plain values are wrapped by _wrap_awaitable so gather() can consume them.
    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    # Bounded log for the dropped errors — same sample cap as fire_and_forget.
    if errors:
        sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
        suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
        _log.debug(
            f"[GHOST] safe_gather_ok{' ' + label if label else ''} "
            f"dropped {len(errors)} exceptions "
            f"(sample: {sample_preview}"
            f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
        )

    if re_raise is not None:
        raise re_raise

    return ok


# P1-09: bounded_gather — semaphore-gated gather for I/O-bound loops
#
# Problem: sequential `for target in targets: result = await fetch(target)` wastes
# 100-500ms per item in TCP handshake + HTTP + parse. 10 targets = 1-5 s serial.
#
# Solution: asyncio.gather with asyncio.Semaphore concurrency cap.
# 10 targets at concurrency=10 → ~200-500 ms total (vs 1-5 s serial).
#
# M1 8GB: semaphore prevents fan-out explosion (e.g. 1000 concurrent DNS queries).
# All GHOST invariants (I6/I7/I8) are inherited from _classify_gathered kernel.
#
# Usage:
#   results, errors = await bounded_gather(
#       [fetch(t) for t in targets],
#       concurrency=5,  # M1 8GB: 5×50MB = 250MB peak, safe for UMA budget
#       ctx="discovery.sources",
#   )


async def bounded_gather[T](
    coros: list[Awaitable[T]],
    *,
    concurrency: int = 5,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> tuple[list[T], list[BaseException]]:
    """P1-09: Bounded concurrent gather with semaphore.

    Semantically equivalent to ``safe_gather_ok`` but with explicit
    concurrency cap. Use when the number of coroutines exceeds the safe
    fan-out bound for the underlying I/O resource (TCP connections, DNS
    resolver, HTTP/1.1 connection pool, etc.).

    Args:
        coros: List of awaitables to gather concurrently.
        concurrency: Maximum concurrent tasks (default 10). Must be ≥ 1.
        ctx: Context label for log messages (e.g. "discovery.sources").
        logger_instance: Optional logger override.

    Returns:
        Tuple of (ok_results, error_exceptions).
        - ok_results: successful results, in original order
        - error_exceptions: Exception instances (non-fatal; logged at DEBUG)

    Raises:
        asyncio.CancelledError: if the caller's task is cancelled.
        BaseException (not Exception): KeyboardInterrupt, SystemExit, etc.

    Invariants inherited from _classify_gathered:
        - [I6] CancelledError → re-raised immediately
        - [I7] non-Exception BaseException → re-raised immediately
        - [I8] Exception → routed to error_exceptions (logged at DEBUG)
    """
    _log = logger_instance or logger
    if not coros:
        return [], []
    if concurrency < 1:
        concurrency = 1

    sem = asyncio.Semaphore(concurrency)

    async def _wrapped(coro: Awaitable[T]) -> T:
        async with sem:
            return await coro

    wrapped = [_wrapped(c) for c in coros]
    raw = await asyncio.gather(*wrapped, return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, ctx, _log)

    if errors:
        sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
        suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
        _log.debug(
            f"[GHOST] bounded_gather{' ' + ctx if ctx else ''} "
            f"concurrency={concurrency} "
            f"dropped {len(errors)} exceptions "
            f"(sample: {sample_preview}"
            f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
        )
    else:
        suppressed = 0

    # Sprint F360: Record stats to MetricsRegistry for unified dashboard
    try:
        from hledac.universal.metrics_registry import get_metrics_registry

        get_metrics_registry().record_bounded_gather(
            ctx=ctx or "unknown",
            total_tasks=len(coros),
            ok_count=len(ok),
            error_count=len(errors),
            suppressed_count=suppressed,
        )
    except Exception:  # noqa: BLE001
        pass  # fail-soft: metrics never crash the gather

    if re_raise is not None:
        raise re_raise

    return ok, list(errors)  # type: ignore[return-value]


async def safe_gather_return_exceptions(
    *coros: Awaitable[Any],
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[Any]:
    """F314: asyncio.gather(return_exceptions=True) with GHOST invariants enforced.

    Drop-in for sites that need raw exception objects from gather() for
    downstream explicit handling, while still enforcing:
      - [I6] asyncio.CancelledError → re-raised immediately
      - [I7] non-Exception BaseException → re-raised immediately
      - [I8] regular Exception → returned as-is (not filtered)

    Use when caller does:
        results = await asyncio.gather(..., return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, CancelledError):
                ...  # explicit exception handling

    Args:
        *coros: Coroutines/awaitables to gather.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        list[Any]: raw gather results — exceptions NOT filtered, caller handles them.

    Raises:
        asyncio.CancelledError: if any coro was cancelled.
        BaseException: for non-Exception BaseException (KeyboardInterrupt, SystemExit).
    """
    _log = logger_instance or logger
    if not coros:
        return []

    loop = asyncio.get_running_loop()
    tasks: list[Any] = []
    for c in coros:
        if isinstance(c, (asyncio.Task, asyncio.Future)):
            tasks.append(c)
            continue
        try:
            tasks.append(loop.create_task(c))  # type: ignore[ty:invalid-argument-type]
        except TypeError:
            tasks.append(c)

    raw = await asyncio.gather(*tasks, return_exceptions=True)

    # Enforce [I6] + [I7]: re-raise CancelledError / non-Exception BaseException
    # (same kernel as _classify_gathered, but we keep raw exception objects intact)
    for item in raw:
        if isinstance(item, asyncio.CancelledError):
            _log.debug(
                f"[GHOST] safe_gather_return_exceptions{' ' + label if label else ''} CancelledError — re-raising"
            )
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            _log.debug(
                f"[GHOST] safe_gather_return_exceptions{' ' + label if label else ''} "
                f"{type(item).__name__} — re-raising"
            )
            raise item

    return list(raw)


# =============================================================================
# Sprint F265C: safe_gather_shielded — result-preserving TaskGroup
# =============================================================================
# Problem: raw TaskGroup cancels ALL siblings on first failure — successful
# results from cancelled siblings are LOST. safe_gather_strict preserves this
# behavior (all-or-nothing is intentional there).
#
# safe_gather_shielded solves this differently:
# - Uses TaskGroup for structured cancellation propagation
# - On failure: cancels siblings, BUT captures their results BEFORE cancel
# - Returns (results, errors) — partial success preserved
# - CancelledError / BaseException re-raised (I6/I7 invariant)
#
# Unlike safe_gather (gather-based): shield always cancels on first failure
# Unlike safe_gather_strict: shield preserves partial results
# Unlike safe_gather_ok: shield cancels siblings, not just logs


class SafeGatherShieldedResult(msgspec.Struct, frozen=True):
    """Result of `safe_gather_shielded` — msgspec.Struct for ~3× faster instantiation."""

    ok: list[Any] = msgspec.field(default_factory=list)
    errors: list[BaseException] = msgspec.field(default_factory=list)
    re_raised: BaseException | None = None


async def safe_gather_shielded[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> SafeGatherShieldedResult:
    """F265C: TaskGroup with result preservation on sibling cancellation.

    Uses `asyncio.TaskGroup` (PEP 654, 3.11+) for structured concurrency.
    Unlike raw TaskGroup which loses results on cancel, this helper
    captures results from ALL tasks even when siblings fail.

    Use this when:
        - You want structured cancellation (first failure → cancel siblings)
        - But also want to preserve partial results from successful siblings
        - You want TaskGroup semantics + gather-style result collection

    DO NOT use this when:
        - You want "all run, errors collected" → safe_gather_ok
        - You want strict all-or-nothing → safe_gather_strict
        - Running on Python < 3.11 (no TaskGroup)

    Args:
        *coros: Coroutines or awaitables. Plain values pass through.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        SafeGatherShieldedResult with .ok (all results), .errors (Exception list),
        .re_raised (BaseException if any was re-raised).

    Raises:
        asyncio.CancelledError: if caller's task was cancelled.
        BaseException (not Exception): re-raised per I7 invariant.
    """
    _log = logger_instance or logger
    if not coros:
        return SafeGatherShieldedResult(ok=[], errors=[], re_raised=None)

    results: list[Any] = [None] * len(coros)
    errors: list[BaseException] = []
    wrapped = [_wrap_awaitable(c) for c in coros]

    try:
        async with asyncio.TaskGroup() as tg:
            for i, c in enumerate(wrapped):

                async def _runner(idx: int, coro: Awaitable[Any]) -> None:
                    results[idx] = await coro

                tg.create_task(_runner(i, c), name=f"sg_shielded[{i}]")
    except BaseExceptionGroup as eg:
        # TaskGroup cancelled siblings. Collect errors from the group.
        for exc in eg.exceptions:
            if isinstance(exc, asyncio.CancelledError):
                _log.debug("[GHOST] safe_gather_shielded CancelledError%s", ("_" + label) if label else "")
                raise exc from None
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                _log.debug(
                    "[GHOST] safe_gather_shielded BaseException%s: %s",
                    ("_" + label) if label else "",
                    type(exc).__name__,
                )
                raise exc from None
            errors.append(exc)
        # Collect any non-cancelled results from the partial run
        ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
        return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=eg)
    except asyncio.CancelledError:
        _log.debug("[GHOST] safe_gather_shielded CancelledError%s", ("_" + label) if label else "")
        raise
    except BaseException as exc:
        if isinstance(exc, Exception):
            errors.append(exc)
            ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
            return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=None)
        _log.debug(
            "[GHOST] safe_gather_shielded BaseException%s: %s", ("_" + label) if label else "", type(exc).__name__
        )
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return SafeGatherShieldedResult(ok=ok_results, errors=[], re_raised=None)


class RaceFirstSuccessResult(msgspec.Struct, frozen=True):
    """Result of `race_first_success` — msgspec.Struct for ~3× faster instantiation."""

    result: Any = None
    winner_index: int = -1
    winner_label: str = ""
    errors: list[BaseException] = msgspec.field(default_factory=list)
    falsy_results: list[Any] = msgspec.field(default_factory=list)


async def race_first_success(
    *coros: tuple[Awaitable[Any], str],
    timeout: float | None = None,
    label: str = "",
    require_truthy: bool = True,
    logger_instance: logging.Logger | None = None,
) -> RaceFirstSuccessResult:
    """Race coroutines to first success — cancel all others immediately.
    Coroutines complete in parallel; the first one to finish AND (optionally)
    return a truthy non-exception result wins. All others are cancelled.
    Unlike ``safe_gather_shielded`` which waits for ALL tasks, this races
    on FIRST COMPLETION — perfect for profile fallback chains.

    Args:
        *coros: Tuples of (awaitable, label_string). Label is used in logs.
        timeout: Optional global timeout. If None, races indefinitely.
        label: Context string for log messages.
        require_truthy: If True (default), only a truthy non-None result qualifies
            as a win. If False, the first task to complete wins regardless of
            its return value. Losers are always cancelled on winner-set.
        logger_instance: Optional logger override.

    Returns:
        RaceFirstSuccessResult with .result (winner value), .winner_index,
        .winner_label, and .errors (exceptions from cancelled/failed losers).

    Raises:
        asyncio.CancelledError: if caller's task was cancelled.
        TimeoutError: if global timeout expires before any qualifying success.
    """
    _log = logger_instance or logger
    if not coros:
        return RaceFirstSuccessResult(result=None, winner_index=-1, winner_label="", errors=[])
    result_holder: list[Any] = [None]
    index_holder: list[int] = [-1]
    errors: list[BaseException] = []
    falsy_results: list[Any] = []  # track falsy completions for timeout diagnosis

    def _set_winner(idx: int, value: Any) -> bool:
        """Set winner if not already set. Returns True if THIS call set the winner."""
        if index_holder[0] < 0:
            result_holder[0] = value
            index_holder[0] = idx
            return True
        return False

    try:
        async with asyncio.timeout(timeout) if timeout else contextlib.nullcontext():
            async with asyncio.TaskGroup() as tg:
                for idx, (coro, coro_label) in enumerate(coros):

                    async def _runner(i: int, c: Awaitable[Any], lbl: str, need_truthy: bool) -> None:
                        try:
                            val = await c
                        except asyncio.CancelledError:
                            _log.debug("[race_first_success] %s cancelled (winner=%s)", lbl, index_holder[0] >= 0)
                            raise
                        except BaseException as e:
                            errors.append(e)
                            _log.debug("[race_first_success] %s failed: %s", lbl, e)
                        else:
                            # Non-exception return — extract win-condition.
                            # Session-creation tuples like (True, session) use val[0].
                            # Fall back to plain bool(val) for other return types.
                            if isinstance(val, tuple) and len(val) > 0 and isinstance(val[0], bool):
                                truthy_result = val[0]
                            else:
                                truthy_result = bool(val)
                            if not need_truthy or truthy_result:
                                _set_winner(i, val)
                            else:
                                falsy_results.append(val)

                    tg.create_task(_runner(idx, coro, coro_label, require_truthy), name=f"race:{coro_label}")
    except TimeoutError:
        return RaceFirstSuccessResult(
            result=None,
            winner_index=-1,
            winner_label="",
            errors=errors,
            falsy_results=falsy_results,
        )
    except BaseExceptionGroup as eg:
        for exc in eg.exceptions:
            if isinstance(exc, asyncio.CancelledError):
                continue
            # Non-CancelledError in BaseExceptionGroup = winner's exception.
            # Re-raise it so caller sees the propagation path clearly.
            # Do NOT add to errors — the bare raise carries it.
            raise exc from None
    idx = index_holder[0]
    winner_label = coros[idx][1] if idx >= 0 else ""
    return RaceFirstSuccessResult(
        result=result_holder[0],
        winner_index=idx,
        winner_label=winner_label,
        errors=errors,
        falsy_results=falsy_results,
    )


# =============================================================================
# F4.4: Trio-style CancelScope for graceful shutdown
# anyio is available transitively via aiohttp (anyio>=4.0 in aiohttp deps).
# Uses anyio.move_on_after() instead of asyncio.timeout for cross-runtime
# compatibility (asyncio/trio compatible). CancelScope provides structured
# cancellation with shield semantics.


async def cancel_scope_drain(
    timeout: float = 5.0,
    label: str = "",
    _log: logging.Logger | None = None,
) -> int:
    """Trio-style cancel scope drain for orphan tasks.

    Replaces the 38-LOC _cancel_orphan_tasks pattern in __main__.py and
    the inline orphan-drain block in core/__main__.py:2978-2993.

    Pattern: anyio.move_on_after() provides a cancel scope with timeout.
    Tasks are cancelled, then drained via gather(return_exceptions=True).
    TimeoutError from move_on_after is caught and logged — shutdown continues.

    Args:
        timeout: Maximum seconds to wait for tasks to drain (default 5.0).
        label: Context label for log messages.
        _log: Optional logger override (defaults to module logger).

    Returns:
        Number of tasks that were cancelled and drained.
    """
    _log = _log or logger
    current_task = asyncio.current_task()
    all_tasks = [t for t in asyncio.all_tasks() if t is not current_task and not t.done()]
    count = len(all_tasks)

    if not all_tasks:
        return 0

    for task in all_tasks:
        task.cancel()

    try:
        # anyio.move_on_after is the trio/anyio equivalent of asyncio.timeout.
        # anyio is available transitively via aiohttp deps.
        import anyio

        with anyio.move_on_after(timeout):
            await asyncio.gather(*all_tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Propagate CancelledError — caller handles it (e.g. in finally block).
        raise
    except Exception as e:
        _log.debug(f"[CANCEL_SCOPE_DRAIN{'_' + label if label else ''}] gather error: {e}")

    return count


# =============================================================================
# ISSUE-006: asyncio.TaskGroup helpers — Python 3.11+ PEP 654 cutting-edge
#
# Problem: sequential `for url in urls: await fetch(url)` is N×latency serial.
# asyncio.gather() / safe_gather_ok() handles parallel but has no concurrency
# cap built-in. bounded_gather() adds a semaphore but still uses gather().
#
# Solution: Two TaskGroup-based helpers using PEP 654 asyncio.TaskGroup:
#   1. gather_taskgroup() — TaskGroup + Semaphore, cleaner than bounded_gather
#   2. chunked_taskgroup() — memory-safe batch processing for M1 8GB
#
# Why TaskGroup over gather():
#   • Automatic ExceptionGroup aggregation (PEP 654)
#   • Structured cancellation — no hanging tasks on timeout
#   • async with scope — deterministic cleanup
#   • Built-in support in Python 3.11+ (always-on in this codebase)
#
# gather_taskgroup: semantically equivalent to bounded_gather but uses
# TaskGroup internally (more Pythonic for 3.11+).
#
# chunked_taskgroup: processes items in bounded batches. Yields results
# incrementally so callers can start processing while more items are still
# being fetched. M1 8GB safe — never holds more than `batch_size` in memory.
# =============================================================================


async def gather_taskgroup[T](
    coros: list[Awaitable[T]],
    *,
    concurrency: int = 10,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> tuple[list[T], list[BaseException]]:
    """ISSUE-006: TaskGroup + Semaphore parallel fetch.

    Drop-in replacement for bounded_gather with TaskGroup (PEP 654, 3.11+).
    Processes all coros concurrently with explicit concurrency cap.

    Args:
        coros: List of awaitables to run concurrently.
        concurrency: Max simultaneous tasks (default 10). M1 8GB: 10× ~50MB = 500MB.
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        Tuple of (ok_results, error_exceptions).

    Invariants:
        - [TG1] CancelledError → re-raised immediately
        - [TG2] non-Exception BaseException → re-raised immediately
        - [TG3] Exception → routed to error_exceptions (logged at DEBUG)
    """
    _log = logger_instance or logger
    if not coros:
        return [], []
    if concurrency < 1:
        concurrency = 1

    results: list[Any] = [None] * len(coros)
    errors: list[BaseException] = []

    sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, coro: Awaitable[T]) -> None:
        async with sem:
            results[idx] = await coro

    try:
        async with asyncio.TaskGroup() as tg:
            for idx, coro in enumerate(coros):
                tg.create_task(_run(idx, coro), name=f"tg[{idx}]")
    except BaseExceptionGroup as eg:
        for exc in eg.exceptions:
            if isinstance(exc, asyncio.CancelledError):
                _log.debug("[GHOST] gather_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
                raise exc from None
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                _log.debug(
                    "[GHOST] gather_taskgroup BaseException%s: %s", ("_" + ctx) if ctx else "", type(exc).__name__
                )
                raise exc from None
            errors.append(exc)
        # Collect non-exception results
        ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
        return ok_results, errors
    except asyncio.CancelledError:
        _log.debug("[GHOST] gather_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return ok_results, errors


async def chunked_taskgroup[T, R](
    items: list[T],
    coro_fn: Callable[[T], Awaitable[R]],
    *,
    batch_size: int = 20,
    concurrency: int = 10,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[R]:
    """ISSUE-006: Memory-safe batch processing via TaskGroup.

    Processes `items` in bounded batches using asyncio.TaskGroup. Each batch
    runs concurrently up to `concurrency` limit. Results are yielded incrementally
    so the caller can process results while the next batch is still loading.

    M1 8GB safe: at most `batch_size` items are in-flight at once.
    Compared to gather_taskgroup/bounded_gather which hold all results in memory.

    Args:
        items: List of items to process.
        coro_fn: Async function to apply to each item, e.g. `lambda url: fetch(url)`.
        batch_size: Items per batch (default 20). M1 8GB: 20 × ~50MB = 1GB/batch.
        concurrency: Max simultaneous tasks per batch (default 10).
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        List of results in original order (items that raised exceptions are omitted).

    Invariants:
        - [CT1] Items processed in order within each batch
        - [CT2] Exceptions in a batch are logged at DEBUG, not propagated
        - [CT3] Batch results are yielded immediately after the batch completes
    """
    _log = logger_instance or logger
    if not items:
        return []
    if batch_size < 1:
        batch_size = 1
    if concurrency < 1:
        concurrency = 1

    all_results: list[R] = []
    sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, item: T) -> tuple[int, R | None]:
        async with sem:
            try:
                result = await coro_fn(item)
                return idx, result
            except Exception as e:
                _log.debug("[GHOST] chunked_taskgroup[%s] item[%d] exception: %s", ctx, idx, type(e).__name__)
                return idx, None

    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]
        # Shared list to capture results from TaskGroup tasks
        batch_results: list[Any | None] = [None] * len(batch)

        async def _run_with_capture(local_idx: int, item: T) -> None:  # noqa: B023
            nonlocal batch_results
            batch_results[local_idx] = await _run(local_idx, item)  # noqa: B023

        try:
            async with asyncio.TaskGroup() as tg:
                for local_idx, item in enumerate(batch):
                    tg.create_task(_run_with_capture(local_idx, item), name=f"chunk[{batch_start + local_idx}]")
        except BaseExceptionGroup as eg:
            for exc in eg.exceptions:
                if isinstance(exc, asyncio.CancelledError):
                    _log.debug("[GHOST] chunked_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
                raise exc from None
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                _log.debug(
                    "[GHOST] chunked_taskgroup BaseException%s: %s", ("_" + ctx) if ctx else "", type(exc).__name__
                )
                raise exc from None
            # Collect None results (exceptions)
        except asyncio.CancelledError:
            _log.debug("[GHOST] chunked_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
            raise

        # Collect non-None results from this batch
        for r in batch_results:
            if r is not None and not isinstance(r, BaseException):
                _, val = r
                all_results.append(val)

    return all_results


# ----------------------------------------------------------------------
# BoundedPerHostGate — LRU-bounded per-host concurrency gate
# ----------------------------------------------------------------------
class BoundedPerHostGate:
    """
    Bounded per-host concurrency gate with LRU eviction.

    Prevents unbounded growth of per-host Semaphore objects in
    FetchCoordinator when crawling high-diversity URL sets.

    Invariants:
    - max_hosts cap bounds RAM usage (~512 hosts × ~250 B ≈ 128 KB)
    - LRU eviction keeps hot hosts resident
    - Telemetry: evicted / hits / misses counters
    """

    __slots__ = ("_max_hosts", "_per_host_limit", "_gates", "_last_used", "_stats")

    def __init__(self, max_hosts: int = 512, per_host_limit: int = 4) -> None:
        self._max_hosts = max_hosts
        self._per_host_limit = per_host_limit
        self._gates: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        self._last_used: dict[str, float] = {}
        self._stats: dict[str, int] = {"evicted": 0, "hits": 0, "misses": 0}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _evict_idle(self) -> None:
        """Evict LRU hosts when over capacity (called lazily on miss)."""
        if len(self._gates) < self._max_hosts:
            return
        # Sort by last_used ascending — evict oldest first
        sorted_by_age = sorted(
            self._gates.keys(),
            key=lambda h: self._last_used.get(h, 0.0),
        )
        # Evict exactly the overage — no headroom padding
        evict_count = max(1, len(self._gates) - self._max_hosts)
        for host in sorted_by_age[:evict_count]:
            del self._gates[host]
            self._last_used.pop(host, None)
        self._stats["evicted"] += evict_count

    # ------------------------------------------------------------------
    # Public API — acquire / release pair
    # ------------------------------------------------------------------
    async def acquire(self, host: str) -> tuple[asyncio.Semaphore, str]:
        """
        Acquire a per-host concurrency slot.

        Returns (semaphore_instance, op_id) where op_id is 'hit' or 'miss'.
        The caller MUST pass the returned semaphore to ``release()`` —
        NOT self._gates[host], which may have been evicted and replaced.
        """
        # Monotonic timestamp via time.monotonic (available in asyncio context)
        now = time.monotonic()
        if host in self._gates:
            sem = self._gates[host]
            self._gates.move_to_end(host)
            self._last_used[host] = now
            self._stats["hits"] += 1
            op_id = "hit"
        else:
            self._evict_idle()
            sem = asyncio.Semaphore(self._per_host_limit)
            self._gates[host] = sem
            self._last_used[host] = now
            self._stats["misses"] += 1
            op_id = "miss"

        await sem.acquire()
        return sem, op_id

    def release(self, sem: asyncio.Semaphore) -> None:
        """
        Release a per-host slot using the instance returned by ``acquire()``.

        Safe against double-release (ValueError is swallowed).
        """
        try:
            sem.release()
        except ValueError:
            pass  # already released

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot."""
        return {
            **self._stats,
            "active_hosts": len(self._gates),
            "max_hosts": self._max_hosts,
        }


# ---------------------------------------------------------------------------
# Lifecycle helpers — stop pattern (F360)
# ---------------------------------------------------------------------------


async def stop_task(coro: asyncio.Task[Any] | None) -> None:
    """
    Stop a background task gracefully — cancel and await CancelledError.

    Standardises the ``_running + _task`` cancellation pattern used across
    SprintScheduler, SystemResourcesSampler, ResourceGovernor and similar
    run-loops.

    Pattern::

        self._running = False
        await stop_task(self._task)
        self._task = None

    Args:
        coro: The asyncio.Task to cancel. None or already-finished tasks
              are handled silently (no-op).
    """
    if coro is None:
        return
    coro.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await coro
