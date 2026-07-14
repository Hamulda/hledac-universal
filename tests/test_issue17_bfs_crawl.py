"""
ISSUE-017: BFS crawl engine for dark_web_intelligence.py.

Tests:
  - CrawlTask dataclass structure
  - DarkWebCrawler BFS fields present
  - _is_url_visited / _mark_url_visited dual backend (Rust vs OrderedDict)
  - BFS queue operations
  - reset_session clears BFS state
  - legacy crawl_onion_legacy still accessible
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


class TestCrawlTaskDataclass:
    """CrawlTask is immutable frozen dataclass for BFS tasks."""

    def test_crawltask_slots(self):
        from intelligence.dark_web_intelligence import CrawlTask
        t = CrawlTask(url='http://test.onion', depth=1, parent_url=None)
        assert t.url == 'http://test.onion'
        assert t.depth == 1
        assert t.parent_url is None
        # frozen = immutable
        with pytest.raises(Exception):  # frozen dataclass
            t.url = 'http://evil.onion'

    def test_crawltask_parent(self):
        from intelligence.dark_web_intelligence import CrawlTask
        t = CrawlTask(url='http://child.onion', depth=2, parent_url='http://parent.onion')
        assert t.parent_url == 'http://parent.onion'


class TestBFSFields:
    """DarkWebCrawler has BFS engine fields."""

    def test_bfs_slots_present(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        assert hasattr(crawler, '_rust_url_set')
        assert hasattr(crawler, '_bfs_queue')
        assert hasattr(crawler, '_bfs_lock')
        assert hasattr(crawler, '_bfs_sem')

    def test_bfs_queue_empty_init(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        assert crawler._bfs_queue == []

    def test_bfs_semaphore_none_before_init(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        assert crawler._bfs_sem is None  # set in initialize()


class TestURLDedupBackend:
    """Dual dedup backend: Rust MmapUrlSet or OrderedDict fallback."""

    def test_fallback_visited_urls_no_init(self):
        """Without calling initialize(), Rust URL set is None and fallback is used."""
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        # Rust not initialized yet - fallback to OrderedDict
        assert crawler._rust_url_set is None
        # OrderedDict fallback works
        assert not crawler._is_url_visited('http://test.onion')
        crawler._mark_url_visited('http://test.onion', None)
        assert crawler._is_url_visited('http://test.onion')

    def test_visited_urls_dedup(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        crawler._mark_url_visited('http://dup.onion', None)
        assert crawler._is_url_visited('http://dup.onion')


class TestBFSQueueOperations:
    """BFS queue list operations."""

    @pytest.mark.asyncio
    async def test_bfs_queue_append(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler, CrawlTask
        crawler = DarkWebCrawler()
        # Directly set semaphore to avoid needing initialize()
        crawler._bfs_sem = asyncio.Semaphore(5)
        async with crawler._bfs_lock:
            crawler._bfs_queue.append(CrawlTask(url='http://q1.onion', depth=1))
            crawler._bfs_queue.append(CrawlTask(url='http://q2.onion', depth=2))
        assert len(crawler._bfs_queue) == 2
        assert crawler._bfs_queue[0].url == 'http://q1.onion'

    @pytest.mark.asyncio
    async def test_bfs_queue_pop(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler, CrawlTask
        crawler = DarkWebCrawler()
        crawler._bfs_sem = asyncio.Semaphore(5)
        async with crawler._bfs_lock:
            crawler._bfs_queue.append(CrawlTask(url='http://pop.onion', depth=1))
        task = crawler._bfs_queue.pop(0)
        assert task.url == 'http://pop.onion'
        assert len(crawler._bfs_queue) == 0


class TestResetSession:
    """reset_session clears BFS state."""

    @pytest.mark.asyncio
    async def test_reset_clears_bfs_queue(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler, CrawlTask
        crawler = DarkWebCrawler()
        crawler._bfs_sem = asyncio.Semaphore(5)
        async with crawler._bfs_lock:
            crawler._bfs_queue.append(CrawlTask(url='http://reset.onion', depth=1))
        assert len(crawler._bfs_queue) == 1
        crawler.reset_session()
        assert len(crawler._bfs_queue) == 0


class TestLegacyCrawl:
    """Legacy crawl_onion_legacy still accessible."""

    def test_legacy_method_exists(self):
        from intelligence.dark_web_intelligence import DarkWebCrawler
        crawler = DarkWebCrawler()
        assert hasattr(crawler, 'crawl_onion_legacy')
        # async generator function (uses async def + yield)
        assert inspect.isasyncgenfunction(DarkWebCrawler.crawl_onion_legacy)
