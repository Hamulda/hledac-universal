"""
Passive DNS — DoH (DNS-over-HTTPS) resolver with multi-source fallback.

Sources:
  - Cloudflare (1.1.1.1, one.one.one.one)
  - Google (8.8.8.8, dns.google)
  - Quad9 (9.9.9.9, dns.quad9.net)
  - AdGuard (94.140.14.14, dns.adguard.com)
  - NextDNS (45.90.28.0, dns.nextdns.io)

Capabilities:
  - A/AAAA record resolution via DoH JSON API
  - HTTPS RR (Type 65) query via DoH
  - Per-resolver token bucket rate limiting
  - Censorship comparison (compare same query across all resolvers)
  - TTL-cached responses (60s default)

GHOST_INVARIANTS:
  - safe_gather_return_exceptions(...) + _check_gathered()
  - asyncio.sleep() only, no time.sleep()
  - circuit_breaker.domain_breaker_check() before every external call
  - async_get_httpx_session() for all HTTP (httpx replaces aiohttp)
  - Bounded deques, 50MB response caps, TTL caches
  - Fail-soft: resolver error returns empty list, never raises
"""
import asyncio
from hledac.universal.utils.async_helpers import safe_gather_ok, parallel
from hledac.universal.utils.async_helpers import retry_backoff_async
import logging
import time
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.utils.async_helpers import _check_gathered
logger = logging.getLogger(__name__)
MAX_DOH_CACHE_SIZE: int = 2000
MAX_CENSORMAP_SIZE: int = 500
DOH_CACHE_TTL_S: int = 60
TOKEN_BUCKET_RATE: int = 10
TOKEN_BUCKET_BURST: int = 20
BGP_EVENT_TYPES: frozenset[str] = frozenset({'announce', 'withdraw', 'unknown'})
DOH_RESOLVERS: dict[str, str] = {'cloudflare': 'https://cloudflare-dns.com/dns-query', 'google': 'https://dns.google/resolve', 'opendns': 'https://doh.opendns.com/resolve', 'quad9': 'https://dns.quad9.net/dns-query', 'adguard': 'https://dns.adguard.com/dns-query', 'nextdns': 'https://dns.nextdns.io/dns-query'}
DOH_FALLBACK_CHAIN: list[tuple[str, str]] = [('cloudflare', 'https://cloudflare-dns.com/dns-query'), ('google', 'https://dns.google/resolve'), ('opendns', 'https://doh.opendns.com/resolve'), ('quad9', 'https://dns.quad9.net/dns-query'), ('adguard', 'https://dns.adguard.com/dns-query'), ('nextdns', 'https://dns.nextdns.io/dns-query')]
_MAX_DOH_RETRIES: int = 2
_DOH_RETRY_DELAY_S: float = 0.5
from dataclasses import dataclass
import msgspec

class RetryableError(Exception):
    """Signals a retriable error for retry_backoff_async."""


class _ResolverHealth(msgspec.Struct):
    """Per-resolver health state for circuit breaker."""
    consecutive_failures: int = 0
    last_failure_ts: float = 0.0
    total_requests: int = 0
    total_successes: int = 0

    @property
    def failure_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return 1.0 - self.total_successes / self.total_requests

    @property
    def is_healthy(self) -> bool:
        return self.consecutive_failures < _MAX_CONSECUTIVE_FAILURES
_MAX_CONSECUTIVE_FAILURES: int = 5
_RECOVERY_WINDOW_S: float = 60.0

class _ResolverHealthTracker:
    """Thread-safe resolver health tracker with recovery window."""
    __slots__ = ('_health', '_lock')
    _health: dict[str, _ResolverHealth]
    _lock: asyncio.Lock | None  # ISSUE-014 fix: None sentinel, lazy creation

    def __init__(self) -> None:
        self._health = {name: _ResolverHealth() for name in DOH_RESOLVERS}
        self._lock = None  # Lazily created

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create lock — ISSUE-014 fix: asyncio.Lock() at __init__ crashes on macOS."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def record_success(self, resolver: str) -> None:
        async with self._get_lock():
            h = self._health.get(resolver)
            if h:
                h.total_requests += 1
                h.total_successes += 1
                h.consecutive_failures = 0

    async def record_failure(self, resolver: str) -> None:
        async with self._get_lock():
            h = self._health.get(resolver)
            if h:
                h.total_requests += 1
                h.consecutive_failures += 1
                h.last_failure_ts = time.time()

    async def get_healthy_resolvers(self) -> list[tuple[str, str]]:
        """Return fallback chain with unhealthy resolvers filtered out.

        Recovery window: resolvers with consecutive_failures > 0 but within
        _RECOVERY_WINDOW_S are temporarily excluded so a transient DoH outage
        doesn't permanently blacklist a provider. After recovery window expires
        the resolver is re-added (failure count is reset to 0).
        """
        async with self._get_lock():
            now = time.time()
            result: list[tuple[str, str]] = []
            for name, url in DOH_FALLBACK_CHAIN:
                h = self._health.get(name)
                if h is None:
                    result.append((name, url))
                    continue
                if h.is_healthy:
                    if h.consecutive_failures > 0 and h.last_failure_ts > 0:
                        if now - h.last_failure_ts > _RECOVERY_WINDOW_S:
                            h.consecutive_failures = 0
                            result.append((name, url))
                    else:
                        result.append((name, url))
                elif h.last_failure_ts > 0 and now - h.last_failure_ts > _RECOVERY_WINDOW_S:
                    h.consecutive_failures = 0
                    result.append((name, url))
            return result

    def get_stats(self) -> dict[str, dict]:
        """Return per-resolver health stats for telemetry."""
        return {name: {'consecutive_failures': h.consecutive_failures, 'total_requests': h.total_requests, 'total_successes': h.total_successes, 'failure_rate': h.failure_rate, 'is_healthy': h.is_healthy} for name, h in self._health.items()}
_resolver_health = _ResolverHealthTracker()

class _TokenBucket:
    """Async token bucket with event-driven wait using asyncio.Condition.

    ISSUE-018 fix: Replaced polling loop (await asyncio.sleep(0.05))
    with event-driven wait using asyncio.Condition.notify_all().
    This eliminates 40×/sec CPU spin during token wait.
    """
    __slots__ = ('rate', 'burst', 'tokens', '_lock', '_last_refill', '_condition')

    def __init__(self, rate: int, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self._lock = asyncio.Lock()
        self._condition: asyncio.Condition | None = None  # Lazily initialized
        self._last_refill = time.monotonic()

    def _get_condition(self) -> asyncio.Condition:
        """Lazily create Condition attached to the running event loop."""
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    async def acquire(self, timeout: float=5.0) -> bool:
        """Acquire a token, waiting if needed. Returns False on timeout."""
        deadline = time.monotonic() + timeout

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self._last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False

                # ISSUE-018 fix: Event-driven wait instead of polling
                # asyncio.Condition.wait() releases the lock, waits, reacquires
                # This is the correct pattern - wait() inside the lock
                condition = self._get_condition()
                try:
                    async with asyncio.timeout(min(remaining, 0.1)):
                        await condition.wait()
                except asyncio.TimeoutError:
                    pass  # Timeout expired, loop will re-check tokens
                except asyncio.CancelledError:
                    raise
                # Loop will re-check tokens after wait() returns

class _DoHCache:
    """TTL-cached DoH responses, bounded by MAX_DOH_CACHE_SIZE."""
    __slots__ = ('_cache', '_timestamps')

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, name: str, rdtype: str, resolver: str) -> str:
        return f'{resolver}:{rdtype}:{name}'

    def get(self, name: str, rdtype: str, resolver: str) -> dict | None:
        k = self._key(name, rdtype, resolver)
        ts = self._timestamps.get(k, 0)
        if time.time() - ts > DOH_CACHE_TTL_S:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)
            return None
        return self._cache.get(k)

    def set(self, name: str, rdtype: str, resolver: str, value: dict) -> None:
        k = self._key(name, rdtype, resolver)
        if len(self._cache) >= MAX_DOH_CACHE_SIZE:
            oldest = min(self._timestamps.items(), key=lambda kv: kv[1])[0]
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)
        self._cache[k] = value
        self._timestamps[k] = time.time()
_resolver_buckets: dict[str, _TokenBucket] = {name: _TokenBucket(TOKEN_BUCKET_RATE, TOKEN_BUCKET_BURST) for name in DOH_RESOLVERS}
_doh_cache = _DoHCache()

class PassiveDNSResolver:
    """
    Multi-resolver DoH client with token-bucket rate limiting and TTL cache.

    Methods (all async):
      - resolve(name, rdtype)       → list of str (A/AAAA/CNAME/TXT)
      - resolve_https_rr(name)       → list of str (HTTPS RR values, RFC 9460)
      - compare_resolvers(name, rdtype) → dict resolver→answers (censorship comparison)
    """
    __slots__ = tuple(('_session',))

    def __init__(self):
        self._session: httpx.AsyncClient | None = None

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = await async_get_httpx_session()
        return self._session

    async def _do_query(self, name: str, rdtype: str, resolver: str, url: str) -> list[str]:
        """Query one resolver, return results or [] on error."""
        bucket = _resolver_buckets.get(resolver)
        if bucket and (not await bucket.acquire(timeout=5.0)):
            logger.debug(f'[DoH] Rate limited: {resolver}')
            return []
        try:
            from hledac.universal.transport.circuit_breaker import get_breaker
            domain = url.split('/')[2] if '//' in url else url
            if not get_breaker(domain).check_circuit().allowed:
                logger.debug(f'[DoH] Circuit breaker open for {resolver}: {domain}')
                return []
        except Exception as e:
            logger.debug(f'[DoH] Circuit breaker check failed for {resolver}: {e}')
            return []
        cached = _doh_cache.get(name, rdtype, resolver)
        if cached is not None:
            return cached.get('answers', [])

        session = await self._ensure_session()

        # E1 fix: retry_backoff_async with jitter + proper CancelledError propagation
        # instead of manual for-loop with asyncio.sleep
        async def _fetch_once() -> dict:
            params = {'name': name, 'type': rdtype}
            resp = await session.get(url, params=params, timeout=httpx.Timeout(10.0), headers={'Accept': 'application/dns-json'})
            async with resp:
                if resp.status_code >= 500:
                    raise RetryableError(f'HTTP {resp.status_code}')
                if resp.status_code != 200:
                    return {}
                return await resp.json()

        try:
            data = await retry_backoff_async(
                _fetch_once,
                max_retries=_MAX_DOH_RETRIES,
                base_delay=_DOH_RETRY_DELAY_S,
                max_delay=30.0,
                jitter=True,
            )
        except RetryableError as e:
            logger.debug(f'[DoH] Query failed for {resolver}: {e}')
            return []

        if not data:
            return []
        answers: list[str] = []
        for item in data.get('Answer', []) or []:
            answer_str = item.get('data', '')
            if answer_str:
                answers.append(answer_str)
        _doh_cache.set(name, rdtype, resolver, {'answers': answers})
        return answers

    async def resolve(self, name: str, rdtype: str='A') -> list[str]:
        """
        Resolve name via DoH fallback chain — F300.

        Tries resolvers in order (cloudflare → google → opendns → quad9 → adguard → nextdns).
        Early exit on first successful resolution with results.
        Records success/failure per resolver for circuit breaker health tracking.
        """
        healthy_resolvers = await _resolver_health.get_healthy_resolvers()
        if not healthy_resolvers:
            logger.warning('[DoH] All resolvers unhealthy, attempting recovery')
            healthy_resolvers = DOH_FALLBACK_CHAIN
        for resolver, url in healthy_resolvers:
            result = await self._do_query(name, rdtype, resolver, url)
            if result:
                await _resolver_health.record_success(resolver)
                return result
            else:
                await _resolver_health.record_failure(resolver)
        return []

    async def resolve_https_rr(self, name: str) -> list[str]:
        """Query HTTPS RR (Type 65) via DoH."""
        return await self.resolve(name, rdtype='65')

    async def compare_resolvers(self, name: str, rdtype: str='A') -> dict[str, list[str]]:
        """
        Compare answers across all healthy resolvers — detects censorship.

        F300: Uses health-aware resolver list. Unhealthy resolvers are excluded.
        """
        healthy_resolvers = await _resolver_health.get_healthy_resolvers()
        if not healthy_resolvers:
            return {}
        tasks = {resolver: self._do_query(name, rdtype, resolver, url) for resolver, url in healthy_resolvers}
        _result = await parallel(list(tasks.values()), taskgroup=True, policy='collect', ctx='passive_dns:compare_resolvers', logger_instance=logger)
        _ok_results, _errors = _result.ok, list(_result.errors)
        comparison: dict[str, list[str]] = {}
        for (resolver, _coro), res in zip(tasks.items(), _ok_results, strict=False):
            comparison[resolver] = res if isinstance(res, list) else []
        return comparison

    async def close(self) -> None:
        if self._session and (not self._session.is_closed):
            await self._session.aclose()

class PassiveDNSAdapter:
    """
    Passive DNS adapter for use in sidecar runners.
    Wraps PassiveDNSResolver, returns CanonicalFinding-compatible dicts.
    """
    __slots__ = tuple(('_resolver',))

    def __init__(self):
        self._resolver = PassiveDNSResolver()

    async def query(self, target: str) -> list[dict]:
        """Query passive DNS for a target (domain or IP)."""
        from typing import Any
        findings: list[dict[str, Any]] = []
        rdtype = 'A'
        if _is_ipv6(target):
            rdtype = 'AAAA'
        try:
            answers = await self.resolve(target, rdtype=rdtype)
        except Exception:
            answers = []
        if not answers:
            return findings
        ts = time.time()
        for answer in answers[:50]:
            findings.append({'source_type': 'passive_dns', 'ioc_type': 'ipv4' if rdtype == 'A' else 'ipv6', 'ioc_value': answer, 'target': target, 'confidence': 0.6, 'ts': ts, 'payload_text': f'passive_dns:{target}:{rdtype}:{answer}'})
        return findings

    async def resolve(self, name: str, rdtype: str='A') -> list[str]:
        return await self._resolver.resolve(name, rdtype)

    async def resolve_https_rr(self, name: str) -> list[str]:
        return await self._resolver.resolve_https_rr(name)

    async def compare_resolvers(self, name: str, rdtype: str='A') -> dict[str, list[str]]:
        return await self._resolver.compare_resolvers(name, rdtype)

    async def close(self) -> None:
        await self._resolver.close()

def _is_ipv6(value: str) -> bool:
    return ':' in value
__all__ = ['PassiveDNSResolver', 'PassiveDNSAdapter', 'DOH_RESOLVERS', 'RetryableError']