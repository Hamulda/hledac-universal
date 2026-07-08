# runtime/error_policy.py
"""
PEP 654 ExceptionGroup-based error handling + Rust-style Result monad.

Issue 9 — Cancelltion/error handling restructure
Modern Python 3.11+ async error patterns for Hledac Universal.

Design principles:
- Result[T] replaces raise-on-error in lane runners (happy path = no exceptions)
- lane_result() aggregates per-lane errors as ExceptionGroup
- CancelledError chains through shield boundaries
- asyncio.timeout() as context manager for bounded operations
- No bare except Exception — specific exceptions only

Invariant table:
| Test | What it verifies |
|------|-----------------|
| test_result_ok | Result.Ok unwrap / is_ok / is_err |
| test_result_err | Result.Err unwrap / is_err / error field |
| test_result_match | Result.match() dispatches correctly |
| test_lane_result_single | lane_result() wraps single exception |
| test_lane_result_multiple | lane_result() creates ExceptionGroup from list |
| test_cancellation_propagates | CancelledError chains through shield |
| test_timeout_context | asyncio.timeout() fires on timeout |
| test_shield_critical_section | asyncio.shield() protects critical work |

Author: Issue 9
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TypeVar, Generic, Any, cast
from collections.abc import Awaitable, Callable

from hledac.universal.utils.async_helpers import safe_gather_ok

T = TypeVar("T", default=object)
E = TypeVar("E", bound=BaseException, default=Exception)


# ─────────────────────────────────────────────────────────────────────────────
# Result[T, E] — Rust-style discriminated union
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Ok[T]:
    """Success variant of Result[T, E]."""

    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value

    def unwrap_err(self) -> E:
        msg = "Ok.unwrap_err() called on Ok variant"
        raise ValueError(msg)

    def map(self, fn: Callable[[T], Any]) -> Ok:
        return Ok(fn(self.value))

    def match(self, ok: Callable[[T], Any], err: Callable[[E], Any]) -> Any:  # noqa: ARG001
        return ok(self.value)


@dataclass(frozen=True, slots=True)
class Err[E: BaseException]:
    """Error variant of Result[T, E]."""

    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> T:
        msg = f"Err.unwrap() called with error: {self.error!r}"
        raise RuntimeError(msg)

    def unwrap_or(self, default: T) -> T:
        return default

    def unwrap_err(self) -> E:
        return self.error

    def map(self, fn: Callable[[Any], Any]) -> Err:
        return self  # propagate error unchanged

    def match(self, ok: Callable[[T], Any], err: Callable[[E], Any]) -> Any:
        return err(self.error)


Result = Ok[T] | Err[E]


def result_of[T](fn: Callable[[], T]) -> Result[T, Exception]:
    """Wrap a synchronous callable that may raise. Returns Result[T, Exception]."""
    try:
        return Ok(fn())
    except Exception as exc:  # noqa: BLE001
        return Err(exc)


async def result_of_await[T](
    coro: Awaitable[T],
) -> Result[T, Exception]:
    """Wrap an awaitable that may raise. Returns Result[T, Exception]."""
    try:
        value = await coro
        return Ok(value)
    except Exception as exc:  # noqa: BLE001
        return Err(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Lane result aggregation — PEP 654 ExceptionGroup
# ─────────────────────────────────────────────────────────────────────────────


LaneError = ExceptionGroup | Exception


def lane_result(
    lane_name: str,
    results: list[Result],
) -> Result[list, ExceptionGroup]:
    """
    Aggregate a list of per-item Results into a single Result[list, ExceptionGroup].

    On success: returns Ok([all values])
    On failure: returns Err(ExceptionGroup("lane_name", [all errors]))

    PEP 654 pattern: raises ExceptionGroup only when there are errors;
    happy path returns Ok without raising.

    Example:
        results = await safe_gather_ok(*[run_one(item) for item in items])
        lane_results = [lane_result("my_lane", results)]
        return lane_results
    """
    errors: list[Exception] = []
    values: list = []

    for r in results:
        if isinstance(r, Ok):
            values.append(r.value)
        else:
            errors.append(r.error)

    if not errors:
        return Ok(values)

    # Build ExceptionGroup with lane context
    eg = ExceptionGroup(lane_name, errors)
    return Err(eg)


def lane_result_from_exceptions(
    lane_name: str,
    exceptions: list[BaseException],
) -> Result[list, ExceptionGroup]:
    """Convert a list of exceptions to a Result. Empty list = Ok([])."""
    if not exceptions:
        return Ok([])

    # CancelledError is a BaseException but NOT an Exception — it cannot go
    # into ExceptionGroup (PEP 654). We collect only Exception members.
    error_members: list[Exception] = []
    for exc in exceptions:
        if isinstance(exc, asyncio.CancelledError):
            # Propagate cancellation immediately — don't embed in ExceptionGroup
            raise asyncio.CancelledError() from exc
        if isinstance(exc, ExceptionGroup):
            for sub in exc.exceptions:
                if isinstance(sub, asyncio.CancelledError):
                    raise asyncio.CancelledError() from sub
                error_members.append(cast(Exception, sub))
        else:
            error_members.append(cast(Exception, exc))

    if not error_members:
        return Ok([])

    return Err(ExceptionGroup(lane_name, error_members))


# ─────────────────────────────────────────────────────────────────────────────
# Cancellation helpers
# ─────────────────────────────────────────────────────────────────────────────


def is_cancellation_tree(exc: BaseException) -> bool:
    """
    Walk an exception tree and return True if the root or any leaf is CancelledError.

    Python 3.14+ asyncio.gather wraps CancelledError in BaseExceptionGroup
    (not ExceptionGroup since CancelledError is not an Exception).
    This helper detects that without relying on exc.type (removed in 3.14).
    """
    if isinstance(exc, asyncio.CancelledError):
        return True
    # BaseExceptionGroup is the Python 3.14+ parent of ExceptionGroup;
    # it can contain CancelledError which ExceptionGroup cannot
    if isinstance(exc, BaseExceptionGroup):
        return any(is_cancellation_tree(sub) for sub in exc.exceptions)
    return False


async def cancel_scope_drain[T](
    coro: Awaitable[T],
    timeout_s: float,
    label: str = "",
) -> T:
    """
    Run coro inside asyncio.timeout() context manager (Python 3.11+).

    On timeout, raises asyncio.TimeoutError which is NOT a CancelledError.
    Use this for operations where timeout != cancellation is semantically distinct.

    Args:
        coro: The coroutine to run with timeout
        timeout_s: Maximum duration in seconds
        label: Descriptive label for logging

    Returns:
        The result of coro on success

    Raises:
        asyncio.TimeoutError: When timeout expires
        asyncio.CancelledError: When cancelled externally
    """
    try:
        async with asyncio.timeout(timeout_s):
            return await coro
    except TimeoutError:
        logging.getLogger("error_policy").debug(
            "[error_policy] timeout after %ss: %s", timeout_s, label
        )
        raise


async def shield_cancel_scope[T](
    coro: Awaitable[T],
    label: str = "",
) -> T:
    """
    Run coro with asyncio.shield() — protects from outer cancellation.

    Use for critical sections: DuckDB writes, LMDB commits, evidence logging.
    Even if the surrounding TaskGroup cancels, these complete.

    Args:
        coro: The coroutine to shield
        label: Descriptive label for logging

    Returns:
        The result of coro on success

    Raises:
        asyncio.CancelledError: Only if shield itself is cancelled (rare)
    """
    try:
        return await asyncio.shield(coro)
    except asyncio.CancelledError:
        logging.getLogger("error_policy").debug(
            "[error_policy] shielded coroutine cancelled: %s", label
        )
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Bounded lane runner with Result semantics
# ─────────────────────────────────────────────────────────────────────────────


LaneRunner = Callable[[], Awaitable[Result]]
"""A lane runner that returns Result instead of raising."""


async def run_bounded_lane(
    name: str,
    runner: LaneRunner,
    timeout_s: float | None = None,
) -> Result:
    """
    Run a lane runner with optional timeout and Result semantics.

    - timeout_s=None: no timeout (use with care)
    - On timeout: Err(TimeoutError)
    - On success: Ok(value)
    - On exception: Err(exception) — never raises

    This is the canonical lane runner pattern replacing raw coroutines.
    """
    async def _run() -> Result:
        try:
            result = await runner()
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return Err(exc)

    if timeout_s is not None:
        try:
            async with asyncio.timeout(timeout_s):
                return await _run()
        except TimeoutError:
            return Err(TimeoutError(f"Lane '{name}' exceeded timeout of {timeout_s}s"))
    else:
        return await _run()


# ─────────────────────────────────────────────────────────────────────────────
# Batch runner — runs multiple lanes and aggregates as ExceptionGroup
# ─────────────────────────────────────────────────────────────────────────────


async def run_lane_batch(
    lanes: list[tuple[str, LaneRunner, float | None]],
) -> Result[list[tuple[str, Result]], ExceptionGroup]:
    """
    Run multiple lanes concurrently, aggregate errors as ExceptionGroup.

    Args:
        lanes: List of (name, runner, timeout_s) tuples

    Returns:
        Ok(list of (name, Result) tuples) on success
        Err(ExceptionGroup) if any lane failed

    Example:
        lanes = [
            ("bgp", lambda: run_bounded_lane("bgp", bgp_runner, 30.0)),
            ("wayback", lambda: run_bounded_lane("wayback", wayback_runner, 45.0)),
        ]
        result = await run_lane_batch(lanes)
        result.match(
            ok=lambda v: process_results(v),
            err=lambda e: handle_lane_errors(e),
        )
    """
    async def _run_one(name: str, runner: LaneRunner, timeout_s: float | None) -> tuple[str, Result]:
        r = await run_bounded_lane(name, runner, timeout_s)
        return name, r

    tasks = [asyncio.create_task(_run_one(n, r, t), name=f"lane_batch:{n}") for n, r, t in lanes]
    results = await safe_gather_ok(*tasks, label="lane_batch")

    errors: list[Exception] = []
    ok_results: list[tuple[str, Result]] = []

    for name, result in results:
        if isinstance(result, Err):
            errors.append(result.error)
        else:
            ok_results.append((name, result))

    if errors:
        return Err(ExceptionGroup("lane_batch", errors))
    return Ok(ok_results)


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "Result",
    "Ok",
    "Err",
    "result_of",
    "result_of_await",
    "lane_result",
    "lane_result_from_exceptions",
    "is_cancellation_tree",
    "cancel_scope_drain",
    "shield_cancel_scope",
    "LaneRunner",
    "run_bounded_lane",
    "run_lane_batch",
]
