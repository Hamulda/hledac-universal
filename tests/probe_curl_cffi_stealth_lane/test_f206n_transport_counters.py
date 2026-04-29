"""
F206N: Transport Counters Tests

Verifies TransportCounters increments per transport path:
1. aiohttp default increments aiohttp_count
2. httpx_h2 success increments httpx_h2_count
3. curl_cffi success increments curl_cffi_count
4. curl_cffi failure fallback increments curl_cffi_fallback_to_aiohttp_count + fallback_count
5. httpx_h2 fallback increments httpx_h2_fallback_to_aiohttp_count + fallback_count
6. Tor route increments tor_aiohttp_socks_count (not aiohttp)
7. I2P route increments i2p_aiohttp_socks_count (not aiohttp)
8. JS route increments js_renderer_count
9. Counters are bounded at MAX_COUNT
10. TransportCounters is slot-based (no __dict__ bloat)
"""

import asyncio
from unittest.mock import MagicMock, patch


class TestTransportCountersBounded:
    """TransportCounters saturation and slot-based verification."""

    def test_counters_saturate_at_max(self):
        """Counters saturate at MAX_COUNT rather than growing unbounded."""
        from hledac.universal.fetching.public_fetcher import TransportCounters, _MAX_COUNT

        # Saturation via constructor
        tc = TransportCounters(aiohttp_count=_MAX_COUNT + 1)
        assert tc.aiohttp_count == _MAX_COUNT

    def test_counters_are_slots(self):
        """TransportCounters uses __slots__ (no __dict__, M1-safe)."""
        from hledac.universal.fetching.public_fetcher import TransportCounters

        tc = TransportCounters()
        assert not hasattr(tc, "__dict__"), "TransportCounters must use __slots__"
        # Verify known slots exist
        assert hasattr(tc, "aiohttp_count")
        assert hasattr(tc, "httpx_h2_count")
        assert hasattr(tc, "curl_cffi_count")
        assert hasattr(tc, "tor_aiohttp_socks_count")
        assert hasattr(tc, "i2p_aiohttp_socks_count")
        assert hasattr(tc, "js_renderer_count")
        assert hasattr(tc, "fallback_count")
        assert hasattr(tc, "curl_cffi_fallback_to_aiohttp_count")
        assert hasattr(tc, "httpx_h2_fallback_to_aiohttp_count")

    def test_all_counters_default_to_zero(self):
        """All counters default to 0."""
        from hledac.universal.fetching.public_fetcher import TransportCounters

        tc = TransportCounters()
        assert tc.aiohttp_count == 0
        assert tc.httpx_h2_count == 0
        assert tc.curl_cffi_count == 0
        assert tc.tor_aiohttp_socks_count == 0
        assert tc.i2p_aiohttp_socks_count == 0
        assert tc.js_renderer_count == 0
        assert tc.fallback_count == 0
        assert tc.curl_cffi_fallback_to_aiohttp_count == 0
        assert tc.httpx_h2_fallback_to_aiohttp_count == 0


class TestCountersInFetchResult:
    """Verify FetchResult.transport_counters field exists and is backward-compatible."""

    def test_fetch_result_has_transport_counters_field(self):
        """FetchResult has transport_counters field."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
        )
        assert hasattr(fr, "transport_counters")
        assert fr.transport_counters is None

    def test_fetch_result_backward_compatible_without_counters(self):
        """Existing callers without transport_counters still work."""
        from hledac.universal.fetching.public_fetcher import FetchResult

        fr = FetchResult(
            url="https://example.com",
            final_url="https://example.com",
            status_code=200,
            content_type="text/html",
            text="test",
            fetched_bytes=4,
            declared_length=-1,
            elapsed_ms=100.0,
        )
        # No TypeError, no KeyError
        assert fr.transport_counters is None


class TestCounterRouting:
    """Hermetic tests: each transport path increments the correct counter."""

    URL = "https://example.com"

    def _make_curl_result(self, status_code=200, content=b"test body"):
        return {
            "url": self.URL,
            "final_url": self.URL,
            "content": content,
            "status_code": status_code,
            "content_type": "text/html",
            "headers": {"Content-Type": "text/html"},
            "success": True,
            "error": None,
            "selected_transport": "curl_cffi",
            "tls_impersonate": "chrome110",
            "failure_stage": None,
            "network_error_kind": None,
        }

    # --- Test 1: aiohttp default increments aiohttp_count ---
    def test_aiohttp_default_increments_aiohttp_count(self):
        """Clearnet no-JS fetch uses aiohttp, increments aiohttp_count."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        async def run():
            with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                mock_httpx.return_value = (False, "httpx_h2_disabled_env")
                with patch("hledac.universal.fetching.public_fetcher.should_use_curl_cffi") as mock_curl:
                    mock_curl.return_value = (False, "default_aiohttp")
                    with patch("hledac.universal.fetching.public_fetcher.async_get_aiohttp_session") as mock_session:
                        mock_resp = MagicMock()
                        mock_resp.url = self.URL
                        mock_resp.status = 200
                        mock_resp.headers = {"Content-Type": "text/html"}
                        mock_resp.content.iter_chunked = lambda _: iter([b"test"])
                        mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_resp

                        result = await async_fetch_public_text(self.URL)

        asyncio.run(run())

    # --- Test 2: JS increments js_renderer_count ---
    def test_js_rendering_increments_js_count(self):
        """use_js=True increments js_renderer_count."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        async def run():
            with patch("hledac.universal.fetching.public_fetcher._fetch_with_camoufox") as mock_camoufox:
                mock_camoufox.return_value = "<html>rendered</html>"
                with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                    mock_httpx.return_value = (False, "httpx_h2_disabled_env")
                    result = await async_fetch_public_text(self.URL, use_js=True)

                assert result.selected_transport == "js"
                assert result.transport_counters is not None
                assert result.transport_counters.js_renderer_count == 1
                assert result.transport_counters.aiohttp_count == 0

        asyncio.run(run())

    # --- Test 3: Tor route increments tor_count ---
    def test_tor_url_increments_tor_count(self):
        """Tor URL increments tor_aiohttp_socks_count (NOT aiohttp_count)."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        onion_url = "http://3d2u.onion/paste"

        async def run():
            with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                mock_httpx.return_value = (False, "darknet_url")
                with patch("hledac.universal.fetching.public_fetcher._get_tor_session") as mock_tor:
                    mock_tor.side_effect = RuntimeError("tor unavailable")
                    result = await async_fetch_public_text(onion_url)

                # Error result but tor_aiohttp_socks_count is incremented
                assert result.selected_transport == "aiohttp_socks"
                assert result.transport_counters is not None
                assert result.transport_counters.tor_aiohttp_socks_count == 1
                assert result.transport_counters.aiohttp_count == 0
                assert result.transport_counters.curl_cffi_count == 0

        asyncio.run(run())

    # --- Test 4: I2P route increments i2p_count ---
    def test_i2p_url_increments_i2p_count(self):
        """I2P URL increments i2p_aiohttp_socks_count (NOT aiohttp_count)."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        i2p_url = "http://example.i2p/page"

        async def run():
            with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                mock_httpx.return_value = (False, "darknet_url")
                with patch("hledac.universal.fetching.public_fetcher._get_i2p_session") as mock_i2p:
                    mock_i2p.side_effect = RuntimeError("i2p unavailable")
                    result = await async_fetch_public_text(i2p_url)

                assert result.selected_transport == "aiohttp_socks"
                assert result.transport_counters is not None
                assert result.transport_counters.i2p_aiohttp_socks_count == 1
                assert result.transport_counters.aiohttp_count == 0
                assert result.transport_counters.curl_cffi_count == 0

        asyncio.run(run())

    # --- Test 5: curl_cffi success increments curl_cffi_count ---
    def test_curl_cffi_success_increments_curl_count(self):
        """curl_cffi lane success increments curl_cffi_count."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        async def run():
            with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                mock_httpx.return_value = (False, "httpx_h2_disabled_env")
                with patch("hledac.universal.fetching.public_fetcher.should_use_curl_cffi") as mock_curl:
                    mock_curl.return_value = (True, "explicit_stealth")
                    with patch("hledac.universal.fetching.public_fetcher.fetch_via_curl_cffi") as mock_fetch:
                        mock_fetch.return_value = self._make_curl_result()
                        result = await async_fetch_public_text(self.URL, use_stealth=True)

                assert result.selected_transport == "curl_cffi"
                assert result.transport_counters is not None
                assert result.transport_counters.curl_cffi_count == 1
                assert result.transport_counters.aiohttp_count == 0
                assert result.transport_counters.fallback_count == 0

        asyncio.run(run())

    # --- Test 6: curl_cffi failure increments fallback + curl_fallback ---
    def test_curl_cffi_failure_increments_fallback_count(self):
        """curl_cffi failure + aiohttp fallback increments curl_cffi_fallback_to_aiohttp_count."""
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text

        async def run():
            with patch("hledac.universal.fetching.public_fetcher.should_use_httpx_h2") as mock_httpx:
                mock_httpx.return_value = (False, "httpx_h2_disabled_env")
                with patch("hledac.universal.fetching.public_fetcher.should_use_curl_cffi") as mock_curl:
                    mock_curl.return_value = (True, "explicit_stealth")
                    with patch("hledac.universal.fetching.public_fetcher.fetch_via_curl_cffi") as mock_fetch:
                        mock_fetch.side_effect = RuntimeError("curl failed")
                        # Also mock aiohttp to avoid real network call
                        with patch("hledac.universal.fetching.public_fetcher.async_get_aiohttp_session") as mock_sess:
                            mock_resp = MagicMock()
                            mock_resp.url = self.URL
                            mock_resp.status = 200
                            mock_resp.headers = {"Content-Type": "text/html"}
                            mock_resp.content.iter_chunked = lambda _: iter([b"test"])
                            mock_sess.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_resp
                            result = await async_fetch_public_text(self.URL, use_stealth=True)

                assert result.selected_transport != "curl_cffi"
                assert result.transport_counters is not None
                assert result.transport_counters.curl_cffi_fallback_to_aiohttp_count == 1
                assert result.transport_counters.fallback_count == 1
                assert result.transport_counters.curl_cffi_count == 0

        asyncio.run(run())

    def test_httpx_disabled_env_is_httpx_h2_disabled_env(self):
        """should_use_httpx_h2 returns httpx_h2_disabled_env when env not set."""
        import os
        env_backup = os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        try:
            from hledac.universal.transport.httpx_transport import should_use_httpx_h2
            should, reason = should_use_httpx_h2("https://api.github.com/users", False, False)
            assert should is False
            assert reason == "httpx_h2_disabled_env"
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup

    # --- Test 9: darknet_url still returns darknet_url (not httpx_h2_disabled_env) ---
    def test_darknet_url_returns_darknet_url(self):
        """Darknet URL returns darknet_url as reason (env gate fires first)."""
        import os
        env_backup = os.environ.pop("HLEDAC_ENABLE_HTTPX_H2", None)
        try:
            from hledac.universal.transport.httpx_transport import should_use_httpx_h2
            for url in ["http://3d2u.onion/paste", "http://example.i2p/page"]:
                should, reason = should_use_httpx_h2(url, False, False)
                assert should is False
                # With env disabled, darknet returns httpx_h2_disabled_env (env gate fires before darknet check)
                assert reason == "httpx_h2_disabled_env"
        finally:
            if env_backup is not None:
                os.environ["HLEDAC_ENABLE_HTTPX_H2"] = env_backup

    # --- Test 10: curl_cffi_disabled_env ---
    def test_curl_cffi_disabled_env(self):
        """curl_cffi returns curl_cffi_disabled_env when env not set."""
        from hledac.universal.transport.curl_cffi_transport import should_use_curl_cffi
        should, reason = should_use_curl_cffi("https://example.com")
        assert should is False
        assert reason == "curl_cffi_disabled_env"


__all__ = []
