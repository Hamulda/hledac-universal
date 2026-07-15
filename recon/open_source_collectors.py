"""
OpenSourceCollectors — Consolidated open-source intelligence collectors.

Sprint OSINT-Collection: Unified collector for public data sources.

SOURCES:
- Paste sites: pastebin.com, paste.gg, rentry.co, privatebin, ghostbin, 0bin
- Usenet: Google Groups, GMane archives
- Chat: Matrix public rooms
- Academic: arXiv, bioRxiv, medRxiv, SSRN, PhilPapers, RePEc, Crossref, SemanticScholar
- SEC EDGAR: full-text filings via EFTS API
- Court records: CourtListener + RECAP archive

INTEGRATION:
- Session: network.session_runtime.httpx.AsyncClient()
- Transport: fetching.public_fetcher.async_fetch_public_text()
- Confidence: intelligence.confidence_policy source_family tagging
- Memory: runtime.resource_governor.M1ResourceGovernor.sidecar_admission()

BOUNDS:
- MAX_PASTE_RESULTS = 50
- MAX_USENET_ARTICLES = 200
- MAX_CHAT_MESSAGES = 300
- MAX_ACADEMIC_PAPERS = 100
- MAX_SEC_FILINGS = 100
- MAX_COURT_CASES = 50
- RATE_LIMIT_S = 2.0
- TIMEOUT_S = 30.0

GHOST_INVARIANTS:
- gather return_exceptions=True + _check_gathered()
- no time.sleep() — asyncio.sleep()
- mx.eval([]) before clear_cache if MLX used
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass
from hledac.universal.fetching.public_fetcher import FetchResult, async_fetch_public_text
from hledac.universal.runtime.resource_governor import M1ResourceGovernor
from hledac.universal.utils.async_helpers import safe_gather, safe_gather_ok
from hledac.universal.utils.msgspec_json import loads as _msgspec_loads
logger = logging.getLogger(__name__)
MAX_PASTE_RESULTS: int = 50
MAX_USENET_ARTICLES: int = 200
MAX_CHAT_MESSAGES: int = 300
MAX_ACADEMIC_PAPERS: int = 100
MAX_SEC_FILINGS: int = 100
MAX_COURT_CASES: int = 50
RATE_LIMIT_S: float = 2.0
TIMEOUT_S: float = 30.0

@dataclass(slots=True)
class PasteFinding:
    uri: str
    source: str
    extracted_secrets: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    context_snippet: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': 'pastebin', 'source_family': 'FEED', 'uri': self.uri, 'source_name': self.source, 'secrets': [_mask_secret(s) for s in self.extracted_secrets], 'emails': self.emails, 'ips': self.ip_addresses, 'snippet': self.context_snippet[:200]}

@dataclass(slots=True)
class UsenetArticle:
    message_id: str
    subject: str
    from_addr: str
    date: str
    newsgroup: str
    body: str
    url: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': 'usenet', 'source_family': 'FEED', 'message_id': self.message_id, 'subject': self.subject, 'from': self.from_addr, 'date': self.date, 'newsgroup': self.newsgroup, 'body_preview': self.body[:500], 'url': self.url}

@dataclass(frozen=True, slots=True)
class ChatMessage:
    platform: str
    channel: str
    user: str
    timestamp: str
    content: str
    message_id: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': f'{self.platform}_chat', 'source_family': 'FEED', 'platform': self.platform, 'channel': self.channel, 'user': self.user, 'timestamp': self.timestamp, 'content_preview': self.content[:200], 'message_id': self.message_id}

@dataclass(slots=True)
class AcademicPaper:
    title: str
    authors: list[str]
    year: int | None
    link: str
    source: str
    abstract: str = ''
    doi: str | None = None
    citations: int = 0
    tags: list[str] = field(default_factory=list)

    def to_finding_dict(self) -> dict:
        return {'source': 'academic', 'source_family': 'PUBLIC', 'title': self.title, 'authors': self.authors, 'year': self.year, 'link': self.link, 'source_name': self.source, 'abstract_preview': self.abstract[:500], 'doi': self.doi, 'citations': self.citations, 'tags': self.tags}

@dataclass(frozen=True, slots=True)
class EdgarFiling:
    cik: str
    company_name: str
    form_type: str
    filing_date: str
    accession_number: str
    document_url: str
    description: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': 'sec_edgar', 'source_family': 'PUBLIC', 'cik': self.cik, 'company': self.company_name, 'form': self.form_type, 'date': self.filing_date, 'accession': self.accession_number, 'url': self.document_url}

@dataclass(frozen=True, slots=True)
class CourtCase:
    case_id: str
    docket_number: str
    court: str
    case_name: str
    date_filed: str
    status: str = ''
    nature_of_suit: str = ''
    docket_url: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': 'court_records', 'source_family': 'PUBLIC', 'case_id': self.case_id, 'docket': self.docket_number, 'court': self.court, 'case_name': self.case_name, 'filed': self.date_filed, 'status': self.status, 'nature_of_suit': self.nature_of_suit, 'docket_url': self.docket_url}
_SECRET_REDACT_LEN = 4
_RE_EMAIL = re.compile('\\b[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}\\b')
_RE_IPV4 = re.compile('\\b(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\.){3}(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\b')
_RE_IPV6 = re.compile('\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b')
_RE_AWS_KEY = re.compile('\\bAKIA[0-9A-Z]{16}\\b')
_RE_BEARER = re.compile('\\bBearer\\s+[A-Za-z0-9_\\.\\-]{20,}\\b', re.IGNORECASE)
_RE_PKEY = re.compile('-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', re.IGNORECASE)
_RE_TOKEN = re.compile('\\b(?:token|key|secret|password|passwd|pwd|auth|credential)[\'\\"]?[:=]?\\s*[\'\\"]?([A-Za-z0-9_\\-]{16,64})[\'\\"]?\\b', re.IGNORECASE)

def _mask_secret(value: str) -> str:
    if len(value) <= _SECRET_REDACT_LEN:
        return '*' * len(value)
    return value[:-_SECRET_REDACT_LEN] + '*' * _SECRET_REDACT_LEN

def _extract_secrets(text: str) -> tuple[list[str], list[str], list[str]]:
    emails = _RE_EMAIL.findall(text)
    ipv4s = _RE_IPV4.findall(text)
    ipv6s = _RE_IPV6.findall(text)
    ip_addresses = ipv4s + ipv6s
    secrets: list[str] = []
    for pat in (_RE_AWS_KEY, _RE_BEARER, _RE_PKEY):
        secrets.extend(pat.findall(text))
    for m in _RE_TOKEN.finditer(text):
        secrets.append(m.group(1))
    return (emails, ip_addresses, secrets)
_PASTE_RATE_LIMIT_S = 1.0
_last_paste_request: float = 0.0
_paste_rate_lock: asyncio.Lock | None = None

def _get_paste_rate_lock() -> asyncio.Lock:
    """ISSUE-014 FIX: Lazily create paste rate lock in the current event loop."""
    global _paste_rate_lock
    if _paste_rate_lock is None:
        _paste_rate_lock = asyncio.Lock()
    return _paste_rate_lock

async def _scrape_pastebin_raw(paste_id: str) -> str | None:
    url = f'https://pastebin.com/raw/{paste_id}'
    try:
        result: FetchResult = await async_fetch_public_text(url, timeout_s=10.0, max_bytes=2 * 1024 * 1024)
        if result.status_code == 404 or result.error or result.text is None:
            return None
        return result.text
    except asyncio.CancelledError:
        raise
    except Exception:
        return None

async def _scrape_paste_gg(paste_id: str) -> str | None:
    url = f'https://paste.gg/api/v1/pastes/{paste_id}'
    try:
        result: FetchResult = await async_fetch_public_text(url, timeout_s=10.0, max_bytes=2 * 1024 * 1024)
        if result.status_code == 404 or result.error or result.text is None:
            return None
        data = re.sub('<!--[\\s\\S]*?-->', '', result.text)
        import json
        parsed = _msgspec_loads(data)
        files = (parsed.get('data') or {}).get('files') or []
        return files[0].get('content') or '' if files else ''
    except asyncio.CancelledError:
        raise
    except Exception:
        return None

async def _scrape_rentry(raw_path: str) -> str | None:
    url = f'https://rentry.co/{raw_path}/raw'
    try:
        result: FetchResult = await async_fetch_public_text(url, timeout_s=10.0, max_bytes=2 * 1024 * 1024)
        if result.status_code == 404 or result.error or result.text is None:
            return None
        return result.text
    except asyncio.CancelledError:
        raise
    except Exception:
        return None

class PasteSiteAdapter(Protocol):
    """Contract every paste-site adapter must satisfy (duck-typed).

    Concrete adapters are frozen dataclasses that expose:
        site_id: str        — registry key, used for cache/inflight dedup
        host:   str         — used for per-host Semaphore
        timeout_s: float    — async_fetch_public_text timeout
        max_bytes: int      — async_fetch_public_text max response size
        build_url(paste_id) -> str | list[str]
        parse(body, paste_id) -> str | None
    """
    site_id: str
    host: str
    timeout_s: float
    max_bytes: int

    def build_url(self, paste_id: str) -> str | list[str]:
        """Return one URL or an ordered list of fallback URLs to try."""
        ...

    def parse(self, body: str, paste_id: str) -> str | None:
        """Parse the response body. Return None on parse error or empty body."""
        ...

@dataclass(slots=True, frozen=True)
class _RawPasteAdapter:
    """Trivial adapter: response body IS the paste text (ghostbin, rentry, pastebin_raw)."""
    site_id: str
    host: str
    url_template: str
    timeout_s: float = 10.0
    max_bytes: int = 2 * 1024 * 1024

    def build_url(self, paste_id: str) -> str:
        return self.url_template.format(paste_id=paste_id)

    def parse(self, body: str, paste_id: str) -> str | None:
        return body or None

@dataclass(slots=True, frozen=True)
class _PrivateBinAdapter:
    """PrivateBin v2 → v1 fallback + encrypted-paste marker detection.

    Behavior preserved bit-for-bit from the original _scrape_privatebin:
    - try v2 endpoint first, then v1
    - if response has both 'ct' and 'adata' → encrypted marker
    - if response has 'content' → return its value
    - on any exception in the parse of v2 → fall through to v1
    """
    site_id: str = 'privatebin'
    host: str = 'privatebin.net'
    timeout_s: float = 10.0
    max_bytes: int = 2 * 1024 * 1024

    def build_url(self, paste_id: str) -> list[str]:
        return [f'https://privatebin.net/api/v2/paste/{paste_id}?format=json', f'https://privatebin.net/api/v1/paste/{paste_id}?format=json']

    def parse(self, body: str, paste_id: str) -> str | None:
        import json
        try:
            data = _msgspec_loads(body)
        except (json.JSONDecodeError, ValueError):
            return None
        if 'ct' in data and 'adata' in data:
            return f'[PrivateBin encrypted - id:{paste_id}]'
        if 'content' in data:
            return data.get('content') or None
        return None

@dataclass(slots=True, frozen=True)
class _ZeroBinAdapter:
    """0bin HTML page → extract <pre class='paste-content'> with len > 10."""
    site_id: str = '0bin'
    host: str = '0bin.net'
    timeout_s: float = 10.0
    max_bytes: int = 2 * 1024 * 1024

    def build_url(self, paste_id: str) -> str:
        return f'https://0bin.net/p/{paste_id}'

    def parse(self, body: str, paste_id: str) -> str | None:
        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            return None
        tree = HTMLParser(body)
        for elem in tree.css('pre.paste-content, textarea.paste-content, .paste-content'):
            text = elem.text()
            if text and len(text) > 10:
                return text.strip()
        return None
PRIVATEBIN_ADAPTER: _PrivateBinAdapter = _PrivateBinAdapter()
GHOSTBIN_ADAPTER: _RawPasteAdapter = _RawPasteAdapter(site_id='ghostbin', host='ghostbin.com', url_template='https://ghostbin.com/paste/{paste_id}/raw')
ZEROBIN_ADAPTER: _ZeroBinAdapter = _ZeroBinAdapter()
_PASTE_CACHE_MAX: int = 2000
_PASTE_CACHE_TTL_S: float = 3600.0
_PASTE_CACHE_EVICT_FRAC: float = 0.1
_PASTE_HOST_SEMAPHORE: int = 3
_paste_cache: OrderedDict[tuple[str, str], tuple[float, str | None]] = OrderedDict()
_paste_inflight: dict[tuple[str, str], asyncio.Future[str | None]] = {}
_paste_host_sems: dict[str, asyncio.Semaphore] = {}

def _paste_cache_get(site_id: str, paste_id: str) -> tuple[bool, str | None]:
    """Returns (hit, body). Body is meaningful only when hit=True.

    TTL-expired entries are evicted lazily on read.
    """
    key = (site_id, paste_id)
    entry = _paste_cache.get(key)
    if entry is None:
        return (False, None)
    ts, body = entry
    if time.monotonic() - ts > _PASTE_CACHE_TTL_S:
        _paste_cache.pop(key, None)
        return (False, None)
    return (True, body)

def _paste_cache_put(site_id: str, paste_id: str, body: str | None) -> None:
    """Bounded insert. On overflow, evicts oldest 10% (FIFO)."""
    if len(_paste_cache) >= _PASTE_CACHE_MAX:
        evict_count = max(1, int(_PASTE_CACHE_MAX * _PASTE_CACHE_EVICT_FRAC))
        for _ in range(evict_count):
            if not _paste_cache:
                break
            _paste_cache.popitem(last=False)
    _paste_cache[site_id, paste_id] = (time.monotonic(), body)

def _host_semaphore(host: str) -> asyncio.Semaphore:
    """Lazy per-host Semaphore creation (M1 soft cap 3 concurrent fetches/host)."""
    sem = _paste_host_sems.get(host)
    if sem is None:
        sem = asyncio.Semaphore(_PASTE_HOST_SEMAPHORE)
        _paste_host_sems[host] = sem
    return sem

async def _scrape_paste_site(adapter: _RawPasteAdapter | _PrivateBinAdapter | _ZeroBinAdapter, paste_id: str) -> str | None:
    """Canonical base for paste-site scrapers.

    Pipeline (M1 8GB friendly):
        1. LRU+TTL cache check → return on hit (bounded by _PASTE_CACHE_MAX)
        2. Atomic claim leadership via dict.setdefault (race-free under
           concurrent gather — N callers → exactly 1 leader, N-1 followers)
        3. Run fetch under per-host semaphore (M1 soft cap)
        4. Build URL(s) via adapter → fetch with timeout/byte cap
        5. adapter.parse() → return parsed text; None falls through to next URL
        6. Cache write (positive and negative results)
        7. Fail-soft: any Exception → None; CancelledError → re-raise (invariant)

    Concurrent callers for the same (site, paste_id) await the same Future
    and receive the same text-or-None result. No duplicate network requests.
    """
    site_id: str = adapter.site_id
    key: tuple[str, str] = (site_id, paste_id)
    hit, cached = _paste_cache_get(site_id, paste_id)
    if hit:
        return cached
    loop = asyncio.get_running_loop()
    new_future: asyncio.Future[str | None] = loop.create_future()
    existing_future = _paste_inflight.setdefault(key, new_future)
    if existing_future is not new_future:
        try:
            return await existing_future
        except asyncio.CancelledError:
            raise
    try:
        sem = _host_semaphore(adapter.host)
        async with sem:
            urls = adapter.build_url(paste_id)
            url_list: list[str] = urls if isinstance(urls, list) else [urls]
            final: str | None = None
            for url in url_list:
                try:
                    result: FetchResult = await async_fetch_public_text(url, timeout_s=adapter.timeout_s, max_bytes=adapter.max_bytes)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(f'{site_id} fetch paste_id={paste_id} url={url} failed: {e}')
                    continue
                if result.status_code == 404 or result.error or result.text is None:
                    continue
                try:
                    parsed = adapter.parse(result.text, paste_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug(f'{site_id} parse paste_id={paste_id} url={url} failed: {e}')
                    continue
                if parsed is not None:
                    final = parsed
                    break
            _paste_cache_put(site_id, paste_id, final)
            if not new_future.done():
                new_future.set_result(final)
            return final
    except asyncio.CancelledError:
        if not new_future.done():
            new_future.cancel()
        raise
    except Exception as e:
        logger.debug(f'{site_id} leader paste_id={paste_id} failed: {e}')
        if not new_future.done():
            new_future.set_result(None)
        return None
    finally:
        _paste_inflight.pop(key, None)
        if not new_future.done() and (not new_future.cancelled()):
            new_future.set_result(None)

async def _scrape_privatebin(paste_id: str) -> str | None:
    return await _scrape_paste_site(PRIVATEBIN_ADAPTER, paste_id)

async def _scrape_ghostbin(paste_id: str) -> str | None:
    return await _scrape_paste_site(GHOSTBIN_ADAPTER, paste_id)

async def _scrape_0bin(paste_id: str) -> str | None:
    return await _scrape_paste_site(ZEROBIN_ADAPTER, paste_id)

async def search_paste_sites(query: str, max_results: int=MAX_PASTE_RESULTS) -> list[PasteFinding]:
    """Search paste sites for secrets/leaks."""
    global _last_paste_request
    async with _get_paste_rate_lock():
        elapsed = time.time() - _last_paste_request
        if elapsed < _PASTE_RATE_LIMIT_S:
            await asyncio.sleep(_PASTE_RATE_LIMIT_S - elapsed)
        _last_paste_request = time.time()
    findings: list[PasteFinding] = []
    session = await httpx.AsyncClient()

    async def search_pastebin() -> list[PasteFinding]:
        try:
            search_url = f'https://pastebin.com/search?q={query}'
            result: FetchResult = await async_fetch_public_text(search_url, timeout_s=15.0, max_bytes=2 * 1024 * 1024)
            if result.status_code != 200 or result.error or result.text is None:
                return []
            try:
                from selectolax.parser import HTMLParser
            except ImportError:
                return []
            tree = HTMLParser(result.text)
            paste_ids: list[str] = []
            for a in tree.css('a'):
                href = a.attributes.get('href', '')
                if '/dpaste/' in href or '/raw/' in href:
                    pid = href.rstrip('/').split('/')[-1]
                    if pid:
                        paste_ids.append(pid)
            paste_id_list = paste_ids[:10]

            async def _scrape_one(pid: str) -> PasteFinding | None:
                text = await _scrape_pastebin_raw(pid)
                if not text:
                    return None
                emails, ips, secrets = _extract_secrets(text)
                if not (emails or ips or secrets):
                    return None
                return PasteFinding(uri=f'https://pastebin.com/{pid}', source='pastebin', extracted_secrets=secrets, emails=emails, ip_addresses=ips, context_snippet=text[:200])
            scraped = await safe_gather_ok(*tuple((_scrape_one(pid) for pid in paste_id_list)), label='pastebin_scrape')
            results = [r for r in scraped if r is not None]
            return results
        except Exception as e:
            logger.debug(f'pastebin search failed: {e}')
            return []

    async def search_paste_gg() -> list[PasteFinding]:
        try:
            resp = await session.post('https://paste.gg/api/v1/pastes/search', json={'query': query, 'limit': 10}, timeout=15.0)
            if resp.status_code != 200:
                return []
            data = await resp.json()
            items = (data.get('data') or {}).get('pasties') or []
            item_list = items[:10]

            async def _scrape_one(item: dict) -> PasteFinding | None:
                paste_id = item.get('id', '')
                text = await _scrape_paste_gg(paste_id)
                if not text:
                    return None
                emails, ips, secrets = _extract_secrets(text)
                if not (emails or ips or secrets):
                    return None
                return PasteFinding(uri=f'https://paste.gg/{paste_id}', source='paste_gg', extracted_secrets=secrets, emails=emails, ip_addresses=ips, context_snippet=text[:200])
            scraped = await safe_gather_ok(*tuple((_scrape_one(item) for item in item_list)), label='paste_gg_scrape')
            results = [r for r in scraped if r is not None]
            return results
        except Exception as e:
            logger.debug(f'paste.gg search failed: {e}')
            return []

    async def search_rentry() -> list[PasteFinding]:
        try:
            resp = await session.get(f'https://rentry.co/search?query={query}', timeout=15.0)
            if resp.status_code != 200:
                return []
            html = resp.text()
            try:
                from selectolax.parser import HTMLParser
            except ImportError:
                return []
            tree = HTMLParser(html)
            raw_paths: list[str] = []
            for a in tree.css('a'):
                href = a.attributes.get('href', '')
                if href.startswith('/') and len(href) > 2:
                    raw_paths.append(href.lstrip('/'))
            path_list = raw_paths[:10]

            async def _scrape_one(path: str) -> PasteFinding | None:
                text = await _scrape_rentry(path)
                if not text:
                    return None
                emails, ips, secrets = _extract_secrets(text)
                if not (emails or ips or secrets):
                    return None
                return PasteFinding(uri=f'https://rentry.co/{path}', source='rentry', extracted_secrets=secrets, emails=emails, ip_addresses=ips, context_snippet=text[:200])
            scraped = await safe_gather_ok(*tuple((_scrape_one(p) for p in path_list)), label='rentry_scrape')
            results = [r for r in scraped if r is not None]
            return results
        except Exception as e:
            logger.debug(f'rentry search failed: {e}')
            return []
    _result = await safe_gather(search_pastebin(), search_paste_gg(), search_rentry(), label='paste_sites')
    gathered = _result.ok
    for res in gathered:
        if isinstance(res, list):
            findings.extend(res)
    return findings[:max_results]
_USENET_RATE_LIMIT_S = 2.0
_last_usenet_request: float = 0.0
_usenet_rate_lock = asyncio.Lock()

async def search_usenet(query: str, max_results: int=MAX_USENET_ARTICLES) -> list[UsenetArticle]:
    """Search Usenet archives via Google Groups and GMane."""
    global _last_usenet_request
    async with _usenet_rate_lock:
        elapsed = time.time() - _last_usenet_request
        if elapsed < _USENET_RATE_LIMIT_S:
            await asyncio.sleep(_USENET_RATE_LIMIT_S - elapsed)
        _last_usenet_request = time.time()
    articles: list[UsenetArticle] = []
    session = await httpx.AsyncClient()

    async def search_google_groups() -> list[UsenetArticle]:
        try:
            import httpx
            async with session.get('https://groups.google.com/d/msg', params={'q': query, 'num': str(min(max_results, 50))}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
            try:
                from selectolax.parser import HTMLParser
            except ImportError:
                return []
            tree = HTMLParser(html)
            results: list[UsenetArticle] = []
            for a in tree.css("a[href*='/msg/']"):
                href = a.attributes.get('href', '')
                if not href or '/msg/' not in href:
                    continue
                subject = a.text().strip()
                if not subject:
                    continue
                msg_match = re.search('/msg/([^/]+)/(\\d+)', href)
                if msg_match:
                    results.append(UsenetArticle(message_id=msg_match.group(2), subject=subject, from_addr='', date='', newsgroup=msg_match.group(1), body='', url=f'https://groups.google.com{href}'))
            return results
        except Exception as e:
            logger.debug(f'Google Groups search failed: {e}')
            return []

    async def search_gmane() -> list[UsenetArticle]:
        try:
            import httpx
            async with session.get('https://news.gmane.io/search', params={'query': query, 'num': str(min(max_results, 50))}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
            try:
                from selectolax.parser import HTMLParser
            except ImportError:
                return []
            tree = HTMLParser(html)
            results: list[UsenetArticle] = []
            for a in tree.css("a[href*='/message/id/']"):
                href = a.attributes.get('href', '')
                subject = a.text().strip()
                if not subject:
                    continue
                results.append(UsenetArticle(message_id=href.split('/')[-1], subject=subject, from_addr='', date='', newsgroup='', body='', url=f'https://news.gmane.io{href}'))
            return results
        except Exception as e:
            logger.debug(f'GMane search failed: {e}')
            return []
    _result = await safe_gather(search_google_groups(), search_gmane(), label='usenet')
    gathered = _result.ok
    seen_ids: set[str] = set()
    for res in gathered:
        if isinstance(res, list):
            for article in res:
                if article.message_id and article.message_id not in seen_ids:
                    seen_ids.add(article.message_id)
                    articles.append(article)
    return articles[:max_results]
_MATRIX_RATE_LIMIT_S = 2.0
_last_matrix_request: float = 0.0
_matrix_rate_lock = asyncio.Lock()

async def search_matrix(query: str, max_results: int=MAX_CHAT_MESSAGES) -> list[ChatMessage]:
    """Search public Matrix rooms."""
    global _last_matrix_request
    async with _matrix_rate_lock:
        elapsed = time.time() - _last_matrix_request
        if elapsed < _MATRIX_RATE_LIMIT_S:
            await asyncio.sleep(_MATRIX_RATE_LIMIT_S - elapsed)
        _last_matrix_request = time.time()
    messages: list[ChatMessage] = []
    session = await httpx.AsyncClient()

    async def search_public_rooms() -> list[str]:
        try:
            import httpx
            async with session.get('https://matrix.org/_matrix/client/r0/publicRooms', params={'limit': '50'}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
            room_ids: list[str] = []
            for room in data.get('chunk', []):
                room_id = room.get('room_id', '')
                if room_id:
                    room_ids.append(room_id)
            return room_ids
        except Exception as e:
            logger.debug(f'Matrix room search failed: {e}')
            return []

    async def fetch_room_messages(room_id: str) -> list[ChatMessage]:
        try:
            import httpx
            async with session.get(f'https://matrix.org/_matrix/client/r0/rooms/{room_id}/messages', params={'dir': 'b', 'limit': '50', 'filter': '{"types":["m.room.message"]}'}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status == 403:
                    return []
                if resp.status != 200:
                    return []
                data = await resp.json()
            results: list[ChatMessage] = []
            for event in data.get('chunk', []):
                if event.get('type') != 'm.room.message':
                    continue
                content = event.get('content', {})
                if content.get('msgtype') != 'm.text':
                    continue
                body = content.get('body', '')
                if query.lower() in body.lower():
                    results.append(ChatMessage(platform='matrix', channel=room_id, user=event.get('sender', ''), timestamp=str(event.get('origin_server_ts', '')), content=body, message_id=event.get('event_id', '')))
            return results
        except Exception as e:
            logger.debug(f'Matrix room fetch failed: {e}')
            return []
    room_ids = await search_public_rooms()
    if not room_ids:
        return []
    tasks = [fetch_room_messages(rid) for rid in room_ids[:10]]
    _result = await safe_gather(*tasks, label='matrix')
    gathered = _result.ok
    for res in gathered:
        if isinstance(res, list):
            messages.extend(res)
    return messages[:max_results]
_ACADEMIC_RATE_LIMIT_S = 2.0
_last_academic_request: float = 0.0
_academic_rate_lock = asyncio.Lock()

async def search_academic(query: str, max_results: int=MAX_ACADEMIC_PAPERS) -> list[AcademicPaper]:
    """Search academic preprint servers."""
    global _last_academic_request
    async with _academic_rate_lock:
        elapsed = time.time() - _last_academic_request
        if elapsed < _ACADEMIC_RATE_LIMIT_S:
            await asyncio.sleep(_ACADEMIC_RATE_LIMIT_S - elapsed)
        _last_academic_request = time.time()
    papers: list[AcademicPaper] = []
    session = await httpx.AsyncClient()

    async def search_biorxiv() -> list[AcademicPaper]:
        try:
            import httpx
            async with session.get('https://api.biorxiv.org/details/biorxiv/0/1/50', params={'q': query}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
            results: list[AcademicPaper] = []
            for item in data.get('collection', []):
                results.append(AcademicPaper(title=item.get('title', ''), authors=item.get('authors', '').split(';'), year=item.get('year'), link=f"https://doi.org/{item['doi']}" if item.get('doi') else '', source='biorxiv', abstract=item.get('abstract', ''), doi=item.get('doi'), citations=0, tags=item.get('categories', [])))
            return results
        except Exception as e:
            logger.debug(f'bioRxiv search failed: {e}')
            return []

    async def search_medrxiv() -> list[AcademicPaper]:
        try:
            import httpx
            async with session.get('https://api.medrxiv.org/details/medrxiv/0/1/50', params={'q': query}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
            results: list[AcademicPaper] = []
            for item in data.get('collection', []):
                results.append(AcademicPaper(title=item.get('title', ''), authors=item.get('authors', '').split(';'), year=item.get('year'), link=f"https://doi.org/{item['doi']}" if item.get('doi') else '', source='medrxiv', abstract=item.get('abstract', ''), doi=item.get('doi'), citations=0, tags=item.get('categories', [])))
            return results
        except Exception as e:
            logger.debug(f'medRxiv search failed: {e}')
            return []

    async def search_ssrn() -> list[AcademicPaper]:
        try:
            import httpx
            async with session.get('https://api.ssrn.com/content/search', params={'q': query, 'topdf': 'false', 'numResults': '50'}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
            results: list[AcademicPaper] = []
            for item in data.get('results', []):
                authors_data = item.get('authors', [])
                authors = [a.get('name', '') for a in authors_data] if isinstance(authors_data, list) else []
                results.append(AcademicPaper(title=item.get('title', ''), authors=authors, year=item.get('year'), link=item.get('url', ''), source='ssrn', abstract=item.get('abstract', ''), doi=None, citations=item.get('downloadCount', 0), tags=[]))
            return results
        except Exception as e:
            logger.debug(f'SSRN search failed: {e}')
            return []

    async def search_repec() -> list[AcademicPaper]:
        try:
            import httpx
            async with session.get('https://econpapers.repec.org/search/', params={'q': query, 'limit': '50'}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
                if resp.status != 200:
                    return []
                html = await resp.text()
            try:
                from selectolax.parser import HTMLParser
            except ImportError:
                return []
            tree = HTMLParser(html)
            results: list[AcademicPaper] = []
            for article in tree.css('div.panel-content'):
                title_elem = article.css_first('h5 a, .headline a')
                if not title_elem:
                    continue
                title = title_elem.text()
                url = title_elem.attributes.get('href', '')
                author_elems = article.css('span.author a')
                authors = [a.text() for a in author_elems] if author_elems else []
                year_elem = article.css_first('span.year')
                year = None
                if year_elem:
                    try:
                        year = int(year_elem.text())
                    except (ValueError, TypeError):
                        pass
                results.append(AcademicPaper(title=title, authors=authors, year=year, link=url, source='repec', abstract='', doi=None, citations=0, tags=[]))
            return results[:20]
        except Exception as e:
            logger.debug(f'RePEc search failed: {e}')
            return []
    _result = await safe_gather(search_biorxiv(), search_medrxiv(), search_ssrn(), search_repec(), label='academic')
    gathered = _result.ok
    for res in gathered:
        if isinstance(res, list):
            papers.extend(res)
    return papers[:max_results]
_SEC_RATE_LIMIT_S = 1.0
_last_sec_request: float = 0.0
_sec_rate_lock = asyncio.Lock()

async def search_sec_edgar(query: str, max_results: int=MAX_SEC_FILINGS) -> list[EdgarFiling]:
    """Search SEC EDGAR full-text filings via EFTS API."""
    global _last_sec_request
    async with _sec_rate_lock:
        elapsed = time.time() - _last_sec_request
        if elapsed < _SEC_RATE_LIMIT_S:
            await asyncio.sleep(_SEC_RATE_LIMIT_S - elapsed)
        _last_sec_request = time.time()
    filings: list[EdgarFiling] = []
    session = await httpx.AsyncClient()
    try:
        import httpx
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; research bot)'}
        async with session.get('https://efts.sec.gov/LATEST/search-index', params={'q': query, 'dateRange': 'custom'}, headers=headers, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
            if resp.status in (403, 429):
                return []
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
        for hit in data.get('hits', {}).get('hits', []):
            source = hit.get('_source', {})
            filings.append(EdgarFiling(cik=source.get('cik', ''), company_name=source.get('company_name', ''), form_type=source.get('form_type', ''), filing_date=source.get('filing_date', ''), accession_number=source.get('accession_number', ''), document_url=source.get('document_url', ''), description=source.get('description', '')))
    except Exception as e:
        logger.debug(f'SEC EDGAR search failed: {e}')
    return filings[:max_results]
_COURT_RATE_LIMIT_S = 2.0
_last_court_request: float = 0.0
_court_rate_lock = asyncio.Lock()

async def search_court_records(query: str, max_results: int=MAX_COURT_CASES) -> list[CourtCase]:
    """Search federal court cases via CourtListener API."""
    global _last_court_request
    async with _court_rate_lock:
        elapsed = time.time() - _last_court_request
        if elapsed < _COURT_RATE_LIMIT_S:
            await asyncio.sleep(_COURT_RATE_LIMIT_S - elapsed)
        _last_court_request = time.time()
    cases: list[CourtCase] = []
    session = await httpx.AsyncClient()
    try:
        import httpx
        async with session.get('https://www.courtlistener.com/api/rest/v3/docket/', params={'q': query, 'order_by': 'dateFiled desc', 'page_size': str(min(max_results, 50))}, headers={'User-Agent': 'research-bot/1.0'}, timeout=httpx.Timeout(total=TIMEOUT_S)) as resp:
            if resp.status == 429:
                return []
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
        for result in data.get('results', []):
            cases.append(CourtCase(case_id=str(result.get('id', '')), docket_number=result.get('docket_number', ''), court=result.get('court', {}).get('short_name', ''), case_name=result.get('case_name', ''), date_filed=result.get('date_filed', ''), status=result.get('status', ''), nature_of_suit=result.get('nature_of_suit', ''), docket_url=result.get('absolute_url', '')))
    except Exception as e:
        logger.debug(f'Court records search failed: {e}')
    return cases[:max_results]

class OpenSourceCollectors:
    """
    Unified collector for open-source intelligence sources.

    Integrates with:
    - Session: httpx.AsyncClient()
    - Transport: async_fetch_public_text()
    - Memory: M1ResourceGovernor.sidecar_admission()
    - Confidence: source_family tagging in all findings
    """
    __slots__ = tuple(('_governor',))

    def __init__(self) -> None:
        self._governor: M1ResourceGovernor | None = None

    def _get_governor(self) -> M1ResourceGovernor | None:
        """Lazy load governor singleton to avoid circular imports and ensure consistent state."""
        if self._governor is None:
            try:
                from hledac.universal.core.protocols import get_governor
                self._governor = get_governor()
            except Exception:
                pass
        return self._governor

    def _check_admission(self, name: str, est_mb: int=30) -> bool:
        """Check M1ResourceGovernor admission. Returns True if allowed."""
        governor = self._get_governor()
        if governor is None:
            return True
        try:
            admission = governor.sidecar_admission(name, est_mb)
            return admission.allowed
        except Exception:
            return True

    async def search_pastebin(self, query: str, max_results: int=MAX_PASTE_RESULTS) -> list[PasteFinding]:
        """Search paste sites for secrets/leaks."""
        if not self._check_admission('open_source_collectors.pastebin', est_mb=20):
            return []
        return await search_paste_sites(query, max_results)

    async def search_usenet(self, query: str, max_results: int=MAX_USENET_ARTICLES) -> list[UsenetArticle]:
        """Search Usenet archives."""
        if not self._check_admission('open_source_collectors.usenet', est_mb=30):
            return []
        return await search_usenet(query, max_results)

    async def search_matrix(self, query: str, max_results: int=MAX_CHAT_MESSAGES) -> list[ChatMessage]:
        """Search public Matrix rooms."""
        if not self._check_admission('open_source_collectors.matrix', est_mb=25):
            return []
        return await search_matrix(query, max_results)

    async def search_academic(self, query: str, max_results: int=MAX_ACADEMIC_PAPERS) -> list[AcademicPaper]:
        """Search academic preprint servers."""
        if not self._check_admission('open_source_collectors.academic', est_mb=30):
            return []
        return await search_academic(query, max_results)

    async def search_sec_edgar(self, query: str, max_results: int=MAX_SEC_FILINGS) -> list[EdgarFiling]:
        """Search SEC EDGAR filings."""
        if not self._check_admission('open_source_collectors.sec_edgar', est_mb=25):
            return []
        return await search_sec_edgar(query, max_results)

    async def search_court_records(self, query: str, max_results: int=MAX_COURT_CASES) -> list[CourtCase]:
        """Search federal court cases."""
        if not self._check_admission('open_source_collectors.court_records', est_mb=25):
            return []
        return await search_court_records(query, max_results)

    async def gather_all(self, query: str, sources: list[str] | None=None) -> dict[str, list[dict]]:
        """
        Gather from all or specified sources.

        Args:
            query: Search query
            sources: List of sources to search. If None, searches all.
                    Options: pastebin, usenet, matrix, academic, sec_edgar, court_records

        Returns:
            Dict mapping source name to list of finding dicts
        """
        if sources is None:
            sources = ['pastebin', 'usenet', 'matrix', 'academic', 'sec_edgar', 'court_records']
        results: dict[str, list[dict]] = {}

        async def gather_pastebin():
            if 'pastebin' not in sources:
                return
            findings = await self.search_pastebin(query)
            results['pastebin'] = [f.to_finding_dict() for f in findings]

        async def gather_usenet():
            if 'usenet' not in sources:
                return
            articles = await self.search_usenet(query)
            results['usenet'] = [a.to_finding_dict() for a in articles]

        async def gather_matrix():
            if 'matrix' not in sources:
                return
            messages = await self.search_matrix(query)
            results['matrix'] = [m.to_finding_dict() for m in messages]

        async def gather_academic():
            if 'academic' not in sources:
                return
            papers = await self.search_academic(query)
            results['academic'] = [p.to_finding_dict() for p in papers]

        async def gather_sec_edgar():
            if 'sec_edgar' not in sources:
                return
            filings = await self.search_sec_edgar(query)
            results['sec_edgar'] = [f.to_finding_dict() for f in filings]

        async def gather_court_records():
            if 'court_records' not in sources:
                return
            cases = await self.search_court_records(query)
            results['court_records'] = [c.to_finding_dict() for c in cases]
        _result = await safe_gather(gather_pastebin(), gather_usenet(), gather_matrix(), gather_academic(), gather_sec_edgar(), gather_court_records(), label='open_source_collectors.gather_all')
        return results

    async def close(self) -> None:
        """Graceful shutdown — no-op since sessions are shared singletons."""
        pass
_collector: OpenSourceCollectors | None = None

def get_open_source_collectors() -> OpenSourceCollectors:
    """Get the canonical OpenSourceCollectors singleton."""
    global _collector
    if _collector is None:
        _collector = OpenSourceCollectors()
    return _collector