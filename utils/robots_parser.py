"""
Robots.txt Parser - Web Crawling Rules
======================================

Integrated from hledac/utils/network/robots.py

Robots.txt parser with caching and validation.
Respects crawling rules defined by websites.

Example:
    >>> parser = RobotsParser()
    >>> async with parser:
    ...     doc = await parser.fetch_robots('https://example.com')
    ...     can_crawl = parser.can_fetch('/page', 'MyBot')
"""
import logging
import time
from dataclasses import dataclass, field
import msgspec
from urllib.parse import urlparse
logger = logging.getLogger(__name__)
_DEFAULT_TTL_SECONDS = 900
_MAX_ROBOTS_SIZE = 512 * 1024
_MAX_CACHE_SIZE = 128
_MAX_SITEMAP_SIZE = 2 * 1024 * 1024
_MAX_SITEMAP_URLS = 200

@dataclass(slots=True)
class Rule:
    """Single robots.txt rule."""
    path: str
    allow: bool
    line_no: int

@dataclass(frozen=True, slots=True)
class RobotsDocument:
    """Parsed robots.txt document."""
    fetched_at: float
    ttl: float
    rules: dict[str, list[Rule]] = field(default_factory=dict)
    sitemaps: list[str] = field(default_factory=list)
    crawl_delays: dict[str, float] = field(default_factory=dict)

class RobotsParser:
    """
    Robots.txt parser with caching and validation - M1 8GB optimized.

    Features:
    - LRU cache s limitem 128 domén (paměťové limity)
    - Reuse httpx.AsyncClient přes async context manager
    - TTL-based cache invalidation

    Example:
        >>> async with RobotsParser() as parser:
        ...     doc = await parser.fetch_robots('https://example.com')
        ...     can_crawl = parser.can_fetch('/page', 'MyBot')
    """
    __slots__ = tuple(('_cache', '_cache_access_time', '_max_cache_size', '_session', '_user_agent', 'cache_ttl'))

    def __init__(self, cache_ttl: float=_DEFAULT_TTL_SECONDS, max_cache_size: int=_MAX_CACHE_SIZE):
        """
        Initialize robots parser.

        Args:
            cache_ttl: Cache time-to-live in seconds
            max_cache_size: Maximum number of domains in cache (M1 8GB limit)
        """
        self.cache_ttl = cache_ttl
        self._max_cache_size = max_cache_size
        self._cache: dict[str, RobotsDocument] = {}
        self._cache_access_time: dict[str, float] = {}
        self._user_agent = 'Hledac-Bot/1.0'
        self._session: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RobotsParser:
        """Async context manager entry - create shared session."""
        self._session = httpx.AsyncClient(timeout=httpx.Timeout(total=10.0))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup session."""
        if self._session:
            await self._session.aclose()
            self._session = None

    def _get_cache_key(self, base_url: str) -> str:
        """Generate cache key from base URL."""
        parsed = urlparse(base_url)
        return parsed.netloc.lower()

    def _evict_oldest_if_needed(self):
        """LRU eviction: remove oldest entries if cache is full."""
        if len(self._cache) >= self._max_cache_size:
            access_times: dict[str, float] = self._cache_access_time
            oldest_key = min(access_times, key=access_times.get)
            del self._cache[oldest_key]
            del self._cache_access_time[oldest_key]
            logger.debug(f'Cache eviction: removed {oldest_key}')

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cached entry is still valid (not expired)."""
        if key not in self._cache:
            return False
        doc = self._cache[key]
        age = time.time() - doc.fetched_at
        if age > doc.ttl:
            del self._cache[key]
            if key in self._cache_access_time:
                del self._cache_access_time[key]
            return False
        self._cache_access_time[key] = time.time()
        return True

    async def fetch_robots(self, base_url: str, user_agent: str | None=None) -> RobotsDocument | None:
        """
        Fetch and parse robots.txt file with caching.

        Args:
            base_url: Base URL to fetch robots.txt from
            user_agent: Optional custom user agent

        Returns:
            Parsed robots document or None
        """
        cache_key = self._get_cache_key(base_url)
        if self._is_cache_valid(cache_key):
            logger.debug(f'Robots.txt cache hit: {cache_key}')
            return self._cache[cache_key]
        try:
            robots_url = f"{base_url.rstrip('/')}/robots.txt"
            agent = user_agent or self._user_agent
            session = self._session
            if session is None or session.is_closed:
                timeout = httpx.Timeout(total=10.0)
                close_after = True
            else:
                close_after = False
            try:
                async with session.get(robots_url, headers={'User-Agent': agent}) as response:
                    if response.status_code != 200:
                        logger.debug(f'Failed to fetch robots.txt: {response.status_code}')
                        return None
                    content = await response.text
                    if not content or len(content) > _MAX_ROBOTS_SIZE:
                        logger.warning('Robots.txt too large, ignoring')
                        return None
                    doc = self._parse_robots_content(content, robots_url)
                    self._evict_oldest_if_needed()
                    self._cache[cache_key] = doc
                    self._cache_access_time[cache_key] = time.time()
                    logger.debug(f'Robots.txt cached: {cache_key}')
                    return doc
            finally:
                if close_after and session:
                    await session.aclose()
        except Exception as e:
            logger.debug(f'Error fetching robots.txt: {e}')
            return None

    def _parse_robots_content(self, content: str, source_url: str) -> RobotsDocument:
        """Parse robots.txt content into structured format."""
        doc = RobotsDocument(fetched_at=time.time(), ttl=self.cache_ttl)
        current_agent = '*'
        line_no = 0
        for line in content.split('\n'):
            line_no += 1
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                directive, value = line.split(':', 1)
                directive = directive.strip().lower()
                value = value.strip()
                if directive == 'user-agent':
                    current_agent = value
                    if current_agent not in doc.rules:
                        doc.rules[current_agent] = []
                elif directive == 'allow':
                    if current_agent not in doc.rules:
                        doc.rules[current_agent] = []
                    doc.rules[current_agent].append(Rule(path=value, allow=True, line_no=line_no))
                elif directive == 'disallow':
                    if current_agent not in doc.rules:
                        doc.rules[current_agent] = []
                    doc.rules[current_agent].append(Rule(path=value, allow=False, line_no=line_no))
                elif directive == 'crawl-delay':
                    try:
                        delay = float(value)
                        doc.crawl_delays[current_agent] = delay
                    except ValueError:
                        pass
                elif directive == 'sitemap':
                    doc.sitemaps.append(value)
        return doc

    def can_fetch(self, path: str, user_agent: str='*', robots_doc: RobotsDocument | None=None) -> bool:
        """
        Check if path can be fetched according to robots.txt.

        Args:
            path: URL path to check
            user_agent: User agent string
            robots_doc: Optional pre-fetched robots document

        Returns:
            True if fetching is allowed
        """
        if not robots_doc:
            return True
        rules = robots_doc.rules.get(user_agent, robots_doc.rules.get('*', []))
        for rule in sorted(rules, key=lambda r: len(r.path), reverse=True):
            if path.startswith(rule.path):
                return rule.allow
        return True

    def get_crawl_delay(self, user_agent: str='*', robots_doc: RobotsDocument | None=None) -> float:
        """
        Get crawl delay for user agent.

        Args:
            user_agent: User agent string
            robots_doc: Optional pre-fetched robots document

        Returns:
            Crawl delay in seconds (0 if not specified)
        """
        if not robots_doc:
            return 0.0
        return robots_doc.crawl_delays.get(user_agent, robots_doc.crawl_delays.get('*', 0.0))

    async def fetch_sitemap(self, sitemap_url: str, max_urls: int=_MAX_SITEMAP_URLS) -> list[str]:
        """
        Fetch and parse sitemap.xml - M1 8GB optimized.

        Args:
            sitemap_url: URL of sitemap
            max_urls: Maximum URLs to extract (hard limit)

        Returns:
            List of URLs from sitemap
        """
        try:
            session = self._session
            close_after = False
            if session is None or session.is_closed:
                self._session = httpx.AsyncClient(timeout=httpx.Timeout(total=30.0))
                close_after = True
            try:
                resp = await session.get(sitemap_url)
                if resp.status_code != 200:
                    logger.debug(f'Failed to fetch sitemap: {resp.status_code}')
                    return []
                content = await resp.text
                if not content or len(content) > _MAX_SITEMAP_SIZE:
                    logger.warning('Sitemap too large, ignoring')
                    return []
                return self._parse_sitemap_content(content, max_urls)
            finally:
                if close_after and session:
                    await session.aclose()
        except Exception as e:
            logger.debug(f'Error fetching sitemap: {e}')
            return []

    def _parse_sitemap_content(self, content: str, max_urls: int) -> list[str]:
        """Parse sitemap XML and extract URLs."""
        import re
        urls = []
        pattern = '<loc>([^<]+)</loc>'
        for match in re.finditer(pattern, content, re.IGNORECASE):
            if len(urls) >= max_urls:
                logger.debug(f'Sitemap URL limit reached: {max_urls}')
                break
            url = match.group(1).strip()
            if url.startswith(('http://', 'https://')):
                urls.append(url)
        if '<sitemapindex' in content.lower():
            logger.debug('Sitemap index detected - only extracted first sitemap URLs')
        return urls

    async def fetch_sitemaps_from_robots(self, base_url: str, max_urls: int=_MAX_SITEMAP_URLS) -> list[str]:
        """
        Fetch robots.txt, extract sitemap URLs, and fetch all sitemaps.

        Args:
            base_url: Base URL of site
            max_urls: Max URLs per sitemap

        Returns:
            Combined list of URLs from all sitemaps
        """
        robots_doc = await self.fetch_robots(base_url)
        if not robots_doc or not robots_doc.sitemaps:
            return []
        all_urls = []
        for sitemap_url in robots_doc.sitemaps[:3]:
            urls = await self.fetch_sitemap(sitemap_url, max_urls)
            all_urls.extend(urls)
            if len(all_urls) >= max_urls:
                all_urls = all_urls[:max_urls]
                break
        return all_urls
__all__ = ['Rule', 'RobotsDocument', 'RobotsParser']