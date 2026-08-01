"""
Darknet přístup – Tor, I2P, experimentální post‑quantum crypto.
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
Socks5 proxy support via httpx-socks.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx
    import httpx_socks
logger = logging.getLogger(__name__)
try:
    from httpx_socks import AsyncProxyTransport
    HTTPX_SOCKS_AVAILABLE = True
except ImportError:
    HTTPX_SOCKS_AVAILABLE = False
    AsyncProxyTransport = None
try:
    from stem import Signal
    from stem.control import Controller
    STEM_AVAILABLE = True
except ImportError:
    STEM_AVAILABLE = False
    Signal = None
    Controller = None
try:
    import oqs
    LIBOQS_AVAILABLE = True
except ImportError:
    LIBOQS_AVAILABLE = False
    oqs = None

class DarknetConnector:
    """Connector pro darknet (Tor, I2P) a post-quantum crypto."""
    __slots__ = tuple(('_i2p_port', '_i2p_client', '_tor_client', '_tor_control_port', '_tor_port', 'tor_controller'))

    def __init__(self) -> None:
        self.tor_controller = None
        self._tor_port = 9050
        self._tor_control_port = 9051
        # SEC: I2P SOCKS5 proxy port is 7654 (not 4444 which is HTTP proxy).
        # Port 4444 would cause SOCKS handshake to fail → fallback to clearnet.
        self._i2p_port = 7654
        self._tor_client: httpx.AsyncClient | None = None
        self._i2p_client: httpx.AsyncClient | None = None

    async def ensure_tor(self) -> bool:
        """Zajistí, že Tor controller je připojen."""
        if not STEM_AVAILABLE:
            return False
        if self.tor_controller is not None:
            return True
        try:
            self.tor_controller = Controller.from_port(port=self._tor_control_port)
            self.tor_controller.authenticate()
            return True
        except Exception as e:
            logger.warning(f'[TOR] Controller failed: {e}')
            return False

    async def _get_tor_client(self) -> httpx.AsyncClient | None:
        """Get or create persistent Tor httpx client (connection reuse — httpx-socks 0.11+)."""
        if not HTTPX_SOCKS_AVAILABLE:
            return None
        if self._tor_client is None or self._tor_client.is_closed:
            import httpx
            # OPSEC-001: socks5h:// forces remote DNS resolution by Tor proxy.
            transport = AsyncProxyTransport.from_url('socks5h://127.0.0.1:9050', rdns=True)
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
            self._tor_client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(30.0),
                limits=limits,
                trust_env=False,
            )
        return self._tor_client

    async def fetch_via_tor(self, url: str) -> bytes | None:
        """Fetch URL přes Tor SOCKS proxy (persistent connection — httpx-socks 0.11+)."""
        if not HTTPX_SOCKS_AVAILABLE:
            logger.warning('[TOR] httpx-socks not available')
            return None
        client = await self._get_tor_client()
        if client is None:
            return None
        try:
            resp = await client.get(url)
            return resp.read()
        except Exception as e:
            logger.warning(f'[TOR] Fetch failed {url}: {e}')
            return None

    async def _get_i2p_client(self) -> httpx.AsyncClient | None:
        """Get or create persistent I2P httpx client (connection reuse — httpx-socks 0.11+)."""
        if not HTTPX_SOCKS_AVAILABLE:
            return None
        if self._i2p_client is None or self._i2p_client.is_closed:
            import httpx
            # OPSEC-001: socks5h:// forces remote DNS resolution by I2P proxy.
            transport = AsyncProxyTransport.from_url(f'socks5h://127.0.0.1:{self._i2p_port}', rdns=True)
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
            self._i2p_client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(30.0),
                limits=limits,
                trust_env=False,
            )
        return self._i2p_client

    async def fetch_via_i2p(self, url: str) -> bytes | None:
        """Fetch URL přes I2P SOCKS proxy (persistent connection — httpx-socks 0.11+)."""
        if not HTTPX_SOCKS_AVAILABLE:
            logger.warning('[I2P] httpx-socks not available')
            return None
        client = await self._get_i2p_client()
        if client is None:
            return None
        try:
            resp = await client.get(url)
            return resp.read()
        except Exception as e:
            logger.warning(f'[I2P] Fetch failed {url}: {e}')
            return None

    async def close(self) -> None:
        """Close persistent httpx clients."""
        for client_attr in ('_tor_client', '_i2p_client'):
            client: httpx.AsyncClient | None = getattr(self, client_attr, None)
            if client is not None and not client.is_closed:
                try:
                    await client.aclose()
                except Exception:
                    pass
        setattr(self, '_tor_client', None)
        setattr(self, '_i2p_client', None)

    async def new_tor_circuit(self) -> bool:
        """Požádá Tor o nový okruh (nová IP)."""
        if not await self.ensure_tor():
            return False
        try:
            self.tor_controller.signal(Signal.NEWNYM)
            await asyncio.sleep(2)
            return True
        except Exception as e:
            logger.warning(f'[TOR] NEWNYM failed: {e}')
            return False

    async def try_liboqs_handshake(self, host: str) -> bool:
        """Experimentální post‑quantum handshake – graceful fallback."""
        if not LIBOQS_AVAILABLE:
            logger.debug('[LIBOQS] Not installed, skipping post-quantum')
            return False
        try:
            kem = oqs.KeyEncapsulation('Kyber512')
            kem.generate_keypair()
            logger.info(f'[LIBOQS] Kyber512 available for {host}')
            return True
        except ImportError:
            logger.debug('[LIBOQS] Not installed')
            return False
        except Exception as e:
            logger.warning(f'[LIBOQS] Handshake failed: {e}')
            return False

    async def fetch_onion(self, url: str) -> dict[str, Any] | None:
        """Fetch .onion URL through Tor.

        Validates that the hostname (not full URL) ends with .onion.
        """
        try:
            host = urlparse(url).hostname or ''
            if not host.lower().endswith('.onion'):
                return None
        except Exception:
            return None
        content = await self.fetch_via_tor(url)
        if content:
            return {'url': url, 'content': content, 'via': 'tor'}
        return None

    async def fetch_i2p(self, url: str) -> dict[str, Any] | None:
        """Fetch .i2p URL through I2P.

        Validates that the hostname (not full URL) ends with .i2p.
        """
        try:
            host = urlparse(url).hostname or ''
            if not host.lower().endswith('.i2p'):
                return None
        except Exception:
            return None
        content = await self.fetch_via_i2p(url)
        if content:
            return {'url': url, 'content': content, 'via': 'i2p'}
        return None