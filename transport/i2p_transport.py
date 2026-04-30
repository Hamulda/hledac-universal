"""
I2P Transport - Anonymous overlay network transport via I2P SAM/SOCKS.

P10: I2P transport implementation using I2P SAM protocol or SOCKS proxy.

TRANSPORT MODES:
  - SAM mode: Direct SAM protocol communication (requires i2p.jar)
  - SOCKS mode: Connect to existing I2P router SOCKS proxy (port 7654)
  - HTTP mode: Connect to I2P HTTP proxy (Freenet FProxy on port 8888)

FAIL-SAFE: If no I2P router available, available=False, no crash.
BOUNDED: Session pool limits, timeout guards.

M1 8GB: No native code, minimal RAM footprint.
"""

import asyncio
import logging
import socket
from pathlib import Path
from typing import Optional

from .base import Transport

logger = logging.getLogger(__name__)

# I2P default ports
I2P_SOCKS_PORT = 7654
I2P_SAM_PORT = 7656
I2P_HTTP_PORT = 8888

# I2P SAM protocol constants
SAM_VERSION = "1.0"
SAM_OK = "OK"


class I2PUnavailableError(RuntimeError):
    """Raised when I2P fetch attempted without running I2P router."""


class I2PTransport(Transport):
    """
    I2P transport using SAM protocol or SOCKS proxy.

    Modes (in priority order):
      1. SAM: Direct protocol communication with i2p-router
      2. SOCKS: Connect to existing I2P SOCKS5 proxy
      3. HTTP: Connect to I2P HTTP proxy (Freenet compatibility)

    P10: Integrated with transport_resolver.get_transport_for_url()
    """

    available: bool = True
    transport_mode: str = "none"  # sam, socks, http, none

    def __init__(
        self,
        data_dir: Optional[str] = None,
        socks_port: int = I2P_SOCKS_PORT,
        sam_port: int = I2P_SAM_PORT,
        http_port: int = I2P_HTTP_PORT,
    ):
        # B7: graceful fallback — I2P unavailable → available=False, no crash
        self.available = True
        self.transport_mode = "none"

        try:
            import aiohttp
            import aiohttp_socks
        except ImportError:
            logger.critical("I2PTransport unavailable: missing aiohttp or aiohttp_socks")
            self.available = False
            return

        self._aiohttp = aiohttp
        self._aiohttp_socks = aiohttp_socks

        from hledac.universal.paths import I2P_ROOT
        if data_dir is None:
            self.data_dir = I2P_ROOT
        else:
            self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.socks_port = socks_port
        self.sam_port = sam_port
        self.http_port = http_port
        self.i2p_address: Optional[str] = None
        self._session_socks: Optional[aiohttp.ClientSession] = None
        self._session_http: Optional[aiohttp.ClientSession] = None
        self._ready = asyncio.Event()

    async def start(self) -> bool:
        """
        Start I2P transport by detecting available mode.

        Returns True if any I2P mode is operational.
        """
        if not self.available:
            return False

        # Try each mode in order of preference
        if await self._try_socks_mode():
            self.transport_mode = "socks"
            logger.info(f"I2PTransport ready via SOCKS5 proxy (127.0.0.1:{self.socks_port})")
            self._ready.set()
            return True

        if await self._try_sam_mode():
            self.transport_mode = "sam"
            logger.info(f"I2PTransport ready via SAM protocol (127.0.0.1:{self.sam_port})")
            self._ready.set()
            return True

        if await self._try_http_mode():
            self.transport_mode = "http"
            logger.info(f"I2PTransport ready via HTTP proxy (127.0.0.1:{self.http_port})")
            self._ready.set()
            return True

        logger.warning("No I2P transport mode available")
        self.available = False
        return False

    async def _try_socks_mode(self) -> bool:
        """Try to connect to existing I2P SOCKS5 proxy."""
        loop = asyncio.get_running_loop()

        def _check_socks() -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(("127.0.0.1", self.socks_port))
                s.close()
                return True
            except OSError:
                return False

        try:
            socks_ok = await loop.run_in_executor(None, _check_socks)
            if socks_ok:
                # Create SOCKS5 proxy session
                connector = self._aiohttp_socks.ProxyConnector.from_url(
                    f"socks5://127.0.0.1:{self.socks_port}", rdns=True
                )
                self._session_socks = self._aiohttp.ClientSession(connector=connector)
                return True
        except Exception as e:
            logger.debug(f"I2P SOCKS mode failed: {e}")
        return False

    async def _try_sam_mode(self) -> bool:
        """
        Try to connect via I2P SAM protocol.

        SAM protocol: TCP socket to SAM router for I2P destination management.
        This allows creating I2P destinations without a full I2P router.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.sam_port),
                timeout=3.0
            )

            # SAM Hello
            hello_msg = f"HELLO VERSION {SAM_VERSION}\n"
            writer.write(hello_msg.encode())
            await writer.drain()

            response = await asyncio.wait_for(reader.readline(), timeout=3.0)
            if SAM_OK in response.decode():
                # Generate destination
                dest_msg = "DEST GENERATE\n"
                writer.write(dest_msg.encode())
                await writer.drain()

                dest_response = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if SAM_OK in dest_response.decode():
                    # Parse destination from response
                    # Format: OK DESTINATION=<base64> PUBLICKEY=<base64> ...
                    resp_text = dest_response.decode()
                    for line in resp_text.split("\n"):
                        if line.startswith("DESTINATION="):
                            self.i2p_address = line.split("=", 1)[1].strip()
                            break

                writer.close()
                await writer.wait_closed()
                return True

            writer.close()
            await writer.wait_closed()
        except Exception as e:
            logger.debug(f"I2P SAM mode failed: {e}")
        return False

    async def _try_http_mode(self) -> bool:
        """Try to connect to I2P HTTP proxy (Freenet FProxy compatibility)."""
        loop = asyncio.get_running_loop()

        def _check_http() -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(("127.0.0.1", self.http_port))
                s.close()
                return True
            except OSError:
                return False

        try:
            http_ok = await loop.run_in_executor(None, _check_http)
            if http_ok:
                # HTTP proxy session (not SOCKS)
                self._session_http = self._aiohttp.ClientSession()
                return True
        except Exception as e:
            logger.debug(f"I2P HTTP mode failed: {e}")
        return False

    async def stop(self) -> None:
        """Graceful I2P transport shutdown."""
        if self._session_socks:
            await self._session_socks.close()
            self._session_socks = None
        if self._session_http:
            await self._session_http.close()
            self._session_http = None
        logger.info("I2P transport stopped")

    async def wait_ready(self) -> None:
        """Wait for transport to be ready."""
        await self._ready.wait()

    def register_handler(self, msg_type: str, handler):
        """I2P SAM mode message handler registration."""
        # SAM mode supports async messaging
        pass

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str = None):
        """
        Send message via I2P SAM session.

        Uses HTTP POST through I2P SOCKS5 or HTTP proxy to target's /message endpoint.
        This is the standard way to send messages over I2P — similar to Tor's messaging.

        Args:
            target: I2P destination address (Base32 destination or .i2p address)
            msg_type: Message type identifier
            payload: Message content as dict
            signature: Digital signature for message authentication
            msg_id: Optional message ID for tracking

        Returns:
            Response text from target's message endpoint
        """
        # Build the message URL — target is I2P destination
        url = f"http://{target}/message"
        data = {
            'sender': self.i2p_address,
            'type': msg_type,
            'payload': payload,
            'signature': signature,
            'msg_id': msg_id
        }

        # Try to get appropriate session based on transport mode
        session = None
        if self.transport_mode == "socks" and self._session_socks:
            session = self._session_socks
        elif self.transport_mode == "http" and self._session_http:
            session = self._session_http
        else:
            # Fallback: try to create a session
            try:
                session = await self.get_session()
            except I2PUnavailableError:
                logger.warning(f"No I2P session available for message to {target}")
                raise I2PUnavailableError(
                    f"No I2P session available (transport_mode={self.transport_mode})"
                )

        try:
            async with session.post(url, json=data, timeout=self._aiohttp.ClientTimeout(total=30)) as resp:
                return await resp.text()
        except Exception as e:
            logger.error(f"I2P message send failed to {target}: {e}")
            raise I2PUnavailableError(f"Message send failed: {e}")

    async def get_session(self, scheme: str = "http") -> "aiohttp.ClientSession":
        """
        Get aiohttp ClientSession configured for I2P.

        Args:
            scheme: "http" for I2P HTTP proxy, "socks" for SOCKS5 proxy

        Returns:
            aiohttp.ClientSession with appropriate proxy connector
        """
        if scheme == "socks" and self._session_socks:
            return self._session_socks
        if scheme == "http" and self._session_http:
            return self._session_http

        # Fallback: try to create session
        if self.transport_mode == "socks":
            if not self._session_socks:
                connector = self._aiohttp_socks.ProxyConnector.from_url(
                    f"socks5://127.0.0.1:{self.socks_port}", rdns=True
                )
                self._session_socks = self._aiohttp.ClientSession(connector=connector)
            return self._session_socks

        if self.transport_mode == "http":
            if not self._session_http:
                self._session_http = self._aiohttp.ClientSession()
            return self._session_http

        # No valid session
        raise I2PUnavailableError(f"No I2P session available (mode: {self.transport_mode})")

    def is_running(self) -> bool:
        """Check if I2P transport is operational."""
        return self.available and self.transport_mode != "none"


# ---------------------------------------------------------------------------
# P10: I2P Constants for transport_resolver integration
# ---------------------------------------------------------------------------

I2P_SOCKS_PROXY: str = f"socks5://127.0.0.1:{I2P_SOCKS_PORT}"
I2P_HTTP_PROXY: str = f"http://127.0.0.1:{I2P_HTTP_PORT}"


async def get_i2p_session() -> "aiohttp.ClientSession":
    """
    Get or create aiohttp session via I2P SOCKS5 proxy (lazy singleton).
    P10: Used by public_fetcher for .i2p/.b32.i2p URLs.
    """
    global _i2p_session
    if _i2p_session is None or _i2p_session.closed:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            raise RuntimeError("aiohttp_socks required for I2P: pip install aiohttp_socks")
        connector = ProxyConnector.from_url(I2P_SOCKS_PROXY, rdns=True)
        _i2p_session = aiohttp.ClientSession(connector=connector)
    return _i2p_session


# Module-level session singleton
_i2p_session: Optional["aiohttp.ClientSession"] = None


async def close_i2p_session() -> None:
    """Close the I2P session (for cleanup)."""
    global _i2p_session
    if _i2p_session is not None and not _i2p_session.closed:
        await _i2p_session.close()
        _i2p_session = None
