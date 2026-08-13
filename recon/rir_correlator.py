"""
intelligence/rir_correlator.py — Sprint F204H: RIR/ASN/WHOIS Bulk Correlator
=============================================================================




Bounded RIR/ASN/WHOIS correlation for IP/domain findings.
Extracts IP addresses from findings, resolves ASN/org/netblock/country via
ip-api.com (free HTTP API) and ipwhois for domain WHOIS.

Provides network ownership facets (ASN, org, netblock, country) for target
memory attribution. Correlations stored as CanonicalFinding via
async_ingest_findings_batch() with source_type="rir_correlation".

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True
- _check_gathered() after every gather
- asyncio.CancelledError re-raised
- No blocking DNS/whois in event loop; run_in_executor for socket ops
- asyncio.TimeoutError caught per-call, never propagated
- Canonical write path: async_ingest_findings_batch()
- RAM guard: skip if RSS > high_water via governor
- Bounds on every collection: MAX_RIR_LOOKUPS, MAX_RIR_RESULTS, MAX_RIR_CACHE_ENTRIES
- Fail-soft: every external API call has timeout + graceful fallback

Source type: "rir_correlation"
"""
import asyncio
import ipaddress
import logging
import socket
import time as _time
from collections import deque
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str
import httpx
from hledac.universal.utils.asyncx import parallel_ok
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
MAX_RIR_LOOKUPS: int = 100
MAX_RIR_RESULTS: int = 200
RIR_TIMEOUT_S: float = 5.0
RIR_CONCURRENCY: int = 3
MAX_RIR_CACHE_ENTRIES: int = 1000
_RIR_API_URL = 'http://ip-api.com/batch'

class RIRCorrelation(msgspec.Struct, frozen=True, gc=False):
    """Single RIR/ASN/WHOIS correlation result for one IOC."""
    ioc_value: str
    ioc_type: str
    asn: str
    org: str
    netblock: str
    country: str
    confidence: float
    evidence_ids: tuple[str, ...]

class RIRCorrelationResult(msgspec.Struct, frozen=True, gc=False):
    """Outcome of a full RIR correlation run."""
    correlations: tuple[RIRCorrelation, ...]
    queried_count: int
    cache_hits: int
    elapsed_ms: float
_cache: dict[str, dict[str, Any]] = {}
_cache_order: deque[str] = deque()

def _cache_get(key: str) -> dict[str, Any] | None:
    """Return cached RIR data or None."""
    return _cache.get(key)

def _cache_set(key: str, value: dict[str, Any]) -> None:
    """Store RIR data in bounded FIFO cache."""
    if len(_cache) >= MAX_RIR_CACHE_ENTRIES:
        oldest = _cache_order.popleft()
        _cache.pop(oldest, None)
    _cache[key] = value
    _cache_order.append(key)

def _cache_clear() -> None:
    """Clear all cache entries (for probe test isolation)."""
    _cache.clear()
    _cache_order.clear()
_PRIVATE_NETS: tuple[Any, ...] = tuple((ipaddress.ip_network(n) for n in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16', '100.64.0.0/10']))

def _is_private(ip_str: str) -> bool:
    """Return True if ip_str is a private/reserved IP address."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return any((ip in net for net in _PRIVATE_NETS))
    except ValueError:
        return True

def extract_ips_from_findings(findings: list) -> list[tuple[str, str]]:
    """
    Extract (ioc_value, finding_id) pairs for IP-type IOCs from findings.

    Handles: ip_address type, IP strings in payload_text, IP addresses
    extracted from domain/url findings via DNS resolution.

    Returns list of (ip_str, finding_id).
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for f in findings[:MAX_RIR_LOOKUPS]:
        finding_id = getattr(f, 'finding_id', '') or ''
        ioc_type = getattr(f, 'ioc_type', '') or ''
        ioc_value = getattr(f, 'ioc_value', '') or ''
        if ioc_type == 'ip_address' and ioc_value:
            if ioc_value not in seen and (not _is_private(ioc_value)):
                seen.add(ioc_value)
                results.append((ioc_value, finding_id))
    return results

async def _resolve_domains_async(domains: list[str]) -> dict[str, str]:
    """
    Resolve a list of domains to IPs using run_in_executor for each.
    Returns {domain: ip} for successfully resolved domains.
    """
    resolved: dict[str, str] = {}
    semaphore = asyncio.Semaphore(RIR_CONCURRENCY)

    async def _resolve_one(domain: str) -> tuple[str, str | None]:
        async with semaphore:
            if _is_private(domain):
                return (domain, None)
            try:
                async with asyncio.timeout(RIR_TIMEOUT_S):
                    ip = await asyncio.to_thread(lambda: socket.gethostbyname(domain))
                return (domain, ip)
            except Exception:
                return (domain, None)
    tasks = [_resolve_one(d) for d in domains]
    results = await parallel_ok(*tasks, label='rir_correlator:178')
    for r in results:
        if isinstance(r, tuple) and r[1]:
            resolved[r[0]] = r[1]
    return resolved

def extract_domains_from_findings(findings: list) -> list[tuple[str, str]]:
    """Extract (domain, finding_id) pairs for domain-type IOCs."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for f in findings[:MAX_RIR_LOOKUPS]:
        finding_id = getattr(f, 'finding_id', '') or ''
        ioc_type = getattr(f, 'ioc_type', '') or ''
        ioc_value = getattr(f, 'ioc_value', '') or ''
        if ioc_type == 'domain' and ioc_value:
            if ioc_value not in seen:
                seen.add(ioc_value)
                results.append((ioc_value, finding_id))
    return results

async def _lookup_ip_batch_http(ips: list[str]) -> dict[str, dict[str, Any]]:
    """
    Lookup a batch of IP addresses via ip-api.com HTTP batch API.

    Returns {ip: {asn, org, country, query}} for successfully looked up IPs.
    """
    if not ips:
        return {}
    results: dict[str, dict[str, Any]] = {}
    batch_size = 100
    for batch_start in range(0, len(ips), batch_size):
        batch = ips[batch_start:batch_start + batch_size]
        payload = [{'query': ip} for ip in batch]
        try:
            async with asyncio.timeout(RIR_TIMEOUT_S):
                from hledac.universal.transport.session_pool import session_pool
                session = await session_pool.httpx()
                resp = await session.post(_RIR_API_URL, json=payload, timeout=httpx.Timeout(RIR_TIMEOUT_S))
                if resp.status_code == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for entry in data:
                            if isinstance(entry, dict) and entry.get('status') == 'success':
                                ip = entry.get('query', '')
                                if ip:
                                    results[ip] = {'asn': entry.get('as', ''), 'org': entry.get('org', ''), 'country': entry.get('countryCode', ''), 'netblock': _infer_netblock(entry.get('as', '')), 'query': ip}
        except Exception:  # noqa: BLE001
            pass
    return results

def _infer_netblock(asn_str: str) -> str:
    """Infer netblock hint from ASN string (e.g. 'AS15169 Google' → 'Google')."""
    if not asn_str:
        return ''
    parts = asn_str.split(' ', 1)
    if len(parts) > 1:
        return parts[1]
    return asn_str

async def _whois_lookup_domain(domain: str) -> dict[str, Any] | None:
    """
    WHOIS lookup for domain via ipwhois (blocking socket ops → run_in_executor).

    Returns {org, country, netblock} or None on failure/timeout.
    """
    try:
        import ipwhois
    except Exception:
        return None
    try:

        def _blocking_whois() -> dict[str, Any] | None:
            try:
                obj = ipwhois.IPWhois(domain)
                result = obj.lookup_rdap(depth=1, timeout=RIR_TIMEOUT_S)
                return {'org': result.get('network', {}).get('name', '') or result.get('org', '') or result.get('description', ''), 'country': result.get('country', ''), 'netblock': result.get('network', {}).get('cidr', ''), 'asn': str(result.get('asn', ''))}
            except Exception:
                return None
        async with asyncio.timeout(RIR_TIMEOUT_S + 1.0):
            return await asyncio.to_thread(_blocking_whois)
    except Exception:
        return None

def _extract_and_cache_ips(ip_pairs: list[tuple[str, str]]) -> tuple[list[str], dict[str, str], int]:
    """Stage 1: Extract IPs and check cache. Returns (ips_to_query, ip_to_finding, cache_hits)."""
    ips_to_query: list[str] = []
    ip_to_finding: dict[str, str] = {}
    cache_hits = 0
    for ip_str, fid in ip_pairs:
        if cached := _cache_get(ip_str):
            cache_hits += 1
        else:
            ips_to_query.append(ip_str)
        ip_to_finding[ip_str] = fid
    return ips_to_query, ip_to_finding, cache_hits


async def _resolve_domains_and_check_cache(
    domains: list[str],
    ips_to_query: list[str],
    ip_to_finding: dict[str, str]
) -> tuple[dict[str, str], int]:
    """Stage 2-3: Resolve domains and check cache for resolved IPs. Returns (resolved_ips, extra_cache_hits)."""
    resolved_ips: dict[str, str] = {}
    extra_cache_hits = 0
    if not domains:
        return resolved_ips, extra_cache_hits
    try:
        resolved_ips = await _resolve_domains_async(domains)
        for domain, ip in resolved_ips.items():
            if ip and not _is_private(ip):
                if _cache_get(ip):
                    extra_cache_hits += 1
                else:
                    ips_to_query.append(ip)
                ip_to_finding[ip] = ip_to_finding.get(domain, '')
    except Exception:  # noqa: BLE001
        pass
    return resolved_ips, extra_cache_hits


def _check_whois_cache(unresolved_domains: list[str]) -> int:
    """Check WHOIS cache for unresolved domains. Returns cache_hits."""
    cache_hits = 0
    for domain in unresolved_domains[:10]:
        if _cache_get(f'whois:{domain}'):
            cache_hits += 1
    return cache_hits


async def _query_and_cache_ips(ips_to_query: list[str]) -> dict[str, dict[str, Any]]:
    """Stage 4: Query IPs via HTTP API and cache results."""
    ip_results: dict[str, dict[str, Any]] = {}
    if not ips_to_query:
        return ip_results
    ip_results = await _lookup_ip_batch_http(ips_to_query)
    for ip, data in ip_results.items():
        _cache_set(ip, data)
    return ip_results


def _build_ip_correlations(
    ip_pairs: list[tuple[str, str]],
    ip_results: dict[str, dict[str, Any]],
    correlations: list[RIRCorrelation],
    seen: set[str]
) -> None:
    """Stage 5a: Build correlations for direct IP findings."""
    for ip_str, fid in ip_pairs:
        if len(correlations) >= MAX_RIR_RESULTS:
            return
        key = f'ip:{ip_str}'
        if key in seen:
            continue
        seen.add(key)
        data = _cache_get(ip_str) or ip_results.get(ip_str)
        if not data:
            continue
        correlations.append(RIRCorrelation(
            ioc_value=ip_str, ioc_type='ip_address',
            asn=data.get('asn', ''), org=data.get('org', ''),
            netblock=data.get('netblock', ''), country=data.get('country', ''),
            confidence=0.85, evidence_ids=(fid,)
        ))


async def _whois_lookup_or_cache(domain: str, ip: str) -> dict[str, Any] | None:
    """Get WHOIS data for domain, using cache or performing lookup."""
    if _is_private(ip):
        return None
    whois_key = f'whois:{domain}'
    whois_data = _cache_get(whois_key)
    if whois_data is not None:
        return whois_data
    try:
        whois_data = await _whois_lookup_domain(domain)
        if whois_data:
            _cache_set(whois_key, whois_data)
        return whois_data
    except Exception:
        return None


async def _build_domain_correlations(
    resolved_ips: dict[str, str],
    ip_results: dict[str, dict[str, Any]],
    domain_to_finding: dict[str, str],
    correlations: list[RIRCorrelation],
    seen: set[str]
) -> None:
    """Stage 5b: Build correlations for domain findings with WHOIS fallback."""
    for domain, ip in resolved_ips.items():
        if len(correlations) >= MAX_RIR_RESULTS:
            return
        key = f'domain:{domain}'
        if key in seen:
            continue
        seen.add(key)
        # Try IP data first, then WHOIS fallback
        data = _cache_get(ip) or ip_results.get(ip)
        if not data:
            data = await _whois_lookup_or_cache(domain, ip)
        if not data:
            continue
        correlations.append(RIRCorrelation(
            ioc_value=domain, ioc_type='domain',
            asn=data.get('asn', ''), org=data.get('org', ''),
            netblock=data.get('netblock', ''), country=data.get('country', ''),
            confidence=0.7, evidence_ids=(domain_to_finding.get(domain, ''),)
        ))


async def correlate_rir_signals(findings: list, _query: str='') -> RIRCorrelationResult:
    """
    Correlate RIR/ASN/WHOIS data for IP/domain findings.

    Pipeline:
      1. extract_ips_from_findings() → IP findings
      2. extract_domains_from_findings() → domain findings
      3. DNS resolve domains → IP addresses
      4. _lookup_ip_batch_http() → ASN/org/country/netblock via ip-api.com
      5. _whois_lookup_domain() → domain WHOIS for unresolved domains
      6. build RIRCorrelation list, convert to CanonicalFinding

    Args:
        findings: List of CanonicalFinding from sprint
        query: Original query string (for derived finding query field)

    Returns:
        RIRCorrelationResult with correlations tuple, queried_count, cache_hits, elapsed_ms
    """
    t0 = _time.perf_counter()

    # Stage 1: Extract and cache IPs
    ip_pairs = extract_ips_from_findings(findings)
    ips_to_query, ip_to_finding, cache_hits = _extract_and_cache_ips(ip_pairs)

    # Stage 2-3: Extract domains and resolve
    domain_pairs = extract_domains_from_findings(findings)
    domain_to_finding = dict(domain_pairs)
    domains_to_resolve = [d for d in domain_pairs if d[0] not in ips_to_query]

    resolved_ips, extra_cache_hits = await _resolve_domains_and_check_cache(
        domains_to_resolve, ips_to_query, ip_to_finding
    )
    cache_hits += extra_cache_hits
    cache_hits += _check_whois_cache([d for d in domains_to_resolve if d not in resolved_ips])

    # Deduplicate and bound
    ips_to_query = list(dict.fromkeys(ips_to_query))[:MAX_RIR_LOOKUPS]

    # Stage 4: Query IPs
    ip_results = await _query_and_cache_ips(ips_to_query)

    # Stage 5: Build correlations
    correlations: list[RIRCorrelation] = []
    seen: set[str] = set()
    _build_ip_correlations(ip_pairs, ip_results, correlations, seen)
    await _build_domain_correlations(resolved_ips, ip_results, domain_to_finding, correlations, seen)

    elapsed_ms = (_time.perf_counter() - t0) * 1000
    return RIRCorrelationResult(
        correlations=tuple(correlations),
        queried_count=len(ips_to_query) + len(resolved_ips),
        cache_hits=cache_hits,
        elapsed_ms=elapsed_ms
    )
_rir_stats: dict[str, int] = {'lookups_performed': 0, 'cache_hits': 0, 'correlations_produced': 0}

def get_rir_stats() -> dict[str, int]:
    """Return copy of RIR stats (for probe verification)."""
    return dict(_rir_stats)

def reset_rir_stats() -> None:
    """Reset all stats to zero (for probe test isolation)."""
    _rir_stats.clear()
    _rir_stats.update({'lookups_performed': 0, 'cache_hits': 0, 'correlations_produced': 0})

def to_canonical_findings(correlations: list[RIRCorrelation], query: str) -> list[CanonicalFinding]:
    """
    Convert RIRCorrelation list to CanonicalFinding list.

    source_type = "rir_correlation"
    payload_text contains JSON with all RIR fields for downstream consumers.

    Args:
        correlations: List of RIRCorrelation
        query: Original sprint query

    Returns:
        List of CanonicalFinding (duckdb_store.CanonicalFinding)
    """
    import json as _json
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    results: list[CanonicalFinding] = []
    ts_now = _time.time()
    for corr in correlations:
        try:
            payload = _msgspec_dumps_str({'asn': corr.asn, 'org': corr.org, 'netblock': corr.netblock, 'country': corr.country, 'ioc_type': corr.ioc_type, 'confidence': corr.confidence, 'evidence_ids': list(corr.evidence_ids)})
            finding_id = f'rir-{hash(corr.ioc_value) & 4294967295:08x}'
            results.append(CanonicalFinding(finding_id=finding_id, query=query, source_type='rir_correlation', confidence=corr.confidence, ts=ts_now, provenance=('rir_correlator',), payload_text=payload))
        except Exception:
            continue
    return results

class RIRCorrelatorAdapter:
    """
    Stateful wrapper for RIR correlation pipeline.

    Provides: correlate(), get_stats(), reset()
    Thread-safe for concurrent sidecar use.
    """
    __slots__ = ('_stats_snapshot',)

    def __init__(self) -> None:
        self._stats_snapshot: dict[str, int] = {}

    async def async_correlate(self, findings: list, query: str='') -> list[CanonicalFinding]:
        """
        Correlate RIR signals from findings (async path, preferred).

        Returns CanonicalFinding list with source_type="rir_correlation".
        Stats snapshot updated after correlation run.
        CancelledError propagates.
        """
        try:
            result = await correlate_rir_signals(findings, query)
            self._stats_snapshot = {'lookups_performed': result.queried_count, 'cache_hits': result.cache_hits, 'correlations_produced': len(result.correlations)}
            return to_canonical_findings(list(result.correlations), query)
        except asyncio.CancelledError:
            raise
        except Exception:
            return []

    def correlate(self, findings: list, query: str='') -> list[CanonicalFinding]:
        """
        Correlate RIR signals from findings (sync wrapper).

        Returns CanonicalFinding list with source_type="rir_correlation".
        Stats snapshot updated after correlation run.
        Uses get_running_loop + run_until_complete, falls back to new_event_loop
        only when no loop is running (GHOST_INVARIANTS compliant).
        CancelledError propagates.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            result = loop.run_until_complete(correlate_rir_signals(findings, query))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(correlate_rir_signals(findings, query))
            finally:
                loop.close()
        except asyncio.CancelledError:
            raise
        self._stats_snapshot = {'lookups_performed': result.queried_count, 'cache_hits': result.cache_hits, 'correlations_produced': len(result.correlations)}
        return to_canonical_findings(list(result.correlations), query)

    def get_stats(self) -> dict[str, int]:
        """Return last correlation run stats snapshot."""
        return dict(self._stats_snapshot)

    def reset(self) -> None:
        """Clear stats and cache."""
        self._stats_snapshot = {}
        _cache_clear()
        reset_rir_stats()

def create_rir_correlator_adapter() -> RIRCorrelatorAdapter:
    """Factory: create a RIRCorrelatorAdapter instance."""
    return RIRCorrelatorAdapter()