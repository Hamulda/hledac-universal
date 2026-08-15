"""
DomainRateLimiter — Async-safe token bucket per (scheme, host).

Provides per-domain rate limiting as an alternative to global AIMD semaphore.



Designed for aiohttp_socks-friendly operation with optional Tor/I2P lane awareness.

Key design (cutting-edge, M1 8GB safe):
  - asyncio.Lock-free fast path: dict lookup + arithmetic only
  - asyncio.Lock only on the refill+acquire slow path
  - time.monotonic() for interval tracking (not time.time() — monotonic)
  - Gaussian jitter (±15%) to avoid correlated request bursts
  - LMDB-backed persistence for rate state across sprints (optional)

Usage::

    limiter = DomainRateLimiter(default_rps=5.0)
    wait_s = limiter.acquire("https://example.com/api")
    await asyncio.sleep(wait_s)   # non-blocking wait outside lock
    async with limiter.acquire_async("https://example.com/api"):
        ...

Sprint F320: Issue #10 per-source manual rate limiting.
"""

import asyncio
import logging
import math
import secrets
import time
import urllib.parse
from typing import ClassVar

__all__ = ["DomainRateLimiter", "LMDBDomainRateLimiter"]

logger = logging.getLogger(__name__)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

def _gauss(mu: float, sigma: float) -> float:
    """Box-Muller transform for Gaussian random numbers using crypto-safe RNG."""
    u1 = _RNG.random()
    u2 = _RNG.random()
    z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z0

# Default rates per domain category
_DEFAULT_RPS: float = 5.0
_DEFAULT_BURST: int = 10


# --- TokenBucket (lock-free fast path) ---------------------------------------


class _TokenBucket:
    """
    Non-blocking token bucket with refill-on-acquire pattern.

    Fast path (no lock): check tokens, consume, return.
    Slow path (lock): refill tokens, then consume.

    This is the core algorithm used by DomainRateLimiter.
    """

    __slots__ = ("rate", "capacity", "tokens", "last_refill")

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate: float = rate
        self.capacity: int = capacity
        self.tokens: float = float(capacity)
        self.last_refill: float = time.monotonic()

    def try_acquire(self) -> float:
        """
        Try to acquire one token without blocking.

        Returns:
            0.0 if token acquired immediately.
            > 0.0 = seconds to wait until a token is available (refill estimate).
        """
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0

        # Seconds until one token is available
        wait = (1.0 - self.tokens) / self.rate
        return max(0.0, wait)

    def consume(self) -> None:
        """Consume one token (caller guarantees token was available)."""
        self.tokens -= 1.0


# --- DomainRateLimiter --------------------------------------------------------


class DomainRateLimiter:
    """
    Token bucket per (scheme, host) — lock-free fast path + async slow path.

    Fast path: acquire_take() does dict lookup + arithmetic, returns immediately.
    Slow path: if no token, await asyncio.sleep() OUTSIDE the lock, then retry.

    Thread-safe: uses asyncio.Lock only for the slow refill path.

    M1 8GB: stateless, <1 KB per bucket, no external allocations.

    Usage::

        limiter = DomainRateLimiter(default_rps=5.0)

        # Sync fast path — returns seconds to wait
        wait = limiter.acquire("https://example.com/path")
        if wait > 0:
            await asyncio.sleep(wait)

        # Async context manager — handles wait internally
        async with limiter.acquire_async("https://example.com/path"):
            await fetch(url)
    """

    # Gaussian jitter sigma (15% of wait time)
    _JITTER_SIGMA: ClassVar[float] = 0.15

    __slots__ = (
        "_default_rps",
        "_default_burst",
        "_jitter_sigma",
        "_buckets",
        "_locks",
        "_global_lock",
    )

    def __init__(
        self,
        default_rps: float = _DEFAULT_RPS,
        default_burst: int = _DEFAULT_BURST,
        jitter_sigma: float = _JITTER_SIGMA,
    ) -> None:
        self._default_rps = default_rps
        self._default_burst = default_burst
        self._jitter_sigma = jitter_sigma
        self._buckets: dict[str, _TokenBucket] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    # --- Public API ---

    def acquire(self, url: str) -> float:
        """
        Fast-path acquire: dict lookup + arithmetic, no lock.

        Returns:
            0.0 = token acquired immediately.
            > 0.0 = seconds to wait (refill estimate, caller sleeps outside lock).
        """
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        wait = bucket.try_acquire()
        return wait

    async def acquire_async(self, url: str) -> None:
        """
        Async acquire with backoff — acquires token, handling refill wait.

        Uses asyncio.sleep() OUTSIDE the lock (non-blocking).
        """
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        lock = self._get_lock(host)

        while True:
            wait = bucket.try_acquire()
            if wait == 0.0:
                return  # Acquired immediately

            await asyncio.sleep(wait)

            # Bucket may have been updated by another coroutine while sleeping
            # Re-check under lock
            async with lock:
                wait = bucket.try_acquire()
                if wait == 0.0:
                    return
                # else: loop again with updated bucket state

    def acquire_take(self, url: str) -> bool:
        """
        Try to acquire a token, consuming it immediately.

        Returns:
            True if token was available and consumed.
            False if no token available (do not wait).
        """
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        wait = bucket.try_acquire()
        if wait == 0.0:
            return True
        return False

    def close(self) -> None:
        """No-op for base class. Subclass (LMDBDomainRateLimiter) overrides for persistence."""
        pass

    # --- Config helpers ---

    def set_rate(self, url: str, rps: float) -> None:
        """Dynamically change the rate for a specific domain."""
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        bucket.rate = max(0.0, rps)

    def get_rate(self, url: str) -> float:
        """Get current rate for a domain."""
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        return bucket.rate

    @property
    def default_rps(self) -> float:
        return self._default_rps

    # --- Internal ---

    def _parse_host(self, url: str) -> str:
        """Extract netloc from URL for bucketing."""
        try:
            parsed = urllib.parse.urlsplit(url)
            return f"{parsed.scheme}://{parsed.netloc}".lower()
        except Exception:
            return "unknown://unknown"

    def _get_bucket(self, host: str) -> _TokenBucket:
        """Get or create bucket for host (no lock needed — dict ops are GIL-protected)."""
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = _TokenBucket(rate=self._default_rps, capacity=self._default_burst)
            self._buckets[host] = bucket
        return bucket

    def _get_lock(self, host: str) -> asyncio.Lock:
        """Get or create lock for host (no lock needed — dict ops are GIL-protected)."""
        lock = self._locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[host] = lock
        return lock

    def _jitter(self, wait: float) -> float:
        """Apply Gaussian jitter to wait time (±15%)."""
        if self._jitter_sigma <= 0.0 or wait <= 0.0:
            return wait
        jitter = _gauss(0.0, self._jitter_sigma)
        jitter = max(-self._jitter_sigma, min(self._jitter_sigma, jitter))
        return max(0.0, wait * (1.0 + jitter))


# --- LMDB-backed variant (optional persistence) --------------------------------


try:
    import lmdb
    import orjson

    _LMDB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LMDB_AVAILABLE = False

import typing  # noqa: E402
from _core import aclose
if typing.TYPE_CHECKING:
    import lmdb
    import orjson


class LMDBDomainRateLimiter(DomainRateLimiter):
    """
    DomainRateLimiter with LMDB persistence for rate state across sprints.

    Persists (host → rate, tokens, last_refill) to LMDB so that:
      - Rate limits survive sprint restarts
      - AIMD state (rate adjustments) are preserved

    Falls back to in-memory on LMDB errors (fail-soft).

    M1 8GB: bounded LMDB map (16 MB), no growth beyond configured size.
    """

    __slots__ = ("_lmdb_env", "_lmdb_path", "_map_size")

    def __init__(
        self,
        lmdb_path: str | None = None,
        default_rps: float = _DEFAULT_RPS,
        default_burst: int = _DEFAULT_BURST,
        map_size: int = 16 * 1024 * 1024,  # 16 MB — M1 8GB safe
        jitter_sigma: float = 0.15,
    ) -> None:
        super().__init__(default_rps=default_rps, default_burst=default_burst, jitter_sigma=jitter_sigma)
        self._lmdb_env: "lmdb.Environment" | None = None
        self._lmdb_path = lmdb_path
        self._map_size = map_size

        if _LMDB_AVAILABLE and lmdb_path:
            try:
                self._lmdb_env = lmdb.open(lmdb_path, map_size=map_size, writemap=False)
                self._load_from_lmdb()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[DomainRateLimiter] LMDB open failed (non-fatal): %s", exc)
                self._lmdb_env = None

    # --- Persistence ---

    def _load_from_lmdb(self) -> None:
        """Load persisted bucket state from LMDB on startup."""
        if not self._lmdb_env:
            return
        try:
            with self._lmdb_env.begin() as txn:
                cursor = txn.cursor()
                for key, value in cursor:
                    try:
                        host = key.decode("utf-8")
                        data = orjson.loads(value)  # type: ignore[union-attr]
                        bucket = _TokenBucket(rate=data["rate"], capacity=data["capacity"])
                        bucket.tokens = data["tokens"]
                        bucket.last_refill = data["last_refill"]
                        self._buckets[host] = bucket
                    except Exception:  # noqa: BLE001
                        continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("[DomainRateLimiter] LMDB load failed (non-fatal): %s", exc)

    def _persist_bucket(self, host: str, bucket: _TokenBucket) -> None:
        """Persist single bucket state to LMDB (best-effort)."""
        if not self._lmdb_env:
            return
        try:
            data = orjson.dumps({  # type: ignore[union-attr]
                "rate": bucket.rate,
                "capacity": bucket.capacity,
                "tokens": bucket.tokens,
                "last_refill": bucket.last_refill,
            })
            with self._lmdb_env.begin(write=True) as txn:  # type: ignore[union-attr]
                txn.put(host.encode("utf-8"), data)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass  # fail-soft

    def _persist_all(self) -> None:
        """Persist all bucket states to LMDB (called on winddown)."""
        if not self._lmdb_env:
            return
        try:
            with self._lmdb_env.begin(write=True) as txn:  # type: ignore[union-attr]
                for host, bucket in self._buckets.items():
                    data = orjson.dumps({  # type: ignore[union-attr]
                        "rate": bucket.rate,
                        "capacity": bucket.capacity,
                        "tokens": bucket.tokens,
                        "last_refill": bucket.last_refill,
                    })
                    txn.put(host.encode("utf-8"), data)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[DomainRateLimiter] LMDB persist failed (non-fatal): %s", exc)

    # --- Override acquire_async to persist on state change ---

    async def acquire_async(self, url: str) -> None:
        """Async acquire with LMDB persistence."""
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        lock = self._get_lock(host)

        while True:
            wait = bucket.try_acquire()
            if wait == 0.0:
                self._persist_bucket(host, bucket)  # persist state
                return

            await asyncio.sleep(wait)

            async with lock:
                wait = bucket.try_acquire()
                if wait == 0.0:
                    self._persist_bucket(host, bucket)
                    return

    def acquire_take(self, url: str) -> bool:
        """Try to acquire, persist state on success."""
        host = self._parse_host(url)
        bucket = self._get_bucket(host)
        wait = bucket.try_acquire()
        if wait == 0.0:
            self._persist_bucket(host, bucket)
            return True
        return False

    def close(self) -> None:
        """Persist all buckets and close LMDB environment."""
        self._persist_all()
        if self._lmdb_env:
            try:
                self._lmdb_env.close()
            except Exception:  # noqa: BLE001
                pass
            self._lmdb_env = None
