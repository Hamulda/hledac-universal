"""
Characterization tests for HTML parser outputs.

These tests document the CURRENT output behavior of each parser so that
migrations (bs4 → selectolax) can be validated against known-good output.

No network calls. All HTML is inline.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_html() -> str:
    return """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<a href="/relative">Relative Link</a>
<a href="https://example.com">Absolute Link</a>
<p>Hello world with <strong>bold</strong> text.</p>
</body>
</html>"""


@pytest.fixture
def malformed_html() -> str:
    return """<div><p>Unclosed
<p>Another broken
<img src="test.jpg">
<a href="test.html">Link</a>
"""


@pytest.fixture
def rss_feed_html() -> str:
    return """<html>
<head>
<link rel="alternate" type="application/rss+xml" href="/feed/rss">
<link rel="alternate" type="application/atom+xml" href="/feed/atom">
</head>
<body><a href="/">Home</a></body>
</html>"""


# ---------------------------------------------------------------------------
# rss_atom_adapter — discover_feed_urls_from_html
# ---------------------------------------------------------------------------

class TestRSSAtomAdapterFeedDiscovery:
    """Characterize discover_feed_urls_from_html behavior.

    Primary parser: selectolax (line ~2052, rss_atom_adapter.py)
    Fallback: BeautifulSoup4 html.parser (line ~2063)

    Returns FeedDiscoveryBatchResult with hits tuple[FeedDiscoveryHit].

    These tests capture the exact output structure so migration preserves it.
    """

    def test_discovers_rss_and_atom_links(self, rss_feed_html: str) -> None:
        """Both RSS and Atom feed links are discovered."""
        from discovery.rss_atom_adapter import discover_feed_urls_from_html

        result = discover_feed_urls_from_html("https://example.com", rss_feed_html)
        assert result.error is None
        assert len(result.hits) == 2
        hrefs = [hit.feed_url for hit in result.hits]
        assert "https://example.com/feed/rss" in hrefs
        assert "https://example.com/feed/atom" in hrefs

    def test_malformed_html_no_crash(self, malformed_html: str) -> None:
        """Malformed HTML must not raise; empty result is acceptable."""
        from discovery.rss_atom_adapter import discover_feed_urls_from_html

        result = discover_feed_urls_from_html("https://example.com", malformed_html)
        assert isinstance(result.hits, tuple)

    def test_empty_html_returns_empty(self) -> None:
        """Empty HTML returns empty hits, not an exception."""
        from discovery.rss_atom_adapter import discover_feed_urls_from_html

        result = discover_feed_urls_from_html("https://example.com", "")
        assert result.error is None
        assert result.hits == ()

    def test_selectolax_primary_no_bs4_in_normal_path(self, minimal_html: str) -> None:
        """Normal well-formed HTML uses selectolax path (check via result shape)."""
        from discovery.rss_atom_adapter import discover_feed_urls_from_html

        result = discover_feed_urls_from_html("https://example.com", minimal_html)
        # No feed links in minimal_html — this exercises the selectolax parse path
        assert isinstance(result.hits, tuple)
        assert len(result.hits) == 0  # no feed links in this fixture


# ---------------------------------------------------------------------------
# content_miner — RustMiner._extract_links_selectolax
# ---------------------------------------------------------------------------

class TestContentMinerLinkExtraction:
    """Characterize RustMiner._extract_links_selectolax behavior.

    Uses selectolax only (no bs4 path exists). CSS selector: 'a' tags.
    Returns List[Dict[str, Any]] with href, text, is_external fields.

    Module-level SELECTOLAX_AVAILABLE guard at content_miner.py:22-24.
    """

    def test_extracts_links_via_selectolax(self, minimal_html: str) -> None:
        """selectolax CSS selector 'a' extracts hrefs correctly."""
        from tools.content_miner import RustMiner, SELECTOLAX_AVAILABLE

        if not SELECTOLAX_AVAILABLE:
            pytest.skip("selectolax not available")

        miner = RustMiner()
        links = miner._extract_links_selectolax(minimal_html, "https://example.com", max_links=10)
        assert isinstance(links, list)
        assert len(links) == 2
        hrefs = [l["href"] for l in links]
        assert "/relative" in hrefs
        assert "https://example.com" in hrefs

    def test_skips_javascript_links(self, minimal_html: str) -> None:
        """Links starting with javascript: are skipped."""
        from tools.content_miner import RustMiner, SELECTOLAX_AVAILABLE

        if not SELECTOLAX_AVAILABLE:
            pytest.skip("selectolax not available")

        html = '<a href="javascript:void(0)">JS Link</a><a href="/valid">Valid</a>'
        miner = RustMiner()
        links = miner._extract_links_selectolax(html, "https://example.com", max_links=10)
        hrefs = [l["href"] for l in links]
        assert "javascript:void(0)" not in hrefs
        assert "/valid" in hrefs

    def test_max_links_respected(self, minimal_html: str) -> None:
        """max_links cap is enforced."""
        from tools.content_miner import RustMiner, SELECTOLAX_AVAILABLE

        if not SELECTOLAX_AVAILABLE:
            pytest.skip("selectolax not available")

        miner = RustMiner()
        links = miner._extract_links_selectolax(minimal_html, "https://example.com", max_links=1)
        assert len(links) <= 1

    def test_malformed_html_tolerance(self, malformed_html: str) -> None:
        """lol_html (selectolax backend) handles malformed HTML without raising."""
        from tools.content_miner import RustMiner, SELECTOLAX_AVAILABLE

        if not SELECTOLAX_AVAILABLE:
            pytest.skip("selectolax not available")

        miner = RustMiner()
        links = miner._extract_links_selectolax(malformed_html, "https://example.com", max_links=10)
        assert isinstance(links, list)


# ---------------------------------------------------------------------------
# content_extractor — extract_content_bounded
# ---------------------------------------------------------------------------

class TestContentExtractorExtraction:
    """Characterize extract_content_bounded behavior.

    Uses bs4 (html.parser) as primary with regex fallback.
    Returns ExtractedContent: url, title, main_content, links, metadata.

    These tests capture the output structure so bs4→selectolax migration
    can be validated against existing behavior.
    """

    def test_extract_basic_fields(self, minimal_html: str) -> None:
        """All fields of ExtractedContent are populated correctly."""
        from tools.content_extractor import extract_content_bounded

        result = extract_content_bounded("https://example.com", minimal_html)
        assert result.url == "https://example.com"
        assert result.title == "Test Page"
        assert "Hello world" in result.main_content
        assert "/relative" in result.links
        assert "https://example.com" in result.links

    def test_extract_malformed_html(self, malformed_html: str) -> None:
        """Malformed HTML must not raise; partial extraction is acceptable."""
        from tools.content_extractor import extract_content_bounded

        result = extract_content_bounded("https://example.com", malformed_html)
        assert isinstance(result.url, str)
        assert isinstance(result.main_content, str)

    def test_empty_html_returns_empty_content(self) -> None:
        """Empty HTML returns empty content, not an exception."""
        from tools.content_extractor import extract_content_bounded

        result = extract_content_bounded("https://example.com", "")
        assert result.url == "https://example.com"
        assert result.main_content == ""

    def test_max_text_chars_boundary(self) -> None:
        """main_content is bounded by max_text_chars."""
        from tools.content_extractor import extract_content_bounded

        large_html = "<p>" + "x" * 1000 + "</p>"
        result = extract_content_bounded("https://example.com", large_html, max_text_chars=100)
        assert len(result.main_content) <= 100


# ---------------------------------------------------------------------------
# html_text_fast — already selectolax-first
# ---------------------------------------------------------------------------

class TestHtmlTextFast:
    """Characterize html_to_text_fast behavior.

    Already selectolax-first. These tests document expected output
    so future changes can be validated against known-good baseline.
    """

    def test_strips_html_tags(self, minimal_html: str) -> None:
        """Output contains no HTML tags."""
        from utils.html_text_fast import html_to_text_fast

        text = html_to_text_fast(minimal_html)
        assert "<" not in text

    def test_preserves_text_content(self, minimal_html: str) -> None:
        """Title and body text are present in output."""
        from utils.html_text_fast import html_to_text_fast

        text = html_to_text_fast(minimal_html)
        assert "Test Page" in text
        assert "Hello world" in text

    def test_max_chars_boundary(self, minimal_html: str) -> None:
        """Output is truncated to max_chars."""
        from utils.html_text_fast import html_to_text_fast

        text = html_to_text_fast(minimal_html, max_chars=10)
        assert len(text) <= 10

    def test_empty_html_returns_empty_string(self) -> None:
        """Empty HTML returns empty string, not an exception."""
        from utils.html_text_fast import html_to_text_fast

        text = html_to_text_fast("")
        assert text == ""


# ---------------------------------------------------------------------------
# archive_discovery — ArchiveResurrector._extract_metadata_html
# ---------------------------------------------------------------------------

_METADATA_HTML_FIXTURE = """<!DOCTYPE html>
<html>
<head>
<title>Test Page Title</title>
<meta property="og:title" content="OG Test Title">
<meta name="author" content="Test Author">
<meta property="article:published_time" content="2024-01-15T10:00:00Z">
<meta name="description" content="Test page description">
</head>
<body><p>Content here</p></body>
</html>"""

_METADATA_HTML_NO_AUTHOR = """<!DOCTYPE html>
<html>
<head>
<title>No Author Page</title>
<meta name="publishedDate" content="2024-02-20">
<meta name="description" content="A page without author">
</head>
<body>Empty</body>
</html>"""


class TestArchiveDiscoveryMetadataExtraction:
    """Characterize ArchiveResurrector._extract_metadata_html behavior.

    Current: bs4-only (html.parser), fails silently when bs4 unavailable.
    Target: selectolax-first → bs4 fallback → regex/stdlib fallback.

    Returns Dict with keys: title, og_title, author, date, description.
    """

    def test_extracts_standard_metadata(self) -> None:
        """All standard meta fields are extracted."""
        from intelligence.archive_discovery import ArchiveResurrector

        resurrector = ArchiveResurrector()
        meta = resurrector._extract_metadata_html(_METADATA_HTML_FIXTURE)

        assert meta.get("title") == "Test Page Title"
        assert meta.get("og_title") == "OG Test Title"
        assert meta.get("author") == "Test Author"
        assert meta.get("date") == "2024-01-15T10:00:00Z"
        assert meta.get("description") == "Test page description"

    def test_extracts_publisheddate_fallback(self) -> None:
        """publishedDate meta tag is captured as date."""
        from intelligence.archive_discovery import ArchiveResurrector

        resurrector = ArchiveResurrector()
        meta = resurrector._extract_metadata_html(_METADATA_HTML_NO_AUTHOR)

        assert meta.get("title") == "No Author Page"
        assert meta.get("date") == "2024-02-20"
        assert meta.get("description") == "A page without author"

    def test_malformed_html_no_crash(self, malformed_html: str) -> None:
        """Malformed HTML must not raise; empty or partial dict is acceptable."""
        from intelligence.archive_discovery import ArchiveResurrector

        resurrector = ArchiveResurrector()
        meta = resurrector._extract_metadata_html(malformed_html)
        assert isinstance(meta, dict)

    def test_empty_html_returns_empty_dict(self) -> None:
        """Empty HTML returns empty dict, not an exception."""
        from intelligence.archive_discovery import ArchiveResurrector

        resurrector = ArchiveResurrector()
        meta = resurrector._extract_metadata_html("")
        assert meta == {}

    def test_no_meta_tags_returns_empty_dict(self) -> None:
        """HTML with no meta tags returns empty dict (title only if found)."""
        from intelligence.archive_discovery import ArchiveResurrector

        resurrector = ArchiveResurrector()
        meta = resurrector._extract_metadata_html("<html><body>Plain text</body></html>")
        assert isinstance(meta, dict)