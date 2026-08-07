"""
Wayback Machine Sitemap Archive Adapter.

Sprint P2-2: Wayback Machine Sitemap archive support.

Internet Archive ukládá sitemaps (sitemap.xml) navštívených domén.
Tento adapter:
1. Zkusí https://web.archive.org/list/google-sitemaps/{domain} — XML index sitemapů
2. Pro každý sitemapindex entry fetchne a rozparsuje <url> entries
3. Vrátí DiscoveryBatchResult s archivovanými URL

Pravidla:
- HTTP API only (aiohttp přes async_fetch_public_text)
- bounded top-k (max 200 sitemap URLs, max 1000 discovery hits)
- dedup URLs (preserve-first ordering)
- passive only (žádné ukládání obsahu)
- fail-soft na všech chybách
- M1-safe: pouze stdlib + aiohttp, žádné torch/sklearn

Env gate: HLEDAC_ENABLE_WAYBACK_SITEMAP=1 (default disabled)
"""
import asyncio
import logging
import os
import time
import urllib.parse
try:
    import defusedxml.ElementTree as _DET
except ImportError:
    import xml.etree.ElementTree as _DET
from hledac.universal.discovery.base import DiscoveryBatchResult, DiscoveryHit
from hledac.universal.fetching.public_fetcher import async_fetch_public_text
from hledac.universal.utils.async_helpers import parallel_ok, safe_wait_for
logger = logging.getLogger(__name__)
_SOURCE_NAME: str = 'wayback_sitemap'
_MAX_SITEMAPS: int = 50
_MAX_URLS_PER_SITEMAP: int = 20
_HARD_MAX_HITS: int = 1000
_DEFAULT_TIMEOUT_S: float = 30.0
_PER_SITEMAP_TIMEOUT_S: float = 5.0
_WAYBACK_LIST_URL = 'https://web.archive.org/list/google-sitemaps/{domain}'
_ENVAR: str = 'HLEDAC_ENABLE_WAYBACK_SITEMAP'

def _is_enabled() -> bool:
    """Check env gate at call time."""
    return os.environ.get(_ENVAR, '0').strip().lower() in ('1', 'true', 'yes', 'on')

def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix from tag name."""
    if tag is None:
        return ''
    return tag.split('}', 1)[-1] if '}' in tag else tag

def _parse_sitemapindex_xml(xml_bytes: bytes) -> list[str]:
    """
    Parse sitemapindex XML and return list of sitemap <loc> URLs.

    Supports:
    - Standard: <sitemapindex><sitemap><loc>...
    - Atom: <feed><entry><link href=...>
    """
    sitemap_locs: list[str] = []
    try:
        root = _DET.fromstring(xml_bytes)
    except Exception:
        return sitemap_locs
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in ('sitemap', 'Sitemap'):
            loc = _find_child_text(elem, ('loc', 'Loc', 'URL', 'url'))
            if loc:
                sitemap_locs.append(loc)
    if not sitemap_locs:
        for elem in root.iter():
            tag = _strip_ns(elem.tag)
            if tag in ('entry', 'Entry'):
                for child in elem:
                    child_tag = _strip_ns(child.tag)
                    if child_tag in ('link', 'Link'):
                        href = child.attrib.get('href', '')
                        if href and '/sitemap' in href.lower():
                            sitemap_locs.append(href)
    return sitemap_locs

def _parse_sitemap_xml(xml_bytes: bytes) -> list[str]:
    """
    Parse individual sitemap XML and return list of URL <loc> entries.

    Supports:
    - Standard: <urlset><url><loc>...
    - Sitemapindex: <sitemapindex><sitemap><loc>...
    """
    url_locs: list[str] = []
    try:
        root = _DET.fromstring(xml_bytes)
    except Exception:
        return url_locs
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag in ('url', 'URL'):
            loc = _find_child_text(elem, ('loc', 'Loc', 'location'))
            if loc:
                url_locs.append(loc)
        elif tag in ('sitemap', 'Sitemap'):
            loc = _find_child_text(elem, ('loc', 'Loc', 'URL', 'url'))
            if loc:
                url_locs.append(loc)
    return url_locs

def _find_child_text(parent, tags: tuple[str, ...]) -> str | None:
    """Find first child element with any of the given tags (case-insensitive) and return its text."""
    tags_lower = tuple((t.lower() for t in tags))
    for child in parent:
        tag = _strip_ns(child.tag)
        if tag.lower() in tags_lower:
            text = child.text
            if text:
                return text.strip()
    return None

def _build_hit(query: str, url: str, snippet: str, retrieved_ts: float, rank: int) -> DiscoveryHit:
    """Build a DiscoveryHit from an archived URL."""
    return DiscoveryHit(query=query, title=f'Archived: {url[:80]}', url=url, snippet=snippet[:500] if snippet else 'Archived page from Wayback Machine', source=_SOURCE_NAME, rank=rank, retrieved_ts=retrieved_ts, score=0.55, reason='wayback_sitemap')

async def async_search_wayback_sitemap(domain_or_url: str, max_results: int=50, timeout_s: float=_DEFAULT_TIMEOUT_S) -> DiscoveryBatchResult:
    """
    Search Wayback Machine Sitemap archive for a domain/URL.

    Krok 1: Fetch https://web.archive.org/list/google-sitemaps/{domain}
    Krok 2: Parse sitemapindex XML → seznam sitemap URLs
    Krok 3: Pro každý sitemap fetch + parse → URL entries
    Krok 4: Vrátí DiscoveryBatchResult s archivovanými URL

    Args:
        domain_or_url: Domain name (example.com) or URL to query.
                       If URL passed, domain is extracted automatically.
        max_results: Max hits to return (default 50, hard cap 1000).
        timeout_s: Total HTTP timeout in seconds (default 30.0).

    Returns:
        DiscoveryBatchResult with Wayback Machine archived URLs.

    Fail-soft: returns empty hits on any error.
    """
    try:
        max_results = max(1, min(int(max_results), _HARD_MAX_HITS))
    except (TypeError, ValueError):
        max_results = 50
    raw_input = domain_or_url.strip() if domain_or_url else ''
    if not raw_input:
        return DiscoveryBatchResult(hits=(), error='empty_query', error_type='validation')
    domain = _extract_domain(raw_input)
    if not domain:
        return DiscoveryBatchResult(hits=(), error='invalid_domain', error_type='validation', provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family='archive')
    start = time.monotonic()
    sitemapindex_url = _WAYBACK_LIST_URL.format(domain=urllib.parse.quote(domain))
    fetch_timeout = min(timeout_s, 10.0)
    try:
        result = await safe_wait_for(async_fetch_public_text(sitemapindex_url, timeout_s=fetch_timeout, max_bytes=2 * 1024 * 1024), timeout=fetch_timeout + 2.0, label='wayback_sitemapindex_fetch')
    except TimeoutError:
        elapsed = time.monotonic() - start
        return _make_empty_result(elapsed, 'sitemapindex_timeout')
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.debug(f'[wayback_sitemap] Failed to fetch sitemapindex: {e}')
        return _make_empty_result(elapsed, f'sitemapindex_fetch_error:{e}')
    if result.status_code != 200 or not result.text:
        elapsed = time.monotonic() - start
        err_msg = result.error or f'http_{result.status_code}'
        return _make_empty_result(elapsed, err_msg)
    try:
        sitemap_bytes = result.text.encode('utf-8')
    except Exception:
        elapsed = time.monotonic() - start
        return _make_empty_result(elapsed, 'sitemapindex_encoding_error')
    sitemap_urls = _parse_sitemapindex_xml(sitemap_bytes)
    if not sitemap_urls:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(hits=(), provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family='archive', elapsed_s=elapsed, error_type='provider_empty')
    sitemap_urls = sitemap_urls[:_MAX_SITEMAPS]
    remaining_timeout = timeout_s - (time.monotonic() - start)
    per_sitemap_timeout = min(_PER_SITEMAP_TIMEOUT_S, remaining_timeout / len(sitemap_urls))
    per_sitemap_timeout = max(1.0, per_sitemap_timeout)
    from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
    semaphore = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

    async def fetch_sitemap(url: str) -> tuple[str, list[str]]:
        """Fetch and parse a single sitemap."""
        async with semaphore:
            try:
                sm_result = await safe_wait_for(async_fetch_public_text(url, timeout_s=per_sitemap_timeout, max_bytes=2 * 1024 * 1024), timeout=per_sitemap_timeout + 2.0, label='wayback_sitemap_fetch')
                if sm_result.status_code == 200 and sm_result.text:
                    sm_bytes = sm_result.text.encode('utf-8')
                    return (url, _parse_sitemap_xml(sm_bytes)[:_MAX_URLS_PER_SITEMAP])
            except Exception:  # noqa: BLE001
                pass
            return (url, [])
    fetch_tasks = [fetch_sitemap(sm_url) for sm_url in sitemap_urls]
    try:
        sitemap_results = await safe_wait_for(parallel_ok(*fetch_tasks, label='wayback_sitemap'), timeout=timeout_s, label='wayback_sitemap_gather')
    except TimeoutError:
        elapsed = time.monotonic() - start
        return _make_empty_result(elapsed, 'sitemap_fetch_timeout')
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.debug(f'[wayback_sitemap] Sitemap gather error: {e}')
        return _make_empty_result(elapsed, f'sitemap_gather_error:{e}')
    elapsed = time.monotonic() - start
    seen_urls: set[str] = set()
    hits_list: list[DiscoveryHit] = []
    now_ts = time.time()
    for task_result in sitemap_results:
        if isinstance(task_result, Exception):
            continue
        if not isinstance(task_result, tuple):
            continue
        try:
            _, urls = task_result
        except ValueError:
            continue
        for url in urls:
            if url in seen_urls:
                continue
            if not url:
                continue
            snapshot_url = _build_wayback_url(url)
            snippet = f'Sitemap archived: {url}'
            hit = _build_hit(query=raw_input, url=snapshot_url, snippet=snippet, retrieved_ts=now_ts, rank=len(hits_list))
            hits_list.append(hit)
            seen_urls.add(url)
            if len(hits_list) >= max_results:
                break
        if len(hits_list) >= max_results:
            break
    elapsed = time.monotonic() - start
    return DiscoveryBatchResult(hits=tuple(hits_list), provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family='archive', elapsed_s=elapsed, error_type='none' if hits_list else 'provider_empty')

def _extract_domain(raw: str) -> str | None:
    """Extract domain from URL or return the string if it looks like a domain."""
    raw = raw.strip()
    if not raw:
        return None
    if '/' not in raw and '.' in raw:
        if raw.startswith(('http://', 'https://')):
            parsed = urllib.parse.urlparse(raw)
            return parsed.netloc
        return raw
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.netloc:
            return parsed.netloc
    except Exception:  # noqa: BLE001
        pass
    return None

def _build_wayback_url(original_url: str) -> str:
    """
    Build a Wayback Machine URL for an archived page.

    Returns the base Wayback Machine redirect URL.
    """
    encoded_url = urllib.parse.quote(original_url, safe='')
    return f'https://web.archive.org/web/1/{encoded_url}'

def _make_empty_result(elapsed_s: float, err: str) -> DiscoveryBatchResult:
    """Factory for empty error results."""
    return DiscoveryBatchResult(hits=(), provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family='archive', elapsed_s=elapsed_s, error_type='provider_error', error=err)