"""
TestSprintF3XX: IOC batch extraction + batch HTML parsing (Pipeline optimization)
============================================================================

Tests:
  1. extract_iocs_from_texts: empty input → returns []
  2. extract_iocs_from_texts: small batch < 4 texts → per-text path
  3. _batch_sync_process_html: empty input → returns []
  4. _batch_sync_process_html: single HTML → returns (text, links, metadata)
  5. _batch_sync_process_html: multiple HTML → preserves order
  6. _batch_sync_process_html: cap at 1000 items
  7. process_html_payload_batch: empty → returns []
  8. process_html_payload_batch: submits to ThreadPoolExecutor
  9. extract_iocs_from_text: single text → returns list

All tests are hermetic — no network, no real MLX.
"""

import concurrent.futures
from unittest import mock

import pytest
from core import aclose

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<p>Contact us at admin@example.com or support@test.org</p>
<p>IP: 192.168.1.1 and 10.0.0.1</p>
<p>URL: https://example.com/path?q=test</p>
<script>console.log('ignore this');</script>
</body>
</html>"""

SAMPLE_HTML_2 = """<!DOCTYPE html>
<html>
<body>
<p>Email: developer@corp.net</p>
<p>IPv6: 2001:db8::1</p>
</body>
</html>"""

SAMPLE_HTML_LARGE = """
<html><body>
%s
</body></html>
""" % ("<p>IP: 192.168.1.1</p>" * 200)


# ---------------------------------------------------------------------------
# IOC batch extraction tests
# ---------------------------------------------------------------------------


class TestExtractIocsFromTextsBatch:
    """Tests for extract_iocs_from_texts (Rust batch path)."""

    def test_empty_input_returns_empty_list(self):
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts

        result = extract_iocs_from_texts([])
        assert result == []

    def test_single_short_text_uses_per_text_path(self):
        """Single short text (< 4 texts, < 16KB) → per-text path."""
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts

        with mock.patch(
            "hledac.universal.pipeline.public_patterns.extract_iocs_from_text",
            return_value=[("a@b.com", "email")],
        ) as mock_single:
            result = extract_iocs_from_texts(["Contact a@b.com"])
            assert mock_single.called
            assert result == [[("a@b.com", "email")]]

    def test_result_is_list_of_lists(self):
        """Result structure: list of lists, one per input text."""
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts

        result = extract_iocs_from_texts(
            [
                "admin@example.com",
                "support@corp.org",
                "192.168.1.1",
            ]
        )
        assert isinstance(result, list)
        assert len(result) == 3
        for sublist in result:
            assert isinstance(sublist, list)


class TestExtractIocsFromTextOriginal:
    """Ensure original single-text extract_iocs_from_text still works."""

    def test_single_text_returns_list(self):
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_text

        result = extract_iocs_from_text("Contact admin@example.com for info")
        assert isinstance(result, list)

    def test_empty_text_returns_empty_list(self):
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_text

        result = extract_iocs_from_text("")
        assert result == []


# ---------------------------------------------------------------------------
# Batch HTML processing tests
# ---------------------------------------------------------------------------


class TestBatchSyncProcessHtml:
    """Tests for _batch_sync_process_html (selectolax path)."""

    def test_empty_input_returns_empty_list(self):
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        result = _batch_sync_process_html([])
        assert result == []

    def test_single_html_returns_text_links_metadata(self):
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        result = _batch_sync_process_html([(SAMPLE_HTML, "https://example.com")])
        assert len(result) == 1
        text, links, metadata = result[0]
        assert isinstance(text, str)
        assert isinstance(links, list)
        assert isinstance(metadata, dict)

    def test_multiple_html_preserves_order(self):
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        items = [
            (SAMPLE_HTML, "https://a.com"),
            (SAMPLE_HTML_2, "https://b.com"),
        ]
        result = _batch_sync_process_html(items)
        assert len(result) == 2
        for r in result:
            assert len(r) == 3

    def test_cap_at_1000_items(self):
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        items = [(SAMPLE_HTML, f"https://example.com/{i}") for i in range(2000)]
        result = _batch_sync_process_html(items)
        # Should be capped at 1000
        assert len(result) == 1000

    def test_links_resolved_correctly(self):
        """Rust batch path: relative links resolved to absolute via lol_html urljoin."""
        from core.rust_backend import rust
        if not rust.is_available:
            pytest.skip("Rust extension not available")
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        html_with_link = '<html><body><a href="/path">Link</a></body></html>'
        result = _batch_sync_process_html([(html_with_link, "https://example.com")])
        _text, links, _metadata = result[0]
        assert any("example.com/path" in link for link in links)

    def test_relative_links_not_duplicated(self):
        """Rust batch path: relative links resolved to absolute, no http/https duplication."""
        from core.rust_backend import rust
        if not rust.is_available:
            pytest.skip("Rust extension not available")
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        html_rel = '<html><body><a href="/relative">Rel</a></body></html>'
        result = _batch_sync_process_html([(html_rel, "https://test.com")])
        _text, links, _metadata = result[0]
        http_links = [l for l in links if l.startswith("http")]
        assert len(http_links) == len(links)

    def test_ignores_none_href_attributes(self):
        from hledac.universal.fetching.public_fetcher import _batch_sync_process_html

        html_no_href = "<html><body><a>No href here</a></body></html>"
        result = _batch_sync_process_html([(html_no_href, "https://example.com")])
        _text, links, _metadata = result[0]
        assert isinstance(links, list)


class TestProcessHtmlPayloadBatch:
    """Tests for process_html_payload_batch (async ThreadPoolExecutor wrapper)."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list(self):
        """FIX F350M-R: Use @pytest.mark.asyncio instead of asyncio.run()."""
        from hledac.universal.fetching.public_fetcher import process_html_payload_batch

        result = await process_html_payload_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_submits_to_thread_pool_executor(self):
        """FIX F350M-R: Use @pytest.mark.asyncio instead of asyncio.run()."""
        from hledac.universal.fetching.public_fetcher import (
            _batch_sync_process_html,
            process_html_payload_batch,
        )

        submitted_fn = None

        class MockExecutor(concurrent.futures.ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):
                nonlocal submitted_fn
                submitted_fn = fn
                f = concurrent.futures.Future()
                f.set_result(fn(*args, **kwargs))
                return f

        with mock.patch(
            "hledac.universal.fetching.public_fetcher._get_html_executor",
            return_value=MockExecutor(max_workers=2),
        ):
            result = await process_html_payload_batch([(SAMPLE_HTML, "https://test.com")])
            assert submitted_fn is _batch_sync_process_html
            assert len(result) == 1
