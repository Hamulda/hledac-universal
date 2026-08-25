"""
Speculative Prefetch Service — BREAKTHROUGH #2 Streaming Link Prediction
=======================================================================

Provides speculative prefetching of URLs based on:
- HTML link extraction
- Streaming HTML parser for large pages
- Priority-based queue management

Features:
- Streaming HTML parsing for memory efficiency
- Priority queue for URL ordering
- mmap_delta_index for change detection

M1 8GB: Uses __slots__ for memory efficiency, streaming for large pages.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.utils.crawler_dedup import BoundedCappedSet

logger = logging.getLogger(__name__)

# Bounded URL-seen set to prevent unbounded growth on long crawls (M1 8GB).
_SEEN_URLS_CAP = 200_000


class SpeculativeConfig(Struct, frozen=True):
    """Speculative prefetch configuration. M1 8GB: msgspec.Struct for fast init."""

    max_prefetch_depth: int = 2
    max_urls_per_page: int = 100
    prefetch_concurrency: int = 10
    enable_streaming: bool = True
    html_buffer_size: int = 8192
    link_pattern: str = r'href=["\']([^"\']+)["\']'
    priority_base_score: int = 10


@dataclass(slots=True, order=True)
class URLPriorityEntry:
    """URL entry with priority for prefetch queue."""

    priority: int = field(compare=True)
    url: str = field(compare=False)
    depth: int = field(compare=False)
    source_url: str = field(compare=False)
    discovered_at: float = field(compare=False)
    estimated_size: int = field(compare=False, default=0)

    def __post_init__(self) -> None:
        if not isinstance(self.priority, int):
            self.priority = 0


class StreamingLinkExtractor:
    """
    Streaming HTML parser for link extraction.

    Extracts links from HTML without loading entire document into memory.
    M1 8GB: Streaming approach prevents memory exhaustion on large pages.

    Thread-safety: Uses threading.Lock for concurrent access.
    """

    def __init__(self, base_url: str, pattern: str | None = None, thread_safe: bool = True) -> None:
        self.base_url = base_url
        self.pattern = re.compile(pattern) if pattern else re.compile(r'href=["\']([^"\']+)["\']')
        self.links: list[str] = []
        self._buffer: str = ""
        self._in_script: bool = False
        self._in_style: bool = False
        self._tag_depth: int = 0
        self._lock = threading.Lock() if thread_safe else None

    def feed(self, chunk: bytes) -> list[str]:
        """
        Feed HTML chunk and extract discovered links.

        Returns list of new links found in this chunk.
        """
        if self._lock:
            with self._lock:
                return self._feed_impl(chunk)
        return self._feed_impl(chunk)

    def _feed_impl(self, chunk: bytes) -> list[str]:
        """Internal feed implementation (must be called with lock held)."""
        new_links: list[str] = []

        try:
            text = chunk.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            text = chunk.decode("latin-1", errors="ignore")

        self._buffer += text

        while len(self._buffer) > 1024:
            # Find potential link patterns
            for match in self.pattern.finditer(self._buffer):
                url = match.group(1)
                resolved = self._process_url(url)
                if resolved and resolved not in self.links:
                    self.links.append(resolved)
                    new_links.append(resolved)

            # Keep buffer small to avoid memory bloat
            if len(self._buffer) > 8192:
                self._buffer = self._buffer[-1024:]

        return new_links

    def _process_url(self, url: str) -> str | None:
        """Process and normalize URL."""
        if not url or url.startswith(("#", "javascript:", "mailto:", "tel:")):
            return None

        # Skip fragment-only URLs
        if url.startswith("#"):
            return None

        try:
            # Join relative URLs with base
            resolved = urljoin(self.base_url, url)
            parsed = urlparse(resolved)

            # Only HTTP/HTTPS
            if parsed.scheme not in ("http", "https"):
                return None

            return resolved.split("#")[0]
        except Exception:  # noqa: BLE001
            return None

    @property
    def all_links(self) -> list[str]:
        """Get all discovered links."""
        return list(self.links)


@dataclass(slots=True)
class SpeculativePrefetchService:
    """
    Speculative prefetch service for URL prediction.

    Implements BREAKTHROUGH #2: streaming link prediction for:
    - Fast page load with prefetched resources
    - Priority-based queue management
    - Change detection via mmap_delta_index

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    config: SpeculativeConfig = field(default_factory=SpeculativeConfig)

    _queue: asyncio.PriorityQueue[URLPriorityEntry] = field(default_factory=lambda: asyncio.PriorityQueue())
    _seen_urls: BoundedCappedSet = field(default_factory=lambda: BoundedCappedSet(maxlen=_SEEN_URLS_CAP))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _mmap_index: dict[str, float] = field(default_factory=dict)  # url -> last_seen
    _prefetch_stats: dict[str, int] = field(
        default_factory=lambda: {
            "pages_processed": 0,
            "links_found": 0,
            "links_prefetched": 0,
            "dedup_skipped": 0,
        }
    )

    def __post_init__(self) -> None:
        self._queue = asyncio.PriorityQueue()

    def compute_priority(self, url: str, depth: int, in_same_domain: bool = True) -> int:
        """
        Compute priority for URL.

        Higher priority = more important to prefetch.
        """
        base = self.config.priority_base_score

        # Same domain = +5
        domain_bonus = 5 if in_same_domain else 0

        # Lower depth = +3
        depth_bonus = max(0, (self.config.max_prefetch_depth - depth) * 3)

        # Common resource types = +2
        resource_bonus = 2 if any(ext in url.lower() for ext in [".css", ".js", ".jpg", ".png"]) else 0

        return base + domain_bonus + depth_bonus + resource_bonus

    async def extract_links_streaming(self, base_url: str, html_chunks: AsyncIterator[bytes]) -> list[str]:
        """
        Extract links from streaming HTML.

        Args:
            base_url: Base URL for resolving relative links
            html_chunks: Async iterator of HTML chunks

        Yields:
            Discovered links as they are found
        """
        extractor = StreamingLinkExtractor(base_url=base_url, pattern=self.config.link_pattern)

        async for chunk in html_chunks:
            links = extractor.feed(chunk)
            for link in links:
                yield link

    async def add_urls(self, urls: list[str], source_url: str, depth: int = 0, base_priority: int | None = None) -> int:
        """
        Add URLs to prefetch queue.

        Args:
            urls: URLs to add
            source_url: URL where these were discovered
            depth: Current crawl depth
            base_priority: Override base priority

        Returns:
            Number of URLs actually added (after dedup)
        """
        added = 0

        async with self._lock:
            for url in urls[: self.config.max_urls_per_page]:
                if url in self._seen_urls:
                    self._prefetch_stats["dedup_skipped"] += 1
                    continue

                # Compute priority
                in_same_domain = urlparse(url).netloc == urlparse(source_url).netloc
                priority = self.compute_priority(url, depth, in_same_domain)

                entry = URLPriorityEntry(
                    priority=-priority,  # Negative for max-heap behavior
                    url=url,
                    depth=depth,
                    source_url=source_url,
                    # ISSUE-10 FIX: get_running_loop().time() instead of deprecated get_event_loop().time()
                    discovered_at=asyncio.get_running_loop().time(),
                )

                await self._queue.put(entry)
                self._seen_urls.add(url)
                self._mmap_index[url] = asyncio.get_running_loop().time()
                added += 1
                self._prefetch_stats["links_found"] += 1

        return added

    async def get_next_prefetch(self) -> URLPriorityEntry | None:
        """Get next URL for prefetching."""
        if self._queue.empty():
            return None

        try:
            entry = self._queue.get_nowait()
            self._prefetch_stats["links_prefetched"] += 1
            return entry
        except asyncio.QueueEmpty:
            return None

    async def mark_processed(self, url: str) -> None:
        """Mark URL as processed."""
        async with self._lock:
            if url not in self._mmap_index:
                # ISSUE-10 FIX: get_running_loop().time() instead of deprecated get_event_loop().time()
                self._mmap_index[url] = asyncio.get_running_loop().time()

    def is_seen(self, url: str) -> bool:
        """Check if URL has been seen."""
        return url in self._seen_urls

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    def get_stats(self) -> dict[str, Any]:
        """Get prefetch statistics."""
        return {
            **self._prefetch_stats,
            "queue_size": self._queue.qsize(),
            "seen_urls_count": len(self._seen_urls),
            "index_size": len(self._mmap_index),
        }

    def reset(self) -> None:
        """Reset prefetch state."""
        # Can't clear PriorityQueue, just drain it
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._seen_urls.clear()
        self._mmap_index.clear()
        self._prefetch_stats = {
            "pages_processed": 0,
            "links_found": 0,
            "links_prefetched": 0,
            "dedup_skipped": 0,
        }

    async def aclose(self) -> None:
        """Close speculative prefetch service and release resources."""
        self.reset()
        logger.debug("SpeculativePrefetchService closed")


__all__ = [
    "SpeculativeConfig",
    "URLPriorityEntry",
    "StreamingLinkExtractor",
    "SpeculativePrefetchService",
]
