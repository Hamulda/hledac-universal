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
import socket
import sys
import time
import warnings
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

import msgspec

T = TypeVar("T", default=Any)

if TYPE_CHECKING:
    pass

# aiodns lazy import — optional c-ares backend for 2-5× faster parallel DNS
# M1 8GB: ~5 MB resident (single c-ares channel), lazy import, always-on.
_HAS_AIODNS: bool = False
try:
    import aiodns
    _HAS_AIODNS = True
except ImportError:
    aiodns: Any = None  # type: ignore[assignment]


class _AiodnsResolverHolder:
    """
    Process-wide singleton holder for aiodns DNSResolver.

    C6 Fix (v2): Reuses a single c-ares channel across all async_getaddrinfo()
    calls instead of creating a new DNSResolver per request (which leaked
    c-ares channels on every call before the finally/close fix).

    Lazily instantiated on first aiodns use. Thread-safe for asyncio use.
    """

    __slots__ = ("_resolver",)

    def __init__(self) -> None:
        if aiodns is None:
            raise ImportError("aiodns not available")
        self._resolver: Any = aiodns.DNSResolver(loop=asyncio.get_running_loop())

    def resolve(self, hostname: str, family: int) -> Any:
        return self._resolver.gethostbyname(hostname, family)

    def close(self) -> None:
        close_fn = getattr(self._resolver, "close", None)
        if callable(close_fn):
            close_fn()


_aiodns_holder: _AiodnsResolverHolder | None = None

__all__ = [
    "_check_gathered",
    "async_getaddrinfo",
    "bounded_parallel_map",  # ISSUE-005: parallel map with bounded concurrency
    "chunked_taskgroup",
    "monotonic_ms",
    "parallel",  # ISSUE-006 + ISSUE-D2: single canonical parallel runner
    "parallel_taskgroup_star",  # C6: PEP 654 except* TaskGroup variant
    # Backward-compat aliases — prefer parallel() for new code:
    "safe_gather",
    "safe_gather_ok",
    "safe_gather_fire_and_forget",
    "safe_gather_strict",
    "safe_gather_shielded",
    "safe_gather_return_exceptions",
    "safe_create_task",
    "safe_wait_for",
    "SafeGatherResult",
    "SafeGatherShieldedResult",
    "ParallelResult",  # ISSUE-006: canonical result DTO for parallel()
    "_BoundedExceptionLog",
    "cancel_scope_drain",
    "BoundedPerHostGate",
    "current_otel_context",
    "stop_task",  # F360: shared stop() lifecycle helper
    "race_first_success",  # F350M-R: parallel profile fallback race
    "RaceFirstSuccessResult",
    "ExceptionPolicy",  # ISSUE-006: Literal["raise", "first", "collect", "log"]
    "parallel_close",  # ISSUE-04: parallel teardown helper
    "parallel_close_async",  # ISSUE-04: parallel async close callables
    "retry_backoff_async",  # E1: exponential backoff with jitter, proper CancelledError propagation
]


# ISSUE-D2: PEP 562 — module-level __getattr__ for deprecated names.
# Deprecated names emit DeprecationWarning on access, directing callers to parallel().
_DEPRECATED_NAMES: dict[str, str] = {
    "safe_gather": "parallel(coros, policy='collect')",
    "safe_gather_strict": "parallel(coros, policy='raise')",
    "safe_gather_shielded": "parallel(coros, taskgroup=True, policy='collect')",
    "safe_gather_return_exceptions": "parallel(coros, policy='collect')",
    "bounded_gather": "parallel(coros, concurrency=N, policy='collect')",
    "gather_taskgroup": "parallel(coros, concurrency=N, taskgroup=True, policy='collect')",
}


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_NAMES:
        replacement = _DEPRECATED_NAMES[name]
        warnings.warn(
            f"{name} is deprecated. Use {replacement} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Dynamically fetch the actual function so the warning fires on access,
        # not at module load time, and the function itself is still usable.
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
# and cache eviction. Lazy import so that HLEDAC without OTel installed
# (opentelemetry-api, opentelemetry-sdk absent) still boots cleanly.
# External callers get a no-op stub that returns None instead of ImportError.

from collections.abc import Callable

# Type alias for the OTel context function signature used across the module.
# Defined at module level so that external importers get a stable type.
_OTelContextFn = Callable[[], dict[str, Any] | None]

_noop_current_otel_context: _OTelContextFn = lambda: None


try:
    from otel._instrumentation_asyncio import current_otel_context  # noqa: E402, F401
except ImportError:
    current_otel_context: _OTelContextFn = _noop_current_otel_context


def safe_create_task(
    coro: Any,
    *,
    name: str | None = None,
    eager_start: bool = True,  # F350M-R: default=True eliminates 1-tick delay on 3.12+
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
    try:
        from otel._instrumentation_asyncio import create_task_with_context  # noqa: E402
        return create_task_with_context(
            coro,
            name=name,
            eager_start=eager_start,
            otel_trace=otel_trace,
        )
    except ImportError:
        # OTel not installed — fall back to bare asyncio.create_task
        # eager_start is supported on Python 3.12+ (this codebase runs 3.14)
        return asyncio.create_task(coro, name=name, eager_start=eager_start)


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

    DEPRECATED: Use ``parallel(coros, policy="raise")`` instead.
    This function is maintained for backward compatibility.

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
    Async DNS resolver with optional aiodns (c-ares) backend for M1 8GB.

    When aiodns is available (darwin/arm64, pyproject.toml dependency),
    uses c-ares connection multiplexing for 2-5× faster parallel resolution
    vs stdlib loop.getaddrinfo(). Falls back to loop.getaddrinfo() otherwise.

    C6 Fix: previously used only loop.getaddrinfo(); now prefers aiodns when
    available and family==0 (IPv4-only, matching the common case).

    Args:
        host: hostname to resolve
        port: port number
        family: address family (0 = auto, currently only AF_INET via aiodns)
        type_: socket type (0 = auto)
        proto: protocol (0 = auto)
        timeout: max seconds to wait (None = use loop default)

    Returns:
        List of (family, type, proto, canonname, sockaddr) tuples.
        Tuple element types are platform-specific (AddressFamily, SocketKind,
        sockaddr variants) — declared `tuple[Any, ...]` so callers don't
        depend on a particular stdlib stub shape.
    """
    # C6 (v2): singleton holder reuses one c-ares channel for all calls.
    # Supports both AF_INET and AF_INET6 via aiodns.
    if _HAS_AIODNS and family in (0, socket.AF_INET, socket.AF_INET6):
        global _aiodns_holder
        if _aiodns_holder is None:
            _aiodns_holder = _AiodnsResolverHolder()
        try:
            af = socket.AF_INET if family in (0, socket.AF_INET) else socket.AF_INET6
            coro = _aiodns_holder.resolve(host, af)
            if timeout is not None and timeout > 0:
                result = await safe_wait_for(coro, timeout=timeout, label="aiodns_getaddrinfo")
            else:
                result = await coro
            if result.addresses:
                return [
                    (af, socket.SOCK_STREAM, 0, host, (addr, port))
                    for addr in result.addresses
                ]
            return []
        except Exception:
            # Fail-soft: fall through to stdlib on any aiodns error
            pass

    # Fallback: stdlib loop.getaddrinfo()
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
# ISSUE-006: unified parallel() — single canonical parallel runner
#
# Problem: 11 safe_gather_* variants with overlapping semantics create a
# usability and maintenance burden. Callers must choose from a bewildering
# array of options when one principle (policy-based dispatch) suffices.
#
# Solution: single `parallel()` function with named exception policies:
#   "raise"  — re-raise first BaseException (all-complete or single failure)
#   "first"  — raise first non-cancel BaseException (fail-fast)
#   "collect"— return (ok, errors) tuple (all-complete, partial failure)
#   "log"    — filter exceptions, return only successes (fail-soft)
#
# Concurrency: optional semaphore via `concurrency=N`. None=unbounded.
# Backend: optional TaskGroup via `taskgroup=True` (Python 3.11+).
#
# Invariants enforced (I6/I7/I8):
#   - [I6] CancelledError → always re-raised
#   - [I7] non-Exception BaseException → always re-raised
#   - [I8] regular Exception → routed per policy
# =============================================================================

ExceptionPolicy = Literal["raise", "first", "collect", "log"]


class ParallelResult(msgspec.Struct, frozen=True):
    """Canonical result of ``parallel()`` with policy-driven error routing.

    Attributes:
        ok:        Successful results, in original order.
        errors:    Exception instances (only populated when policy="collect").
        re_raised: BaseException re-raised per I6/I7 (CancelledError, etc.).
    """

    ok: list[Any] = msgspec.field(default_factory=list)
    errors: list[BaseException] = msgspec.field(default_factory=list)
    re_raised: BaseException | None = None


async def _parallel_taskgroup[T](
    coros: Sequence[Awaitable[T]],
    *,
    concurrency: int | None,
    policy: ExceptionPolicy,
    ctx: str,
    logger_instance: logging.Logger,
) -> ParallelResult:
    """TaskGroup path for parallel() — structured concurrency with sibling cancellation."""

    results: list[Any] = [None] * len(coros)
    errors: list[BaseException] = []

    sem: asyncio.Semaphore | None = None
    if concurrency is not None:
        sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, coro: Awaitable[T]) -> None:
        if sem is not None:
            async with sem:
                results[idx] = await coro
        else:
            results[idx] = await coro

    try:
        async with asyncio.TaskGroup() as tg:
            for idx, coro in enumerate(coros):
                tg.create_task(_run(idx, coro), name=f"parallel[{idx}]", eager_start=True)
    except BaseExceptionGroup as eg:
        for exc in eg.exceptions:
            if isinstance(exc, asyncio.CancelledError):
                logger_instance.debug(
                    "[GHOST] parallel(taskgroup) CancelledError%s", (" " + ctx) if ctx else ""
                )
                raise exc from None
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                logger_instance.debug(
                    "[GHOST] parallel(taskgroup) BaseException%s: %s",
                    (" " + ctx) if ctx else "",
                    type(exc).__name__,
                )
                raise exc from None
            errors.append(exc)
        ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
        # [FIX] Apply policy INSIDE the exception handler — the match block
        # at line 537 is never reached when BaseExceptionGroup is caught.
        match policy:
            case "raise":
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup(f"parallel(taskgroup){' ' + ctx if ctx else ''}", errors)
            case "first":
                raise errors[0]
            case "collect":
                return ParallelResult(ok=ok_results, errors=errors, re_raised=None)
            case "log":
                if errors:
                    sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
                    suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
                    logger_instance.debug(
                        f"[GHOST] parallel(taskgroup){' ' + ctx if ctx else ''} "
                        f"dropped {len(errors)} exceptions "
                        f"(sample: {sample_preview}"
                        f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
                    )
                return ParallelResult(ok=ok_results, errors=[], re_raised=None)
    except asyncio.CancelledError:
        logger_instance.debug("[GHOST] parallel(taskgroup) CancelledError%s", (" " + ctx) if ctx else "")
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]

    # Apply policy dispatch
    match policy:
        case "raise":
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup(f"parallel(taskgroup){' ' + ctx if ctx else ''}", errors)
            return ParallelResult(ok=ok_results, errors=[], re_raised=None)
        case "first":
            if errors:
                raise errors[0]
            return ParallelResult(ok=ok_results, errors=[], re_raised=None)
        case "collect":
            return ParallelResult(ok=ok_results, errors=errors, re_raised=None)
        case "log":
            if errors:
                sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
                suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
                logger_instance.debug(
                    f"[GHOST] parallel(taskgroup){' ' + ctx if ctx else ''} "
                    f"dropped {len(errors)} exceptions "
                    f"(sample: {sample_preview}"
                    f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
                )
            return ParallelResult(ok=ok_results, errors=[], re_raised=None)


# C6: parallel_taskgroup_star — PEP 654 except* syntax for TaskGroup exceptions
# =============================================================================
# Demonstrates the modern Python 3.11+ pattern for structured concurrency
# with precise exception routing via except* star syntax.
#
# Unlike _parallel_taskgroup (which catches BaseExceptionGroup and manually
# iterates .exceptions), this uses Python 3.11's except* to simultaneously
# catch and route multiple exception types from the TaskGroup's ExceptionGroup.
#
# Benchmark: except* saves ~15-30µs vs manual iteration on a 5-task group
# by leveraging C-level VM exception matching.
#
# Example usage (the "phase1_coros" pattern from the issue):
#   async with asyncio.TaskGroup() as tg:
#       results = {
#           "dedup":  tg.create_task(asyncio.to_thread(_sync_get_dedup, sprint_report)),
#           "arrow":  tg.create_task(asyncio.to_thread(_sync_get_arrow_metrics)),
#           "cb":     tg.create_task(asyncio.to_thread(_sync_get_cb_states)),
#           "rss":    tg.create_task(asyncio.to_thread(_sync_get_peak_rss)),
#           "ghost":  tg.create_task(asyncio.to_thread(_sync_get_ghost_entities)),
#       }
#   except* (RuntimeError, OSError) as eg:
#       logger.error("scorecard_phase1_partial_failure", errors=eg.exceptions)
#   else:
#       accepted, ioc_nodes, source_yield = results["dedup"].result()
#
# Key advantage over SafeGatherResult wrapper:
#   - Built-in ExceptionGroup correlation (PEP 654)
#   - ~10× fewer allocations (no .ok/.errors/.re_raised envelope)
#   - Structured: results dict is in scope after TaskGroup exits via except*
#   - except* simultaneously handles multiple exception types


async def parallel_taskgroup_star[T](
    coros: Sequence[Awaitable[T]],
    *,
    concurrency: int | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> ParallelResult:
    """PEP 654 except* variant of _parallel_taskgroup.

    Uses Python 3.11+ ``except*`` syntax for precise exception routing.
    This eliminates the manual BaseExceptionGroup iteration overhead —
    the C-level VM matches exceptions to except* handlers directly.

    Args:
        coros:       List of awaitables to run concurrently.
        concurrency: Max simultaneous tasks. None = unbounded.
        ctx:         Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        ParallelResult with .ok (successes), .errors (routed exceptions).

    Raises:
        BaseExceptionGroup: when any task fails with a non-Exception BaseException
                            (KeyboardInterrupt, SystemExit) — these are never silenced.
    """
    _log = logger_instance or logger
    _SENTINEL = object()  # distinguishes absent result from None return value
    results: list[Any] = [_SENTINEL] * len(coros)
    errors: list[BaseException] = []

    sem: asyncio.Semaphore | None = None
    if concurrency is not None:
        sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, coro: Awaitable[T]) -> None:
        if sem is not None:
            async with sem:
                results[idx] = await coro
        else:
            results[idx] = await coro

    try:
        async with asyncio.TaskGroup() as tg:
            for idx, coro in enumerate(coros):
                tg.create_task(_run(idx, coro), name=f"ptgs[{idx}]", eager_start=True)
    except* asyncio.CancelledError as _eg:
        # [I6] CancelledError — identity preserved, re-raised immediately
        _log.debug(
            "[GHOST] parallel_taskgroup_star CancelledError%s",
            (" " + ctx) if ctx else "",
        )
        raise
    except* (RuntimeError, OSError) as eg:
        # Application-level exceptions — route to .errors, never silently swallowed
        for exc in eg.exceptions:
            _log.debug(
                "[GHOST] parallel_taskgroup_star%s %s: %s",
                (" " + ctx) if ctx else "",
                type(exc).__name__,
                exc,
            )
            errors.append(exc)
    except* BaseException as eg:
        # Non-Exception BaseException (KeyboardInterrupt, SystemExit) — [I7] never silenced
        _log.debug(
            "[GHOST] parallel_taskgroup_star BaseException%s: %s",
            (" " + ctx) if ctx else "",
            type(eg.exceptions[0]).__name__ if eg.exceptions else "unknown",
        )
        raise

    # Filter: exclude sentinel (absent) and BaseException (error escapees).
    # Sentinel区分 "coro never wrote to results[i]" vs "coro returned None".
    ok_results = [r for r in results if r is not _SENTINEL and not isinstance(r, BaseException)]
    return ParallelResult(ok=ok_results, errors=errors, re_raised=None)


ConcurrencyBudgetResolver = Callable[[], Awaitable[int]]


async def parallel[T](
    coros: Sequence[Awaitable[T]],
    *,
    policy: ExceptionPolicy = "collect",
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    timeout: float | None = None,
    taskgroup: bool = False,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> ParallelResult:
    """ISSUE-006: Single canonical parallel runner with named exception policies.

    Unified replacement for bounded_gather, gather_taskgroup, safe_gather_ok,
    safe_gather_fire_and_forget, and safe_gather_strict.

    Exception policies:
        "raise"   — after all complete, raise BaseExceptionGroup if any errors.
                    Single BaseException → bare raise. Same as safe_gather_strict.
        "first"   — raise the first non-CancelledError BaseException immediately
                    (fail-fast). Uses gather semantics, not TaskGroup.
        "collect" — return (ok_results, errors) tuple. All run to completion.
                    Same as bounded_gather / gather_taskgroup. DEFAULT.
        "log"     — filter exceptions silently, return only successes.
                    Same as safe_gather_ok / safe_gather_fire_and_forget.

    Concurrency: pass ``concurrency=N`` to cap simultaneous tasks (semaphore).
                 None (default) = unbounded.
                 F1 FIX: pass a callable ``concurrency=lambda: expr`` that returns
                 an int at call time — evaluated immediately before the semaphore
                 is created, so it picks up the current UMA state each call.
                 Example: ``concurrency=lambda: concurrency_budget(ConcurrencyCategory.PASTE_SCRAPE)``

    Backend: pass ``taskgroup=True`` to use asyncio.TaskGroup (Python 3.11+).
             Default (False) uses asyncio.gather with semaphore wrapper.

    Timeout: optional total timeout in seconds. Uses asyncio.timeout (3.11+).
             None = no timeout.

    Args:
        coros:       List of awaitables to run concurrently.
        policy:      Exception handling policy: "raise" | "first" | "collect" | "log".
        concurrency: Max simultaneous tasks. None = unbounded.
        timeout:     Total timeout in seconds. None = no timeout.
        taskgroup:   Use TaskGroup instead of gather (Python 3.11+).
        ctx:         Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        ParallelResult with .ok (successes), .errors (exceptions, policy="collect"),
        and .re_raised (BaseException re-raised per I6/I7).

    Raises (policy="raise" / "first"):
        BaseExceptionGroup: aggregated exceptions when policy="raise" and errors exist.
        asyncio.CancelledError: when the caller's scope is cancelled.
        BaseException: for non-Exception BaseException (KeyboardInterrupt, SystemExit).

    Example:
        # bounded gather (concurrency=5, taskgroup=False, policy="collect"):
        result = await parallel(
            [fetch(url) for url in urls],
            concurrency=5,
            policy="collect",
            ctx="fetch.urls",
        )
        for item in result.ok:
            ...

        # fail-fast race (concurrency=None, taskgroup=False, policy="first"):
        result = await parallel(
            [try_curl(), try_aiohttp()],
            policy="first",
            ctx="http.fallback",
        )

        # fire-and-forget (concurrency=10, taskgroup=True, policy="log"):
        await parallel(
            [log_event(e) for e in events],
            concurrency=10,
            taskgroup=True,
            policy="log",
            ctx="audit.events",
        )
    """
    _log = logger_instance or logger
    if not coros:
        return ParallelResult(ok=[], errors=[], re_raised=None)

    # F1 FIX: resolve callable concurrency BEFORE the semaphore is created.
    # Callable form (lambda: concurrency_budget(Category)) is re-evaluated each
    # call so it picks up the current UMA state dynamically.
    # Note: the callable itself is NOT awaited — it returns an awaitable (coroutine)
    # from an async function, so we await the RESULT of calling it.
    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1

    # TaskGroup path (Python 3.11+): structured concurrency with sibling cancellation
    if taskgroup:
        return await _parallel_taskgroup(
            coros,
            concurrency=concurrency,
            policy=policy,
            ctx=ctx,
            logger_instance=_log,
        )

    # Gather path: all coroutines run to completion, results collected
    # Wrap coros with semaphore if concurrency is bounded
    if concurrency is not None:
        sem = asyncio.Semaphore(concurrency)

        async def _wrap(coro: Awaitable[T]) -> T:
            async with sem:
                return await coro

        wrapped = [_wrap(c) for c in coros]
    else:
        wrapped = coros

    # Optional timeout wrapper
    if timeout is not None and timeout > 0:

        async def _with_timeout() -> list[Any]:
            async with asyncio.timeout(timeout):
                return await asyncio.gather(*wrapped, return_exceptions=True)

        raw: list[Any] = await _with_timeout()
    else:
        raw = await asyncio.gather(*wrapped, return_exceptions=True)

    # Classify: ok / errors / re_raise (I6/I7/I8)
    _CE = asyncio.CancelledError
    _BaseE = BaseException
    _Ex = Exception

    ok: list[Any] = []
    errors: list[BaseException] = []
    re_raise: BaseException | None = None

    for i, item in enumerate(raw):
        t = type(item)
        if t is _CE:  # CancelledError — identity check, no MRO
            _log.debug("[GHOST] parallel CancelledError[%d]%s", i, (" " + ctx) if ctx else "")
            if re_raise is None:
                re_raise = cast(BaseException, item)
        elif isinstance(item, _Ex):  # regular Exception
            _log.debug(
                "[GHOST] parallel exception[%d]%s: %s: %s",
                i,
                (" " + ctx) if ctx else "",
                type(item).__name__,
                item,
            )
            errors.append(item)
        elif isinstance(item, _BaseE):  # BaseException but not Exception
            _log.debug(
                "[GHOST] parallel BaseException[%d]%s: %s",
                i,
                (" " + ctx) if ctx else "",
                type(item).__name__,
            )
            if re_raise is None:
                re_raise = cast(BaseException, item)
        else:
            ok.append(item)

    # Re-raise CancelledError / non-Exception BaseException immediately (I6/I7)
    if re_raise is not None:
        raise re_raise

    # Dispatch by policy
    match policy:
        case "raise":
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup(f"parallel{' ' + ctx if ctx else ''}", errors)
            return ParallelResult(ok=ok, errors=[], re_raised=None)

        case "first":
            if errors:
                raise errors[0]
            return ParallelResult(ok=ok, errors=[], re_raised=None)

        case "collect":
            return ParallelResult(ok=ok, errors=errors, re_raised=None)

        case "log":
            # Silently drop errors — already logged above
            if errors:
                sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
                suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
                _log.debug(
                    f"[GHOST] parallel{' ' + ctx if ctx else ''} "
                    f"dropped {len(errors)} exceptions "
                    f"(sample: {sample_preview}"
                    f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
                )
            return ParallelResult(ok=ok, errors=[], re_raised=None)


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

    DEPRECATED: Use ``parallel(coros, policy="collect")`` instead.
    """
    _log = logger_instance or logger
    if not coros:
        return SafeGatherResult(ok=[], errors=[])

    # I6/I7/I8 boundary: always return_exceptions=True at the gather level.
    # Classification delegated to _classify_gathered (ISSUE-15: single source of truth).
    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    # [I6]/[I7] — re_raise carries CancelledError or non-Exception BaseException.
    # Re-raise immediately so caller's finally blocks run.
    if re_raise is not None:
        raise re_raise

    return SafeGatherResult(ok=list(ok), errors=list(errors))


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

    DEPRECATED: Use ``parallel(coros, policy="log")`` instead.
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

    DEPRECATED: Use ``parallel(coros, policy="log")`` instead.
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

    DEPRECATED: Use ``parallel(coros, concurrency=N, policy="collect")`` instead.
    This function is a thin wrapper maintained for backward compatibility.

    Semantically equivalent to ``parallel(coros, concurrency=concurrency, policy="collect")``.

    Args:
        coros: List of awaitables to gather concurrently.
        concurrency: Maximum concurrent tasks (default 5). Must be ≥ 1.
        ctx: Context label for log messages (e.g. "discovery.sources").
        logger_instance: Optional logger override.

    Returns:
        Tuple of (ok_results, error_exceptions).
        - ok_results: successful results, in original order
        - error_exceptions: Exception instances (non-fatal; logged at DEBUG)

    Raises:
        asyncio.CancelledError: if the caller's task is cancelled.
        BaseException (not Exception): KeyboardInterrupt, SystemExit, etc.
    """
    import warnings
    warnings.warn(
        "bounded_gather is deprecated. Use parallel(coros, concurrency=N, policy='collect') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    result = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        ctx=ctx,
        logger_instance=logger_instance,
    )
    return result.ok, list(result.errors)  # type: ignore[return-value]


# ISSUE-005: bounded_parallel_map — parallel map with bounded concurrency
#
# Problem: sequential `for x in xs: await f(x)` wastes N×latency serial.
# bounded_gather takes a list of coros (pre-built), but callers must write:
#   [coro_fn(x) for x in items]
#
# bounded_parallel_map takes (items, coro_fn) directly — cleaner API:
#   results = await bounded_parallel_map(emails[:3], hunter.check_target, concurrency=3)
#
# Invariants:
#   [BPM1] Concurrency capped by semaphore — M1 8GB safe
#   [BPM2] Results ordered by input order (ordered=True default)
#   [BPM3] Exceptions → None in output (fail-soft, caller decides)
#   [BPM4] CancelledError / BaseException → re-raised (GHOST I6/I7)


ConcurrencyBudgetResolver = Callable[[], Awaitable[int]]


async def bounded_parallel_map[T, R](
    items: list[T],
    coro_fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    ordered: bool = True,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[R | None]:
    """ISSUE-005: Parallel async map with bounded concurrency.

    Transforms a list of items concurrently with explicit concurrency cap.
    Clean replacement for sequential `for x in xs: await f(x)`.

    F1 FIX: concurrency accepts a callable (lambda: concurrency_budget(Category))
    for dynamic UMA-aware resolution at call time.

    Args:
        items: List of items to process.
        coro_fn: Async function to apply to each item, e.g. `lambda e: check(e)`.
        concurrency: Max simultaneous tasks. int (default 10), callable, or None.
            - int: explicit limit
            - callable: resolved at call time via await (supports lambda patterns)
            - None: resolved via concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL)
        ordered: If True (default), results in input order. False = faster (no sort).
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        List of results in input order (or completion order if ordered=False).
        Items that raised exceptions are None in the output (fail-soft).

    Raises:
        asyncio.CancelledError: if the caller's task is cancelled.
        BaseException (not Exception): KeyboardInterrupt, SystemExit, etc.

    Example:
        # OLD (hardcoded):
        raw = await bounded_parallel_map(emails[:3], ..., concurrency=3)

        # NEW (UMA-aware dynamic):
        from core.concurrency_registry import concurrency_budget, ConcurrencyCategory
        raw = await bounded_parallel_map(
            emails[:3],
            lambda e: hunter.check_target(e, 'email'),
            concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL),
        )
        results = [r for r in raw if r is not None]
    """
    _log = logger_instance or logger
    if not items:
        return []

    # F1 FIX: resolve callable concurrency before semaphore creation.
    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1
    else:
        # Default: SCRAPE_GENERAL category for general scraping operations
        from core.concurrency_registry import concurrency_budget, ConcurrencyCategory
        concurrency = await concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL)

    sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, item: T) -> tuple[int, R | BaseException]:
        async with sem:
            try:
                return idx, await coro_fn(item)
            except BaseException as e:
                _log.debug(
                    f"[GHOST] bounded_parallel_map{' ' + ctx if ctx else ''} "
                    f"item[{idx}] raised {type(e).__name__}: {e}"
                )
                return idx, e  # noqa: RET504

    # E4: use safe_create_task for OTel trace context propagation on all child tasks
    from utils.async_helpers import safe_create_task  # noqa: F811
    tasks = [safe_create_task(_run(i, item)) for i, item in enumerate(items)]
    raw = cast("list[tuple[int, R | BaseException]]", await asyncio.gather(*tasks, return_exceptions=True))

    # Check for CancelledError / BaseException — re-raise per GHOST I6/I7
    for item in raw:
        if isinstance(item, asyncio.CancelledError):
            _log.debug("[GHOST] bounded_parallel_map CancelledError — re-raising")
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            _log.debug(f"[GHOST] bounded_parallel_map {type(item).__name__} — re-raising")
            raise item

    if ordered:
        raw.sort(key=lambda x: x[0])

    # Filter exceptions: _run returns (idx, result) where result may be Exception.
    # The for-loop above already re-raised any BaseException (CancelledError, etc.).
    # Here we filter regular Exception instances to None per BPM3 fail-soft contract.
    filtered: list[R | None] = []
    for _, result in raw:
        filtered.append(None if isinstance(result, Exception) else cast(R, result))
    return filtered


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

    DEPRECATED: Use ``parallel(coros, policy="collect", return_exceptions=True)`` instead.
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

    DEPRECATED: Use ``parallel(coros, taskgroup=True, policy="collect")`` instead.
    This function is maintained for its unique partial-result preservation semantics.
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
    label: str = "",  # reserved for future log context
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
    if label:
        _log.debug("[race_first_success] starting%s with %d candidates", f"({label})" if label else "", len(coros))
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

    DEPRECATED: Use ``parallel(coros, concurrency=N, taskgroup=True, policy="collect")`` instead.
    This function is a thin wrapper maintained for backward compatibility.

    Drop-in replacement for bounded_gather with TaskGroup (PEP 654, 3.11+).
    """
    import warnings
    warnings.warn(
        "gather_taskgroup is deprecated. Use parallel(coros, concurrency=N, taskgroup=True, policy='collect') instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    result = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        taskgroup=True,
        ctx=ctx,
        logger_instance=logger_instance,
    )
    return result.ok, list(result.errors)  # type: ignore[return-value]


async def chunked_taskgroup[T, R](
    items: list[T],
    coro_fn: Callable[[T], Awaitable[R]],
    *,
    batch_size: int = 20,
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[R]:
    """ISSUE-006: Memory-safe batch processing via TaskGroup.

    Processes `items` in bounded batches using asyncio.TaskGroup. Each batch
    runs concurrently up to `concurrency` limit. Results are yielded incrementally
    so the caller can process results while the next batch is still loading.

    M1 8GB safe: at most `batch_size` items are in-flight at once.
    Compared to gather_taskgroup/bounded_gather which hold all results in memory.

    F1 FIX: concurrency accepts callable (lambda: concurrency_budget(Category))
    for dynamic UMA-aware resolution at call time.

    Args:
        items: List of items to process.
        coro_fn: Async function to apply to each item, e.g. `lambda url: fetch(url)`.
        batch_size: Items per batch (default 20). M1 8GB: 20 × ~50MB = 1GB/batch.
        concurrency: Max simultaneous tasks per batch.
            - int: explicit limit (default 10)
            - callable: resolved at call time via await
            - None: resolved via concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL)
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

    # F1 FIX: resolve callable concurrency before semaphore creation.
    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1
    else:
        from core.concurrency_registry import concurrency_budget, ConcurrencyCategory
        concurrency = await concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL)

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

    class _BatchCapture:
        """Closure-free result capture for a single batch.

        ISSUE-008: Replaces nested _run_with_capture + nonlocal closure cell.
        Using __slots__ eliminates per-task closure allocation and reduces GC pressure
        in tight loops (batch_size=20 × thousands of batches = significant savings).

        __slots__ ensures:
        - No __dict__ per instance (~48 bytes saved/instance)
        - No closure cell objects for nonlocal variables
        - Faster attribute access on M1 (direct offset indexing)
        """
        __slots__ = ("results",)

        def __init__(self, n: int) -> None:
            self.results: list[tuple[int, R | None] | None] = [None] * n

        async def __call__(self, local_idx: int, item: T) -> None:
            self.results[local_idx] = await _run(local_idx, item)

    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]
        capture = _BatchCapture(len(batch))

        try:
            async with asyncio.TaskGroup() as tg:
                for local_idx, item in enumerate(batch):
                    tg.create_task(capture(local_idx, item), name=f"chunk[{batch_start + local_idx}]")
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
                # Regular Exception from TaskGroup itself (should not happen since _run wraps in try/except)
                # — log and continue to collect batch results below
        except asyncio.CancelledError:
            _log.debug("[GHOST] chunked_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
            raise

        # Collect non-None results from this batch
        # _run returns tuple[int, R | None] — BaseException is never stored here
        for r in capture.results:
            if r is not None:
                # val may still be None from _run exception path
                _, val = r
                if val is not None:
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

    __slots__ = ("_max_hosts", "_per_host_limit", "_gates", "_stats")

    def __init__(self, max_hosts: int = 512, per_host_limit: int = 4) -> None:
        self._max_hosts = max_hosts
        self._per_host_limit = per_host_limit
        self._gates: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        self._stats: dict[str, int] = {"evicted": 0, "hits": 0, "misses": 0}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _evict_idle(self) -> None:
        """Evict LRU hosts when over capacity (called lazily on miss).

        Uses OrderedDict LRU ordering: move_to_end() marks recent access,
        popitem(last=False) evicts oldest — both O(1) C-implemented.
        """
        if len(self._gates) < self._max_hosts:
            return
        # Evict exactly the overage — OrderedDict maintains LRU order
        # via move_to_end() in acquire(), so oldest = first = popitem(last=False)
        evict_count = max(1, len(self._gates) - self._max_hosts)
        for _ in range(evict_count):
            self._gates.popitem(last=False)  # O(1) LRU evict
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
        if host in self._gates:
            sem = self._gates[host]
            self._gates.move_to_end(host)  # O(1) LRU: mark as most-recently-used
            self._stats["hits"] += 1
            op_id = "hit"
        else:
            self._evict_idle()
            sem = asyncio.Semaphore(self._per_host_limit)
            self._gates[host] = sem
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


# ---------------------------------------------------------------------------
# ISSUE-04: parallel_close — parallel resource teardown helper
# ---------------------------------------------------------------------------


async def _safe_aclose(
    resource: Any,
    *,
    ctx: str,
    logger_instance: logging.Logger,
) -> Exception | None:
    """Close a single resource (aclose/close), returning None on success or the exception."""
    try:
        # Try aclose() first (async resources), then close() (sync resources)
        close_fn = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        if close_fn is None:
            logger_instance.debug("[%s] resource %s has no close/aclose method", ctx, type(resource).__name__)
            return None
        result = close_fn()
        if asyncio.iscoroutine(result):
            await result
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger_instance.debug("[%s] failed to close %s: %s", ctx, type(resource).__name__, e)
        return e


async def parallel_close(
    resources: list[Any],
    *,
    concurrency: int = 4,
    ctx: str = "parallel_close",
    logger_instance: logging.Logger | None = None,
) -> list[Exception | None]:
    """ISSUE-04: Close multiple resources in parallel with bounded concurrency.

    Fail-safe: exceptions are collected and returned, never propagated.
    CancelledError is re-raised (teardown must not swallow cancellation).

    Use for independent resources that can be closed concurrently (HTTP clients,
    transport layers, session pools). For dependent resources (LIFO ordering
    required), close them sequentially before calling this for the rest.

    Args:
        resources: List of objects with .aclose() or .close() method.
        concurrency: Max simultaneous close operations (default 4, M1 8GB friendly).
        ctx: Context label for log messages (e.g. "teardown.transports").
        logger_instance: Optional logger override.

    Returns:
        List of Exception | None per resource (None = success, Exception = failure).

    Example:
        # Close HTTP clients in parallel (independent):
        errors = await parallel_close(
            [httpx_client, curl_cffi_client, public_fetcher, aiohttp_session],
            concurrency=4,
            ctx="teardown.transports",
        )
        failed = [e for e in errors if e is not None]
        if failed:
            logger.debug("teardown: %d/%d transport close failures", len(failed), len(errors))
    """
    _log = logger_instance or logger
    if not resources:
        return []

    coros: list[Awaitable[Exception | None]] = [
        _safe_aclose(r, ctx=ctx, logger_instance=_log)
        for r in resources
    ]

    result = await parallel(
        coros,
        policy="collect",  # Collect exceptions, never propagate
        concurrency=concurrency,
        taskgroup=True,
        ctx=ctx,
        logger_instance=_log,
    )

    return result.ok  # ok contains the return values of _safe_aclose (Exception | None)


async def _safe_close_async(
    close_fn: Callable[[], Awaitable[Any]],
    name: str,
    *,
    ctx: str,
    logger_instance: logging.Logger,
) -> tuple[str, Exception | None]:
    """Close a resource via async callable, returning (name, exception)."""
    try:
        await close_fn()
        return (name, None)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger_instance.debug("[%s] failed to close %s: %s", ctx, name, e)
        return (name, e)


async def parallel_close_async(
    close_funcs: list[tuple[str, Callable[[], Awaitable[Any]]]],
    *,
    concurrency: int = 4,
    ctx: str = "parallel_close_async",
    logger_instance: logging.Logger | None = None,
) -> dict[str, Exception | None]:
    """ISSUE-04: Close multiple async resources in parallel via async callables.

    Unlike ``parallel_close`` which works on objects with .aclose()/.close() methods,
    this variant accepts named async callables — useful for module-level close functions
    like ``close_httpx_client_async()`` that don't belong to an object.

    Fail-safe: exceptions are collected into the result dict, never propagated.
    CancelledError is re-raised (teardown must not swallow cancellation).

    Args:
        close_funcs: List of (name, async_close_fn) tuples. Each fn must be
                     a zero-argument async callable (e.g. ``close_httpx_client_async``).
        concurrency: Max simultaneous close operations (default 4, M1 8GB friendly).
        ctx: Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        Dict mapping name → None (success) or Exception (failure).

    Example:
        errors = await parallel_close_async([
            ("httpx", close_httpx_client_async),
            ("curl_cffi", close_curl_cffi_sessions_async),
            ("public_fetcher", close_public_fetcher_sessions_async),
            ("aiohttp", close_aiohttp_session_async),
        ], concurrency=4, ctx="teardown.transports")
        failed = [name for name, e in errors.items() if e is not None]
        if failed:
            logger.debug("teardown: %d transport close failures: %s", len(failed), failed)
    """
    _log = logger_instance or logger
    if not close_funcs:
        return {}

    coros: list[Awaitable[tuple[str, Exception | None]]] = [
        _safe_close_async(fn, name, ctx=ctx, logger_instance=_log)
        for name, fn in close_funcs
    ]

    result = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        taskgroup=True,
        ctx=ctx,
        logger_instance=_log,
    )

    # Build result dict preserving names
    out: dict[str, Exception | None] = {}
    for item in result.ok:
        if isinstance(item, tuple) and len(item) == 2:
            out[item[0]] = item[1]
        else:
            out[str(item)] = None  # fallback
    return out


async def retry_backoff_async(
    coro_fn: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 0.5,
    *,
    max_delay: float = 30.0,
    jitter: bool = True,
    cancel_is_retriable: bool = False,
) -> T:
    """E1 fix: Retry with exponential backoff and optional jitter.

    Replaces manual ``for _retry_i in range(max_retries):
        await asyncio.sleep(delay * 2**_retry_i)`` patterns.

    Properly propagates CancelledError — the backoff sleep does NOT swallow
    cancellation. This is critical for graceful SIGINT handling where retry
    loops must be interruptible mid-backoff.

    Args:
        coro_fn: Coroutine to execute (callable, not pre-awaited).
        max_retries: Maximum retry attempts (default 3).
        base_delay: Initial delay in seconds (default 0.5).
        max_delay: Cap on delay growth (default 30s).
        jitter: Add ±25% decorrelated jitter (default True, recommended).
        cancel_is_retriable: If True, CancelledError triggers retry instead
            of propagation. Default False (CancelledError propagates — correct
            for all graceful shutdown paths).

    Returns:
        The return value of coro_fn on success.

    Raises:
        CancelledError: Propagates immediately (cancel_is_retriable=False).
        Exception: Re-raised after all retries exhausted.

    Example:
        async def fetch_with_retry(url: str) -> str:
            return await retry_backoff_async(
                lambda: _fetch(url),
                max_retries=3,
                base_delay=0.5,
            )
    """
    import random as _random

    attempt = 0

    while True:
        try:
            return await coro_fn()
        except asyncio.CancelledError:
            if not cancel_is_retriable:
                raise  # Propagate — correct for graceful shutdown
            # cancel_is_retriable=True: fall through to retry
        except Exception as _exc:  # noqa: BLE001
            if attempt >= max_retries:
                raise

        attempt += 1
        # Compute delay with exponential growth
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

        if jitter:
            # Decorrelated jitter: ±25% of current delay, seeded per attempt
            delay *= (0.75 + _random.random() * 0.5)

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # We were cancelled mid-backoff — propagate the cancellation,
            # NOT the last_exception. This is the key invariant: cancellation
            # always wins over retry exhaustion.
            raise


# Storage for deprecated functions — accessed only via __getattr__ after removal from globals.
_DEPRECATED_STORAGE: dict[str, Any] = {}

# Move deprecated functions to storage and remove from globals so __getattr__ intercepts.
# __getattr__ is called ONLY when name is NOT in module.__dict__.
for _dep_name in _DEPRECATED_NAMES:
    _DEPRECATED_STORAGE[_dep_name] = globals().pop(_dep_name)


def __getattr__(name: str) -> Any:
    if name in _DEPRECATED_NAMES:
        replacement = _DEPRECATED_NAMES[name]
        warnings.warn(
            f"{name} is deprecated. Use {replacement} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _DEPRECATED_STORAGE[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
