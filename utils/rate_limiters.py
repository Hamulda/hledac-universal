"""
Rate limiters — SSOT token-bucket implementations.

Provides async-safe TokenBucket with:
  - asyncio.Lock for thread-safe concurrent access
  - time.monotonic() for interval tracking
  - Gaussian jitter (±15 %)
  - set_rate() for dynamic rate adjustment

Sprint 7A scope: SSOT layer only, no sweeping integration.
"""
import asyncio
import random
import time
QOS_CLASS_USER_INTERACTIVE = 33
QOS_CLASS_USER_INITIATED = 25
QOS_CLASS_UTILITY = 17
QOS_CLASS_BACKGROUND = 9

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
    _DEFAULT_JITTER_SIGMA: float = 0.15
    __slots__ = ('_rate', '_capacity', '_tokens', '_last_refill', '_lock', '_jitter_sigma')

    def __init__(self, rate: float, capacity: float, *, jitter_sigma: float=_DEFAULT_JITTER_SIGMA) -> None:
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

    async def acquire(self, timeout: float | None=None) -> bool:
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
        if self._jitter_sigma > 0.0:
            jitter = random.gauss(0.0, self._jitter_sigma)
            jitter = max(-self._jitter_sigma, min(self._jitter_sigma, jitter))
            wait = base_wait * (1.0 + jitter)
        else:
            wait = base_wait
        return max(0.0, wait)

    @property
    def available_tokens(self) -> float:
        """Return approximate token count (no lock — for monitoring only)."""
        return self._tokens
RATE_LIMITERS: dict[str, TokenBucket] = {'shodan_api': TokenBucket(rate=1.0, capacity=5), 'hibp': TokenBucket(rate=0.5, capacity=3), 'ripe_stat': TokenBucket(rate=2.0, capacity=10), 'crt_sh': TokenBucket(rate=5.0, capacity=20), 'wayback_cdx': TokenBucket(rate=4.0, capacity=15), 'netlas': TokenBucket(rate=1.5, capacity=8), 'fofa': TokenBucket(rate=1.0, capacity=6), 'default': TokenBucket(rate=10.0, capacity=30)}

def get_limiter(name: str) -> TokenBucket:
    """Return a named limiter, falling back to ``default``."""
    return RATE_LIMITERS.get(name, RATE_LIMITERS['default'])
RateLimiter = TokenBucket

class AIMDRateLimiter:
    """
    Additive-Increase Multiplicative-Decrease rate controller.

    Wraps a ``TokenBucket`` and dynamically adjusts its rate based on
    congestion signals (HTTP 429, 403, timeout, etc.).

    Algorithm:
      - on_success()  → rate += additive_step (default 1.0 rps)
      - on_congestion() → after 3 signals → rate *= 0.5 (half the throughput)

    Thread-safe: all operations go through the underlying bucket's lock.

    Usage::

        aimd = AIMDRateLimiter(initial_rps=10.0, domain="shodan_api")
        await aimd.acquire()           # acquire token + refill
        aimd.on_success()              # gradual increase
        aimd.on_congestion()           # halve rate after 3 consecutive signals

    M1 8GB: stateless, <1 KB RAM, no external allocations.
    """
    __slots__ = ('_bucket', '_additive_step', '_congestion_count', '_rps')

    def __init__(self, initial_rps: float=10.0, domain: str='default', *, additive_step: float=1.0) -> None:
        """
        Args:
            initial_rps:  starting requests-per-second for this domain
            domain:       name passed to ``get_limiter`` for the underlying bucket
            additive_step: how many rps to add on each success (default 1.0)
        """
        self._bucket = get_limiter(domain)
        self._additive_step = additive_step
        self._congestion_count: int = 0
        self._rps: float = initial_rps
        self._bucket.set_rate(initial_rps)

    async def acquire(self, timeout: float | None=None) -> bool:
        """Acquire one token from the underlying bucket."""
        return await self._bucket.acquire(timeout=timeout)

    def on_success(self) -> None:
        """
        Record a successful request.

        Rate increases additively so the limiter slowly ramps up after
        congestion clears.
        """
        new_rps = self._rps + self._additive_step
        self._rps = new_rps
        self._bucket.set_rate(new_rps)
        self._congestion_count = 0

    def on_congestion(self) -> None:
        """
        Record a congestion signal (429, 403, timeout, etc.).

        After 3 consecutive signals the rate is halved.  The congestion
        counter is NOT reset here — consecutive congestion events are
        required so a single 429 doesn't cause an immediate drop.
        """
        self._congestion_count += 1
        if self._congestion_count >= 3:
            new_rps = self._rps * 0.5
            self._rps = max(0.5, new_rps)
            self._bucket.set_rate(self._rps)
            self._congestion_count = 0

    @property
    def current_rps(self) -> float:
        """Current configured rps (may be lower than initial after congestion)."""
        return self._rps

    @property
    def congestion_signals(self) -> int:
        """Consecutive congestion signals since last successful request."""
        return self._congestion_count

class RateLimitConfig:
    """
    Backward-compat stub — replaced by AIMDRateLimiter.

    Domain-specific rate limits are now handled by AIMDRateLimiter
    which wraps TokenBucket and adapts rates dynamically.
    """
    __slots__ = tuple(('base_rate', 'burst_size'))

    def __init__(self, base_rate: float=1.0, burst_size: int=5):
        self.base_rate = base_rate
        self.burst_size = burst_size

class RateLimitExceeded(Exception):
    """Backward-compat stub. Rate limiting is now implicit in TokenBucket.acquire()."""
    pass

async def with_rate_limit(coro, _domain: str='default', base_rate: float=1.0):
    """Backward-compat. Execute coroutine with rate limiting."""
    bucket = TokenBucket(rate=base_rate, capacity=int(base_rate * 5))
    await bucket.acquire()
    return await coro
__all__ = ['TokenBucket', 'RATE_LIMITERS', 'get_limiter', 'QOS_CLASS_USER_INTERACTIVE', 'QOS_CLASS_USER_INITIATED', 'QOS_CLASS_UTILITY', 'QOS_CLASS_BACKGROUND', 'RateLimiter', 'RateLimitConfig', 'RateLimitExceeded', 'with_rate_limit', 'AIMDRateLimiter']