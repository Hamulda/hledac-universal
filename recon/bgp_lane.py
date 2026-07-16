"""
BGPLane — Sprint F234 (BGP/ASN IP-to-Org Attribution)
======================================================

Free, no API key required. Sources:
  - bgpview.io API (primary): /ip/{ip}, /asn/{asn}, /search?query_term={org}
  - Hurricane Electric BGP Toolkit: https://bgp.he.net/ (scraping fallback)
  - RIPE NCC Stat API: https://stat.ripe.net/data/ (supplementary)

Bounds:
  MAX_ASN_RESULTS = 500        — max results from org search
  RATE_LIMIT_S = 2.0           — bgpview.io ~30 req/min → 2s between batch
  TIMEOUT_PER_REQUEST = 15.0     — seconds
  MAX_PREFIXES_PER_ASN = 200    — max prefixes per ASN lookup

Guardrails:
  asyncio.gather return_exceptions=True + _check_gathered()
  Fail-soft: errors return empty list / None
  No API key required — purely public data
  Rate limited to respect bgpview.io
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import msgspec
from typing import Any, cast
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
from hledac.universal.transport.session_pool import session_pool
import httpx
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
_CanonicalFinding: "type[CanonicalFinding] | None" = None
try:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding as _CF
    _CanonicalFinding = _CF
except ImportError:
    pass
from hledac.universal.utils.async_helpers import parallel, ParallelResult
logger = logging.getLogger(__name__)
MAX_ASN_RESULTS: int = 500
RATE_LIMIT_S: float = 2.0
TIMEOUT_PER_REQUEST: float = 15.0
MAX_PREFIXES_PER_ASN: int = 200
BGPVIEW_API = 'https://api.bgpview.io'

class BGPFinding(msgspec.Struct):
    """
    BGP intelligence finding.

    Fields:
        query_ip:       Original IP that was queried (empty if org-search)
        asn:            Autonomous System Number (integer)
        asn_name:       Organisation name announced by this ASN
        country_code:   ISO 3166-1 alpha-2 country code
        prefix:         CIDR prefix announced by this ASN (e.g. "93.184.216.0/24")
        prefix_name:    Description / name of the prefix (RIR allocation name)
        rir:            Regional Internet Registry (ARIN/RIPE/APNIC/LACNIC/AFRINIC)
        source:         Which API provided this result (bgpview/he/ripe)
    """
    query_ip: str
    asn: int
    asn_name: str
    country_code: str
    prefix: str
    prefix_name: str | None
    rir: str | None
    source: str = 'bgpview'

    def to_finding_dict(self) -> dict:
        """Convert to plain dict for compatibility."""
        return {'source': 'bgp_intelligence', 'ip': self.query_ip, 'asn': f'AS{self.asn}', 'org': self.asn_name, 'country': self.country_code, 'ip_range': self.prefix, 'prefix_name': self.prefix_name, 'rir': self.rir}

    def to_canonical_finding(self, query: str, _sprint_id: str='') -> CanonicalFinding | None:
        """Convert to CanonicalFinding for DuckDB ingestion."""
        if _CanonicalFinding is None:
            return None
        try:
            payload = self._build_payload()
            return _CanonicalFinding(finding_id=f"bgp-{self.asn}-{self.prefix.replace('/', '-')}", source_type='bgp_intelligence', confidence=0.85, query=query[:128], ts=time.time(), payload_text=payload, provenance=(f'asn:AS{self.asn}', f'org:{self.asn_name}', f'prefix:{self.prefix}', f'country:{self.country_code}', f"rir:{self.rir or 'unknown'}"))
        except Exception:
            return None

    def _build_payload(self) -> str:
        parts = [f'[BGP Intelligence] AS{self.asn}', f'Org: {self.asn_name}', f'Country: {self.country_code}', f'Prefix: {self.prefix}']
        if self.prefix_name:
            parts.append(f'Prefix name: {self.prefix_name}')
        if self.rir:
            parts.append(f'RIR: {self.rir}')
        if self.query_ip:
            parts.append(f'Queried IP: {self.query_ip}')
        return '\n'.join(parts)

class BGPResult(msgspec.Struct, frozen=True):
    """Result of a BGP lane operation."""
    ip: str
    asn: int | None = None
    org_name: str | None = None
    country_code: str | None = None
    prefix: str | None = None
    rir: str | None = None
    prefixes: list[BGPFinding] = field(default_factory=list)
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0

    def to_findings(self, query: str, sprint_id: str) -> list:
        if self.error:
            return []
        findings = []
        if self.prefixes:
            for p in self.prefixes:
                f = p.to_canonical_finding(query, sprint_id)
                if f:
                    findings.append(f)
        elif self.asn:
            single = BGPFinding(query_ip=self.ip, asn=self.asn, asn_name=self.org_name or '', country_code=self.country_code or '', prefix=self.prefix or '', prefix_name=None, rir=self.rir)
            f = single.to_canonical_finding(query, sprint_id)
            if f:
                findings.append(f)
        return findings

def _check_gathered(results: list, stats: dict) -> None:
    """Log and count exceptions from asyncio.gather results."""
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        logger.warning(f'BGPLane gather: {len(errors)} errors')
        stats['gather_errors'] = stats.get('gather_errors', 0) + len(errors)

async def ip_to_asn(ip: str, session: httpx.AsyncClient) -> BGPFinding | None:
    """
    Resolve IP address → ASN + org info via BGPView /ip endpoint.

    Args:
        ip:      IPv4 or IPv6 address (e.g. "8.8.8.8")
        session: httpx.AsyncClient

    Returns:
        BGPFinding with ASN, org, country, prefix, RIR or None on failure.
    """
    url = f'{BGPVIEW_API}/ip/{ip}'
    try:
        resp = await session.get(url, timeout=httpx.Timeout(TIMEOUT_PER_REQUEST))
        if resp.status_code != 200:
            logger.debug(f'bgpview /ip {ip} → HTTP {resp.status_code}')
            return None
        data = (await resp.json()).get('data', {})
    except TimeoutError:
        logger.debug(f'bgpview /ip {ip} → timeout')
        return None
    except Exception as e:
        logger.debug(f'bgpview /ip {ip} → {e}')
        return None
    prefixes = data.get('prefixes', [])
    if not prefixes:
        return None
    p = prefixes[0]
    asn_data = p.get('asn', {})
    rir_allocation = asn_data.get('rir_allocation', {}) or {}
    return BGPFinding(query_ip=ip, asn=asn_data.get('asn', 0), asn_name=asn_data.get('name', ''), country_code=asn_data.get('country_code', ''), prefix=p.get('prefix', ''), prefix_name=p.get('name'), rir=rir_allocation.get('rir_name'), source='bgpview')

async def asn_to_prefixes(asn: int, session: httpx.AsyncClient) -> list[BGPFinding]:
    """
    Fetch all IP prefixes announced by a given ASN.

    Args:
        asn:     Autonomous System Number (integer)
        session: httpx.AsyncClient

    Returns:
        List of BGPFinding, one per announced prefix.
    """
    url = f'{BGPVIEW_API}/asn/{asn}'
    try:
        resp = await session.get(url, params={'query': ''}, timeout=httpx.Timeout(TIMEOUT_PER_REQUEST))
        if resp.status_code != 200:
            return []
        data = (await resp.json()).get('data', {})
    except Exception:
        return []
    asn_name = data.get('name', '')
    country_code = data.get('country_code', '')
    rir_allocation = data.get('rir_allocation', {}) or {}
    rir = rir_allocation.get('rir_name')
    prefixes = data.get('prefixes', [])[:MAX_PREFIXES_PER_ASN]
    findings = []
    for p in prefixes:
        asn_data = p.get('asn', {})
        findings.append(BGPFinding(query_ip='', asn=asn_data.get('asn', asn), asn_name=asn_data.get('name', asn_name), country_code=asn_data.get('country_code', country_code), prefix=p.get('prefix', ''), prefix_name=p.get('description'), rir=rir, source='bgpview'))
    return findings

async def org_to_asns(org_query: str, session: httpx.AsyncClient, *, limit: int=MAX_ASN_RESULTS) -> list[BGPFinding]:
    """
    Search organisation name → find all associated ASNs.

    Uses BGPView /search?query_term={org} — returns ASNs whose name
    or description matches the query string.

    Args:
        org_query: Organisation name or part of it (e.g. "Google", "Cloudflare")
        session:   httpx.AsyncClient
        limit:     Max ASNs to return (default 500)

    Returns:
        List of BGPFinding — one per matched ASN (prefix/rir may be empty).
    """
    url = f'{BGPVIEW_API}/search'
    params = {'query_term': org_query}
    try:
        resp = await session.get(url, params=params, timeout=httpx.Timeout(20.0))
        if resp.status_code != 200:
            logger.debug(f'bgpview /search {org_query} → HTTP {resp.status_code}')
            return []
        data = (await resp.json()).get('data', {})
    except TimeoutError:
        logger.debug(f'bgpview /search {org_query} → timeout')
        return []
    except Exception as e:
        logger.debug(f'bgpview /search {org_query} → {e}')
        return []
    asns = data.get('asns', [])[:limit]
    findings = []
    for entry in asns:
        rir_alloc = entry.get('rir_allocation', {}) or {}
        findings.append(BGPFinding(query_ip='', asn=entry.get('asn', 0), asn_name=entry.get('name', ''), country_code=entry.get('country_code', ''), prefix='', prefix_name=entry.get('description'), rir=rir_alloc.get('rir_name'), source='bgpview'))
    return findings[:limit]

async def ip_bulk_to_asn(ips: list[str], session: httpx.AsyncClient, *, rate_limit_s: float=RATE_LIMIT_S, concurrency: int=3) -> list[BGPFinding]:
    """
    Resolve multiple IP addresses to ASNs with rate limiting.

    Args:
        ips:           List of IP addresses
        session:       httpx.AsyncClient
        rate_limit_s:  Minimum seconds between batch requests (bgpview ~30 req/min)
        concurrency:   Max concurrent requests (Semaphore)

    Returns:
        List of BGPFinding (only successful lookups).
    """
    if not ips:
        return []
    semaphore = get_semaphore_for_testing(ConcurrencyCategory.BGP_QUERY)
    last_request = 0.0
    findings: list[BGPFinding] = []

    async def _fetch_one(ip: str) -> BGPFinding | None:
        nonlocal last_request
        async with semaphore:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit_s:
                await asyncio.sleep(rate_limit_s - elapsed)
            last_request = time.monotonic()
            result = await ip_to_asn(ip, session)
            return result
    _result = await parallel([_fetch_one(ip) for ip in ips], taskgroup=True, policy='collect', ctx='bgp_ip_to_asn_bulk', logger_instance=logger)
    for r in _result.ok:
        if isinstance(r, BGPFinding):
            findings.append(r)
    return findings

async def org_bulk_to_asns_with_prefixes(org_queries: list[str], session: httpx.AsyncClient, *, rate_limit_s: float=RATE_LIMIT_S, concurrency: int=2) -> list[BGPFinding]:
    """
    For each org name: find ASNs → fetch their prefixes.

    Two-stage lookup: org name → ASN list → per-ASN prefix list.
    Rate limited across both stages.

    Args:
        org_queries:   List of organisation name strings
        session:       httpx.AsyncClient
        rate_limit_s:  Seconds between requests
        concurrency:   Max concurrent ASN lookups

    Returns:
        All BGPFinding objects (one per ASN + each prefix).
    """
    if not org_queries:
        return []
    asn_semaphore = get_semaphore_for_testing(ConcurrencyCategory.BGP_QUERY)
    last_request = 0.0

    async def _org_to_asns(org: str) -> list[BGPFinding]:
        nonlocal last_request
        async with asn_semaphore:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit_s:
                await asyncio.sleep(rate_limit_s - elapsed)
            last_request = time.monotonic()
            return await org_to_asns(org, session)
    _result = await parallel([_org_to_asns(q) for q in org_queries], taskgroup=True, policy='collect', ctx='bgp_org_to_asns', logger_instance=logger)
    org_results: list[Any] = _result.ok
    all_asns: list[tuple[int, BGPFinding]] = []
    for res in org_results:
        if isinstance(res, list):
            for f in res:
                if f.asn:
                    all_asns.append((f.asn, f))
    if not all_asns:
        return []
    unique_asns = list({asn for asn, _ in all_asns})
    prefix_semaphore = get_semaphore_for_testing(ConcurrencyCategory.BGP_QUERY)

    async def _asn_prefixes(asn: int) -> list[BGPFinding]:
        nonlocal last_request
        async with prefix_semaphore:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit_s:
                await asyncio.sleep(rate_limit_s - elapsed)
            last_request = time.monotonic()
            return await asn_to_prefixes(asn, session)
    _result = await parallel([_asn_prefixes(asn) for asn in unique_asns], taskgroup=True, policy='collect', ctx='bgp_asn_prefixes', logger_instance=logger)
    prefix_results: list[Any] = _result.ok
    findings: list[BGPFinding] = []
    for res in prefix_results:
        if isinstance(res, list):
            findings.extend(res)
    return findings

class BGPAdapter:
    """
    Adapter for SprintScheduler integration.

    Provides a simple `enrich(ip)` interface that returns BGPResult
    with all ASN/org/prefix data. Fail-soft throughout.
    """
    __slots__ = tuple(('_session', '_session_provider', '_stats'))

    def __init__(self, session_provider: Callable[[], Awaitable[httpx.AsyncClient]] | None=None) -> None:
        self._session: httpx.AsyncClient | None = None
        self._session_provider = session_provider
        self._stats: dict[str, int] = {'ips_processed': 0, 'orgs_processed': 0, 'asns_resolved': 0, 'prefixes_collected': 0, 'errors': 0}

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session_provider is not None:
            return await self._session_provider()
        if self._session is None or self._session.is_closed:
            self._session = await session_pool.httpx()
        return self._session

    async def close(self) -> None:
        if self._session is not None and (not self._session.is_closed):
            await self._session.aclose()
            self._session = None

    async def enrich_ip(self, ip: str) -> BGPResult:
        """Resolve single IP → BGPResult."""
        start = time.monotonic()
        session = await self._ensure_session()
        try:
            finding = await ip_to_asn(ip, session)
            if finding is None:
                return BGPResult(ip=ip, error='no_bgp_data', duration_s=time.monotonic() - start)
            self._stats['ips_processed'] += 1
            self._stats['asns_resolved'] += 1
            return BGPResult(ip=ip, asn=finding.asn, org_name=finding.asn_name, country_code=finding.country_code, prefix=finding.prefix, rir=finding.rir, duration_s=time.monotonic() - start)
        except Exception as e:
            logger.debug(f'BGPAdapter.enrich_ip({ip}): {e}')
            self._stats['errors'] += 1
            return BGPResult(ip=ip, error=str(e), duration_s=time.monotonic() - start)

    async def enrich_ips(self, ips: list[str]) -> list[BGPResult]:
        """Batch resolve IPs → list of BGPResult."""
        if not ips:
            return []
        session = await self._ensure_session()
        findings = await ip_bulk_to_asn(ips, session)
        self._stats['ips_processed'] += len(ips)
        self._stats['asns_resolved'] += len(findings)
        found_asns = {f.query_ip: f for f in findings}
        results = []
        for ip in ips:
            if ip in found_asns:
                f = found_asns[ip]
                results.append(BGPResult(ip=ip, asn=f.asn, org_name=f.asn_name, country_code=f.country_code, prefix=f.prefix, rir=f.rir))
            else:
                results.append(BGPResult(ip=ip, error='not_found'))
        return results

    async def enrich_org(self, org_query: str) -> list[BGPFinding]:
        """Search org → return all BGPFindings (ASNs + prefixes)."""
        time.monotonic()
        session = await self._ensure_session()
        self._stats['orgs_processed'] += 1
        asns = await org_to_asns(org_query, session)
        if not asns:
            return []
        unique_asns = list({f.asn for f in asns if f.asn})
        all_findings = list(asns)
        for asn in unique_asns:
            prefixes = await asn_to_prefixes(asn, session)
            all_findings.extend(prefixes)
            self._stats['prefixes_collected'] += len(prefixes)
        self._stats['asns_resolved'] += len(unique_asns)
        return all_findings

    def get_stats(self) -> dict:
        return self._stats.copy()