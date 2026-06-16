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
import inspect
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
    "safe_gather_dropin",
    "safe_gather_fire_and_forget",
    "safe_gather_strict",
    "safe_create_task",
    "SafeGatherResult",
    "_BoundedExceptionLog",
]

logger = logging.getLogger(__name__)

# Eager task creation: Python 3.12+ stdlib accepts `eager_start=True` on
# loop.create_task(), running the coroutine synchronously up to its
# first await. With uvloop on M1, this eliminates ~15-30μs scheduling
# overhead per task in scatter/gather patterns. Degrades gracefully on
# <3.12 (no eager_start kwarg passed).
#
# IMPORTANT: uvloop 0.22.x (current) does NOT implement eager_start in its
# C-level create_task — signature is (coro, *, name=None, context=None).
# A naive `sys.version_info >= (3, 12)` check passes on Python 3.14+uvloop
# but breaks every safe_gather_dropin / safe_gather_strict call. Detect
# at import-time by probing a fresh event loop's create_task signature.
def _detect_eager_start_support() -> bool:
    """True only if Python 3.12+ AND the loop's create_task accepts eager_start.

    uvloop and any custom loop implementation must opt-in via signature
    inspection. Defensive: a probing loop avoids breaking on platforms where
    creating an event loop in a non-main thread or inside an import-time
    call would be problematic.
    """
    probe_loop = None
    try:
        probe_loop = asyncio.new_event_loop()
        sig = inspect.signature(probe_loop.create_task)
        return "eager_start" in sig.parameters
    except (OSError, ValueError, TypeError):
        return False
    finally:
        if probe_loop is not None:
            try:
                probe_loop.close()
            except Exception:
                pass


_EAGER_START_SUPPORTED = _detect_eager_start_support()


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


from collections.abc import Awaitable  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import TypeVar  # noqa: E402

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
    ok: list[Any] = []
    errors: list[Exception] = []
    re_raise: asyncio.CancelledError | BaseException | None = None

    for i, item in enumerate(raw):
        if isinstance(item, asyncio.CancelledError):
            _log.debug(f"[GHOST] gather CancelledError[{i}]{' ' + label if label else ''} — re-raising")
            if re_raise is None:
                re_raise = item
            continue
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            _log.debug(f"[GHOST] gather BaseException[{i}]{' ' + label if label else ''}: "
                       f"{type(item).__name__} — re-raising")
            if re_raise is None:
                re_raise = item
            continue
        if isinstance(item, Exception):
            _log.debug(f"[GHOST] gather exception[{i}]{' ' + label if label else ''}: "
                       f"{type(item).__name__}: {item}")
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
        _log.debug(f"[GHOST] safe_gather_faf re-raise suppressed{' ' + label if label else ''}: "
                   f"{type(re_raise).__name__}")

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

    # Pre-create tasks with eager_start (Python 3.12+) for ~15-30μs scheduling
    # win per task. asyncio.gather() consumes pre-existing Task instances
    # directly (no re-wrap), preserving gather(return_exceptions=True) semantics.
    # Sprint F271F fix: If a caller already passed a finished Task (e.g. acquisition
    # lanes pre-wrap with asyncio.create_task at runtime/acquisition_strategy.py:4209),
    # we must NOT call loop.create_task(Task) -- that raises
    # "TypeError: a coroutine was expected, got Task" and aborts the lane gather.
    loop = asyncio.get_running_loop()
    tasks: list[Any] = []
    for c in coros:
        if isinstance(c, (asyncio.Task, asyncio.Future)):
            # Already a Task/Future: pass through unchanged. asyncio.gather handles them.
            tasks.append(c)
            continue
        # Coroutine / awaitable-with-__await__ / plain value → wrap + create.
        # eager_start kwarg is Python 3.12+ only; guarded by _EAGER_START_SUPPORTED
        # to keep py3.10/3.11 import-time + runtime compatibility.
        if _EAGER_START_SUPPORTED:
            # ty: `_wrap_awaitable` is typed `Awaitable[Any]`, but `create_task`
            # requires `Coroutine[Any, Any, Unknown]`. Runtime works because
            # `_wrap_awaitable` either returns the value (if it has __await__)
            # or a fresh coroutine from `_lift()` — both satisfy create_task
            # at runtime. Suppress the static narrowing mismatch.
            tasks.append(loop.create_task(_wrap_awaitable(c), eager_start=True))  # type: ignore[ty:invalid-argument-type]
        else:
            tasks.append(loop.create_task(_wrap_awaitable(c)))  # type: ignore[ty:invalid-argument-type]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
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
                _log.debug(f"[GHOST] safe_gather_shielded CancelledError{'_' + label if label else ''}")
                raise exc
            if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                _log.debug(f"[GHOST] safe_gather_shielded BaseException{'_' + label if label else ''}: {type(exc).__name__}")
                raise exc
            errors.append(exc)
        # Collect any non-cancelled results from the partial run
        ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
        return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=eg)
    except asyncio.CancelledError:
        _log.debug(f"[GHOST] safe_gather_shielded CancelledError{'_' + label if label else ''}")
        raise
    except BaseException as exc:
        if isinstance(exc, Exception):
            errors.append(exc)
            ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
            return SafeGatherShieldedResult(ok=ok_results, errors=errors, re_raised=None)
        _log.debug(f"[GHOST] safe_gather_shielded BaseException{'_' + label if label else ''}: {type(exc).__name__}")
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return SafeGatherShieldedResult(ok=ok_results, errors=[], re_raised=None)
