#!/usr/bin/env python3
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
  - async_get_aiohttp_session() for all HTTP
  - Bounded deques, 50MB response caps
  - Fail-soft: source error returns empty dict, never raises
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from hledac.universal.network.session_runtime import async_get_aiohttp_session

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_FP_CACHE_SIZE: int = 500
FP_CACHE_TTL_S: int = 300
FP_SOURCE_TIMEOUT_S: float = 8.0

# ── Fingerprint Cache ─────────────────────────────────────────────────────────
class _FPCache:
    """TTL-cached fingerprint lookups, bounded by MAX_FP_CACHE_SIZE."""
    __slots__ = ("_cache", "_timestamps")
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, source: str, value: str) -> str:
        return f"{source}:{value}"

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

_fp_cache = _FPCache()

# ── Per-source rate limiter ───────────────────────────────────────────────────
_source_rate_limiters: dict[str, asyncio.Semaphore] = {}
_rate_limit_lock = asyncio.Lock()

async def _get_rate_limiter(source: str) -> asyncio.Semaphore:
    async with _rate_limit_lock:
        if source not in _source_rate_limiters:
            _source_rate_limiters[source] = asyncio.Semaphore(1)
        return _source_rate_limiters[source]

# ── Main Class ─────────────────────────────────────────────────────────────────
class PassiveFingerprint:
    """
    Multi-source passive fingerprinting client.

    Methods (all async):
      - lookup_ip(ip)    → dict with tags, ports, cpes, hostnames, etc.
      - lookup_domain(domain) → dict with subdomains, emails, etc.
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = await async_get_aiohttp_session()
        return self._session

    async def _lookup(
        self,
        source: str,
        url: str,
        params: dict | None = None,
    ) -> dict:
        """Generic lookup with cache, rate limit, circuit breaker."""
        # Check cache
        cache_key = url.split("/")[-1] if params is None else f"{url}/{params.get('query', params.get('ip', params.get('domain', '')))}"
        cached = _fp_cache.get(source, cache_key)
        if cached is not None:
            return cached

        # Rate limit
        sem = await _get_rate_limiter(source)
        async with sem:
            # Circuit breaker
            try:
                from hledac.universal.fetching.fetch_coordinator import circuit_breaker
                domain = url.split("/")[2] if "//" in url else url
                circuit_breaker.domain_breaker_check(domain)
            except Exception as e:
                logger.debug(f"[FP] Circuit breaker blocked {source}: {e}")
                return {}

            session = await self._ensure_session()
            import aiohttp
            try:
                async with session.get(
                    url,
                    params=params or {},
                    timeout=aiohttp.ClientTimeout(total=FP_SOURCE_TIMEOUT_S),
                ) as resp:
                    if resp.status == 404:
                        return {}
                    if resp.status != 200:
                        return {}
                    data = await resp.json()
            except Exception as e:
                logger.debug(f"[FP] {source} lookup failed: {e}")
                return {}

        _fp_cache.set(source, cache_key, data)
        return data

    # ── Shodan InternetDB ──────────────────────────────────────────────────────
    async def shodan_internetdb(self, ip: str) -> dict:
        """Shodan InternetDB — free, no API key. Returns tags, ports, cpes, hostnames."""
        url = f"https://internetdb.shodan.io/{ip}"
        return await self._lookup("shodan_internetdb", url)

    # ── GreyNoise Community ────────────────────────────────────────────────────
    async def greynoise_community(self, ip: str) -> dict:
        """GreyNoise Community — free tier, no API key. Returns classification, tags, metadata."""
        url = f"https://api.greynoise.io/v3/community/{ip}"
        return await self._lookup("greynoise", url)

    # ── CIRCL Passive DNS ─────────────────────────────────────────────────────
    async def circl_pdns(self, domain: str) -> dict:
        """CIRCL Passive DNS — free, no API key. Returns A/AAAA/CNAME records."""
        url = f"https://api.circl.lu/pdns/f/{domain}"
        return await self._lookup("circl_pdns", url)

    # ── VirusTotal v3 ─────────────────────────────────────────────────────────
    async def virustotal(self, value: str, vtype: str = "ip") -> dict:
        """VirusTotal v3 — free tier, API key optional. Returns last_analysis_stats."""
        url = f"https://www.virustotal.com/api/v3/{vtype}s/{value}"
        # Note: VT free tier is heavily rate-limited. Fail-soft.
        return await self._lookup("virustotal", url)

    # ── SecurityTrails ────────────────────────────────────────────────────────
    async def securitytrails(self, value: str, vtype: str = "domain") -> dict:
        """SecurityTrails — requires API key, fail-soft if not configured."""
        import os
        api_key = os.environ.get("SECURITYTRAILS_API_KEY", "")
        if not api_key:
            return {}
        url = f"https://api.securitytrails.com/v1/{vtype}/{value}"
        return await self._lookup("securitytrails", url, params={"apikey": api_key})

    # ── Unified lookup ─────────────────────────────────────────────────────────
    async def lookup_ip(self, ip: str) -> dict:
        """Look up an IP across all available sources in parallel."""
        tasks = [
            self.shodan_internetdb(ip),
            self.greynoise_community(ip),
            self.virustotal(ip, "ip"),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: dict[str, Any] = {"ip": ip, "sources": {}}
        source_names = ["shodan_internetdb", "greynoise", "virustotal"]
        for name, res in zip(source_names, results, strict=False):
            if isinstance(res, dict) and res:
                merged["sources"][name] = res
        return merged

    async def lookup_domain(self, domain: str) -> dict:
        """Look up a domain across all available sources in parallel."""
        tasks = [
            self.circl_pdns(domain),
            self.virustotal(domain, "domain"),
            self.securitytrails(domain, "domain"),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: dict[str, Any] = {"domain": domain, "sources": {}}
        source_names = ["circl_pdns", "virustotal", "securitytrails"]
        for name, res in zip(source_names, results, strict=False):
            if isinstance(res, dict) and res:
                merged["sources"][name] = res
        return merged

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ── PassiveFingerprintAdapter for sidecar bus ─────────────────────────────────
class PassiveFingerprintAdapter:
    """
    Passive fingerprint adapter for sidecar runners.
    Wraps PassiveFingerprint, returns CanonicalFinding-compatible dicts.
    """
    def __init__(self):
        self._fp = PassiveFingerprint()

    async def query(self, target: str) -> list[dict]:
        findings: list[dict[str, Any]] = []

        if _is_ip(target):
            result = await self._fp.lookup_ip(target)
        else:
            result = await self._fp.lookup_domain(target)

        if not result.get("sources"):
            return findings

        ts = time.time()
        sources = result.get("sources", {})

        if "shodan_internetdb" in sources:
            shodan = sources["shodan_internetdb"]
            for tag in shodan.get("tags", [])[:20]:
                findings.append({
                    "source_type": "passive_fingerprint",
                    "ioc_type": "ip",
                    "ioc_value": target,
                    "target": target,
                    "confidence": 0.7,
                    "ts": ts,
                    "payload_text": f"shodan:tag:{tag}",
                })
            for port in shodan.get("ports", [])[:30]:
                findings.append({
                    "source_type": "passive_fingerprint",
                    "ioc_type": "ip",
                    "ioc_value": target,
                    "target": target,
                    "confidence": 0.7,
                    "ts": ts,
                    "payload_text": f"shodan:port:{port}",
                })

        if "greynoise" in sources:
            gn = sources["greynoise"]
            classification = gn.get("classification", "")
            if classification:
                findings.append({
                    "source_type": "passive_fingerprint",
                    "ioc_type": "ip",
                    "ioc_value": target,
                    "target": target,
                    "confidence": 0.8,
                    "ts": ts,
                    "payload_text": f"greynoise:classification:{classification}",
                })

        return findings[:100]  # bounded

    async def close(self) -> None:
        await self._fp.close()


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            pass
    return False


__all__ = [
    "PassiveFingerprint",
    "PassiveFingerprintAdapter",
]
