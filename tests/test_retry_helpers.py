"""
Tests for utils/retry.py — centralizovaný retry helper.

Invariant table:
| Test | Invariant |
|------|-----------|
| test_retry_loop_exhausted_after_max_attempts | max_attempts=3 → exactly 3 iterations |
| test_retry_loop_deterministic_sequence | jitter=False → deterministic delay sequence |
| test_retry_loop_jitter_bounds | jitter=True → delay within [0.1*base, max_delay] |
| test_retry_loop_properties | attempt/exhausted properties correct |
| test_retry_async_success_first_try | success on first try → no sleep |
| test_retry_async_exhausts_retries | retries max_attempts times before raising |
| test_retry_async_propagates_cancelled_error | cancel_is_retriable=False → CancelledError propagates |
| test_retry_async_retryable_exception_retried | retryable exc → retry, non-retryable exc → raise |
| test_retry_async_on_retry_callback | on_retry called with correct args per attempt |
| test_is_retryable_default | default tuple covers TimeoutError/ConnectionError/OSError |
| test_is_retryable_custom | custom retryable tuple works |
"""

from __future__ import annotations

import asyncio
from typing import Never

import pytest

from hledac.universal.utils.retry import (
    RetryLoop,
    is_retryable,
    retry_async,
)

# ==============================================================================
# RetryLoop (sync iterator)
# ==============================================================================


class TestRetryLoop:
    def test_retry_loop_exhausted_after_max_attempts(self) -> None:
        loop = RetryLoop(max_attempts=3, base_delay=0.01, jitter=False)
        attempts = list(loop)
        assert len(attempts) == 3
        assert [a for a, _ in attempts] == [1, 2, 3]

    def test_retry_loop_exhausted_after_max_attempts(self) -> None:
        """max_attempts=3 → exactly 3 (attempt, delay) pairs."""
        loop = RetryLoop(max_attempts=3, base_delay=0.01, jitter=False)
        results = list(loop)
        assert len(results) == 3
        assert [attempt for attempt, _ in results] == [1, 2, 3]

    def test_retry_loop_deterministic_sequence(self) -> None:
        """jitter=False → deterministic geometric backoff sequence."""
        loop = RetryLoop(max_attempts=4, base_delay=0.5, max_delay=30.0, jitter=False)
        _, delays = zip(*list(loop), strict=False)
        # base_delay * 2^(attempt-1): 0.5, 1.0, 2.0, 4.0
        expected = [0.5, 1.0, 2.0, 4.0]
        assert delays == tuple(expected)

    def test_retry_loop_jitter_bounds(self) -> None:
        """jitter=True → delay stays within valid bounds."""
        RetryLoop(max_attempts=20, base_delay=1.0, max_delay=30.0, jitter=True)
        all_delays = []
        for _ in range(5):  # 5 full iterations
            results = list(RetryLoop(max_attempts=20, base_delay=1.0, max_delay=30.0, jitter=True))
            for _, delay in results:
                assert delay >= 0.0, f"delay {delay} must be >= 0"
                assert delay <= 30.0, f"delay {delay} must be <= max_delay"
                all_delays.append(delay)

    def test_retry_loop_properties(self) -> None:
        """attempt and exhausted properties reflect iterator state."""
        loop = RetryLoop(max_attempts=3, base_delay=0.01, jitter=False)
        assert loop.attempt == 0
        assert loop.exhausted is False

        for _ in loop:
            pass

        assert loop.attempt == 3
        assert loop.exhausted is True


# ==============================================================================
# is_retryable
# ==============================================================================


class TestIsRetryable:
    def test_is_retryable_default(self) -> None:
        assert is_retryable(TimeoutError()) is True
        assert is_retryable(ConnectionError()) is True
        assert is_retryable(OSError()) is True
        assert is_retryable(ValueError()) is False
        assert is_retryable(RuntimeError()) is False

    def test_is_retryable_custom(self) -> None:
        custom = (ValueError, TypeError)
        assert is_retryable(ValueError(), retryable=custom) is True
        assert is_retryable(TypeError(), retryable=custom) is True
        assert is_retryable(RuntimeError(), retryable=custom) is False


# ==============================================================================
# retry_async
# ==============================================================================


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_retry_async_success_first_try(self) -> None:
        """Success on first try → no sleep, return value."""
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_async(succeed, max_attempts=3)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_async_exhausts_retries(self) -> None:
        """All attempts fail → raises last exception after max_attempts."""
        call_count = 0

        async def always_fail() -> Never:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError) as exc_info:
            await retry_async(always_fail, max_attempts=3, base_delay=0.001)
        assert str(exc_info.value) == "fail"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_async_propagates_cancelled_error(self) -> None:
        """cancel_is_retriable=False → CancelledError propagates immediately."""
        call_count = 0

        async def cancel_me() -> Never:
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await retry_async(cancel_me, cancel_is_retriable=False)

        # CancelledError propagates after first attempt, not retried
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_async_cancelled_is_retriable(self) -> None:
        """cancel_is_retriable=True → CancelledError treated as retriable."""
        call_count = 0

        async def cancel_me() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise asyncio.CancelledError()
            return "recovered"

        result = await retry_async(cancel_me, cancel_is_retriable=True, max_attempts=5, base_delay=0.001)
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_async_retryable_exception_retried(self) -> None:
        """Retryable exception → retries; non-retryable → raises immediately."""
        call_count = 0

        async def fail_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("try again")
            return "done"

        result = await retry_async(fail_then_succeed, max_attempts=3, base_delay=0.001)
        assert result == "done"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self) -> None:
        call_count = 0

        async def raise_value_error() -> Never:
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await retry_async(raise_value_error, max_attempts=3)
        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_retry_async_on_retry_callback(self) -> None:
        """on_retry called with (attempt, delay, exception) for each retry."""
        call_count = 0
        callbacks = []

        async def fail_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError(f"fail {call_count}")
            return "ok"

        def on_retry(attempt: int, delay: float, exc: Exception) -> None:
            callbacks.append((attempt, delay, exc))

        result = await retry_async(
            fail_twice,
            max_attempts=5,
            base_delay=0.001,
            on_retry=on_retry,
        )
        assert result == "ok"
        # Called twice: once before retry 1, once before retry 2
        assert len(callbacks) == 2
        assert callbacks[0][0] == 1
        assert callbacks[1][0] == 2
        assert isinstance(callbacks[0][2], ConnectionError)
        assert isinstance(callbacks[1][2], ConnectionError)

    @pytest.mark.asyncio
    async def test_retry_async_max_delay_cap(self) -> None:
        """delay is capped at max_delay."""
        call_count = 0

        async def always_fail() -> Never:
            nonlocal call_count
            call_count += 1
            raise ConnectionError()

        with pytest.raises(ConnectionError):
            await retry_async(
                always_fail,
                max_attempts=3,
                base_delay=1.0,
                max_delay=1.5,  # cap
                jitter=False,  # deterministic
            )
        # Exponential: 1.0, 2.0(capped), 4.0(capped)
        assert call_count == 3


# ==============================================================================
# Smoke — retry_backoff_linear_async (no jitter)
# ==============================================================================


class TestRetryBackoffLinearAsync:
    @pytest.mark.asyncio
    async def test_linear_no_jitter_deterministic(self) -> None:
        """Linear backoff without jitter is deterministic."""
        call_count = 0

        async def fail_twice() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError()
            return "ok"

        from hledac.universal.utils.retry import retry_backoff_linear_async

        result = await retry_backoff_linear_async(
            fail_twice,
            max_attempts=5,
            base_delay=1.0,
            max_delay=30.0,
        )
        assert result == "ok"
        assert call_count == 3
