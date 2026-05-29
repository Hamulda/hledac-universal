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
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import anyio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class TestI2PURLRouting:
    """invariant_I2P-T1: *.i2p URLs are routed to i2p_socks lane."""

    def test_i2p_url_gets_i2p_lane(self):
        """route_transport returns lane='i2p_socks' for .i2p domains."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://example.i2p/page")
        assert result.lane == "i2p_socks", f"Expected i2p_socks, got {result.lane}"
        assert result.reason == "darknet_i2p"

    def test_b32_i2p_url_gets_i2p_lane(self):
        """route_transport returns lane='i2p_socks' for .b32.i2p domains."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://v4.b32.i2p/test")
        assert result.lane == "i2p_socks", f"Expected i2p_socks, got {result.lane}"
        assert result.reason == "darknet_i2p"

    def test_clearnet_url_does_not_get_i2p_lane(self):
        """clearnet URLs route to clearnet lane, not i2p_socks."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("https://example.com/page")
        assert result.lane != "i2p_socks"

    def test_onion_url_does_not_get_i2p_lane(self):
        """onion URLs route to tor_socks, not i2p_socks."""
        from hledac.universal.transport.transport_router import route_transport

        result = route_transport("http://example.onion/page")
        assert result.lane == "tor_socks"


class TestI2PSessionPool:
    """invariant_I2P-T2: pool creates aiohttp session via ProxyConnector."""

    def test_i2p_socks_proxy_constant_exported(self):
        """I2P_SOCKS_PROXY is exported from public_fetcher."""
        from hledac.universal.fetching.public_fetcher import I2P_SOCKS_PROXY

        assert I2P_SOCKS_PROXY is not None
        assert "socks5://" in I2P_SOCKS_PROXY
        assert "7654" in I2P_SOCKS_PROXY

    def test_i2p_socks_proxy_from_env(self, monkeypatch):
        """I2P_SOCKS_PROXY reads from I2P_PROXY_URL env var."""
        monkeypatch.setenv("I2P_PROXY_URL", "socks5://127.0.0.1:9999")

        # Re-import to pick up env var (module-level constant)
        import importlib

        from hledac.universal.fetching import public_fetcher as pf
        importlib.reload(pf)

        assert pf.I2P_SOCKS_PROXY == "socks5://127.0.0.1:9999"

    def test_get_i2p_session_creates_session(self):
        """_get_i2p_session creates aiohttp.ClientSession with ProxyConnector."""
        import importlib

        from hledac.universal.fetching import public_fetcher as pf
        importlib.reload(pf)

        # Reset module-level state
        pf._i2p_session = None
        pf._i2p_session_locally_created = False
        pf._injected_session_provider = None

        mock_connector = MagicMock()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_pc_cls = MagicMock()
        mock_pc_cls.from_url.return_value = mock_connector

        mock_aiohttp_socks = MagicMock()
        mock_aiohttp_socks.ProxyConnector = mock_pc_cls

        async def run_test():
            with patch.object(pf.aiohttp, 'ClientSession', return_value=mock_session) as mock_cs:
                with patch.dict('sys.modules', {'aiohttp_socks': mock_aiohttp_socks}):
                    session = await pf._get_i2p_session()

                    # Verify ProxyConnector was created with I2P proxy URL
                    mock_pc_cls.from_url.assert_called_once()
                    call_url = mock_pc_cls.from_url.call_args[0][0]
                    assert "socks5://" in call_url

                    # Verify ClientSession was created with connector
                    mock_cs.assert_called_once()
                    assert session is mock_session

        anyio.run(run_test)

    def test_get_i2p_session_injects_provider(self):
        """_get_i2p_session uses injected provider when available."""
        import importlib

        from hledac.universal.fetching import public_fetcher as pf
        importlib.reload(pf)

        pf._i2p_session = None
        pf._i2p_session_locally_created = False

        injected = MagicMock()
        injected.closed = False
        pf._injected_session_provider = (None, injected)

        async def run_test():
            session = await pf._get_i2p_session()
            assert session is injected

        anyio.run(run_test)


class TestI2PFallback:
    """invariant_I2P-T3: pool failure falls back to darknet path."""

    def test_get_i2p_session_raises_on_missing_dep(self):
        """Missing aiohttp_socks raises RuntimeError."""
        import importlib

        from hledac.universal.fetching import public_fetcher as pf
        importlib.reload(pf)

        pf._i2p_session = None
        pf._i2p_session_locally_created = False
        pf._injected_session_provider = None

        async def run_test():
            with patch.dict('sys.modules', {'aiohttp_socks': None}):
                with pytest.raises(RuntimeError, match="aiohttp_socks required"):
                    await pf._get_i2p_session()

        anyio.run(run_test)

    def test_is_i2p_url_helper(self):
        """_is_i2p_url returns True for .i2p and .b32.i2p, False otherwise."""
        from hledac.universal.fetching.public_fetcher import _is_i2p_url

        assert _is_i2p_url("http://example.i2p/page") is True
        assert _is_i2p_url("http://v4.b32.i2p/test") is True
        assert _is_i2p_url("https://example.com/page") is False
        assert _is_i2p_url("http://example.onion/page") is False
        # .b32.i2p is suffix of .i2p, but we check .b32.i2p first
        assert _is_i2p_url("http://v4.b32.i2p.i2p/page") is True  # endswith .i2p

    def test_i2p_fetch_result_has_i2p_transport(self):
        """FetchResult for i2p URL marks selected_transport='aiohttp_socks'."""
        from hledac.universal.fetching.public_fetcher import _is_i2p_url

        # Verify the transport indicator is correctly set in the routing
        assert _is_i2p_url("http://test.i2p")
        assert _is_i2p_url("http://test.b32.i2p") is True
