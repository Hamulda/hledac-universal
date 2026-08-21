"""Async Retry — Centralized tenacity-based retry decorator for Hledac Universal.

ROADMAP-005: Replaces all manual retry loops with centralized decorator.

Features:
- Exponential backoff with jitter (decorrelated)
- Configurable retry conditions (exception types, predicates)
- Before/after callbacks for logging, circuit breakers, etc.
- Blitz mode optimization (2 attempts instead of full retries)
- Memory-efficient (slots=True, no closure capture)
- Python 3.14+ compatible (no legacy patterns)

Usage:
    from _core.async_retry import async_retry, retry_if_exception

    @async_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
    async def fetch_with_retry(url: str) -> str:
        return await http_client.fetch(url)

    # With custom backoff
    @async_retry(max_attempts=5, exceptions=(OSError,), wait=jitter_wait)
    async def robust_operation():
        ...
"""

from __future__ import annotations

import functools
import secrets
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    stop_after_attempt,
)
from tenacity import retry_if_exception_type as _retry_if_exception_type

if TYPE_CHECKING:
    pass

__all__ = [
    "async_retry",
    "retry_if_exception",
    "retry_if_exception_type",
    "retry_if_result",
    "blitz_aware_stop",
    "exponential_backoff",
    "jitter_wait",
]

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_MAX_ATTEMPTS: int = 3
DEFAULT_BASE_DELAY: float = 0.5
DEFAULT_MAX_DELAY: float = 30.0

# BLITZ-15: 2 attempts in blitz mode (1 retry), 4 total in normal mode (3 retries)
BLITZ_MAX_ATTEMPTS: int = 2

# Memory-efficient crypto-safe RNG (reuse across retries)
_JITTER_RNG = secrets.SystemRandom()

# ── Backoff Strategies ────────────────────────────────────────────────────────


def exponential_backoff(
    attempt: int,
    base: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
) -> float:
    """Exponential backoff with optional jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        base: Base delay in seconds
        max_delay: Maximum delay cap
        jitter: Apply random jitter to decorrelate retries

    Returns:
        Delay in seconds before next retry
    """
    delay = min(base * (2**attempt), max_delay)
    if jitter:
        # Full jitter: uniform(0, delay)
        delay = _JITTER_RNG.uniform(0.0, delay)
    return delay


def jitter_wait(retry_state: RetryCallState) -> float:
    """Tenacity wait generator: exponential backoff with full jitter.

    Compatible with tenacity's retry decorator.
    """
    attempt = retry_state.attempt_number
    return exponential_backoff(attempt, base=DEFAULT_BASE_DELAY, max_delay=DEFAULT_MAX_DELAY, jitter=True)


def _resolve_blitz_max_attempts() -> int:
    """Resolve max attempts based on blitz mode."""
    try:
        from hledac.universal._core.telemetry.context_state import is_blitz_mode as _is_blitz

        return BLITZ_MAX_ATTEMPTS if _is_blitz() else DEFAULT_MAX_ATTEMPTS
    except Exception:
        return DEFAULT_MAX_ATTEMPTS


def blitz_aware_stop(retry_state: RetryCallState) -> bool:
    """Tenacity stop predicate: blitz-aware max attempts.

    BLITZ-15: In blitz mode, stop after 2 attempts (1 retry).
    In normal mode, stops after DEFAULT_MAX_ATTEMPTS.
    """
    max_attempts = _resolve_blitz_max_attempts()
    return retry_state.attempt_number >= max_attempts


# ── Retry Predicates ──────────────────────────────────────────────────────────

T = TypeVar("T")
P = ParamSpec("P")


def retry_if_exception(
    *exceptions: type[BaseException],
) -> Callable[[BaseException], bool]:
    """Create a tenacity retry predicate for specific exception types.

    Usage:
        @async_retry(retry=retry_if_exception(ConnectionError, TimeoutError))
        async def fetch():
            ...
    """
    return _retry_if_exception_type(exceptions)


def retry_if_result[T](
    predicate: Callable[[T], bool],
) -> Callable[[RetryCallState], bool]:
    """Create a tenacity retry predicate based on result value.

    Usage:
        @async_retry(retry=retry_if_result(lambda r: r.status_code == 429))
        async def fetch():
            ...
    """

    def predicate_fn(retry_state: RetryCallState) -> bool:
        if retry_state.outcome is None:
            return False
        result = retry_state.outcome.result()
        if result is None:
            return False
        return predicate(result)

    return predicate_fn


# ── Main Decorator ────────────────────────────────────────────────────────────


def async_retry(
    max_attempts: int | None = None,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    retry: Callable[[BaseException], bool] | None = None,
    wait: Callable[[RetryCallState], float] | None = None,
    before_sleep: Callable[[RetryCallState], None] | None = None,
    after: Callable[[RetryCallState], None] | None = None,
    reraise: bool = True,
    blitz_aware: bool = True,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Create an async retry decorator with tenacity.

    Args:
        max_attempts: Maximum retry attempts. None = use blitz_aware (2/3).
        exceptions: Tuple of exception types to catch and retry.
        retry: Custom tenacity retry predicate. Overrides `exceptions`.
        wait: Custom tenacity wait strategy. Default = exponential_backoff with jitter.
        before_sleep: Callback called before retry delay (logging, circuit breaker, etc.).
        after: Callback called after successful retry (cleanup, metrics, etc.).
        reraise: Re-raise final exception if all retries exhausted.
        blitz_aware: If True and max_attempts is None, use blitz-aware stop (2 in blitz, 3 normal).

    Returns:
        Decorated async function with automatic retry logic.

    Usage:
        @async_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        async def fetch_with_retry(url: str) -> str:
            return await http_client.fetch(url)

        # With custom callbacks
        @async_retry(
            exceptions=(OSError,),
            before_sleep=lambda s: logger.warning(f"Retrying after {s.attempt_number} attempts"),
        )
        async def robust_operation():
            ...
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if max_attempts is not None:
            _stop = stop_after_attempt(max_attempts)
        elif blitz_aware:
            _stop = blitz_aware_stop
        else:
            _stop = stop_after_attempt(DEFAULT_MAX_ATTEMPTS)

        if retry is not None:
            _retry_predicate = retry
        else:
            _retry_predicate = _retry_if_exception_type(exceptions)

        _wait = wait if wait is not None else jitter_wait

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            async for attempt in AsyncRetrying(
                stop=_stop,
                wait=_wait,
                retry=_retry_predicate,
                before_sleep=before_sleep,
                after=after,
                reraise=reraise,
            ):
                with attempt:
                    return await func(*args, **kwargs)
            # Should not reach here if reraise=True
            raise RuntimeError(f"Retry loop exited unexpectedly in {func.__name__}")

        return wrapper

    return decorator


# ── Convenience Decorators ────────────────────────────────────────────────────


def network_retry(
    max_attempts: int | None = None,
    blitz_aware: bool = True,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator for network operations with common retry logic.

    Retries on:
    - ConnectionError, TimeoutError, OSError (network-related)
    - httpx exceptions (if available)

    Usage:
        @network_retry()
        async def fetch_url(url: str) -> str:
            return await http_client.get(url)
    """
    exceptions: tuple[type[BaseException], ...] = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    # Try to add httpx exceptions if available
    try:
        import httpx

        exceptions = exceptions + (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ProtocolError,
        )
    except ImportError:
        pass

    return async_retry(
        max_attempts=max_attempts,
        exceptions=exceptions,
        blitz_aware=blitz_aware,
    )


def http_retry(
    max_attempts: int | None = None,
    status_codes: tuple[int, ...] = (429, 502, 503, 504, 520),
    blitz_aware: bool = True,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator for HTTP operations with status code retry logic.

    Note: This requires the wrapped function to raise exceptions for retryable
    status codes, or use retry_if_result for status code checking.

    Usage:
        @http_retry(status_codes=(429, 502, 503, 504))
        async def fetch_with_status_handling() -> Response:
            response = await client.get(url)
            if response.status_code in (429, 502, 503, 504):
                raise RetryableStatus(response.status_code)
            return response
    """
    return async_retry(
        max_attempts=max_attempts,
        blitz_aware=blitz_aware,
    )


# ── Backoff Utilities ─────────────────────────────────────────────────────────


def compute_backoff(
    attempt: int,
    base: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    *,
    jitter: bool = True,
    prev_sleep: float = 0.0,
) -> tuple[float, float]:
    """Compute backoff with optional decorrelated jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        base: Base delay
        max_delay: Maximum delay cap
        jitter: Apply decorrelated jitter
        prev_sleep: Previous sleep duration (for decorrelation)

    Returns:
        (delay, new_prev_sleep) tuple
    """
    raw_delay = min(base * (2**attempt), max_delay)

    if jitter and prev_sleep > 0:
        # Decorrelated jitter: uniform(0, max(raw_delay, prev_sleep) * 3)
        new_prev_sleep = _JITTER_RNG.uniform(0.0, max(raw_delay, prev_sleep) * 3.0)
        return min(new_prev_sleep, max_delay), new_prev_sleep
    elif jitter:
        new_prev_sleep = _JITTER_RNG.uniform(0.0, raw_delay)
        return min(new_prev_sleep, max_delay), new_prev_sleep
    else:
        return raw_delay, raw_delay


def resolve_blitz_cap(max_delay: float = DEFAULT_MAX_DELAY) -> float:
    """Resolve backoff cap based on blitz mode.

    BLITZ-15: Returns 1.0s cap in blitz mode, max_delay otherwise.
    """
    try:
        from hledac.universal._core.telemetry.context_state import is_blitz_mode as _is_blitz

        return 1.0 if _is_blitz() else max_delay
    except Exception:
        return max_delay
