"""
discovery/crtsh_adapter.py — CT/crt.sh Providerless Pivot Adapter

Sprint F206AV: transport alignment with canonical session_runtime + circuit_breaker.




Replaces local httpx.AsyncClient + local checked_aiohttp_get with:
- async_get_httpx_session() from network.session_runtime
- checked_aiohttp_get() from transport.circuit_breaker

Passive only — no auth/API key, no body fetch beyond crt.sh JSON endpoint.
Fail-soft throughout.
"""
import asyncio
from hledac.universal.utils.asyncx import parallel
import logging
import re
import time
from dataclasses import dataclass
import msgspec
from enum import Enum
from pathlib import Path
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get, domain_breaker_check
from .base import DiscoveryBatchResult, DiscoveryHit
from hledac.universal.discovery.base import BaseDiscoveryMixin, DiscoveryResult
from _core import aclose
__all__ = ['call_crtsh', 'CTOutcome', 'CTProviderStatus']


# =============================================================================
# Factory Functions - Reduce Clone Code (89 pairs eliminated)
# =============================================================================

def _make_ct_outcome(
    query: str,
    raw_count: int,
    built_count: int,
    error: str | None,
    elapsed: float,
    provider_status: 'CTProviderStatus',
    timeout: bool = False,
    ct_cache_used: bool = False,
    ct_cache_stale: bool = False,
    ct_cache_age_s: float = 0.0,
) -> 'CTOutcome':
    """Factory: Build CTOutcome with common parameters."""
    return CTOutcome(
        attempted=True,
        query=query,
        raw_count=raw_count,
        built_count=built_count,
        error=error,
        timeout=timeout,
        duration_s=elapsed,
        ct_cache_used=ct_cache_used,
        ct_cache_stale=ct_cache_stale,
        ct_cache_age_s=ct_cache_age_s,
        provider_status=provider_status,
    )


def _make_discovery_result(
    hits: tuple[DiscoveryHit, ...],
    error: str | None,
    error_type: str,
    elapsed: float,
    provider_chain: tuple[str, ...] = ('crtsh',),
) -> DiscoveryBatchResult:
    """Factory: Build DiscoveryBatchResult with common parameters."""
    return DiscoveryBatchResult(
        hits=hits,
        error=error,
        error_type=error_type,
        provider_name='crtsh',
        provider_chain=provider_chain,
        source_family='ct',
        elapsed_s=elapsed,
    )


def _make_stale_cache_response(
    hits: list[DiscoveryHit],
    raw_count: int,
    query: str,
    error_prefix: str,
    error_type: str,
    stale_age: float,
    elapsed: float,
    provider_status: 'CTProviderStatus',
) -> tuple[DiscoveryBatchResult, 'CTOutcome']:
    """Factory: Build stale cache hit response."""
    outcome = _make_ct_outcome(
        query=query,
        raw_count=raw_count,
        built_count=len(hits),
        error=f'{error_prefix}_stale_cache',
        elapsed=elapsed,
        provider_status=provider_status,
        ct_cache_used=True,
        ct_cache_stale=True,
        ct_cache_age_s=stale_age,
    )
    result = _make_discovery_result(
        hits=tuple(hits),
        error=f'{error_prefix}_stale_cache',
        error_type=error_type,
        elapsed=elapsed,
    )
    return (result, outcome)


def _make_error_response(
    error: str,
    error_type: str,
    query: str,
    elapsed: float,
    provider_status: 'CTProviderStatus',
    raw_count: int = 0,
    built_count: int = 0,
) -> tuple[DiscoveryBatchResult, 'CTOutcome']:
    """Factory: Build error response."""
    outcome = _make_ct_outcome(
        query=query,
        raw_count=raw_count,
        built_count=built_count,
        error=error,
        elapsed=elapsed,
        provider_status=provider_status,
    )
    result = _make_discovery_result(
        hits=(),
        error=error,
        error_type=error_type,
        elapsed=elapsed,
    )
    return (result, outcome)


def _make_success_response(
    hits: list[DiscoveryHit],
    raw_count: int,
    query: str,
    elapsed: float,
) -> tuple[DiscoveryBatchResult, 'CTOutcome']:
    """Factory: Build success response."""
    outcome = _make_ct_outcome(
        query=query,
        raw_count=raw_count,
        built_count=len(hits),
        error=None,
        elapsed=elapsed,
        provider_status=CTProviderStatus.OK,
    )
    result = _make_discovery_result(
        hits=tuple(hits),
        error=None,
        error_type='none',
        elapsed=elapsed,
    )
    return (result, outcome)


class CTProviderStatus(Enum):
    """F217D: Explicit CT provider status tags. F219E adds cooldown states."""
    OK = 'ok'
    HTTP_5XX = 'http_5xx'
    HTTP_4XX = 'http_4xx'
    TIMEOUT = 'timeout'
    PARSE_ERROR = 'parse_error'
    EMPTY = 'empty'
    DISABLED = 'disabled'
    CACHE_HIT_STALE = 'cache_hit_stale'
    COOLDOWN_ACTIVE = 'cooldown_active'
    PROVIDER_FAILURE = 'provider_failure'

class CTProviderStatusReport(msgspec.Struct, frozen=True, gc=False):
    """
    F217D: Explicit CT provider status report with bounded error sampling.
    F219E adds cooldown fields.

    Fields:
        provider_name:    Always "crtsh".
        attempted:       True if HTTP call was attempted (also True on cache hit).
        status:          CTProviderStatus tag.
        raw_count:       Certs from live call or cached response (0 if no call and no cache).
        error_sample:    Bounded error message (max 200 chars, None on success).
        ct_cache_used:   True if response came from stale cache.
        ct_cache_stale:  True if cached response was stale when served.
        ct_cache_age_s: Seconds since cache file was written (0 if not cached).
        # F219E: cooldown fields
        cooldown_active:              True if provider is in cooldown for this key.
        cooldown_reason:              Reason cooldown was entered (None if not in cooldown).
        cooldown_remaining_s:         Seconds remaining in cooldown (0 if not in cooldown).
        cooldown_started_at_monotonic: Monotonic timestamp when cooldown started (0 if not in cooldown).
        stale_cache_preferred:        True if stale cache was preferred due to cooldown.
        provider_attempt_suppressed:  True if provider call was suppressed due to cooldown.
    """
    provider_name: str = 'crtsh'
    attempted: bool = False
    status: CTProviderStatus = CTProviderStatus.DISABLED
    raw_count: int = 0
    error_sample: str | None = None
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    cooldown_active: bool = False
    cooldown_reason: str | None = None
    cooldown_remaining_s: float = 0.0
    cooldown_started_at_monotonic: float = 0.0
    stale_cache_preferred: bool = False
    provider_attempt_suppressed: bool = False

class CTOutcome(msgspec.Struct, frozen=True, gc=False):
    """
    Normalized CT adapter outcome — F207F, extended F217D with cache fields.

    Fields:
        attempted:    True if HTTP call was attempted (also True on cache hit).
        query:        Domain/query that was submitted.
        raw_count:    Certs received from crt.sh before filtering (0 if not attempted).
        built_count:  DiscoveryHit records built after filtering (0 if not attempted).
        accepted_count: Always None for CT — lane owns acceptance decision.
        error:        Error tag string or None on success.
        timeout:      True if call timed out.
        duration_s:   Wall-clock seconds for the call.
        skip_reason:  Reason for skip or None if attempted/errored.
        # F217D: cache fields
        ct_cache_used:  True if response was served from stale cache.
        ct_cache_stale: True if cached response was already stale when served.
        ct_cache_age_s: Seconds since cache was written (0 if not cached).
        provider_status: CTProviderStatus enum tag for explicit provider state.
    """
    attempted: bool = False
    query: str = ''
    raw_count: int = 0
    built_count: int = 0
    accepted_count: None = None
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    provider_status: CTProviderStatus = CTProviderStatus.DISABLED
logger = logging.getLogger(__name__)
_MAX_CERTS = 50
_MAX_HITS = 20
_CRTSH_URL = 'https://crt.sh/'
_CERTSPOTTER_URL = 'https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names'
_CERTSPOTTER_RATE_LIMIT_S = 3.0
_HTTP_TIMEOUT_S = 8.0
_STALE_THRESHOLD_S = 604800
_COOLDOWN_DEFAULT_S = 300.0
_MAX_COOLDOWN_KEYS = 256
_PRIVATE_HOSTNAMES = {'localhost', 'invalid', 'test'}
_WILDCARD_ONLY_RE = re.compile('^\\*\\.')
_STOPWORDS = {'report', 'operation', 'campaign', 'tool', 'framework', 'payload', 'group', 'actor', 'attack', 'security', 'alert', 'tracker', 'intel', 'feed', 'platform', 'portal', 'api', 'monitor', 'scan', 'map', 'probe', 'watch', 'data', 'open', 'source', 'system', 'network', 'target', 'domain', 'host', 'server', 'client', 'user', 'password', 'email', 'name', 'id', 'ip', 'url', 'web', 'site', 'com', 'net', 'org', 'info', 'io', 'dev', ' Ryuk', ' Hive'}

def _build_crtsh_queries(seed: str) -> list[str]:
    """
    Build structured crt.sh wildcard queries from sprint seed.

    Uses domain wildcard API (fast, <2s) instead of full-text search (slow,
    timeout-prone). Generates %.{term}.{tld} patterns for each significant
    alphanum term found in the seed.

    Returns up to 36 URLs (max 6 terms × 6 TLDs). Empty list if no qualifying
    terms found.
    """
    term_bucket: list[str] = []
    for token in re.findall('[a-zA-Z0-9]{3,}', seed):
        lowered = token.lower()
        if lowered not in _STOPWORDS:
            term_bucket.append(lowered)
    seen_terms: set[str] = set()
    output_terms: list[str] = []
    for term in term_bucket:
        if term not in seen_terms:
            seen_terms.add(term)
            output_terms.append(term)
    top_terms = output_terms[:6]
    tlds = ('com', 'net', 'io', 'org', 'site', 'info')
    queries: list[str] = []
    for term in top_terms:
        for tld in tlds:
            queries.append(f'https://crt.sh/?q=%.{term}.{tld}&output=json')
    return queries

def _is_private_domain(domain: str) -> bool:
    """Return True if domain is private, internal, or reserved."""
    domain_lower = domain.lower()
    if domain_lower in _PRIVATE_HOSTNAMES:
        return True
    if _is_ip_like(domain_lower):
        return True
    return False

def _is_ip_like(value: str) -> bool:
    """Return True if value looks like an IP address (v4 or v6)."""
    if re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', value):
        return True
    if ':' in value:
        return True
    return False

def _extract_domain_from_query(query: str) -> str | None:
    """
    Extract the best domain candidate from a query string.

    If the query looks like a domain already (has dots), return it.
    Otherwise scan tokens for the first domain-like token (has at least one dot).

    Returns None if no domain-like token found.
    """
    query = query.strip()
    if not query:
        return None
    if _looks_like_domain(query):
        return query
    for token in query.split():
        token = token.strip().lower()
        if '.' in token and _looks_like_domain(token):
            parts = token.split('.')
            if len(parts) >= 2 and len(parts[0]) <= 63:
                return token
    return None

def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name (not an IP, has TLD)."""
    if _is_ip_like(value):
        return False
    if not value or len(value) > 253:
        return False
    if '.' not in value:
        return False
    parts = value.split('.')
    if len(parts) < 2:
        return False
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    if not re.match('^[a-z0-9.\\-_]+$', tld):
        return False
    return True

def _is_wildcard_only(domain: str) -> bool:
    """Return True if domain is a wildcard cert (e.g. '*.example.com')."""
    return bool(_WILDCARD_ONLY_RE.match(domain))

def _make_cache_key(domain: str) -> str:
    """Make a cache key for a domain using xxhash."""
    try:
        import xxhash
        return f'{xxhash.xxh3_64(domain.encode()).hexdigest()}.json'
    except ImportError:
        import hashlib
        return f'{hashlib.sha256(domain.encode()).hexdigest()[:16]}.json'

def _read_stale_cache(domain: str, cache_dir: Path | None, max_age_s: float) -> tuple[list | None, float]:
    """
    F217D: Read a stale cache entry for diagnostic reuse.

    Returns (raw_data, age_s) if a cache file exists and is within max_age_s.
    Returns (None, 0.0) if no cache or cache is older than max_age_s.

    This does NOT count as fresh accepted evidence — callers must set
    ct_cache_used=True, ct_cache_stale=True on the returned outcome.
    """
    if cache_dir is None:
        return (None, 0.0)
    cache_key = _make_cache_key(domain)
    cache_path = cache_dir / cache_key
    if not cache_path.exists():
        return (None, 0.0)
    age_s = time.time() - cache_path.stat().st_mtime
    if age_s > max_age_s:
        return (None, 0.0)
    try:
        import orjson
        raw_data = orjson.loads(cache_path.read_bytes())
        return (raw_data, age_s)
    except Exception:
        return (None, 0.0)

def _build_hits_from_raw(raw_data: list, domain_candidate: str, query: str, max_results: int) -> tuple[list[DiscoveryHit], int]:
    """
    F217D: Build DiscoveryHit list from raw crt.sh JSON data (live or cached).

    Used by stale-cache fallback path. Returns (hits, raw_count) where raw_count
    is the total certs before filtering (diagnostic signal, not accepted evidence).
    """
    seen_domains: set[str] = set()
    hits: list[DiscoveryHit] = []
    now = time.time()
    raw_count = len(raw_data) if isinstance(raw_data, list) else 0

    def _process_cert(cert: dict, name_value: str) -> None:
        """Process a single certificate and add valid subdomains to hits."""
        for subdomain in name_value.split('\n'):
            subdomain = subdomain.strip()
            if not subdomain or _is_wildcard_only(subdomain) or _is_private_domain(subdomain):
                continue
            subdomain_lower = subdomain.lower()
            if subdomain_lower in seen_domains or len(hits) >= max_results:
                continue
            seen_domains.add(subdomain_lower)
            hits.append(DiscoveryHit(
                query=query, title=f'CT: {subdomain}', url=f'https://{subdomain}/',
                snippet=f'Certificate Transparency match via crt.sh — {subdomain}',
                source='crtsh', rank=len(hits), retrieved_ts=now,
                score=1.0 - len(hits) / max_results, reason='ct_subdomain',
                ct_name_value=name_value, ct_common_name=cert.get('common_name'),
                ct_issuer_name=cert.get('issuer_name'), ct_not_before=cert.get('not_before'),
                ct_not_after=cert.get('not_after'), ct_entry_timestamp=cert.get('entry_timestamp'),
                ct_serial_number=cert.get('serial_number')
            ))

    for cert in (raw_data if isinstance(raw_data, list) else [])[:_MAX_CERTS]:
        if isinstance(cert, dict) and cert.get('name_value'):
            _process_cert(cert, cert['name_value'])
        if len(hits) >= max_results:
            break
    return (hits, raw_count)

async def _fetch_certspotter_fallback(session: httpx.AsyncClient, domain: str, timeout: httpx.Timeout) -> tuple[list | None, int, str | None]:
    """
    F285: Fetch CT entries from certspotter.io when crt.sh circuit breaker is OPEN.

    Returns (raw_entries, status, err) — mirrors the crt.sh response format.
    certspotter returns: [{dns_names: [...], serial_number: ..., issuer: ...}]
    We convert to crt.sh format: [{name_value: ..., issuer_name: ..., ...}]
    """
    url = _CERTSPOTTER_URL.format(domain=domain)
    raw, status, err = await checked_aiohttp_get(session, url, timeout=timeout, failure_kind='certspotter_ct')
    if err or not isinstance(raw, list):
        return (None, status or 0, err or 'certspotter_parse_error')
    entries: list[dict] = []
    for item in raw[:50]:
        dns_names = item.get('dns_names', [])
        if not isinstance(dns_names, list):
            continue
        for name in dns_names:
            name = name.strip().lstrip('*.')
            if name and '.' in name and (len(name) < 253):
                entries.append({'name_value': name, 'issuer_name': item.get('issuer', {}).get('name', ''), 'not_before': item.get('not_before', ''), 'not_after': item.get('not_after', ''), 'serial_number': item.get('serial_number', '')})
    if not entries:
        return (None, status, 'certspotter_empty')
    return (entries, status, None)
_ct_provider_cooldown: dict[str, tuple[float, str]] = {}

def _enter_cooldown(domain: str, reason: str, now: float) -> None:
    """
    F219E: Enter cooldown for a domain after provider failure.

    Bounds: max _MAX_COOLDOWN_KEYS entries, FIFO eviction.
    """
    domain_key = domain.lower()
    if len(_ct_provider_cooldown) >= _MAX_COOLDOWN_KEYS and domain_key not in _ct_provider_cooldown:
        oldest_key = next(iter(_ct_provider_cooldown))
        _ct_provider_cooldown.pop(oldest_key, None)
    _ct_provider_cooldown[domain_key] = (now, reason)

def _check_cooldown(domain: str, now: float) -> tuple[bool, float, str]:
    """
    F219E: Check if domain is in active cooldown.

    Returns (is_cooldown_active, remaining_s, reason).
    """
    domain_key = domain.lower()
    entry = _ct_provider_cooldown.get(domain_key)
    if entry is None:
        return (False, 0.0, '')
    started_at, reason = entry
    remaining = _COOLDOWN_DEFAULT_S - (now - started_at)
    if remaining <= 0:
        _ct_provider_cooldown.pop(domain_key, None)
        return (False, 0.0, '')
    return (True, remaining, reason)

def _clear_cooldown(domain: str) -> None:
    """F219E: Clear cooldown for a domain on provider success."""
    _ct_provider_cooldown.pop(domain.lower(), None)


# =============================================================================
# call_crtsh helper functions (complexity reduction)
# =============================================================================

def _normalize_max_results(raw_max: Any) -> int:
    """Validate and normalize max_results parameter."""
    try:
        return max(1, min(int(raw_max), _MAX_HITS))
    except (TypeError, ValueError):
        return 20


async def _try_certspotter_fallback(
    query: str,
    timeout_s: float,
    circuit_reason: str,
    start: float,
) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Try certspotter as fallback when crt.sh circuit is open."""
    cs_start = time.monotonic()
    try:
        session = await async_get_httpx_session()
        cs_timeout = httpx.Timeout(min(timeout_s, _HTTP_TIMEOUT_S))
        raw, status, err = await _fetch_certspotter_fallback(session, query, cs_timeout)
        elapsed = time.monotonic() - cs_start
        if raw and isinstance(raw, list):
            hits, raw_count = _build_hits_from_raw(raw, query, query, _MAX_HITS)
            if hits:
                outcome = CTOutcome(
                    attempted=True, query=query, raw_count=raw_count, built_count=len(hits),
                    error=None, timeout=False, duration_s=elapsed, provider_status=CTProviderStatus.OK
    )
                result = DiscoveryBatchResult(
                    hits=tuple(hits)[:_MAX_HITS], error=None, error_type='ok',
                    provider_name='certspotter',
                    provider_chain=('certspotter', f'crtsh_{circuit_reason}'),
                    source_family='ct', elapsed_s=elapsed
    )
                return (result, outcome)
    except Exception:  # noqa: BLE001
        pass
    elapsed = time.monotonic() - start
    outcome = CTOutcome(
        attempted=True, query=query, raw_count=0, built_count=0,
        error='circuit_breaker_open', skip_reason=f'circuit_open:{circuit_reason}',
        duration_s=elapsed, provider_status=CTProviderStatus.PROVIDER_FAILURE
    )
    result = DiscoveryBatchResult(
        hits=(), error=f'circuit_breaker_open:{circuit_reason}',
        error_type='circuit_breaker_open', provider_name='crtsh',
        provider_chain=('crtsh',), source_family='ct', elapsed_s=elapsed
    )
    return (result, outcome)


async def _search_wildcards(
    wildcard_urls: list[str],
    query: str,
    timeout_s: float,
    start: float,
) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Search using wildcard queries when no domain candidate found."""
    all_hits: list[DiscoveryHit] = []
    all_errors: list[str] = []
    strong_error: str | None = None
    strong_tag: CTProviderStatus = CTProviderStatus.DISABLED
    search_start = time.monotonic()

    from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
    sem = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

    async def _fetch_one(url: str) -> tuple[list[DiscoveryHit], str | None, CTProviderStatus]:
        async with sem:
            try:
                session = await async_get_httpx_session()
                timeout = httpx.Timeout(min(15.0, _HTTP_TIMEOUT_S))
                data, status, err = await checked_aiohttp_get(
                    session, url, params={'output': 'json'},
                    headers={'User-Agent': 'Hledac/1.0 (research bot)'},
                    timeout=timeout, failure_kind='crtsh'
    )
                if err:
                    return ([], err, CTProviderStatus.TIMEOUT if err == 'timeout' else CTProviderStatus.HTTP_5XX)
                if status != 200:
                    return ([], f'http_{status}', CTProviderStatus.HTTP_5XX)
                hits, _ = _build_hits_from_raw(data, url, url, _MAX_HITS)
                return (hits, None, CTProviderStatus.OK)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                return ([], str(e), CTProviderStatus.DISABLED)

    try:
        async with asyncio.timeout(min(timeout_s, 15.0)):
            result = await parallel(
                [_fetch_one(u) for u in wildcard_urls],
                taskgroup=True, policy='collect', ctx='crtsh_adapter:wildcard'
    )
            results = result.ok
    except asyncio.CancelledError:
        raise
    except Exception:
        results = []

    for r in results:
        if isinstance(r, BaseException):
            all_errors.append(str(r))
            continue
        hits, err, tag = r
        if err:
            all_errors.append(err)
            if strong_error is None:
                strong_error = err
                strong_tag = tag
        else:
            all_hits.extend(hits)
            strong_tag = CTProviderStatus.OK

    elapsed = time.monotonic() - search_start
    if all_hits:
        deduped = {_hit.url: _hit for _hit in all_hits}
        final_hits = tuple(deduped.values())[:_MAX_HITS]
        final_raw = len(all_hits)
        return _make_success_response(list(final_hits), final_raw, query, elapsed)
    elif not wildcard_urls:
        elapsed = time.monotonic() - start
        outcome = CTOutcome(
            attempted=True, query=query, raw_count=0, built_count=0,
            error='no_domain_like_token', skip_reason='no_domain_like_token', duration_s=elapsed
    )
        result = DiscoveryBatchResult(
            hits=(), error='no_domain_like_token', error_type='invalid_query',
            provider_name='crtsh', provider_chain=('crtsh',), source_family='ct', elapsed_s=elapsed
    )
        return (result, outcome)
    else:
        elapsed = time.monotonic() - search_start
        outcome = CTOutcome(
            attempted=True, query=query, raw_count=0, built_count=0,
            error=strong_error or 'all_wildcard_failed',
            timeout=strong_tag == CTProviderStatus.TIMEOUT,
            duration_s=elapsed, provider_status=strong_tag
    )
        result = DiscoveryBatchResult(
            hits=(), error=strong_error or 'all_wildcard_failed',
            error_type='wildcard_exhausted', provider_name='crtsh',
            provider_chain=('crtsh',), source_family='ct', elapsed_s=elapsed
    )
        return (result, outcome)


async def _search_freetext(
    query: str,
    timeout_s: float,
    start: float,
) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Search using freetext query when no domain candidate found."""
    freetext_query = query[:200]
    freetext_url = f'https://crt.sh/?q={freetext_query}&output=json'
    search_start = time.monotonic()

    try:
        async with asyncio.timeout(min(timeout_s, 12.0)):
            session = await async_get_httpx_session()
            timeout = httpx.Timeout(min(12.0, _HTTP_TIMEOUT_S))
            data, status, err = await checked_aiohttp_get(
                session, freetext_url, params={'output': 'json'},
                headers={'User-Agent': 'Hledac/1.0 (research bot)'},
                timeout=timeout, failure_kind='crtsh'
    )
            if err is None and status == 200:
                hits, raw = _build_hits_from_raw(data, freetext_query, freetext_query, _MAX_HITS)
                if hits:
                    elapsed = time.monotonic() - search_start
                    return _make_success_response(hits, raw, freetext_query, elapsed)
    except Exception:  # noqa: BLE001
        pass

    elapsed = time.monotonic() - start
    outcome = CTOutcome(
        attempted=True, query=query, raw_count=0, built_count=0,
        error='no_domain_like_token', skip_reason='no_domain_like_token', duration_s=elapsed
    )
    result = DiscoveryBatchResult(
        hits=(), error='no_domain_like_token', error_type='invalid_query',
        provider_name='crtsh', provider_chain=('crtsh',), source_family='ct', elapsed_s=elapsed
    )
    return (result, outcome)


async def _handle_cooldown_with_cache(
    domain: str,
    query: str,
    cache_dir: Path | None,
    start: float,
    cooldown_now: float,
) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Handle cooldown state with stale cache fallback."""
    stale_data, stale_age = _read_stale_cache(domain, cache_dir, _STALE_THRESHOLD_S)
    elapsed = time.monotonic() - start

    if stale_data is not None:
        stale_hits, stale_raw = _build_hits_from_raw(stale_data, domain, query, _MAX_HITS)
        return _make_stale_cache_response(
            hits=stale_hits,
            raw_count=stale_raw,
            query=domain,
            error_prefix='cooldown',
            error_type='cooldown_cache_fallback',
            stale_age=stale_age,
            elapsed=elapsed,
            provider_status=CTProviderStatus.CACHE_HIT_STALE,
    )

    return _make_error_response(
        error='cooldown_active',
        error_type='cooldown_active',
        query=domain,
        elapsed=elapsed,
        provider_status=CTProviderStatus.COOLDOWN_ACTIVE,
    )


def _build_http_error_response(status: int, domain: str, elapsed: float, provider_status: CTProviderStatus, error: str, error_type: str) -> tuple[DiscoveryBatchResult, CTOutcome]:
    """Build a standard HTTP error response."""
    outcome = CTOutcome(
        attempted=True, query=domain, raw_count=0, built_count=0,
        error=error, duration_s=elapsed, provider_status=provider_status
    )
    result = DiscoveryBatchResult(
        hits=(), error=error, error_type=error_type,
        provider_name='crtsh', provider_chain=('crtsh',), source_family='ct', elapsed_s=elapsed
    )
    return (result, outcome)


def _handle_http_error(status: int, domain: str, cooldown_now: float, cache_dir: Path | None, elapsed: float) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Handle HTTP error status codes using dispatch table."""
    # Dispatch table for error responses
    dispatch: dict[int, tuple[str, CTProviderStatus, str]] = {
        429: ('rate_limited', CTProviderStatus.HTTP_5XX, 'http_429'),
        403: ('captcha_or_blocked', CTProviderStatus.HTTP_4XX, 'http_403'),
    }
    
    if status in dispatch:
        error, prov_status, err_type = dispatch[status]
        return _build_http_error_response(status, domain, elapsed, prov_status, error, err_type)
    
    if status >= 500:
        _enter_cooldown(domain, f'http_{status}', cooldown_now)
        stale_data, stale_age = _read_stale_cache(domain, cache_dir, _STALE_THRESHOLD_S)
        if stale_data is not None:
            stale_hits, stale_raw = _build_hits_from_raw(stale_data, domain, domain, _MAX_HITS)
            return _make_stale_cache_response(
                hits=stale_hits,
                raw_count=stale_raw,
                query=domain,
                error_prefix=f'http_{status}',
                error_type='http_5xx_cache_fallback',
                stale_age=stale_age,
                elapsed=elapsed,
                provider_status=CTProviderStatus.CACHE_HIT_STALE,
    )
        return _build_http_error_response(status, domain, elapsed, CTProviderStatus.HTTP_5XX, f'http_{status}', 'http_5xx')

    if status >= 400:
        return _build_http_error_response(status, domain, elapsed, CTProviderStatus.HTTP_4XX, f'http_{status}', 'http_4xx')

    return (None, None)


def _process_cert_entry(cert: dict, query: str, seen_domains: set[str], hits: list[DiscoveryHit], now: float) -> None:
    """Process a single certificate entry and add hits."""
    if not isinstance(cert, dict):
        return
    name_value = cert.get('name_value', '')
    if not name_value:
        return
    for subdomain in name_value.split('\n'):
        subdomain = subdomain.strip()
        if not subdomain:
            continue
        if _is_wildcard_only(subdomain) or _is_private_domain(subdomain):
            continue
        subdomain_lower = subdomain.lower()
        if subdomain_lower in seen_domains:
            continue
        if len(hits) >= _MAX_HITS:
            return
        seen_domains.add(subdomain_lower)
        hits.append(DiscoveryHit(
            query=query, title=f'CT: {subdomain}', url=f'https://{subdomain}/',
            snippet=f'Certificate Transparency match via crt.sh — {subdomain}',
            source='crtsh', rank=len(hits), retrieved_ts=now,
            score=1.0 - len(hits) / _MAX_HITS, reason='ct_subdomain',
            ct_name_value=name_value, ct_common_name=cert.get('common_name'),
            ct_issuer_name=cert.get('issuer_name'), ct_not_before=cert.get('not_before'),
            ct_not_after=cert.get('not_after'), ct_entry_timestamp=cert.get('entry_timestamp'),
            ct_serial_number=cert.get('serial_number')
        ))


def _parse_cert_data(data: list, query: str, elapsed: float) -> tuple[DiscoveryBatchResult | None, CTOutcome | None]:
    """Parse certificate data and build hits."""
    if not isinstance(data, list):
        return _make_error_response(
            error='unexpected_response_format',
            error_type='parse_error',
            query=query,
            elapsed=elapsed,
            provider_status=CTProviderStatus.PARSE_ERROR,
    )

    raw_count = len(data)
    seen_domains: set[str] = set()
    hits: list[DiscoveryHit] = []
    now = time.time()

    for cert in data[:_MAX_CERTS]:
        _process_cert_entry(cert, query, seen_domains, hits, now)

    if not hits:
        return _make_error_response(
            error='no_subdomains_found',
            error_type='provider_empty',
            query=query,
            elapsed=elapsed,
            provider_status=CTProviderStatus.EMPTY,
            raw_count=raw_count,
    )

    return _make_success_response(hits, raw_count, query, elapsed)


async def _make_crtsh_api_call(
    domain: str,
    query: str,
    timeout_s: float,
    cooldown_now: float,
    cache_dir: Path | None,
    start: float,
) -> tuple[DiscoveryBatchResult, CTOutcome]:
    """Make the main crt.sh API call and handle all response cases."""
    session = await async_get_httpx_session()
    timeout = httpx.Timeout(min(timeout_s, _HTTP_TIMEOUT_S))
    params = {'q': domain, 'output': 'json'}

    try:
        async with asyncio.timeout(timeout_s):
            data, status, err = await checked_aiohttp_get(
                session, _CRTSH_URL, params=params,
                headers={'User-Agent': 'Hledac/1.0 (research bot)'},
                timeout=timeout, failure_kind='crtsh'
    )
    except asyncio.CancelledError:
        raise

    elapsed = time.monotonic() - start

    # Handle errors
    if err:
        is_timeout = err == 'timeout'
        err_tag = 'timeout' if is_timeout else ('circuit_breaker_open' if err.startswith('circuit_breaker_open:') else 'network_error')
        _enter_cooldown(domain, err, cooldown_now)
        stale_data, stale_age = _read_stale_cache(domain, cache_dir, _STALE_THRESHOLD_S)
        if stale_data is not None and is_timeout:
            stale_hits, stale_raw = _build_hits_from_raw(stale_data, domain, query, _MAX_HITS)
            return _make_stale_cache_response(
                hits=stale_hits,
                raw_count=stale_raw,
                query=domain,
                error_prefix=err,
                error_type='timeout_cache_fallback',
                stale_age=stale_age,
                elapsed=elapsed,
                provider_status=CTProviderStatus.CACHE_HIT_STALE,
    )
        return _make_error_response(
            error=err,
            error_type=err_tag,
            query=domain,
            elapsed=elapsed,
            provider_status=CTProviderStatus.TIMEOUT if is_timeout else CTProviderStatus.HTTP_5XX,
    )

    # Handle HTTP status codes
    http_result, http_outcome = _handle_http_error(status, domain, cooldown_now, cache_dir, elapsed)
    if http_result is not None:
        return (http_result, http_outcome)

    # Parse response
    parse_result, parse_outcome = _parse_cert_data(data, query, elapsed)
    if parse_result is not None:
        _clear_cooldown(domain)
        return (parse_result, parse_outcome)

    # This should never happen - return empty result
    return _parse_cert_data([], query, elapsed)


async def _handle_crtsh_timeout(
    domain: str,
    query: str,
    timeout_s: float,
    cache_dir: Path | None,
    start: float,
) -> tuple[DiscoveryBatchResult, CTOutcome]:
    """Handle timeout case with fallback to certspotter and stale cache."""
    elapsed = time.monotonic() - start
    _enter_cooldown(domain, 'timeout', start)

    # Try certspotter fallback
    try:
        session = await async_get_httpx_session()
        cs_timeout = httpx.Timeout(min(timeout_s, _HTTP_TIMEOUT_S))
        raw, status, err = await _fetch_certspotter_fallback(session, domain, cs_timeout)
        if raw and isinstance(raw, list) and not err:
            hits, raw_count = _build_hits_from_raw(raw, domain, query, _MAX_HITS)
            if hits:
                cs_elapsed = time.monotonic() - start
                outcome = _make_ct_outcome(
                    query=domain,
                    raw_count=raw_count,
                    built_count=len(hits),
                    error=None,
                    elapsed=cs_elapsed,
                    provider_status=CTProviderStatus.OK,
                    timeout=True,
    )
                result = DiscoveryBatchResult(
                    hits=tuple(hits)[:_MAX_HITS],
                    error=None,
                    error_type='ok',
                    provider_name='certspotter',
                    provider_chain=('certspotter', 'crtsh_timeout'),
                    source_family='ct',
                    elapsed_s=cs_elapsed,
    )
                return (result, outcome)
    except Exception:  # noqa: BLE001
        pass

    # Try stale cache
    stale_data, stale_age = _read_stale_cache(domain, cache_dir, _STALE_THRESHOLD_S)
    if stale_data is not None:
        stale_hits, stale_raw = _build_hits_from_raw(stale_data, domain, query, _MAX_HITS)
        return _make_stale_cache_response(
            hits=stale_hits,
            raw_count=stale_raw,
            query=domain,
            error_prefix='timeout',
            error_type='timeout_cache_fallback',
            stale_age=stale_age,
            elapsed=elapsed,
            provider_status=CTProviderStatus.CACHE_HIT_STALE,
    )

    return _make_error_response(
        error='timeout',
        error_type='timeout',
        query=domain,
        elapsed=elapsed,
        provider_status=CTProviderStatus.TIMEOUT,
    )


async def call_crtsh(query: str, max_results: int=20, timeout_s: float=8.0, cache_dir: Path | None=None) -> tuple[DiscoveryBatchResult, CTOutcome]:
    """
    crt.sh search with normalized outcome — F207F, extended F217D.

    F217D adds stale-cache diagnostic reuse:
      - If live provider fails with HTTP 5xx/timeout and a stale cache exists
        (within _STALE_THRESHOLD_S), the cached response is returned with
        ct_cache_used=True and ct_cache_stale=True.
      - Cached raw is NOT counted as fresh accepted evidence.
      - Provider status is explicitly tagged via CTProviderStatus.

    Complexity reduction: delegates to helper functions for each phase.

    Returns:
        (DiscoveryBatchResult, CTOutcome) tuple.
    """
    start = time.monotonic()
    max_results = _normalize_max_results(max_results)
    query_stripped = query.strip() if query else ''

    # Phase 1: Empty query check
    if not query_stripped:
        elapsed = time.monotonic() - start
        return _make_error_response(
            error='empty_query', error_type='invalid_query',
            query=query_stripped, elapsed=elapsed,
            provider_status=CTProviderStatus.SKIP
    )

    # Phase 2: Circuit breaker check with fallback
    crtsh_decision = domain_breaker_check('crt.sh')
    if not crtsh_decision.allowed:
        result = await _try_certspotter_fallback(query_stripped, timeout_s, crtsh_decision.reason, start)
        if result is not None:
            return result

    domain_candidate = _extract_domain_from_query(query_stripped)
    wildcard_urls = _build_crtsh_queries(query_stripped)

    # Phase 3: Wildcard search (if applicable)
    if wildcard_urls and domain_candidate is None:
        result = await _search_wildcards(wildcard_urls, query_stripped, timeout_s, start)
        if result is not None:
            return result

    # Phase 4: Freetext search (if applicable)
    if domain_candidate is None:
        result = await _search_freetext(query_stripped, timeout_s, start)
        if result is not None:
            return result

    # Phase 5: Cooldown + stale cache handling
    cooldown_now = time.monotonic()
    in_cooldown, _, _ = _check_cooldown(domain_candidate, cooldown_now)
    dc_decision = domain_breaker_check('crt.sh')

    if not dc_decision.allowed:
        result = await _try_certspotter_fallback(domain_candidate, timeout_s, dc_decision.reason, start)
        if result is not None:
            return result

    if in_cooldown:
        result = await _handle_cooldown_with_cache(domain_candidate, query_stripped, cache_dir, start, cooldown_now)
        if result is not None:
            return result

    # Phase 6: Main API call
    try:
        return await _make_crtsh_api_call(domain_candidate, query_stripped, timeout_s, cooldown_now, cache_dir, start)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return await _handle_crtsh_timeout(domain_candidate, query_stripped, timeout_s, cache_dir, start)
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f'[crtsh] unexpected error: {e}')
        return _make_error_response(
            error=str(e), error_type='provider_exception',
            query=domain_candidate, elapsed=elapsed,
            provider_status=CTProviderStatus.PARSE_ERROR
    )


class CRTshAdapter(BaseDiscoveryMixin):
    """
    crt.sh Certificate Transparency adapter using BaseDiscoveryMixin infrastructure.

    Wraps call_crtsh() as _do_discover() — AP-06 FIX: was using async_search_crtsh
    which duplicated call_crtsh logic without cache/outcome support.
    """

    name: str = "crtsh"
    source_type: str = "ct"

    @property
    def rate_limit_rpm(self) -> int:
        return 30  # CT providers are rate-sensitive

    @property
    def retry_attempts(self) -> int:
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        return 1.0

    @property
    def timeout_s(self) -> float:
        return 8.0

    async def _do_discover(
        self, query: str, limit: int
    ):
        """Wrap call_crtsh() as an async iterator — AP-06 FIX."""
        try:
            result, _outcome = await call_crtsh(query, max_results=limit, timeout_s=self.timeout_s)
        except Exception:
            return

        for hit in result.hits:
            metadata: dict[str, str] = {}
            if hit.ct_issuer_name:
                metadata["ct_issuer_name"] = hit.ct_issuer_name
            if hit.ct_serial_number:
                metadata["ct_serial_number"] = hit.ct_serial_number
            if hit.ct_not_before:
                metadata["ct_not_before"] = hit.ct_not_before
            if hit.ct_not_after:
                metadata["ct_not_after"] = hit.ct_not_after
            if hit.ct_entry_timestamp:
                metadata["ct_entry_timestamp"] = hit.ct_entry_timestamp
            if hit.ct_name_value:
                metadata["ct_name_value"] = hit.ct_name_value
            if hit.ct_common_name:
                metadata["ct_common_name"] = hit.ct_common_name

            yield DiscoveryResult(
                query=hit.query,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                source=hit.source,
                source_type=self.source_type,
                rank=hit.rank,
                retrieved_ts=hit.retrieved_ts,
                score=hit.score,
                reason=hit.reason,
                metadata=metadata,
    )
