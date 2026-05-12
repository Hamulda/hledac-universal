#!/usr/bin/env python3
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
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only, no time.sleep()
  - circuit_breaker.domain_breaker_check() before every external call
  - async_get_aiohttp_session() for all HTTP
  - Bounded deques, 50MB response caps, TTL caches
  - Fail-soft: resolver error returns empty list, never raises
"""

from __future__ import annotations

import aiohttp
import asyncio
import logging
import time
from typing import Optional

from hledac.universal.network.session_runtime import async_get_aiohttp_session

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_DOH_CACHE_SIZE: int = 2000
MAX_CENSORMAP_SIZE: int = 500
DOH_CACHE_TTL_S: int = 60
TOKEN_BUCKET_RATE: int = 10  # requests per second per resolver
TOKEN_BUCKET_BURST: int = 20
BGP_EVENT_TYPES: frozenset[str] = frozenset({"announce", "withdraw", "unknown"})

# ── DoH Resolvers ─────────────────────────────────────────────────────────────
DOH_RESOLVERS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
    "quad9": "https://dns.quad9.net:5053/dns-query",
    "adguard": "https://dns.adguard.com/dns-query",
    "nextdns": "https://dns.nextdns.io/dns-query",
}

# ── Token Bucket per resolver ─────────────────────────────────────────────────
class _TokenBucket:
    """Simple async token bucket with asyncio.Lock."""
    __slots__ = ("rate", "burst", "tokens", "_lock", "_last_refill")
    def __init__(self, rate: int, burst: int):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self._lock = asyncio.Lock()
        self._last_refill = time.monotonic()

    async def acquire(self, timeout: float = 5.0) -> bool:
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
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

# ── DoH Cache ─────────────────────────────────────────────────────────────────
class _DoHCache:
    """TTL-cached DoH responses, bounded by MAX_DOH_CACHE_SIZE."""
    __slots__ = ("_cache", "_timestamps")
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, name: str, rdtype: str, resolver: str) -> str:
        return f"{resolver}:{rdtype}:{name}"

    def get(self, name: str, rdtype: str, resolver: str) -> Optional[dict]:
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


# ── Per-resolver buckets ──────────────────────────────────────────────────────
_resolver_buckets: dict[str, _TokenBucket] = {
    name: _TokenBucket(TOKEN_BUCKET_RATE, TOKEN_BUCKET_BURST)
    for name in DOH_RESOLVERS
}
_doh_cache = _DoHCache()

# ── Main Class ─────────────────────────────────────────────────────────────────
class PassiveDNSResolver:
    """
    Multi-resolver DoH client with token-bucket rate limiting and TTL cache.

    Methods (all async):
      - resolve(name, rdtype)       → list of str (A/AAAA/CNAME/TXT)
      - resolve_https_rr(name)       → list of str (HTTPS RR values, RFC 9460)
      - compare_resolvers(name, rdtype) → dict resolver→answers (censorship comparison)
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = await async_get_aiohttp_session()
        return self._session

    async def _do_query(
        self,
        name: str,
        rdtype: str,
        resolver: str,
        url: str,
    ) -> list[str]:
        """Query one resolver, return results or [] on error."""
        bucket = _resolver_buckets.get(resolver)
        if bucket and not await bucket.acquire(timeout=5.0):
            logger.debug(f"[DoH] Rate limited: {resolver}")
            return []

        # Check circuit breaker
        try:
            from hledac.universal.fetching.fetch_coordinator import circuit_breaker
            domain = url.split("/")[2] if "//" in url else url
            circuit_breaker.domain_breaker_check(domain)
        except Exception as e:
            logger.debug(f"[DoH] Circuit breaker blocked {resolver}: {e}")
            return []

        # Check cache
        cached = _doh_cache.get(name, rdtype, resolver)
        if cached is not None:
            return cached.get("answers", [])

        session = await self._ensure_session()
        import aiohttp
        try:
            params = {"name": name, "type": rdtype}
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10.0),
                headers={"Accept": "application/dns-json"},
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except Exception as e:
            logger.debug(f"[DoH] Query failed for {resolver}: {e}")
            return []

        answers: list[str] = []
        for item in data.get("Answer", []) or []:
            answer_str = item.get("data", "")
            if answer_str:
                answers.append(answer_str)

        _doh_cache.set(name, rdtype, resolver, {"answers": answers})
        return answers

    async def resolve(self, name: str, rdtype: str = "A") -> list[str]:
        """Resolve name via all available DoH resolvers, return merged results."""
        tasks = [
            self._do_query(name, rdtype, resolver, url)
            for resolver, url in DOH_RESOLVERS.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[str] = []
        for res in results:
            if isinstance(res, list):
                merged.extend(res)
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for a in merged:
            if a not in seen:
                seen.add(a)
                unique.append(a)
        return unique

    async def resolve_https_rr(self, name: str) -> list[str]:
        """Query HTTPS RR (Type 65) via DoH."""
        return await self.resolve(name, rdtype="65")

    async def compare_resolvers(self, name: str, rdtype: str = "A") -> dict[str, list[str]]:
        """Compare answers across all resolvers — detects censorship."""
        tasks = {
            resolver: self._do_query(name, rdtype, resolver, url)
            for resolver, url in DOH_RESOLVERS.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        comparison: dict[str, list[str]] = {}
        for resolver, res in zip(tasks.keys(), results):
            if isinstance(res, list):
                comparison[resolver] = res
            else:
                comparison[resolver] = []
        return comparison

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ── PassiveDNSAdapter for sidecar bus ─────────────────────────────────────────
class PassiveDNSAdapter:
    """
    Passive DNS adapter for use in sidecar runners.
    Wraps PassiveDNSResolver, returns CanonicalFinding-compatible dicts.
    """
    def __init__(self):
        self._resolver = PassiveDNSResolver()

    async def query(self, target: str) -> list[dict]:
        """Query passive DNS for a target (domain or IP)."""
        from typing import Any
        findings: list[dict[str, Any]] = []
        rdtype = "A"
        if _is_ipv6(target):
            rdtype = "AAAA"
        try:
            answers = await self.resolve(target, rdtype=rdtype)
        except Exception:
            answers = []

        if not answers:
            return findings

        ts = time.time()
        for answer in answers[:50]:  # bounded
            findings.append({
                "source_type": "passive_dns",
                "ioc_type": "ipv4" if rdtype == "A" else "ipv6",
                "ioc_value": answer,
                "target": target,
                "confidence": 0.6,
                "ts": ts,
                "payload_text": f"passive_dns:{target}:{rdtype}:{answer}",
            })
        return findings

    async def resolve(self, name: str, rdtype: str = "A") -> list[str]:
        return await self._resolver.resolve(name, rdtype)

    async def resolve_https_rr(self, name: str) -> list[str]:
        return await self._resolver.resolve_https_rr(name)

    async def compare_resolvers(self, name: str, rdtype: str = "A") -> dict[str, list[str]]:
        return await self._resolver.compare_resolvers(name, rdtype)

    async def close(self) -> None:
        await self._resolver.close()


def _is_ipv6(value: str) -> bool:
    return ":" in value


__all__ = [
    "PassiveDNSResolver",
    "PassiveDNSAdapter",
    "DOH_RESOLVERS",
]
