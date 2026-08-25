"""
Centralizovaný retry helpers -- konzistentní retry politika pro celý projekt.

Retry strategy: exponential backoff + decorrelated jitter + telemetrie.


Usage:
    # Simple async retry
    result = await retry_async(lambda: fetch(url))

    # With custom config
    result = await retry_async(
        lambda: fetch(url),
        max_attempts=5,
        base_delay=1.0,
        max_delay=60.0,
        retryable=lambda e: isinstance(e, (TimeoutError, HTTPError)),
        on_retry=lambda attempt, delay, exc: logger.warning(...),
    )

    # Sync wrapper
    for attempt in retry_loop(max_attempts=3, base_delay=0.5):
        try:
            result = sync_operation()
            break
        except Exception as e:
            if not is_retryable(e):
                raise
"""

from __future__ import annotations

import asyncio
import logging
import random as _random
from typing import TYPE_CHECKING, TypeVar
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds
DEFAULT_JITTER_FACTOR = 0.25  # +/-25% decorrelated jitter

# Retryable exception types (broad by default, narrow per-call)
DEFAULT_RETRYABLE: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
    asyncio.CancelledError,
    )


def is_retryable(exc: Exception, retryable: type[Exception] | tuple[type[Exception], ...] | None = None) -> bool:
    """Check if exception is retryable."""
    if retryable is None:
        return isinstance(exc, DEFAULT_RETRYABLE)
    return isinstance(exc, retryable)



async def retry_async(
    coro_fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: bool = True,
    jitter_factor: float = DEFAULT_JITTER_FACTOR,
    retryable: type[Exception] | tuple[type[Exception], ...] | None = None,
    cancel_is_retriable: bool = False,
    on_retry: Callable[[int, float, Exception], None] | None = None,
    backoff_factor: float = 2.0,
) -> T:
    """Retry an async coroutine with exponential backoff and optional jitter.

    Bounded: all allocations stay fixed-size (no growing structures).

    Args:
        coro_fn: Coroutine factory (callable returning Awaitable[T]).
        max_attempts: Maximum retry attempts (default 3).
        base_delay: Initial delay in seconds (default 0.5).
        max_delay: Cap on delay growth (default 30.0).
        jitter: Add +/-jitter_factor decorrelated jitter (default True).
        jitter_factor: Jitter range (default +/-25%).
        retryable: Exception types to retry on. None = DEFAULT_RETRYABLE.
        cancel_is_retriable: If True, CancelledError triggers retry instead of
            propagation. Default False (CancelledError propagates -- correct for
            graceful SIGINT shutdown).
        on_retry: Optional callback fired before each retry: (attempt, delay, exc).
            Use for telemetry, logging, or circuit-breaker updates.
        backoff_factor: Exponential base (default 2.0 = 0.5 -> 1.0 -> 2.0).

    Returns:
        The return value of coro_fn on success.

    Raises:
        CancelledError: Propagates immediately (cancel_is_retriable=False).
        Exception: Re-raised after all retries exhausted.

    Example:
        async def fetch_with_retry(url: str) -> str:
            return await retry_async(
                lambda: _fetch(url),
                max_attempts=3,
                base_delay=1.0,
                on_retry=lambda att, dly, exc: telemetry.retry_inc(),
    )
    """
    attempt = 0
    last_exception: Exception | None = None

    while True:
        try:
            return await coro_fn()
        except asyncio.CancelledError:
            if not cancel_is_retriable:
                raise  # Propagate -- correct for graceful shutdown
            # cancel_is_retriable=True: fall through to retry
            last_exception = asyncio.CancelledError("retry after cancellation")
        except Exception as exc:  # noqa: BLE001
            if not is_retryable(exc, retryable):
                raise
            last_exception = exc
            if attempt >= max_attempts:
                raise

        attempt += 1

        # Compute delay: exponential growth capped at max_delay
        delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)

        if jitter:
            # Decorrelated jitter: +/-jitter_factor of current delay
            delay *= 1.0 + jitter_factor * (2.0 * _random.random() - 1.0)
            delay = max(base_delay * 0.1, delay)  # floor at 10% of base_delay

        delay = min(delay, max_delay)

        if on_retry and last_exception is not None:
            on_retry(attempt, delay, last_exception)

        logger.debug(
            "[RETRY] attempt=%d/%d delay=%.2fs exc=%r",
            attempt,
            max_attempts,
            delay,
            last_exception,
    )

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # Cancelled mid-backoff -- propagate cancellation,
            # NOT the last exception. Cancellation always wins.
            raise



class RetryLoop:
    """Sync iterator for retry loops -- use with `for attempt in RetryLoop():`.

    Generates (attempt_number, delay) pairs. Caller calls break on success.

    Example:
        for attempt, delay in RetryLoop(max_attempts=3, base_delay=0.5):
            try:
                result = sync_operation()
                break
            except Exception as e:
                if not is_retryable(e):
                    raise
                await asyncio.sleep(delay)  # sync context needs event loop
    """

    __slots__ = ("_attempt", "_max_attempts", "_base_delay", "_max_delay", "_jitter", "_jitter_factor", "_backoff_factor", "_exhausted")

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        jitter: bool = True,
        jitter_factor: float = DEFAULT_JITTER_FACTOR,
        backoff_factor: float = 2.0,
    ) -> None:
        self._attempt = 0
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter
        self._jitter_factor = jitter_factor
        self._backoff_factor = backoff_factor
        self._exhausted = False

    def __iter__(self) -> RetryLoop:
        return self

    def __next__(self) -> tuple[int, float]:
        if self._exhausted:
            raise StopIteration

        self._attempt += 1

        if self._attempt > self._max_attempts:
            self._exhausted = True
            raise StopIteration

        delay = min(self._base_delay * (self._backoff_factor ** (self._attempt - 1)), self._max_delay)

        if self._jitter:
            delay *= 1.0 + self._jitter_factor * (2.0 * _random.random() - 1.0)
            delay = max(self._base_delay * 0.1, delay)

        delay = min(delay, self._max_delay)

        return (self._attempt, delay)

    @property
    def attempt(self) -> int:
        """Current attempt number (1-based)."""
        return self._attempt

    @property
    def exhausted(self) -> bool:
        """True if all retry attempts have been exhausted."""
        return self._exhausted or self._attempt >= self._max_attempts



def default_on_retry(attempt: int, delay: float, exc: Exception) -> None:
    """Default retry callback -- emit structured log + telemetry counter."""
    logger.debug("[RETRY] attempt=%d/%d delay=%.2fs exc=%r", attempt, "?", delay, exc)



async def retry_backoff_linear_async(
    coro_fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = 2.0,
    retryable: type[Exception] | tuple[type[Exception], ...] | None = None,
    cancel_is_retriable: bool = False,
) -> T:
    """Linear-backoff variant (no jitter)."""
    return await retry_async(
        coro_fn,
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=False,
        backoff_factor=backoff_factor,
        retryable=retryable,
        cancel_is_retriable=cancel_is_retriable,
    )

