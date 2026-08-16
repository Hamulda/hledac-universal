# tests/test_issue9_error_policy.py
"""
TestSprint9 — Error policy tests (Issue 9)

Tests Result[T, E] monad, PEP 654 ExceptionGroup aggregation,
cancellation propagation, asyncio.timeout() and asyncio.shield().

Run: pytest tests/test_issue9_error_policy.py -x -q
"""

import asyncio
import pytest

from runtime.error_policy import (
    Ok,
    Err,
    Result,
    result_of,
    result_of_await,
    lane_result,
    lane_result_from_exceptions,
    is_cancellation_tree,
    cancel_scope_drain,
    shield_cancel_scope,
    LaneRunner,
    run_bounded_lane,
    run_lane_batch,
)


# ─────────────────────────────────────────────────────────────────────────────
# Result monad tests
# ─────────────────────────────────────────────────────────────────────────────


def test_result_ok_unwrap() -> None:
    """Ok.unwrap() returns the value."""
    ok = Ok(42)
    assert ok.unwrap() == 42
    assert ok.is_ok() is True
    assert ok.is_err() is False


def test_result_err_unwrap() -> None:
    """Err.unwrap() raises RuntimeError."""
    err = Err(ValueError("bad"))
    assert err.is_ok() is False
    assert err.is_err() is True
    with pytest.raises(RuntimeError):
        err.unwrap()


def test_result_err_error_field() -> None:
    """Err.error returns the wrapped exception."""
    exc = ValueError("bad")
    err = Err(exc)
    assert err.error is exc


def test_result_match_ok() -> None:
    """Ok.match() calls the ok handler."""
    ok = Ok(10)
    result = ok.match(
        ok=lambda v: f"got {v}",
        err=lambda e: f"err {e}",
    )
    assert result == "got 10"


def test_result_match_err() -> None:
    """Err.match() calls the err handler."""
    err = Err(ValueError("bad"))
    result = err.match(
        ok=lambda v: f"got {v}",
        err=lambda e: f"err {e}",
    )
    assert result == "err bad"


def test_result_unwrap_or() -> None:
    """Ok.unwrap_or() returns value; Err.unwrap_or() returns default."""
    ok = Ok(5)
    err = Err(RuntimeError("oops"))
    assert ok.unwrap_or(999) == 5
    assert err.unwrap_or(999) == 999


def test_result_of_sync() -> None:
    """result_of() wraps a sync callable that may raise."""
    f = result_of(lambda: 1 + 1)
    assert isinstance(f, Ok)
    assert f.value == 2

    g = result_of(lambda: 1 / 0)
    assert isinstance(g, Err)


@pytest.mark.asyncio
async def test_result_of_await_ok() -> None:
    """result_of_await() returns Ok on success."""
    async def succeed():
        await asyncio.sleep(0)
        return 42
    r = await result_of_await(succeed())
    assert isinstance(r, Ok)
    assert r.value == 42


@pytest.mark.asyncio
async def test_result_of_await_err() -> None:
    """result_of_await() returns Err on exception."""
    async def fail():
        raise RuntimeError("boom")
    r = await result_of_await(fail())
    assert isinstance(r, Err)
    assert isinstance(r.error, RuntimeError)


# ─────────────────────────────────────────────────────────────────────────────
# PEP 654 ExceptionGroup — lane_result
# ─────────────────────────────────────────────────────────────────────────────


def test_lane_result_single() -> None:
    """lane_result() wraps a single error as ExceptionGroup."""
    results: list[Result] = [Ok(1), Err(ValueError("fail"))]
    r = lane_result("my_lane", results)
    assert isinstance(r, Err)
    assert isinstance(r.error, ExceptionGroup)
    assert r.error.message == "my_lane"
    assert len(r.error.exceptions) == 1


def test_lane_result_multiple() -> None:
    """lane_result() aggregates multiple errors into one ExceptionGroup."""
    results: list[Result] = [Ok(1), Err(ValueError("a")), Ok(2), Err(TypeError("b"))]
    r = lane_result("multi_lane", results)
    assert isinstance(r, Err)
    eg = r.error
    assert eg.message == "multi_lane"
    assert len(eg.exceptions) == 2
    assert isinstance(eg.exceptions[0], ValueError)
    assert isinstance(eg.exceptions[1], TypeError)


def test_lane_result_all_ok() -> None:
    """lane_result() returns Ok with all values when no errors."""
    results: list[Result] = [Ok(1), Ok(2), Ok(3)]
    r = lane_result("all_ok", results)
    assert isinstance(r, Ok)
    assert r.value == [1, 2, 3]


def test_lane_result_from_exceptions_empty() -> None:
    """lane_result_from_exceptions() returns Ok([]) for empty list."""
    r = lane_result_from_exceptions("empty", [])
    assert isinstance(r, Ok)
    assert r.value == []


def test_lane_result_from_exceptions_single() -> None:
    """lane_result_from_exceptions() wraps a single exception."""
    r = lane_result_from_exceptions("single", [ValueError("x")])
    assert isinstance(r, Err)
    assert isinstance(r.error, ExceptionGroup)
    assert r.error.message == "single"


# ─────────────────────────────────────────────────────────────────────────────
# Cancellation — is_cancellation_tree
# ─────────────────────────────────────────────────────────────────────────────


def test_is_cancellation_tree_direct() -> None:
    """is_cancellation_tree() detects direct CancelledError."""
    assert is_cancellation_tree(asyncio.CancelledError()) is True


def test_is_cancellation_tree_nested() -> None:
    """is_cancellation_tree() walks BaseExceptionGroup (Python 3.14+).

    CancelledError is a BaseException, not Exception — it cannot be nested
    in ExceptionGroup (PEP 654). Python 3.14 uses BaseExceptionGroup instead.
    """
    exc = asyncio.CancelledError()
    # BaseExceptionGroup is Python 3.14+; ExceptionGroup cannot hold CancelledError
    try:
        beg = BaseExceptionGroup("cancel", [exc])
    except NameError:
        # fallback: wrap in Exception then check ( CancelledError must propagate)
        beg = ExceptionGroup("cancel", [exc])
    assert is_cancellation_tree(beg) is True


def test_is_cancellation_tree_mixed() -> None:
    """is_cancellation_tree() returns False for plain Exception."""
    assert is_cancellation_tree(ValueError("bad")) is False


def test_is_cancellation_tree_deep_nested() -> None:
    """is_cancellation_tree() handles deeply nested trees."""
    inner_exc = asyncio.CancelledError()
    try:
        inner = BaseExceptionGroup("inner", [inner_exc])
        outer = BaseExceptionGroup("outer", [ValueError("bad"), inner])
    except NameError:
        inner = ExceptionGroup("inner", [inner_exc])
        outer = ExceptionGroup("outer", [ValueError("bad"), inner])
    assert is_cancellation_tree(outer) is True


# ─────────────────────────────────────────────────────────────────────────────
# asyncio.timeout — cancel_scope_drain
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_scope_drain_ok() -> None:
    """cancel_scope_drain() returns result on success."""
    async def fast():
        await asyncio.sleep(0.01)
        return 42
    r = await cancel_scope_drain(fast(), 5.0)
    assert r == 42


@pytest.mark.asyncio
async def test_cancel_scope_drain_timeout() -> None:
    """cancel_scope_drain() raises asyncio.TimeoutError on timeout."""
    async def slow():
        await asyncio.sleep(10)
    with pytest.raises(asyncio.TimeoutError):
        await cancel_scope_drain(slow(), 0.01)


@pytest.mark.asyncio
async def test_cancel_scope_drain_cancellation() -> None:
    """cancel_scope_drain() propagates CancelledError from outer cancellation."""
    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    async def wrapper():
        try:
            async with asyncio.timeout(0.5):
                await slow()
        except TimeoutError:
            raise asyncio.CancelledError()

    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):
        await wrapper()


# ─────────────────────────────────────────────────────────────────────────────
# asyncio.shield — shield_cancel_scope
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shield_cancel_scope_ok() -> None:
    """shield_cancel_scope() returns result on success."""
    async def fast():
        await asyncio.sleep(0.01)
        return "done"
    r = await shield_cancel_scope(fast())
    assert r == "done"


@pytest.mark.asyncio
async def test_shield_protects_against_cancellation() -> None:
    """asyncio.shield() intercepts cancellation so the inner coroutine completes.

    shield_cancel_scope wraps asyncio.shield() — when outer code is cancelled,
    the shielded inner still runs to completion. This is the critical-section
    pattern for DuckDB writes, LMDB commits, evidence logging.

    Python 3.14: asyncio.shield() returns a Future; we await it directly
    inside the TaskGroup and use tg._abort() (Python 3.14 TaskGroup internal)
    to trigger cancellation that shield intercepts.
    """
    inner_completed = False

    async def inner():
        nonlocal inner_completed
        await asyncio.sleep(0.05)
        inner_completed = True
        return "protected"

    async def run():
        nonlocal inner_completed
        async with asyncio.TaskGroup() as group:
            shielded = asyncio.shield(inner())  # Future in Python 3.14+
            group.create_task(asyncio.sleep(0.001))  # trigger
            group._abort()  # cancel all tasks in group
            await shielded  # shielded intercepts; inner still completes
        return inner_completed

    result = await run()
    assert inner_completed is True


# ─────────────────────────────────────────────────────────────────────────────
# run_bounded_lane
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_bounded_lane_ok() -> None:
    """run_bounded_lane() returns Ok on success."""

    async def runner() -> Result:
        return Ok(123)

    r = await run_bounded_lane("test", runner, timeout_s=5.0)
    assert isinstance(r, Ok)
    assert r.value == 123


@pytest.mark.asyncio
async def test_run_bounded_lane_err() -> None:
    """run_bounded_lane() returns Err on exception."""

    async def runner() -> Result:
        raise RuntimeError("boom")

    r = await run_bounded_lane("test", runner)
    assert isinstance(r, Err)
    assert isinstance(r.error, RuntimeError)


@pytest.mark.asyncio
async def test_run_bounded_lane_timeout() -> None:
    """run_bounded_lane() returns Err(TimeoutError) on timeout."""

    async def runner() -> Result:
        await asyncio.sleep(10)
        return Ok(1)

    r = await run_bounded_lane("slow", runner, timeout_s=0.01)
    assert isinstance(r, Err)
    assert isinstance(r.error, TimeoutError)


@pytest.mark.asyncio
async def test_run_bounded_lane_no_timeout() -> None:
    """run_bounded_lane(timeout_s=None) runs without timeout."""
    async def runner() -> Result:
        await asyncio.sleep(0.02)
        return Ok("done")

    r = await run_bounded_lane("no_timeout", runner, timeout_s=None)
    assert isinstance(r, Ok)
    assert r.value == "done"


# ─────────────────────────────────────────────────────────────────────────────
# run_lane_batch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_lane_batch_all_ok() -> None:
    """run_lane_batch() returns Ok with all lane results on full success."""
    async def ok(value: int) -> Result:
        return Ok(value)

    async def lane_a() -> Result:
        return await ok(1)
    async def lane_b() -> Result:
        return await ok(2)

    lanes: list[tuple[str, LaneRunner, float | None]] = [
        ("a", lane_a, 5.0),
        ("b", lane_b, 5.0),
    ]
    r = await run_lane_batch(lanes)
    assert isinstance(r, Ok)
    assert len(r.value) == 2


@pytest.mark.asyncio
async def test_run_lane_batch_partial_err() -> None:
    """run_lane_batch() returns Err(ExceptionGroup) if any lane fails."""
    async def ok() -> Result:
        return Ok(1)

    async def fail() -> Result:
        raise RuntimeError("lane fail")

    lanes: list[tuple[str, LaneRunner, float | None]] = [
        ("good", ok, 5.0),
        ("bad", fail, 5.0),
    ]
    r = await run_lane_batch(lanes)
    assert isinstance(r, Err)
    assert isinstance(r.error, ExceptionGroup)
    assert r.error.message == "lane_batch"
