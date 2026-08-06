"""
Passive DNS — DoH (DNS-over-HTTPS) resolver and CIRCL PDNS lookup with HackerTarget fallback.

Providers:


  - cloudflare: https://cloudflare-dns.com/dns-query
  - google:     https://dns.google/resolve
  - CIRCL PDNS: https://www.circl.lu/pdns/query (primary, may return 401 if rate-limited)
  - HackerTarget: https://api.hackertarget.com/dnslookup (fallback on CIRCL auth failure)

Graceful degradation: returns [] on failure, never blocks pipeline.

Anti-patterns prevented:
  - No blocking socket ops (aiohttp only)
  - Non-blocking: asyncio.sleep for rate limits, not blocking waits
  - Graceful degradation: [] return with WARNING log on any failure
  - CIRCL 401 triggers automatic HackerTarget fallback

F206AW Transport Seams:
  - Optional session_provider: inject a pre-configured httpx.AsyncClient
  - Optional fetch_func: inject an async fetch(url, headers) -> bytes
  - Canonical circuit breaker preflight via domain_breaker_check
  - transport_policy telemetry: "injected" | "local_fallback" | "bypass_legacy"
  - NO import-time session creation
"""
import asyncio
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
import msgspec
from typing import Any
import httpx
import orjson
logger = logging.getLogger(__name__)

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()
_HACKERTARGET_PDNS_URL = 'https://api.hackertarget.com/dnslookup'
_HACKERTARGET_RATE_LIMIT_SLEEP = 2.0
_HACKERTARGET_TIMEOUT = httpx.Timeout(10.0)

async def _fallback_hackertarget_pdns(domain: str, session: httpx.AsyncClient) -> tuple[list[str], PassiveDNSOutcome]:
    """Fallback to HackerTarget PDNS when CIRCL returns 401."""
    start = time.monotonic()
    url = f'{_HACKERTARGET_PDNS_URL}?q={domain}'
    try:
        await asyncio.sleep(_HACKERTARGET_RATE_LIMIT_SLEEP)
        resp = await session.get(url, timeout=_HACKERTARGET_TIMEOUT)
        text = resp.text
        if resp.status_code != 200:
            elapsed = time.monotonic() - start
            return ([], PassiveDNSOutcome(attempted=True, query=domain, result_count=0, error=f'http_{resp.status_code}', duration_s=elapsed))
        if 'error' in text.lower() or 'quota' in text.lower() or (not text) or text.startswith('#'):
            elapsed = time.monotonic() - start
            return ([], PassiveDNSOutcome(attempted=True, query=domain, result_count=0, error='hackertarget_empty', duration_s=elapsed))
        ips: list[str] = []
        for line in text.splitlines()[:50]:
            parts = re.split('\\s*:\\s*|\\|', line.strip(), maxsplit=1)
            if len(parts) < 2:
                continue
            rec_type = parts[0].strip()
            value = parts[1].strip()
            if rec_type in ('A', 'AAAA') and re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', value):
                ips.append(value)
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain, result_count=len(ips), error=None, duration_s=elapsed)
        return (ips, outcome)
    except Exception as e:
        elapsed = time.monotonic() - start
        return ([], PassiveDNSOutcome(attempted=True, query=domain, result_count=0, error=str(e), duration_s=elapsed))

class CIRCLPDNSRecord(msgspec.Struct, frozen=True, gc=False):
    """Parsed CIRCL PDNS record — F207F."""
    ip: str
    rrname: str
    rrtype: str

def parse_circl_pdns_text(text: str, max_results: int=50) -> list[CIRCLPDNSRecord]:
    """
    Parse CIRCL PDNS text response into structured records.

    Handles:
      - NDJSON (canonical CIRCL format): {"rrname":"...","rrtype":"A","rdata":"1.2.3.4"}
      - Legacy plain IP-per-line
      - CSV "ip,rrname,rrtype" fallback

    Skips:
      - Empty lines
      - Private/loopback IPs
      - Malformed JSON (fallback to plain IP)

    Args:
        text: Raw response text from CIRCL PDNS endpoint.
        max_results: Hard cap on records returned (default 50).

    Returns:
        List of CIRCLPDNSRecord, deduplicated by IP.
    """
    records: list[CIRCLPDNSRecord] = []
    seen_ips: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ip: str | None = None
        rrname = ''
        rrtype = ''
        try:
            record = orjson.loads(line)
            rdata = record.get('rdata', '')
            rrname = str(record.get('rrname', '')).strip()
            rrtype = str(record.get('rrtype', '')).strip()
            if rdata:
                ip = str(rdata).strip()
        except Exception:
            parts = line.split(',')
            candidate = parts[0].strip() if parts else ''
            if candidate:
                ip = candidate
        if not ip or _is_private_ip(ip):
            continue
        if ip in seen_ips:
            continue
        if len(records) >= max_results:
            break
        seen_ips.add(ip)
        records.append(CIRCLPDNSRecord(ip=ip, rrname=rrname, rrtype=rrtype))
    return records

class PassiveDNSOutcome(msgspec.Struct, frozen=True, gc=False):
    """
    Normalized PassiveDNS adapter outcome — F207F.

    Fields:
        attempted:     True if network call was made.
        query:        Domain/IP that was queried.
        result_count: IP records returned (0 if not attempted or on error).
        error:        Error tag string or None on success.
        timeout:      True if call timed out.
        duration_s:   Wall-clock seconds for the call.
        skip_reason:  Reason for skip or None if attempted.
    """
    attempted: bool = False
    query: str = ''
    result_count: int = 0
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None
DOH_ENDPOINTS: dict[str, str] = {'cloudflare': 'https://cloudflare-dns.com/dns-query', 'google': 'https://dns.google/resolve', 'opendns': 'https://doh.opendns.com/resolve', 'quad9': 'https://dns.quad9.net/dns-query'}
_DOH_GET_PROVIDERS: frozenset[str] = frozenset({'cloudflare', 'google', 'opendns'})
_DOH_POST_PROVIDERS: frozenset[str] = frozenset({'quad9'})
DOH_PROVIDER_WEIGHTS: dict[str, float] = {'cloudflare': 1.0, 'google': 1.0, 'opendns': 1.0, 'quad9': 1.0}

def get_random_doh_provider() -> str:
    """Return a random DoH provider weighted evenly. Thread-safe via _RNG.choice (secrets.SystemRandom)."""
    providers = list(DOH_PROVIDER_WEIGHTS.keys())
    return _RNG.choice(providers)
CIRCL_PDNS_URL: str = 'https://www.circl.lu/pdns/query'
CIRCL_RATE_LIMIT_SLEEP: float = 2.0
transport_policy: str = 'bypass_legacy'
_RFC1918_RE = re.compile('^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)')
_LOCALHOST_RE = re.compile('^(127\\.|::1|fe80:|localhost$)')
_LINKLOCAL_RE = re.compile('^(169\\.254\\.|fe80:)')

def _is_private_ip(ip: str) -> bool:
    """Return True if IP is private, loopback, or link-local."""
    if not ip:
        return True
    ip_stripped = ip.strip()
    if not ip_stripped:
        return True
    ip_lower = ip_stripped.lower()
    if _RFC1918_RE.match(ip_lower):
        return True
    if _LOCALHOST_RE.match(ip_lower):
        return True
    if _LINKLOCAL_RE.match(ip_lower):
        return True
    return False
_circuit_breaker_check: Callable[[str], Any] | None = None

def _get_circuit_breaker():
    """Lazily import domain_breaker_check. Returns None if unavailable."""
    global _circuit_breaker_check
    if _circuit_breaker_check is None:
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_check
            _circuit_breaker_check = domain_breaker_check
        except ImportError:
            _circuit_breaker_check = None
    return _circuit_breaker_check

def _try_domain_breaker_check(domain: str) -> Any:
    """Fail-soft circuit breaker check. Returns None if breaker unavailable."""
    if not domain:
        return None
    cb = _get_circuit_breaker()
    if cb is not None:
        try:
            return cb(domain)
        except Exception:
            pass
    return None

def _parse_dns_wire_response(wire: bytes) -> dict[str, Any] | None:
    """
    Parse RFC 8484 DNS wire format response into a dns-json-like dict.
    Returns {"Answer": [{"type": 1, "ttl": N, "data": "IP"}]} or None on parse error.
    """
    import struct
    if len(wire) < 12:
        return None
    try:
        _, _, _, ancount = struct.unpack('>HHHH', wire[:8])
        offset = 12
        while offset < len(wire):
            label_len = wire[offset]
            if label_len == 0:
                offset += 1
                break
            offset += 1 + label_len
        offset += 4
        answers = []
        for _ in range(ancount):
            if offset + 12 > len(wire):
                break
            if wire[offset] & 192 == 192:
                offset += 2
            else:
                while offset < len(wire) and wire[offset] != 0:
                    offset += 1 + wire[offset]
                offset += 1
            if offset + 10 > len(wire):
                break
            ans_type, _ans_class, ans_ttl = struct.unpack('>HHI', wire[offset:offset + 8])
            offset += 8
            rdlength = struct.unpack('>H', wire[offset:offset + 2])[0]
            offset += 2
            if offset + rdlength > len(wire):
                break
            rdata = wire[offset:offset + rdlength]
            offset += rdlength
            if ans_type == 1 and rdlength == 4:
                ip = '.'.join((str(b) for b in rdata))
                answers.append({'type': 1, 'ttl': ans_ttl, 'data': ip})
            elif ans_type == 28 and rdlength == 16:
                ip = ':'.join((f'{rdata[i] << 8 | rdata[i + 1]:x}' for i in range(0, 16, 2)))
                answers.append({'type': 28, 'ttl': ans_ttl, 'data': ip})
        return {'Answer': answers}
    except Exception:
        return None

async def resolve_doh(domain: str, provider: str='cloudflare', session_provider: httpx.AsyncClient | None=None, fetch_func: Callable[..., Any] | None=None) -> list[str]:
    """
    Resolve hostname via DNS-over-HTTPS (DoH).

    Multi-provider fallback: cloudflare, google, opendns (GET) + quad9 (POST RFC 8484).
    For quad9, the DNS query is encoded as RFC 8484 BASE64-URL wire format.

    Args:
        domain: Domain name to resolve (e.g. "example.com")
        provider: DoH provider — "cloudflare" (default), "google", "opendns", or "quad9"
        session_provider: Optional pre-configured httpx.AsyncClient.
            When provided, takes precedence over internal ephemeral session.
            Enables canonical transport seam (shared session, circuit breaker).
        fetch_func: Optional async fetch(url, headers) -> bytes.
            When provided along with session_provider, uses both for fetch.

    Returns:
        List of IP addresses (A records), or [] on failure.

    Anti-patterns prevented:
      - Non-blocking httpx
      - Graceful degradation: [] return on any error
      - Accept: application/dns-json header
    """
    global transport_policy
    if provider not in DOH_ENDPOINTS:
        logger.warning(f'Unknown DoH provider: {provider} — using cloudflare')
        provider = 'cloudflare'
    timeout = httpx.Timeout(15.0)
    ips: list[str] = []
    circuit_decision = _try_domain_breaker_check(domain)
    if circuit_decision is not None and (not circuit_decision.allowed):
        logger.debug(f'DoH circuit breaker blocked {domain}: {circuit_decision.reason} (retry in {circuit_decision.retry_after_s:.1f}s)')
        return []
    if session_provider is not None or fetch_func is not None:
        transport_policy = 'injected'
    else:
        transport_policy = 'local_fallback'
    _providers_to_try = [provider]
    if len(DOH_PROVIDER_WEIGHTS) > 1:
        _fallback = [_p for _p in DOH_PROVIDER_WEIGHTS if _p != provider]
        if _fallback:
            _providers_to_try.append(_fallback[0])
    for _attempt_provider in _providers_to_try:
        _attempt_endpoint = DOH_ENDPOINTS[_attempt_provider]
        _use_post = _attempt_provider in _DOH_POST_PROVIDERS
        try:
            if _use_post:
                import base64
                import struct
                txn_id = secrets.token_hex(2)  # 16-bit TXN ID via crypto RNG
                qname = b''
                for label in domain.split('.'):
                    qname += bytes([len(label)]) + label.encode('ascii')
                qname += b'\x00'
                dns_query = struct.pack('>HHHHH', txn_id, 256, 1, 0, 0) + qname + struct.pack('>HH', 1, 1)
                encoded_query = base64.urlsafe_b64encode(dns_query).rstrip(b'=').decode('ascii')
                post_headers = {'Content-Type': 'application/dns-message', 'Accept': 'application/dns-message'}
                post_url = f'{_attempt_endpoint}?dns={encoded_query}'
                if fetch_func is not None:
                    result = await fetch_func(post_url, post_headers)
                    data = result if isinstance(result, dict) else {}
                elif session_provider is not None:
                    resp = await session_provider.get(post_url, headers=post_headers, follow_redirects=True, timeout=timeout)
                    if resp.status_code >= 500:
                        logger.warning(f'DoH {_attempt_provider} returned HTTP {resp.status_code} for {domain}, retrying...')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'DoH {_attempt_provider} failed for {domain}: HTTP {resp.status_code}')
                        return []
                    raw = resp.content
                    data = _parse_dns_wire_response(raw)
                    if data is None:
                        logger.warning(f'DoH {_attempt_provider} invalid wire format for {domain}')
                        return []
                else:
                    from hledac.universal.transport.session_pool import session_pool
                    session = await session_pool.httpx()
                    resp = await session.get(post_url, headers=post_headers, follow_redirects=True, timeout=timeout)
                    if resp.status_code >= 500:
                        logger.warning(f'DoH {_attempt_provider} returned HTTP {resp.status_code} for {domain}, retrying...')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'DoH {_attempt_provider} failed for {domain}: HTTP {resp.status_code}')
                        return []
                    raw = resp.content
                    data = _parse_dns_wire_response(raw)
                    if data is None:
                        logger.warning(f'DoH {_attempt_provider} invalid wire format for {domain}')
                        return []
            else:
                _attempt_url = f'{_attempt_endpoint}?name={domain}&type=A'
                get_headers = {'Accept': 'application/dns-json'}
                if fetch_func is not None:
                    result = await fetch_func(_attempt_url, get_headers)
                    data = result if isinstance(result, dict) else {}
                elif session_provider is not None:
                    resp = await session_provider.get(_attempt_url, headers=get_headers, follow_redirects=True, timeout=timeout)
                    if resp.status_code >= 500:
                        logger.warning(f'DoH {_attempt_provider} returned HTTP {resp.status_code} for {domain}, retrying...')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'DoH {_attempt_provider} failed for {domain}: HTTP {resp.status_code}')
                        return []
                    text = resp.text
                    import orjson
                    try:
                        data = orjson.loads(text)
                    except (Exception,):
                        logger.warning(f'DoH {_attempt_provider} invalid JSON for {domain}')
                        return []
                else:
                    from hledac.universal.transport.session_pool import session_pool
                    session = await session_pool.httpx()
                    resp = await session.get(_attempt_url, headers=get_headers, follow_redirects=True, timeout=timeout)
                    if resp.status_code >= 500:
                        logger.warning(f'DoH {_attempt_provider} returned HTTP {resp.status_code} for {domain}, retrying...')
                        continue
                    if resp.status_code != 200:
                        logger.warning(f'DoH {_attempt_provider} failed for {domain}: HTTP {resp.status_code}')
                        return []
                    text = resp.text
                    import orjson
                    try:
                        data = orjson.loads(text)
                    except (Exception,):
                        logger.warning(f'DoH {_attempt_provider} invalid JSON for {domain}')
                        return []
            answers = data.get('Answer', []) if isinstance(data, dict) else []
            for answer in answers:
                if answer.get('type') == 1:
                    ip = answer.get('data', '')
                    if ip:
                        ips.append(ip)
            if ips:
                break
            else:
                logger.debug(f'DoH {_attempt_provider} returned no A records for {domain}')
                break
        except TimeoutError:
            logger.warning(f'DoH timeout for {domain} (provider={_attempt_provider})')
            continue
        except Exception as e:
            logger.warning(f'DoH error for {domain} (provider={_attempt_provider}): {e}')
            continue
    return ips

async def lookup_passive_dns(domain: str, session_provider: httpx.AsyncClient | None=None, fetch_func: Callable[..., Any] | None=None) -> list[str]:
    """
    Legacy compatibility wrapper for CIRCL PDNS lookup.

    Prefer call_lookup_passive_dns() for runtime code because it returns
    PassiveDNSOutcome telemetry. This wrapper preserves the old list[str]
    contract.
    """
    ips, _ = await call_lookup_passive_dns(domain, session_provider=session_provider, fetch_func=fetch_func)
    return ips
CirclPdnsRecord = CIRCLPDNSRecord

def _is_ip_address(value: str) -> bool:
    """Return True if value looks like an IP address (v4 or v6)."""
    if re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', value):
        return True
    if ':' in value and re.match('^[0-9a-fA-F:]+$', value):
        return True
    return False

def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name."""
    if not value or len(value) > 253:
        return False
    if '.' not in value:
        return False
    if _is_ip_address(value):
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

async def call_lookup_passive_dns(domain: str, session_provider: httpx.AsyncClient | None=None, fetch_func: Callable[..., Any] | None=None) -> tuple[list[str], PassiveDNSOutcome]:
    """
    CIRCL PDNS lookup with normalized outcome — F207F.

    Returns (ips, outcome) so callers can measure yield without changing
    the existing list[str] contract.

    Args:
        domain:          Domain or IP to query.
        session_provider: Optional pre-configured httpx.AsyncClient.
        fetch_func:      Optional async fetch(url) -> str (plain text).

    Returns:
        (list of IPs, PassiveDNSOutcome) tuple.
        outcome.attempted=True on every code path including skips.
        outcome.skip_reason is set when query is not a valid domain/IP.
    """
    start = time.monotonic()
    if not domain or not domain.strip():
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain, result_count=0, error=None, skip_reason='empty_query', duration_s=elapsed)
        return ([], outcome)
    domain_stripped = domain.strip()
    if not _looks_like_domain(domain_stripped) and (not _is_ip_address(domain_stripped)):
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=None, skip_reason='not_domain_or_ip', duration_s=elapsed)
        return ([], outcome)
    url = f'{CIRCL_PDNS_URL}/{domain_stripped}'
    circuit_decision = _try_domain_breaker_check(domain_stripped)
    if circuit_decision is not None and (not circuit_decision.allowed):
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=f'circuit_breaker:{circuit_decision.reason}', timeout=False, duration_s=elapsed)
        return ([], outcome)
    global transport_policy
    if session_provider is not None or fetch_func is not None:
        transport_policy = 'injected'
    else:
        transport_policy = 'local_fallback'
    try:
        if fetch_func is not None:
            text = await fetch_func(url)
        elif session_provider is not None:
            resp = await session_provider.get(url, timeout=httpx.Timeout(15.0))
            if resp.status_code == 404:
                elapsed = time.monotonic() - start
                outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=None, duration_s=elapsed)
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return ([], outcome)
            if resp.status_code != 200:
                elapsed = time.monotonic() - start
                outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=f'http_{resp.status_code}', duration_s=elapsed)
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return ([], outcome)
            text = resp.text
        else:
            from hledac.universal.transport.session_pool import session_pool
            session = await session_pool.httpx()
            http_timeout = httpx.Timeout(15.0)
            resp = await session.get(url, headers={'User-Agent': 'Hledac/1.0 (research bot)'}, timeout=http_timeout)
            status = resp.status_code
            if status == 404:
                elapsed = time.monotonic() - start
                outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=None, duration_s=elapsed)
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return ([], outcome)
            if status != 200:
                elapsed = time.monotonic() - start
                if status == 401:
                    ips, outcome = await _fallback_hackertarget_pdns(domain_stripped, session)
                    if not outcome.error:
                        await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                        return (ips, outcome)
                outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=f'http_{status}', duration_s=elapsed)
                await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
                return ([], outcome)
            text = resp.text
        records = parse_circl_pdns_text(str(text), max_results=50)
        ips = [record.ip for record in records]
    except TimeoutError:
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error='timeout', timeout=True, duration_s=elapsed)
        await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
        return ([], outcome)
    except Exception as e:
        elapsed = time.monotonic() - start
        outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=0, error=str(e), duration_s=elapsed)
        await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
        return ([], outcome)
    elapsed = time.monotonic() - start
    outcome = PassiveDNSOutcome(attempted=True, query=domain_stripped, result_count=len(ips), error=None, duration_s=elapsed)
    await asyncio.sleep(CIRCL_RATE_LIMIT_SLEEP)
    return (ips, outcome)
__all__ = ['resolve_doh', 'lookup_passive_dns', 'call_lookup_passive_dns', 'PassiveDNSOutcome', 'CirclPdnsRecord', 'parse_circl_pdns_text', 'DOH_ENDPOINTS', 'get_random_doh_provider', 'DOH_PROVIDER_WEIGHTS', 'transport_policy']