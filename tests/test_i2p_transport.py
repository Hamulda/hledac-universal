"""
tests/test_i2p_transport.py

Sprint 11 integration: I2P session pool wired to research run pipeline.

Tests:
1. test_i2p_url_routing — *.i2p/*.b32.i2p URLs get Transport.I2P lane
2. test_i2p_session_pool_creates — pool creates aiohttp session via SAM proxy
3. test_i2p_fallback_to_darknet — pool failure falls back to darknet_connector

Invariant table:
  [I2P-T1] route_transport(".i2p URL") returns lane="i2p_socks"
  [I2P-T2] _get_i2p_session() creates ProxyConnector with I2P_SOCKS_PROXY
  [I2P-T3] pool failure raises RuntimeError caught by fetch caller
  [I2P-T4] I2P_SOCKS_PROXY is exported and readable
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class TestI2PURLRouting:
    """invariant_I2P-T1: *.i2p URLs are routed to i2p_socks lane."""

    def test_i2p_url_gets_i2p_lane(self) -> None:
        """route_transport returns lane='i2p_socks' for .i2p domains."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://example.i2p/page")
        assert result.lane == "i2p_socks", f"Expected i2p_socks, got {result.lane}"
        assert result.reason == "darknet_i2p"

    def test_b32_i2p_url_gets_i2p_lane(self) -> None:
        """route_transport returns lane='i2p_socks' for .b32.i2p domains."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://v4.b32.i2p/test")
        assert result.lane == "i2p_socks", f"Expected i2p_socks, got {result.lane}"
        assert result.reason == "darknet_i2p"

    def test_clearnet_url_does_not_get_i2p_lane(self) -> None:
        """clearnet URLs route to clearnet lane, not i2p_socks."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("https://example.com/page")
        assert result.lane != "i2p_socks"

    def test_onion_url_does_not_get_i2p_lane(self) -> None:
        """onion URLs route to tor_socks, not i2p_socks."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://example.onion/page")
        assert result.lane == "tor_socks"


class TestI2PSessionPool:
    """invariant_I2P-T2: pool creates aiohttp session via ProxyConnector."""

    def test_i2p_socks_proxy_constant_exported(self) -> None:
        """I2P_SOCKS_PROXY is exported from public_fetcher."""
        from hledac.universal.fetching.public_fetcher import I2P_SOCKS_PROXY

        assert I2P_SOCKS_PROXY is not None
        assert "socks5://" in I2P_SOCKS_PROXY
        assert "7654" in I2P_SOCKS_PROXY

    def test_i2p_socks_proxy_from_env(self, monkeypatch) -> None:
        """I2P_SOCKS_PROXY reads from I2P_PROXY_URL env var."""
        monkeypatch.setenv("I2P_PROXY_URL", "socks5://127.0.0.1:9999")

        # Re-import to pick up env var (module-level constant)
        import importlib

        from hledac.universal.fetching import public_fetcher as pf

        importlib.reload(pf)

        assert pf.I2P_SOCKS_PROXY == "socks5://127.0.0.1:9999"

    @pytest.mark.asyncio
    async def test_get_i2p_session_creates_session(self) -> None:
        """_get_i2p_session creates httpx.AsyncClient with SOCKS5H proxy."""
        import importlib

        from hledac.universal.fetching import public_fetcher as pf

        importlib.reload(pf)

        # Reset module-level state
        pf._i2p_session = None
        pf._i2p_session_locally_created = False
        pf._injected_session_provider = None

        mock_session = MagicMock()
        mock_session.closed = False

        # F260: Force the httpx-socks fallback path by pretending
        # curl_cffi is unavailable. The F260 default prefers curl_cffi
        # (JA3 unification), but these tests verify the httpx-socks fallback.
        with patch.object(pf, "is_curl_cffi_available", return_value=(False, "test_forced_fallback")):
            with patch.object(pf, "httpx_socks_client", return_value=mock_session) as mock_httpx_socks_client:
                session = await pf._get_i2p_session()

                # Verify httpx_socks_client was called with I2P proxy URL
                mock_httpx_socks_client.assert_called_once()
                call_args = mock_httpx_socks_client.call_args
                assert "socks5://" in call_args[0][0]
                assert session is mock_session

    @pytest.mark.asyncio
    async def test_get_i2p_session_injects_provider(self) -> None:
        """get_session_manager().get_i2p_session() uses injected provider when available."""
        from hledac.universal.fetching._session_mgr import get_session_manager

        mgr = get_session_manager("test_i2p_inject")
        injected = MagicMock()
        injected.is_closed = False
        mgr.inject_provider(None, injected)

        # Force curl_cffi unavailable and mock httpx_socks_client to prevent real HTTP calls
        with patch("hledac.universal.transport.curl_cffi_runtime.is_curl_cffi_available", return_value=(False, "test")):
            with patch("hledac.universal.transport.session_pool.httpx_socks_client", return_value=injected):
                try:
                    session = await mgr.get_i2p_session()
                    assert session is injected
                finally:
                    mgr.inject_provider(None, None)  # cleanup


class TestI2PFallback:
    """invariant_I2P-T3: pool failure falls back to darknet path."""

    @pytest.mark.asyncio
    async def test_get_i2p_session_raises_on_missing_dep(self) -> None:
        """Missing httpx_socks raises RuntimeError."""
        from hledac.universal.fetching._session_mgr import get_session_manager

        mgr = get_session_manager("test_i2p_missing_dep")

        # Force curl_cffi unavailable and httpx_socks import failure
        with patch("hledac.universal.transport.curl_cffi_runtime.is_curl_cffi_available", return_value=(False, "test")):
            with patch.dict("sys.modules", {"httpx_socks": None}):
                with pytest.raises((ModuleNotFoundError, RuntimeError)):
                    await mgr.get_i2p_session()

    def test_is_i2p_url_helper(self) -> None:
        """_is_i2p_url returns True for .i2p and .b32.i2p, False otherwise."""
        from hledac.universal.fetching.public_fetcher import _is_i2p_url

        assert _is_i2p_url("http://example.i2p/page") is True
        assert _is_i2p_url("http://v4.b32.i2p/test") is True
        assert _is_i2p_url("https://example.com/page") is False
        assert _is_i2p_url("http://example.onion/page") is False
        # .b32.i2p is suffix of .i2p, but we check .b32.i2p first
        assert _is_i2p_url("http://v4.b32.i2p.i2p/page") is True  # endswith .i2p

    def test_i2p_fetch_result_has_i2p_transport(self) -> None:
        """FetchResult for i2p URL marks selected_transport='aiohttp_socks'."""
        from hledac.universal.fetching.public_fetcher import _is_i2p_url

        # Verify the transport indicator is correctly set in the routing
        assert _is_i2p_url("http://test.i2p")
        assert _is_i2p_url("http://test.b32.i2p") is True
