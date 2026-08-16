"""
GopherCrawler — recursive Gopher directory crawler + text extractor
==================================================================




Crawls known Gopher archives, extracts text from type-0 (file) items,
and emits structured findings for DuckDB ingestion.

Bounds:
  MAX_CRAWL_DEPTH = 5         # recursion depth cap
  MAX_ITEMS_PER_HOST = 500   # per-host item cap
  MAX_TEXT_SIZE = 256 * 1024 # 256KB per text fetch
  CRAWL_TIMEOUT_S = 30        # per-request timeout
  MAX_CONCURRENT = 4          # concurrent directory fetches

Seed servers:
  gopher.floodgap.com  (port 70)
  gopher.quux.org      (port 70)
"""
import asyncio
import re
import time
from dataclasses import dataclass, field
import msgspec
from typing import Any
from hledac.universal.transport.gopher_transport import GopherItem, GopherTransport, get_gopher_transport
from hledac.universal.utils.asyncx import parallel_ok
from _core import aclose
MAX_CRAWL_DEPTH: int = 5
MAX_ITEMS_PER_HOST: int = 500
MAX_TEXT_SIZE: int = 256 * 1024
CRAWL_TIMEOUT_S: float = 30.0
MAX_CONCURRENT: int = 4
SEED_SERVERS: list[tuple[str, int]] = [('gopher.floodgap.com', 70), ('gopher.quux.org', 70)]

class GopherCrawlResult(msgspec.Struct, gc=False):
    """Result from a single gopher crawl operation."""
    host: str
    port: int
    items: list[GopherCrawlItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    crawled_at: float = field(default_factory=time.time)

class GopherCrawlItem(msgspec.Struct, frozen=True, gc=False):
    """Structured item extracted from Gopher crawl."""
    host: str
    port: int
    selector: str
    item_type: str
    display_string: str
    depth: int = 0
    text_content: str | None = None
    source_url: str = ''

    @property
    def is_directory(self) -> bool:
        return self.item_type == '1'

    @property
    def is_file(self) -> bool:
        return self.item_type == '0'

    @property
    def is_search(self) -> bool:
        return self.item_type == '7'

class GopherCrawler:
    """
    Async recursive Gopher crawler with text extraction.

    Usage:
        crawler = GopherCrawler()
        result = await crawler.crawl("gopher.floodgap.com", port=70, selector="/")
    """
    __slots__ = tuple(('_item_counts', '_max_depth', '_max_items_per_host', '_max_text_size', '_semaphore', '_timeout_s', '_transport', '_visited'))

    def __init__(self, transport: GopherTransport | None=None, max_depth: int=MAX_CRAWL_DEPTH, max_items_per_host: int=MAX_ITEMS_PER_HOST, max_text_size: int=MAX_TEXT_SIZE, timeout_s: float=CRAWL_TIMEOUT_S, max_concurrent: int=MAX_CONCURRENT) -> None:
        self._transport = transport or get_gopher_transport()
        self._max_depth = max_depth
        self._max_items_per_host = max_items_per_host
        self._max_text_size = max_text_size
        self._timeout_s = timeout_s
        from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore = get_semaphore(ConcurrencyCategory.GOPHER_LANE)
        self._visited: dict[str, frozenset[str]] = {}
        self._item_counts: dict[str, int] = {}

    def _visited_key(self, host: str, port: int, selector: str) -> str:
        return f'{host}:{port}{selector}'

    def _mark_visited(self, host: str, port: int, selector: str) -> bool:
        """Mark selector visited. Returns True if already visited."""
        key = f'{host}:{port}'
        if key not in self._visited:
            self._visited[key] = frozenset()
        if selector in self._visited[key]:
            return True
        self._visited[key] = self._visited[key] | {selector}
        return False

    def _is_host_exhausted(self, host: str) -> bool:
        return self._item_counts.get(host, 0) >= self._max_items_per_host

    def _inc_count(self, host: str, count: int=1) -> int:
        current = self._item_counts.get(host, 0)
        self._item_counts[host] = min(current + count, self._max_items_per_host)
        return self._item_counts[host]

    async def crawl(self, host: str, port: int=70, selector: str='/', depth: int=0) -> GopherCrawlResult:
        """
        Crawl a Gopher server recursively.

        Args:
            host: Gopher server hostname
            port: Gopher server port (default 70)
            selector: Starting selector (default "/")
            depth: Current recursion depth (internal)

        Returns:
            GopherCrawlResult with all discovered items
        """
        result = GopherCrawlResult(host=host, port=port)
        if depth > self._max_depth:
            result.errors.append(f'max depth {self._max_depth} reached')
            return result
        if self._is_host_exhausted(host):
            result.errors.append(f'host {host} exhausted (max {self._max_items_per_host} items)')
            return result
        if self._mark_visited(host, port, selector):
            return result
        try:
            async with asyncio.timeout(self._timeout_s + 5):
                items = await self._transport.list_directory(host, port, selector, self._timeout_s)
        except TimeoutError:
            result.errors.append(f'timeout listing {host}:{port}{selector}')
            return result
        except Exception as e:
            result.errors.append(f'error listing {host}:{port}{selector}: {e}')
            return result
        if not items:
            return result
        directory_items: list[tuple[GopherItem, int]] = []
        text_items: list[tuple[GopherCrawlItem, str]] = []  # (crawl_item, selector) for text extraction
        for item in items:
            if self._is_host_exhausted(host):
                break
            crawl_item = self._make_crawl_item(item, host, port, depth)
            result.items.append(crawl_item)
            self._inc_count(host)
            if crawl_item.is_directory and depth < self._max_depth:
                directory_items.append((item, depth + 1))
            elif crawl_item.is_file and crawl_item.item_type == '0' and crawl_item.selector:
                # Collect text items for concurrent extraction
                text_items.append((crawl_item, crawl_item.selector))
        # Fetch text content concurrently for text files
        if text_items:
            text_results = await parallel_ok(
                *[self._extract_text_preview(host, port, sel) for _, sel in text_items],
                label='gopher_crawler:text_preview',
            )
            for (crawl_item, _), text in zip(text_items, text_results):
                if isinstance(text, str):
                    crawl_item.text_content = text
        if directory_items:
            await self._crawl_directories_concurrent(directory_items, host, port, result, depth)
        return result

    async def _crawl_directories_concurrent(self, directory_items: list[tuple[GopherItem, int]], host: str, port: int, result: GopherCrawlResult, _parent_depth: int) -> None:
        """Crawl multiple directories concurrently with bounded concurrency."""

        async def crawl_dir(item: GopherItem, depth: int) -> GopherCrawlResult | None:
            async with self._semaphore:
                if self._is_host_exhausted(host):
                    return None
                return await self.crawl(host, port, item.selector, depth)
        tasks = [crawl_dir(item, depth) for item, depth in directory_items]
        sub_results = await parallel_ok(*tasks, label='gopher_crawler:214')
        for sub_result in sub_results:
            if isinstance(sub_result, Exception):
                result.errors.append(f'sub-crawl error: {sub_result}')
            elif sub_result is not None and isinstance(sub_result, GopherCrawlResult):
                result.items.extend(sub_result.items)
                result.errors.extend(sub_result.errors)

    def _make_crawl_item(self, item: GopherItem, host: str, port: int, depth: int) -> GopherCrawlItem:
        """Convert GopherItem to GopherCrawlItem. Text extraction is done separately via _extract_text_preview."""
        return GopherCrawlItem(
            host=host,
            port=port,
            selector=item.selector,
            item_type=item.item_type,
            display_string=item.display_string,
            depth=depth,
            source_url=f'gopher://{host}:{port}{item.selector}',
        )

    async def _extract_text_preview(self, host: str, port: int, selector: str) -> str | None:
        """
        Fetch text content from a type-0 gopher URL and return preview.
        Runs on thread pool to avoid blocking the event loop.
        """
        try:
            # Use to_thread to run the async transport call in a thread pool.
            # This avoids blocking the event loop while waiting for I/O.
            text = await asyncio.to_thread(
                self._transport.fetch_text,
                f'gopher://{host}:{port}{selector}',
                timeout_s=self._timeout_s,
                max_bytes=self._max_text_size,
            )
            return text
        except Exception:
            return None

    async def crawl_seed_servers(self) -> list[GopherCrawlResult]:
        """Crawl all seed servers concurrently."""
        tasks = [self.crawl(host, port, '/', depth=0) for host, port in SEED_SERVERS]
        results = await parallel_ok(*tasks, label='gopher_crawler:279')
        return [r for r in results if isinstance(r, GopherCrawlResult)]

    @staticmethod
    def items_to_findings(crawl_result: GopherCrawlResult, sprint_id: str) -> list[dict[str, Any]]:
        """
        Convert GopherCrawlResult items to CanonicalFinding-compatible dicts.
        Returns list of finding dicts ready for async_ingest_findings_batch.
        """
        findings: list[dict[str, Any]] = []
        for item in crawl_result.items:
            finding: dict[str, Any] = {'source_type': 'gopher_crawl', 'source_host': item.host, 'ioc_type': 'gopher_item', 'ioc_value': item.source_url, 'confidence': 'medium', 'sprint_id': sprint_id, 'Finding': {'selector': item.selector, 'item_type': item.item_type, 'display_string': item.display_string, 'depth': item.depth, 'text_preview': item.text_content[:500] if item.text_content else None, 'gopher_url': item.source_url, 'host': item.host, 'port': item.port}}
            domain_match = re.search('[a-zA-Z0-9]([a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9]([a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+', item.display_string)
            if domain_match:
                domain = domain_match.group(0)
                findings.append({**finding, 'ioc_type': 'domain', 'ioc_value': domain})
            findings.append(finding)
        return findings