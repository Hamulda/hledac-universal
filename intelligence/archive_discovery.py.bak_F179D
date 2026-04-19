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

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import aiohttp
from urllib.parse import quote, unquote, urlparse

# Optional imports for enhanced functionality
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from hledac.security.temporal_anonymizer import TemporalAnonymizer
    from hledac.security.zero_attribution_engine import ZeroAttributionEngine
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Sprint 8I: Payload safety cap for M1 RAM protection
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB


async def _read_text_with_cap(response: "aiohttp.ClientResponse", cap: int = MAX_PAYLOAD_BYTES) -> str:
    """Read response text with payload cap for M1 RAM safety."""
    # Read up to cap bytes; if content exceeds cap, truncate/abort
    try:
        # Use content_length header as a fast path check
        content_length = response.headers.get("content-length", "")
        if content_length and int(content_length) > cap:
            logger.warning(f"[Archive] Content-Length {content_length} exceeds cap {cap}, aborting")
            return ""
        # Read with explicit limit
        body = await response.read()
        if len(body) > cap:
            logger.warning(f"[Archive] Body {len(body)} bytes exceeds cap {cap}, truncating")
            return body[:cap].decode("utf-8", errors="replace")
        return body.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[Archive] Failed to read response body: {e}")
        return ""


# =============================================================================
# ENUMS (from stealth_osint/archive_resurrector.py)
# =============================================================================

class ContentSource(Enum):
    """Sources of archived content (from stealth_osint integration)"""
    WAYBACK = "wayback"
    SEARCH_CACHE = "search_cache"
    SOCIAL_ARCHIVE = "social_archive"
    GHOST_ARCHIVE = "ghost_archive"


class ContentType(Enum):
    """Types of content (from stealth_osint integration)"""
    HTML = "html"
    PDF = "pdf"
    IMAGE = "image"
    VIDEO = "video"
    TEXT = "text"
    UNKNOWN = "unknown"


# =============================================================================
# DATACLASSES (from stealth_osint/archive_resurrector.py)
# =============================================================================

@dataclass
class Snapshot:
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


@dataclass
class ResurrectionResult:
    """Result of content resurrection (from stealth_osint integration)"""
    request_id: str
    original_url: str
    success: bool
    best_snapshot: Optional[Snapshot]
    all_snapshots: List[Snapshot]
    content: Optional[str]
    title: Optional[str]
    author: Optional[str]
    published_date: Optional[datetime]
    extracted_metadata: Dict[str, Any]
    processing_time: float


@dataclass
class ResurrectionRequest:
    """Request for content resurrection (from stealth_osint integration)"""
    request_id: str
    url: str
    target_date: Optional[datetime]
    min_quality: float
    extract_metadata: bool
    created_at: datetime


# =============================================================================
# ORIGINAL DATACLASSES
# =============================================================================

@dataclass
class ArchiveResult:
    """Result from archive discovery."""
    url: str
    title: str
    source: str  # wayback, archive_today, ipfs, etc.
    timestamp: Optional[datetime] = None
    content: Optional[str] = None
    content_type: str = "text/html"
    metadata: Dict[str, Any] = field(default_factory=dict)
    available: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "content_type": self.content_type,
            "metadata": self.metadata,
            "available": self.available,
        }


@dataclass
class SnapshotInfo:
    """Wayback snapshot information."""
    timestamp: datetime
    url: str
    status: str
    digest: str
    length: int


@dataclass
class CDXSnapshot:
    """CDX API snapshot result."""
    timestamp: str
    original_url: str
    status_code: str
    digest: str
    length: str
    
    @property
    def wayback_url(self) -> str:
        """Get Wayback Machine URL for this snapshot."""
        return f"https://web.archive.org/web/{self.timestamp}/{self.original_url}"
    
    @property
    def datetime(self) -> Optional[datetime]:
        """Parse timestamp as datetime."""
        try:
            return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            return None


@dataclass
class DiscoveredEndpoint:
    """Discovered endpoint with metadata."""
    url: str
    title: Optional[str] = None
    confidence_score: float = 0.0
    discovery_method: str = "unknown"
    file_type: Optional[str] = None
    path: str = ""
    source_url: Optional[str] = None
    tech_stack: Optional[Dict[str, Any]] = None
    last_modified: Optional[str] = None
    size_bytes: Optional[int] = None
    archive_source: Optional[str] = None
    
    def __post_init__(self):
        if not self.path and self.url:
            parsed = urlparse(self.url)
            self.path = parsed.path
    
    @property
    def is_archived(self) -> bool:
        return self.archive_source is not None
    
    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'url': self.url,
            'title': self.title,
            'confidence_score': self.confidence_score,
            'discovery_method': self.discovery_method,
            'archive_source': self.archive_source,
            'is_archived': self.is_archived,
            'domain': self.domain,
        }


class WaybackMachineClient:
    """Client for Internet Archive Wayback Machine."""
    
    BASE_URL = "https://web.archive.org"
    CDX_API = "https://web.archive.org/cdx/search/cdx"
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.session = None
    
    async def __aenter__(self):
        import aiohttp
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def get_snapshots(
        self,
        url: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 10
    ) -> List[SnapshotInfo]:
        """Get list of snapshots for a URL."""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()
        
        params = {
            "url": url,
            "output": "json",
            "fl": "timestamp,original,statuscode,digest,length",
            "collapse": "digest",
            "limit": str(limit),
        }
        
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        
        try:
            async with self.session.get(
                self.CDX_API,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status != 200:
                    logger.warning(f"Wayback CDX API returned {response.status}")
                    return []
                
                data = await response.json()
                snapshots = []
                
                for row in data[1:]:  # Skip header
                    if len(row) >= 5:
                        timestamp_str = row[0]
                        timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                        
                        snapshots.append(SnapshotInfo(
                            timestamp=timestamp,
                            url=row[1],
                            status=row[2],
                            digest=row[3],
                            length=int(row[4]) if row[4].isdigit() else 0
                        ))
                
                return snapshots
                
        except Exception as e:
            logger.error(f"Wayback snapshots error: {e}")
            return []
    
    async def get_snapshot_content(
        self,
        url: str,
        timestamp: Optional[datetime] = None
    ) -> Optional[ArchiveResult]:
        """Get content of a specific snapshot."""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()
        
        try:
            if timestamp:
                ts_str = timestamp.strftime("%Y%m%d%H%M%S")
                archive_url = f"{self.BASE_URL}/web/{ts_str}/{url}"
            else:
                archive_url = f"{self.BASE_URL}/web/{url}"
            
            async with self.session.get(
                archive_url,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    content = await _read_text_with_cap(response)
                    title = self._extract_title(content) or f"Snapshot of {url}"
                    
                    return ArchiveResult(
                        url=archive_url,
                        title=title,
                        source="wayback",
                        timestamp=timestamp,
                        content=content,
                        content_type=response.headers.get("Content-Type", "text/html"),
                        metadata={"original_url": url}
                    )
                else:
                    logger.warning(f"Wayback content returned {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Wayback content error: {e}")
            return None
    
    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML."""
        import re
        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        return match.group(1).strip() if match else None


class ArchiveTodayClient:
    """Client for Archive.today / archive.ph."""
    
    BASE_URL = "https://archive.today"
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.session = None
    
    async def __aenter__(self):
        import aiohttp
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def search(self, url: str) -> List[ArchiveResult]:
        """Search for archived versions on Archive.today."""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()
        
        try:
            search_url = f"{self.BASE_URL}/search/?q={quote(url)}"
            
            async with self.session.get(
                search_url,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    html = await response.text()
                    return self._parse_search_results(html, url)
                else:
                    return []
                    
        except Exception as e:
            logger.error(f"Archive.today search error: {e}")
            return []
    
    def _parse_search_results(self, html: str, original_url: str) -> List[ArchiveResult]:
        """Parse Archive.today search results."""
        import re
        results = []
        
        pattern = r'href="(https://archive\.today/[^"]+)"[^>]*>([^<]+)'
        matches = re.findall(pattern, html)
        
        for archive_url, title in matches[:5]:
            results.append(ArchiveResult(
                url=archive_url,
                title=title or f"Archive of {original_url}",
                source="archive_today",
                metadata={"original_url": original_url}
            ))
        
        return results


class IPFSClient:
    """Client for IPFS gateways."""
    
    GATEWAYS = [
        "https://ipfs.io/ipfs/",
        "https://gateway.ipfs.io/ipfs/",
        "https://cloudflare-ipfs.com/ipfs/",
        "https://dweb.link/ipfs/",
    ]
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.session = None
    
    async def __aenter__(self):
        import aiohttp
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def fetch_content(self, cid: str) -> Optional[ArchiveResult]:
        """Fetch content from IPFS by CID."""
        if not self.session:
            import aiohttp
            self.session = aiohttp.ClientSession()
        
        for gateway in self.GATEWAYS:
            try:
                url = f"{gateway}{cid}"
                
                async with self.session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status == 200:
                        content = await _read_text_with_cap(response)
                        
                        return ArchiveResult(
                            url=url,
                            title=f"IPFS: {cid[:20]}...",
                            source="ipfs",
                            content=content,
                            content_type=response.headers.get("Content-Type", "text/html"),
                            metadata={"cid": cid, "gateway": gateway}
                        )
                        
            except Exception as e:
                logger.debug(f"IPFS gateway {gateway} failed: {e}")
                continue
        
        return None


class GitHubHistoricalClient:
    """Client for GitHub historical commits."""
    
    API_BASE = "https://api.github.com"
    
    def __init__(self, token: Optional[str] = None, timeout: float = 30.0):
        self.token = token
        self.timeout = timeout
        self.session = None
    
    async def __aenter__(self):
        import aiohttp
        headers = {}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        
        self.session = aiohttp.ClientSession(headers=headers)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
            self.session = None
    
    async def get_file_history(
        self,
        repo: str,
        path: str,
        limit: int = 10
    ) -> List[ArchiveResult]:
        """Get historical versions of a file from GitHub."""
        if not self.session:
            await self.__aenter__()
        
        try:
            url = f"{self.API_BASE}/repos/{repo}/commits"
            params = {"path": path, "per_page": limit}
            
            async with self.session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    commits = await response.json()
                    
                    results = []
                    for commit in commits:
                        commit_data = commit.get("commit", {})
                        author_data = commit_data.get("author", {})
                        
                        timestamp_str = author_data.get("date")
                        if timestamp_str:
                            timestamp = datetime.fromisoformat(
                                timestamp_str.replace("Z", "+00:00")
                            )
                        else:
                            timestamp = None
                        
                        results.append(ArchiveResult(
                            url=commit.get("html_url", ""),
                            title=f"{commit_data.get('message', 'No message')[:50]}...",
                            source="github",
                            timestamp=timestamp,
                            metadata={
                                "sha": commit.get("sha"),
                                "author": author_data.get("name"),
                                "repo": repo,
                                "path": path,
                            }
                        ))
                    
                    return results
                else:
                    logger.warning(f"GitHub API returned {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"GitHub history error: {e}")
            return []


class ArchiveDiscovery:
    """
    Main archive discovery orchestrator.
    
    Combines multiple archival sources for comprehensive
    historical content discovery.
    """
    
    def __init__(
        self,
        wayback_timeout: float = 30.0,
        archive_today_timeout: float = 30.0,
        ipfs_timeout: float = 30.0,
        github_token: Optional[str] = None
    ):
        self.wayback = WaybackMachineClient(wayback_timeout)
        self.archive_today = ArchiveTodayClient(archive_today_timeout)
        self.ipfs = IPFSClient(ipfs_timeout)
        self.github = GitHubHistoricalClient(github_token)
    
    async def search_url(
        self,
        url: str,
        sources: Optional[List[str]] = None,
        limit_per_source: int = 5
    ) -> Dict[str, List[ArchiveResult]]:
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
            sources = ["wayback", "archive_today"]
        
        results = {}
        
        if "wayback" in sources:
            try:
                async with self.wayback:
                    wayback_results = await self.wayback.search(url, limit=limit_per_source)
                    results["wayback"] = wayback_results
            except Exception as e:
                logger.error(f"Wayback search error: {e}")
                results["wayback"] = []
        
        if "archive_today" in sources:
            try:
                async with self.archive_today:
                    at_results = await self.archive_today.search(url)
                    results["archive_today"] = at_results
            except Exception as e:
                logger.error(f"Archive.today search error: {e}")
                results["archive_today"] = []
        
        return results
    
    async def get_timeline(
        self,
        url: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None
    ) -> List[ArchiveResult]:
        """Get timeline of changes for a URL."""
        from_date_str = from_date.strftime("%Y%m%d") if from_date else None
        to_date_str = to_date.strftime("%Y%m%d") if to_date else None
        
        async with self.wayback:
            snapshots = await self.wayback.get_snapshots(
                url,
                from_date=from_date_str,
                to_date=to_date_str,
                limit=50
            )
            
            results = []
            for snapshot in snapshots:
                results.append(ArchiveResult(
                    url=f"https://web.archive.org/web/{snapshot.timestamp.strftime('%Y%m%d%H%M%S')}/{snapshot.url}",
                    title=f"Snapshot from {snapshot.timestamp.strftime('%Y-%m-%d %H:%M')}",
                    source="wayback",
                    timestamp=snapshot.timestamp,
                    metadata={
                        "status": snapshot.status,
                        "digest": snapshot.digest,
                        "length": snapshot.length
                    }
                ))
            
            return results


# =============================================================================
# WaybackCDX — Sprint 8UB: canonical low-level CDX request surface
# AUTHORITY: WaybackCDX je jediný canonical low-level CDX interface.
# Všechny ostatní Wayback CDX implementace jsou compat layery.
# AUTHORITY NOTE: xxhash cache (24h TTL), rate limit 2s, M1-safe.
# REMOVAL CONDITION: až všechny call-sites přejdou na WaybackCDX.get_snapshots()
# =============================================================================


# =============================================================================
# ARCHIVE RESURRECTOR (from stealth_osint/archive_resurrector.py)
# =============================================================================

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
    
    # Archive configurations
    WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
    WAYBACK_RAW_URL = "https://web.archive.org/web/{timestamp}id_/{url}"
    
    SEARCH_ENGINES = {
        "google": "https://webcache.googleusercontent.com/search?q=cache:",
        "bing": "https://r.jina.ai/http://",
        "yandex": "https://yandexwebcache.net/yandbtm?url=",
    }
    
    SOCIAL_ARCHIVES = {
        "politwoops": "https://politwoops.com/",
        "unreddit": "https://r.jina.ai/http://reddit.com",
    }
    
    # Content patterns for quality assessment
    ERROR_PATTERNS = [
        r"404\s*not\s*found",
        r"page\s*not\s*found",
        r"site\s*not\s*found",
        r"wayback\s*machine\s*doesn't\s*have",
        r"this\s*page\s*is\s*not\s*available",
        r"snapshot\s*cannot\s*be\s*displayed",
    ]
    
    def __init__(
        self,
        min_quality: float = 0.5,
        max_snapshots: int = 10,
        concurrent_requests: int = 3
    ):
        self.min_quality = min_quality
        self.max_snapshots = max_snapshots
        self.concurrent_requests = concurrent_requests
        
        # Security components
        self._anonymizer = None
        self._zero_attribution = None
        
        # HTTP session
        self._session = None
        
        # Request tracking
        self._active_requests: Dict[str, ResurrectionRequest] = {}
        self._request_history: List[ResurrectionRequest] = []
        
        # Performance metrics
        self._resurrections_attempted = 0
        self._resurrections_successful = 0
        self._snapshots_found = 0
        
        logger.info("ArchiveResurrector initialized")
    
    async def initialize(self) -> bool:
        """Initialize security components and HTTP session"""
        try:
            import aiohttp
            
            # Initialize security components
            if SECURITY_AVAILABLE:
                try:
                    self._anonymizer = TemporalAnonymizer()
                    self._zero_attribution = ZeroAttributionEngine()
                except Exception as e:
                    logger.warning(f"Security components not available: {e}")
            
            # Create HTTP session
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=aiohttp.ClientTimeout(total=60)
            )
            
            logger.info("✅ ArchiveResurrector initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            return False
    
    async def resurrect(
        self,
        url: str,
        target_date: Optional[datetime] = None,
        min_quality: Optional[float] = None
    ) -> ResurrectionResult:
        """Resurrect content from web archives."""
        min_quality = min_quality or self.min_quality
        self._resurrections_attempted += 1
        
        request_id = hashlib.sha256(f"{url}:{datetime.now()}".encode()).hexdigest()[:16]
        
        request = ResurrectionRequest(
            request_id=request_id,
            url=url,
            target_date=target_date,
            min_quality=min_quality,
            extract_metadata=True,
            created_at=datetime.now()
        )
        
        self._active_requests[request_id] = request
        
        logger.info(f"🕸️ Resurrecting: {url}")
        start_time = time.monotonic()
        
        try:
            # Find all available snapshots
            snapshots = await self._find_snapshots(url, target_date)
            
            if not snapshots:
                logger.warning(f"No snapshots found for: {url}")
                return ResurrectionResult(
                    request_id=request_id,
                    original_url=url,
                    success=False,
                    best_snapshot=None,
                    all_snapshots=[],
                    content=None,
                    title=None,
                    author=None,
                    published_date=None,
                    extracted_metadata={},
                    processing_time=time.monotonic() - start_time
                )
            
            self._snapshots_found += len(snapshots)
            
            # Extract content from best snapshots
            results = await self._extract_from_snapshots(snapshots)
            
            # Filter successful extractions
            successful = [r for r in results if r is not None]
            
            if not successful:
                logger.warning(f"Could not extract content from any snapshot: {url}")
                return ResurrectionResult(
                    request_id=request_id,
                    original_url=url,
                    success=False,
                    best_snapshot=None,
                    all_snapshots=snapshots,
                    content=None,
                    title=None,
                    author=None,
                    published_date=None,
                    extracted_metadata={},
                    processing_time=time.monotonic() - start_time
                )
            
            # Select best content
            best_result = self._select_best_content(successful)
            
            self._resurrections_successful += 1
            
            logger.info(
                f"✅ Resurrected: {url} "
                f"(snapshots: {len(snapshots)}, best: {best_result['snapshot'].timestamp})"
            )
            
            # Move to history
            self._request_history.append(request)
            del self._active_requests[request_id]
            
            return ResurrectionResult(
                request_id=request_id,
                original_url=url,
                success=True,
                best_snapshot=best_result["snapshot"],
                all_snapshots=snapshots,
                content=best_result["content"],
                title=best_result["metadata"].get("title"),
                author=best_result["metadata"].get("author"),
                published_date=best_result["metadata"].get("date"),
                extracted_metadata=best_result["metadata"],
                processing_time=time.monotonic() - start_time
            )
            
        except Exception as e:
            logger.error(f"❌ Resurrection failed: {e}")
            return ResurrectionResult(
                request_id=request_id,
                original_url=url,
                success=False,
                best_snapshot=None,
                all_snapshots=[],
                content=None,
                title=None,
                author=None,
                published_date=None,
                extracted_metadata={},
                processing_time=time.monotonic() - start_time
            )
    
    async def _find_snapshots(
        self,
        url: str,
        target_date: Optional[datetime]
    ) -> List[Snapshot]:
        """Find all available snapshots for URL"""
        snapshots = []
        
        # Apply temporal anonymization
        if self._anonymizer:
            await asyncio.sleep(self._anonymizer.get_random_delay())
        
        # Check Wayback Machine
        wayback_snapshots = await self._check_wayback(url, target_date)
        snapshots.extend(wayback_snapshots)
        
        # Check search engine cache
        cache_snapshots = await self._check_search_cache(url)
        snapshots.extend(cache_snapshots)
        
        # Check social media archives
        social_snapshots = await self._check_social_archive(url)
        snapshots.extend(social_snapshots)
        
        # Sort by timestamp (most recent first)
        snapshots.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Limit to max_snapshots
        return snapshots[:self.max_snapshots]
    
    async def _check_wayback(
        self,
        url: str,
        target_date: Optional[datetime]
    ) -> List[Snapshot]:
        """Check Wayback Machine CDX API for snapshots"""
        snapshots = []
        
        try:
            # Build CDX query
            params = {
                "url": url,
                "output": "json",
                "collapse": "digest",
                "fl": "timestamp,original,mimetype,statuscode,digest,length",
            }
            
            # Add date filter if target date specified
            if target_date:
                params["from"] = (target_date - timedelta(days=30)).strftime("%Y%m%d")
                params["to"] = (target_date + timedelta(days=30)).strftime("%Y%m%d")
            
            async with self._session.get(self.WAYBACK_CDX_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.text()
                    
                    # Parse CDX JSON
                    lines = data.strip().split("\n")
                    if len(lines) > 1:
                        # Skip header
                        for line in lines[1:]:
                            try:
                                parts = json.loads(line)
                                if len(parts) >= 6:
                                    timestamp_str = parts[0]
                                    original_url = parts[1]
                                    mimetype = parts[2]
                                    status = parts[3]
                                    length = parts[5]
                                    
                                    # Parse timestamp
                                    timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                                    
                                    # Determine content type
                                    content_type = self._detect_content_type(mimetype)
                                    
                                    # Build archived URL
                                    archived_url = self.WAYBACK_RAW_URL.format(
                                        timestamp=timestamp_str,
                                        url=original_url
                                    )
                                    
                                    snapshot = Snapshot(
                                        snapshot_id=hashlib.sha256(
                                            f"wayback:{timestamp_str}:{url}".encode()
                                        ).hexdigest()[:16],
                                        url=url,
                                        archived_url=archived_url,
                                        timestamp=timestamp,
                                        source=ContentSource.WAYBACK,
                                        content_type=content_type,
                                        status_code=int(status) if status else 200,
                                        content_length=int(length) if length else 0,
                                        available=True
                                    )
                                    snapshots.append(snapshot)
                                    
                            except Exception as e:
                                logger.debug(f"Failed to parse CDX line: {e}")
                                continue
                                
        except Exception as e:
            logger.debug(f"Wayback check failed: {e}")
        
        return snapshots
    
    async def _check_search_cache(self, url: str) -> List[Snapshot]:
        """Check search engine cache for URL"""
        snapshots = []
        
        for engine, cache_url in self.SEARCH_ENGINES.items():
            try:
                cache_full_url = f"{cache_url}{quote(url)}"
                
                async with self._session.head(cache_full_url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        snapshot = Snapshot(
                            snapshot_id=hashlib.sha256(
                                f"cache:{engine}:{url}".encode()
                            ).hexdigest()[:16],
                            url=url,
                            archived_url=cache_full_url,
                            timestamp=datetime.now(),
                            source=ContentSource.SEARCH_CACHE,
                            content_type=ContentType.HTML,
                            status_code=200,
                            content_length=0,
                            available=True
                        )
                        snapshots.append(snapshot)
                        
            except Exception as e:
                logger.debug(f"Cache check failed for {engine}: {e}")
        
        return snapshots
    
    async def _check_social_archive(self, url: str) -> List[Snapshot]:
        """Check social media archives"""
        snapshots = []
        
        # Check if URL is from social media
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Politwoops for Twitter/X
        if any(x in domain for x in ["twitter.com", "x.com", "t.co"]):
            try:
                # Extract tweet ID
                tweet_id = self._extract_tweet_id(url)
                if tweet_id:
                    snapshot = Snapshot(
                        snapshot_id=f"politwoops:{tweet_id}",
                        url=url,
                        archived_url=f"{self.SOCIAL_ARCHIVES['politwoops']}{tweet_id}",
                        timestamp=datetime.now(),
                        source=ContentSource.SOCIAL_ARCHIVE,
                        content_type=ContentType.HTML,
                        status_code=200,
                        content_length=0,
                        available=True
                    )
                    snapshots.append(snapshot)
            except Exception as e:
                logger.debug(f"Politwoops check failed: {e}")
        
        return snapshots
    
    def _extract_tweet_id(self, url: str) -> Optional[str]:
        """Extract tweet ID from Twitter/X URL"""
        patterns = [
            r"twitter\.com/\w+/status/(\d+)",
            r"x\.com/\w+/status/(\d+)",
            r"t\.co/(\w+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def _detect_content_type(self, mimetype: str) -> ContentType:
        """Detect content type from MIME type"""
        mimetype = mimetype.lower()
        
        if "html" in mimetype:
            return ContentType.HTML
        elif "pdf" in mimetype:
            return ContentType.PDF
        elif any(x in mimetype for x in ["image", "jpeg", "png", "gif"]):
            return ContentType.IMAGE
        elif any(x in mimetype for x in ["video", "mp4", "webm"]):
            return ContentType.VIDEO
        elif "text" in mimetype:
            return ContentType.TEXT
        else:
            return ContentType.UNKNOWN
    
    async def _extract_from_snapshots(
        self,
        snapshots: List[Snapshot]
    ) -> List[Dict[str, Any]]:
        """Extract content from snapshots concurrently"""
        semaphore = asyncio.Semaphore(self.concurrent_requests)
        
        async def extract_with_limit(snapshot: Snapshot) -> Optional[Dict[str, Any]]:
            async with semaphore:
                return await self._extract_snapshot(snapshot)
        
        tasks = [extract_with_limit(s) for s in snapshots]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out exceptions and None results
        return [r for r in results if r is not None and not isinstance(r, Exception)]
    
    async def _extract_snapshot(self, snapshot: Snapshot) -> Optional[Dict[str, Any]]:
        """Extract content from a single snapshot"""
        try:
            # Apply temporal delay
            if self._anonymizer:
                await asyncio.sleep(self._anonymizer.get_random_delay())
            
            async with self._session.get(snapshot.archived_url) as resp:
                if resp.status != 200:
                    return None
                
                content = await resp.text()
                
                # Check content size
                if len(content) < 100:
                    return None
                
                # Check for error pages
                if self._is_error_page(content):
                    return None
                
                # Determine content type and quality
                content_type = snapshot.content_type
                quality = self._assess_quality(content, content_type)
                
                # Extract metadata
                metadata = {}
                if content_type == ContentType.HTML:
                    metadata = self._extract_metadata_html(content)
                
                snapshot.quality_score = quality
                
                return {
                    "snapshot": snapshot,
                    "content": content,
                    "metadata": metadata,
                    "quality": quality
                }
                
        except Exception as e:
            logger.debug(f"Snapshot extraction failed: {e}")
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
        score = 0.5  # Base score
        
        # Length factor
        length = len(content)
        if length > 10000:
            score += 0.2
        elif length > 5000:
            score += 0.1
        elif length < 500:
            score -= 0.2
        
        # HTML quality
        if content_type == ContentType.HTML:
            # Check for common content indicators
            if "<article" in content or "<main" in content:
                score += 0.1
            
            # Check for error indicators
            if self._is_error_page(content):
                score -= 0.5
        
        return max(0.0, min(1.0, score))
    
    def _extract_metadata_html(self, content: str) -> Dict[str, Any]:
        """Extract metadata from HTML content"""
        metadata = {}
        
        if not BS4_AVAILABLE:
            return metadata
        
        try:
            soup = BeautifulSoup(content, 'html.parser')
            
            # Title
            title_tag = soup.find('title')
            if title_tag:
                metadata["title"] = title_tag.get_text(strip=True)
            
            # OG title
            og_title = soup.find('meta', property='og:title')
            if og_title:
                metadata["og_title"] = og_title.get('content', '')
            
            # Author
            author = soup.find('meta', attrs={'name': 'author'})
            if author:
                metadata["author"] = author.get('content', '')
            
            # Publication date
            date_tags = [
                soup.find('meta', property='article:published_time'),
                soup.find('meta', attrs={'name': 'publishedDate'}),
                soup.find('meta', attrs={'name': 'date'}),
            ]
            for tag in date_tags:
                if tag:
                    metadata["date"] = tag.get('content', '')
                    break
            
            # Description
            desc = soup.find('meta', attrs={'name': 'description'})
            if desc:
                metadata["description"] = desc.get('content', '')
            
        except Exception as e:
            logger.debug(f"Metadata extraction failed: {e}")
        
        return metadata
    
    def _select_best_content(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Select best content from results"""
        # Sort by quality (best first) and then by timestamp (most recent)
        sorted_results = sorted(
            results,
            key=lambda x: (x["quality"], x["snapshot"].timestamp),
            reverse=True
        )
        
        return sorted_results[0]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get resurrector statistics"""
        return {
            "resurrections_attempted": self._resurrections_attempted,
            "resurrections_successful": self._resurrections_successful,
            "success_rate": (
                self._resurrections_successful / self._resurrections_attempted
                if self._resurrections_attempted > 0 else 0
            ),
            "snapshots_found": self._snapshots_found,
            "avg_snapshots_per_resurrection": (
                self._snapshots_found / self._resurrections_attempted
                if self._resurrections_attempted > 0 else 0
            ),
        }
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._session:
            await self._session.close()
        logger.info("ArchiveResurrector cleanup complete")


# Convenience functions
async def resurrect_url(url: str) -> Optional[str]:
    """Quick resurrect URL and return content."""
    resurrector = ArchiveResurrector()
    
    if await resurrector.initialize():
        result = await resurrector.resurrect(url)
        if result.success:
            return result.content
    
    return None


# Global instance
_archive_resurrector: Optional[ArchiveResurrector] = None


def get_archive_resurrector() -> ArchiveResurrector:
    """Get or create global ArchiveResurrector instance"""
    global _archive_resurrector
    if _archive_resurrector is None:
        _archive_resurrector = ArchiveResurrector()
    return _archive_resurrector


# =============================================================================
# ORIGINAL Convenience functions
# =============================================================================

async def search_archives(url: str, limit: int = 5) -> Dict[str, List[ArchiveResult]]:
    """Search for archived versions of a URL."""
    discovery = ArchiveDiscovery()
    return await discovery.search_url(url, limit_per_source=limit)


async def get_wayback_snapshots(url: str, limit: int = 10) -> List[SnapshotInfo]:
    """Get Wayback Machine snapshots for a URL."""
    async with WaybackMachineClient() as client:
        return await client.get_snapshots(url, limit=limit)


async def discover_from_wayback(
    url: str,
    limit: int = 50
) -> List[DiscoveredEndpoint]:
    """Discover historical endpoints from Wayback Machine.
    COMPAT: Tato funkce je archive-discovery wrapper kolem WaybackCDX.
    AUTHORITY: WaybackCDX.get_snapshots() je nízkoúrovňový interface.
    REMOVAL CONDITION: pokud by se měl tento wrapper odstranit,
    všechny call-sites přejdou přímo na WaybackCDX."""
    endpoints = []

    async with WaybackCDX(cache_dir="/tmp/wayback_cdx") as client:
        snapshots = await client.get_snapshots(url, limit=limit)

        for rec in snapshots:
            ts = rec.get("timestamp", "")
            original_url = rec.get("original", "")
            endpoint = DiscoveredEndpoint(
                url=f"https://web.archive.org/web/{ts}/{original_url}",
                confidence_score=0.8,
                discovery_method="wayback",
                last_modified=ts,
                archive_source="wayback"
            )
            endpoints.append(endpoint)

    return endpoints


# =============================================================================
# WaybackCDX — Sprint 8UB: canonical low-level CDX request surface
# AUTHORITY: WaybackCDX je jediný canonical low-level CDX interface.
# Všechny ostatní Wayback CDX wrappery jsou compat layers.
# AUTHORITY NOTE: xxhash cache (24h TTL), rate limit 2s, M1-safe.
# REMOVAL CONDITION: až všechny call-sites přejdou na WaybackCDX.get_snapshots()
# =============================================================================
# COMPAT ALIAS: WaybackCDXClient je deprecated alias — použij WaybackCDX
# REMOVAL CONDITION: po přechodu všech call-sites na WaybackCDX
# =============================================================================

import xxhash
import orjson


class WaybackCDX:
    """Wayback Machine CDX API — low-level domain/URL snapshot discovery.
    ZADARMO, bez API klíče. Unikátní zdroj: smazaný obsah (C2 configs,
    leaked keys, expired phishing domains).
    M1: pure aiohttp async, orjson, xxhash cache 24h."""

    _CDX_URL = "https://web.archive.org/cdx/search/cdx"
    _RATE_S = 2.0
    _CACHE_TTL = 86400  # 24h — historická data se nemění

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else Path("/tmp/wayback_cache")
        self._last_req = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "WaybackCDX":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def get_snapshots(
        self,
        url_or_domain: str,
        limit: int = 50,
        from_year: int = 2019,
    ) -> list[dict]:
        """Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.
        Akceptuje URL i domain (auto-detekce podle wildcard syntaxe).
        Bez externí session — vytváří vlastní."""
        if not self._session:
            raise RuntimeError("WaybackCDX requires 'async with' context")

        # xxhash cache key
        key = xxhash.xxh64(f"wb_{url_or_domain}_{from_year}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()

        # Auto-detekce domain vs URL
        is_domain = "*." in url_or_domain or not url_or_domain.startswith("http")
        url_param = url_or_domain if is_domain else f"*.{url_or_domain}"

        params = {
            "url": url_param,
            "output": "json",
            "limit": str(limit),
            "filter": "statuscode:200",
            "from": str(from_year) + "0101",
            "fl": "original,timestamp,statuscode,mimetype",
            "collapse": "urlkey",
        }
        try:
            async with self._session.get(
                self._CDX_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 429:
                    logger.warning(f"Wayback CDX rate limit: {url_or_domain}")
                    return []
                r.raise_for_status()
                raw = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"WaybackCDX {url_or_domain}: {e}")
            return []

        if not raw or len(raw) < 2:
            return []
        # První řádek jsou headers
        headers, rows = raw[0], raw[1:]
        result = [
            dict(zip(headers, row))
            for row in rows
            if len(row) > 3 and row[3].startswith("text/") or len(row) <= 3
        ]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(result))
        return result

    async def fetch_snapshot_text(
        self,
        url: str,
        timestamp: str,
    ) -> str:
        """Stáhnout text konkrétního snapshotu pro PatternMatcher scan.
        URL format: https://web.archive.org/web/{timestamp}/{original_url}"""
        if not self._session:
            raise RuntimeError("WaybackCDX requires 'async with' context")

        wayback_url = f"https://web.archive.org/web/{timestamp}/{url}"
        try:
            async with self._session.get(
                wayback_url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"Accept": "text/html"},
            ) as r:
                if r.status != 200:
                    return ""
                return await r.text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"snapshot fetch {wayback_url}: {e}")
            return ""

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()

    async def snapshots_one_shot(
        self,
        url_or_domain: str,
        limit: int = 50,
        from_year: int = 2019,
    ) -> list[dict]:
        """One-shot CDX lookup — vytvoří a zavře vlastní session.
        USE CASE: compat layer, tests, ad-hoc volání bez externího session.
        PRO: žádné unclosed session warnings.
        """
        async with self:
            return await self.get_snapshots(url_or_domain, limit=limit, from_year=from_year)


# COMPAT: Deprecated alias pro WaybackCDX — bude odstraněn po přechodu všech call-sites
WaybackCDXClient = WaybackCDX


# =============================================================================
# COMPAT LAYER: wayback_cdx_lookup — search-shaped helper
# AUTHORITY: Tato funkce je compat wrapper kolem WaybackCDX.
# Používá se v fetch_coordinator.py pro GHOST_DEEP_RESEARCH feature.
# REMOVAL CONDITION: Po CE-001 (Sprint 8AQ?) — forward fetch_coordinator
#   na WaybackCDX.get_snapshots() přímo.
# =============================================================================


async def wayback_cdx_lookup(url_or_host: str, limit: int = 10, timeout_s: float = 8.0) -> list[dict]:
    """Compat: Wayback CDX lookup pro deep_research_sources.py call-site.
    AUTHORITY: Canonical implementation je WaybackCDX.get_snapshots().
    Tato funkce je dočasný compat wrapper — neměň její return format,
    dokud nebudou všechny call-sites přesměrovány.

    Returns:
        List of dicts s klíči: title, url, snippet, backend, rank, provider, source, timestamp
    """
    client = WaybackCDX(cache_dir="/tmp/wayback_cdx")
    snapshots = await client.snapshots_one_shot(url_or_host, limit=limit)

    out = []
    for i, rec in enumerate(snapshots[:limit], 1):
        ts = rec.get("timestamp", "")
        original_url = rec.get("original", "")
        out.append({
            "title": f"Wayback capture {ts}",
            "url": original_url,
            "snippet": f"wayback status={rec.get('statuscode', '')} mimetype={rec.get('mimetype', '')}",
            "backend": "wayback",
            "rank": i,
            "provider": "wayback_cdx",
            "source": "wayback",
            "timestamp": ts,
        })
    return out
