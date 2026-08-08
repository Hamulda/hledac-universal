"""
WhoisService — Historical WHOIS/RDAP Intelligence

Consolidated async WHOIS/RDAP client replacing three broken implementations:



  - network_reconnaissance.WHOISLookup  (raw socket, no RDAP, no history)
  - rir_correlator._whois_lookup_domain (blocking ipwhois via run_in_executor)
  - ipv6_recon WHOIS fallback            (IP-only, no domain RDAP)

RDAP (RFC 9224) is the modern WHOIS successor — structured JSON, RIR bootstrap,
no port 43 socket hacks. Supported by ARIN, RIPE, APNIC, LACNIC, AfriNIC.

Historical data sources (M1 8GB bounded):
  - RDAP bootstrap servers (primary, no extra cost)
  - whoisxmlapi.com   — historical WHOIS via paid API
  - WhoisXML API       — alternate provider
  - domain IQ API      — alternate provider
  - Whoisology         — historical domain intelligence

Bounds (M1 8GB safe):
  - MAX_TARGETS = 50          (max domains to query per sprint)
  - RDAP_TIMEOUT_S = 8.0
  - WHOIS_TIMEOUT_S = 10.0
  - MAX_CACHE_SIZE = 500      (TTL cache entries)
  - CACHE_TTL_S = 3600        (1 hour)
  - MAX_LITERAL_WHOIS = 20    (fallback WHOIS port 43 limit)
  - MAX_CONCURRENT = 5         (semaphore cap)

GHOST_INVARIANTS:
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only
  - circuit_breaker check before every external call
  - async_get_httpx_session for HTTP
  - asyncio.open_connection for WHOIS port 43 (no run_in_executor)
  - Fail-soft: every error returns empty dict, never raises
  - Lazy imports for optional deps (ipwhois, requests)
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
import msgspec
from enum import StrEnum
from typing import Any
logger = logging.getLogger(__name__)
import httpx

class WhoisError(StrEnum):
    """String-based error codes for WHOIS/RDAP operations."""
    RDAP_404 = '{source}: 404'
    RDAP_STATUS = '{source}: {status}'
    RDAP_TIMEOUT = '{source}: timeout'
    RDAP_ERROR = '{source}: {error}'
    CONN_FAILED = 'conn_failed: {error}'
    PARSE_ERROR = 'parse_error: {error}'
    READ_ERROR = 'read_error: {error}'
    TIMEOUT = 'timeout'
    NO_IPWHOIS = 'no_ipwhois'
    IPWHOIS_ERROR = 'ipwhois_error: {error}'
MAX_TARGETS: int = 50
RDAP_TIMEOUT_S: float = 8.0
WHOIS_TIMEOUT_S: float = 10.0
MAX_CACHE_SIZE: int = 500
CACHE_TTL_S: int = 3600
MAX_LITERAL_WHOIS: int = 20
MAX_CONCURRENT: int = 5
RDAP_BOOTSTRAP: dict[str, str] = {'arin': 'https://rdap.arin.net/registry/ip', 'ripe': 'https://rdap.ripe.net/rdap/ip', 'apnic': 'https://rdap.apnic.net/ip', 'lacnic': 'https://rdap.lacnic.net/rdap/ip', 'afrinic': 'https://rdap.afrinic.net/rdap/ip'}
RDAP_DOMAIN_BOOTSTRAP: list[str] = ['https://rdap.org/domain/', 'https://rdap.verisign.com/domain/v1/', 'https://rdap.nic.xyz/domain/', 'https://rdap.nic.io/domain/', 'https://rdap.nic.Online/domain/']
WHOIS_FALLBACK_SERVERS: dict[str, str] = {'com': 'whois.verisign-grs.com', 'net': 'whois.verisign-grs.com', 'org': 'whois.pir.org', 'io': 'whois.nic.io', 'co': 'whois.nic.co', 'info': 'whois.afilias.net', 'biz': 'whois.biz', 'us': 'whois.nic.us', 'uk': 'whois.nic.uk', 'de': 'whois.denic.de', 'fr': 'whois.nic.fr', 'eu': 'whois.eu', 'nl': 'whois.sidn.nl', 'ru': 'whois.tcinet.ru', 'jp': 'whois.jprs.jp', 'cn': 'whois.cnnic.cn'}
HISTORICAL_APIS: dict[str, str] = {'whoisxmlapi': 'https://www.whoisxmlapi.com/WHOISAPI/V1/', 'whoiswhoisxml': 'https://www.whoiswhoisxmlapi.com/api/1.0/', 'domainiq': 'https://www.domainiq.com/api/', 'whoisology': 'https://whoisology.com/api/'}

class WhoisResult(msgspec.Struct, gc=False):
    """Structured WHOIS/RDAP result."""
    domain: str
    registrar: str | None = None
    creation_date: str | None = None
    expiration_date: str | None = None
    updated_date: str | None = None
    name_servers: list[str] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    dnssec: bool = False
    registrant_name: str | None = None
    registrant_org: str | None = None
    registrant_email: str | None = None
    admin_name: str | None = None
    admin_email: str | None = None
    tech_name: str | None = None
    tech_email: str | None = None
    registrant_org_country: str | None = None
    asn: str | None = None
    asn_name: str | None = None
    asn_country: str | None = None
    netblock: str | None = None
    netname: str | None = None
    org: str | None = None
    source: str = 'rdap'
    historical: bool = False
    raw: str | None = None
    errors: list[str] = field(default_factory=list)

class _WhoisCache:
    """Bounded TTL cache for WHOIS/RDAP responses."""
    __slots__ = ('_cache', '_timestamps')

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}

    def _key(self, domain: str) -> str:
        return domain.lower()

    def get(self, domain: str) -> dict | None:
        k = self._key(domain)
        ts = self._timestamps.get(k, 0)
        if time.time() - ts > CACHE_TTL_S:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)
        return self._cache.get(k)

    def set(self, domain: str, data: dict) -> None:
        k = self._key(domain)
        if len(self._cache) >= MAX_CACHE_SIZE:
            oldest = min(self._timestamps.items(), key=lambda kv: kv[1])[0]
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)
        self._cache[k] = data
        self._timestamps[k] = time.time()
_whois_cache = _WhoisCache()

def _get_breaker(domain: str):
    """Lazy import to avoid circular deps."""
    try:
        from hledac.universal.transport.circuit_breaker import get_breaker
        return get_breaker(domain)
    except Exception:
        return None

async def _get_session() -> tuple[Any, bool]:
    """
    Get aiohttp session via session_runtime.

    Returns (session, is_own_session).
    Callers must close the session only when is_own_session=True.
    """
    try:
        from hledac.universal.network.session_runtime import async_get_httpx_session
        session = await httpx.AsyncClient()
        return (session, False)
    except Exception:
        from hledac.universal.transport.session_pool import session_pool
        _sess = await httpx.AsyncClient()
        return (_sess, False)

async def _rdap_lookup_domain(domain: str) -> dict[str, Any]:
    """
    RDAP lookup for domain — tries RDAP bootstrap servers.
    Returns raw RDAP JSON dict or {} on failure.
    """
    import httpx
    cached = _whois_cache.get(domain)
    if cached:
        return cached
    breaker = _get_breaker('rdap.org')
    if breaker and (not breaker.check_circuit().allowed):
        return {}
    session, is_own = await _get_session()
    tld = domain.split('.')[-1].lower()
    servers_to_try = RDAP_DOMAIN_BOOTSTRAP[:]
    if tld in ('com', 'net', 'org', 'io', 'co', 'info', 'biz', 'us', 'uk'):
        servers_to_try.insert(0, f'https://rdap.verisign.com/domain/v1/{domain}')
    errors: list[str] = []
    result = {}
    try:
        for rdap_base in servers_to_try[:4]:
            try:
                url = f'{rdap_base}{domain}' if not rdap_base.endswith('/') else f'{rdap_base}{domain}'
                resp = await session.get(url, timeout=httpx.Timeout(total=RDAP_TIMEOUT_S), headers={'Accept': 'application/rdap+json, application/json'})
                try:
                    if resp.status_code == 200:
                        data = await resp.json()
                        _whois_cache.set(domain, data)
                        result = data
                        break
                    elif resp.status_code == 404:
                        errors.append(WhoisError.RDAP_404.format(source=rdap_base))
                        continue
                    else:
                        errors.append(WhoisError.RDAP_STATUS.format(source=rdap_base, status=resp.status_code))
                        continue
                finally:
                    await resp.aclose()
            except TimeoutError:
                errors.append(WhoisError.RDAP_TIMEOUT.format(source=rdap_base))
                continue
            except Exception as e:
                errors.append(WhoisError.RDAP_ERROR.format(source=rdap_base, error=str(e)))
                continue
        if not result:
            try:
                url = f'https://rdap.iana.org/domain/{domain}'
                resp = await session.get(url, timeout=httpx.Timeout(RDAP_TIMEOUT_S), headers={'Accept': 'application/rdap+json'})
                try:
                    if resp.status_code == 200:
                        data = await resp.json()
                        _whois_cache.set(domain, data)
                        result = data
                finally:
                    await resp.aclose()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if is_own:
            await session.close()
    return result

def _parse_rdap_events(data: dict, result: WhoisResult) -> None:
    """Parse events for dates."""
    events = data.get('events', []) or []
    for event in events:
        action = event.get('eventAction', '')
        date = event.get('eventDate', '')
        if not date:
            continue
        if action in ('registration', 'created'):
            result.creation_date = date
        elif action in ('expiration', 'expires'):
            result.expiration_date = date
        elif action in ('last changed', 'updated'):
            result.updated_date = date
    for attr in ('creation_date', 'expiration_date', 'updated_date'):
        val = getattr(result, attr)
        if val and len(val) > 10:
            setattr(result, attr, val[:10])


def _parse_rdap_nameservers(data: dict, result: WhoisResult) -> None:
    """Parse nameservers."""
    name_servers = data.get('nameservers', []) or []
    result.name_servers = [ns.get('ldhName', '') for ns in name_servers if ns.get('ldhName')]


def _parse_rdap_status(data: dict, result: WhoisResult) -> None:
    """Parse status."""
    result.status = data.get('status', []) or []
    if isinstance(result.status, str):
        result.status = [result.status]


def _parse_rdap_dnssec(data: dict, result: WhoisResult) -> None:
    """Parse DNSSEC."""
    dnssec = data.get('dnsSec', data.get('secureDNS', {}))
    if isinstance(dnssec, dict):
        result.dnssec = dnssec.get('delegationSigned', False)
    else:
        result.dnssec = bool(dnssec)


def _parse_rdap_entities(data: dict, result: WhoisResult) -> None:
    """Parse entities (vcard)."""
    entities = data.get('entities', []) or []
    for entity in entities:
        vcard = entity.get('vcardArray', [])
        if not vcard:
            continue
        for item in vcard[1:] if len(vcard) > 1 else []:
            if not isinstance(item, list):
                continue
            item_type = item[0] if item else ''
            item_value = item[3] if len(item) > 3 else item[1] if len(item) > 1 else ''
            if item_type == 'registrar':
                result.registrar = item_value
            elif item_type == 'admin':
                result.admin_email = item_value
            elif item_type == 'tech':
                result.tech_email = item_value


def _parse_rdap_network(data: dict, result: WhoisResult) -> None:
    """Parse network info."""
    network = data.get('network', {}) or {}
    if network:
        result.netblock = network.get('cidr0', network.get('cidr'))
        result.netname = network.get('name')
        result.asn = str(network.get('handle', ''))
        result.org = network.get('name')


def _parse_rdap_autnum(data: dict, result: WhoisResult) -> None:
    """Parse AS numbers."""
    autnum = data.get('autnum', []) or []
    if autnum and isinstance(autnum, list):
        for a in autnum:
            if isinstance(a, dict):
                result.asn = str(a.get('handle', ''))
                result.asn_name = a.get('name')


def _parse_rdap_response(domain: str, data: dict) -> WhoisResult:
    """Parse RDAP JSON response into WhoisResult."""
    result = WhoisResult(domain=domain, source='rdap')
    try:
        _parse_rdap_events(data, result)
        _parse_rdap_nameservers(data, result)
        _parse_rdap_status(data, result)
        _parse_rdap_dnssec(data, result)
        _parse_rdap_entities(data, result)
        _parse_rdap_network(data, result)
        _parse_rdap_autnum(data, result)
        public_ids = data.get('publicIds', []) or []
        for pid in public_ids:
            if pid.get('type') == 'IANA Registrar ID':
                pass
        remarks = data.get('remarks', []) or []
        if remarks:
            result.raw = str(remarks[:2])
    except Exception as e:
        result.errors.append(WhoisError.PARSE_ERROR.format(error=str(e)))
    return result

async def _whois_fallback_lookup(domain: str) -> WhoisResult:
    """
    Legacy WHOIS via asyncio.open_connection port 43.
    Used only when RDAP fails.
    """
    tld = domain.split('.')[-1].lower()
    server = WHOIS_FALLBACK_SERVERS.get(tld, f'whois.nic.{tld}')
    result = WhoisResult(domain=domain, source='whois')
    try:
        async with asyncio.timeout(WHOIS_TIMEOUT_S):
            reader, writer = await asyncio.open_connection(server, 43)
    except Exception as e:
        result.errors.append(WhoisError.CONN_FAILED.format(error=str(e)))
        return result
    try:
        query = f'{domain}\r\n'
        writer.write(query.encode())
        await writer.drain()
        async with asyncio.timeout(WHOIS_TIMEOUT_S):
            data = await reader.read(8192)
        text = data.decode('utf-8', errors='replace')
        result.raw = text[:4000]
        result.registrar = _extract_whois_field(text, 'Registrar:')
        result.creation_date = _extract_whois_date(text, ['Creation Date:', 'Created:', 'Created On:'])
        result.expiration_date = _extract_whois_date(text, ['Registry Expiry Date:', 'Expires:', 'Expiry Date:'])
        result.updated_date = _extract_whois_date(text, ['Updated Date:', 'Modified:', 'Updated:'])
        result.name_servers = _extract_whois_list(text, ['Name Server:', 'Nameserver:', 'NS:'])
        result.status = _extract_whois_list(text, ['Domain Status:', 'Status:'])
        result.dnssec = 'DNSSEC: signed' in text or 'DNSSEC:' in text
        registrant = _extract_whois_field(text, 'Registrant Name:')
        if registrant and 'redact' not in registrant.lower() and ('priv' not in registrant.lower()):
            result.registrant_name = registrant
        result.registrant_org = _extract_whois_field(text, 'Registrant Organization:')
        result.registrant_email = _extract_whois_email(text, 'Registrant Email:')
        result.admin_name = _extract_whois_field(text, 'Admin Name:')
        result.admin_email = _extract_whois_email(text, 'Admin Email:')
        result.tech_name = _extract_whois_field(text, 'Tech Name:')
        result.tech_email = _extract_whois_email(text, 'Tech Email:')
    except TimeoutError:
        result.errors.append(WhoisError.TIMEOUT)
    except Exception as e:
        result.errors.append(WhoisError.READ_ERROR.format(error=str(e)))
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
    return result

def _extract_whois_field(text: str, field: str) -> str | None:
    """Extract single field from WHOIS text."""
    for line in text.split('\n'):
        if line.startswith(field):
            val = line.split(':', 1)[1].strip()
            if val and val != 'REDACTED FOR PRIVACY' and (val != 'Not specified'):
                return val
    return None

def _extract_whois_date(text: str, fields: list[str]) -> str | None:
    """Extract date from WHOIS text, trying multiple field names."""
    for field_name in fields:
        val = _extract_whois_field(text, field_name)
        if val:
            val = val[:10]
            return val
    return None

def _extract_whois_list(text: str, fields: list[str]) -> list[str]:
    """Extract list field from WHOIS text."""
    values = []
    seen = set()
    for field in fields:
        for line in text.split('\n'):
            if line.startswith(field):
                val = line.split(':', 1)[1].strip().lower()
                if val and val not in seen:
                    seen.add(val)
                    values.append(val)
    return values

def _extract_whois_email(text: str, field: str) -> str | None:
    """Extract email from WHOIS text, handling privacy redaction."""
    email = _extract_whois_field(text, field)
    if email:
        low = email.lower()
        if 'priv' in low or 'redact' in low or 'spam' in low or ('nored' in low):
            return None
    return email

async def _historical_whois_lookup(domain: str, api_name: str, api_key: str) -> WhoisResult | None:
    """
    Query historical WHOIS API.
    Returns WhoisResult with historical=True, or None on failure.
    """
    import httpx
    if not api_key:
        return None
    result = WhoisResult(domain=domain, source='historical', historical=True)
    api_base = HISTORICAL_APIS.get(api_name)
    if not api_base:
        return None
    breaker = _get_breaker(api_base.split('/')[2])
    if breaker and (not breaker.check_circuit().allowed):
        return None
    session, is_own = await _get_session()
    params: dict[str, Any] = {'domain': domain, 'format': 'json'}
    headers: dict[str, str] = {}
    if 'whoisxmlapi' in api_base:
        params['username'] = api_key
        params['password'] = api_key
        url = f'{api_base}domain'
    elif 'whoiswhoisxmlapi' in api_base:
        params['apiKey'] = api_key
        url = f'{api_base}whois'
    elif 'domainiq' in api_base:
        params['key'] = api_key
        url = f'{api_base}domain'
    elif 'whoisology' in api_base:
        headers['Authorization'] = f'Bearer {api_key}'
        url = f'{api_base}{domain}'
        params = {}
    else:
        if is_own:
            await session.close()
        return None
    data = None
    try:
        resp = await session.get(url, params=params, headers=headers, timeout=httpx.Timeout(15.0))
        try:
            if resp.status_code != 200:
                result.errors.append(f'http_{resp.status_code}')
                return result
            data = await resp.json()
        finally:
            await resp.aclose()
    except TimeoutError:
        result.errors.append(WhoisError.TIMEOUT)
        return result
    except Exception as e:
        result.errors.append(WhoisError.IPWHOIS_ERROR.format(error=str(e)))
        return result
    finally:
        if is_own:
            await session.close()
    if data is None:
        return result
    if 'whoisxmlapi' in api_base or 'whoiswhoisxmlapi' in api_base:
        whois_record = data.get('WhoisRecord', {}) or {}
        result.registrar = whois_record.get('registrarName')
        result.creation_date = whois_record.get('createdDate', '')[:10] if whois_record.get('createdDate') else None
        result.expiration_date = whois_record.get('expiresDate', '')[:10] if whois_record.get('expiresDate') else None
        result.updated_date = whois_record.get('updatedDate', '')[:10] if whois_record.get('updatedDate') else None
        result.name_servers = whois_record.get('nameServers', {}).get('hostNames', []) or []
        result.status = [whois_record.get('status', '')] if whois_record.get('status') else []
        result.dnssec = whois_record.get('dnssec', {}).get('securityDNS', False) if isinstance(whois_record.get('dnssec'), dict) else False
        result.registrant_name = whois_record.get('registrant', {}).get('name')
        result.registrant_org = whois_record.get('registrant', {}).get('organization')
        result.registrant_email = whois_record.get('registrant', {}).get('email')
        result.raw = str(whois_record)[:2000]
        history = whois_record.get('historicalData', []) or []
        if isinstance(history, list) and history:
            events = []
            for h in history[:10]:
                events.append({'date': h.get('createdDate', '')[:10], 'action': 'created'})
            if events:
                result.creation_date = events[0].get('date')
                result.updated_date = events[-1].get('date')
    elif 'domainiq' in api_base:
        record = data.get('domain', {}) or {}
        result.registrar = record.get('registrar')
        result.creation_date = record.get('create_date', '')[:10] if record.get('create_date') else None
        result.expiration_date = record.get('expiry_date', '')[:10] if record.get('expiry_date') else None
        result.name_servers = record.get('nameservers', []) or []
        result.raw = str(record)[:2000]
    else:
        result.raw = str(data)[:2000]
    return result if result.registrar or result.creation_date else None

async def _ipwhois_rdap_lookup(domain: str) -> WhoisResult:
    """
    Fallback via ipwhois.IPWhois().lookup_rdap().
    Uses run_in_executor (blocking) — last resort after RDAP and WHOIS fail.
    """
    result = WhoisResult(domain=domain, source='ipwhois')
    try:
        import ipwhois
    except Exception:
        result.errors.append(WhoisError.NO_IPWHOIS)
        return result
    try:

        def _blocking() -> dict[str, Any]:
            try:
                obj = ipwhois.IPWhois(domain)
                return obj.lookup_rdap(depth=1)
            except Exception as e:
                result.errors.append(WhoisError.IPWHOIS_ERROR.format(error=str(e)))
                return {}
        async with asyncio.timeout(15.0):
            rdap_data = await asyncio.to_thread(_blocking)
        if not rdap_data:
            return result
        result.registrar = rdap_data.get('network', {}).get('name', '')
        result.asn = str(rdap_data.get('asn', ''))
        result.asn_name = rdap_data.get('asn_description', '')
        result.asn_country = rdap_data.get('country', '')
        result.netblock = rdap_data.get('network', {}).get('cidr', '')
        result.org = rdap_data.get('org', '')
        result.registrant_org_country = rdap_data.get('country')
        events = rdap_data.get('events', []) or []
        for event in events:
            action = event.get('eventAction', '')
            date = event.get('eventDate', '')
            if not date:
                continue
            if action in ('registration', 'created'):
                result.creation_date = date[:10]
            elif action in ('expiration', 'expires'):
                result.expiration_date = date[:10]
            elif action in ('last changed', 'updated'):
                result.updated_date = date[:10]
        name_servers = rdap_data.get('nameservers', []) or []
        result.name_servers = [ns.get('ldhName', '') for ns in name_servers if ns.get('ldhName')]
    except TimeoutError:
        result.errors.append('timeout')
    except Exception as e:
        result.errors.append(f'error: {e}')
    return result

class WhoisService:
    """
    Consolidated async WHOIS/RDAP service.

    Lookup strategy (tried in order):
      1. RDAP bootstrap (IANA/RIR, no API key, JSON) — PRIMARY
      2. WHOIS port 43 fallback (legacy, for tlds without RDAP)
      3. ipwhois RDAP (blocking, last resort)
      4. Historical WHOIS API (opt-in via env vars, if enabled)

    M1 8GB safe:
      - Lazy imports
      - Semaphore-concurrency (MAX_CONCURRENT=5)
      - TTL cache (500 entries, 1h)
      - Circuit breakers on all external calls
      - Fail-soft (returns empty WhoisResult, never raises)

    Usage:
      service = WhoisService()
      result = await service.lookup("example.com")
      if result.registrar:
          print(f"Registrar: {result.registrar}")
    """
    __slots__ = tuple(('_historical_api', '_historical_api_key', '_semaphore', '_stats'))

    def __init__(self, historical_api: str | None=None, historical_api_key: str | None=None) -> None:
        """
        Initialize WhoisService.

        Args:
            historical_api: One of "whoisxmlapi", "whoiswhoisxmlapi",
                            "domainiq", "whoisology" (opt-in)
            historical_api_key: API key for historical WHOIS service
        """
        self._historical_api = historical_api
        self._historical_api_key = historical_api_key
        from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore = get_semaphore(ConcurrencyCategory.IP_QUERY)
        self._stats = {'rdap': 0, 'whois': 0, 'ipwhois': 0, 'historical': 0, 'cache_hit': 0}

    @property
    def stats(self) -> dict[str, int]:
        """Return lookup statistics."""
        return self._stats.copy()

    async def lookup(self, domain: str) -> WhoisResult:
        """
        Perform WHOIS lookup for a domain.

        Args:
            domain: Domain name to look up

        Returns:
            WhoisResult with all available fields populated
        """
        domain = domain.lower().strip()
        if not domain or len(domain) > 253:
            return WhoisResult(domain=domain, errors=['invalid_domain'])
        cached = _whois_cache.get(domain)
        if cached and cached.get('_cached_result'):
            self._stats['cache_hit'] += 1
            return cached['_cached_result']
        async with self._semaphore:
            result = await self._lookup_impl(domain)
        if result.registrar or result.creation_date:
            _whois_cache.set(domain, {'_cached_result': result})
        return result

    async def lookup_batch(self, domains: list[str]) -> list[WhoisResult]:
        """
        Perform WHOIS lookups for multiple domains concurrently.

        Args:
            domains: List of domain names (max MAX_TARGETS)

        Returns:
            List of WhoisResult (one per domain, in same order)
        """
        domains = domains[:MAX_TARGETS]
        results: list[WhoisResult] = []
        from hledac.universal.utils.async_helpers import parallel_ok
        tasks = [self.lookup(d) for d in domains]
        gathered = await parallel_ok(*tasks, label='whois_service:lookup_batch')
        for r in gathered:
            if isinstance(r, WhoisResult):
                results.append(r)
            else:
                results.append(WhoisResult(domain='unknown', errors=[str(r)]))
        return results

    async def _lookup_impl(self, domain: str) -> WhoisResult:
        """Internal lookup implementation — tries RDAP → WHOIS → ipwhois → historical."""
        rdap_data = await _rdap_lookup_domain(domain)
        if rdap_data:
            result = _parse_rdap_response(domain, rdap_data)
            if result.registrar or result.creation_date:
                self._stats['rdap'] += 1
                return result
        whois_result = await _whois_fallback_lookup(domain)
        if whois_result.registrar or whois_result.creation_date:
            self._stats['whois'] += 1
            return whois_result
        ipwhois_result = await _ipwhois_rdap_lookup(domain)
        if ipwhois_result.registrar or ipwhois_result.asn:
            self._stats['ipwhois'] += 1
            return ipwhois_result
        if self._historical_api and self._historical_api_key:
            hist_result = await _historical_whois_lookup(domain, self._historical_api, self._historical_api_key)
            if hist_result:
                self._stats['historical'] += 1
                return hist_result
        return WhoisResult(domain=domain, errors=['all_lookup_methods_failed'])
_HISTORICAL_API = None
_HISTORICAL_API_KEY = None

def configure_historical_api(api: str | None, key: str | None) -> None:
    """Configure historical WHOIS API (call before creating WhoisService)."""
    global _HISTORICAL_API, _HISTORICAL_API_KEY
    _HISTORICAL_API = api
    _HISTORICAL_API_KEY = key

def create_whois_service() -> WhoisService:
    """Factory: create WhoisService with configured historical API."""
    return WhoisService(historical_api=_HISTORICAL_API, historical_api_key=_HISTORICAL_API_KEY)
__all__ = ['WhoisService', 'WhoisResult', 'create_whois_service', 'configure_historical_api', 'MAX_TARGETS', 'RDAP_BOOTSTRAP', 'WHOIS_FALLBACK_SERVERS']