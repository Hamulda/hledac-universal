"""
Archive Discovery System
=========================





















From deep_research/advanced_archive_discovery.py:
- Wayback Machine (Internet Archive)
- Archive.today / archive.ph
- IPFS (InterPlanetary File System)
- GitHub Historical
- Memento Protocol

Enhanced with stealth_osint integration:
- Search engine cache (Google, Bing, Yandex)
- Social media archives (Politwoops, Unreddit)
- Content quality assessment
- Metadata extraction

Historical content discovery across multiple archival sources.
"""
import orjson
from zstandard import ZstdCompressor, ZstdDecompressor
_zstd_compressor = ZstdCompressor()
_zstd_decompressor = ZstdDecompressor()

import asyncio
import msgspec
import hashlib
import logging
import msgspec.json as _json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from hledac.universal.utils.msgspec_json import loads as _msgspec_loads
from urllib.parse import quote, urlparse
import httpx
from hledac.universal.transport.session_pool import session_pool
from operator import attrgetter, itemgetter

# G1 FIX: beautifulsoup4 REMOVED — selectolax is primary, regex fallback
SELECTOLAX_AVAILABLE = False
try:
    from selectolax.parser import HTMLParser as _SelectoLAXParser
    SELECTOLAX_AVAILABLE = True
except ImportError:
    _SelectoLAXParser = None
try:
    from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
    from hledac.universal.security.zero_attribution_engine import ZeroAttributionEngine
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
logger = logging.getLogger(__name__)
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024

async def _read_text_with_cap(response: httpx.Response, cap: int=MAX_PAYLOAD_BYTES) -> str:
    """Read response text with payload cap for M1 RAM safety."""
    try:
        content_length = response.headers.get('content-length', '')
        if content_length and int(content_length) > cap:
            logger.warning(f'[Archive] Content-Length {content_length} exceeds cap {cap}, aborting')
            return ''
        body = await response.read()
        if len(body) > cap:
            logger.warning(f'[Archive] Body {len(body)} bytes exceeds cap {cap}, truncating')
            return body[:cap].decode('utf-8', errors='replace')
        return body.decode('utf-8', errors='replace')
    except Exception as e:
        logger.warning(f'[Archive] Failed to read response body: {e}')
        return ''

class ContentSource(Enum):
    """Sources of archived content (from stealth_osint integration)"""
    WAYBACK = 'wayback'
    SEARCH_CACHE = 'search_cache'
    SOCIAL_ARCHIVE = 'social_archive'
    GHOST_ARCHIVE = 'ghost_archive'

class ContentType(Enum):
    """Types of content (from stealth_osint integration)"""
    HTML = 'html'
    PDF = 'pdf'
    IMAGE = 'image'
    VIDEO = 'video'
    TEXT = 'text'
    UNKNOWN = 'unknown'

class Snapshot(msgspec.Struct, gc=False):
    """Web archive snapshot (from stealth_osint integration)"""
    snapshot_id: str
    url: str
    archived_url: str
    timestamp: datetime
    source: ContentSource
    content_type: ContentType
    status_code: int
    content_length: int
    available: bool
    quality_score: float = 0.0

class ResurrectionResult(msgspec.Struct, gc=False):
    """Result of content resurrection (from stealth_osint integration)"""
    request_id: str
    original_url: str
    success: bool
    best_snapshot: Snapshot | None
    all_snapshots: list[Snapshot]
    content: str | None
    title: str | None
    author: str | None
    published_date: datetime | None
    extracted_metadata: dict[str, Any]
    processing_time: float

class ResurrectionRequest(msgspec.Struct, gc=False):
    """Request for content resurrection (from stealth_osint integration)"""
    request_id: str
    url: str
    target_date: datetime | None
    min_quality: float
    extract_metadata: bool
    created_at: datetime

class ArchiveResult(msgspec.Struct, gc=False):
    """Result from archive discovery."""
    url: str
    title: str
    source: str
    timestamp: datetime | None = None
    content: str | None = None
    content_type: str = 'text/html'
    metadata: dict[str, Any] = field(default_factory=dict)
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {'url': self.url, 'title': self.title, 'source': self.source, 'timestamp': self.timestamp.isoformat() if self.timestamp else None, 'content_type': self.content_type, 'metadata': self.metadata, 'available': self.available}

class SnapshotInfo(msgspec.Struct, gc=False):
    """Wayback snapshot information."""
    timestamp: datetime
    url: str
    status: str
    digest: str
    length: int

class CDXSnapshot(msgspec.Struct, gc=False):
    """CDX API snapshot result."""
    timestamp: str
    original_url: str
    status_code: str
    digest: str
    length: str

    @property
    def wayback_url(self) -> str:
        """Get Wayback Machine URL for this snapshot."""
        return f'https://web.archive.org/web/{self.timestamp}/{self.original_url}'

    @property
    def datetime(self) -> datetime | None:
        """Parse timestamp as datetime."""
        try:
            return datetime.strptime(self.timestamp, '%Y%m%d%H%M%S')
        except ValueError:
            return None

class DiscoveredEndpoint(msgspec.Struct, gc=False):
    """Discovered endpoint with metadata."""
    url: str
    title: str | None = None
    confidence_score: float = 0.0
    discovery_method: str = 'unknown'
    file_type: str | None = None
    path: str = ''
    source_url: str | None = None
    tech_stack: dict[str, Any] | None = None
    last_modified: str | None = None
    size_bytes: int | None = None
    archive_source: str | None = None

    def __post_init__(self) -> None:
        if not self.path and self.url:
            parsed = urlparse(self.url)
            self.path = parsed.path

    @property
    def is_archived(self) -> bool:
        return self.archive_source is not None

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc

    def to_dict(self) -> dict[str, Any]:
        return {'url': self.url, 'title': self.title, 'confidence_score': self.confidence_score, 'discovery_method': self.discovery_method, 'archive_source': self.archive_source, 'is_archived': self.is_archived, 'domain': self.domain}

class WaybackMachineClient:
    """Client for Internet Archive Wayback Machine."""
    BASE_URL = 'https://web.archive.org'
    CDX_API = 'https://web.archive.org/cdx/search/cdx'
    __slots__ = tuple(('session', 'timeout'))

    def __init__(self, timeout: float=30.0):
        self.timeout = timeout
        self.session = None

    async def __aenter__(self):
        self.session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_snapshots(self, url: str, from_date: str | None=None, to_date: str | None=None, limit: int=10) -> list[SnapshotInfo]:
        """Get list of snapshots for a URL."""
        if not self.session:
            self.session = await httpx.AsyncClient()
        params = {'url': url, 'output': 'json', 'fl': 'timestamp,original,statuscode,digest,length', 'collapse': 'digest', 'limit': str(limit)}
        if from_date:
            params['from'] = from_date
        if to_date:
            params['to'] = to_date
        try:
            async with self.session.get(self.CDX_API, params=params, timeout=httpx.Timeout(total=self.timeout)) as response:
                if response.status != 200:
                    logger.warning(f'Wayback CDX API returned {response.status}')
                    return []
                data = await response.json()
                snapshots = []
                for row in data[1:]:
                    if len(row) >= 5:
                        timestamp_str = row[0]
                        timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')
                        snapshots.append(SnapshotInfo(timestamp=timestamp, url=row[1], status=row[2], digest=row[3], length=int(row[4]) if row[4].isdigit() else 0))
                return snapshots
        except Exception as e:
            logger.error(f'Wayback snapshots error: {e}')
            return []

    async def get_snapshot_content(self, url: str, timestamp: datetime | None=None) -> ArchiveResult | None:
        """Get content of a specific snapshot."""
        if not self.session:
            self.session = await httpx.AsyncClient()
        try:
            if timestamp:
                ts_str = timestamp.strftime('%Y%m%d%H%M%S')
                archive_url = f'{self.BASE_URL}/web/{ts_str}/{url}'
            else:
                archive_url = f'{self.BASE_URL}/web/{url}'
            async with self.session.get(archive_url, timeout=httpx.Timeout(total=self.timeout), follow_redirects=True) as response:
                if response.status == 200:
                    content = await _read_text_with_cap(response)
                    title = self._extract_title(content) or f'Snapshot of {url}'
                    return ArchiveResult(url=archive_url, title=title, source='wayback', timestamp=timestamp, content=content, content_type=response.headers.get('Content-Type', 'text/html'), metadata={'original_url': url})
                else:
                    logger.warning(f'Wayback content returned {response.status}')
                    return None
        except Exception as e:
            logger.error(f'Wayback content error: {e}')
            return None

    def _extract_title(self, html: str) -> str | None:
        """Extract title from HTML."""
        import re
        match = re.search('<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        return match.group(1).strip() if match else None

class ArchiveTodayClient:
    """Client for Archive.today / archive.ph."""
    BASE_URL = 'https://archive.today'
    __slots__ = tuple(('session', 'timeout'))

    def __init__(self, timeout: float=30.0):
        self.timeout = timeout
        self.session = None

    async def __aenter__(self):
        self.session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None

    async def search(self, url: str) -> list[ArchiveResult]:
        """Search for archived versions on Archive.today."""
        if not self.session:
            self.session = await httpx.AsyncClient()
        try:
            search_url = f'{self.BASE_URL}/search/?q={quote(url)}'
            async with self.session.get(search_url, timeout=httpx.Timeout(total=self.timeout)) as response:
                if response.status == 200:
                    # NEW-MEM-002: Use capped read for archive search (HTML pages, cap for safety)
                    html = await _read_text_with_cap(response)
                    return self._parse_search_results(html, url)
                else:
                    return []
        except Exception as e:
            logger.error(f'Archive.today search error: {e}')
            return []

    def _parse_search_results(self, html: str, original_url: str) -> list[ArchiveResult]:
        """Parse Archive.today search results."""
        import re
        results = []
        pattern = 'href="(https://archive\\.today/[^"]+)"[^>]*>([^<]+)'
        matches = re.findall(pattern, html)
        for archive_url, title in matches[:5]:
            results.append(ArchiveResult(url=archive_url, title=title or f'Archive of {original_url}', source='archive_today', metadata={'original_url': original_url}))
        return results

class IPFSClient:
    """Client for IPFS gateways."""
    GATEWAYS = ['https://ipfs.io/ipfs/', 'https://gateway.ipfs.io/ipfs/', 'https://cloudflare-ipfs.com/ipfs/', 'https://dweb.link/ipfs/']
    __slots__ = tuple(('session', 'timeout'))

    def __init__(self, timeout: float=30.0):
        self.timeout = timeout
        self.session = None

    async def __aenter__(self):
        self.session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None

    async def fetch_content(self, cid: str) -> ArchiveResult | None:
        """Fetch content from IPFS by CID."""
        if not self.session:
            self.session = await httpx.AsyncClient()
        for gateway in self.GATEWAYS:
            try:
                url = f'{gateway}{cid}'
                async with self.session.get(url, timeout=httpx.Timeout(total=self.timeout)) as response:
                    if response.status == 200:
                        content = await _read_text_with_cap(response)
                        return ArchiveResult(url=url, title=f'IPFS: {cid[:20]}...', source='ipfs', content=content, content_type=response.headers.get('Content-Type', 'text/html'), metadata={'cid': cid, 'gateway': gateway})
            except Exception as e:
                logger.debug(f'IPFS gateway {gateway} failed: {e}')
                continue
        return None

class GitHubHistoricalClient:
    """Client for GitHub historical commits."""
    API_BASE = 'https://api.github.com'
    __slots__ = tuple(('session', 'timeout', 'token'))

    def __init__(self, token: str | None=None, timeout: float=30.0):
        self.token = token
        self.timeout = timeout
        self.session = None

    async def __aenter__(self):
        headers = {}
        if self.token:
            headers['Authorization'] = f'token {self.token}'
        self.session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_file_history(self, repo: str, path: str, limit: int=10) -> list[ArchiveResult]:
        """Get historical versions of a file from GitHub."""
        if not self.session:
            await self.__aenter__()
        try:
            url = f'{self.API_BASE}/repos/{repo}/commits'
            params = {'path': path, 'per_page': limit}
            async with self.session.get(url, params=params, timeout=httpx.Timeout(total=self.timeout)) as response:
                if response.status == 200:
                    commits = await response.json()
                    results = []
                    for commit in commits:
                        commit_data = commit.get('commit', {})
                        author_data = commit_data.get('author', {})
                        timestamp_str = author_data.get('date')
                        if timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            timestamp = None
                        results.append(ArchiveResult(url=commit.get('html_url', ''), title=f"{commit_data.get('message', 'No message')[:50]}...", source='github', timestamp=timestamp, metadata={'sha': commit.get('sha'), 'author': author_data.get('name'), 'repo': repo, 'path': path}))
                    return results
                else:
                    logger.warning(f'GitHub API returned {response.status}')
                    return []
        except Exception as e:
            logger.error(f'GitHub history error: {e}')
            return []

class ArchiveDiscovery:
    """
    Main archive discovery orchestrator.

    Combines multiple archival sources for comprehensive
    historical content discovery.
    """
    __slots__ = tuple(('archive_today', 'github', 'ipfs', 'wayback'))

    def __init__(self, wayback_timeout: float=30.0, archive_today_timeout: float=30.0, ipfs_timeout: float=30.0, github_token: str | None=None):
        self.wayback = WaybackMachineClient(wayback_timeout)
        self.archive_today = ArchiveTodayClient(archive_today_timeout)
        self.ipfs = IPFSClient(ipfs_timeout)
        self.github = GitHubHistoricalClient(github_token)

    async def search_url(self, url: str, sources: list[str] | None=None, limit_per_source: int=5) -> dict[str, list[ArchiveResult]]:
        """
        Search for archived versions of a URL.

        Args:
            url: URL to search
            sources: List of sources (wayback, archive_today, etc.)
            limit_per_source: Maximum results per source

        Returns:
            Dictionary of source -> results
        """
        if sources is None:
            sources = ['wayback', 'archive_today']
        results = {}
        if 'wayback' in sources:
            try:
                async with self.wayback:
                    wayback_results = await self.wayback.search(url, limit=limit_per_source)
                    results['wayback'] = wayback_results
            except Exception as e:
                logger.error(f'Wayback search error: {e}')
                results['wayback'] = []
        if 'archive_today' in sources:
            try:
                async with self.archive_today:
                    at_results = await self.archive_today.search(url)
                    results['archive_today'] = at_results
            except Exception as e:
                logger.error(f'Archive.today search error: {e}')
                results['archive_today'] = []
        return results

    async def get_timeline(self, url: str, from_date: datetime | None=None, to_date: datetime | None=None) -> list[ArchiveResult]:
        """Get timeline of changes for a URL."""
        from_date_str = from_date.strftime('%Y%m%d') if from_date else None
        to_date_str = to_date.strftime('%Y%m%d') if to_date else None
        async with self.wayback:
            snapshots = await self.wayback.get_snapshots(url, from_date=from_date_str, to_date=to_date_str, limit=50)
            results = []
            for snapshot in snapshots:
                results.append(ArchiveResult(url=f"https://web.archive.org/web/{snapshot.timestamp.strftime('%Y%m%d%H%M%S')}/{snapshot.url}", title=f"Snapshot from {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}", source='wayback', timestamp=snapshot.timestamp, metadata={'status': snapshot.status, 'digest': snapshot.digest, 'length': snapshot.length}))
            return results

class ArchiveResurrector:
    """
    Advanced web archive content recovery system.

    Features:
    - Wayback Machine CDX API integration
    - Search engine cache checking
    - Social media archive access
    - Content quality assessment
    - Metadata extraction
    - Concurrent processing

    Integrated from stealth_osint for universal orchestrator.
    """
    WAYBACK_CDX_URL = 'https://web.archive.org/cdx/search/cdx'
    WAYBACK_RAW_URL = 'https://web.archive.org/web/{timestamp}id_/{url}'
    SEARCH_ENGINES = {'google': 'https://webcache.googleusercontent.com/search?q=cache:', 'bing': 'https://r.jina.ai/http://', 'yandex': 'https://yandexwebcache.net/yandbtm?url='}
    SOCIAL_ARCHIVES = {'politwoops': 'https://politwoops.com/', 'unreddit': 'https://r.jina.ai/http://reddit.com'}
    ERROR_PATTERNS = ['404\\s*not\\s*found', 'page\\s*not\\s*found', 'site\\s*not\\s*found', "wayback\\s*machine\\s*doesn't\\s*have", 'this\\s*page\\s*is\\s*not\\s*available', 'snapshot\\s*cannot\\s*be\\s*displayed']
    __slots__ = tuple(('_active_requests', '_anonymizer', '_request_history', '_resurrections_attempted', '_resurrections_successful', '_session', '_snapshots_found', '_zero_attribution', 'concurrent_requests', 'max_snapshots', 'min_quality'))

    def __init__(self, min_quality: float=0.5, max_snapshots: int=10, concurrent_requests: int=3):
        self.min_quality = min_quality
        self.max_snapshots = max_snapshots
        self.concurrent_requests = concurrent_requests
        self._anonymizer = None
        self._zero_attribution = None
        self._session = None
        self._active_requests: dict[str, ResurrectionRequest] = {}
        self._request_history: list[ResurrectionRequest] = []
        self._resurrections_attempted = 0
        self._resurrections_successful = 0
        self._snapshots_found = 0
        logger.info('ArchiveResurrector initialized')

    async def initialize(self) -> bool:
        """Initialize security components and HTTP session"""
        try:
            if SECURITY_AVAILABLE:
                try:
                    self._anonymizer = TemporalAnonymizer()
                    self._zero_attribution = ZeroAttributionEngine()
                except Exception as e:
                    logger.warning(f'Security components not available: {e}')
            self._session = await httpx.AsyncClient()
            logger.info('✅ ArchiveResurrector initialized')
            return True
        except Exception as e:
            logger.error(f'❌ Initialization failed: {e}')
            return False

    async def resurrect(self, url: str, target_date: datetime | None=None, min_quality: float | None=None) -> ResurrectionResult:
        """Resurrect content from web archives."""
        min_quality = min_quality or self.min_quality
        self._resurrections_attempted += 1
        request_id = hashlib.sha256(f'{url}:{datetime.now(timezone.utc)}'.encode()).hexdigest()[:16]
        request = ResurrectionRequest(request_id=request_id, url=url, target_date=target_date, min_quality=min_quality, extract_metadata=True, created_at=datetime.now(timezone.utc))
        self._active_requests[request_id] = request
        logger.info(f'🕸️ Resurrecting: {url}')
        start_time = time.monotonic()
        try:
            snapshots = await self._find_snapshots(url, target_date)
            if not snapshots:
                logger.warning(f'No snapshots found for: {url}')
                return ResurrectionResult(request_id=request_id, original_url=url, success=False, best_snapshot=None, all_snapshots=[], content=None, title=None, author=None, published_date=None, extracted_metadata={}, processing_time=time.monotonic() - start_time)
            self._snapshots_found += len(snapshots)
            results = await self._extract_from_snapshots(snapshots)
            successful = [r for r in results if r is not None]
            if not successful:
                logger.warning(f'Could not extract content from any snapshot: {url}')
                return ResurrectionResult(request_id=request_id, original_url=url, success=False, best_snapshot=None, all_snapshots=snapshots, content=None, title=None, author=None, published_date=None, extracted_metadata={}, processing_time=time.monotonic() - start_time)
            best_result = self._select_best_content(successful)
            self._resurrections_successful += 1
            logger.info(f"✅ Resurrected: {url} (snapshots: {len(snapshots)}, best: {best_result['snapshot'].timestamp})")
            self._request_history.append(request)
            del self._active_requests[request_id]
            return ResurrectionResult(request_id=request_id, original_url=url, success=True, best_snapshot=best_result['snapshot'], all_snapshots=snapshots, content=best_result['content'], title=best_result['metadata'].get('title'), author=best_result['metadata'].get('author'), published_date=best_result['metadata'].get('date'), extracted_metadata=best_result['metadata'], processing_time=time.monotonic() - start_time)
        except Exception as e:
            logger.error(f'❌ Resurrection failed: {e}')
            return ResurrectionResult(request_id=request_id, original_url=url, success=False, best_snapshot=None, all_snapshots=[], content=None, title=None, author=None, published_date=None, extracted_metadata={}, processing_time=time.monotonic() - start_time)

    async def _find_snapshots(self, url: str, target_date: datetime | None) -> list[Snapshot]:
        """Find all available snapshots for URL"""
        snapshots = []
        if self._anonymizer:
            await asyncio.sleep(self._anonymizer.get_random_delay())
        wayback_snapshots = await self._check_wayback(url, target_date)
        snapshots.extend(wayback_snapshots)
        cache_snapshots = await self._check_search_cache(url)
        snapshots.extend(cache_snapshots)
        social_snapshots = await self._check_social_archive(url)
        snapshots.extend(social_snapshots)
        snapshots.sort(key=attrgetter("timestamp"), reverse=True)
        return snapshots[:self.max_snapshots]

    async def _check_wayback(self, url: str, target_date: datetime | None) -> list[Snapshot]:
        """Check Wayback Machine CDX API for snapshots"""
        snapshots = []
        try:
            params = {'url': url, 'output': 'json', 'collapse': 'digest', 'fl': 'timestamp,original,mimetype,statuscode,digest,length'}
            if target_date:
                params['from'] = (target_date - timedelta(days=30)).strftime('%Y%m%d')
                params['to'] = (target_date + timedelta(days=30)).strftime('%Y%m%d')
            async with self._session.get(self.WAYBACK_CDX_URL, params=params) as resp:
                if resp.status == 200:
                    # NEW-MEM-002: Use capped read for CDX API (typically small, but cap for safety)
                    data = await _read_text_with_cap(resp, cap=1024 * 1024)  # 1MB cap for CDX
                    lines = data.strip().split('\n')
                    if len(lines) > 1:
                        for line in lines[1:]:
                            try:
                                parts = _json.loads(line)
                                if len(parts) >= 6:
                                    timestamp_str = parts[0]
                                    original_url = parts[1]
                                    mimetype = parts[2]
                                    status = parts[3]
                                    length = parts[5]
                                    timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')
                                    content_type = self._detect_content_type(mimetype)
                                    archived_url = self.WAYBACK_RAW_URL.format(timestamp=timestamp_str, url=original_url)
                                    snapshot = Snapshot(snapshot_id=hashlib.sha256(f'wayback:{timestamp_str}:{url}'.encode()).hexdigest()[:16], url=url, archived_url=archived_url, timestamp=timestamp, source=ContentSource.WAYBACK, content_type=content_type, status_code=int(status) if status else 200, content_length=int(length) if length else 0, available=True)
                                    snapshots.append(snapshot)
                            except Exception as e:
                                logger.debug(f'Failed to parse CDX line: {e}')
                                continue
        except Exception as e:
            logger.debug(f'Wayback check failed: {e}')
        return snapshots

    async def _check_search_cache(self, url: str) -> list[Snapshot]:
        """Check search engine cache for URL"""
        snapshots = []
        for engine, cache_url in self.SEARCH_ENGINES.items():
            try:
                cache_full_url = f'{cache_url}{quote(url)}'
                async with self._session.head(cache_full_url, follow_redirects=True) as resp:
                    if resp.status == 200:
                        snapshot = Snapshot(snapshot_id=hashlib.sha256(f'cache:{engine}:{url}'.encode()).hexdigest()[:16], url=url, archived_url=cache_full_url, timestamp=datetime.now(timezone.utc), source=ContentSource.SEARCH_CACHE, content_type=ContentType.HTML, status_code=200, content_length=0, available=True)
                        snapshots.append(snapshot)
            except Exception as e:
                logger.debug(f'Cache check failed for {engine}: {e}')
        return snapshots

    async def _check_social_archive(self, url: str) -> list[Snapshot]:
        """Check social media archives"""
        snapshots = []
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if any((x in domain for x in ['twitter.com', 'x.com', 't.co'])):
            try:
                tweet_id = self._extract_tweet_id(url)
                if tweet_id:
                    snapshot = Snapshot(snapshot_id=f'politwoops:{tweet_id}', url=url, archived_url=f"{self.SOCIAL_ARCHIVES['politwoops']}{tweet_id}", timestamp=datetime.now(timezone.utc), source=ContentSource.SOCIAL_ARCHIVE, content_type=ContentType.HTML, status_code=200, content_length=0, available=True)
                    snapshots.append(snapshot)
            except Exception as e:
                logger.debug(f'Politwoops check failed: {e}')
        return snapshots

    def _extract_tweet_id(self, url: str) -> str | None:
        """Extract tweet ID from Twitter/X URL"""
        patterns = ['twitter\\.com/\\w+/status/(\\d+)', 'x\\.com/\\w+/status/(\\d+)', 't\\.co/(\\w+)']
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _detect_content_type(self, mimetype: str) -> ContentType:
        """Detect content type from MIME type"""
        mimetype = mimetype.lower()
        if 'html' in mimetype:
            return ContentType.HTML
        elif 'pdf' in mimetype:
            return ContentType.PDF
        elif any((x in mimetype for x in ['image', 'jpeg', 'png', 'gif'])):
            return ContentType.IMAGE
        elif any((x in mimetype for x in ['video', 'mp4', 'webm'])):
            return ContentType.VIDEO
        elif 'text' in mimetype:
            return ContentType.TEXT
        else:
            return ContentType.UNKNOWN

    async def _extract_from_snapshots(self, snapshots: list[Snapshot]) -> list[dict[str, Any]]:
        """Extract content from snapshots concurrently"""
        semaphore = asyncio.Semaphore(self.concurrent_requests)

        async def extract_with_limit(snapshot: Snapshot) -> dict[str, Any] | None:
            async with semaphore:
                return await self._extract_snapshot(snapshot)
        tasks = [extract_with_limit(s) for s in snapshots]
        results = await parallel_ok(*tasks, label='archive_discovery:1113')
        return [r for r in results if r is not None and (not isinstance(r, Exception))]

    async def _extract_snapshot(self, snapshot: Snapshot) -> dict[str, Any] | None:
        """Extract content from a single snapshot"""
        try:
            if self._anonymizer:
                await asyncio.sleep(self._anonymizer.get_random_delay())
            async with self._session.get(snapshot.archived_url) as resp:
                if resp.status != 200:
                    return None
                # NEW-MEM-002: Use capped read for M1 8GB safety
                content = await _read_text_with_cap(resp)
                if len(content) < 100:
                    return None
                if self._is_error_page(content):
                    return None
                content_type = snapshot.content_type
                quality = self._assess_quality(content, content_type)
                metadata = {}
                if content_type == ContentType.HTML:
                    metadata = self._extract_metadata_html(content)
                snapshot.quality_score = quality
                return {'snapshot': snapshot, 'content': content, 'metadata': metadata, 'quality': quality}
        except Exception as e:
            logger.debug(f'Snapshot extraction failed: {e}')
            return None

    def _is_error_page(self, content: str) -> bool:
        """Check if content is an error page"""
        content_lower = content.lower()
        for pattern in self.ERROR_PATTERNS:
            if re.search(pattern, content_lower):
                return True
        return False

    def _assess_quality(self, content: str, content_type: ContentType) -> float:
        """Assess content quality (0.0-1.0)"""
        score = 0.5
        length = len(content)
        if length > 10000:
            score += 0.2
        elif length > 5000:
            score += 0.1
        elif length < 500:
            score -= 0.2
        if content_type == ContentType.HTML:
            if '<article' in content or '<main' in content:
                score += 0.1
            if self._is_error_page(content):
                score -= 0.5
        return max(0.0, min(1.0, score))

    def _extract_selectolax_metadata(self, parser) -> dict[str, Any]:
        """Extract metadata from HTML using selectolax parser."""
        metadata = {}
        for tag in parser.css('title'):
            text = tag.text(strip=True)
            if text:
                metadata['title'] = text
                break
        # Helper to extract first meta tag content
        def _meta_content(selector):
            for tag in parser.css(selector):
                return tag.attributes.get('content', '')
            return ''
        metadata['og_title'] = _meta_content('meta[property="og:title"]')
        metadata['author'] = _meta_content('meta[name="author"]')
        date = _meta_content('meta[property="article:published_time"]')
        if not date:
            date = _meta_content('meta[name="publishedDate"]')
        if not date:
            date = _meta_content('meta[name="date"]')
        if date:
            metadata['date'] = date
        metadata['description'] = _meta_content('meta[name="description"]')
        return metadata

    def _parse_selectolax(self, content: str) -> dict[str, Any] | None:
        """Parse HTML metadata using selectolax parser."""
        if not (SELECTOLAX_AVAILABLE and _SelectoLAXParser):
            return None
        try:
            parser = _SelectoLAXParser(content)
            return self._extract_selectolax_metadata(parser)
        except Exception:  # noqa: BLE001
            return None

    def _parse_bs4(self, content: str) -> dict[str, Any] | None:
        """Parse HTML metadata using BeautifulSoup."""
        if not BS4_AVAILABLE:
            return None
        try:
            soup = BeautifulSoup(content, 'html.parser')
            metadata: dict[str, Any] = {}
            title_tag = soup.find('title')
            if title_tag:
                metadata['title'] = title_tag.get_text(strip=True)
            og_title = soup.find('meta', property='og:title')
            if og_title:
                metadata['og_title'] = og_title.get('content', '')
            author = soup.find('meta', attrs={'name': 'author'})
            if author:
                metadata['author'] = author.get('content', '')
            date_tags = [
                soup.find('meta', property='article:published_time'),
                soup.find('meta', attrs={'name': 'publishedDate'}),
                soup.find('meta', attrs={'name': 'date'}),
            ]
            for tag in date_tags:
                if tag:
                    metadata['date'] = tag.get('content', '')
                    break
            desc = soup.find('meta', attrs={'name': 'description'})
            if desc:
                metadata['description'] = desc.get('content', '')
            return metadata
        except Exception:  # noqa: BLE001
            return None

    def _parse_regex(self, content: str) -> dict[str, Any] | None:
        """Parse HTML metadata using regex (fallback parser)."""
        try:
            metadata: dict[str, Any] = {}
            title_match = re.search(
                r'<title[^>]*>([^<]+)</title>', content, re.IGNORECASE
            )
            if title_match:
                metadata['title'] = title_match.group(1).strip()
            for match in re.finditer(
                r'<meta\s+(?:property|name)=["\']author["\']\s+content=["\']([^"\']+)["\']',
                content,
                re.IGNORECASE,
            ):
                metadata['author'] = match.group(1)
            for match in re.finditer(
                r'<meta\s+property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
                content,
                re.IGNORECASE,
            ):
                metadata['date'] = match.group(1)
            for match in re.finditer(
                r'<meta\s+name=["\'](?:publishedDate|date)["\']\s+content=["\']([^"\']+)["\']',
                content,
                re.IGNORECASE,
            ):
                metadata.setdefault('date', match.group(1))
            desc_match = re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
                content,
                re.IGNORECASE,
            )
            if desc_match:
                metadata['description'] = desc_match.group(1)
            return metadata
        except Exception:  # noqa: BLE001
            return None

    def _extract_metadata_html(self, content: str) -> dict[str, Any]:
        """Extract metadata from HTML content.

        Simple dispatcher: selectolax → bs4 → regex (first non-empty wins).
        """
        for parser in (self._parse_selectolax, self._parse_bs4, self._parse_regex):
            result = parser(content)
            if result:
                return result
        return {}

    def _select_best_content(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Select best content from results"""
        sorted_results = sorted(results, key=lambda x: (x['quality'], x['snapshot'].timestamp), reverse=True)
        return sorted_results[0]

    def get_statistics(self) -> dict[str, Any]:
        """Get resurrector statistics"""
        return {'resurrections_attempted': self._resurrections_attempted, 'resurrections_successful': self._resurrections_successful, 'success_rate': self._resurrections_successful / self._resurrections_attempted if self._resurrections_attempted > 0 else 0, 'snapshots_found': self._snapshots_found, 'avg_snapshots_per_resurrection': self._snapshots_found / self._resurrections_attempted if self._resurrections_attempted > 0 else 0}

    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._session:
            await self._session.close()
        logger.info('ArchiveResurrector cleanup complete')

async def resurrect_url(url: str) -> str | None:
    """Quick resurrect URL and return content."""
    resurrector = ArchiveResurrector()
    if await resurrector.initialize():
        result = await resurrector.resurrect(url)
        if result.success:
            return result.content
    return None
_archive_resurrector: ArchiveResurrector | None = None

def get_archive_resurrector() -> ArchiveResurrector:
    """Get or create global ArchiveResurrector instance"""
    global _archive_resurrector
    if _archive_resurrector is None:
        _archive_resurrector = ArchiveResurrector()
    return _archive_resurrector

async def search_archives(url: str, limit: int=5) -> dict[str, list[ArchiveResult]]:
    """Search for archived versions of a URL."""
    discovery = ArchiveDiscovery()
    return await discovery.search_url(url, limit_per_source=limit)

async def get_wayback_snapshots(url: str, limit: int=10) -> list[SnapshotInfo]:
    """Get Wayback Machine snapshots for a URL."""
    async with WaybackMachineClient() as client:
        return await client.get_snapshots(url, limit=limit)

async def discover_from_wayback(url: str, limit: int=50) -> list[DiscoveredEndpoint]:
    """Discover historical endpoints from Wayback Machine.
    COMPAT: Tato funkce je archive-discovery wrapper kolem WaybackCDX.
    AUTHORITY: WaybackCDX.get_snapshots() je nízkoúrovňový interface.
    REMOVAL CONDITION: pokud by se měl tento wrapper odstranit,
    všechny call-sites přejdou přímo na WaybackCDX."""
    endpoints = []
    async with WaybackCDX(cache_dir=Path('/tmp/wayback_cdx')) as client:
        snapshots = await client.get_snapshots(url, limit=limit)
        for rec in snapshots:
            ts = rec.get('timestamp', '')
            original_url = rec.get('original', '')
            endpoint = DiscoveredEndpoint(url=f'https://web.archive.org/web/{ts}/{original_url}', confidence_score=0.8, discovery_method='wayback', last_modified=ts, archive_source='wayback')
            endpoints.append(endpoint)
    return endpoints
import orjson
from zstandard import ZstdCompressor, ZstdDecompressor
_zstd_compressor = ZstdCompressor()
_zstd_decompressor = ZstdDecompressor()
import xxhash
from hledac.universal.utils.asyncx import parallel_ok
from core import aclose

class WaybackCDX:
    """Wayback Machine CDX API — low-level domain/URL snapshot discovery.
    ZADARMO, bez API klíče. Unikátní zdroj: smazaný obsah (C2 configs,
    leaked keys, expired phishing domains).
    M1: pure aiohttp async, orjson, xxhash cache 24h."""
    _CDX_URL = 'https://web.archive.org/cdx/search/cdx'
    _RATE_S = 2.0
    _CACHE_TTL = 86400
    __slots__ = tuple(('_cache_dir', '_last_req', '_session'))

    def __init__(self, cache_dir: str | Path | None=None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else Path('/tmp/wayback_cache')
        self._last_req = 0.0
        self._session: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WaybackCDX":
        self._session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def get_snapshots(self, url_or_domain: str, limit: int=50, from_year: int=2019) -> list[dict]:
        """Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.
        Akceptuje URL i domain (auto-detekce podle wildcard syntaxe).
        Bez externí session — vytváří vlastní."""
        if not self._session:
            raise RuntimeError("WaybackCDX requires 'async with' context")
        key = xxhash.xxh3_64(f'wb_{url_or_domain}_{from_year}'.encode()).hexdigest()
        zst_path = self._cache_dir / f'{key}.json.zst'
        json_path = self._cache_dir / f'{key}.json'
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            raw_bytes = await asyncio.to_thread(zst_path.read_bytes)
            return orjson.loads(_zstd_decompressor.decompress(raw_bytes))
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            raw_bytes = await asyncio.to_thread(json_path.read_bytes)
            return orjson.loads(raw_bytes)
        await self._throttle()
        is_domain = '*.' in url_or_domain or not url_or_domain.startswith('http')
        url_param = url_or_domain if is_domain else f'*.{url_or_domain}'
        params = {'url': url_param, 'output': 'json', 'limit': str(limit), 'filter': 'statuscode:200', 'from': str(from_year) + '0101', 'fl': 'original,timestamp,statuscode,mimetype', 'collapse': 'urlkey'}
        try:
            async with self._session.get(self._CDX_URL, params=params, timeout=httpx.Timeout(total=15)) as r:
                if r.status == 429:
                    logger.warning(f'Wayback CDX rate limit: {url_or_domain}')
                    return []
                r.raise_for_status()
                raw = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f'WaybackCDX {url_or_domain}: {e}')
            return []
        if not raw or len(raw) < 2:
            return []
        headers, rows = (raw[0], raw[1:])
        result = [dict(zip(headers, row, strict=False)) for row in rows if len(row) > 3 and row[3].startswith('text/') or len(row) <= 3]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        zst_path.write_bytes(_zstd_compressor.compress(orjson.dumps(result)))
        return result

    async def fetch_snapshot_text(self, url: str, timestamp: str) -> str:
        """Stáhnout text konkrétního snapshotu pro PatternMatcher scan.
        URL format: https://web.archive.org/web/{timestamp}/{original_url}"""
        if not self._session:
            raise RuntimeError("WaybackCDX requires 'async with' context")
        wayback_url = f'https://web.archive.org/web/{timestamp}/{url}'
        try:
            async with self._session.get(wayback_url, timeout=httpx.Timeout(total=20), headers={'Accept': 'text/html'}) as r:
                if r.status != 200:
                    return ''
                return await r.text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.debug(f'snapshot fetch {wayback_url}: {e}')
            return ''

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()

    async def snapshots_one_shot(self, url_or_domain: str, limit: int=50, from_year: int=2019) -> list[dict]:
        """One-shot CDX lookup — vytvoří a zavře vlastní session.
        USE CASE: compat layer, tests, ad-hoc volání bez externího session.
        PRO: žádné unclosed session warnings.
        """
        async with self:
            return await self.get_snapshots(url_or_domain, limit=limit, from_year=from_year)

class WaybackSnapshot(msgspec.Struct, gc=False):
    """Structured Wayback Machine snapshot result."""
    timestamp: str
    archived_url: str
    status_code: int
    mimetype: str
    length: int
    digest: str

async def query_wayback(url: str, limit: int=10) -> list[WaybackSnapshot]:
    """
    Query Wayback Machine CDX API for snapshots of a URL.

    Args:
        url: URL to query
        limit: Maximum number of snapshots to return

    Returns:
        List of WaybackSnapshot objects with snapshot URL and timestamp
    """
    WAYBACK_CDX_API = 'https://web.archive.org/cdx/search/cdx'
    results: list[WaybackSnapshot] = []
    try:
        params = {'url': url, 'output': 'json', 'collapse': 'digest', 'fl': 'timestamp,original,statuscode,mimetype,length,digest', 'limit': limit}
        _sess = await httpx.AsyncClient()
        async with _sess as sess:
            async with sess.get(WAYBACK_CDX_API, params=params, timeout=httpx.Timeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for rec in data:
                        if len(rec) >= 6:
                            results.append(WaybackSnapshot(timestamp=rec[0], archived_url=f'https://web.archive.org/web/{rec[0]}/{rec[1]}', status_code=int(rec[2]) if rec[2] else 200, mimetype=rec[3] or 'unknown', length=int(rec[4]) if rec[4] else 0, digest=rec[5] or ''))
    except Exception as e:
        logger.debug(f'query_wayback({url}): {e}')
    return results

class CommonCrawlSnapshot(msgspec.Struct, gc=False):
    """Structured Common Crawl result."""
    url: str
    timestamp: str
    status_code: int
    html_length: int
    offset: int

async def query_common_crawl(domain: str, limit: int=10) -> list[CommonCrawlSnapshot]:
    """
    Query Common Crawl Index for URLs matching a domain.

    Args:
        domain: Domain to search (e.g., "example.com")
        limit: Maximum number of results to return

    Returns:
        List of CommonCrawlSnapshot objects
    """
    CC_INDEX_API = 'https://index.commoncrawl.org/collinfo.json'
    results: list[CommonCrawlSnapshot] = []
    try:
        _sess = await httpx.AsyncClient()
        async with _sess as session:
            async with session.get(CC_INDEX_API, timeout=httpx.Timeout(total=15)) as resp:
                if resp.status == 200:
                    col_info = await resp.json()
                    if not col_info:
                        return results
            for col in col_info[:3]:
                cdo = col.get('cdx-api', '')
                if not cdo:
                    continue
                params = {'url': f'*.{domain}', 'output': 'json', 'limit': limit, 'fl': 'url,timestamp,status,length,offset'}
                async with session.get(cdo, params=params, timeout=httpx.Timeout(total=30)) as resp:
                    if resp.status == 200:
                        # NEW-MEM-002: Use capped read for CommonCrawl CDX (small lines, cap for safety)
                        text = await _read_text_with_cap(resp, cap=512 * 1024)  # 512KB cap
                        for line in text.strip().split('\n'):
                            try:
                                rec = _msgspec_loads(line)
                                if len(rec) >= 5:
                                    results.append(CommonCrawlSnapshot(url=rec[0], timestamp=rec[1], status_code=int(rec[2]) if rec[2] else 0, html_length=int(rec[3]) if rec[3] else 0, offset=int(rec[4]) if rec[4] else 0))
                            except Exception:
                                continue
                if len(results) >= limit:
                    break
    except Exception as e:
        logger.debug(f'query_common_crawl({domain}): {e}')
    return results[:limit]

class GitHubDorkResult(msgspec.Struct, gc=False):
    """GitHub search result."""
    name: str
    url: str
    description: str | None
    stars: int
    language: str | None
    updated: str

class GitHubDorkingClient:
    """
    GitHub REST API search bez tokenu — rate limit 10 req/min.
    Pro použití s GitHub Advanced Search operators v query string.
    """
    _BASE_URL = 'https://api.github.com/search/code'
    _RATE_LIMIT = 6.1
    __slots__ = tuple(('_last_req', '_token'))

    def __init__(self, token: str | None=None) -> None:
        self._token = token
        self._last_req = 0.0

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_LIMIT:
            await asyncio.sleep(self._RATE_LIMIT - elapsed)
        self._last_req = time.time()

    async def search(self, query: str, session: httpx.AsyncClient, limit: int=10) -> list[GitHubDorkResult]:
        """
        Search GitHub code using advanced operators.
        Example: "leaked password" language:python extension:env
        """
        results: list[GitHubDorkResult] = []
        headers = {'Accept': 'application/vnd.github.v3+json'}
        if self._token:
            headers['Authorization'] = f'token {self._token}'
        await self._throttle()
        try:
            params = {'q': query, 'per_page': min(limit, 100), 'page': 1}
            async with session.get(self._BASE_URL, headers=headers, params=params, timeout=httpx.Timeout(total=30)) as resp:
                self._last_req = time.time()
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get('items', [])[:limit]:
                        results.append(GitHubDorkResult(name=item.get('name', ''), url=item.get('html_url', ''), description=item.get('description'), stars=item.get('stargazers_count', 0), language=item.get('language'), updated=item.get('updated_at', '')))
                elif resp.status == 403:
                    logger.warning('GitHub API rate limit exceeded')
        except Exception as e:
            logger.debug(f'GitHub search({query}): {e}')
        return results

class PastebinResult(msgspec.Struct, gc=False):
    """Pastebin scrape result."""
    key: str
    title: str | None
    date: str
    size: int
    syntax: str | None
    url: str
    content_preview: str | None = None

class PastebinMonitorClient:
    """
    Pastebin scraping API — free tier, 1 req/min.
    Filter pastes by keyword and store in evidence.
    """
    _SCRAPE_URL = 'https://scrape.pastebin.com/api_scraping.php'
    _RATE_LIMIT = 61.0
    __slots__ = tuple(('_cache_dir', '_cache_ttl', '_last_req'))

    def __init__(self, cache_dir: str | Path=Path('/tmp/pastebin_cache')) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_ttl = 300
        self._last_req = 0.0

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_LIMIT:
            await asyncio.sleep(self._RATE_LIMIT - elapsed)
        self._last_req = time.time()

    async def get_recent_pastes(self, session: httpx.AsyncClient, limit: int=100) -> list[PastebinResult]:
        """Fetch recent public pastes."""
        results: list[PastebinResult] = []
        await self._throttle()
        try:
            async with session.get(f'{self._SCRAPE_URL}?limit={limit}', timeout=httpx.Timeout(total=15)) as resp:
                if resp.status in (403, 401, 429):
                    logger.debug(f'Pastebin scrape: HTTP {resp.status}')
                    return []
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    for item in data:
                        key = item.get('key', '')
                        results.append(PastebinResult(key=key, title=item.get('title'), date=item.get('date', ''), size=item.get('size', 0), syntax=item.get('syntax'), url=f'https://pastebin.com/raw/{key}' if key else ''))
                    self._last_req = time.time()
        except Exception as e:
            logger.debug(f'PastebinMonitor.get_recent_pastes: {e}')
        return results

    async def filter_by_keyword(self, session: httpx.AsyncClient, keyword: str, limit: int=50) -> list[PastebinResult]:
        """
        Fetch recent pastes and filter by keyword.
        Used for credential/component leak detection.
        """
        all_pastes = await self.get_recent_pastes(session, limit=limit * 3)
        keyword_lower = keyword.lower()
        filtered: list[PastebinResult] = []
        for paste in all_pastes:
            title = (paste.title or '').lower()
            if keyword_lower in title:
                filtered.append(paste)
                if len(filtered) >= limit:
                    break
        return filtered

async def wayback_cdx_lookup(url_or_host: str, limit: int=10, timeout_s: float=8.0) -> list[dict]:
    """Compat: Wayback CDX lookup pro deep_research_sources.py call-site.
    AUTHORITY: Canonical implementation je WaybackCDX.get_snapshots().
    Tato funkce je dočasný compat wrapper — neměň její return format,
    dokud nebudou všechny call-sites přesměrovány.

    Returns:
        List of dicts s klíči: title, url, snippet, backend, rank, provider, source, timestamp
    """
    client = WaybackCDX(cache_dir='/tmp/wayback_cdx')
    snapshots = await client.snapshots_one_shot(url_or_host, limit=limit)
    out = []
    for i, rec in enumerate(snapshots[:limit], 1):
        ts = rec.get('timestamp', '')
        original_url = rec.get('original', '')
        out.append({'title': f'Wayback capture {ts}', 'url': original_url, 'snippet': f"wayback status={rec.get('statuscode', '')} mimetype={rec.get('mimetype', '')}", 'backend': 'wayback', 'rank': i, 'provider': 'wayback_cdx', 'source': 'wayback', 'timestamp': ts})
    return out


class WaybackCDX:
    """Wayback Machine CDX API — low-level domain/URL snapshot discovery.
    ZADARMO, bez API klíče. Unikátní zdroj: smazaný obsah (C2 configs,
    leaked keys, expired phishing domains).
    M1: pure aiohttp async, orjson, xxhash cache 24h."""
    _CDX_URL = 'https://web.archive.org/cdx/search/cdx'
    _RATE_S = 2.0
    _CACHE_TTL = 86400
    __slots__ = tuple(('_cache_dir', '_last_req', '_session'))
    async def _select_cdn_for_crawl(self, session: httpx.AsyncClient) -> list[dict]:
        """Select CDN endpoints from Common Crawl index."""
        CC_INDEX_API = 'https://index.commoncrawl.org/collinfo.json'
        try:
            async with session.get(CC_INDEX_API, timeout=httpx.Timeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return []

    async def _process_crawl_response(self, session: httpx.AsyncClient, cdo: str, domain: str, limit: int) -> list[CommonCrawlSnapshot]:
        """Process a single Common Crawl response."""
        results: list[CommonCrawlSnapshot] = []
        params = {'url': f'*.{domain}', 'output': 'json', 'limit': limit, 'fl': 'url,timestamp,status,length,offset'}
        try:
            async with session.get(cdo, params=params, timeout=httpx.Timeout(total=30)) as resp:
                if resp.status == 200:
                    # NEW-MEM-002: Use capped read for CommonCrawl response
                    text = await _read_text_with_cap(resp, cap=512 * 1024)  # 512KB cap
                    for line in text.strip().split('\n'):
                        try:
                            rec = _msgspec_loads(line)
                            if len(rec) >= 5:
                                results.append(CommonCrawlSnapshot(
                                    url=rec[0], timestamp=rec[1], status_code=int(rec[2]) if rec[2] else 0,
                                    html_length=int(rec[3]) if rec[3] else 0, offset=int(rec[4]) if rec[4] else 0
                                ))
                        except Exception:
                            continue
        except Exception:
            pass
        return results

    async def query_common_crawl(self, domain: str, limit: int=10) -> list[CommonCrawlSnapshot]:
        """
        Query Common Crawl Index for URLs matching a domain.

        Args:
            domain: Domain to search (e.g., "example.com")
            limit: Maximum number of results to return

        Returns:
            List of CommonCrawlSnapshot objects
        """
        results: list[CommonCrawlSnapshot] = []
        try:
            async with httpx.AsyncClient() as session:
                col_info = await self._select_cdn_for_crawl(session)
                if not col_info:
                    return results

                for col in col_info[:3]:
                    cdo = col.get('cdx-api', '')
                    if not cdo:
                        continue
                    cdo_results = await self._process_crawl_response(session, cdo, domain, limit)
                    results.extend(cdo_results)
                    if len(results) >= limit:
                        break
        except Exception as e:
            logger.debug(f'query_common_crawl({domain}): {e}')
        return results[:limit]
