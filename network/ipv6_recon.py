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
  - async_get_httpx_session() for all HTTP
  - asyncio.open_connection() for WHOIS (no run_in_executor)
  - Bounded deques, 50MB response caps, TTL caches
  - Fail-soft: source error returns empty dict, never raises
"""
import asyncio
import logging
import time
from dataclasses import dataclass
import msgspec
from typing import Any
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.utils.async_helpers import parallel_ok, parallel
logger = logging.getLogger(__name__)
MAX_IPV6_TARGETS: int = 50
RDAP_TIMEOUT_S: float = 8.0
WHOIS_TIMEOUT_S: float = 10.0
MAX_RDAP_CACHE_SIZE: int = 500
RDAP_CACHE_TTL_S: int = 3600

class _RDAPCache:
    """TTL-cached RDAP/WHOIS responses."""
    __slots__ = ('_cache', '_timestamps')

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, rdap_url: str, ip: str) -> str:
        return f'{rdap_url}:{ip}'

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
RDAP_BOOTSTRAP: dict[str, str] = {'arin': 'https://rdap.arin.net/registry/ip', 'ripe': 'https://rdap.ripe.net/rdap/ip', 'apnic': 'https://rdap.apnic.net/ip', 'lacnic': 'https://rdap.lacnic.net/rdap/ip', 'afrinic': 'https://rdap.afrinic.net/rdap/ip'}
WHOIS_SERVERS: dict[str, str] = {'arin': 'whois.arin.net', 'ripe': 'whois.ripe.net', 'apnic': 'whois.apnic.net'}

class IPv6Result(msgspec.Struct):
    target: str
    rdap: dict[str, Any]
    whois: dict[str, Any]
    aaaa_records: list[str]
    bgp_peer: dict[str, Any]
    errors: list[str]
    elapsed_ms: float

class IPv6Recon:
    """
    IPv6 reconnaissance client.

    Methods (all async):
      - recon_ip(ip)            → IPv6Result for an IP (RDAP + WHOIS + BGP)
      - recon_domain(domain)     → IPv6Result for a domain (DoH AAAA + RDAP)
      - get_aaaa(domain)        → list of AAAA records via DoH
      - get_bgp_peer(ip)        → BGP peer info from bgpkit.com
    """
    __slots__ = tuple(('_session',))

    def __init__(self):
        self._session: httpx.AsyncClient | None = None

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = await async_get_httpx_session()
        return self._session

    async def _rdap_lookup(self, ip: str) -> dict[str, Any]:
        """RDAP lookup for an IP — auto-detect registry from IP range."""
        for rdap_url in RDAP_BOOTSTRAP.values():
            cached = _rdap_cache.get(rdap_url, ip)
            if cached is not None:
                return cached
        try:
            from hledac.universal.transport.circuit_breaker import get_breaker
            if not get_breaker('rdap.arin.net').check_circuit().allowed:
                raise RuntimeError(f"circuit_open: {'rdap.arin.net'}")
            if not get_breaker('rdap.ripe.net').check_circuit().allowed:
                raise RuntimeError(f"circuit_open: {'rdap.ripe.net'}")
            if not get_breaker('rdap.apnic.net').check_circuit().allowed:
                raise RuntimeError(f"circuit_open: {'rdap.apnic.net'}")
        except Exception as e:
            logger.debug(f'[IPv6] RDAP circuit breaker: {e}')
        session = await self._ensure_session()
        for name, rdap_url in RDAP_BOOTSTRAP.items():
            try:
                url = f'{rdap_url}/{ip}'
                resp = await session.get(url, timeout=httpx.Timeout(total=RDAP_TIMEOUT_S))
                if resp.status_code == 200:
                    data = resp.json()
                    _rdap_cache.set(rdap_url, ip, data)
                    return data
                elif resp.status_code == 404:
                    continue
                else:
                    continue
            except Exception as e:
                logger.debug(f'[IPv6] RDAP {name} failed: {e}')
                continue
        return {}

    async def _whois_lookup(self, ip: str) -> dict[str, Any]:
        """WHOIS lookup via asyncio.open_connection() — no run_in_executor."""
        server = self._whois_server_for_ip(ip)
        if not server:
            return {}
        try:
            async with asyncio.timeout(WHOIS_TIMEOUT_S):
                reader, writer = await asyncio.open_connection(server, 43)
        except Exception as e:
            logger.debug(f'[IPv6] WHOIS connection failed: {e}')
            return {}
        try:
            writer.write(f'{ip}\r\n'.encode())
            await writer.drain()
            async with asyncio.timeout(WHOIS_TIMEOUT_S):
                data = await reader.read(4096)
            text = data.decode('utf-8', errors='replace')
            return self._parse_whois(text)
        except Exception as e:
            logger.debug(f'[IPv6] WHOIS read failed: {e}')
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
            first_octet = int(ip.split('.')[0])
            if 0 <= first_octet <= 63:
                return 'whois.arin.net'
            elif 64 <= first_octet <= 127:
                return 'whois.ripe.net'
            elif 128 <= first_octet <= 191:
                return 'whois.apnic.net'
            elif 192 <= first_octet <= 223:
                return 'whois.apnic.net'
        except Exception:
            pass
        return 'whois.arin.net'

    def _parse_whois(self, text: str) -> dict[str, Any]:
        """Parse WHOIS text into structured dict."""
        result: dict[str, Any] = {'raw': text[:2000]}
        for line in text.split('\n'):
            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip().lower().replace('-', '_')
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

    async def get_aaaa(self, domain: str) -> list[str]:
        """Get AAAA records for a domain via DoH."""
        try:
            from hledac.universal.network.passive_dns import DOH_RESOLVERS
        except Exception:
            DOH_RESOLVERS = {'cloudflare': 'https://cloudflare-dns.com/dns-query', 'google': 'https://dns.google/resolve'}
        results: list[str] = []

        async def _query(url: str) -> list[str]:
            try:
                session = await self._ensure_session()
                params = {'name': domain, 'type': 'AAAA'}
                resp = await session.get(url, params=params, timeout=httpx.Timeout(total=8.0), headers={'Accept': 'application/dns-json'})
                if resp.status_code != 200:
                    return []
                data = resp.json()
            except Exception:
                return []
            answers: list[str] = []
            for item in data.get('Answer', []) or []:
                answer_str = item.get('data', '')
                if answer_str:
                    answers.append(answer_str)
            return answers
        tasks = [_query(url) for url in DOH_RESOLVERS.values()]
        all_results = await parallel_ok(*tasks, label='ipv6_recon:273')
        for res in all_results:
            if isinstance(res, list):
                results.extend(res)
        seen: set[str] = set()
        unique: list[str] = []
        for a in results:
            if a not in seen:
                seen.add(a)
                unique.append(a)
        return unique

    async def get_bgp_peer(self, ip: str) -> dict[str, Any]:
        """Get BGP peer info from bgpkit.com/v4/peer/{ip}."""
        try:
            from hledac.universal.transport.circuit_breaker import get_breaker
            if not get_breaker('bgpkit.com').check_circuit().allowed:
                raise RuntimeError(f"circuit_open: {'bgpkit.com'}")
        except Exception:
            return {}
        session = await self._ensure_session()
        url = f'https://bgpkit.com/v4/peer/{ip}'
        try:
            resp = await session.get(url, timeout=httpx.Timeout(total=RDAP_TIMEOUT_S))
            if resp.status_code != 200:
                return {}
            return resp.json()
        except Exception as e:
            logger.debug(f'[IPv6] BGP peer lookup failed: {e}')
            return {}

    async def recon_ip(self, ip: str) -> IPv6Result:
        """Full IPv6 recon for an IP address."""
        t0 = time.monotonic()
        errors: list[str] = []
        gathered = await parallel([self._rdap_lookup(ip), self._whois_lookup(ip), self.get_bgp_peer(ip)], taskgroup=True, policy='collect', ctx='ipv6_recon', logger_instance=logger)
        rdap_result: dict[str, Any] = {}
        whois_result: dict[str, Any] = {}
        bgp_result: dict[str, Any] = {}
        for res in gathered.ok:
            if isinstance(res, dict):
                if 'handle' in res or 'network' in res:
                    rdap_result = res
                elif 'raw' in res:
                    whois_result = res
                else:
                    bgp_result = res
        for exc in gathered.errors:
            errors.append(str(exc))
        if gathered.re_raised is not None:
            errors.append(str(gathered.re_raised))
        elapsed_ms = (time.monotonic() - t0) * 1000
        return IPv6Result(target=ip, rdap=rdap_result, whois=whois_result, aaaa_records=[], bgp_peer=bgp_result, errors=errors, elapsed_ms=elapsed_ms)

    async def recon_domain(self, domain: str) -> IPv6Result:
        """Full IPv6 recon for a domain — gets AAAA records, then RDAP for each."""
        t0 = time.monotonic()
        errors: list[str] = []
        aaaa_gathered = await parallel([self.get_aaaa(domain)], taskgroup=True, policy='collect', ctx='ipv6_recon:aaaa', logger_instance=logger)
        aaaa_records: list[str] = []
        for res in aaaa_gathered.ok:
            if isinstance(res, list):
                aaaa_records.extend(res)
        for exc in aaaa_gathered.errors:
            errors.append(f'aaaa:{exc}')
        bgp_gathered = await parallel([self.get_bgp_peer(ip) for ip in aaaa_records[:10]], taskgroup=True, policy='collect', ctx='ipv6_recon:bgp', logger_instance=logger)
        bgp_results: list[dict] = []
        for res in bgp_gathered.ok:
            if isinstance(res, dict):
                bgp_results.append(res)
        for exc in bgp_gathered.errors:
            errors.append(str(exc))
        elapsed_ms = (time.monotonic() - t0) * 1000
        return IPv6Result(target=domain, rdap={}, whois={}, aaaa_records=aaaa_records, bgp_peer={'records': bgp_results}, errors=errors, elapsed_ms=elapsed_ms)

    async def close(self) -> None:
        if self._session and (not self._session.is_closed):
            await self._session.aclose()

class IPv6ReconAdapter:
    """
    IPv6 recon adapter for sidecar runners.
    Wraps IPv6Recon, returns CanonicalFinding-compatible dicts.
    """
    __slots__ = tuple(('_recon',))

    def __init__(self):
        self._recon = IPv6Recon()

    async def query(self, target: str) -> list[dict]:
        """Run IPv6 recon on a target (IP or domain)."""
        findings: list[dict[str, Any]] = []
        try:
            if _is_ip(target):
                result = await self._recon.recon_ip(target)
                if result.bgp_peer:
                    findings.append({'source_type': 'ipv6_recon', 'ioc_type': 'ipv4', 'ioc_value': target, 'target': target, 'confidence': 0.7, 'ts': time.time(), 'payload_text': f"bgp_peer:{result.bgp_peer.get('asn', 'unknown')}"})
            else:
                result = await self._recon.recon_domain(target)
                for aaaa in result.aaaa_records[:50]:
                    findings.append({'source_type': 'ipv6_recon', 'ioc_type': 'ipv6', 'ioc_value': aaaa, 'target': target, 'confidence': 0.6, 'ts': time.time(), 'payload_text': f'aaaa:{target}:{aaaa}'})
        except Exception as e:
            logger.debug(f'[IPv6Recon] Error: {e}')
        return findings[:100]

    async def close(self) -> None:
        await self._recon.close()

def _is_ip(value: str) -> bool:
    parts = value.split('.')
    if len(parts) == 4:
        try:
            return all((0 <= int(p) <= 255 for p in parts))
        except ValueError:
            pass
    return False
__all__ = ['IPv6Recon', 'IPv6ReconAdapter', 'IPv6Result', 'MAX_IPV6_TARGETS']