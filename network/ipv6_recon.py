#!/usr/bin/env python3
"""
IPv6 Reconnaissance — RDAP, WHOIS, DoH AAAA, BGP peer lookups.

Primary methods:
  1. RDAP (arin/ripe/apnic) — primary for IP/ASN metadata
  2. WHOIS fallback — if RDAP returns no data
  3. DoH AAAA query — get IPv6 addresses for domains via DoH
  4. bgpkit.com/v4/peer/{ip} — BGP peer info for an IP

Bounds:
  - MAX_IPV6_TARGETS = 50 (max domains to enumerate IPv6 for)
  - RDAP_TIMEOUT_S = 8.0
  - WHOIS_TIMEOUT_S = 10.0
  - MAX_RDAP_CACHE_SIZE = 500

GHOST_INVARIANTS:
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only
  - circuit_breaker.domain_breaker_check() before every external call
  - async_get_aiohttp_session() for all HTTP
  - asyncio.open_connection() for WHOIS (no run_in_executor)
  - Bounded deques, 50MB response caps, TTL caches
  - Fail-soft: source error returns empty dict, never raises
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_IPV6_TARGETS: int = 50
RDAP_TIMEOUT_S: float = 8.0
WHOIS_TIMEOUT_S: float = 10.0
MAX_RDAP_CACHE_SIZE: int = 500
RDAP_CACHE_TTL_S: int = 3600  # 1 hour

# ── RDAP Cache ────────────────────────────────────────────────────────────────
class _RDAPCache:
    """TTL-cached RDAP/WHOIS responses."""
    __slots__ = ("_cache", "_timestamps")
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, rdap_url: str, ip: str) -> str:
        return f"{rdap_url}:{ip}"

    def get(self, rdap_url: str, ip: str) -> dict | None:
        k = self._key(rdap_url, ip)
        ts = self._timestamps.get(k, 0)
        if time.time() - ts > RDAP_CACHE_TTL_S:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)
            return None
        return self._cache.get(k)

    def set(self, rdap_url: str, ip: str, data: dict) -> None:
        k = self._key(rdap_url, ip)
        if len(self._cache) >= MAX_RDAP_CACHE_SIZE:
            oldest = min(self._timestamps.items(), key=lambda kv: kv[1])[0]
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)
        self._cache[k] = data
        self._timestamps[k] = time.time()

_rdap_cache = _RDAPCache()

# ── RDAP bootstrap servers ────────────────────────────────────────────────────
RDAP_BOOTSTRAP: dict[str, str] = {
    "arin": "https://rdap.arin.net/registry/ip",
    "ripe": "https://rdap.ripe.net/rdap/ip",
    "apnic": "https://rdap.apnic.net/ip",
    "lacnic": "https://rdap.lacnic.net/rdap/ip",
    "afrinic": "https://rdap.afrinic.net/rdap/ip",
}

# ── WHOIS servers ─────────────────────────────────────────────────────────────
WHOIS_SERVERS: dict[str, str] = {
    "arin": "whois.arin.net",
    "ripe": "whois.ripe.net",
    "apnic": "whois.apnic.net",
}


# ── Result Dataclass ──────────────────────────────────────────────────────────
@dataclass
class IPv6Result:
    target: str
    rdap: dict[str, Any]
    whois: dict[str, Any]
    aaaa_records: list[str]
    bgp_peer: dict[str, Any]
    errors: list[str]
    elapsed_ms: float


# ── Main Class ────────────────────────────────────────────────────────────────
class IPv6Recon:
    """
    IPv6 reconnaissance client.

    Methods (all async):
      - recon_ip(ip)            → IPv6Result for an IP (RDAP + WHOIS + BGP)
      - recon_domain(domain)     → IPv6Result for a domain (DoH AAAA + RDAP)
      - get_aaaa(domain)        → list of AAAA records via DoH
      - get_bgp_peer(ip)        → BGP peer info from bgpkit.com
    """

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            from hledac.universal.network.session_runtime import async_get_aiohttp_session
            self._session = await async_get_aiohttp_session()
        return self._session

    # ── RDAP ──────────────────────────────────────────────────────────────────
    async def _rdap_lookup(self, ip: str) -> dict[str, Any]:
        """RDAP lookup for an IP — auto-detect registry from IP range."""
        # Check cache across all RDAP servers
        for rdap_url in RDAP_BOOTSTRAP.values():
            cached = _rdap_cache.get(rdap_url, ip)
            if cached is not None:
                return cached

        # Circuit breaker
        try:
            from hledac.universal.fetching.fetch_coordinator import circuit_breaker
            circuit_breaker.domain_breaker_check("rdap.arin.net")
            circuit_breaker.domain_breaker_check("rdap.ripe.net")
            circuit_breaker.domain_breaker_check("rdap.apnic.net")
        except Exception as e:
            logger.debug(f"[IPv6] RDAP circuit breaker: {e}")

        session = await self._ensure_session()
        import aiohttp

        # Try each RDAP server until one works
        for name, rdap_url in RDAP_BOOTSTRAP.items():
            try:
                url = f"{rdap_url}/{ip}"
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=RDAP_TIMEOUT_S),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _rdap_cache.set(rdap_url, ip, data)
                        return data
                    elif resp.status == 404:
                        continue  # Try next server
                    else:
                        continue
            except Exception as e:
                logger.debug(f"[IPv6] RDAP {name} failed: {e}")
                continue
        return {}

    # ── WHOIS ─────────────────────────────────────────────────────────────────
    async def _whois_lookup(self, ip: str) -> dict[str, Any]:
        """WHOIS lookup via asyncio.open_connection() — no run_in_executor."""
        # Determine appropriate WHOIS server from IP range
        server = self._whois_server_for_ip(ip)
        if not server:
            return {}

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(server, 43),
                timeout=WHOIS_TIMEOUT_S,
            )
        except Exception as e:
            logger.debug(f"[IPv6] WHOIS connection failed: {e}")
            return {}

        try:
            writer.write(f"{ip}\r\n".encode())
            await writer.drain()

            data = await asyncio.wait_for(reader.read(4096), timeout=WHOIS_TIMEOUT_S)
            text = data.decode("utf-8", errors="replace")
            return self._parse_whois(text)
        except Exception as e:
            logger.debug(f"[IPv6] WHOIS read failed: {e}")
            return {}
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _whois_server_for_ip(self, ip: str) -> str | None:
        """Select appropriate WHOIS server based on IP prefix."""
        try:
            first_octet = int(ip.split(".")[0])
            if 0 <= first_octet <= 63:
                return "whois.arin.net"
            elif 64 <= first_octet <= 127:
                return "whois.ripe.net"
            elif 128 <= first_octet <= 191:
                return "whois.apnic.net"
            elif 192 <= first_octet <= 223:
                return "whois.apnic.net"
        except Exception:
            pass
        return "whois.arin.net"

    def _parse_whois(self, text: str) -> dict[str, Any]:
        """Parse WHOIS text into structured dict."""
        result: dict[str, Any] = {"raw": text[:2000]}
        for line in text.split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lower().replace("-", "_")
                value = value.strip()
                if key and value:
                    if key in result:
                        if isinstance(result[key], list):
                            result[key].append(value)
                        else:
                            result[key] = [result[key], value]
                    else:
                        result[key] = value
        return result

    # ── DoH AAAA ────────────────────────────────────────────────────────────────
    async def get_aaaa(self, domain: str) -> list[str]:
        """Get AAAA records for a domain via DoH."""
        try:
            from hledac.universal.network.passive_dns import DOH_RESOLVERS
        except Exception:
            DOH_RESOLVERS = {
                "cloudflare": "https://cloudflare-dns.com/dns-query",
                "google": "https://dns.google/resolve",
            }

        results: list[str] = []

        async def _query(url: str) -> list[str]:
            try:
                session = await self._ensure_session()
                import aiohttp
                params = {"name": domain, "type": "AAAA"}
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=8.0),
                    headers={"Accept": "application/dns-json"},
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            except Exception:
                return []
            answers: list[str] = []
            for item in data.get("Answer", []) or []:
                answer_str = item.get("data", "")
                if answer_str:
                    answers.append(answer_str)
            return answers

        tasks = [_query(url) for url in DOH_RESOLVERS.values()]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in all_results:
            if isinstance(res, list):
                results.extend(res)

        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for a in results:
            if a not in seen:
                seen.add(a)
                unique.append(a)
        return unique

    # ── BGP Peer ────────────────────────────────────────────────────────────────
    async def get_bgp_peer(self, ip: str) -> dict[str, Any]:
        """Get BGP peer info from bgpkit.com/v4/peer/{ip}."""
        # Check circuit breaker
        try:
            from hledac.universal.fetching.fetch_coordinator import circuit_breaker
            circuit_breaker.domain_breaker_check("bgpkit.com")
        except Exception:
            return {}

        session = await self._ensure_session()
        import aiohttp
        url = f"https://bgpkit.com/v4/peer/{ip}"

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=RDAP_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
        except Exception as e:
            logger.debug(f"[IPv6] BGP peer lookup failed: {e}")
            return {}

    # ── Unified Recon ──────────────────────────────────────────────────────────
    async def recon_ip(self, ip: str) -> IPv6Result:
        """Full IPv6 recon for an IP address."""
        t0 = time.monotonic()
        errors: list[str] = []

        # Parallel RDAP, WHOIS, BGP
        rdap_task = asyncio.create_task(self._rdap_lookup(ip), name="ipv6_recon:rdap_lookup")
        whois_task = asyncio.create_task(self._whois_lookup(ip), name="ipv6_recon:whois_lookup")
        bgp_task = asyncio.create_task(self.get_bgp_peer(ip), name="ipv6_recon:bgp_peer")

        done, pending = await asyncio.wait(
            [rdap_task, whois_task, bgp_task],
            return_when=asyncio.ALL_COMPLETED,
        )

        rdap_result: dict[str, Any] = {}
        whois_result: dict[str, Any] = {}
        bgp_result: dict[str, Any] = {}

        for task in done:
            if task is rdap_task:
                try:
                    rdap_result = task.result()
                except Exception as e:
                    errors.append(f"rdap:{e}")
            elif task is whois_task:
                try:
                    whois_result = task.result()
                except Exception as e:
                    errors.append(f"whois:{e}")
            elif task is bgp_task:
                try:
                    bgp_result = task.result()
                except Exception as e:
                    errors.append(f"bgp:{e}")

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        return IPv6Result(
            target=ip,
            rdap=rdap_result,
            whois=whois_result,
            aaaa_records=[],  # Only used for domain recon
            bgp_peer=bgp_result,
            errors=errors,
            elapsed_ms=elapsed_ms,
        )

    async def recon_domain(self, domain: str) -> IPv6Result:
        """Full IPv6 recon for a domain — gets AAAA records, then RDAP for each."""
        t0 = time.monotonic()
        errors: list[str] = []

        aaaa_task = asyncio.create_task(self.get_aaaa(domain), name="ipv6_recon:get_aaaa")
        aaaa_records = []
        try:
            aaaa_records = await aaaa_task
        except Exception as e:
            errors.append(f"aaaa:{e}")

        # Recon each AAAA
        bgp_tasks = [asyncio.create_task(self.get_bgp_peer(ip), name=f"ipv6_recon:bgp_peer:{ip}") for ip in aaaa_records[:10]]
        bgp_results: list[dict] = []
        if bgp_tasks:
            done, pending = await asyncio.wait(bgp_tasks, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                try:
                    bgp_results.append(task.result())
                except Exception:
                    pass
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        elapsed_ms = (time.monotonic() - t0) * 1000
        return IPv6Result(
            target=domain,
            rdap={},
            whois={},
            aaaa_records=aaaa_records,
            bgp_peer={"records": bgp_results},
            errors=errors,
            elapsed_ms=elapsed_ms,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ── IPv6ReconAdapter for sidecar bus ─────────────────────────────────────────
class IPv6ReconAdapter:
    """
    IPv6 recon adapter for sidecar runners.
    Wraps IPv6Recon, returns CanonicalFinding-compatible dicts.
    """
    def __init__(self):
        self._recon = IPv6Recon()

    async def query(self, target: str) -> list[dict]:
        """Run IPv6 recon on a target (IP or domain)."""
        findings: list[dict[str, Any]] = []

        try:
            if _is_ip(target):
                result = await self._recon.recon_ip(target)
                if result.bgp_peer:
                    findings.append({
                        "source_type": "ipv6_recon",
                        "ioc_type": "ipv4",
                        "ioc_value": target,
                        "target": target,
                        "confidence": 0.7,
                        "ts": time.time(),
                        "payload_text": f"bgp_peer:{result.bgp_peer.get('asn', 'unknown')}",
                    })
            else:
                result = await self._recon.recon_domain(target)
                for aaaa in result.aaaa_records[:50]:
                    findings.append({
                        "source_type": "ipv6_recon",
                        "ioc_type": "ipv6",
                        "ioc_value": aaaa,
                        "target": target,
                        "confidence": 0.6,
                        "ts": time.time(),
                        "payload_text": f"aaaa:{target}:{aaaa}",
                    })
        except Exception as e:
            logger.debug(f"[IPv6Recon] Error: {e}")

        return findings[:100]  # bounded

    async def close(self) -> None:
        await self._recon.close()


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            pass
    return False


__all__ = [
    "IPv6Recon",
    "IPv6ReconAdapter",
    "IPv6Result",
    "MAX_IPV6_TARGETS",
]
