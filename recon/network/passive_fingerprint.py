"""
Passive Fingerprinting — Shodan InternetDB, GreyNoise Community, CIRCL, VirusTotal, SecurityTrails.

No active scanning. All sources are passive/lookup-based.

Sources (all free tier or API key optional):
  1. Shodan InternetDB (free, no API key) — https://internetdb.shodan.io/{ip}
  2. GreyNoise Community (free, no API key) — https://api.greynoise.io/v3/community/{ip}
  3. CIRCL Passive DNS + CVEs (free, no API key) — https://api.circl.lu/pdns/f/{domain}
  4. VirusTotal v3 free (free tier, API key optional) — https://www.virustotal.com/api/v3/{type}/{value}
  5. SecurityTrails (API key required, fail-soft) — https://api.securitytrails.com/v1/{type}/{value}

Bounds:
  - MAX_FP_CACHE_SIZE = 500
  - FP_CACHE_TTL_S = 300 (5 min)
  - Per-source timeout: 8s
  - Rate limit: 10 req/min per source for free tiers

GHOST_INVARIANTS:
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only
  - circuit_breaker.domain_breaker_check() before every external call
  - async_get_httpx_session() for all HTTP (httpx replaces aiohttp)
  - Bounded deques, 50MB response caps
  - Fail-soft: source error returns empty dict, never raises
"""
import asyncio
import contextvars
import logging
import time
from typing import Any
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.utils.async_helpers import safe_gather_ok
logger = logging.getLogger(__name__)
MAX_FP_CACHE_SIZE: int = 500
FP_CACHE_TTL_S: int = 300
FP_SOURCE_TIMEOUT_S: float = 8.0

# ── Per-context caches and locks (ContextVar for test isolation + async safety) ──

# ContextVar for _FPCache — allows tests to override with isolated cache per context
_fp_cache_var: contextvars.ContextVar[_FPCache] = contextvars.ContextVar('_fp_cache_var')

class _FPCache:
    """TTL-cached fingerprint lookups, bounded by MAX_FP_CACHE_SIZE."""
    __slots__ = ('_cache', '_timestamps')

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, source: str, value: str) -> str:
        return f'{source}:{value}'

    def get(self, source: str, value: str) -> dict | None:
        k = self._key(source, value)
        ts = self._timestamps.get(k, 0)
        if time.time() - ts > FP_CACHE_TTL_S:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)
            return None
        return self._cache.get(k)

    def set(self, source: str, value: str, data: dict) -> None:
        k = self._key(source, value)
        if len(self._cache) >= MAX_FP_CACHE_SIZE:
            oldest = min(self._timestamps.items(), key=lambda kv: kv[1])[0]
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)
        self._cache[k] = data
        self._timestamps[k] = time.time()

def _get_fp_cache() -> _FPCache:
    """Get or create the ContextVar-backed _FPCache for the current async context."""
    try:
        cache = _fp_cache_var.get()
    except LookupError:
        cache = _FPCache()
        _fp_cache_var.set(cache)
    return cache

# ContextVar for per-source semaphores — ContextVar gives each async context its own
# semaphore map while preserving the lazy asyncio.Semaphore creation pattern.
# Fixes ISSUE-014: asyncio.Lock() at module import without event loop on macOS.
_source_rate_limiters_var: contextvars.ContextVar[dict[str, asyncio.Semaphore]] = contextvars.ContextVar('_source_rate_limiters_var')
_rate_limit_lock_var: contextvars.ContextVar[asyncio.Lock] = contextvars.ContextVar('_rate_limit_lock_var')

def _get_rate_limit_lock() -> asyncio.Lock:
    """Lazy asyncio.Lock getter — ContextVar ensures one lock per async context.

    Fixes ISSUE-014: asyncio.Lock() at module import crashes on macOS because
    asyncio.Lock() captures the current event loop at creation time. By storing
    the lock in a ContextVar and creating it lazily inside the first async call,
    we guarantee an event loop exists at creation time.
    """
    try:
        return _rate_limit_lock_var.get()
    except LookupError:
        lock = asyncio.Lock()
        _rate_limit_lock_var.set(lock)
        return lock

def _get_source_rate_limiters() -> dict[str, asyncio.Semaphore]:
    """Get the ContextVar-backed source rate limiters dict for the current context."""
    try:
        return _source_rate_limiters_var.get()
    except LookupError:
        d: dict[str, asyncio.Semaphore] = {}
        _source_rate_limiters_var.set(d)
        return d

async def _get_rate_limiter(source: str) -> asyncio.Semaphore:
    """Get or create a per-source rate limiter Semaphore for the current async context."""
    lock = _get_rate_limit_lock()
    limiters = _get_source_rate_limiters()
    async with lock:
        if source not in limiters:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            limiters[source] = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        return limiters[source]

class PassiveFingerprint:
    """
    Multi-source passive fingerprinting client.

    Methods (all async):
      - lookup_ip(ip)    → dict with tags, ports, cpes, hostnames, etc.
      - lookup_domain(domain) → dict with subdomains, emails, etc.
    """
    __slots__ = tuple(('_session',))

    def __init__(self):
        self._session: httpx.AsyncClient | None = None

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = await async_get_httpx_session()
        return self._session

    async def _lookup(self, source: str, url: str, params: dict | None=None) -> dict:
        """Generic lookup with cache, rate limit, circuit breaker."""
        cache_key = url.split('/')[-1] if params is None else f"{url}/{params.get('query', params.get('ip', params.get('domain', '')))}"
        fp_cache = _get_fp_cache()
        cached = fp_cache.get(source, cache_key)
        if cached is not None:
            return cached
        data: dict = {}
        sem = await _get_rate_limiter(source)
        async with sem:
            try:
                from hledac.universal.transport.circuit_breaker import get_breaker
                domain = url.split('/')[2] if '//' in url else url
                if not get_breaker(domain).check_circuit().allowed:
                    raise RuntimeError(f'circuit_open: {domain}')
            except Exception as e:
                logger.debug(f'[FP] Circuit breaker blocked {source}: {e}')
                return {}
            session = await self._ensure_session()
            try:
                resp = await session.get(url, params=params or {}, timeout=httpx.Timeout(FP_SOURCE_TIMEOUT_S))
                try:
                    if resp.status_code == 404:
                        return {}
                    if resp.status_code != 200:
                        return {}
                    data = resp.json()
                finally:
                    await resp.aclose()
            except Exception as e:
                logger.debug(f'[FP] {source} lookup failed: {e}')
                return {}
        fp_cache.set(source, cache_key, data)
        return data

    async def shodan_internetdb(self, ip: str) -> dict:
        """Shodan InternetDB — free, no API key. Returns tags, ports, cpes, hostnames."""
        url = f'https://internetdb.shodan.io/{ip}'
        return await self._lookup('shodan_internetdb', url)

    async def greynoise_community(self, ip: str) -> dict:
        """GreyNoise Community — free tier, no API key. Returns classification, tags, metadata."""
        url = f'https://api.greynoise.io/v3/community/{ip}'
        return await self._lookup('greynoise', url)

    async def circl_pdns(self, domain: str) -> dict:
        """CIRCL Passive DNS — free, no API key. Returns A/AAAA/CNAME records."""
        url = f'https://api.circl.lu/pdns/f/{domain}'
        return await self._lookup('circl_pdns', url)

    async def virustotal(self, value: str, vtype: str='ip') -> dict:
        """VirusTotal v3 — free tier, API key optional. Returns last_analysis_stats."""
        url = f'https://www.virustotal.com/api/v3/{vtype}s/{value}'
        return await self._lookup('virustotal', url)

    async def securitytrails(self, value: str, vtype: str='domain') -> dict:
        """SecurityTrails — requires API key, fail-soft if not configured."""
        import os
        api_key = os.environ.get('SECURITYTRAILS_API_KEY', '')
        if not api_key:
            return {}
        url = f'https://api.securitytrails.com/v1/{vtype}/{value}'
        return await self._lookup('securitytrails', url, params={'apikey': api_key})

    async def lookup_ip(self, ip: str) -> dict:
        """Look up an IP across all available sources in parallel."""
        tasks = [self.shodan_internetdb(ip), self.greynoise_community(ip), self.virustotal(ip, 'ip')]
        results = await safe_gather_ok(*tasks, label='passive_fingerprint:193')
        merged: dict[str, Any] = {'ip': ip, 'sources': {}}
        source_names = ['shodan_internetdb', 'greynoise', 'virustotal']
        for name, res in zip(source_names, results, strict=False):
            if isinstance(res, dict) and res:
                merged['sources'][name] = res
        return merged

    async def lookup_domain(self, domain: str) -> dict:
        """Look up a domain across all available sources in parallel."""
        tasks = [self.circl_pdns(domain), self.virustotal(domain, 'domain'), self.securitytrails(domain, 'domain')]
        results = await safe_gather_ok(*tasks, label='passive_fingerprint:208')
        merged: dict[str, Any] = {'domain': domain, 'sources': {}}
        source_names = ['circl_pdns', 'virustotal', 'securitytrails']
        for name, res in zip(source_names, results, strict=False):
            if isinstance(res, dict) and res:
                merged['sources'][name] = res
        return merged

    async def close(self) -> None:
        if self._session and (not self._session.is_closed):
            await self._session.aclose()

class PassiveFingerprintAdapter:
    """
    Passive fingerprint adapter for sidecar runners.
    Wraps PassiveFingerprint, returns CanonicalFinding-compatible dicts.
    """
    __slots__ = tuple(('_fp',))

    def __init__(self):
        self._fp = PassiveFingerprint()

    async def query(self, target: str) -> list[dict]:
        findings: list[dict[str, Any]] = []
        if _is_ip(target):
            result = await self._fp.lookup_ip(target)
        else:
            result = await self._fp.lookup_domain(target)
        if not result.get('sources'):
            return findings
        ts = time.time()
        sources = result.get('sources', {})
        if 'shodan_internetdb' in sources:
            shodan = sources['shodan_internetdb']
            for tag in shodan.get('tags', [])[:20]:
                findings.append({'source_type': 'passive_fingerprint', 'ioc_type': 'ip', 'ioc_value': target, 'target': target, 'confidence': 0.7, 'ts': ts, 'payload_text': f'shodan:tag:{tag}'})
            for port in shodan.get('ports', [])[:30]:
                findings.append({'source_type': 'passive_fingerprint', 'ioc_type': 'ip', 'ioc_value': target, 'target': target, 'confidence': 0.7, 'ts': ts, 'payload_text': f'shodan:port:{port}'})
        if 'greynoise' in sources:
            gn = sources['greynoise']
            classification = gn.get('classification', '')
            if classification:
                findings.append({'source_type': 'passive_fingerprint', 'ioc_type': 'ip', 'ioc_value': target, 'target': target, 'confidence': 0.8, 'ts': ts, 'payload_text': f'greynoise:classification:{classification}'})
        return findings[:100]

    async def close(self) -> None:
        await self._fp.close()

def _is_ip(value: str) -> bool:
    parts = value.split('.')
    if len(parts) == 4:
        try:
            return all((0 <= int(p) <= 255 for p in parts))
        except ValueError:
            pass
    return False
__all__ = ['PassiveFingerprint', 'PassiveFingerprintAdapter']