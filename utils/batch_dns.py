"""
Batch DNS Resolver — bounded LRU + parallel resolve via c-ares
================================================================

Sprint F-A4: Eliminates per-fetch DNS lookup cost in the discovery/fetch
pipeline. 50 URLs with unique hostnames used to cost 50 sequential DNS
round-trips (~5–10 s) because each ``FetchCoordinator._validate_fetch_target``
called ``async_getaddrinfo`` synchronously inside its own coroutine.

Design:
  - Bounded LRU cache (default 1024 hosts, 5 min TTL).
  - Concurrency cap (``asyncio.Semaphore(50)``) to avoid FD exhaustion.
  - ``asyncio.gather(..., return_exceptions=True)`` + ``_check_gathered``
    (invariant I1 / I8) — never raises on per-host failure.
  - Fail-soft: any DNS error → host omitted from result, caller falls
    through to per-fetch DNS via ``_validate_fetch_target``.
  - Singleton accessor ``get_batch_dns_resolver()`` — M1-friendly, one
    resolver instance = one c-ares channel (~5 MB resident).

M1 8 GB safety:
  - LRU bound 1024 hosts × ~100 B per IP list = ~100 KB max.
  - No new heavy deps (uses stdlib ``loop.getaddrinfo`` backed by c-ares).
  - Single resolver instance across the process — no per-call channel alloc.

Cutting edge:
  - OrderedDict LRU gives O(1) get + FIFO eviction when bound reached.
  - ``resolve_many`` extracts unique hosts once, resolves only misses.
  - Cache hits are synchronous (no event-loop yield), so the common-case
    path costs ~O(N) dictionary lookups.

Invariants (CLAUDE.md):
  - Always-on, no toggle (opt-out via env var only).
  - Bounded: cache max + semaphore max.
  - Fail-safe: every error path returns empty/partial result.
  - ``_check_gathered`` enforces CancelledError re-raise (I6),
    BaseException re-raise (I7), Exception route-to-errors (I8).
"""

import asyncio
import logging
import os
import socket
import time
from collections import OrderedDict

from .async_helpers import async_getaddrinfo, safe_gather

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Boundedness constants (M1 8 GB safety)
# ---------------------------------------------------------------------------
DEFAULT_CACHE_MAX = 1024  # Max distinct hostnames in LRU
DEFAULT_TTL_S = 300.0     # 5 min DNS cache TTL
DEFAULT_CONCURRENCY = 50  # Max parallel DNS queries in a single batch
DEFAULT_PER_HOST_TIMEOUT_S = 5.0  # Per-host getaddrinfo timeout

# Opt-out via env var (CLAUDE.md invariant: always-on, but allow disable
# for offline / DNS-blocked environments)
ENV_OPT_OUT = "HLEDAC_BATCH_DNS_DISABLED"

# Sprint F2.3: Common domains for pre-resolution
# Top-level domains commonly seen in OSINT sprints — resolved eagerly
# at startup so the first fetch batch hits the cache. Bounded set
# avoids memory blowup on M1 8GB.
DEFAULT_PREWARM_DOMAINS: tuple[str, ...] = (
    "google.com",
    "googleapis.com",
    "github.com",
    "githubusercontent.com",
    "microsoft.com",
    "cloudflare.com",
    "amazonaws.com",
    "akamai.com",
    "fastly.net",
    "cloudfront.net",
    "facebook.com",
    "twitter.com",
    "reddit.com",
    "stackoverflow.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "dns.google",
    "resolver1.opendns.com",
)


class BatchDNSStats:
    """Bounded telemetry counter snapshot for BatchDNSResolver."""

    __slots__ = (
        "cache_hits",
        "cache_misses",
        "evictions",
        "errors",
        "resolved_total",
        "batch_calls",
    )

    def __init__(self) -> None:
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.evictions: int = 0
        self.errors: int = 0
        self.resolved_total: int = 0
        self.batch_calls: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "evictions": self.evictions,
            "errors": self.errors,
            "resolved_total": self.resolved_total,
            "batch_calls": self.batch_calls,
        }


class BatchDNSResolver:
    """
    Bounded LRU + parallel batch resolver backed by ``loop.getaddrinfo``.

    Single-process singleton: ``get_batch_dns_resolver()``. Reuse the
    same c-ares channel across all fetch batches. Fail-soft on every
    error path — partial results are returned, never raise.
    """

    __slots__ = (
        "_cache",
        "_cache_max",
        "_ttl_s",
        "_semaphore",
        "_semaphore_max",
        "_stats",
        "_lock",
        "_prewarm_done",
    )

    def __init__(
        self,
        max_cache: int = DEFAULT_CACHE_MAX,
        ttl_s: float = DEFAULT_TTL_S,
        max_concurrent: int = DEFAULT_CONCURRENCY,
    ) -> None:
        # OrderedDict: insertion order = LRU order. ``move_to_end`` on
        # cache hit promotes the entry. ``popitem(last=False)`` evicts
        # the oldest entry on overflow. O(1) for all ops.
        self._cache: OrderedDict[str, tuple[list[str], float]] = OrderedDict()
        self._cache_max: int = max(1, int(max_cache))
        self._ttl_s: float = max(0.0, float(ttl_s))
        # Semaphore caps concurrent getaddrinfo calls. M1 default ulimit
        # is 256 FDs; 50 concurrent DNS leaves headroom for HTTP sockets.
        self._semaphore: asyncio.Semaphore | None = None  # lazy in async ctx
        self._semaphore_max: int = max(1, int(max_concurrent))
        self._stats: BatchDNSStats = BatchDNSStats()
        # Guard OrderedDict mutation from concurrent gather tasks.
        # Async gather completes in event loop order, but multiple
        # coroutines may reach the cache-update path interleaved with
        # eviction. Single lock is sufficient — dict ops are O(1).
        self._lock: asyncio.Lock | None = None  # lazy in async ctx
        self._prewarm_done: bool = False  # guard against double prewarm

    # -- lazy async init ---------------------------------------------------

    def _ensure_async_primitives(self) -> None:
        """Lazily allocate async primitives on first async use.

        Avoids binding the resolver to a specific event loop at
        construction time. Lets the resolver be passed across loops
        (rare in this codebase, but cheap to support).
        """
        if self._semaphore is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            self._semaphore = get_semaphore_for_testing(ConcurrencyCategory.DNS_BRUTE)
        if self._lock is None:
            self._lock = asyncio.Lock()

    async def prewarm(
        self,
        domains: tuple[str, ...] | None = None,
        *,
        timeout: float = DEFAULT_PER_HOST_TIMEOUT_S,
    ) -> None:
        """
        Sprint F2.3: Pre-resolve common domains eagerly.

        Call this at sprint startup to populate the LRU cache before
        the first fetch batch arrives. Reduces first-batch DNS latency
        to ~0 ms for common infrastructure domains.

        Idempotent: subsequent calls are no-ops ( guarded by _prewarm_done).

        Args:
            domains: tuple of domain names to pre-resolve. Defaults to
                DEFAULT_PREWARM_DOMAINS (20 common OSINT infrastructure domains).
            timeout: per-host getaddrinfo timeout in seconds.
        """
        if self._prewarm_done or self._is_disabled():
            return

        targets = domains or DEFAULT_PREWARM_DOMAINS
        if not targets:
            return

        self._ensure_async_primitives()
        assert self._semaphore is not None

        # Resolve in background — prewarm is fire-and-forget.
        # Errors are logged but never raise (fail-soft invariant).
        async def _prewarm_host(domain: str) -> None:
            try:
                async with self._semaphore:  # type: ignore[union-attr]
                    raw = await async_getaddrinfo(
                        domain,
                        0,
                        proto=socket.IPPROTO_TCP,
                        timeout=timeout,
                    )
                    ips = sorted({str(r[4][0]) for r in raw})
                    if ips:
                        async with self._lock:  # type: ignore[union-attr]
                            if domain not in self._cache:
                                if (
                                    self._cache_max > 0
                                    and len(self._cache) >= self._cache_max
                                ):
                                    self._cache.popitem(last=False)
                                    self._stats.evictions += 1
                            self._cache[domain] = (ips, time.monotonic())
                            self._stats.resolved_total += 1
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.debug(
                    "[BATCH_DNS] prewarm failed for %s: %s: %s",
                    domain, type(exc).__name__, exc,
                )
                self._stats.errors += 1

        # Fire-and-forget: prewarm runs in background without blocking sprint start.
        await safe_gather(
            *(_prewarm_host(d) for d in targets),
            label="batch_dns_prewarm",
            logger_instance=logger,
        )
        self._prewarm_done = True
        logger.debug(
            "[BATCH_DNS] prewarm complete: %d domains cached",
            len(targets),
        )

    # -- public API --------------------------------------------------------

    async def resolve_many(
        self,
        hosts: list[str],
        *,
        timeout: float = DEFAULT_PER_HOST_TIMEOUT_S,
    ) -> dict[str, list[str]]:
        """
        Resolve a batch of hostnames, returning ``{host: [ip, ...]}``.

        - Cache hits are returned synchronously (no event-loop yield).
        - Cache misses are resolved in parallel under a bounded semaphore.
        - Hosts that fail DNS are omitted from the result (caller's
          fallback path handles them).
        - Duplicate hosts in the input are deduplicated; order of
          iteration does not matter.

        Args:
            hosts: list of hostnames (may contain duplicates / IPs).
            timeout: per-host getaddrinfo timeout in seconds.

        Returns:
            Mapping of hostname → list of resolved IP strings. Hosts
            that fail to resolve are simply absent from the mapping.
            IPv4-literal hosts are returned as a single-element list
            (no DNS lookup) so the caller can treat the result uniformly.
        """
        if not hosts:
            return {}

        if self._is_disabled():
            return {}

        self._ensure_async_primitives()
        assert self._semaphore is not None
        assert self._lock is not None

        self._stats.batch_calls += 1

        # Normalize input: drop empties, strip, dedupe while preserving
        # first-seen order.
        seen: set[str] = set()
        unique_hosts: list[str] = []
        for h in hosts:
            if not h:
                continue
            h_norm = h.strip().lower()
            if h_norm and h_norm not in seen:
                seen.add(h_norm)
                unique_hosts.append(h_norm)

        if not unique_hosts:
            return {}

        # Split into cache hits (synchronous) and misses (async resolve).
        result: dict[str, list[str]] = {}
        now = time.monotonic()
        misses: list[str] = []

        # Cache lookup under lock to avoid race with eviction.
        async with self._lock:
            for host in unique_hosts:
                cached = self._cache.get(host)
                if cached is not None:
                    ips, ts = cached
                    if self._ttl_s <= 0.0 or (now - ts) < self._ttl_s:
                        # Cache hit — promote to most-recently-used.
                        self._cache.move_to_end(host)
                        result[host] = list(ips)  # defensive copy
                        self._stats.cache_hits += 1
                        continue
                    # Expired — drop and re-resolve.
                    del self._cache[host]
                # IPv4/IPv6 literal — short-circuit, no DNS needed.
                try:
                    import ipaddress

                    ipaddress.ip_address(host)
                    result[host] = [host]
                    self._stats.cache_hits += 1
                    continue
                except ValueError:
                    pass
                misses.append(host)
                self._stats.cache_misses += 1

        if not misses:
            return result

        # Parallel resolve for misses, bounded by semaphore.
        # Each host wrapped in a semaphore-guarded coroutine so the
        # gather doesn't fan out unbounded.
        async def _bounded_resolve(host: str) -> tuple[str, list[str]]:
            async with self._semaphore:  # type: ignore[union-attr]
                try:
                    raw = await async_getaddrinfo(
                        host,
                        0,
                        proto=socket.IPPROTO_TCP,
                        timeout=timeout,
                    )
                    ips = sorted({str(r[4][0]) for r in raw})
                    return host, ips
                except Exception as exc:  # noqa: BLE001 — fail-soft invariant
                    logger.debug(
                        "[BATCH_DNS] resolve failed for %s: %s: %s",
                        host, type(exc).__name__, exc,
                    )
                    self._stats.errors += 1
                    return host, []

        # I1 invariant: gather with return_exceptions=True, classified
        # via safe_gather (cancels / BaseException re-raised automatically;
        # Exception routed to .errors).
        gather_result = await safe_gather(
            *(_bounded_resolve(h) for h in misses),
            label="batch_dns_resolve_many",
            logger_instance=logger,
        )
        ok_results = gather_result.ok

        # Update cache with new entries (under lock).
        now = time.monotonic()
        async with self._lock:
            for item in ok_results:
                # Each item is (host, ips) per _bounded_resolve — narrow
                # defensively in case a future refactor changes the shape.
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                host, ips = item
                if not isinstance(host, str) or not isinstance(ips, list):
                    continue
                if not ips:
                    # Don't cache empty results — DNS may succeed on retry.
                    continue
                # Bounded eviction: drop oldest entries when over cap.
                if (
                    self._cache_max > 0
                    and host not in self._cache
                    and len(self._cache) >= self._cache_max
                ):
                    self._cache.popitem(last=False)
                    self._stats.evictions += 1
                self._cache[host] = (list(ips), now)
                result[host] = list(ips)
                self._stats.resolved_total += 1

        return result

    def cache_size(self) -> int:
        """Return current LRU cache size (for tests + telemetry)."""
        return len(self._cache)

    def stats(self) -> dict[str, int]:
        """Return bounded telemetry snapshot."""
        return self._stats.to_dict()

    def reset_stats(self) -> None:
        """Reset telemetry counters (does not clear the cache)."""
        self._stats = BatchDNSStats()

    def clear_cache(self) -> None:
        """Drop all cached entries. Safe to call from sync context."""
        self._cache.clear()

    def is_empty(self) -> bool:
        """Return True if cache is empty."""
        return len(self._cache) == 0

    # -- opt-out ------------------------------------------------------------

    @staticmethod
    def _is_disabled() -> bool:
        """Return True if the env-var opt-out is set."""
        return os.environ.get(ENV_OPT_OUT, "").strip() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_default_resolver: BatchDNSResolver | None = None


def get_batch_dns_resolver() -> BatchDNSResolver:
    """
    Return the process-wide ``BatchDNSResolver`` singleton.

    Single resolver = single c-ares channel = bounded memory. Tests
    that need a fresh instance should call ``reset_batch_dns_resolver()``
    first (or instantiate ``BatchDNSResolver()`` directly).
    """
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = BatchDNSResolver()
    return _default_resolver


def reset_batch_dns_resolver() -> None:
    """
    Drop the singleton (for tests + teardown). The next
    ``get_batch_dns_resolver()`` call instantiates a fresh resolver.
    """
    global _default_resolver
    _default_resolver = None


__all__ = [
    "BatchDNSResolver",
    "BatchDNSStats",
    "DEFAULT_CACHE_MAX",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PER_HOST_TIMEOUT_S",
    "DEFAULT_PREWARM_DOMAINS",
    "DEFAULT_TTL_S",
    "ENV_OPT_OUT",
    "get_batch_dns_resolver",
    "reset_batch_dns_resolver",
]
