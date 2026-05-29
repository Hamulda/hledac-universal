"""
Darknet přístup – Tor, I2P, experimentální post‑quantum crypto.
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
"""

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# aiohttp_socks for Tor/I2P proxy support
try:
    from aiohttp_socks import ProxyConnector, ProxyType
    AIOHTTP_SOCKS_AVAILABLE = True
except ImportError:
    AIOHTTP_SOCKS_AVAILABLE = False
    ProxyConnector = None
    ProxyType = None

# Stem for Tor control
try:
    from stem import Signal
    from stem.control import Controller
    STEM_AVAILABLE = True
except ImportError:
    STEM_AVAILABLE = False
    Signal = None
    Controller = None

# liboqs for post-quantum crypto
try:
    import oqs
    LIBOQS_AVAILABLE = True
except ImportError:
    LIBOQS_AVAILABLE = False
    oqs = None


class DarknetConnector:
    """Connector pro darknet (Tor, I2P) a post-quantum crypto."""

    def __init__(self):
        self.tor_controller = None
        self._tor_port = 9050
        self._tor_control_port = 9051
        self._i2p_port = 4444

    async def ensure_tor(self):
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
            logger.warning(f"[TOR] Controller failed: {e}")
            return False

    async def fetch_via_tor(self, url: str) -> bytes | None:
        """Fetch URL přes Tor SOCKS proxy."""
        if not AIOHTTP_SOCKS_AVAILABLE:
            logger.warning("[TOR] aiohttp_socks not available")
            return None

        try:
            import aiohttp
            connector = ProxyConnector.from_url('socks5://127.0.0.1:9050')
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    return await resp.read()
        except Exception as e:
            logger.warning(f"[TOR] Fetch failed {url}: {e}")
            return None

    async def fetch_via_i2p(self, url: str) -> bytes | None:
        """Fetch URL přes I2P SOCKS proxy."""
        if not AIOHTTP_SOCKS_AVAILABLE:
            logger.warning("[I2P] aiohttp_socks not available")
            return None

        try:
            import aiohttp
            connector = ProxyConnector.from_url(f'socks5://127.0.0.1:{self._i2p_port}')
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    return await resp.read()
        except Exception as e:
            logger.warning(f"[I2P] Fetch failed {url}: {e}")
            return None

    async def new_tor_circuit(self) -> bool:
        """Požádá Tor o nový okruh (nová IP)."""
        if not await self.ensure_tor():
            return False
        try:
            self.tor_controller.signal(Signal.NEWNYM)
            await asyncio.sleep(2)  # Wait for new circuit
            return True
        except Exception as e:
            logger.warning(f"[TOR] NEWNYM failed: {e}")
            return False

    async def try_liboqs_handshake(self, host: str) -> bool:
        """Experimentální post‑quantum handshake – graceful fallback."""
        if not LIBOQS_AVAILABLE:
            logger.debug("[LIBOQS] Not installed, skipping post-quantum")
            return False

        try:
            kem = oqs.KeyEncapsulation('Kyber512')
            kem.generate_keypair()
            logger.info(f"[LIBOQS] Kyber512 available for {host}")
            return True
        except ImportError:
            logger.debug("[LIBOQS] Not installed")
            return False
        except Exception as e:
            logger.warning(f"[LIBOQS] Handshake failed: {e}")
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
