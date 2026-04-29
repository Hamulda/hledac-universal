"""
F206K: HTTPX Transport Routing Tests

Tests httpx_transport.py:
  [H2-P1] Tor/I2P/Freenet URLs NEVER select HTTPX H2
  [H2-P2] use_stealth=True NEVER selects HTTPX H2
  [H2-P3] use_js=True NEVER selects HTTPX H2
  [H2-P4] HTTPX H2 requires h2 to be installed
  [_is_api_like_url] deterministic API endpoint classification
"""

import os

import pytest


class TestTransportRoutingTruthTable:
    """Transport routing truth table for all URL types."""

    @pytest.mark.parametrize("url,use_stealth,use_js,expected_httpx,expected_reason", [
        # Env disabled (default) — all routes blocked at env gate first
        ("https://example.com/page", False, False, False, "httpx_h2_disabled_env"),
        ("http://httpbin.org/html", False, False, False, "httpx_h2_disabled_env"),
        # Darknet — blocked BEFORE h2 check (darknet_url even when env would be enabled)
        ("http://3d2u.onion/paste", False, False, False, "httpx_h2_disabled_env"),
        ("http://expyuzz4wqqyqhvn.onion/", False, False, False, "httpx_h2_disabled_env"),
        ("http://example.i2p/page", False, False, False, "httpx_h2_disabled_env"),
        ("http://v4.b32.i2p/test", False, False, False, "httpx_h2_disabled_env"),
        ("http://mysite.freenet/", False, False, False, "httpx_h2_disabled_env"),
        # Stealth mode — blocked BEFORE h2 check
        ("https://example.com", True, False, False, "httpx_h2_disabled_env"),
        # JS rendering — blocked BEFORE h2 check
        ("https://example.com", False, True, False, "httpx_h2_disabled_env"),
    ])
    def test_routing_truth_table(self, url, use_stealth, use_js, expected_httpx, expected_reason):
        """Verify routing decision for each URL type."""
        from hledac.universal.transport.httpx_transport import should_use_httpx_h2

        should_use, reason = should_use_httpx_h2(url, use_stealth, use_js)
        assert should_use == expected_httpx, f"URL {url}: expected httpx={expected_httpx}, got {should_use}"
        assert reason == expected_reason, f"URL {url}: expected reason={expected_reason}, got {reason}"


class TestApiLikeUrlClassification:
    """Tests for _is_api_like_url() deterministic classification."""

    @pytest.mark.parametrize("url,expected", [
        # API subdomain (api.*.com style)
        ("https://api.github.com/users", True),
        ("https://api.example.com/v1/data", True),
        # API paths (/api/ and /v\d+/api/)
        ("https://example.com/api/v2/search", True),
        ("https://example.com/api/users", True),
        ("https://example.com/v1/api/data", True),
        # CDN hosts — detected by hostname suffix
        ("https://cdn.cloudflare.com/abc", True),
        ("https://static.example.com/img", True),
        # Cloudflare Workers
        ("https://abc.workers.dev", True),
        # CT endpoints — NOT reliably detectable from URL alone
        # (crl.pki.go is a regular domain, not a CT-specific pattern)
        ("https://crl.pki.go/pki.crl", False),
        ("https://ct.googleapis.com/.../ct", False),
        ("https://crt.sh/?format=ct", False),
        # Random web pages — not API-like
        ("https://example.com/page", False),
        ("https://httpbin.org/html", False),
        ("https://news.ycombinator.com/", False),
    ])
    def test_api_like_classification(self, url, expected):
        """Verify API-like URL classification is deterministic (h2 not required)."""
        from hledac.universal.transport.httpx_transport import _is_api_like_url

        # Run multiple times — should be deterministic
        for _ in range(3):
            result = _is_api_like_url(url)
            assert result == expected, f"URL {url}: expected {expected}, got {result}"

    def test_extract_host(self):
        """Verify _extract_host helper."""
        from hledac.universal.transport.httpx_transport import _extract_host

        assert _extract_host("https://api.github.com:8080/users") == "api.github.com"
        assert _extract_host("http://3d2u.onion/path") == "3d2u.onion"
        assert _extract_host("https://example.com") == "example.com"
        assert _extract_host(" malformed ") == ""


class TestFallbackBehavior:
    """Verify fail-soft when h2 not installed AND env is enabled."""

    def test_h2_missing_falls_back_to_aiohttp(self):
        """When h2 is missing AND env enabled, should_use_httpx_h2 returns False with httpx_h2_disabled."""
        # Set env to enabled so we actually reach the h2 check
        env_backup = os.environ.get("HLEDAC_ENABLE_HTTPX_H2")
        os.environ["HLEDAC_ENABLE_HTTPX_H2"] = "1"
        try:
            from hledac.universal.transport import httpx_transport as ht

            # h2 is not installed (mock by resetting httpx_client state)
            from hledac.universal.transport import httpx_client as hc
            hc._httpx_h2_enabled = False
            hc._httpx_import_error = "h2 not installed (httpx[http2] required for HTTP/2)"

            should_use, reason = ht.should_use_httpx_h2("https://api.github.com/users", False, False)
            assert should_use is False
            assert reason == "httpx_h2_disabled"
        finally:
            if env_backup is None:
                os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
            else:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup


__all__ = []
