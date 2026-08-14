# hledac/universal/utils/async/_parallel.py
# Parallel execution primitives
#
# Provides:
# - parallel(), parallel_ok(): unified parallel runner with policy-based error handling
# - bounded_parallel_map(): parallel map with bounded concurrency
# - chunked_taskgroup(): memory-safe batch processing via TaskGroup
# - safe_gather, safe_gather_ok, safe_gather_strict, safe_gather_fire_and_forget
# - try_group, parallel_taskgroup_star: TaskGroup-based execution
# - race_first_success(): race coroutines to first success
# - Result DTOs: ParallelResult, SafeGatherResult, RaceFirstSuccessResult
#
# Exception policies:
#   "raise"   — re-raise first BaseException (all-complete or single failure)
#   "first"   — raise first non-cancel BaseException (fail-fast)
#   "collect" — return (ok, errors) tuple (all-complete, partial failure)
#   "log"     — filter exceptions, return only successes (fail-soft)
"""
Parallel execution primitives

Provides:
- parallel(), parallel_ok(): unified parallel runner with policy-based error handling
- bounded_parallel_map(): parallel map with bounded concurrency
- chunked_taskgroup(): memory-safe batch processing via TaskGroup
- safe_gather, safe_gather_ok, safe_gather_strict, safe_gather_fire_and_forget
- try_group, parallel_taskgroup_star: TaskGroup-based execution
- race_first_success(): race coroutines to first success
- Result DTOs: ParallelResult, SafeGatherResult, RaceFirstSuccessResult

Exception policies:
  "raise"   — re-raise first BaseException (all-complete or single failure)
  "first"   — raise first non-cancel BaseException (fail-fast)
  "collect" — return (ok, errors) tuple (all-complete, partial failure)
  "log"     — filter exceptions, return only successes (fail-soft)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast, overload

import msgspec

from hledac.universal.utils.asyncx._fault import _log_failure, silent_except

if TYPE_CHECKING:
    pass

T = TypeVar("T", default=Any)


logger = logging.getLogger(__name__)

# Sample cap: how many detailed exception entries to log before aggregating.
# 5 is the empirical sweet spot — small enough to avoid log spam, large enough
# to diagnose a non-trivial pattern. The "+N more" line always fires once.
_SAFE_GATHER_SAMPLE_CAP = 5

# Eager task creation: Python 3.12+ stdlib accepts `eager_start=True` on
# loop.create_task(), running the coroutine synchronously up to its
# first await. With uvloop on M1, this eliminates ~15-30μs scheduling
# overhead per task in scatter/gather patterns. Degrades gracefully on
# <3.12 (no eager_start kwarg passed).
import sys

_PY_312_PLUS: bool = sys.version_info >= (3, 12)

try:
    import uvloop  # noqa: F401

    _UVLOOP_INSTALLED: bool = True
except ImportError:
    _UVLOOP_INSTALLED = False

# uvloop 0.22.x C-level create_task does NOT accept eager_start kwarg.
_EAGER_START_SUPPORTED: bool = _PY_312_PLUS and not _UVLOOP_INSTALLED


# ---------------------------------------------------------------------------
# OTel context propagation
# ---------------------------------------------------------------------------

_OTelContextFn = Callable[[], dict[str, Any] | None]


def _noop_current_otel_context() -> dict[str, Any] | None:
    return None


try:
    from otel._instrumentation_asyncio import current_otel_context, create_task_with_context  # noqa: E402, F401

    _safe_task_factory: Callable[..., asyncio.Task[Any]] = create_task_with_context
except ImportError:
    current_otel_context: _OTelContextFn = _noop_current_otel_context

    def _safe_task_factory(coro: Any, *, name: str | None = None, eager_start: bool = True, **_: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro, name=name, eager_start=eager_start)


def safe_create_task(
    coro: Any,
    *,
    name: str | None = None,
    eager_start: bool = True,
    otel_trace: bool = True,
) -> asyncio.Task[Any]:
    """Defensive create_task wrapper with OTel context propagation.

    Args:
        coro: The coroutine to wrap in a task.
        name: Optional task name (passed to asyncio.create_task).
        eager_start: Run coroutine synchronously up to first await (3.12+).
        otel_trace: Capture and propagate OTel trace context (default True).

    Returns:
        asyncio.Task wrapping the coroutine.
    """
    task = _safe_task_factory(coro, name=name, eager_start=eager_start, otel_trace=otel_trace)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    """Done-callback: log unhandled exception from a background task."""
    try:
        task.result()
    except asyncio.CancelledError:  # noqa: BLE001
        pass
    except BaseException as e:
        try:
            _log_failure("background_task", e, is_escalated=True)
        except Exception:
            import sys

            try:
                sys.stderr.write(f"Unhandled task exception in background_task: {e!r}\n")
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Result DTOs
# ---------------------------------------------------------------------------

ExceptionPolicy = Literal["raise", "first", "collect", "log"]


class ParallelResult(msgspec.Struct, frozen=True, gc=False):
    """Canonical result of ``parallel()`` with policy-driven error routing.

    Attributes:
        ok:        Successful results, in original order (positional list).
                   When ``names`` is provided, also accessible via ``by_name[name]``.
        by_name:   Dict mapping task name -> result (populated when ``names`` is passed).
        errors:    Exception instances (only populated when policy="collect").
        re_raised: BaseException re-raised per I6/I7 (CancelledError, etc.).
    """

    ok: list[Any] = msgspec.field(default_factory=list)
    by_name: dict[str, Any] = msgspec.field(default_factory=dict)
    errors: list[BaseException] = msgspec.field(default_factory=list)
    re_raised: BaseException | None = None


class SafeGatherResult(msgspec.Struct, frozen=True, gc=False):
    """Result of `safe_gather` — msgspec.Struct for ~3× faster instantiation.

    Attributes:
        ok:       List of successful results (order preserved)
        errors:   List of exception instances (excluding BaseException)
        re_raised:BaseException instance if one was re-raised (caller should handle)
    """

    ok: list[Any] = msgspec.field(default_factory=list)
    errors: list[BaseException] = msgspec.field(default_factory=list)
    re_raised: BaseException | None = None


class RaceFirstSuccessResult(msgspec.Struct, frozen=True, gc=False):
    """Result of `race_first_success` — msgspec.Struct for ~3× faster instantiation."""

    result: Any = None
    winner_index: int = -1
    winner_label: str = ""
    errors: list[BaseException] = msgspec.field(default_factory=list)
    falsy_results: list[Any] = msgspec.field(default_factory=list)


class _BoundedExceptionLog(msgspec.Struct, frozen=True, gc=False):
    """Single bounded log line summarizing suppressed exceptions.

    Returned by safe_gather_fire_and_forget so callers can decide whether to
    escalate (e.g. for telemetry). msgspec.Struct keeps it cheap on M1 UMA.
    """

    sample: tuple[tuple[str, str, str], ...]  # ((type_name, str(exc), label), ...)
    suppressed_count: int  # how many additional exceptions


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

ConcurrencyBudgetResolver = Callable[[], Awaitable[int]]


def _wrap_awaitable(value: Any) -> Awaitable[Any]:
    """Wrap a plain value in a coroutine so asyncio.gather accepts it."""
    if hasattr(value, "__await__"):
        return value  # type: ignore[no-any-return]

    async def _lift() -> Any:
        return value

    return _lift()


def _build_by_name(results: list[Any], names: Sequence[str] | None) -> dict[str, Any]:
    """Build name->result dict from indexed results list, respecting original order."""
    if not names:
        return {}
    return {name: results[i] for i, name in enumerate(names) if i < len(results)}


def _classify_gathered(
    raw: list[Any],
    label: str,
    _log: logging.Logger,
) -> tuple[list[Any], list[BaseException], asyncio.CancelledError | BaseException | None]:
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

    _CE = asyncio.CancelledError  # noqa: N806
    _BaseE = BaseException  # noqa: N806
    _Ex = Exception  # noqa: N806

    # Fast path: all-success case (common)
    all_ok = True
    for item in raw:
        if isinstance(item, _BaseE):
            all_ok = False
            break

    if all_ok:
        return list(raw), [], None

    # Slow path: at least one exception present
    ok: list[Any] = []
    errors: list[BaseException] = []
    re_raise: asyncio.CancelledError | BaseException | None = None

    for i, item in enumerate(raw):
        t = type(item)
        if t is _CE:
            _log.debug("[GHOST] gather CancelledError[%d]%s — re-raising", i, (" " + label) if label else "")
            if re_raise is None:
                re_raise = item
            continue
        if isinstance(item, _Ex):
            _log.debug("[GHOST] gather exception[%d]%s: %s: %s", i, (" " + label) if label else "", t.__name__, item)
            errors.append(item)
            continue
        if isinstance(item, _BaseE):
            _log.debug("[GHOST] gather BaseException[%d]%s: %s — re-raising", i, (" " + label) if label else "", t.__name__)
            if re_raise is None:
                re_raise = item
            continue
        ok.append(item)

    return ok, errors, re_raise


def _check_gathered(
    results: list[Any],
    logger_instance: logging.Logger | None = None,
    ctx: str = "",
) -> tuple[list[Any], list[Any]]:
    """Process results from asyncio.gather(..., return_exceptions=True)."""
    n = len(results)
    if n == 0:
        return [], []

    _log = logger_instance or logger
    _CE = asyncio.CancelledError  # noqa: N806
    _BaseE = BaseException  # noqa: N806
    _Ex = Exception  # noqa: N806

    if n <= 8:
        for item in results:
            if isinstance(item, _BaseE):
                break
        else:
            return results, []
    else:
        probe = results[: min(8, n >> 2)]
        for item in probe:
            if isinstance(item, _BaseE):
                break
        else:
            for item in results:
                if isinstance(item, _BaseE):
                    break
            else:
                return results, []

    ok_results: list[Any] = []
    cancel_errors: list[BaseException] = []
    other_errors: list[BaseException] = []
    for item in results:
        t = type(item)
        if t is _CE:
            cancel_errors.append(item)
        elif isinstance(item, _Ex):
            other_errors.append(item)
        elif isinstance(item, _BaseE):
            cancel_errors.append(item)
        else:
            ok_results.append(item)

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


def _apply_policy(
    ok_results: list[Any],
    errors: list[BaseException],
    policy: ExceptionPolicy,
    ctx: str,
    logger_instance: logging.Logger,
    results: list[Any],
    names: Sequence[str] | None = None,
) -> ParallelResult:
    """Apply exception policy and return ParallelResult, or raise for raise/first policies."""
    by_name = _build_by_name(results, names) if names else {}

    match policy:
        case "raise":
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup(f"parallel(taskgroup){' ' + ctx if ctx else ''}", errors)
            return ParallelResult(ok=ok_results, by_name=by_name, errors=[], re_raised=None)
        case "first":
            if errors:
                raise errors[0]
            return ParallelResult(ok=ok_results, by_name=by_name, errors=[], re_raised=None)
        case "collect":
            return ParallelResult(ok=ok_results, by_name=by_name, errors=errors, re_raised=None)
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
            return ParallelResult(ok=ok_results, by_name=by_name, errors=[], re_raised=None)


# ---------------------------------------------------------------------------
# TaskGroup helpers
# ---------------------------------------------------------------------------

async def _parallel_taskgroup[T](
    coros: Sequence[Awaitable[T]],
    *,
    concurrency: int | None,
    policy: ExceptionPolicy,
    ctx: str,
    logger_instance: logging.Logger,
    names: Sequence[str] | None = None,
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
                logger_instance.debug("[GHOST] parallel(taskgroup) CancelledError%s", (" " + ctx) if ctx else "")
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
        return _apply_policy(ok_results, errors, policy, ctx, logger_instance, results, names)
    except asyncio.CancelledError:
        logger_instance.debug("[GHOST] parallel(taskgroup) CancelledError%s", (" " + ctx) if ctx else "")
        raise

    ok_results = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return _apply_policy(ok_results, errors, policy, ctx, logger_instance, results, names)


# _classify_gathered is defined earlier at line ~231 (optimized version with fast path)

def _build_parallel_result(
    ok: list[Any],
    errors: list[BaseException],
    re_raise: BaseException | None,
    by_name: dict[str, Any],
    policy: str,
    ctx: str,
) -> ParallelResult | list[Any]:
    """
    Build result based on exception policy.
    Raises immediately if re_raise is set.
    """
    if re_raise is not None:
        raise re_raise

    match policy:
        case "raise":
            if errors:
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup(f"parallel{' ' + ctx if ctx else ''}", errors)
            return ParallelResult(ok=ok, by_name=by_name, errors=[], re_raised=None)

        case "first":
            if errors:
                raise errors[0]
            return ParallelResult(ok=ok, by_name=by_name, errors=[], re_raised=None)

        case "collect":
            return ParallelResult(ok=ok, by_name=by_name, errors=errors, re_raised=None)

        case "log":
            if errors:
                sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
                suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
                return ok

        case _:
            return ParallelResult(ok=ok, by_name=by_name, errors=errors, re_raised=None)


# ---------------------------------------------------------------------------
# parallel() — unified parallel runner
# ---------------------------------------------------------------------------

@overload
async def parallel[T](
    coros: Sequence[Awaitable[T]],
    *,
    policy: Literal["log"],
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    timeout: float | None = None,
    taskgroup: bool = False,
    names: Sequence[str] | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """Overload: policy='log' returns list[T] (only successes, exceptions logged)."""


@overload
async def parallel[T](
    coros: Sequence[Awaitable[T]],
    *,
    policy: ExceptionPolicy = "collect",
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    timeout: float | None = None,
    taskgroup: bool = False,
    names: Sequence[str] | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> ParallelResult:
    """Overload: policy='collect'/'raise'/'first' returns ParallelResult."""


# P4-5 FIX: Consolidated overloads. The implementation uses positional
# coros (Sequence), not variadic *coros. The original overloads with
# *coros were misleading and didn't match the implementation.
# Usage: parallel([coro1, coro2], policy="log") — list as single argument.


async def parallel[T](
    coros: Sequence[Awaitable[T]] | tuple[Awaitable[T], ...],
    *,
    policy: ExceptionPolicy = "collect",
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    timeout: float | None = None,
    taskgroup: bool = False,
    names: Sequence[str] | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> ParallelResult | list[T]:
    """Single canonical parallel runner with named exception policies.

    Exception policies:
        "raise"   — after all complete, raise BaseExceptionGroup if any errors.
        "first"   — raise the first non-CancelledError BaseException immediately
                    (fail-fast). Uses gather semantics, not TaskGroup.
        "collect" — return ParallelResult with .ok and .errors. All run to completion.
                    DEFAULT.
        "log"     — filter exceptions silently, return only successes.

    Concurrency: pass ``concurrency=N`` to cap simultaneous tasks (semaphore).
    Backend: pass ``taskgroup=True`` to use asyncio.TaskGroup (Python 3.11+).
    Timeout: optional total timeout in seconds. Uses asyncio.timeout (3.11+).

    Args:
        coros:       List of awaitables to run concurrently.
        policy:      Exception handling policy: "raise" | "first" | "collect" | "log".
        concurrency: Max simultaneous tasks. None = unbounded.
        timeout:     Total timeout in seconds. None = no timeout.
        taskgroup:   Use TaskGroup instead of gather (Python 3.11+).
        names:       Optional sequence of names for dict-based result access.
        ctx:         Context label for log messages.
        logger_instance: Optional logger override.

    Returns:
        ParallelResult with .ok (successes), .errors (exceptions, policy="collect"),
        and .re_raised (BaseException re-raised per I6/I7).
    """
    _log = logger_instance or logger
    if not coros:
        return ParallelResult(ok=[], errors=[], re_raised=None)

    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1

    if taskgroup:
        return await _parallel_taskgroup(
            coros,
            concurrency=concurrency,
            policy=policy,
            ctx=ctx,
            logger_instance=_log,
            names=names,
        )

    if concurrency is not None:
        sem = asyncio.Semaphore(concurrency)

        async def _wrap(coro: Awaitable[T]) -> T:
            async with sem:
                return await coro

        wrapped = [_wrap(c) for c in coros]
    else:
        wrapped = coros

    if timeout is not None and timeout > 0:

        async def _with_timeout() -> list[Any]:
            async with asyncio.timeout(timeout):
                return await asyncio.gather(*wrapped, return_exceptions=True)

        raw: list[Any] = await _with_timeout()
    else:
        raw = await asyncio.gather(*wrapped, return_exceptions=True)

    # Classify results
    by_name = _build_by_name(raw, names)
    ok, errors, re_raise = _classify_gathered(raw, ctx, _log)

    return _build_parallel_result(ok, errors, re_raise, by_name, policy, ctx)





# ---------------------------------------------------------------------------
# parallel_ok — drop-in for safe_gather_ok
# ---------------------------------------------------------------------------

async def parallel_ok[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """Drop-in replacement for parallel_ok.

    Returns a plain list of successful results, in original order, with all
    Exception instances filtered out. CancelledError / non-Exception
    BaseException are re-raised per I6/I7 invariants.

    Equivalent to: ``parallel(*coros, policy="log", ctx=label)``
    """
    _log = logger_instance or logger
    if not coros:
        return []

    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    if errors:
        sample_preview = ", ".join(type(e).__name__ for e in errors[:_SAFE_GATHER_SAMPLE_CAP])
        suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
        _log.debug(
            f"[GHOST] parallel_ok{' ' + label if label else ''} "
            f"dropped {len(errors)} exceptions "
            f"(sample: {sample_preview}"
            f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
        )

    if re_raise is not None:
        raise re_raise

    return ok


# ---------------------------------------------------------------------------
# try_group — TaskGroup + except* for structured groups
# ---------------------------------------------------------------------------

async def try_group[*Ts](
    *coros: Awaitable[Ts],
    ctx: str = "",
) -> tuple[Ts, ...]:
    """asyncio.TaskGroup with except* exception routing.

    All coroutines run to completion (structured concurrency). If any raises
    an Exception, all exceptions are collected into a BaseExceptionGroup and
    raised. CancelledError is re-raised immediately.
    """
    results: list[Any] = [None] * len(coros)

    async def _run(idx: int, coro: Awaitable[Any]) -> None:
        results[idx] = await coro

    try:
        async with asyncio.TaskGroup() as tg:
            for idx, coro in enumerate(coros):
                tg.create_task(_run(idx, coro), name=f"try_group[{idx}]", eager_start=True)
    except* asyncio.CancelledError:
        raise
    except* BaseException as eg:
        raise BaseExceptionGroup(
            f"try_group{' ' + ctx if ctx else ''}",
            list(eg.exceptions),
        )

    return tuple(results)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# parallel_taskgroup_star — PEP 654 except* variant
# ---------------------------------------------------------------------------

async def parallel_taskgroup_star[T](
    coros: Sequence[Awaitable[T]],
    *,
    concurrency: int | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> ParallelResult:
    """PEP 654 except* variant of _parallel_taskgroup.

    Uses Python 3.11+ ``except*`` syntax for precise exception routing.
    """
    _log = logger_instance or logger
    _SENTINEL = object()
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
        _log.debug("[GHOST] parallel_taskgroup_star CancelledError%s", (" " + ctx) if ctx else "")
        raise
    except* (RuntimeError, OSError) as eg:
        for exc in eg.exceptions:
            _log.debug("[GHOST] parallel_taskgroup_star%s %s: %s", (" " + ctx) if ctx else "", type(exc).__name__, exc)
            errors.append(exc)
    except* BaseException as eg:
        _log.debug("[GHOST] parallel_taskgroup_star BaseException%s: %s", (" " + ctx) if ctx else "", type(eg.exceptions[0]).__name__ if eg.exceptions else "unknown")
        raise

    ok_results = [r for r in results if r is not _SENTINEL and not isinstance(r, BaseException)]
    return ParallelResult(ok=ok_results, errors=errors, re_raised=None)


# ---------------------------------------------------------------------------
# safe_gather variants (deprecated)
# ---------------------------------------------------------------------------

@warnings.deprecated("Use parallel(coros, policy='collect') instead", category=DeprecationWarning)
async def safe_gather[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> SafeGatherResult:
    """Single-call helper for safe gather with full invariant enforcement."""
    _log = logger_instance or logger
    if not coros:
        return SafeGatherResult(ok=[], errors=[])

    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    if re_raise is not None:
        raise re_raise

    return SafeGatherResult(ok=ok, errors=errors)


@warnings.deprecated("Use parallel_ok(*coros) instead", category=DeprecationWarning)
async def safe_gather_ok[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """Deprecated alias for parallel_ok."""
    return await parallel_ok(*coros, label=label, logger_instance=logger_instance)


@warnings.deprecated("Use parallel(coros, policy='raise') instead", category=DeprecationWarning)
async def safe_gather_strict[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[T]:
    """asyncio.gather wrapper that auto-raises BaseExceptionGroup on exceptions."""
    _log = logger_instance or logger
    if not coros:
        return []

    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    ok, errors, re_raise = _classify_gathered(raw, label, _log)

    if re_raise is not None:
        raise re_raise

    if errors:
        if len(errors) == 1:
            raise errors[0]
        raise BaseExceptionGroup(f"safe_gather_strict{' ' + label if label else ''}", errors)

    return ok  # type: ignore[return-value]


@warnings.deprecated("Use parallel(coros, policy='log') instead", category=DeprecationWarning)
async def safe_gather_fire_and_forget[T](
    *coros: Awaitable[T] | T,
    label: str = "",
    logger_instance: logging.Logger | None = None,
) -> _BoundedExceptionLog | None:
    """Fire-and-forget gather for sites that discard the result entirely."""
    _log = logger_instance or logger
    if not coros:
        return None

    raw = await asyncio.gather(*(_wrap_awaitable(c) for c in coros), return_exceptions=True)
    _ok, errors, re_raise = _classify_gathered(raw, label, _log)

    if re_raise is not None:
        _log.debug(
            "[GHOST] safe_gather_faf re-raising %s%s",
            type(re_raise).__name__,
            (" " + label) if label else "",
        )
        raise re_raise

    if not errors:
        return None

    sample: list[tuple[str, str, str]] = []
    for exc in errors[:_SAFE_GATHER_SAMPLE_CAP]:
        sample.append((type(exc).__name__, str(exc)[:200], label))
    suppressed = max(0, len(errors) - _SAFE_GATHER_SAMPLE_CAP)
    _log.debug(
        f"[GHOST] safe_gather_faf{' ' + label if label else ''} "
        f"suppressed {len(errors)} exceptions "
        f"(sample: {', '.join(t for t, _, _ in sample)}"
        f"{' +' + str(suppressed) + ' more' if suppressed else ''})"
    )
    return _BoundedExceptionLog(sample=tuple(sample), suppressed_count=suppressed)


# ---------------------------------------------------------------------------
# bounded_parallel_map
# ---------------------------------------------------------------------------

async def bounded_parallel_map[T, R](
    items: list[T],
    coro_fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    ordered: bool = True,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
    jitter_sigma_s: float = 0.0,
    jitter_max_s: float = 2.0,
) -> list[R | None]:
    """Parallel async map with bounded concurrency.

    Transforms a list of items concurrently with explicit concurrency cap.
    Clean replacement for sequential `for x in xs: await f(x)`.
    """
    _log = logger_instance or logger
    if not items:
        return []

    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1
    else:
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, concurrency_budget
        concurrency = await concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL)

    sem = asyncio.Semaphore(concurrency)

    async def _run(idx: int, item: T) -> tuple[int, R | BaseException]:
        try:
            _bpm_jitter = jitter_sigma_s
            if _bpm_jitter > 0:
                from hledac.universal.core.telemetry.context_state import is_blitz_mode
                if not is_blitz_mode():
                    import random as _rng
                    await asyncio.sleep(min(abs(_rng.gauss(0.0, _bpm_jitter)), jitter_max_s))
        except Exception:  # noqa: BLE001
            pass

        async with sem:
            try:
                return idx, await coro_fn(item)
            except BaseException as e:
                _log.debug(
                    f"[GHOST] bounded_parallel_map{' ' + ctx if ctx else ''} "
                    f"item[{idx}] raised {type(e).__name__}: {e}"
                )
                return idx, e

    tasks = [safe_create_task(_run(i, item)) for i, item in enumerate(items)]
    raw = cast("list[tuple[int, R | BaseException]]", await asyncio.gather(*tasks, return_exceptions=True))

    for item in raw:
        if isinstance(item, asyncio.CancelledError):
            _log.debug("[GHOST] bounded_parallel_map CancelledError — re-raising")
            raise item
        if isinstance(item, BaseException) and not isinstance(item, Exception):
            _log.debug(f"[GHOST] bounded_parallel_map {type(item).__name__} — re-raising")
            raise item

    if ordered:
        raw.sort(key=lambda x: x[0])

    filtered: list[R | None] = []
    for _, result in raw:
        filtered.append(None if isinstance(result, Exception) else cast(R, result))
    return filtered


# ---------------------------------------------------------------------------
# race_first_success
# ---------------------------------------------------------------------------

async def race_first_success(
    *coros: tuple[Awaitable[Any], str],
    timeout: float | None = None,
    label: str = "",
    require_truthy: bool = True,
    logger_instance: logging.Logger | None = None,
) -> RaceFirstSuccessResult:
    """Race coroutines to first success — cancel all others immediately."""
    _log = logger_instance or logger
    if label:
        _log.debug("[race_first_success] starting%s with %d candidates", f"({label})" if label else "", len(coros))
    if not coros:
        return RaceFirstSuccessResult(result=None, winner_index=-1, winner_label="", errors=[])
    result_holder: list[Any] = [None]
    index_holder: list[int] = [-1]
    errors: list[BaseException] = []
    falsy_results: list[Any] = []

    def _set_winner(idx: int, value: Any) -> bool:
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
        return RaceFirstSuccessResult(result=None, winner_index=-1, winner_label="", errors=errors, falsy_results=falsy_results)
    except BaseExceptionGroup as eg:
        for exc in eg.exceptions:
            if isinstance(exc, asyncio.CancelledError):
                continue
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


# ---------------------------------------------------------------------------
# chunked_taskgroup
# ---------------------------------------------------------------------------

async def chunked_taskgroup[T, R](
    items: list[T],
    coro_fn: Callable[[T], Awaitable[R]],
    *,
    batch_size: int = 20,
    concurrency: int | ConcurrencyBudgetResolver | None = None,
    ctx: str = "",
    logger_instance: logging.Logger | None = None,
) -> list[R]:
    """Memory-safe batch processing via TaskGroup.

    Processes `items` in bounded batches using asyncio.TaskGroup. Each batch
    runs concurrently up to `concurrency` limit. Results are yielded incrementally.
    """
    _log = logger_instance or logger
    if not items:
        return []
    if batch_size < 1:
        batch_size = 1

    if concurrency is not None:
        if callable(concurrency):
            concurrency = await concurrency()
        if concurrency < 1:
            concurrency = 1
    else:
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, concurrency_budget
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
                    _log.debug("[GHOST] chunked_taskgroup BaseException%s: %s", ("_" + ctx) if ctx else "", type(exc).__name__)
                    raise exc from None
        except asyncio.CancelledError:
            _log.debug("[GHOST] chunked_taskgroup CancelledError%s", ("_" + ctx) if ctx else "")
            raise

        for r in capture.results:
            if r is not None:
                _, val = r
                if val is not None:
                    all_results.append(val)

    return all_results


__all__ = [
    # Result DTOs
    "ParallelResult",
    "SafeGatherResult",
    "RaceFirstSuccessResult",
    "_BoundedExceptionLog",
    "ExceptionPolicy",
    # Core functions
    "parallel",
    "parallel_ok",
    "try_group",
    "parallel_taskgroup_star",
    "safe_create_task",
    "safe_gather",
    "safe_gather_ok",
    "safe_gather_strict",
    "safe_gather_fire_and_forget",
    "bounded_parallel_map",
    "race_first_success",
    "chunked_taskgroup",
    "_check_gathered",
    "ConcurrencyBudgetResolver",
]
