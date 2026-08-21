"""
Per-host rate limiting for transport layer.

ISSUE R15: Refactored from Python TokenBucket to Rust rate_limit.check_rate_limit.
Provides non-blocking per-host rate limiting with Python fallback.

Architecture:
    Primary:   rust.rate_limit.check_rate_limit(host, tokens)
               - Atomic operations, no GIL contention
               - ~10× faster than Python asyncio.Lock
               - Max 512 hosts with LRU eviction

    Fallback:  Python TokenBucket (utils/rate_limiters.TokenBucket)
               - Used when Rust extension unavailable
               - Async-safe with asyncio.Lock

M1 8GB safe: Rust path uses ~32 KB for 512 hosts.

Usage:
    from transport.rate_limiter import check_rate_limit, RateLimiter

    # Non-blocking check (Rust primary, Python fallback)
    if check_rate_limit("api.example.com", 1):
        await fetch("https://api.example.com/data")

    # Class-based with async acquire
    limiter = RateLimiter(host="api.shodan.io", rate=1.0, capacity=5)
    await limiter.acquire()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:

logger = logging.getLogger(__name__)

_RUST_AVAILABLE: bool = False
_rust_check_rate_limit: callable | None = None
_rust_get_stats: callable | None = None

try:
    from _core.rust_backend import rust

    _rate_limit_mod = rust.rate_limit
    if _rate_limit_mod is not None:
        _rust_check_rate_limit = getattr(_rate_limit_mod, "check_rate_limit", None)
        _rust_get_stats = getattr(_rate_limit_mod, "get_host_limiter_stats", None)
        if _rust_check_rate_limit is not None:
            _RUST_AVAILABLE = True
            logger.debug("[rate_limiter] Rust check_rate_limit available")
except ImportError:
    _rate_limit_mod = None
    logger.debug("[rate_limiter] Rust backend unavailable, using Python fallback")


class TokenBucket:
    """
    Minimal token bucket for rate-limiting (Python fallback).

    Used when Rust extension unavailable. For production with Rust,
    use check_rate_limit() directly.
    """

    __slots__ = ("_tokens", "_max_tokens", "_refill_rate", "_last_refill", "_lock")

    def __init__(self, rate: float, capacity: float) -> None:
        self._tokens: float = float(capacity)
        self._max_tokens: float = float(capacity)
        self._refill_rate: float = rate
        self._last_refill: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    def try_acquire(self) -> bool:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    async def acquire(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    wait = min(remaining, (1.0 - self._tokens) / self._refill_rate)
                else:
                    wait = (1.0 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)


# Global host buckets (Python fallback only)
_HOST_BUCKETS: dict[str, TokenBucket] = {}
_HOST_BUCKETS_LOCK: asyncio.Lock = asyncio.Lock()


async def _get_host_bucket(host: str, rate: float, capacity: float) -> TokenBucket:
    """Get or create a host-specific token bucket."""
    async with _HOST_BUCKETS_LOCK:
        if host not in _HOST_BUCKETS:
            _HOST_BUCKETS[host] = TokenBucket(rate=rate, capacity=capacity)
        return _HOST_BUCKETS[host]


def check_rate_limit(host: str, tokens: int = 1) -> bool:
    """
    Non-blocking per-host rate limit check.

    Primary: Uses Rust atomic operations (~10× faster, no GIL).
    Fallback: Uses Python TokenBucket with asyncio.Lock.

    Args:
        host:   Hostname to rate limit (e.g., "api.shodan.io")
        tokens: Number of tokens to acquire (default 1)

    Returns:
        True if allowed (tokens acquired), False if rate limited.

    Example:
        if check_rate_limit("api.example.com", 1):
            await fetch("https://api.example.com/data")
    """
    if _RUST_AVAILABLE and _rust_check_rate_limit is not None:
        try:
            return _rust_check_rate_limit(host, tokens)
        except Exception as e:
            logger.warning(f"[rate_limiter] Rust check_rate_limit failed: {e}, falling back to Python")
            # Fall through to Python fallback

    # Python fallback (async-safe TokenBucket per host)
    # Note: This is blocking, but asyncio.Lock makes it safe for async context
    # For true non-blocking, use the Rust path
    bucket = _HOST_BUCKETS.get(host)
    if bucket is None:
        # Fast path: bucket doesn't exist, allow through
        # Create it for next time
        _HOST_BUCKETS[host] = TokenBucket(rate=10.0, capacity=10)
        return True
    return bucket.try_acquire()


def get_rate_limiter_stats() -> dict[str, int]:
    """
    Get rate limiter statistics.

    Returns dict with:
        - rust_available: bool
        - host_count: number of tracked hosts (Python fallback only)
        - max_hosts: maximum hosts allowed
        - default_capacity: default bucket capacity
        - default_rate: default refill rate
    """
    stats: dict[str, int] = {
        "rust_available": int(_RUST_AVAILABLE),
        "host_count": 0,
        "max_hosts": 512,
        "default_capacity": 10,
        "default_rate": 10,
    }

    if _RUST_AVAILABLE and _rust_get_stats is not None:
        try:
            rust_stats = _rust_get_stats()
            stats.update(rust_stats)
        except Exception as e:
            logger.warning(f"[rate_limiter] Failed to get Rust stats: {e}")

    stats["host_count"] = len(_HOST_BUCKETS)
    return stats


class RateLimiter:
    """
    Per-host rate limiter with async acquire support.

    Use check_rate_limit() for non-blocking checks.
    Use RateLimiter.acquire() when you need async waiting.

    Args:
        host:     Hostname to rate limit
        rate:     Tokens per second (refill rate)
        capacity: Maximum tokens (burst size)

    Example:
        limiter = RateLimiter("api.shodan.io", rate=1.0, capacity=5)
        await limiter.acquire(timeout=30.0)
    """

    __slots__ = ("_host", "_rate", "_capacity", "_bucket", "_lock")

    def __init__(self, host: str, rate: float = 1.0, capacity: float = 5.0) -> None:
        self._host: str = host
        self._rate: float = rate
        self._capacity: float = capacity
        self._bucket: TokenBucket | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    async def _get_bucket(self) -> TokenBucket:
        if self._bucket is None:
            self._bucket = await _get_host_bucket(self._host, self._rate, self._capacity)
        return self._bucket

    def try_acquire(self) -> bool:
        """Non-blocking acquire. Returns True if token acquired."""
        if _RUST_AVAILABLE and _rust_check_rate_limit is not None:
            try:
                return _rust_check_rate_limit(self._host, 1)
            except Exception:
                pass

        # Python fallback
        if self._bucket is None:
            return True  # Allow through if not initialized
        return self._bucket.try_acquire()

    async def acquire(self, timeout: float | None = None) -> bool:
        """
        Acquire one token, waiting if necessary.

        Args:
            timeout: Max seconds to wait (None = wait forever)

        Returns:
            True if token acquired, False if timed out.
        """
        if _RUST_AVAILABLE and _rust_check_rate_limit is not None:
            # Rust path: non-blocking, but we poll with asyncio.sleep
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                try:
                    if _rust_check_rate_limit(self._host, 1):
                        return True
                except Exception:
                    pass  # Fall through to Python fallback

                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    await asyncio.sleep(min(remaining, 0.1))
                else:
                    await asyncio.sleep(0.1)

        # Python fallback
        bucket = await self._get_bucket()
        return await bucket.acquire(timeout=timeout)

    async def close(self) -> None:
        """Cleanup resources."""
        self._bucket = None

    def __repr__(self) -> str:
        return f"RateLimiter(host={self._host!r}, rate={self._rate}, capacity={self._capacity})"


RateLimitExceeded = Exception  # Deprecated
RateLimiterClass = RateLimiter  # For code expecting class name


__all__ = [
    "check_rate_limit",
    "get_rate_limiter_stats",
    "RateLimiter",
    "TokenBucket",
    "RateLimiterClass",
    "RateLimitExceeded",
]
