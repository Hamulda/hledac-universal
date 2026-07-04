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
import sys
import time
from typing import TYPE_CHECKING, Any, TypeVar
from collections.abc import Awaitable

T = TypeVar("T")

if TYPE_CHECKING:
    pass

__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "bounded_gather",
    "monotonic_ms",
    "safe_gather",
    "safe_gather_dropin",
    "safe_gather_fire_and_forget",
    "safe_gather_strict",
    "safe_gather_shielded",
    "safe_gather_return_exceptions",
    "safe_create_task",
    "safe_wait_for",
    "SafeGatherResult",
    "SafeGatherShieldedResult",
    "_BoundedExceptionLog",
    "cancel_scope_drain",
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


def safe_create_task(
    coro: Any,
    *,
    name: str | None = None,
    eager_start: bool = False,
) -> asyncio.Task[Any]:
    """
    Sprint F228G: Defensive create_task wrapper that probes the running loop's
    create_task signature and only passes `eager_start` if the loop supports
    it.

    Why this exists:
      - Some event loop implementations (uvloop 0.22.x on M1) do NOT implement
        the `eager_start` kwarg that Python 3.12+ stdlib asyncio accepts.
      - A naive `sys.version_info >= (3, 12)` check is insufficient because
        uvloop overrides create_task and drops the kwarg.
      - Calling `loop.create_task(coro, eager_start=True)` on uvloop raises
        `TypeError: create_task() got an unexpected keyword argument 'eager_start'`.

    The probe:
      - Run ONCE at module import time (cached in _EAGER_START_SUPPORTED).
      - Only passes eager_start=True when the loop actually accepts it.

    Returns:
      asyncio.Task wrapping the coroutine. Never raises TypeError from
      signature mismatch — falls back to standard create_task.

    Invariant: bounded, fail-safe. If the import-time probe failed (e.g. no
    event loop available), _EAGER_START_SUPPORTED is False and we always use
    the safe path.
    """
    if eager_start and _EAGER_START_SUPPORTED:
        try:
            return asyncio.create_task(coro, name=name, eager_start=True)  # type: ignore[call-arg]
        except TypeError:
            # Defensive: even if the probe passed, the actual loop may not
            # accept the kwarg. Fall back to standard call.
            pass
    return asyncio.create_task(coro, name=name)


def _check_gathered(
    results: list[Any],
    logger_instance: logging.Logger | None = None,
    ctx: str = ""
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
    """
    ok_results: list[Any] = []
    cancel_errors: list[BaseException] = []
    other_errors: list[BaseException] = []
    _log = logger_instance or logger

    for i, item in enumerate(results):
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            # [I7] — KeyboardInterrupt, SystemExit, GeneratorExit
            _log.debug("[GHOST] gather BaseException[%d]%s: %s — collecting for PEP 654 aggregation",
                       i, (' ' + ctx) if ctx else '', type(item).__name__)
            cancel_errors.append(item)
            continue
        if isinstance(item, asyncio.CancelledError):
            # [I6] — CancelledError (BaseException subclass since 3.11+)
            _log.debug("[GHOST] gather CancelledError[%d]%s — collecting for PEP 654 aggregation",
                       i, (' ' + ctx) if ctx else '')
            cancel_errors.append(item)
            continue
        if isinstance(item, Exception):
            # [I8] — regular Exception → route to errors
            _log.debug("[GHOST] gather exception[%d]%s: %s: %s",
                       i, (' ' + ctx) if ctx else '', type(item).__name__, item)
            other_errors.append(item)
            continue
        ok_results.append(item)

    # Aggregation logic — PEP 654 compliant
    if cancel_errors:
        if len(cancel_errors) == 1 and not other_errors:
            # Single cancel + no other errors → bare raise (PEP 654 bare raise idiom)
            _log.debug("[GHOST] gather single CancelledError%s — bare raise",
                       (' ' + ctx) if ctx else '')
            raise cancel_errors[0]
        # Multiple cancels OR cancel + exception mix → BaseExceptionGroup
        all_errors: list[BaseException] = cancel_errors + other_errors
        if len(all_errors) == 1:
            raise all_errors[0]
        _log.debug("[GHOST] gather BaseExceptionGroup[%d]%s — raising aggregated",
                   len(all_errors), (' ' + ctx) if ctx else '')
        raise BaseExceptionGroup(f"gather{' ' + ctx if ctx else ''}", all_errors)

    # Only non-cancel exceptions → error_results
    return ok_results, other_errors


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
        _log.debug(
            f"[GHOST] safe_wait_for{'_' + label if label else ''} "
            f"timeout after {timeout}s"
        )
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


from dataclasses import dataclass, field  # noqa: E402


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
            _log.debug("[GHOST] safe_gather CancelledError[%d]%s",
                       i, (' ' + label) if label else '')
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            # [I7] — KeyboardInterrupt, SystemExit, GeneratorExit → re-raise
            _log.debug("[GHOST] safe_gather BaseException[%d]%s: %s",
                       i, (' ' + label) if label else '', type(item).__name__)
            raise item
        if isinstance(item, Exception):
            # [I8] — regular Exception → log + collect, never propagate silently
            _log.debug("[GHOST] safe_gather exception[%d]%s: %s: %s",
                       i, (' ' + label) if label else '', type(item).__name__, item)
            errors.append(item)
        else:
            ok.append(item)

    return SafeGatherResult(ok=ok, errors=errors)


# =============================================================================
# Sprint F261: _BoundedExceptionLog + safe_gather_fire_and_forget + safe_gather_dropin
# Cutting-edge follow-up to F26X safe_gather.
#
# Three call shapes cover the 157 gather sites identified in the F260 audit:
#   1. safe_gather (struct)  — returns SafeGatherResult with .ok + .errors
#      → 28 sites with explicit _check_gathered() post-process
#   2. safe_gather_dropin  — returns list[T], filters exceptions silently
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


@dataclass(frozen=True, slots=True)
class _BoundedExceptionLog:
    """Single bounded log line summarizing suppressed exceptions.

    Returned by safe_gather_fire_and_forget so callers can decide whether to
    escalate (e.g. for telemetry). Frozen + slots keeps it cheap on M1 UMA.
    """
    sample: tuple[tuple[str, str, str], ...]   # ((type_name, str(exc), label), ...)
    suppressed_count: int                       # how many additional exceptions
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
    """
    n = len(raw)
    if n == 0:
        return [], [], None

    # Fast path: all-success case (common). One isinstance check for all items.
    # Checks BaseException first since Exception is the common case; the
    # hierarchy is BaseException → Exception → subclass.
    all_ok = True
    for item in raw:
        if isinstance(item, BaseException):  # CancelledError or Exception or BaseException
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
        if isinstance(item, asyncio.CancelledError):
            _log.debug("[GHOST] gather CancelledError[%d]%s — re-raising",
                       i, (' ' + label) if label else '')
            if re_raise is None:
                re_raise = item
            continue
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            _log.debug("[GHOST] gather BaseException[%d]%s: %s — re-raising",
                       i, (' ' + label) if label else '', type(item).__name__)
            if re_raise is None:
                re_raise = item
            continue
        if isinstance(item, Exception):
            _log.debug("[GHOST] gather exception[%d]%s: %s: %s",
                       i, (' ' + label) if label else '', type(item).__name__, item)
            errors.append(item)
        else:
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
        - Does NOT re-raise CancelledError / BaseException — only logs at DEBUG
        - Samples the first 5 exceptions in detail, then emits a single
          "… +N more silenced" line to bound log volume during cascade failure

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

    # Fire-and-forget policy: log the re-raise candidate at DEBUG but do not
    # propagate. Graceful shutdown paths in F260 audit frequently saw
    # CancelledError during stop(); re-raising here would mask the original
    # stop() intent.
    if re_raise is not None:
        _log.debug("[GHOST] safe_gather_faf re-raise suppressed%s: %s",
                   (' ' + label) if label else '', type(re_raise).__name__)

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


async def safe_gather_dropin[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """F261: Drop-in replacement for asyncio.gather(return_exceptions=True).

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
            f"[GHOST] safe_gather_dropin{' ' + label if label else ''} "
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
#       concurrency=10,
#       ctx="discovery.sources",
#   )


async def bounded_gather[T](
    coros: list[Awaitable[T]],
    *,
    concurrency: int = 10,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> tuple[list[T], list[BaseException]]:
    """P1-09: Bounded concurrent gather with semaphore.

    Semantically equivalent to ``safe_gather_dropin`` but with explicit
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
                f"[GHOST] safe_gather_return_exceptions{' ' + label if label else ''} "
                f"CancelledError — re-raising"
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
# Sprint F262: safe_gather_strict — TaskGroup-based, true all-or-nothing
#
# The cutting-edge PEP 654 / 3.11+ counterpart to the gather-based variants.
# Use ONLY when failure of any sibling task MUST abort the rest (e.g. sprint
# lifecycle, feed pipeline). Direct TaskGroup migration of the 143 gather
# sites is INCORRECT — gather(return_exceptions=True) has different semantics
# (all complete, errors collected) and direct migration would lose results
# and break the M1 fail-soft invariant.
#
# Behaviour differences vs safe_gather (gather-based):
#   - First error cancels ALL siblings (TaskGroup semantics)
#   - Successful task results from cancelled siblings are LOST
#   - On failure, raises BaseExceptionGroup (PEP 654) — use `except*`
#   - On success, returns list[T] of all results in original order
#
# Cutting-edge:
#   - Uses asyncio.TaskGroup (PEP 654, 3.11+) — guaranteed available
#   - Uses except* (PEP 654) for structured exception handling
#   - Zero allocations on success path (only the result list)
#   - Bounded on failure: one BaseExceptionGroup per call (~400B)
#
# M1-safe: pure Python, no MLX/numpy, no Metal interaction.
# =============================================================================


async def safe_gather_strict[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """F262: TaskGroup-based gather with strict all-or-nothing semantics.

    Uses `asyncio.TaskGroup` (PEP 654, 3.11+) internally. On any task failure,
    ALL siblings are cancelled and the function raises `BaseExceptionGroup`
    containing all encountered errors.

    Use this when:
        - The caller explicitly wants "all-or-nothing" cancellation
        - Failed siblings should NOT produce partial results
        - The caller is prepared to handle `BaseExceptionGroup` via `except*`

    DO NOT use this when:
        - You want "all run, errors collected" (use `safe_gather` or
          `safe_gather_dropin` instead)
        - One bad task should not abort the rest (M1 fail-soft invariant)

    Args:
        *coros: Coroutines or awaitables. Plain values are auto-wrapped.
        label:  Context string for log messages.
        logger_instance: Optional logger override.

    Returns:
        list[T] of all results in original order. ALL tasks succeeded.

    Raises:
        BaseExceptionGroup: if any task failed. Contains all errors via
            `.exceptions`. Use `except*` to handle individual error types.
        asyncio.CancelledError: if the caller's task was cancelled.
    """
    _log = logger_instance or logger
    if not coros:
        return []

    results: list[Any] = [None] * len(coros)
    wrapped = [_wrap_awaitable(c) for c in coros]

    # PEP 654: TaskGroup + except* for structured concurrency.
    # On success: `async with` block completes, all results populated.
    # On failure: TaskGroup cancels siblings, raises BaseExceptionGroup.
    #   We catch the group, log it, and re-raise with our label context.
    try:
        async with asyncio.TaskGroup() as tg:
            for i, c in enumerate(wrapped):
                # Create a runner that captures the result, then delegate
                # to the actual coro. This preserves the result even if a
                # sibling raises (TaskGroup's own runners are cancelled
                # before they can populate external state).
                async def _runner(idx: int, coro: Awaitable[Any]) -> None:
                    results[idx] = await coro
                # NOTE: `eager_start` is a kwarg of `AbstractEventLoop.create_task`,
                # NOT of `asyncio.TaskGroup.create_task` (stdlib stub: signature
                # is (coro, *, name=None, context=None)). Spreading it here would
                # raise TypeError at runtime. eager_start acceleration is only
                # applied on the safe_gather_dropin path (see line ~532).
                tg.create_task(
                    _runner(i, c),
                    name=f"sg_strict[{i}]",
                )
    except BaseExceptionGroup as eg:
        # Log at WARNING (this is the strict path; failures are expected
        # to be handled by the caller). Include the label for diagnostics.
        sample_types = [type(e).__name__ for e in eg.exceptions[:_SAFE_GATHER_SAMPLE_CAP]]
        _log.debug(
            f"[GHOST] safe_gather_strict{' ' + label if label else ''} "
            f"raised BaseExceptionGroup with {len(eg.exceptions)} errors "
            f"(sample: {', '.join(sample_types)})"
        )
        raise

    return results


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
# Unlike safe_gather_dropin: shield cancels siblings, not just logs


@dataclass(frozen=True, slots=True)
class SafeGatherShieldedResult:
    """Result of `safe_gather_shielded` — frozen dataclass."""
    ok: list[Any] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)
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
        - You want "all run, errors collected" → safe_gather_dropin
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
                _log.debug("[GHOST] safe_gather_shielded CancelledError%s",
                           ('_' + label) if label else '')
                raise exc
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                _log.debug("[GHOST] safe_gather_shielded BaseException%s: %s",
                           ('_' + label) if label else '', type(exc).__name__)
                raise exc
            errors.append(exc)
        # Collect any non-cancelled results from the partial run
        ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
        return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=eg)
    except asyncio.CancelledError:
        _log.debug("[GHOST] safe_gather_shielded CancelledError%s",
                   ('_' + label) if label else '')
        raise
    except BaseException as exc:
        if isinstance(exc, Exception):
            errors.append(exc)
            ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
            return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=None)
        _log.debug("[GHOST] safe_gather_shielded BaseException%s: %s",
                   ('_' + label) if label else '', type(exc).__name__)
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return SafeGatherShieldedResult(ok=ok_results, errors=[], re_raised=None)


# =============================================================================
# F4.4: Trio-style CancelScope for graceful shutdown
# anyio is available transitively via aiohttp (anyio>=4.0 in aiohttp deps).
# Uses anyio.move_on_after() instead of asyncio.timeout for cross-runtime
# compatibility (asyncio/trio compatible). CancelScope provides structured
# cancellation with shield semantics.
# =============================================================================


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
