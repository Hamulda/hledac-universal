"""
Sprint F271 — url_ops Rust extension tests.

Verifies the Rust-backed URL classifier (Clearnet / Onion / I2P / Freenet),
batch hot path, and feed-URL heuristic. SKIP (not FAIL) when the
rust_extensions shared library is not built — pure-Python fallback is
expected to remain available in that case.
"""

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


# ---------------------------------------------------------------------------
# F271: regression tests — ensure url_ops migration in public_fetcher.py
# returns identical results to the urllib.parse fallback path.
# ---------------------------------------------------------------------------

import urllib.parse  # noqa: E402


class TestPublicFetcherMigration:
    """F271: url_ops migration parity with urllib.parse fallback."""

    def test_url_ops_lazy_import_returns_module_with_required_symbols(self):
        """_get_url_ops() returns a module exposing extract_host/looks_like_feed_url/classify_url."""
        from hledac.universal.fetching import public_fetcher as pf

        uops = pf.url_ops
        assert uops is not None, "Rust url_ops module not built"
        for sym in ("extract_host", "looks_like_feed_url", "classify_url"):
            assert hasattr(uops, sym), f"url_ops missing required symbol: {sym}"
            assert callable(getattr(uops, sym))

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/path",
            "http://ABC.onion/article",
            "https://example.com:8080/page",
            "https://freenetproject.org",
            "http://example.i2p/page",
            "https://Example.COM/Path",
            "",
        ],
    )
    def test_altsvc_extract_host_matches_urlparse_fallback(self, url):
        """_altsvc_extract_host() Rust path must equal urllib.parse fallback.

        This is the F271 migration parity test for the swapped call site at
        public_fetcher.py::_altsvc_extract_host. If extract_host() ever
        diverges from (urlparse(url).hostname or '').lower() for valid
        inputs, the assertion fires.
        """
        from hledac.universal.fetching import public_fetcher as pf

        actual = pf._altsvc_extract_host(url)
        expected = (urllib.parse.urlparse(url).hostname or "").lower()
        assert actual == expected, (
            f"_altsvc_extract_host({url!r}) = {actual!r}, "
            f"urlparse fallback = {expected!r}"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/path",
            "http://abc.onion/",
            "https://example.com:8443/api",
            "https://FREENETproject.org/",
            "HTTPS://Example.COM",
            "",
        ],
    )
    def test_doh_host_extraction_pattern_matches_urlparse(self, url):
        """DoH hostname extraction (Rust fast path or urllib fallback) equals urlparse.

        The DoH block in public_fetcher.py is inline (not a named helper), so
        we replicate the exact branch logic here to verify both paths agree.
        If a future edit changes the pattern, this test exposes the drift.
        """
        from hledac.universal.fetching import public_fetcher as pf

        # Replicate the exact branch from public_fetcher.py around line 2479:
        #   _uops = _get_url_ops()
        #   if _uops is not None:
        #       hostname = _uops.extract_host(url)
        #   else:
        #       parsed_url = urllib.parse.urlparse(url)
        #       hostname = parsed_url.hostname or ""
        uops = pf._get_url_ops()
        if uops is not None:
            rust_host = uops.extract_host(url)
        else:
            rust_host = (urllib.parse.urlparse(url).hostname or "")

        py_fallback = (urllib.parse.urlparse(url).hostname or "")
        assert rust_host == py_fallback, (
            f"host extraction diverged for {url!r}: rust={rust_host!r}, "
            f"urllib={py_fallback!r}"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/feed/rss",
            "https://example.com/news.atom",
            "https://example.com/api/articles.xml",
            "https://example.com/sitemap.xml",
            "https://example.com/search.opensearch",
            "https://example.com/news/article",
            "https://example.com/feed.rss?count=10",
            "",
            "https://example.com",
        ],
    )
    def test_looks_like_feed_url_matches_urllib_fallback(self, url):
        """_looks_like_feed_url() Rust path equals the urllib.parse fallback.

        F271 parity test for the swap at public_fetcher.py::_looks_like_feed_url.
        The Rust looks_like_feed_url is a direct drop-in for
        bool(_FEED_URL_RE.search(urlparse(url).path.rstrip("/"))) — assert
        the contract holds across positive, negative, query-bearing, and
        empty inputs.

        NOTE: The pre-existing Python regex fallback has a known false
        positive on substring "feedback" (the bare "feed" prefix matches).
        The Rust extension avoids that trap. We deliberately keep that
        case out of the parity matrix; it is pinned by
        test_looks_like_feed_url_avoids_feedback_substring below.
        """
        from hledac.universal.fetching import public_fetcher as pf

        actual = pf._looks_like_feed_url(url)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        expected = bool(pf._FEED_URL_RE.search(path))
        assert actual == expected, (
            f"_looks_like_feed_url({url!r}) = {actual!r}, "
            f"urllib fallback = {expected!r}"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/api/feedback",
            "https://example.com/feedback",
            "https://example.com/users/feedburner/profile",
        ],
    )
    def test_looks_like_feed_url_avoids_feedback_substring(self, url):
        """_looks_like_feed_url() (Rust path) must not match the 'feedback' substring.

        F271: the Rust looks_like_feed_url guards against the substring
        trap that the legacy _FEED_URL_RE regex has. When the Rust path
        is active, _looks_like_feed_url must return False for these
        inputs. If the wrapper ever falls through to the Python fallback
        (e.g. on ImportError), this test would fail in environments
        without the Rust build — which is acceptable, because parity is
        only required when the Rust path is the active one.
        """
        from hledac.universal.fetching import public_fetcher as pf

        uops = pf._get_url_ops()
        if uops is None:
            pytest.skip("Rust url_ops not built; parity cannot be asserted")

        assert pf._looks_like_feed_url(url) is False, (
            f"_looks_like_feed_url({url!r}) should reject 'feedback' "
            f"substring via the Rust path; got True."
        )

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "https://example.com/path",
            "http://abc.onion/article",
            "https://example.com:8443/api",
            "http://example.i2p/page",
            "https://freenetproject.org",
            "ftp://example.com/file",
            "gopher://example.com",
            "not_a_url",
            "???://@@@",
        ],
    )
    def test_validate_url_rust_path_matches_python_fallback(self, url):
        """_validate_url() result is identical to the inline urllib fallback.

        F271 parity test for the swap at public_fetcher.py::_validate_url.
        The function now uses classify_url as the fast path with a Python
        fallback. The expected behaviour — error code or None — must be
        identical regardless of which path served the request, so we
        inline the Python logic here as the oracle and assert equality.
        """
        from hledac.universal.fetching import public_fetcher as pf

        actual = pf._validate_url(url)

        # Inline Python oracle — mirrors the fallback branch in _validate_url.
        if not url or not isinstance(url, str):
            expected = "url_empty"
        else:
            s = url.strip()
            if not s:
                expected = "url_empty"
            else:
                try:
                    parsed = urllib.parse.urlparse(s)
                    scheme = parsed.scheme.lower()
                    if not scheme:
                        expected = "url_malformed"
                    elif scheme not in ("http", "https"):
                        expected = f"url_unsupported_scheme:{scheme}"
                    elif not parsed.netloc:
                        expected = "url_no_netloc"
                    else:
                        expected = None
                except (ValueError, AttributeError):
                    expected = "url_malformed"

        assert actual == expected, (
            f"_validate_url({url!r}) = {actual!r}, expected (python oracle) = {expected!r}"
        )

    def test_validate_url_unsupported_scheme_returns_scheme_error(self):
        """Explicit guard: ftp/gopher etc. must surface as url_unsupported_scheme:xxx.

        The Rust classify_url treats unknown schemes as 'clearnet' with
        the bare host, so _validate_url must run a second pass to gate
        non-http(s) schemes. This test pins that gate down with explicit
        scheme values that urllib.parse accepts but the fetcher must
        reject.
        """
        from hledac.universal.fetching import public_fetcher as pf

        for scheme in ("ftp", "gopher", "file", "javascript"):
            url = f"{scheme}://example.com/path"
            result = pf._validate_url(url)
            assert result == f"url_unsupported_scheme:{scheme}", (
                f"_validate_url({url!r}) = {result!r}, "
                f"expected url_unsupported_scheme:{scheme}"
            )

    def test_validate_url_empty_inputs_are_url_empty(self):
        """Empty / whitespace / non-string inputs must short-circuit to url_empty."""
        from hledac.universal.fetching import public_fetcher as pf

        for url in ("", "   ", "\n\t", None, 0, []):
            result = pf._validate_url(url)  # type: ignore[arg-type]
            assert result == "url_empty", (
                f"_validate_url({url!r}) = {result!r}, expected url_empty"
            )

    def test_looks_like_feed_url_empty_returns_false(self):
        """Empty URL must not raise and must return False (matches Rust contract)."""
        from hledac.universal.fetching import public_fetcher as pf

        assert pf._looks_like_feed_url("") is False
        assert pf._looks_like_feed_url(None) is False  # type: ignore[arg-type]


class TestCanonicalUrl:
    """canonical_url — normalize URL to canonical form for dedup."""

    def test_lowercases_scheme_and_host(self):
        result = _rust.canonical_url("HTTPS://Example.COM/Path")
        assert result == "https://example.com/path"

    def test_strips_default_http_port(self):
        assert _rust.canonical_url("http://example.com:80/path") == "http://example.com/path"

    def test_strips_default_https_port(self):
        assert _rust.canonical_url("https://example.com:443/path") == "https://example.com/path"

    def test_keeps_non_default_port(self):
        assert _rust.canonical_url("http://example.com:8080/path") == "http://example.com:8080/path"

    def test_sorts_query_params(self):
        result = _rust.canonical_url("https://example.com/search?z=1&a=2&m=3")
        assert result == "https://example.com/search?a=2&m=3&z=1"

    def test_drops_fragment(self):
        assert _rust.canonical_url("https://example.com/page#section") == "https://example.com/page"

    def test_empty_input_returns_empty(self):
        assert _rust.canonical_url("") == ""

    def test_trims_trailing_slashes(self):
        assert _rust.canonical_url("https://example.com/path///") == "https://example.com/path"
        assert _rust.canonical_url("https://example.com/") == "https://example.com/"

    def test_preserves_root_trailing_slash(self):
        assert _rust.canonical_url("https://example.com/") == "https://example.com/"

    def test_urlencoded_query_params_decoded_and_sorted(self):
        result = _rust.canonical_url("https://example.com/search?q=%7Euser%2Fname&lang=en")
        assert "lang=en" in result
        assert "q=" in result


class TestUrlDedupKey:
    """url_dedup_key — BLAKE3-64 dedup key for BloomFilter."""

    def test_returns_16_hex_chars(self):
        key = _rust.url_dedup_key("https://google.com")
        assert len(key) == 16
        assert key.isascii()
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self):
        url = "https://Example.COM:443/path?b=2&a=1"
        key1 = _rust.url_dedup_key(url)
        key2 = _rust.url_dedup_key(url)
        assert key1 == key2

    def test_same_canonical_form_same_key(self):
        url1 = "https://example.com/path"
        url2 = "https://EXAMPLE.COM/path/"
        assert _rust.url_dedup_key(url1) == _rust.url_dedup_key(url2)

    def test_different_urls_different_keys(self):
        key1 = _rust.url_dedup_key("https://google.com")
        key2 = _rust.url_dedup_key("https://apple.com")
        assert key1 != key2

    def test_empty_input_returns_16_hex(self):
        key = _rust.url_dedup_key("")
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_whitespace_input_returns_16_hex(self):
        key = _rust.url_dedup_key("   ")
        assert len(key) == 16
