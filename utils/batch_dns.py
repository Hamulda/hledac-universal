"""
Batch DNS Resolver — bounded LRU + negative cache + aiodns optional backend
============================================================================

Sprint F-A4: Eliminates per-fetch DNS lookup cost in the discovery/fetch
pipeline. 50 URLs with unique hostnames used to cost 50 sequential DNS
round-trips (~5–10 s) because each ``FetchCoordinator._validate_fetch_target``
called ``async_getaddrinfo`` synchronously inside its own coroutine.

F-A4.1 Extensions (Issue #7):
  - Negative caching: NXDOMAIN/SERVFAIL responses cached for 30s to avoid
    repeated failed lookups within a sprint batch.
  - aiodns backend: when available (pycares), uses c-ares connection pooling
    for 2-5× faster parallel queries vs stdlib loop.getaddrinfo.
  - Bounded negative cache: max 256 entries, LRU eviction.

Design:
  - Bounded LRU cache (default 1024 hosts, 5 min TTL for positive results).
  - Concurrency cap (``asyncio.Semaphore(50)``) to avoid FD exhaustion.
  - ``asyncio.gather(..., return_exceptions=True)`` + ``_check_gathered``
    (invariant I1 / I8) — never raises on per-host failure.
  - Fail-soft: any DNS error → host omitted from result, caller falls
    through to per-fetch DNS via ``_validate_fetch_target``.
  - Singleton accessor ``get_batch_dns_resolver()`` — M1-friendly, one
    resolver instance = one c-ares channel (~5 MB resident).

M1 8 GB safety:
  - LRU bound 1024 hosts × ~100 B per IP list = ~100 KB max.
  - Negative cache bound 256 × ~50 B = ~12 KB max.
  - No new heavy deps by default (uses stdlib ``loop.getaddrinfo`` backed
    by c-ares on macOS). aiodns is optional and lazy-imported.
  - Single resolver instance across the process — no per-call channel alloc.

Cutting edge:
  - OrderedDict LRU gives O(1) get + FIFO eviction when bound reached.
  - ``resolve_many`` extracts unique hosts once, resolves only misses.
  - Cache hits are synchronous (no event-loop yield), so the common-case
    path costs ~O(N) dictionary lookups.
  - aiodns connection multiplexing reduces DNS round-trips for multiple
    queries to the same nameserver.

Invariants (CLAUDE.md):
  - Always-on, no toggle (opt-out via env var only).
  - Bounded: cache max + semaphore max + negative cache max.
  - Fail-safe: every error path returns empty/partial result.
  - ``_check_gathered`` enforces CancelledError re-raise (I6),
    BaseException re-raise (I7), Exception route-to-errors (I8).
"""

from typing import TYPE_CHECKING, Any

import asyncio
import logging
import os
import socket
import time
from collections import OrderedDict

from .async_helpers import async_getaddrinfo, safe_gather, safe_wait_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Boundedness constants (M1 8 GB safety)
# ---------------------------------------------------------------------------
DEFAULT_CACHE_MAX = 1024  # Max distinct hostnames in LRU (positive results)
DEFAULT_NEG_CACHE_MAX = 256  # Max entries in negative cache (NXDOMAIN/SERVFAIL)
DEFAULT_TTL_S = 300.0  # 5 min DNS cache TTL for positive results
DEFAULT_NEG_TTL_S = 30.0  # 30s TTL for negative results (NXDOMAIN/SERVFAIL)
DEFAULT_CONCURRENCY = 50  # Max parallel DNS queries in a single batch
DEFAULT_PER_HOST_TIMEOUT_S = 5.0  # Per-host getaddrinfo timeout

# Opt-out via env var (CLAUDE.md invariant: always-on, but allow disable
# for offline / DNS-blocked environments)
ENV_OPT_OUT = "HLEDAC_BATCH_DNS_DISABLED"

# aiodns REMOVED ISSUE-008: no longer a project dep.
# Fallback path via loop.getaddrinfo() (stdlib) always available.
# Manual install if needed: uv add aiodns
HAS_AIODNS = False
aiodns: Any = None  # type: ignore[no-redef]

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
        "neg_cache_hits",
        "neg_cache_misses",
        "evictions",
        "neg_evictions",
        "errors",
        "resolved_total",
        "neg_cached_total",
        "batch_calls",
        "aiodns_used",
    )

    def __init__(self) -> None:
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.neg_cache_hits: int = 0
        self.neg_cache_misses: int = 0
        self.evictions: int = 0
        self.neg_evictions: int = 0
        self.errors: int = 0
        self.resolved_total: int = 0
        self.neg_cached_total: int = 0
        self.batch_calls: int = 0
        self.aiodns_used: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "neg_cache_hits": self.neg_cache_hits,
            "neg_cache_misses": self.neg_cache_misses,
            "evictions": self.evictions,
            "neg_evictions": self.neg_evictions,
            "errors": self.errors,
            "resolved_total": self.resolved_total,
            "neg_cached_total": self.neg_cached_total,
            "batch_calls": self.batch_calls,
            "aiodns_used": self.aiodns_used,
        }


class _AiodnsResolver:
    """
    Optional c-ares backed DNS resolver using aiodns.

    Provides connection pooling and multiplexing for faster parallel DNS queries.
    Falls back gracefully when aiodns is unavailable.
    """

    __slots__ = ("_resolver", "_loop")

    def __init__(self) -> None:
        if not HAS_AIODNS:
            raise ImportError("aiodns not available")
        self._loop = asyncio.get_running_loop()
        resolver_class = aiodns.DNSResolver  # type: ignore[attr-defined]
        self._resolver = resolver_class(loop=self._loop)

    async def resolve(self, hostname: str, timeout: float) -> list[str]:
        """
        Resolve hostname using c-ares (aiodns).

        Returns IPv4 addresses sorted and deduplicated.
        Raises on failure (caller handles exceptions).
        """
        try:
            # aiodns 4.x: result.addresses is list[str], not list[object with .host]
            result = await safe_wait_for(
                self._resolver.gethostbyname(hostname, socket.AF_INET),
                timeout=timeout, label="aiodns_resolve",
            )
            if result.addresses:
                # aiodns 4.x: addresses are already strings
                return sorted(set(str(addr) for addr in result.addresses))
            return []
        except (asyncio.TimeoutError, OSError, Exception) as exc:
            logger.debug(
                "[BATCH_DNS] aiodns failed for %s: %s: %s",
                hostname, type(exc).__name__, exc,
            )
            raise


def _is_dns_negative_error(exc: Exception) -> bool:
    """Return True if exception represents a DNS negative response (NXDOMAIN/SERVFAIL)."""
    if isinstance(exc, socket.gaierror):
        # gaierror(ai_dns_err_code_t) — common codes:
        # EAI_NONAME = 8 (host not found)
        # EAI_NODATA = 7 (no AAAA record but name exists)
        # EAI_FAIL = 4 (name server failure)
        # EAI_SERVERFAIL = 2 (server failed)
        code = getattr(exc, "errno", None) or getattr(exc, "args", (0,))[0]
        # On macOS, gaierror codes differ from Linux. Check for common patterns.
        code_str = str(code).lower() if isinstance(code, (int, str)) else ""
        negative_codes = {"nodata", "noname", "fail", "serverfail", "no_recovery"}
        if any(c in code_str for c in negative_codes):
            return True
        # Check message content as fallback
        msg = str(exc).lower()
        negative_keywords = (
            "not found", "no data", "nodata", "nxdomain",
            "server fail", "servfail", "no recovery",
        )
        return any(kw in msg for kw in negative_keywords)
    # aiodns raises various OSError subclasses
    if isinstance(exc, OSError) and (
        "name or service not known" in str(exc).lower()
        or "dns" in str(exc).lower()
    ):
        return True
    return False


class BatchDNSResolver:
    """
    Bounded LRU + negative cache + optional aiodns backend.

    Single-process singleton: ``get_batch_dns_resolver()``. Reuse the
    same c-ares channel across all fetch batches. Fail-soft on every
    error path — partial results are returned, never raise.
    """

    __slots__ = (
        "_cache",  # positive results: host -> (ips: list[str], timestamp: float)
        "_neg_cache",  # negative results: host -> timestamp (NXDOMAIN/SERVFAIL)
        "_cache_max",
        "_neg_cache_max",
        "_ttl_s",
        "_neg_ttl_s",
        "_semaphore",
        "_semaphore_max",
        "_stats",
        "_lock",
        "_prewarm_done",
        "_aiodns_resolver",
    )

    def __init__(
        self,
        max_cache: int = DEFAULT_CACHE_MAX,
        neg_cache_max: int = DEFAULT_NEG_CACHE_MAX,
        ttl_s: float = DEFAULT_TTL_S,
        neg_ttl_s: float = DEFAULT_NEG_TTL_S,
        max_concurrent: int = DEFAULT_CONCURRENCY,
    ) -> None:
        # OrderedDict: insertion order = LRU order. ``move_to_end`` on
        # cache hit promotes the entry. ``popitem(last=False)`` evicts
        # the oldest entry on overflow. O(1) for all ops.
        self._cache: OrderedDict[str, tuple[list[str], float]] = OrderedDict()
        # Negative cache: host -> timestamp (time.monotonic() at cache time)
        self._neg_cache: OrderedDict[str, float] = OrderedDict()
        self._cache_max: int = max(1, int(max_cache))
        self._neg_cache_max: int = max(1, int(neg_cache_max))
        self._ttl_s: float = max(0.0, float(ttl_s))
        self._neg_ttl_s: float = max(0.0, float(neg_ttl_s))
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
        self._aiodns_resolver: _AiodnsResolver | None = None  # lazy, optional

    # -- lazy async init ---------------------------------------------------

    def _ensure_async_primitives(self) -> None:
        """Lazily allocate async primitives on first async use.

        Avoids binding the resolver to a specific event loop at
        construction time. Lets the resolver be passed across loops
        (rare in this codebase, but cheap to support).
        """
        if self._semaphore is None:
            from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
            self._semaphore = get_semaphore(ConcurrencyCategory.DNS_BRUTE)
        if self._lock is None:
            self._lock = asyncio.Lock()

    def _ensure_aiodns(self) -> bool:
        """Lazily init aiodns resolver. Returns True if available."""
        if not HAS_AIODNS:
            return False
        if self._aiodns_resolver is None:
            try:
                self._aiodns_resolver = _AiodnsResolver()
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.debug(
                    "[BATCH_DNS] aiodns init failed: %s: %s",
                    type(exc).__name__, exc,
                )
                return False
        return True

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
        # Capture locals to satisfy type checker (semaphore/lock are guaranteed
        # non-None after _ensure_async_primitives + assert above).
        sem = self._semaphore
        lock = self._lock
        assert lock is not None
        async def _prewarm_host(domain: str) -> None:
            try:
                async with sem:
                    raw = await async_getaddrinfo(
                        domain,
                        0,
                        proto=socket.IPPROTO_TCP,
                        timeout=timeout,
                    )
                    ips = sorted({str(r[4][0]) for r in raw})
                    if ips:
                        async with lock:  # type: ignore[misc]
                            if domain not in self._cache:
                                if (
                                    self._cache_max > 0
                                    and len(self._cache) >= self._cache_max
                                ):
                                    self._cache.popitem(last=False)
                                    self._stats.evictions += 1
                            cache_entry: tuple[list[str], float] = (list(ips), time.monotonic())
                            self._cache[domain] = cache_entry
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

    # -- internal helpers --------------------------------------------------

    def _evict_neg_cache_oldest(self) -> None:
        """Evict oldest 25% of negative cache to maintain bounded size."""
        if self._neg_cache_max <= 0:
            return
        evict_count = max(1, self._neg_cache_max // 4)
        for _ in range(min(evict_count, len(self._neg_cache))):
            self._neg_cache.popitem(last=False)
            self._stats.neg_evictions += 1

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
        - Negative cache hits (NXDOMAIN/SERVFAIL) return empty list (cached failure).
        - Cache misses are resolved in parallel under a bounded semaphore.
        - Hosts that fail DNS are omitted from the result unless they are
          cached as negative results.
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
        use_aiodns = self._ensure_aiodns()

        # Cache lookup under lock to avoid race with eviction.
        async with self._lock:
            for host in unique_hosts:
                # Check negative cache first.
                neg_ts = self._neg_cache.get(host)
                if neg_ts is not None:
                    if (now - neg_ts) < self._neg_ttl_s:
                        # Negative cache hit — return empty (cached failure).
                        self._stats.neg_cache_hits += 1
                        result[host] = []  # empty list = known-failed host
                        continue
                    # Expired negative entry — drop and re-resolve.
                    del self._neg_cache[host]

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
        async def _bounded_resolve(host: str) -> tuple[str, list[str] | None]:
            sem = self._semaphore
            assert sem is not None
            async with sem:
                try:
                    if use_aiodns and self._aiodns_resolver is not None:
                        ips = await self._aiodns_resolver.resolve(host, timeout)
                        self._stats.aiodns_used += 1
                    else:
                        raw = await async_getaddrinfo(
                            host,
                            0,
                            proto=socket.IPPROTO_TCP,
                            timeout=timeout,
                        )
                        ips = sorted({str(r[4][0]) for r in raw})
                    return host, ips
                except Exception as exc:
                    self._stats.errors += 1
                    # Cache negative result if it's a definitive failure
                    if _is_dns_negative_error(exc):
                        logger.debug(
                            "[BATCH_DNS] negative result for %s: %s",
                            host, type(exc).__name__,
                        )
                        # Return None to signal negative cache
                        return host, None
                    logger.debug(
                        "[BATCH_DNS] resolve failed for %s: %s: %s",
                        host, type(exc).__name__, exc,
                    )
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

        # Update caches with new entries (under lock).
        now = time.monotonic()
        lock = self._lock
        async with lock:
            for item in ok_results:
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                raw_host, raw_ips = item
                if not isinstance(raw_host, str):
                    continue
                host: str = raw_host

                if raw_ips is None:
                    # Negative result — cache in negative cache.
                    self._stats.neg_cache_misses += 1
                    # Evict if over capacity.
                    if (
                        self._neg_cache_max > 0
                        and host not in self._neg_cache
                        and len(self._neg_cache) >= self._neg_cache_max
                    ):
                        self._evict_neg_cache_oldest()
                    self._neg_cache[host] = now
                    self._stats.neg_cached_total += 1
                    result[host] = []  # empty = known failure
                    continue

                if not isinstance(raw_ips, list) or not raw_ips:
                    continue

                # Narrow type: raw_ips is list[Any] from gather_result.ok
                ips: list[str] = [str(ip) for ip in raw_ips]  # type: ignore[assignment]
                # Positive result — cache in positive cache.
                if (
                    self._cache_max > 0
                    and host not in self._cache
                    and len(self._cache) >= self._cache_max
                ):
                    self._cache.popitem(last=False)
                    self._stats.evictions += 1
                cache_entry: tuple[list[str], float] = (ips, now)
                self._cache[host] = cache_entry
                result[host] = ips
                self._stats.resolved_total += 1

        return result

    def cache_size(self) -> int:
        """Return current LRU cache size (for tests + telemetry)."""
        return len(self._cache)

    def neg_cache_size(self) -> int:
        """Return current negative cache size (for tests + telemetry)."""
        return len(self._neg_cache)

    def stats(self) -> dict[str, int]:
        """Return bounded telemetry snapshot."""
        return self._stats.to_dict()

    def reset_stats(self) -> None:
        """Reset telemetry counters (does not clear the cache)."""
        self._stats = BatchDNSStats()

    def clear_cache(self) -> None:
        """Drop all cached entries. Safe to call from sync context."""
        self._cache.clear()
        self._neg_cache.clear()

    def is_empty(self) -> bool:
        """Return True if cache is empty."""
        return len(self._cache) == 0 and len(self._neg_cache) == 0

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
    "DEFAULT_NEG_CACHE_MAX",
    "DEFAULT_NEG_TTL_S",
    "DEFAULT_PER_HOST_TIMEOUT_S",
    "DEFAULT_PREWARM_DOMAINS",
    "DEFAULT_TTL_S",
    "ENV_OPT_OUT",
    "HAS_AIODNS",
    "get_batch_dns_resolver",
    "reset_batch_dns_resolver",
]
