"""
F265C: Browser-less curl_cffi Fallback — Probe Tests
======================================================

Tests for the _JS_SKIP_HOST_RE whitelist in _needs_js_fetch() that
allows curl_cffi to fetch known non-JS-heavy CTI/news domains without
requiring Chrome/nodriver/camoufox.

Problem: threatfox.abuse.ch and bleepingcomputer.com were being flagged
as "js_required" by _needs_js_fetch() heuristics, causing unnecessary
browser launches that fail when Chrome binary is missing on M1.

Solution: Known CTI/news domains are whitelisted in _JS_SKIP_HOST_RE
and return False from _needs_js_fetch() immediately, allowing curl_cffi
to handle them without browser.
"""
from __future__ import annotations

from hledac.universal.fetching.public_fetcher import (
    _JS_SKIP_HOST_RE,
    _needs_js_fetch,
)


class TestJSSkipHostRegex:
    """Tests for the _JS_SKIP_HOST_RE regex pattern."""

    def test_js_skip_host_re_compiled(self):
        """Regex is properly compiled."""
        assert _JS_SKIP_HOST_RE is not None

    def test_threatfox_abuse_ch_matches(self):
        """threatfox.abuse.ch is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("threatfox.abuse.ch")
        assert _JS_SKIP_HOST_RE.search("www.threatfox.abuse.ch")

    def test_bleepingcomputer_com_matches(self):
        """bleepingcomputer.com is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("bleepingcomputer.com")
        assert _JS_SKIP_HOST_RE.search("www.bleepingcomputer.com")

    def test_thehackernews_com_matches(self):
        """thehackernews.com is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("thehackernews.com")
        assert _JS_SKIP_HOST_RE.search("www.thehackernews.com")

    def test_krebsonsecurity_com_matches(self):
        """krebsonsecurity.com is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("krebsonsecurity.com")

    def test_cisa_gov_matches(self):
        """cisa.gov is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("cisa.gov")
        assert _JS_SKIP_HOST_RE.search("www.cisa.gov")

    def test_urlhaus_abuse_ch_matches(self):
        """urlhaus.abuse.ch is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("urlhaus.abuse.ch")

    def test_malwarebazaar_abuse_ch_matches(self):
        """malwarebazaar.abuse.ch is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("malwarebazaar.abuse.ch")

    def test_sslbl_abuse_ch_matches(self):
        """sslbl.abuse.ch is in the skip list."""
        assert _JS_SKIP_HOST_RE.search("sslbl.abuse.ch")

    def test_unrelated_domain_does_not_match(self):
        """Random domains don't match the skip list."""
        assert not _JS_SKIP_HOST_RE.search("google.com")
        assert not _JS_SKIP_HOST_RE.search("bing.com")
        assert not _JS_SKIP_HOST_RE.search("example.com")
        assert not _JS_SKIP_HOST_RE.search("facebook.com")

    def test_subdomain_of_whitelisted_domain_matches(self):
        """Subdomain of a whitelisted domain SHOULD match (legitimate CTI subdomain)."""
        # evil.threatfox.abuse.ch still ends with threatfox.abuse.ch
        assert _JS_SKIP_HOST_RE.search("evil.threatfox.abuse.ch")
        # A domain that just contains the string isn't a match (example123.com)
        assert not _JS_SKIP_HOST_RE.search("example123.com")


class TestNeedsJSFetchWithJSSkip:
    """Tests for _needs_js_fetch() with JS_SKIP_HOST_RE whitelist."""

    def test_threatfox_returns_false_regardless_of_noscript(self):
        """threatfox.abuse.ch returns False even with <noscript> tag."""
        text_with_noscript = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
        url = "https://threatfox.abuse.ch/browse.php?search=malware"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is False, "threatfox.abuse.ch should not require JS"

    def test_bleepingcomputer_returns_false_regardless_of_noscript(self):
        """bleepingcomputer.com returns False even with <noscript> tag."""
        text_with_noscript = "<html><body><noscript>Please enable JavaScript</noscript></body></html>"
        url = "https://www.bleepingcomputer.com/search/?search=ransomware"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is False, "bleepingcomputer.com should not require JS"

    def test_thehackernews_returns_false_regardless_of_noscript(self):
        """thehackernews.com returns False even with <noscript> tag."""
        text_with_noscript = "<html><body><noscript>enable javascript</noscript></body></html>"
        url = "https://thehackernews.com/search?q=breach"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is False, "thehackernews.com should not require JS"

    def test_cti_domains_return_false_with_various_noscript_patterns(self):
        """All CTI domains return False for various noscript patterns."""
        noscript_patterns = [
            "<noscript>Enable JavaScript</noscript>",
            "<NOSCRIPT>PLEASE ENABLE JAVASCRIPT</NOSCRIPT>",
            "enable javascript to view this page",
            "<noscript class='something'>content</noscript>",
        ]
        urls = [
            ("https://threatfox.abuse.ch/", "threatfox"),
            ("https://www.bleepingcomputer.com/", "bleepingcomputer"),
            ("https://thehackernews.com/", "thehackernews"),
            ("https://krebsonsecurity.com/", "krebson"),
            ("https://www.cisa.gov/", "cisa"),
            ("https://urlhaus.abuse.ch/", "urlhaus"),
            ("https://malwarebazaar.abuse.ch/", "malwarebazaar"),
            ("https://sslbl.abuse.ch/", "sslbl"),
        ]

        for noscript in noscript_patterns:
            for url, name in urls:
                result = _needs_js_fetch(noscript, url=url)
                assert result is False, f"{name} should not require JS (noscript: {noscript[:30]}...)"

    def test_google_still_returns_true(self):
        """Google.com still triggers JS requirement (SERP heuristic)."""
        text_with_noscript = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
        url = "https://www.google.com/search?q=malware"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is True, "google.com should still require JS"

    def test_bing_still_returns_true(self):
        """Bing.com still triggers JS requirement (SERP heuristic)."""
        text_with_noscript = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
        url = "https://www.bing.com/search?q=ransomware"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is True, "bing.com should still require JS"

    def test_unknown_domain_with_noscript_returns_true(self):
        """Unknown domain with noscript still returns True."""
        text_with_noscript = "<html><body><noscript>Enable JavaScript</noscript></body></html>"
        url = "https://www.unknown-example-site.com/"

        result = _needs_js_fetch(text_with_noscript, url=url)
        assert result is True, "unknown site with noscript should require JS"

    def test_content_length_ratio_still_works(self):
        """Content-length ratio heuristic still works for non-whitelisted domains."""
        # Very small body but large declared length = JS rendered
        url = "https://www.example.com/page"
        text = "<html></html>"  # tiny body
        declared_length = 50_000  # server claimed 50KB

        result = _needs_js_fetch(text, url=url, content_length=100, declared_length=declared_length)
        assert result is True, "Content-length ratio should trigger JS need"

    def test_threatfox_ignores_content_length_ratio(self):
        """threatfox.abuse.ch ignores content-length ratio heuristic."""
        url = "https://threatfox.abuse.ch/browse.php?search=malware"
        text = "<html></html>"  # tiny body
        declared_length = 50_000  # server claimed 50KB

        result = _needs_js_fetch(text, url=url, content_length=100, declared_length=declared_length)
        assert result is False, "threatfox.abuse.ch should ignore content-length ratio"


class TestJSSkipIntegration:
    """Integration-style tests for the JS skip mechanism."""

    def test_all_cti_domains_listed_in_rescue_sources(self):
        """All CTI domains in _RESGUE_SOURCE_CANDIDATES are in JS_SKIP_HOST_RE."""
        # These are the domains used in live_public_pipeline.py
        cti_domains = [
            "threatfox.abuse.ch",
            "bleepingcomputer.com",
            "thehackernews.com",
            "krebsonsecurity.com",
            "cisa.gov",
            "id-ransomware.malwarehunterteam.com",
        ]

        for domain in cti_domains:
            # Domain should be in the skip list (with or without www prefix)
            in_skip_list = (
                _JS_SKIP_HOST_RE.search(domain) or
                _JS_SKIP_HOST_RE.search(f"www.{domain}")
            )
            assert in_skip_list, f"{domain} should be in _JS_SKIP_HOST_RE"

    def test_empty_text_does_not_crash(self):
        """Empty text doesn't cause issues."""
        result = _needs_js_fetch("", url="https://threatfox.abuse.ch/")
        # Empty text with no noscript = False
        assert result is False

    def test_none_url_still_works(self):
        """None URL doesn't crash."""
        result = _needs_js_fetch("<noscript>test</noscript>", url="")
        # Empty URL skips the host check, falls through to noscript check
        assert result is True
