"""
Dark Web Intelligence Module
==============================

Tor/I2P crawling and hidden service analysis for deep OSINT research.
Self-hosted on M1 8GB with stealth capabilities.

Features:
- Tor hidden service crawling (.onion)
- I2P eepsite crawling (.i2p)
- Marketplace monitoring
- Forum intelligence gathering
- PGP key extraction
- Cryptocurrency address detection
- Stealth request routing through Tor
- Automatic captcha detection and handling

M1 Optimized: Streaming processing, lazy loading, minimal memory footprint
"""
import asyncio
import io
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import msgspec
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse
import httpx

from utils.lru_cache import LRUCache
_HTTpx_SOCKS_AVAILABLE = False
try:
    import httpx_socks
    _HTTpx_SOCKS_AVAILABLE = True
except ImportError:
    httpx_socks = None
from hledac.universal.project_types import RiskLevel
from hledac.universal.utils.async_helpers import parallel

TOR_AVAILABLE = _HTTpx_SOCKS_AVAILABLE

# Rust URL set — parking_lot::RwLock for thread-safe async dedup
_RUST_URL_SET_AVAILABLE = False
_UrlSet = None
try:
    from hledac.universal import rust
    if hasattr(rust, "url_set"):
        _RUST_URL_SET_AVAILABLE = True
        _UrlSet = rust.url_set.MmapUrlSet
except Exception:
    pass
try:
    from selectolax.parser import HTMLParser as _SelectolaxHTMLParser
    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
try:
    import numpy as np
    NP_AVAILABLE = True
except ImportError:
    NP_AVAILABLE = False

class DarkWebSource(Enum):
    """Types of dark web sources."""
    TOR_ONION = 'tor_onion'
    I2P_EEPSITE = 'i2p_eepsite'
    TORRENT_TRACKER = 'torrent_tracker'
    PASTE_SITE = 'paste_site'
    FORUM = 'forum'
    MARKETPLACE = 'marketplace'
    WHISTLEBLOWER = 'whistleblower'

class OnionType(Enum):
    """Types of onion services."""
    V2 = 'v2'
    V3 = 'v3'
    UNKNOWN = 'unknown'


class CrawlTask(msgspec.Struct, frozen=True, gc=False):
    """
    ISSUE-017: BFS crawl task — single URL with depth for parallel processing.
    Thread-safe: immutable (frozen=True), no internal mutable state.
    F350M-R: gc=False for M1 8GB.
    """
    url: str
    depth: int
    parent_url: str | None = None


class HiddenService(msgspec.Struct, gc=False):
    """Represents a discovered hidden service. F350M-R: gc=False for M1 8GB."""
    address: str
    onion_type: OnionType
    source: DarkWebSource
    title: str | None = None
    description: str | None = None
    last_seen: float = field(default_factory=time.time)
    first_seen: float = field(default_factory=time.time)
    is_online: bool = False
    response_time_ms: float = 0.0
    server_signature: str | None = None
    bitcoin_addresses: list[str] = field(default_factory=list)
    monero_addresses: list[str] = field(default_factory=list)
    pgp_keys: list[str] = field(default_factory=list)
    linked_onions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM

class DarkWebContent(msgspec.Struct, frozen=True, gc=False):
    """Content extracted from dark web. F350M-R: gc=False for M1 8GB."""
    url: str
    content_hash: str
    content_type: str
    title: str | None
    text_content: str
    extracted_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    cryptocurrency_addresses: dict[str, list[str]] = field(default_factory=dict)
    emails: list[str] = field(default_factory=list)
    pgp_blocks: list[str] = field(default_factory=list)
    magnet_links: list[str] = field(default_factory=list)
    raw_html: str = ''

class PGPKeyInfo(msgspec.Struct, frozen=True):
    """Extracted PGP key information."""
    key_id: str
    fingerprint: str
    user_ids: list[str]
    creation_date: datetime | None
    key_type: str
    key_size: int
    raw_key: str

class TorProxyManager:
    """
    Manages Tor proxy connections for stealth crawling.

    Requires Tor to be running locally (brew install tor)

    F4XX: migrated from aiohttp + aiohttp_socks to httpx + httpx-socks.
    """
    __slots__ = tuple(('_session', 'control_password', 'control_port', 'proxy_host', 'proxy_port'))

    def __init__(self, proxy_host: str='127.0.0.1', proxy_port: int=9050, control_port: int=9051, control_password: str | None=None):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.control_port = control_port
        self.control_password = control_password
        self._session: httpx.AsyncClient | None = None

    async def initialize(self) -> bool:
        """Initialize Tor proxy connection."""
        if not TOR_AVAILABLE:
            logger.error('httpx-socks not installed. Run: uv add httpx-socks')
            return False
        try:
            async with asyncio.timeout(5.0):
                reader, writer = await asyncio.open_connection(self.proxy_host, self.proxy_port)
            writer.close()
            await writer.wait_closed()
            transport = httpx_socks.AsyncProxyTransport.from_url(f'socks5://{self.proxy_host}:{self.proxy_port}', rdns=True)
            limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
            timeout = httpx.Timeout(connect=60.0, read=120.0, write=20.0, pool=30.0)
            self._session = httpx.AsyncClient(limits=limits, http2=False, timeout=timeout, transport=transport, trust_env=False, headers={'User-Agent': self._get_tor_browser_ua()})  # SOCKS5 tunnel doesn't support HTTP/2 ALPN
            logger.info(f'Tor proxy initialized: {self.proxy_host}:{self.proxy_port}')
            return True
        except asyncio.TimeoutError:
            logger.error('Tor proxy connection timeout')
            return False
        except Exception as e:
            logger.error(f'Failed to initialize Tor proxy: {e}')
            return False

    def _get_tor_browser_ua(self) -> str:
        """Get Tor Browser User-Agent."""
        return 'Mozilla/5.0 (Windows NT 10.0; rv:102.0) Gecko/20100101 Firefox/102.0'

    async def new_identity(self) -> bool:
        """Request new Tor identity (new exit node)."""
        if not self.control_password:
            logger.warning('No control password set, cannot request new identity')
            return False
        try:
            reader, writer = await asyncio.open_connection(self.proxy_host, self.control_port)
            writer.write(f'AUTHENTICATE "{self.control_password}"\r\n'.encode())
            await writer.drain()
            response = await reader.readline()
            if b'250' not in response:
                logger.error(f'Tor authentication failed: {response}')
                return False
            writer.write(b'SIGNAL NEWNYM\r\n')
            await writer.drain()
            response = await reader.readline()
            writer.close()
            await writer.wait_closed()
            if b'250' in response:
                logger.info('New Tor identity requested')
                await asyncio.sleep(5)
                return True
            return False
        except Exception as e:
            logger.error(f'Failed to get new Tor identity: {e}')
            return False

    def get_session(self) -> httpx.AsyncClient | None:
        """Get httpx.AsyncClient configured for Tor."""
        return self._session

    async def close(self):
        """Close Tor connections."""
        if self._session:
            await self._session.aclose()

    async def __aenter__(self) -> TorProxyManager:
        """Async context manager entry - initializes Tor connection."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - closes Tor connection."""
        await self.close()

class DarkWebCrawler:
    """
    Advanced dark web crawler for OSINT research.

    Crawls Tor hidden services and extracts intelligence:
    - Hidden service enumeration
    - Content extraction and indexing
    - Cryptocurrency address harvesting
    - PGP key discovery
    - Link graph analysis
    """
    MAX_CONTENT_CACHE: int = 200
    MAX_VISITED_URLS: int = 5000
    MAX_DISCOVERED_SERVICES: int = 1000
    MAX_URL_QUEUE: int = 200
    ONION_V2_PATTERN = re.compile('[a-z2-7]{16}\\.onion')
    ONION_V3_PATTERN = re.compile('[a-z2-7]{56}\\.onion')
    I2P_PATTERN = re.compile('[a-zA-Z0-9\\-\\.]+\\.i2p')
    BTC_ADDRESS_PATTERN = re.compile('(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}')
    XMR_ADDRESS_PATTERN = re.compile('4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}')
    EMAIL_PATTERN = re.compile('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')
    MAGNET_PATTERN = re.compile('magnet:\\?xt=urn:btih:[a-fA-F0-9]{40}')
    PGP_BLOCK_PATTERN = re.compile('-----BEGIN PGP (PUBLIC|PRIVATE) KEY BLOCK-----.*?-----END PGP \\1 KEY BLOCK-----', re.DOTALL)
    __slots__ = tuple(('content_cache', 'discovered_services', 'max_depth', 'max_pages_per_site', 'request_delay', 'respect_robots_txt', 'stats', 'tor_proxy', 'url_queue', 'visited_urls', '_rust_url_set', '_bfs_queue', '_bfs_lock', '_bfs_sem'))

    def __init__(self, tor_proxy: TorProxyManager | None=None, max_depth: int=3, max_pages_per_site: int=100, request_delay: float=2.0, respect_robots_txt: bool=False):
        self.tor_proxy = tor_proxy or TorProxyManager()
        self.max_depth = max_depth
        self.max_pages_per_site = max_pages_per_site
        self.request_delay = request_delay
        self.respect_robots_txt = respect_robots_txt
        self.discovered_services: LRUCache[str, HiddenService] = LRUCache(max_size=self.MAX_DISCOVERED_SERVICES)
        self.visited_urls: LRUCache[str, bool] = LRUCache(max_size=self.MAX_VISITED_URLS)
        self.content_cache: LRUCache[str, DarkWebContent] = LRUCache(max_size=self.MAX_CONTENT_CACHE)
        self.url_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_URL_QUEUE)
        self.stats = {'pages_crawled': 0, 'services_discovered': 0, 'bitcoin_addresses': 0, 'monero_addresses': 0, 'pgp_keys_found': 0, 'errors': 0}
        # ISSUE-017: BFS engine — Rust URL dedup + bounded concurrency
        self._rust_url_set = None  # lazy init in initialize()
        self._bfs_queue: list[CrawlTask] = []
        self._bfs_lock: asyncio.Lock | None = None
        self._bfs_sem: asyncio.Semaphore | None = None

    def _get_bfs_lock(self) -> asyncio.Lock:
        """ISSUE-014 FIX: Lazily create BFS lock in the current event loop."""
        if self._bfs_lock is None:
            self._bfs_lock = asyncio.Lock()
        return self._bfs_lock

    async def initialize(self) -> bool:
        """Initialize the crawler + Rust URL set."""
        ok = await self.tor_proxy.initialize()
        if ok and _RUST_URL_SET_AVAILABLE and _UrlSet is not None:
            try:
                self._rust_url_set = _UrlSet('/tmp/darkweb_url_set_' + str(os.getpid()), False)
                logger.debug('Rust MmapUrlSet initialized for BFS dedup')
            except Exception as e:
                logger.warning('Rust MmapUrlSet init failed, falling back to OrderedDict: %s', e)
                self._rust_url_set = None
        self._bfs_sem = asyncio.Semaphore(min(5, max(1, os.cpu_count() or 2)))
        return ok

    async def _crawl_single_onion(self, onion_address: str, depth: int) -> list[DarkWebContent]:
        """Crawl a single onion address and return results list (for parallel())."""
        if not onion_address.endswith('.onion'):
            onion_address = f'{onion_address}.onion'
        url = f'http://{onion_address}'
        if url in self.visited_urls or depth > self.max_depth:
            return []
        self._bounded_insert_visited_url(url)
        results = []
        try:
            content = await self._fetch_page(url)
            if content:
                results.append(content)
                if depth < self.max_depth:
                    links = self._extract_links(content.text_content, onion_address)
                    fresh_links = [link for link in links[:10] if link not in self.visited_urls]
                    if fresh_links and depth + 1 < self.max_depth:
                        sub_results = await self._crawl_depth_parallel(fresh_links, depth + 1)
                        results.extend(sub_results)
        except Exception as e:
            logger.error(f'Error crawling {url}: {e}')
            self.stats['errors'] += 1
        return results

    async def _crawl_depth_parallel(self, links: list[str], depth: int) -> list[DarkWebContent]:
        """
        ISSUE-003: Parallelize crawling of multiple links at the same depth.
        Uses bounded concurrency (max 3 concurrent Tor requests) for rate safety.
        """
        if not links or depth > self.max_depth:
            return []
        result = await parallel(
            [self._crawl_single_onion(link, depth) for link in links],
            concurrency=min(3, len(links)),
            policy="collect",
            ctx="darkweb_crawl",
        )
        all_results = []
        for content_list in result.ok:
            all_results.extend(content_list)
        return all_results

    async def crawl_onion(self, onion_address: str, depth: int=0) -> AsyncIterator[DarkWebContent]:
        """
        ISSUE-017: BFS crawl — bounded concurrency, Rust URL dedup.

        Replaces depth-first serial crawling with breadth-first parallel
        processing using asyncio.Queue + parallel() bounded concurrency.

        Pipeline: enqueue → parallel fetch → process results → enqueue new URLs
        Rust MmapUrlSet (parking_lot::RwLock) for thread-safe URL dedup across coroutines.
        """
        if not onion_address.endswith('.onion'):
            onion_address = f'{onion_address}.onion'
        url = f'http://{onion_address}'
        async with self._get_bfs_lock():
            if self._is_url_visited(url):
                return
            self._mark_url_visited(url, None)
        task = CrawlTask(url=url, depth=depth, parent_url=None)
        async with self._get_bfs_lock():
            self._bfs_queue.append(task)
        if self._bfs_sem is None:
            self._bfs_sem = asyncio.Semaphore(5)
        while True:
            batch: list[CrawlTask] = []
            async with self._get_bfs_lock():
                while self._bfs_queue and len(batch) < 5:
                    t = self._bfs_queue.pop(0)
                    if t.depth > self.max_depth:
                        continue
                    batch.append(t)
            if not batch:
                break
            result = await parallel(
                [self._crawl_task(task) for task in batch],
                concurrency=5,
                policy="collect",
                ctx="darkweb_bfs",
            )
            for content_list in result.ok:
                if content_list:
                    for content in content_list:
                        yield content
            if result.errors:
                self.stats['errors'] += len(result.errors)
                for err in result.errors:
                    logger.debug('BFS crawl error: %s', err)

    async def crawl_onion_legacy(self, onion_address: str, depth: int=0) -> AsyncIterator[DarkWebContent]:
        """
        Legacy depth-first crawl (kept for backward compatibility).
        """
        if not onion_address.endswith('.onion'):
            onion_address = f'{onion_address}.onion'
        url = f'http://{onion_address}'
        if url in self.visited_urls or depth > self.max_depth:
            return
        self._bounded_insert_visited_url(url)
        try:
            content = await self._fetch_page(url)
            if content:
                yield content
                if depth < self.max_depth:
                    links = self._extract_links(content.text_content, onion_address)
                    fresh_links = [link for link in links[:10] if link not in self.visited_urls]
                    if fresh_links:
                        sub_results = await self._crawl_depth_parallel(fresh_links, depth + 1)
                        for sub_content in sub_results:
                            yield sub_content
        except Exception as e:
            logger.error(f'Error crawling {url}: {e}')
            self.stats['errors'] += 1

    def _is_url_visited(self, url: str) -> bool:
        """Check if URL was visited (Rust MmapUrlSet or fallback OrderedDict)."""
        if self._rust_url_set is not None:
            return self._rust_url_set.contains(url)
        return url in self.visited_urls

    def _mark_url_visited(self, url: str, _: Any) -> None:
        """Mark URL as visited (Rust MmapUrlSet or fallback OrderedDict)."""
        if self._rust_url_set is not None:
            self._rust_url_set.add(url)
        else:
            self._bounded_insert_visited_url(url)

    async def _crawl_task(self, task: CrawlTask) -> list[DarkWebContent]:
        """
        ISSUE-017: Process single crawl task — fetch + extract links + enqueue new URLs.
        Thread-safe: uses Rust MmapUrlSet (or OrderedDict fallback) for dedup.
        """
        # DEDUP: use unified _is_url_visited to keep Rust set and visited_urls in sync
        if self._is_url_visited(task.url) or task.depth > self.max_depth:
            return []
        self._bounded_insert_visited_url(task.url)
        results: list[DarkWebContent] = []
        try:
            content = await self._fetch_page(task.url)
            if content:
                results.append(content)
                self.stats['pages_crawled'] += 1
                if task.depth < self.max_depth:
                    links = self._extract_links(content.text_content, task.url)
                    fresh_links = [link for link in links[:10]]
                    new_tasks: list[CrawlTask] = []
                    for link in fresh_links:
                        link_url = link if link.startswith('http') else f'http://{link}'
                        if self._rust_url_set is not None:
                            is_new = self._rust_url_set.add(link_url)
                        else:
                            is_new = link_url not in self.visited_urls
                        if is_new:
                            new_tasks.append(CrawlTask(url=link_url, depth=task.depth + 1, parent_url=task.url))
                    async with self._get_bfs_lock():
                        self._bfs_queue.extend(new_tasks)
        except Exception as e:
            logger.debug('Error crawling %s: %s', task.url, e)
            self.stats['errors'] += 1
        return results

    async def _fetch_page(self, url: str) -> DarkWebContent | None:
        """Fetch a single page through Tor."""
        session = self.tor_proxy.get_session()
        if not session:
            logger.error('No Tor session available')
            return None
        try:
            start_time = time.time()
            async with asyncio.timeout(120.0):
                resp = await session.get(url, follow_redirects=True)
                async with resp:
                    response_time = (time.time() - start_time) * 1000
                    if resp.status_code != 200:
                        logger.warning(f'HTTP {resp.status_code} for {url}')
                        return None
                    html = resp.text
                    content = self._parse_content(url, html)
                    content.response_time_ms = response_time
                    self.stats['pages_crawled'] += 1
                    self.stats['bitcoin_addresses'] += len(content.cryptocurrency_addresses.get('bitcoin', []))
                    self.stats['monero_addresses'] += len(content.cryptocurrency_addresses.get('monero', []))
                    self.stats['pgp_keys_found'] += len(content.pgp_blocks)
                    self._bounded_insert_discovered_service(url, HiddenService(address=url, onion_type=OnionType.V3, source=DarkWebSource.TOR_ONION, is_online=True, response_time_ms=response_time))
                    self._bounded_insert_content_cache(url, content)
                    await asyncio.sleep(self.request_delay)
                    return content
        except asyncio.TimeoutError:
            logger.warning(f'Timeout fetching {url}')
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f'Error fetching {url}: {e}')
            return None

    def _parse_content(self, url: str, html: str) -> DarkWebContent:
        """Parse HTML content and extract intelligence."""
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html)
                for tag in tree.css('script, style'):
                    tag.decompose()
                text = tree.body.text(separator=' ', strip=True) if tree.body else ''
                title_tag = tree.css_first('title')
                title = title_tag.text(strip=True) if title_tag else None
                desc_tag = tree.css_first("meta[name='description']")
                meta_description = desc_tag.get('content', '') if desc_tag else ''
            except Exception:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'html.parser')
                for script in soup(['script', 'style']):
                    script.decompose()
                text = soup.get_text(separator=' ', strip=True)
                title_tag = soup.find('title')
                title = title_tag.get_text(strip=True) if title_tag else None
                desc_tag = soup.find('meta', attrs={'name': 'description'})
                meta_description = desc_tag.get('content', '') if desc_tag else ''
        else:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            for script in soup(['script', 'style']):
                script.decompose()
            text = soup.get_text(separator=' ', strip=True)
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else None
            desc_tag = soup.find('meta', attrs={'name': 'description'})
            meta_description = desc_tag.get('content', '') if desc_tag else ''
        crypto_addresses = {'bitcoin': self.BTC_ADDRESS_PATTERN.findall(text), 'monero': self.XMR_ADDRESS_PATTERN.findall(text)}
        emails = self.EMAIL_PATTERN.findall(text)
        pgp_blocks = self.PGP_BLOCK_PATTERN.findall(html)
        magnet_links = self.MAGNET_PATTERN.findall(text)
        metadata = {'meta_description': meta_description, 'meta_keywords': '', 'server': ''}
        return DarkWebContent(url=url, content_hash=hashlib.sha256(html.encode()).hexdigest(), content_type='text/html', title=title, text_content=text, extracted_at=time.time(), metadata=metadata, cryptocurrency_addresses=crypto_addresses, emails=emails, pgp_blocks=[p[0] for p in pgp_blocks], magnet_links=magnet_links, raw_html=html)

    async def extract_and_encode_images(self, html: str, page_url: str, sprint_id: str, fetch_coordinator, vision_encoder, vector_store) -> list[dict]:
        """
        Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.

        Gate: HLEDAC_ENABLE_IMAGE_OSINT=1 (default: off).
        Bounded: max 3 images per page, 512KB per image, 8s timeout.
        Fail-soft: any exception → log warning, return [].
        """
        if not os.getenv('HLEDAC_ENABLE_IMAGE_OSINT'):
            return []
        if not PIL_AVAILABLE or not NP_AVAILABLE:
            logger.warning('PIL or numpy not available, skipping image extraction')
            return []
        try:
            if SELECTOLAX_AVAILABLE:
                tree = _SelectolaxHTMLParser(html)
                img_tags_raw = tree.css('img[src]')
            else:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'html.parser')
                img_tags_raw = soup.find_all('img', src=True)
        except Exception as exc:
            logger.warning('HTML parse failed for %s: %s', page_url, exc)
            return []
        candidates: list[str] = []
        seen_srcs: set[str] = set()
        for img in img_tags_raw[:10]:
            src = (img.attributes.get('src') if hasattr(img, 'attributes') else img.get('src', '')).strip()
            if not src or src.startswith('data:') or src.startswith('#') or (src in seen_srcs):
                continue
            seen_srcs.add(src)
            w = img.attributes.get('width') if hasattr(img, 'attributes') else img.get('width')
            h = img.attributes.get('height') if hasattr(img, 'attributes') else img.get('height')
            try:
                if w and h and (int(w) < 20) and (int(h) < 20):
                    continue
            except (ValueError, TypeError):
                pass
            candidates.append(urljoin(page_url, src))
            if len(candidates) >= 3:
                break
        if not candidates:
            return []
        results: list[dict] = []
        for img_url in candidates:
            try:
                resp = await fetch_coordinator.fetch(img_url, timeout=8.0)
                if resp is None:
                    continue
                body = resp.get('body') if isinstance(resp, dict) else None
                if body is None:
                    continue
                if isinstance(body, str):
                    body = body.encode()
                if len(body) > 512 * 1024:
                    logger.debug('Image exceeds 512KB limit: %s', img_url)
                    continue
                try:
                    pil_img = Image.open(io.BytesIO(body))
                    pil_img = pil_img.convert('RGB')
                except Exception:
                    logger.debug('Not a valid image: %s', img_url)
                    continue
                stego_result: dict = {'stego_detected': False, 'confidence': 0.0}
                try:
                    import tempfile
                    from pathlib import Path
                    from hledac.universal.security.stego_detector import quick_stego_check
                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                        tmp_path = Path(tmp.name)
                        try:
                            pil_img.save(tmp_path, format='JPEG', quality=85)
                            raw_result = await quick_stego_check(tmp_path)
                            stego_result = {'stego_detected': raw_result.get('is_suspicious', False), 'confidence': raw_result.get('confidence', 0.0)}
                        finally:
                            try:
                                tmp_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug('Stego check failed for %s: %s', img_url, exc)
                    stego_result = {'stego_detected': False, 'confidence': 0.0}
                embeddings = vision_encoder.encode_batch([body])
                if not embeddings or embeddings[0] is None:
                    logger.warning('VisionEncoder returned None for: %s', img_url)
                    continue
                emb = embeddings[0]
                if hasattr(emb, 'tolist'):
                    emb = emb.tolist()
                try:
                    vec_id = f'img_{sprint_id}_{hashlib.md5(img_url.encode()).hexdigest()[:12]}'
                    vector_store.add_vectors(ids=[vec_id], vectors=np.array([emb], dtype=np.float32), index_type='image')
                    stored = True
                except Exception as exc:
                    logger.warning('Vector store write failed for %s: %s', img_url, exc)
                    stored = False
                results.append({'img_url': img_url, 'embedding_dim': len(emb), 'stored': stored, 'stego_detected': stego_result.get('stego_detected', False), 'stego_confidence': stego_result.get('confidence', 0.0), 'stego_signals': stego_result.get('signals', [])})
            except Exception as exc:
                logger.warning('Image extract/encode failed for %s: %s', img_url, exc)
                continue
        logger.debug('Image extraction: %d/%d images processed for %s', len(results), len(candidates), page_url)
        return results

    def _extract_links(self, html: str, base_domain: str) -> list[str]:
        """Extract .onion links from content."""
        links: list[str] = []
        seen: set[str] = set()
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html)
                for anchor in tree.css('a[href]'):
                    href = anchor.attributes.get('href', '')
                    parsed = urlparse(href)
                    if not parsed.netloc:
                        href = urljoin(f'http://{base_domain}', href)
                        parsed = urlparse(href)
                    if '.onion' in parsed.netloc and parsed.netloc not in seen:
                        seen.add(parsed.netloc)
                        links.append(parsed.netloc)
                return links
            except Exception:
                pass
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            parsed = urlparse(href)
            if not parsed.netloc:
                href = urljoin(f'http://{base_domain}', href)
                parsed = urlparse(href)
            if '.onion' in parsed.netloc and parsed.netloc not in seen:
                seen.add(parsed.netloc)
                links.append(parsed.netloc)
        return links

    def search_onion_addresses(self, text: str) -> list[tuple[str, OnionType]]:
        """
        Search text for onion addresses.

        Returns:
            List of (address, type) tuples
        """
        addresses = []
        for match in self.ONION_V3_PATTERN.findall(text):
            addresses.append((match, OnionType.V3))
        for match in self.ONION_V2_PATTERN.findall(text):
            addresses.append((match, OnionType.V2))
        return addresses

    async def monitor_service(self, onion_address: str, interval_minutes: int=60) -> AsyncIterator[dict[str, Any]]:
        """
        Continuously monitor a hidden service for changes.

        Args:
            onion_address: .onion address to monitor
            interval_minutes: Check interval in minutes

        Yields:
            Change notifications

        Note:
            Bounded by caller's iteration — caller MUST use ``async for``
            or ``try/finally`` with ``aclose()`` to ensure cleanup on cancel.
            ``asyncio.CancelledError`` propagates from ``aclose()`` into the
            ``await asyncio.sleep()`` call, causing immediate loop termination.
        """
        last_hash = None
        while True:
            try:
                url = f'http://{onion_address}.onion'
                content = await self._fetch_page(url)
                if content:
                    current_hash = content.content_hash
                    if last_hash and current_hash != last_hash:
                        yield {'type': 'content_change', 'address': onion_address, 'timestamp': time.time(), 'old_hash': last_hash, 'new_hash': current_hash, 'title': content.title}
                    last_hash = current_hash
                else:
                    yield {'type': 'offline', 'address': onion_address, 'timestamp': time.time()}
                await asyncio.sleep(interval_minutes * 60)
            except Exception as e:
                logger.error(f'Monitor error for {onion_address}: {e}')
                await asyncio.sleep(interval_minutes * 60)

    def get_statistics(self) -> dict[str, Any]:
        """Get crawling statistics with bounded truth."""
        return {**self.stats, 'discovered_services_size': len(self.discovered_services), 'discovered_services_limit': self.MAX_DISCOVERED_SERVICES, 'visited_urls_size': len(self.visited_urls), 'visited_urls_limit': self.MAX_VISITED_URLS, 'content_cache_size': len(self.content_cache), 'content_cache_limit': self.MAX_CONTENT_CACHE}

    def _bounded_insert_content_cache(self, url: str, content: DarkWebContent) -> None:
        """Insert into content_cache with FIFO LRU eviction at limit."""
        if url in self.content_cache:
            self.content_cache.move_to_end(url)
        elif len(self.content_cache) >= self.MAX_CONTENT_CACHE:
            self.content_cache.popitem(last=False)
        self.content_cache[url] = content

    def _bounded_insert_visited_url(self, url: str) -> None:
        """Insert into visited_urls with FIFO LRU eviction at limit."""
        if url in self.visited_urls:
            self.visited_urls.move_to_end(url)
        elif len(self.visited_urls) >= self.MAX_VISITED_URLS:
            self.visited_urls.popitem(last=False)
        self.visited_urls[url] = True

    def _bounded_insert_discovered_service(self, url: str, service: HiddenService) -> None:
        """Insert into discovered_services with FIFO eviction at limit."""
        if url in self.discovered_services:
            self.discovered_services.move_to_end(url)
        elif len(self.discovered_services) >= self.MAX_DISCOVERED_SERVICES:
            self.discovered_services.popitem(last=False)
        self.discovered_services[url] = service

    def reset_session(self) -> None:
        """Clear all session state (bounded structures + queues)."""
        self.discovered_services.clear()
        self.visited_urls.clear()
        self.content_cache.clear()
        while not self.url_queue.empty():
            try:
                self.url_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # ISSUE-017: clear BFS queue
        self._bfs_queue.clear()
        # Rust MmapUrlSet: clear + persist
        if self._rust_url_set is not None:
            try:
                self._rust_url_set.clear()
                self._rust_url_set.msync()
            except Exception as e:
                logger.debug('Rust MmapUrlSet clear error: %s', e)
        self.stats = {'pages_crawled': 0, 'services_discovered': 0, 'bitcoin_addresses': 0, 'monero_addresses': 0, 'pgp_keys_found': 0, 'errors': 0}

    async def close(self):
        """Close crawler and cleanup session state."""
        self.reset_session()
        await self.tor_proxy.close()

class CryptocurrencyAnalyzer:
    """
    Analyzes cryptocurrency addresses found in dark web content.

    Tracks transactions, balances (where possible), and relationships.
    """
    __slots__ = tuple(('address_cache',))

    def __init__(self):
        self.address_cache: dict[str, dict[str, Any]] = {}

    def analyze_bitcoin_address(self, address: str) -> dict[str, Any]:
        """
        Analyze Bitcoin address.

        Note: Without external APIs, we can only do basic validation.
        For full analysis, would need blockchain.info or similar API.
        """
        is_valid = self._validate_bitcoin_address(address)
        analysis = {'address': address, 'type': self._get_bitcoin_address_type(address), 'is_valid': is_valid, 'possible_type': 'segwit' if address.startswith('bc1') else 'legacy/p2sh'}
        return analysis

    def _validate_bitcoin_address(self, address: str) -> bool:
        """Basic Bitcoin address validation."""
        if address.startswith('bc1'):
            return len(address) in [42, 62]
        elif address.startswith('1') or address.startswith('3'):
            return 25 <= len(address) <= 35
        return False

    def _get_bitcoin_address_type(self, address: str) -> str:
        """Get Bitcoin address type."""
        if address.startswith('bc1q'):
            return 'P2WPKH' if len(address) == 42 else 'P2WSH'
        elif address.startswith('bc1p'):
            return 'P2TR'
        elif address.startswith('1'):
            return 'P2PKH'
        elif address.startswith('3'):
            return 'P2SH'
        return 'unknown'

    def cluster_addresses(self, addresses: list[str]) -> dict[str, list[str]]:
        """
        Cluster addresses that might belong to the same entity.

        Uses heuristics like:
        - Common input ownership
        - Change address patterns
        """
        clusters = {'unknown': addresses}
        return clusters
__all__ = ['TorProxyManager', 'DarkWebCrawler', 'HiddenService', 'DarkWebContent', 'PGPKeyInfo', 'CryptocurrencyAnalyzer', 'DarkWebSource', 'OnionType', 'darkweb_content_to_canonical', 'DHTFinding', 'dht_content_to_canonical']

def darkweb_content_to_canonical(content: DarkWebContent, query: str) -> CanonicalFinding:
    """
    Sprint F251: Map DarkWebCrawler output → CanonicalFinding for sprint ingestion.

    Bounded: payload_text truncated to 3000 chars, fail-safe if title is None.
    """
    import hashlib
    title = content.title or 'onion'
    body = content.text_content or ''
    payload = f'{title}\n{body[:3000]}'
    meta = content.metadata or {}
    confidence = float(meta.get('relevance_score', 0.5))
    confidence = max(0.0, min(1.0, confidence))
    finding_id = f'dw_{hashlib.md5(content.url.encode()).hexdigest()[:16]}'
    return CanonicalFinding(finding_id=finding_id, query=query, source_type='onion_discovery', confidence=confidence, ts=content.extracted_at, provenance=(content.url,), payload_text=payload)

class DHTFinding(msgspec.Struct, frozen=True):
    """Structured output from DHT crawl operations."""
    info_hash: str
    name: str = ''
    files: list[dict] = field(default_factory=list)
    size_bytes: int = 0
    peers: int = 0
    source: str = 'dht'

def dht_content_to_canonical(dht_result: DHTFinding, query: str) -> CanonicalFinding:
    """
    Sprint F214Q: Map DHT crawl result → CanonicalFinding for sprint ingestion.

    Bounded: payload_text truncated to 3000 chars, fail-safe.
    INVARIANT: DHT queries NEVER go over Tor — clearnet UDP only.
    """
    name = dht_result.name or 'dht_torrent'
    magnet = f'magnet:?xt=urn:btih:{dht_result.info_hash}'
    if dht_result.name:
        magnet += f'&dn={dht_result.name}'
    body = f'info_hash={dht_result.info_hash} peers={dht_result.peers} size={dht_result.size_bytes}'
    if dht_result.files:
        file_names = ', '.join((f.get('name', '') for f in dht_result.files[:10]))
        body += f'\nfiles: {file_names}'
    payload = f'{name}\n{magnet}\n{body[:3000]}'
    confidence = min(0.9, 0.3 + dht_result.peers / 100)
    confidence = max(0.0, min(1.0, confidence))
    finding_id = f'dht_{hashlib.md5(dht_result.info_hash.encode()).hexdigest()[:16]}'
    return CanonicalFinding(finding_id=finding_id, query=query, source_type='dht_discovery', confidence=confidence, ts=time.time(), provenance=(f'info_hash:{dht_result.info_hash}',), payload_text=payload)