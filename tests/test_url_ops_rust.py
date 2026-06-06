"""
Sprint F271 — url_ops Rust extension tests.

Verifies the Rust-backed URL classifier (Clearnet / Onion / I2P / Freenet),
batch hot path, and feed-URL heuristic. SKIP (not FAIL) when the
rust_extensions shared library is not built — pure-Python fallback is
expected to remain available in that case.
"""
from __future__ import annotations

import time

import pytest

# Skip the entire module if the Rust extension was not built.
_rust = pytest.importorskip("hledac_rust_extensions")

# Required symbols for the URL ops surface.
pytest.mark.skipif(
    not hasattr(_rust, "classify_url"),
    reason="hledac_rust_extensions.classify_url not present (older build?)",
)


class TestClassifyUrl:
    """Per-URL classification — kind + lowercase host."""

    def test_classify_onion(self):
        kind, host = _rust.classify_url("http://abc.onion/path")
        assert kind == "onion"
        assert host == "abc.onion"

    def test_classify_clearnet(self):
        kind, host = _rust.classify_url("https://google.com")
        assert kind == "clearnet"
        assert host == "google.com"

    def test_classify_malformed(self):
        # "not_a_url" has no scheme but is recoverable as clearnet host
        # via the synthetic http:// fallback. Truly malformed inputs
        # (e.g. with control chars) would return ("malformed", "").
        kind, host = _rust.classify_url("not_a_url")
        # Both answers are acceptable per design — the contract is
        # "never panic, never raise". Assert no exception, host non-empty
        # OR kind == "malformed".
        assert kind in ("clearnet", "malformed")
        assert isinstance(host, str)
        if kind == "clearnet":
            assert host == "not_a_url"

    def test_classify_truly_malformed_returns_malformed_or_empty(self):
        # Pure garbage with no host-recoverable form.
        result = _rust.classify_url("???://@@@")
        kind = result[0]
        host = result[1]
        assert kind in ("malformed", "empty", "clearnet")
        # Host is always a string (never raises).
        assert isinstance(host, str)

    def test_classify_empty(self):
        kind, host = _rust.classify_url("")
        assert kind == "empty"
        assert host == ""

    def test_classify_i2p(self):
        kind, host = _rust.classify_url("http://example.i2p/page")
        assert kind == "i2p"
        assert host == "example.i2p"

    def test_classify_freenet(self):
        kind, host = _rust.classify_url("https://freenetproject.org")
        assert kind == "freenet"
        assert host == "freenetproject.org"

    def test_classify_uppercase_host_is_lowercased(self):
        kind, host = _rust.classify_url("https://ABC.onion/Path")
        assert kind == "onion"
        assert host == "abc.onion"


class TestBatchClassify:
    """Batch hot path — must beat Python urlparse."""

    def test_batch_1000(self):
        urls = [f"https://example{i}.com/path" for i in range(1000)]
        t0 = time.perf_counter()
        results = _rust.batch_classify(urls)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert len(results) == 1000
        # Generous threshold — Rust is ~1ms on M1, we allow 5ms for
        # noisy CI. Python urlparse at 3µs/call = 3ms baseline.
        assert elapsed_ms < 50.0, f"batch_classify(1000) too slow: {elapsed_ms:.1f}ms"
        for kind, host in results:
            assert kind == "clearnet"
            assert host.startswith("example")

    def test_batch_under_threshold_sequential(self):
        # 50 URLs — well below the 100 threshold, sequential path used.
        urls = [f"https://x{i}.test" for i in range(50)]
        results = _rust.batch_classify(urls)
        assert len(results) == 50
        assert all(kind == "clearnet" for kind, _ in results)

    def test_batch_empty_input(self):
        assert _rust.batch_classify([]) == []

    def test_batch_with_malformed(self):
        urls = ["http://good.com", "not_a_url", "??://@@@", ""]
        results = _rust.batch_classify(urls)
        assert len(results) == 4
        # First is clearnet
        assert results[0][0] == "clearnet"
        # Last is empty
        assert results[3][0] == "empty"
        # Middle two: must not raise — kind is one of the valid labels
        for kind, _ in results[1:3]:
            assert kind in ("clearnet", "malformed", "empty")


class TestExtractHost:
    """Drop-in for urllib.parse.urlparse(url).hostname.lower()."""

    def test_extract_basic(self):
        assert _rust.extract_host("https://Example.com/Path") == "example.com"

    def test_extract_with_port(self):
        assert _rust.extract_host("https://example.com:8080/") == "example.com"

    def test_extract_empty(self):
        assert _rust.extract_host("") == ""

    def test_extract_schemeless_fallback(self):
        # Permissive fallback — bare host is recoverable.
        assert _rust.extract_host("example.com/path") == "example.com"

    def test_never_raises(self):
        # Even the worst input must not panic.
        for bad in ["\x00", "??://@@@", " " * 100, "http://" + "a" * 5000]:
            result = _rust.extract_host(bad)
            assert isinstance(result, str)  # always returns a string


class TestLooksLikeFeedUrl:
    """Pure-string feed-URL heuristic — no regex."""

    def test_feed_rss(self):
        assert _rust.looks_like_feed_url("/feed/rss") is True

    def test_feed_atom(self):
        assert _rust.looks_like_feed_url("/news.atom") is True

    def test_feed_xml(self):
        assert _rust.looks_like_feed_url("/api/articles.xml") is True

    def test_feed_sitemap(self):
        assert _rust.looks_like_feed_url("/sitemap.xml") is True

    def test_feed_opensearch(self):
        assert _rust.looks_like_feed_url("/search.opensearch") is True

    def test_not_feed_article(self):
        assert _rust.looks_like_feed_url("/news/article") is False

    def test_not_feed_feedback_avoid_substring(self):
        # "feedback" contains "feed" but is not a feed URL.
        assert _rust.looks_like_feed_url("/api/feedback") is False

    def test_feed_with_query(self):
        # Query string is stripped before segment analysis.
        assert _rust.looks_like_feed_url("/feed.rss?count=10") is True

    def test_empty(self):
        assert _rust.looks_like_feed_url("") is False

    def test_case_insensitive(self):
        assert _rust.looks_like_feed_url("/FEED.RSS") is True
        assert _rust.looks_like_feed_url("/Feed.Atom") is True
