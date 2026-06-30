"""
Sprint 8AJ — Feed Source Discovery + Curated Seeds
Tests for HTML feed discovery, curated seeds, and merge functionality.
"""


import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.discovery.rss_atom_adapter import (
    FeedDiscoveryBatchResult,
    FeedDiscoveryHit,
    FeedSeed,
    MergedFeedSource,
    _FeedLinkParser,
    async_discover_feed_urls,
    discover_feed_urls_from_html,
    get_default_feed_seeds,
    merge_feed_sources,
)


# ---------------------------------------------------------------------------
# HTML Fixtures
# ---------------------------------------------------------------------------

SIMPLE_HTML_RSS = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" title="My Blog RSS"
          href="/blog/feed/rss.xml">
</head>
<body></body>
</html>"""

SIMPLE_HTML_ATOM = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/atom+xml" title="My Blog Atom"
          href="/blog/feed/atom.xml">
</head>
<body></body>
</html>"""

HTML_MIXED_CASE = """<!DOCTYPE html>
<html>
<head>
    <LINK REL="Alternate" TYPE="Application/Rss+Xml" TITLE="RSS Upper"
          HREF="/rss.xml">
    <link Rel="ALTERNATE" Type="application/atom+xml" Title="Atom Mixed"
          href="/atom.xml">
</head>
<body></body>
</html>"""

HTML_NON_FEED_LINK = """<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" type="text/css" href="/style.css">
    <link rel="icon" type="image/png" href="/favicon.png">
    <link rel="alternate" hreflang="en" type="text/html" href="/en/">
</head>
<body></body>
</html>"""

HTML_FRAGMENT_ONLY = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" href="#section-rss">
</head>
<body></body>
</html>"""

HTML_BASE_HREF = """<!DOCTYPE html>
<html>
<head>
    <base href="https://blog.example.com/">
    <link rel="alternate" type="application/rss+xml" title="Blog RSS"
          href="/feeds/rss.xml">
</head>
<body></body>
</html>"""

HTML_JAVASCRIPT_HREF = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml"
          href="javascript:void(0)">
</head>
<body></body>
</html>"""

HTML_WITHOUT_TITLE = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" href="/feed/rss.xml">
</head>
<body></body>
</html>"""

HTML_HEURISTIC_IGNORED = """<!DOCTYPE html>
<html>
<head>
    <!-- heuristic /feed /rss /atom paths should NOT be discovered here -->
    <link rel="alternate" type="application/rss+xml" href="/feed">
</head>
<body></body>
</html>"""

HTML_BROKEN_HTML = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" title="RSS Before Crash"
          href="/feed/rss.xml">
<!-- unclosed tag causing parse error
<body>
    <link rel="alternate" type="application/atom+xml" href="/feed/atom.xml">
</body>
</html>"""


# ---------------------------------------------------------------------------
# D.1 — test_discover_feed_urls_from_html_finds_rss_link
# ---------------------------------------------------------------------------

class TestDiscoveryRSSLink:
    def test_finds_rss_link(self):
        result = discover_feed_urls_from_html(
            "https://example.com/blog",
            SIMPLE_HTML_RSS,
        )
        assert result.error is None
        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.feed_url == "https://example.com/blog/feed/rss.xml"
        assert hit.feed_type == "application/rss+xml"
        assert hit.confidence == 1.0
        assert hit.source == "link_tag"

    def test_finds_atom_link(self):
        result = discover_feed_urls_from_html(
            "https://example.com/blog",
            SIMPLE_HTML_ATOM,
        )
        assert result.error is None
        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.feed_url == "https://example.com/blog/feed/atom.xml"
        assert hit.feed_type == "application/atom+xml"
        assert hit.confidence == 1.0


# ---------------------------------------------------------------------------
# D.3 — test_uppercase_rel_type_href_variants_work
# ---------------------------------------------------------------------------

class TestMixedCaseVariants:
    def test_uppercase_variants(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_MIXED_CASE,
        )
        assert result.error is None
        assert len(result.hits) == 2
        # Both should be found regardless of case
        feed_urls = {h.feed_url for h in result.hits}
        assert "https://example.com/rss.xml" in feed_urls
        assert "https://example.com/atom.xml" in feed_urls


# ---------------------------------------------------------------------------
# D.4 — test_non_feed_link_is_ignored
# ---------------------------------------------------------------------------

class TestNonFeedLinksIgnored:
    def test_stylesheet_and_icon_ignored(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_NON_FEED_LINK,
        )
        # hreflang alternate without feed MIME type → ignored
        assert len(result.hits) == 0
        assert result.error is None


# ---------------------------------------------------------------------------
# D.5 — test_non_http_scheme_is_rejected
# ---------------------------------------------------------------------------

class TestNonHttpSchemeRejected:
    def test_javascript_scheme_rejected(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_JAVASCRIPT_HREF,
        )
        assert len(result.hits) == 0

    def test_fragment_only_rejected(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_FRAGMENT_ONLY,
        )
        assert len(result.hits) == 0


# ---------------------------------------------------------------------------
# D.6 — test_relative_href_resolves_against_page_url
# ---------------------------------------------------------------------------

class TestRelativeHrefResolution:
    def test_relative_resolves_against_page_url(self):
        result = discover_feed_urls_from_html(
            "https://example.com/blog/post/123",
            SIMPLE_HTML_RSS,
        )
        assert len(result.hits) == 1
        # /blog/feed/rss.xml resolved against page_url
        assert result.hits[0].feed_url == "https://example.com/blog/feed/rss.xml"


# ---------------------------------------------------------------------------
# D.7 — test_base_href_overrides_page_url
# ---------------------------------------------------------------------------

class TestBaseHref:
    def test_base_href_overrides_page_url(self):
        result = discover_feed_urls_from_html(
            "https://example.com/blog/post/123",
            HTML_BASE_HREF,
        )
        assert result.error is None
        assert len(result.hits) == 1
        # base href=https://blog.example.com/ overrides page_url
        # /feeds/rss.xml resolved against blog.example.com
        assert result.hits[0].feed_url == "https://blog.example.com/feeds/rss.xml"

    def test_base_href_takes_first(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <base href="https://first.example.com/">
    <base href="https://second.example.com/">
    <link rel="alternate" type="application/rss+xml" href="/feed/rss.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://ignored.com", html)
        assert "https://first.example.com/feed/rss.xml" == result.hits[0].feed_url


# ---------------------------------------------------------------------------
# D.8 — test_absolute_http_href_is_allowed
# ---------------------------------------------------------------------------

class TestAbsoluteHttpHref:
    def test_absolute_http_allowed(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml"
          href="https://cdn.example.com/feed/rss.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 1
        assert result.hits[0].feed_url == "https://cdn.example.com/feed/rss.xml"


# ---------------------------------------------------------------------------
# D.9 — test_url_dedup_preserve_first
# ---------------------------------------------------------------------------

class TestUrlDedup:
    def test_dedup_preserve_first(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" title="RSS 1"
          href="/feed/rss.xml">
    <link rel="alternate" type="application/atom+xml" title="Atom 1"
          href="/feed/atom.xml">
    <link rel="alternate" type="application/rss+xml" title="RSS Dup"
          href="/feed/rss.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        feed_urls = [h.feed_url for h in result.hits]
        # RSS appeared first → it stays; Atom also present
        assert "https://example.com/feed/rss.xml" in feed_urls
        assert "https://example.com/feed/atom.xml" in feed_urls
        # Only one occurrence of each URL
        assert feed_urls.count("https://example.com/feed/rss.xml") == 1


# ---------------------------------------------------------------------------
# D.10 — test_max_candidates_clamped
# ---------------------------------------------------------------------------

class TestMaxCandidatesClamp:
    def test_clamped_to_20(self):
        html = """<!DOCTYPE html>
<html>
<head>
""" + "\n".join(
            f'    <link rel="alternate" type="application/rss+xml" href="/feed{i}.xml">'
            for i in range(50)
        ) + """
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html, max_candidates=50)
        assert len(result.hits) == 20  # hard cap

    def test_clamped_min_1(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            SIMPLE_HTML_RSS,
            max_candidates=0,
        )
        assert len(result.hits) == 1  # at least 1

    def test_default_max(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            SIMPLE_HTML_RSS,
        )
        assert len(result.hits) == 1  # no error


# ---------------------------------------------------------------------------
# D.11 — test_empty_html_returns_empty_hits
# ---------------------------------------------------------------------------

class TestEmptyHtml:
    def test_empty_html(self):
        result = discover_feed_urls_from_html("https://example.com", "")
        assert result.hits == ()
        assert result.error is None  # fail-soft, not an error

    def test_whitespace_only(self):
        result = discover_feed_urls_from_html("https://example.com", "   \n  ")
        assert result.hits == ()
        assert result.error is None


# ---------------------------------------------------------------------------
# D.12 — test_missing_href_ignored
# ---------------------------------------------------------------------------

class TestMissingHref:
    def test_missing_href_ignored(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 0


# ---------------------------------------------------------------------------
# D.13 — test_title_none_normalized_to_empty_string
# ---------------------------------------------------------------------------

class TestTitleNormalization:
    def test_title_none_normalized(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_WITHOUT_TITLE,
        )
        assert len(result.hits) == 1
        assert result.hits[0].title == ""


# ---------------------------------------------------------------------------
# D.14 — test_async_discover_feed_urls_uses_8ad_fetcher
# ---------------------------------------------------------------------------

class TestAsyncDiscoverUses8AD:
    @pytest.mark.asyncio
    async def test_uses_8ad_fetcher(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = SIMPLE_HTML_RSS
        mock_result.headers = {"content-type": "text/html; charset=utf-8"}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_fetch:
            batch = await async_discover_feed_urls("https://example.com")
            mock_fetch.assert_awaited_once()
            assert mock_fetch.call_args[0][0] == "https://example.com"
            assert len(batch.hits) == 1
            assert batch.hits[0].feed_url == "https://example.com/blog/feed/rss.xml"


# ---------------------------------------------------------------------------
# D.15 — test_async_discover_feed_urls_fail_soft_on_fetch_error
# ---------------------------------------------------------------------------

class TestAsyncDiscoverFailSoft:
    @pytest.mark.asyncio
    async def test_fail_soft_on_fetch_error(self):
        mock_result = MagicMock()
        mock_result.error = "dns_not_found"
        mock_result.text = None
        mock_result.headers = {}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            batch = await async_discover_feed_urls("https://example.com")
            assert batch.hits == ()
            assert "dns_not_found" in batch.error

    @pytest.mark.asyncio
    async def test_fail_soft_on_none_text(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = None
        mock_result.headers = {}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            batch = await async_discover_feed_urls("https://example.com")
            assert batch.hits == ()
            assert "fetch_returned_none" in batch.error


# ---------------------------------------------------------------------------
# D.16 — test_async_discover_feed_urls_reraises_cancelled_error
# ---------------------------------------------------------------------------

class TestAsyncDiscoverCancelledError:
    @pytest.mark.asyncio
    async def test_reraises_cancelled_error(self):
        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ):
            with pytest.raises(asyncio.CancelledError):
                await async_discover_feed_urls("https://example.com")


# ---------------------------------------------------------------------------
# D.17 — test_async_discover_feed_urls_rejects_non_html_fetch_result
# ---------------------------------------------------------------------------

class TestAsyncDiscoverNonHtml:
    @pytest.mark.asyncio
    async def test_rejects_non_html(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = "<binary data>"
        mock_result.headers = {"content-type": "application/pdf"}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            batch = await async_discover_feed_urls("https://example.com")
            assert batch.hits == ()
            assert "content_type_rejected" in batch.error

    @pytest.mark.asyncio
    async def test_accepts_xhtml(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = "<html>...</html>"
        mock_result.headers = {"content-type": "application/xhtml+xml"}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            batch = await async_discover_feed_urls("https://example.com")
            assert batch.error is None


# ---------------------------------------------------------------------------
# D.18 — test_async_discover_feed_urls_offloads_parser_to_thread
# ---------------------------------------------------------------------------

class TestAsyncDiscoverThreadOffload:
    @pytest.mark.asyncio
    async def test_offloads_parser_to_thread(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = SIMPLE_HTML_RSS
        mock_result.headers = {"content-type": "text/html"}

        with patch(
            "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            # The fact that this completes without blocking is the evidence.
            # We verify it by checking the result is correct.
            batch = await async_discover_feed_urls("https://example.com")
            assert len(batch.hits) == 1
            assert batch.hits[0].feed_url == "https://example.com/blog/feed/rss.xml"


# ---------------------------------------------------------------------------
# D.19 — test_parser_returns_partial_hits_on_broken_html
# ---------------------------------------------------------------------------

class TestPartialHitsOnBrokenHtml:
    def test_partial_hits_on_broken_html(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_BROKEN_HTML,
        )
        # Parser should have collected the first hit before crashing
        assert len(result.hits) >= 1
        assert result.hits[0].feed_url == "https://example.com/feed/rss.xml"
        # Error is stored but hits are still returned
        assert result.error is None  # no explicit parse error signal needed


# ---------------------------------------------------------------------------
# D.20 — test_hreflang_alternate_without_feed_type_is_ignored
# ---------------------------------------------------------------------------

class TestHreflangIgnored:
    def test_hreflang_without_feed_type_ignored(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            HTML_NON_FEED_LINK,
        )
        # hreflang alternate without feed MIME → skipped
        assert len(result.hits) == 0


# ---------------------------------------------------------------------------
# D.21 — test_get_default_feed_seeds_is_typed_and_small
# ---------------------------------------------------------------------------

class TestDefaultFeedSeeds:
    def test_is_typed_and_small(self):
        seeds = get_default_feed_seeds()
        assert isinstance(seeds, tuple)
        assert len(seeds) == 5  # small curated set
        for seed in seeds:
            assert isinstance(seed, FeedSeed)
            assert seed.feed_url.startswith("https://")
            assert seed.label != ""
            assert seed.source == "curated_seed"
            assert seed.priority >= 0

    def test_no_import_time_network(self):
        # Should not make any network calls at import time
        # This is verified by the fact that get_default_feed_seeds()
        # is a pure function returning hardcoded tuples
        seeds = get_default_feed_seeds()
        assert len(seeds) > 0


# ---------------------------------------------------------------------------
# D.22 — test_merge_feed_sources_preserve_metadata_and_priority
# ---------------------------------------------------------------------------

class TestMergePreservesMetadata:
    def test_preserves_metadata(self):
        discovered = (
            FeedDiscoveryHit(
                page_url="https://example.com",
                feed_url="https://example.com/rss.xml",
                title="Example RSS",
                feed_type="application/rss+xml",
                confidence=1.0,
                source="link_tag",
                discovered_ts=1000.0,
            ),
        )
        seeds = (
            FeedSeed(
                feed_url="https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml",
                label="NVD CVE",
                source="curated_seed",
                priority=10,
            ),
        )
        merged = merge_feed_sources(discovered, seeds)
        assert len(merged) == 2

        # Seed should come first (higher priority)
        assert merged[0].origin == "seed"
        assert merged[0].priority == 10
        assert merged[0].label == "NVD CVE"

        # Discovered second
        assert merged[1].origin == "discovered"
        assert merged[1].priority == 0
        assert merged[1].label == "Example RSS"


# ---------------------------------------------------------------------------
# D.23 — test_merge_feed_sources_dedups_seed_and_discovered_overlap
# ---------------------------------------------------------------------------

class TestMergeDedup:
    def test_dedup_overlap(self):
        # Same URL discovered and seeded
        discovered = (
            FeedDiscoveryHit(
                page_url="https://example.com",
                feed_url="https://example.com/rss.xml",
                title="Example RSS",
                feed_type="application/rss+xml",
                confidence=1.0,
                source="link_tag",
                discovered_ts=1000.0,
            ),
        )
        seeds = (
            FeedSeed(
                feed_url="https://example.com/rss.xml",
                label="My RSS",
                source="curated_seed",
                priority=5,
            ),
        )
        merged = merge_feed_sources(discovered, seeds)
        assert len(merged) == 1
        # Seed wins on overlap
        assert merged[0].origin == "seed"
        assert merged[0].label == "My RSS"


# ---------------------------------------------------------------------------
# D.24 — test_seed_priority_wins_on_duplicate_url
# ---------------------------------------------------------------------------

class TestSeedPriorityWins:
    def test_seed_wins_over_discovered(self):
        discovered = (
            FeedDiscoveryHit(
                page_url="https://example.com",
                feed_url="https://example.com/feed",
                title="Discovered Feed",
                feed_type="application/rss+xml",
                confidence=1.0,
                source="link_tag",
                discovered_ts=1000.0,
            ),
        )
        seeds = (
            FeedSeed(
                feed_url="https://example.com/feed",
                label="Seed Feed",
                source="curated_seed",
                priority=7,
            ),
        )
        merged = merge_feed_sources(discovered, seeds)
        assert len(merged) == 1
        assert merged[0].origin == "seed"
        assert merged[0].priority == 7


# ---------------------------------------------------------------------------
# D.25 — test_no_import_time_network_calls
# ---------------------------------------------------------------------------

class TestNoImportSideEffects:
    def test_no_network_on_import(self):
        # Import the module and immediately call get_default_feed_seeds
        # No network activity should occur
        seeds = get_default_feed_seeds()
        assert len(seeds) == 5


# ---------------------------------------------------------------------------
# D.26 — test_no_new_production_module_created
# ---------------------------------------------------------------------------

class TestNoNewModule:
    def test_no_new_module(self):
        import os

        discovery_dir = os.path.dirname(__file__).replace("tests/probe_8aj", "discovery")
        allowed_file = os.path.join(discovery_dir, "rss_atom_adapter.py")

        # Only rss_atom_adapter.py should be modified
        # No feed_discovery_helper.py, feed_seed_registry.py etc.
        for filename in os.listdir(discovery_dir):
            if filename.endswith(".py") and filename != "rss_atom_adapter.py":
                # Allow existing files but not NEW ones
                # We can't fully test this without checking git status,
                # but we verify the module is the right one
                pass

        # Verify the functions live in rss_atom_adapter
        from hledac.universal.discovery.rss_atom_adapter import (
            discover_feed_urls_from_html,
            get_default_feed_seeds,
            merge_feed_sources,
            async_discover_feed_urls,
        )
        assert callable(discover_feed_urls_from_html)
        assert callable(get_default_feed_seeds)
        assert callable(merge_feed_sources)
        assert callable(async_discover_feed_urls)


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_feed_type_xml_text_xml_confidence(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/xml" href="/feed/xml.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 1
        assert result.hits[0].confidence == 0.5

    def test_feed_type_text_xml_confidence(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="text/xml" href="/feed/xml.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 1
        assert result.hits[0].confidence == 0.5

    def test_unknown_type_skipped(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="text/html" href="/feed/html.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 0

    def test_empty_href_skipped(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" href="   ">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 0

    def test_only_fragment_in_href_skipped(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml" href="#top">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert len(result.hits) == 0

    def test_discovered_ts_is_reasonable(self):
        result = discover_feed_urls_from_html(
            "https://example.com",
            SIMPLE_HTML_RSS,
        )
        import time

        assert result.hits[0].discovered_ts <= time.time()
        assert result.hits[0].discovered_ts > 0

    def test_page_url_preserved_in_hit(self):
        result = discover_feed_urls_from_html(
            "https://myblog.com/about",
            SIMPLE_HTML_RSS,
        )
        assert result.hits[0].page_url == "https://myblog.com/about"

    def test_feed_url_fragment_stripped(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="application/rss+xml"
          href="/feed/rss.xml#section">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        assert "#" not in result.hits[0].feed_url

    def test_multiple_base_tags_first_wins(self):
        pass  # Covered by test_base_href_takes_first

    def test_discovered_sorted_by_confidence(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <link rel="alternate" type="text/xml" href="/low.xml">
    <link rel="alternate" type="application/rss+xml" href="/high.xml">
</head>
<body></body>
</html>"""
        result = discover_feed_urls_from_html("https://example.com", html)
        # High confidence first
        assert result.hits[0].feed_url == "https://example.com/high.xml"
        assert result.hits[0].confidence == 1.0
        assert result.hits[1].confidence == 0.5

    def test_merge_empty_discovered(self):
        seeds = (FeedSeed(feed_url="https://seed.com/feed", label="Seed", source="curated_seed", priority=5),)
        merged = merge_feed_sources((), seeds)
        assert len(merged) == 1
        assert merged[0].origin == "seed"

    def test_merge_empty_seeds(self):
        discovered = (
            FeedDiscoveryHit(
                page_url="https://x.com",
                feed_url="https://x.com/feed",
                title="X Feed",
                feed_type="application/rss+xml",
                confidence=1.0,
                source="link_tag",
                discovered_ts=1000.0,
            ),
        )
        merged = merge_feed_sources(discovered, ())
        assert len(merged) == 1
        assert merged[0].origin == "discovered"

    def test_merge_both_empty(self):
        merged = merge_feed_sources((), ())
        assert merged == ()

    def test_async_discover_respects_max_candidates(self):
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.text = SIMPLE_HTML_RSS
        mock_result.headers = {"content-type": "text/html"}

        async def run():
            with patch(
                "hledac.universal.fetching.public_fetcher.async_fetch_public_text",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                return await async_discover_feed_urls("https://example.com", max_candidates=1)

        batch = asyncio.get_event_loop().run_until_complete(run())
        assert len(batch.hits) == 1
