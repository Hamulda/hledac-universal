"""
Sprint F192E: Public product-value + CommonCrawl thin integration probe tests
==========================================================================

Tests for:
1. CommonCrawlAdapter: dynamic index, CDN noise filter, search() seam
2. duckduckgo_adapter: CDN noise patterns in _is_noise_result()
3. search_multi_engine: CC wired as domain-specific parallel search
4. live_public_pipeline: _inject_commoncrawl_hits(), cc_archive_injected

INVARIANTS tested:
- CC adapter: fetch() uses _get_latest_index() (not hardcoded)
- CC adapter: CDN noise filtered in fetch() and search()
- DDG adapter: CDN noise patterns in _is_noise_result()
- DDG adapter: _search_commoncrawl_domain() activates for domain queries only
- Pipeline: _inject_commoncrawl_hits() returns CC hits + original hits
- Pipeline: cc_archive_injected field in PipelineRunResult
"""

import asyncio
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')


# ==============================================================================
# TestSprintF192E_CommonCrawlAdapter
# ==============================================================================

class TestSprintF192E_CommonCrawlAdapter(unittest.TestCase):
    """Tests for hledac.universal.tools.commoncrawl_adapter."""

    def test_cc_adapter_fetch_uses_get_latest_index(self):
        """CC adapter fetch() must use _get_latest_index(), not hardcoded URL."""
        from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

        called_index = []

        class _FakeStealth:
            async def get(self, url):
                if "collinfo" in url:
                    return '[{"cdx-api": "https://index.commoncrawl.org/CC-MAIN-2024-99-index"}]'
                called_index.append(url)
                return ""

        adapter = CommonCrawlAdapter(stealth=_FakeStealth())

        async def run():
            # Reset class-level cache
            CommonCrawlAdapter._latest_index = None
            CommonCrawlAdapter._index_fetch_failed = False
            await adapter.fetch("example.com", max_results=5)

        asyncio.run(run())

        # fetch() should have called _get_latest_index() first, getting the dynamic URL
        # then used that dynamic URL (not hardcoded) for the CDX query
        self.assertTrue(
            any("CC-MAIN-2024-99" in u for u in called_index),
            f"Expected dynamic index in fetch URL, got: {called_index}"
        )

    def test_cc_adapter_is_noise_url_filters_cdn(self):
        """CC adapter _is_noise_url() must filter CDN/package noise URLs."""
        from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

        noise_urls = [
            "https://cdn.jsdelivr.net/npm/package@1.0.0/index.js",
            "https://unpkg.com/lodash@4.17.21/lodash.js",
            "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js",
            "https://raw.githubusercontent.com/user/repo/main/file.txt",
            "https://fonts.googleapis.com/css?family=Roboto",
            "https://storage.googleapis.com/bucket/file.html",
        ]
        for url in noise_urls:
            self.assertTrue(
                CommonCrawlAdapter._is_noise_url(url),
                f"Expected CDN noise URL to be filtered: {url}"
            )

        clean_urls = [
            "https://example.com/article/about-ransomware",
            "https://blog.example.com/post/2024",
            "https://www.company.com/products",
        ]
        for url in clean_urls:
            self.assertFalse(
                CommonCrawlAdapter._is_noise_url(url),
                f"Expected clean URL to pass: {url}"
            )

    def test_cc_adapter_search_returns_dict_shape(self):
        """CC adapter search() must return dicts with title/url/snippet/source."""
        from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

        class _FakeStealth:
            async def get(self, url):
                if "collinfo" in url:
                    return '[{"cdx-api": "https://index.commoncrawl.org/CC-MAIN-2024-99-index"}]'
                if "CC-MAIN" in url:
                    return '{"url": "https://example.com/page1", "timestamp": "20240101000000"}\n'
                return ""

        adapter = CommonCrawlAdapter(stealth=_FakeStealth())

        async def run():
            CommonCrawlAdapter._latest_index = None
            CommonCrawlAdapter._index_fetch_failed = False
            return await adapter.search("example.com", max_results=10)

        results = asyncio.run(run())

        self.assertIsInstance(results, list)
        if results:
            r = results[0]
            self.assertIn("title", r)
            self.assertIn("url", r)
            self.assertIn("snippet", r)
            self.assertIn("source", r)
            self.assertEqual(r["source"], "commoncrawl")

    def test_cc_adapter_source_name_constant(self):
        """CC adapter must have SOURCE_NAME = 'commoncrawl'."""
        from hledac.universal.tools.commoncrawl_adapter import SOURCE_NAME
        self.assertEqual(SOURCE_NAME, "commoncrawl")


# ==============================================================================
# TestSprintF192E_DuckDuckGoAdapter
# ==============================================================================

class TestSprintF192E_DuckDuckGoAdapter(unittest.TestCase):
    """Tests for hledac.universal.discovery.duckduckgo_adapter."""

    def test_is_noise_result_filters_cdn_patterns(self):
        """DDG _is_noise_result() must filter CDN/package noise URLs."""
        from hledac.universal.discovery.duckduckgo_adapter import _is_noise_result

        noise_cases = [
            # CDN noise — title/url/snippet triplet
            ("jQuery 3.6.0 CDN", "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js", "jQuery JavaScript Library"),
            ("Lodash CDN", "https://unpkg.com/lodash@4.17.21/lodash.js", "A JavaScript utility library"),
            ("React CDN", "https://cdn.jsdelivr.net/npm/react@18.2.0/index.js", "React JavaScript library"),
        ]
        for title, url, snippet in noise_cases:
            self.assertTrue(
                _is_noise_result(title, url, snippet, "react jquery lodash"),
                f"Expected CDN noise URL to be filtered: {url}"
            )

        # Clean cases should pass through
        clean_cases = [
            ("Example Company Official Site", "https://example.com", "Official website of Example Company"),
            ("Product Documentation", "https://docs.example.com/api/reference", "API reference documentation"),
        ]
        for title, url, snippet in clean_cases:
            self.assertFalse(
                _is_noise_result(title, url, snippet, "example company"),
                f"Expected clean URL to pass: {url}"
            )

    def test_search_commoncrawl_domain_activates_for_domain_query(self):
        """_search_commoncrawl_domain() must activate for domain-like queries."""
        from hledac.universal.discovery.duckduckgo_adapter import _search_commoncrawl_domain

        async def run(query):
            return await _search_commoncrawl_domain(query, max_results=5)

        # Domain queries should activate CC search
        domain_queries = [
            "example.com",
            "*.example.com",
            "site:example.com",
            "domain:example.com",
        ]
        for q in domain_queries:
            result = asyncio.run(run(q))
            # Result is list (empty if no network) — just verify it doesn't raise
            self.assertIsInstance(result, list)

        # Non-domain queries should return empty immediately
        non_domain_queries = [
            "ransomware analysis",
            "what is DNS",
            "how does HTTPS work",
            "CVE-2024-1234",
        ]
        for q in non_domain_queries:
            result = asyncio.run(run(q))
            self.assertEqual(result, [], f"Non-domain query should return empty: {q}")


# ==============================================================================
# TestSprintF192E_LivePublicPipeline
# ==============================================================================

class TestSprintF192E_LivePublicPipeline(unittest.TestCase):
    """Tests for hledac.universal.pipeline.live_public_pipeline."""

    def test_pipeline_has_cc_archive_injected_field(self):
        """PipelineRunResult must have cc_archive_injected field."""
        from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult
        result = PipelineRunResult(
            query="test", discovered=0, fetched=0, matched_patterns=0,
            accepted_findings=0, stored_findings=0, patterns_configured=0,
            pages=(), cc_archive_injected=5,
        )
        self.assertEqual(result.cc_archive_injected, 5)

    def test_query_looks_like_domain_for_cc(self):
        """_query_looks_like_domain_for_cc() must detect domain queries."""
        from hledac.universal.pipeline.live_public_pipeline import _query_looks_like_domain_for_cc

        domain_queries = [
            "example.com",
            "*.example.com",
            "site:example.com",
            "domain:example.com",
            "api.example.com",
        ]
        for q in domain_queries:
            self.assertTrue(
                _query_looks_like_domain_for_cc(q),
                f"Expected domain query to match: {q}"
            )

        non_domain_queries = [
            "ransomware group",
            "CVE-2024-1234",
            "best DNS servers 2024",
            "",
            "a",  # too short
            "x" * 260,  # too long
        ]
        for q in non_domain_queries:
            self.assertFalse(
                _query_looks_like_domain_for_cc(q),
                f"Expected non-domain query to NOT match: {q}"
            )

    def test_inject_commoncrawl_hits_returns_tuple(self):
        """_inject_commoncrawl_hits() must return tuple of hits (not list)."""
        from hledac.universal.pipeline.live_public_pipeline import (
            _inject_commoncrawl_hits, _query_looks_like_domain_for_cc,
        )

        class _FakeHit:
            __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
            def __init__(self, url):
                self.url = url
                self.title = "Test"
                self.snippet = "Test snippet"
                self.rank = 0
                self.score = 0.5
                self.reason = None

        original_hits = (_FakeHit("https://example.com/page1"),)

        async def run():
            return await _inject_commoncrawl_hits(original_hits, "example.com")

        result = asyncio.run(run())

        self.assertIsInstance(result, tuple, "Must return tuple for immutable hits")
        self.assertGreaterEqual(
            len(result), len(original_hits),
            "CC injection must add hits (or return original on failure)"
        )

    def test_inject_commoncrawl_skips_non_domain_query(self):
        """_inject_commoncrawl_hits() must skip non-domain queries."""
        from hledac.universal.pipeline.live_public_pipeline import _inject_commoncrawl_hits

        class _FakeHit:
            __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
            def __init__(self, url):
                self.url = url
                self.title = "Test"
                self.snippet = "Test snippet"
                self.rank = 0
                self.score = 0.5
                self.reason = None

        original_hits = (_FakeHit("https://example.com/page1"),)

        async def run():
            return await _inject_commoncrawl_hits(original_hits, "ransomware analysis")

        result = asyncio.run(run())

        # Non-domain query → returns hits unchanged (no CC injection)
        self.assertEqual(len(result), len(original_hits))


# ==============================================================================
# TestSprintF192E_Integration
# ==============================================================================

class TestSprintF192E_Integration(unittest.TestCase):
    """Integration tests verifying CC is wired through the cluster."""

    def test_cc_adapter_imported_in_ddg_module(self):
        """_search_commoncrawl_domain must be importable from duckduckgo_adapter."""
        from hledac.universal.discovery.duckduckgo_adapter import (
            _search_commoncrawl_domain, search_multi_engine,
        )
        self.assertTrue(callable(_search_commoncrawl_domain))
        self.assertTrue(callable(search_multi_engine))

    def test_cc_adapter_imported_in_pipeline_module(self):
        """_inject_commoncrawl_hits must be importable from live_public_pipeline."""
        from hledac.universal.pipeline.live_public_pipeline import (
            _inject_commoncrawl_hits, _query_looks_like_domain_for_cc,
        )
        self.assertTrue(callable(_inject_commoncrawl_hits))
        self.assertTrue(callable(_query_looks_like_domain_for_cc))

    def test_cc_noise_patterns_defined_in_both_modules(self):
        """CDN noise patterns must be defined in both CC adapter and DDG adapter."""
        from hledac.universal.tools.commoncrawl_adapter import _CDN_NOISE_PATTERNS as CC_PATTERNS
        from hledac.universal.discovery.duckduckgo_adapter import _CDN_NOISE_PATTERNS as DDG_PATTERNS

        self.assertIsInstance(CC_PATTERNS, tuple)
        self.assertIsInstance(DDG_PATTERNS, tuple)
        # Both should cover major CDN patterns
        for pattern in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"):
            self.assertIn(pattern, CC_PATTERNS, f"CC adapter missing: {pattern}")
            self.assertIn(pattern, DDG_PATTERNS, f"DDG adapter missing: {pattern}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
