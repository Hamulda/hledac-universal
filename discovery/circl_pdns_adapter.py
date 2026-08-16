"""
discovery/circl_pdns_adapter.py — CIRCL Passive DNS Discovery Adapter

Sprint F229: CIRCL PDNS adapter aligned with discovery/source_registry tier-1.



Mirrors crtsh_adapter.py pattern:
  - async_get_httpx_session() from network.session_runtime
  - checked_aiohttp_get() from transport.circuit_breaker
  - DiscoveryBatchResult + DiscoveryHit from duckduckgo_adapter

CIRCL endpoint: https://www.circl.lu/pdns/query/{domain}
CIRCL returns: plain text, one JSON object per line (not JSON array)

No API key required — CIRCL PDNS community tier is keyless.
Fail-soft throughout.
"""
import asyncio
import msgspec
from compat.msgspec_gc_compat import Struct
import logging
import time
from dataclasses import dataclass
from enum import Enum
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.security.passive_dns import parse_circl_pdns_text
from hledac.universal.tools.discovery_replay import read_cassette, replay_enabled, replay_strict_enabled, write_cassette
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get
from .base import DiscoveryBatchResult, DiscoveryHit
from hledac.universal.discovery.base import BaseDiscoveryMixin, DiscoveryResult
__all__ = ['async_search_circl_pdns', 'call_circl_pdns', 'PDNSOutcome', 'PDNSProviderStatus']
logger = logging.getLogger(__name__)

class PDNSProviderStatus(Enum):
    """F229: CIRCL PDNS provider status tags."""
    OK = 'ok'
    HTTP_5XX = 'http_5xx'
    HTTP_4XX = 'http_4xx'
    TIMEOUT = 'timeout'
    PARSE_ERROR = 'parse_error'
    EMPTY = 'empty'
    DISABLED = 'disabled'
    COOLDOWN_ACTIVE = 'cooldown_active'
    PROVIDER_FAILURE = 'provider_failure'

class PDNSOutcome(Struct, frozen=True):
    """
    Normalized CIRCL PDNS adapter outcome — F229.

    Fields:
        attempted:       True if HTTP call was attempted.
        query:           Domain that was submitted.
        result_count:    IP records returned (0 if not attempted or on error).
        error:           Error tag string or None on success.
        timeout:         True if call timed out.
        duration_s:      Wall-clock seconds for the call.
        skip_reason:     Reason for skip or None if attempted.
        cooldown_active: True if provider is in cooldown for this domain.
        cooldown_remaining_s: Seconds remaining in cooldown (0 if not in cooldown).
    """
    attempted: bool = False
    query: str = ''
    result_count: int = 0
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None
    cooldown_active: bool = False
    cooldown_remaining_s: float = 0.0
_CIRCL_PDNS_URL = 'https://www.circl.lu/pdns/query'
_HTTP_TIMEOUT_S = 8.0
_RATE_LIMIT_SLEEP_S = 2.0
_MAX_HITS = 50
_COOLDOWN_DEFAULT_S = 300.0
_MAX_COOLDOWN_KEYS = 64
_CIRCL_LAST_CALL_TS: float | None = None
from hledac.universal.security.passive_dns import _is_private_ip
from _core import aclose

def _parse_pdns_line(line: str):
    """
    Parse a single CIRCL PDNS JSON line (for backward compatibility).

    Returns (ip, rrname, rrtype) or None if unparseable.
    CIRCL format: {"rrname":"...", "rrtype":"A", "rdata":"1.2.3.4", ...}
    Strict: returns None for plain text, empty rrname, or missing rdata.
    """
    import orjson
    try:
        record = orjson.loads(line)
    except Exception:
        return None
    rrname = record.get('rrname', '')
    rrtype = record.get('rrtype', '')
    rdata = record.get('rdata', '')
    if not rrname or not rdata:
        return None
    return (str(rdata).strip(), str(rrname).strip(), str(rrtype).strip())

def _normalize_domain(domain: str) -> str:
    """Strip whitespace and lowercase the domain."""
    return domain.strip().lower()
_pdns_cooldown: dict[str, tuple[float, str]] = {}

def _enter_cooldown(domain: str, reason: str, now: float) -> None:
    """Enter cooldown for a domain after provider failure."""
    domain_key = _normalize_domain(domain)
    if len(_pdns_cooldown) >= _MAX_COOLDOWN_KEYS and domain_key not in _pdns_cooldown:
        oldest_key = next(iter(_pdns_cooldown))
        _pdns_cooldown.pop(oldest_key, None)
    _pdns_cooldown[domain_key] = (now, reason)

def _check_cooldown(domain: str, now: float) -> tuple[bool, float, str]:
    """Check if domain is in active cooldown. Returns (active, remaining_s, reason)."""
    domain_key = _normalize_domain(domain)
    entry = _pdns_cooldown.get(domain_key)
    if entry is None:
        return (False, 0.0, '')
    started_at, reason = entry
    remaining = _COOLDOWN_DEFAULT_S - (now - started_at)
    if remaining <= 0:
        _pdns_cooldown.pop(domain_key, None)
        return (False, 0.0, '')
    return (True, remaining, reason)

def _clear_cooldown(domain: str) -> None:
    """Clear cooldown for a domain on provider success."""
    _pdns_cooldown.pop(_normalize_domain(domain), None)

async def _rate_limit_circl() -> None:
    """
    AP-04: Conditional rate limiting for CIRCL PDNS.

    Uses timestamp-based approach: only sleeps if the rate limit window
    hasn't elapsed since the last call. Pattern mirrors fediverse_adapter.py:93-103.
    """
    global _CIRCL_LAST_CALL_TS
    now = time.monotonic()
    if _CIRCL_LAST_CALL_TS is not None:
        elapsed = now - _CIRCL_LAST_CALL_TS
        if elapsed < _RATE_LIMIT_SLEEP_S:
            await asyncio.sleep(_RATE_LIMIT_SLEEP_S - elapsed)
    _CIRCL_LAST_CALL_TS = time.monotonic()

async def async_search_circl_pdns(domain: str, max_results: int=50, timeout_s: float=5.0) -> DiscoveryBatchResult:
    """
    Search CIRCL PDNS for a domain — returns DiscoveryBatchResult.

    Args:
        domain:       Domain to query (e.g. "example.com").
        max_results: Max IP hits to return (default 50, hard cap 50).
        timeout_s:   HTTP timeout in seconds (default 5.0).

    Returns:
        DiscoveryBatchResult with PDNS-sourced IP hits.
        error is None on success, set on all failure paths.

    Fail-soft:
        - empty_query: no domain provided
        - timeout: asyncio.TimeoutError
        - http_5xx: server error
        - http_4xx: client error
        - network_error: connection issue
        - parse_error: CIRCL response unparseable
        - cooldown_active: domain in cooldown
        - no_records: domain in CIRCL but no IP records
    """
    start = time.monotonic()
    domain_norm = _normalize_domain(domain)
    if not domain_norm:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(hits=(), error='empty_query', error_type='invalid_query', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
    cooldown_now = time.monotonic()
    in_cooldown, _, _ = _check_cooldown(domain_norm, cooldown_now)
    if in_cooldown:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(hits=(), error='cooldown_active', error_type='cooldown_active', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
    await _rate_limit_circl()
    session: httpx.AsyncClient | None = None
    try:
        session = await async_get_httpx_session()
        timeout = httpx.Timeout(min(timeout_s, _HTTP_TIMEOUT_S))
        url = f'{_CIRCL_PDNS_URL}/{domain_norm}'
        try:
            async with asyncio.timeout(timeout_s):
                text, status, err = await checked_aiohttp_get(session, url, headers={'User-Agent': 'Hledac/1.0 (research bot)'}, timeout=timeout, failure_kind='circl_pdns')
        except asyncio.CancelledError:
            raise
        elapsed = time.monotonic() - start
        if err:
            err_tag = 'network_error'
            is_timeout = err == 'timeout'
            if err.startswith('circuit_breaker_open:'):
                err_tag = 'circuit_breaker_open'
            elif is_timeout:
                err_tag = 'timeout'
            _enter_cooldown(domain_norm, err, cooldown_now)
            return DiscoveryBatchResult(hits=(), error=err, error_type=err_tag, provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
        if status >= 500:
            _enter_cooldown(domain_norm, f'http_{status}', cooldown_now)
            return DiscoveryBatchResult(hits=(), error=f'http_{status}', error_type='http_5xx', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
        if status == 404 or status >= 400:
            return DiscoveryBatchResult(hits=(), error=f'http_{status}' if status >= 400 else None, error_type='provider_empty' if status == 404 else 'http_4xx', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
        now_ts = time.time()
        records = parse_circl_pdns_text(str(text), max_results=max_results)
        hits: list[DiscoveryHit] = []
        for record in records:
            if len(hits) >= max_results:
                break
            reason = 'pdns_aaaa_record' if record.rrtype.upper() == 'AAAA' else 'pdns_a_record'
            snippet = f'CIRCL PDNS: {record.rrname} → {record.ip} ({record.rrtype})'
            hits.append(DiscoveryHit(query=domain, title=f'PDNS: {record.ip}', url=f'https://{record.rrname}/', snippet=snippet, source='circl_pdns', rank=len(hits), retrieved_ts=now_ts, score=1.0 - len(hits) / _MAX_HITS, reason=reason))
        elapsed = time.monotonic() - start
        if not hits:
            return DiscoveryBatchResult(hits=(), error=None, error_type='provider_empty', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
        _clear_cooldown(domain_norm)
        return DiscoveryBatchResult(hits=tuple(hits), error=None, error_type='none', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        elapsed = time.monotonic() - start
        _enter_cooldown(domain_norm, 'timeout', start)
        return DiscoveryBatchResult(hits=(), error='timeout', error_type='timeout', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f'[circl_pdns] unexpected error: {e}')
        return DiscoveryBatchResult(hits=(), error=str(e), error_type='provider_exception', provider_name='circl_pdns', provider_chain=('circl_pdns',), source_family='pdns', elapsed_s=elapsed)

# ── CIRCL PDNS result handlers ──────────────────────────────────────────────────


def _handle_pdns_network_error(
    domain_norm: str,
    err: str,
    elapsed: float,
    cooldown_now: float,
) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle network errors from PDNS lookup."""
    err_tag, is_timeout = _classify_network_error(err)
    _enter_cooldown(domain_norm, err, cooldown_now)
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=err, timeout=is_timeout, cooldown_active=True, cooldown_remaining_s=_COOLDOWN_DEFAULT_S)
    result = _make_pdns_result(None, err, err_tag, elapsed)
    return (result, outcome)


def _handle_pdns_http_error(
    domain_norm: str,
    status: int,
    elapsed: float,
) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle HTTP error responses (400-499) from PDNS lookup."""
    http_err = f'http_{status}'
    err_type = 'provider_empty' if status == 404 else 'http_4xx'
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=http_err if status >= 400 else None)
    result = _make_pdns_result(None, http_err if status >= 400 else None, err_type, elapsed)
    return (result, outcome)


def _handle_pdns_success(
    domain_norm: str,
    text: str,
    domain: str,
    elapsed: float,
    cooldown_now: float,
) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle successful PDNS lookup and return results."""
    now_ts = time.time()
    records = parse_circl_pdns_text(text, max_results=_MAX_HITS)
    hits = _build_pdns_hits(records, domain, now_ts)
    elapsed = time.monotonic() - start
    if not hits:
        _clear_cooldown(domain_norm)
        outcome = _make_pdns_outcome(domain_norm, 0, elapsed)
        result = _make_pdns_result(None, None, 'provider_empty', elapsed)
        return (result, outcome)
    _clear_cooldown(domain_norm)
    if replay_enabled():
        hits_data = [msgspec.json.decode(msgspec.json.encode(h)) for h in hits]
        write_cassette('circl_pdns', domain_norm, {'hits': hits_data})
    outcome = _make_pdns_outcome(domain_norm, len(hits), elapsed)
    result = _make_pdns_result(tuple(hits), None, 'none', elapsed)
    return (result, outcome)


# ── CIRCL PDNS helper functions ─────────────────────────────────────────────────


def _make_pdns_result(
    hits: tuple[DiscoveryHit, ...] | None,
    error: str | None,
    error_type: str,
    elapsed: float,
) -> DiscoveryBatchResult:
    """Create a standardized PDNS DiscoveryBatchResult."""
    return DiscoveryBatchResult(
        hits=hits or (),
        error=error,
        error_type=error_type,
        provider_name='circl_pdns',
        provider_chain=('circl_pdns',),
        source_family='pdns',
        elapsed_s=elapsed,
    )


def _make_pdns_outcome(
    query: str,
    result_count: int,
    elapsed: float,
    *,
    error: str | None = None,
    timeout: bool = False,
    skip_reason: str | None = None,
    cooldown_active: bool = False,
    cooldown_remaining_s: float | None = None,
) -> PDNSOutcome:
    """Create a standardized PDNSOutcome."""
    return PDNSOutcome(
        attempted=True,
        query=query,
        result_count=result_count,
        error=error,
        timeout=timeout,
        skip_reason=skip_reason,
        cooldown_active=cooldown_active,
        cooldown_remaining_s=cooldown_remaining_s,
        duration_s=elapsed,
    )


def _classify_network_error(err: str) -> tuple[str, bool]:
    """Classify network error and return (error_tag, is_timeout)."""
    if err.startswith('circuit_breaker_open:'):
        return ('circuit_breaker_open', False)
    if err == 'timeout':
        return ('timeout', True)
    return ('network_error', False)


def _build_pdns_hits(
    records: list[Any],
    domain: str,
    now_ts: float,
) -> list[DiscoveryHit]:
    """Build PDNS DiscoveryHit list from parsed records."""
    hits: list[DiscoveryHit] = []
    for record in records:
        if len(hits) >= _MAX_HITS:
            break
        reason = 'pdns_aaaa_record' if record.rrtype.upper() == 'AAAA' else 'pdns_a_record'
        snippet = f'CIRCL PDNS: {record.rrname} → {record.ip} ({record.rrtype})'
        hits.append(DiscoveryHit(
            query=domain,
            title=f'PDNS: {record.ip}',
            url=f'https://{record.rrname}/',
            snippet=snippet,
            source='circl_pdns',
            rank=len(hits),
            retrieved_ts=now_ts,
            score=1.0 - len(hits) / _MAX_HITS,
            reason=reason,
        ))
    return hits


def _result_for_invalid(domain: str, start: float, skip_reason: str) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Return result for invalid/empty domain query."""
    elapsed = time.monotonic() - start
    outcome = _make_pdns_outcome(domain, 0, elapsed, skip_reason=skip_reason)
    result = _make_pdns_result(None, skip_reason, 'invalid_query', elapsed)
    return (result, outcome)


def _result_for_cooldown(domain_norm: str, start: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Return result when domain is in cooldown."""
    elapsed = time.monotonic() - start
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, cooldown_active=True, cooldown_remaining_s=_COOLDOWN_DEFAULT_S, skip_reason='cooldown_active')
    result = _make_pdns_result(None, 'cooldown_active', 'cooldown_active', elapsed)
    return (result, outcome)


def _result_for_replay_hit(domain_norm: str, cached: dict, start: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Return result from cached replay hit."""
    cached_hits = cached.get('hits', ())
    elapsed = time.monotonic() - start
    outcome = _make_pdns_outcome(domain_norm, len(cached_hits), elapsed)
    result = _make_pdns_result(tuple(cached_hits) if isinstance(cached_hits, list) else cached_hits, None, 'replay_hit', elapsed)
    return (result, outcome)


def _result_for_replay_miss(domain_norm: str, start: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Return result when replay is strict and no cassette found."""
    elapsed = time.monotonic() - start
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error='replay_miss')
    result = _make_pdns_result(None, 'replay_miss', 'replay_miss', elapsed)
    return (result, outcome)


def _handle_pdns_network_error(domain_norm: str, err: str, elapsed: float, cooldown_now: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle network errors from PDNS HTTP request."""
    err_tag, is_timeout = _classify_network_error(err)
    _enter_cooldown(domain_norm, err, cooldown_now)
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=err, timeout=is_timeout, cooldown_active=True, cooldown_remaining_s=_COOLDOWN_DEFAULT_S)
    result = _make_pdns_result(None, err, err_tag, elapsed)
    return (result, outcome)


def _handle_pdns_http_error(domain_norm: str, status: int, elapsed: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle HTTP 4xx errors from PDNS lookup."""
    http_err = f'http_{status}'
    err_type = 'provider_empty' if status == 404 else 'http_4xx'
    outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=http_err)
    result = _make_pdns_result(None, http_err, err_type, elapsed)
    return (result, outcome)


def _handle_pdns_success(domain_norm: str, text: str, domain: str, start: float, cooldown_now: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle successful PDNS HTTP response."""
    now_ts = time.time()
    records = parse_circl_pdns_text(text, max_results=_MAX_HITS)
    hits = _build_pdns_hits(records, domain, now_ts)
    elapsed = time.monotonic() - start
    if not hits:
        _clear_cooldown(domain_norm)
        outcome = _make_pdns_outcome(domain_norm, 0, elapsed)
        result = _make_pdns_result(None, None, 'provider_empty', elapsed)
        return (result, outcome)
    _clear_cooldown(domain_norm)
    if replay_enabled():
        import msgspec
        hits_data = [msgspec.json.decode(msgspec.json.encode(h)) for h in hits]
        write_cassette('circl_pdns', domain_norm, {'hits': hits_data})
    outcome = _make_pdns_outcome(domain_norm, len(hits), elapsed)
    result = _make_pdns_result(tuple(hits), None, 'none', elapsed)
    return (result, outcome)


async def _handle_pdns_http_response(
    domain_norm: str,
    text: str,
    status: int,
    domain: str,
    start: float,
    cooldown_now: float,
) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Process successful HTTP response from PDNS lookup."""
    if status >= 500:
        _enter_cooldown(domain_norm, f'http_{status}', cooldown_now)
        elapsed = time.monotonic() - start
        outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=f'http_{status}', cooldown_active=True, cooldown_remaining_s=_COOLDOWN_DEFAULT_S)
        result = _make_pdns_result(None, f'http_{status}', 'http_5xx', elapsed)
        return (result, outcome)
    if status >= 400:
        elapsed = time.monotonic() - start
        return _handle_pdns_http_error(domain_norm, status, elapsed)
    return _handle_pdns_success(domain_norm, str(text), domain, start, cooldown_now)


def _handle_pdns_exception(domain_norm: str, start: float, exc: Exception) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Handle exceptions from PDNS lookup."""
    elapsed = time.monotonic() - start
    if isinstance(exc, TimeoutError):
        _enter_cooldown(domain_norm, 'timeout', start)
        outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error='timeout', timeout=True, cooldown_active=True, cooldown_remaining_s=_COOLDOWN_DEFAULT_S)
        result = _make_pdns_result(None, 'timeout', 'timeout', elapsed)
    else:
        logger.warning(f'[circl_pdns] unexpected error: {exc}')
        outcome = _make_pdns_outcome(domain_norm, 0, elapsed, error=str(exc))
        result = _make_pdns_result(None, str(exc), 'provider_exception', elapsed)
    return (result, outcome)


async def call_circl_pdns(domain: str, timeout_s: float=5.0) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """
    CIRCL PDNS lookup with normalized outcome — returns (DiscoveryBatchResult, PDNSOutcome).
    Uses helper functions to reduce branching complexity.
    """
    start, domain_norm, cooldown_now = _prepare_pdns_request(domain)
    # Early exit: invalid domain, cooldown, or replay cache
    result = _check_early_exit(domain, domain_norm, cooldown_now, start)
    if result is not None:
        return result
    # Execute HTTP request
    return await _execute_pdns_request(domain_norm, domain, timeout_s, cooldown_now, start)


def _prepare_pdns_request(domain: str) -> tuple[float, str, float]:
    """Prepare common values for PDNS request."""
    start = time.monotonic()
    domain_norm = _normalize_domain(domain)
    cooldown_now = time.monotonic()
    return start, domain_norm, cooldown_now


def _check_early_exit(domain: str, domain_norm: str, cooldown_now: float, start: float) -> tuple[DiscoveryBatchResult, PDNSOutcome] | None:
    """Check early exit conditions. Returns result if should exit, None otherwise."""
    if not domain_norm:
        return _result_for_invalid(domain, start, 'empty_query')
    in_cooldown, _, _ = _check_cooldown(domain_norm, cooldown_now)
    if in_cooldown:
        return _result_for_cooldown(domain_norm, start)
    if replay_enabled():
        cached = read_cassette('circl_pdns', domain_norm)
        if cached is not None:
            return _result_for_replay_hit(domain_norm, cached, start)
        if replay_strict_enabled():
            return _result_for_replay_miss(domain_norm, start)
    return None


async def _execute_pdns_request(domain_norm: str, domain: str, timeout_s: float, cooldown_now: float, start: float) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """Execute the actual PDNS HTTP request."""
    await _rate_limit_circl()
    session = await async_get_httpx_session()
    timeout = httpx.Timeout(min(timeout_s, _HTTP_TIMEOUT_S))
    url = f'{_CIRCL_PDNS_URL}/{domain_norm}'
    try:
        async with asyncio.timeout(timeout_s):
            text, status, err = await checked_aiohttp_get(session, url, headers={'User-Agent': 'Hledac/1.0 (research bot)'}, timeout=timeout, failure_kind='circl_pdns')
        if err:
            return _handle_pdns_network_error(domain_norm, err, time.monotonic() - start, cooldown_now)
        return await _handle_pdns_http_response(domain_norm, str(text), status, domain, start, cooldown_now)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return _handle_pdns_exception(domain_norm, start, e)

class CirclPDNSAdapter(BaseDiscoveryMixin):
    """
    CIRCL PDNS adapter using BaseDiscoveryMixin infrastructure.

    Wraps async_search_circl_pdns() as _do_discover().
    """

    name: str = "circl_pdns"
    source_type: str = "pdns"

    @property
    def rate_limit_rpm(self) -> int:
        return 30  # PDNS providers are rate-sensitive

    @property
    def retry_attempts(self) -> int:
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        return 2.0

    @property
    def timeout_s(self) -> float:
        return 8.0

    async def _do_discover(
        self, query: str, limit: int
    ):
        """Wrap async_search_circl_pdns() as an async iterator."""
        try:
            result = await async_search_circl_pdns(query, max_results=limit)
        except Exception:
            return

        for hit in result.hits:
            metadata: dict[str, str] = {}

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
