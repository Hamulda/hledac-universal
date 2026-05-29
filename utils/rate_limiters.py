"""
Rate limiters — SSOT token-bucket implementations.

Provides async-safe TokenBucket with:
  - asyncio.Lock for thread-safe concurrent access
  - time.monotonic() for interval tracking
  - Gaussian jitter (±15 %)
  - set_rate() for dynamic rate adjustment

Sprint 7A scope: SSOT layer only, no sweeping integration.
"""

from __future__ import annotations

import asyncio
import random
import time

# QoS class constants (Darwin / macOS)
QOS_CLASS_USER_INTERACTIVE = 0x21
QOS_CLASS_USER_INITIATED = 0x19
QOS_CLASS_UTILITY = 0x11
QOS_CLASS_BACKGROUND = 0x09


# =============================================================================
# TokenBucket — async-safe, SSOT
# =============================================================================


class TokenBucket:
    """
    Async-safe token bucket with Gaussian jitter and dynamic rate.

    Internally holds ``asyncio.Lock`` — only one caller acquires at a time.

    Jitter: ±15 % of wait time, Gaussian (normal) distribution.

    Usage::

        bucket = TokenBucket(rate=10.0, capacity=20)
        await bucket.acquire()        # blocks until token available
        await bucket.acquire(domain="shodan")  # domain-aware (capacity shared)
    """

    _DEFAULT_JITTER_SIGMA: float = 0.15  # ±15 % sigma for Gaussian jitter

    __slots__ = ("_rate", "_capacity", "_tokens", "_last_refill", "_lock", "_jitter_sigma")

    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        jitter_sigma: float = _DEFAULT_JITTER_SIGMA,
    ) -> None:
        """
        Args:
            rate:       tokens per second (refill rate)
            capacity:   max tokens in bucket (burst size)
            jitter_sigma: Gaussian sigma as fraction of wait time (default 0.15 = ±15 %)
        """
        self._rate: float = rate
        self._capacity: float = capacity
        self._tokens: float = float(capacity)
        self._last_refill: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._jitter_sigma: float = jitter_sigma

    def set_rate(self, rate: float) -> None:
        """Dynamically change the refill rate. Thread-safe."""
        self._rate = max(0.0, rate)

    async def acquire(self, timeout: float | None = None) -> bool:
        """
        Acquire one token, waiting if necessary.

        Args:
            timeout: max seconds to wait (None = wait forever)

        Returns:
            True if token acquired, False if timed out.
        """
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
                    wait = min(remaining, self._compute_wait())
                else:
                    wait = self._compute_wait()

                await asyncio.sleep(wait)

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def _compute_wait(self) -> float:
        """Compute wait time for one token, with Gaussian jitter."""
        if self._rate <= 0.0:
            base_wait = 1.0
        else:
            base_wait = (1.0 - self._tokens) / self._rate

        # Gaussian jitter: sample from Normal(0, sigma) and clamp to ±1 sigma range
        if self._jitter_sigma > 0.0:
            jitter = random.gauss(0.0, self._jitter_sigma)
            # clamp to [-0.15, +0.15] range (≈ ±1 sigma for 0.15)
            jitter = max(-self._jitter_sigma, min(self._jitter_sigma, jitter))
            wait = base_wait * (1.0 + jitter)
        else:
            wait = base_wait

        return max(0.0, wait)

    @property
    def available_tokens(self) -> float:
        """Return approximate token count (no lock — for monitoring only)."""
        return self._tokens


# =============================================================================
# RATE_LIMITERS — SSOT map
# =============================================================================

RATE_LIMITERS: dict[str, TokenBucket] = {
    "shodan_api":    TokenBucket(rate=1.0,  capacity=5),
    "hibp":          TokenBucket(rate=0.5,  capacity=3),
    "ripe_stat":     TokenBucket(rate=2.0,  capacity=10),
    "crt_sh":        TokenBucket(rate=5.0,  capacity=20),
    "wayback_cdx":   TokenBucket(rate=4.0,  capacity=15),
    "netlas":        TokenBucket(rate=1.5,  capacity=8),
    "fofa":          TokenBucket(rate=1.0,  capacity=6),
    "default":       TokenBucket(rate=10.0, capacity=30),
}


def get_limiter(name: str) -> TokenBucket:
    """Return a named limiter, falling back to ``default``."""
    return RATE_LIMITERS.get(name, RATE_LIMITERS["default"])


# =============================================================================
# Backward-compat aliases (Sprint 7C — shim layer)
# These allow old code importing from rate_limiter.py to keep working
# =============================================================================

#: Alias for backward compat
RateLimiter = TokenBucket

#: Backward-compat placeholder (old domain-specific RateLimitConfig)
class RateLimitConfig:
    """Backward-compat stub. Domain-specific limits handled by TokenBucket."""
    def __init__(self, base_rate: float = 1.0, burst_size: int = 5):
        pass

#: Backward-compat placeholder
class RateLimitExceeded(Exception):
    """Backward-compat stub. Rate limiting is now implicit in TokenBucket.acquire()."""
    pass

#: Backward-compat coroutine helper
async def with_rate_limit(coro, domain: str = 'default', base_rate: float = 1.0):
    """Backward-compat. Execute coroutine with rate limiting."""
    bucket = TokenBucket(rate=base_rate, capacity=int(base_rate * 5))
    await bucket.acquire()
    return await coro


__all__ = [
    "TokenBucket",
    "RATE_LIMITERS",
    "get_limiter",
    "QOS_CLASS_USER_INTERACTIVE",
    "QOS_CLASS_USER_INITIATED",
    "QOS_CLASS_UTILITY",
    "QOS_CLASS_BACKGROUND",
    # backward compat
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceeded",
    "with_rate_limit",
]
