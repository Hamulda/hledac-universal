# hledac/universal/utils/async/_rate_limit.py
# Rate limiting primitives
#
# Provides:
# - BoundedPerHostGate: LRU-bounded per-host concurrency gate
# - DomainRateLimiter: Per-domain token-bucket rate limiter
# - _TokenBucketState: Internal state for token bucket
#
# Invariants:
# - max_hosts cap bounds RAM usage
# - LRU eviction keeps hot hosts resident
# - Fully async: uses asyncio.sleep() not blocking time.sleep()


"""
Rate limiting primitives

Provides:
- BoundedPerHostGate: LRU-bounded per-host concurrency gate
- DomainRateLimiter: Per-domain token-bucket rate limiter
- _TokenBucketState: Internal state for token bucket

Invariants:
- max_hosts cap bounds RAM usage
- LRU eviction keeps hot hosts resident
- Fully async: uses asyncio.sleep() not blocking time.sleep()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.lru_cache import LRUCache

if TYPE_CHECKING:
    pass


class BoundedPerHostGate:
    """
    Bounded per-host concurrency gate with LRU eviction.

    Prevents unbounded growth of per-host Semaphore objects in
    FetchCoordinator when crawling high-diversity URL sets.

    Invariants:
    - max_hosts cap bounds RAM usage (~512 hosts × ~250 B ≈ 128 KB)
    - LRU eviction keeps hot hosts resident
    - Telemetry: evicted / hits / misses counters
    """

    __slots__ = ("_max_hosts", "_per_host_limit", "_gates", "_stats")

    def __init__(self, max_hosts: int = 512, per_host_limit: int = 4) -> None:
        self._max_hosts = max_hosts
        self._per_host_limit = per_host_limit
        self._gates: LRUCache[str, asyncio.Semaphore] = LRUCache(max_size=max_hosts)
        self._stats: dict[str, int] = {"evicted": 0, "hits": 0, "misses": 0}

    def _evict_idle(self) -> None:
        """Evict LRU hosts when over capacity (called lazily on miss).

        Uses OrderedDict LRU ordering: move_to_end() marks recent access,
        popitem(last=False) evicts oldest — both O(1) C-implemented.
        """
        if len(self._gates) < self._max_hosts:
            return
        evict_count = max(1, len(self._gates) - self._max_hosts)
        for _ in range(evict_count):
            self._gates.popitem(last=False)  # O(1) LRU evict
        self._stats["evicted"] += evict_count

    async def acquire(self, host: str) -> tuple[asyncio.Semaphore, str]:
        """
        Acquire a per-host concurrency slot.

        Returns (semaphore_instance, op_id) where op_id is 'hit' or 'miss'.
        The caller MUST pass the returned semaphore to ``release()`` —
        NOT self._gates[host], which may have been evicted and replaced.
        """
        if (sem := self._gates.get(host, None)) is not None:
            self._gates.move_to_end(host)  # O(1) LRU: mark as most-recently-used
            op_id = "hit"
        else:
            self._evict_idle()
            sem = asyncio.Semaphore(self._per_host_limit)
            self._gates[host] = sem
            op_id = "miss"

        await sem.acquire()
        return sem, op_id

    def release(self, sem: asyncio.Semaphore) -> None:
        """
        Release a per-host slot using the instance returned by ``acquire()``.

        Safe against double-release (ValueError is swallowed).
        """
        try:
            sem.release()
        except ValueError:  # noqa: BLE001
            pass  # already released

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot."""
        cache_stats = self._gates.stats
        return {
            "evicted": self._stats["evicted"],
            "hits": cache_stats["hits"],
            "misses": cache_stats["misses"],
            "active_hosts": len(self._gates),
            "max_hosts": self._max_hosts,
        }


@dataclass(slots=True)
class _TokenBucketState:
    """Internal state for a single domain's token bucket."""

    tokens: float
    last_refill: float


class DomainRateLimiter:
    """
    Per-domain token-bucket rate limiter with LRU eviction.

    CB-02 FIX: Adds per-domain rate limiting to prevent OSINT targets
    (crt.sh, certstream, Shodan, etc.) from triggering anti-bot defenses.

    Unlike BoundedPerHostGate which limits concurrency (4 parallel requests),
    DomainRateLimiter limits request rate (e.g. 1 req / 2s = 0.5 RPS).

    Default: 0.5 RPS per domain (1 request every 2 seconds).
    Configurable via HLEDAC_RATE_LIMIT_RPS env var.

    Invariants:
    - max_hosts cap bounds RAM (~512 hosts × ~200 B ≈ 100 KB)
    - LRU eviction keeps hot hosts resident
    - Fully async: uses asyncio.sleep() not blocking time.sleep()
    - Token bucket: capacity = rate (single token = single request)
    """

    __slots__ = (
        "_rate",
        "_capacity",
        "_max_hosts",
        "_buckets",
        "_stats",
        "_lock",
    )

    def __init__(
        self,
        rate: float = 0.5,
        max_hosts: int = 512,
    ) -> None:
        """
        Args:
            rate: Requests per second per domain. Default 0.5 = 1 req / 2s.
            max_hosts: Max tracked domains. Default 512 (LRU eviction above).
        """
        self._rate = rate  # tokens per second
        self._capacity = max(1, int(rate))  # bucket capacity = rate
        self._max_hosts = max_hosts
        self._buckets: LRUCache[str, _TokenBucketState] = LRUCache(max_size=max_hosts)
        self._stats: dict[str, int] = {"evicted": 0, "hits": 0, "misses": 0, "waited": 0}
        self._lock = asyncio.Lock()

    def _evict_idle(self) -> None:
        """Evict LRU hosts when over capacity (called lazily on miss)."""
        if len(self._buckets) < self._max_hosts:
            return
        evict_count = max(1, len(self._buckets) - self._max_hosts)
        for _ in range(evict_count):
            self._buckets.popitem(last=False)
        self._stats["evicted"] += evict_count

    async def acquire(self, host: str) -> float:
        """
        Acquire permission to make a request to the given domain.

        Blocks (via asyncio.sleep) until the domain's token bucket
        has a token available. Returns the wait time in seconds.

        Returns:
            Actual wait time in seconds (0.0 if no wait needed).
        """
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(host, None)
            if bucket is not None:
                self._buckets.move_to_end(host)
                self._stats["hits"] += 1
            else:
                self._evict_idle()
                bucket = _TokenBucketState(tokens=self._capacity, last_refill=now)
                self._buckets[host] = bucket
                self._stats["misses"] += 1

            # Refill tokens based on elapsed time
            elapsed = now - bucket.last_refill
            new_tokens = elapsed * self._rate
            bucket.tokens = min(self._capacity, bucket.tokens + new_tokens)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                # Token available — consume it
                bucket.tokens -= 1.0
                return 0.0
            else:
                # No token — wait for refill
                wait_time = (1.0 - bucket.tokens) / self._rate
                self._stats["waited"] += 1
                bucket.tokens = 0.0  # will be refilled after wait
                # Release lock before sleeping so other domains can proceed
                lock_for_sleep = self._lock
        # Sleep OUTSIDE the lock to allow concurrent domain checks
        await asyncio.sleep(wait_time)
        # Re-acquire lock to update bucket after sleep
        async with lock_for_sleep:
            now = time.monotonic()
            if host in self._buckets:
                bucket = self._buckets[host]
                elapsed = now - bucket.last_refill
                new_tokens = elapsed * self._rate
                bucket.tokens = min(self._capacity, bucket.tokens + new_tokens)
                bucket.last_refill = now
                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
        return wait_time

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot."""
        cache_stats = self._buckets.stats
        return {
            "evicted": self._stats["evicted"],
            "hits": cache_stats["hits"],
            "misses": cache_stats["misses"],
            "waited": self._stats["waited"],
            "active_hosts": len(self._buckets),
            "max_hosts": self._max_hosts,
            "rate": self._rate,
        }


__all__ = [
    "BoundedPerHostGate",
    "DomainRateLimiter",
    "_TokenBucketState",
]
