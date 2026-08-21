"""
tests/test_http3_lane_dark_web_guard.py

Probe tests for:
- is_dark_web_url() detection (onion, i2p, b32.i2p, clearnet)
- fetch_http3_aioquic() early-return for dark web URLs (never attempts aioquic)
"""

from unittest import mock

import pytest


class TestIsDarkWebUrl:
    """is_dark_web_url() detection tests."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://abc123.onion/path", True),
            ("https://xyz.onion/", True),
            ("http://ABC123.ONION/", True),  # case-insensitive host
            ("http://sub.example.onion/", True),  # subdomain + onion
            ("http://example.i2p/", True),
            ("https://test.i2p/", True),
            ("http://abc.b32.i2p/", True),
            ("http://abc.B32.I2P/", True),  # case-insensitive
            ("http://example.i2p:8080/", True),
            # Negative
            ("https://google.com", False),
            ("http://example.com.onion.example.com/", False),  # domain ends in .com
            ("https://onion.city/", False),  # not a .onion TLD
            ("http://i2p.example.com/", False),  # not a .i2p TLD
            ("http://b32.i2p.xyz/", False),  # not .b32.i2p
            ("", False),
            ("not-a-url", False),
            ("http://", False),
        ],
    )
    def test_is_dark_web_url(self, url: str, expected: bool) -> None:
        # Lazy import so the test module loads even without aioquic.
        from hledac.universal.transport.http3_lane import is_dark_web_url

        result = is_dark_web_url(url)
        assert result is expected, f"is_dark_web_url({url!r}) = {result}, expected {expected}"


class TestFetchHttp3AioquicSkipsDarkWeb:
    """fetch_http3_aioquic() must return None for dark web URLs without
    attempting any aioquic operations (no socket, no handshake)."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://abc123.onion/",
            "https://xyz.onion/path?query=1",
            "http://example.i2p/",
            "http://abc.b32.i2p/",
        ],
    )
    @pytest.mark.asyncio
    async def test_skips_onion(self, url: str) -> None:
        from hledac.universal.transport.http3_lane import fetch_http3_aioquic

        # Mock _probe_aioquic to ensure it would fail if called
        with mock.patch(
            "hledac.universal.transport.http3_lane._probe_aioquic",
            return_value=True,
        ) as mock_probe:
            result = await fetch_http3_aioquic(url)
            assert result is None
            mock_probe.assert_not_called()


class TestTransportRouterDarkWebRouting:
    """transport_router already routes .onion → tor_socks, .i2p → i2p_socks.
    is_dark_web_url uses the same frozenset, so matching is consistent."""

    def test_is_dark_web_url_matches_router_suffixes(self) -> None:
        # Verify the TLD set in is_dark_web_url is consistent with what
        # the transport router would classify as dark web.
        from hledac.universal.transport.http3_lane import is_dark_web_url

        # These are the dark suffixes the router handles via tor_socks / i2p_socks
        dark_urls = [
            "http://abc123.onion/",
            "https://xyz.onion/path",
            "http://example.i2p/",
            "http://abc.b32.i2p/",
        ]
        for url in dark_urls:
            assert is_dark_web_url(url) is True, f"should detect dark web: {url}"

    def test_clearnet_not_flagged(self) -> None:
        from hledac.universal.transport.http3_lane import is_dark_web_url

        clearnet = [
            "https://google.com",
            "http://example.com/",
            "https://github.com/",
        ]
        for url in clearnet:
            assert is_dark_web_url(url) is False, f"should NOT detect as dark web: {url}"
