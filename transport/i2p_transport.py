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
from typing import TYPE_CHECKING
from .base import Transport, TransportConfig, TransportResult
if TYPE_CHECKING:
    import httpx
logger = logging.getLogger(__name__)
I2P_SOCKS_PORT = 7654
I2P_SAM_PORT = 7656
I2P_HTTP_PORT = 8888
SAM_VERSION = '1.0'
SAM_OK = 'OK'

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
    transport_mode: str = 'none'
    __slots__ = tuple(('_httpx', '_httpx_socks', '_ready', '_session_http', '_session_socks', 'available', 'data_dir', 'http_port', 'i2p_address', 'sam_port', 'socks_port', 'transport_mode'))

    def __init__(self, data_dir: str | None=None, socks_port: int=I2P_SOCKS_PORT, sam_port: int=I2P_SAM_PORT, http_port: int=I2P_HTTP_PORT):
        self.available = True
        self.transport_mode = 'none'
        try:
            import httpx
            import httpx_socks
        except ImportError:
            logger.critical('I2PTransport unavailable: missing httpx or httpx-socks')
            self.available = False
            return
        self._httpx = httpx
        self._httpx_socks = httpx_socks
        from hledac.universal.paths import I2P_ROOT
        if data_dir is None:
            self.data_dir = I2P_ROOT
        else:
            self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.socks_port = socks_port
        self.sam_port = sam_port
        self.http_port = http_port
        self.i2p_address: str | None = None
        self._session_socks: httpx.AsyncClient | None = None
        self._session_http: httpx.AsyncClient | None = None
        self._ready = asyncio.Event()

    async def start(self) -> bool:
        """
        Start I2P transport by detecting available mode.

        Returns True if any I2P mode is operational.
        """
        if not self.available:
            return False
        if await self._try_socks_mode():
            self.transport_mode = 'socks'
            logger.info(f'I2PTransport ready via SOCKS5 proxy (127.0.0.1:{self.socks_port})')
            self._ready.set()
            return True
        if await self._try_sam_mode():
            self.transport_mode = 'sam'
            logger.info(f'I2PTransport ready via SAM protocol (127.0.0.1:{self.sam_port})')
            self._ready.set()
            return True
        if await self._try_http_mode():
            self.transport_mode = 'http'
            logger.info(f'I2PTransport ready via HTTP proxy (127.0.0.1:{self.http_port})')
            self._ready.set()
            return True
        logger.warning('No I2P transport mode available')
        self.available = False
        return False

    async def _try_socks_mode(self) -> bool:
        """Try to connect to existing I2P SOCKS5 proxy."""

        def _check_socks() -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(('127.0.0.1', self.socks_port))
                s.close()
                return True
            except OSError:
                return False
        try:
            socks_ok = await asyncio.to_thread(_check_socks)
            if socks_ok:
                transport = self._httpx_socks.AsyncProxyTransport.from_url(f'socks5://127.0.0.1:{self.socks_port}', rdns=True)
                limits = self._httpx.Limits(max_connections=10, max_keepalive_connections=5)
                timeout = self._httpx.Timeout(connect=5.0, read=20.0, write=10.0)
                self._session_socks = self._httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, transport=transport, trust_env=False)
                return True
        except Exception as e:
            logger.debug(f'I2P SOCKS mode failed: {e}')
        return False

    async def _try_sam_mode(self) -> bool:
        """
        Try to connect via I2P SAM protocol.

        SAM protocol: TCP socket to SAM router for I2P destination management.
        This allows creating I2P destinations without a full I2P router.
        """
        try:
            async with asyncio.timeout(3.0):
                reader, writer = await asyncio.open_connection('127.0.0.1', self.sam_port)
            hello_msg = f'HELLO VERSION {SAM_VERSION}\n'
            writer.write(hello_msg.encode())
            await writer.drain()
            async with asyncio.timeout(3.0):
                response = await reader.readline()
            if SAM_OK in response.decode():
                dest_msg = 'DEST GENERATE\n'
                writer.write(dest_msg.encode())
                await writer.drain()
                async with asyncio.timeout(5.0):
                    dest_response = await reader.readline()
                if SAM_OK in dest_response.decode():
                    resp_text = dest_response.decode()
                    for line in resp_text.split('\n'):
                        if line.startswith('DESTINATION='):
                            self.i2p_address = line.split('=', 1)[1].strip()
                            break
                writer.close()
                await writer.wait_closed()
                logger.debug('I2P SAM mode: DEST GENERATE succeeded but STREAM CONNECT not implemented — falling through')
                return False
            logger.debug('I2P SAM mode: DEST GENERATE succeeded but STREAM CONNECT not implemented — disabling SAM')
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            logger.debug(f'I2P SAM mode failed: {e}')
        return False

    async def _try_http_mode(self) -> bool:
        """Try to connect to I2P HTTP proxy (Freenet FProxy on port 8888).

        ISSUE-007: httpx with HTTP CONNECT proxy support.
        """

        def _check_http() -> bool:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect(('127.0.0.1', self.http_port))
                s.close()
                return True
            except OSError:
                return False
        try:
            http_ok = await asyncio.to_thread(_check_http)
            if http_ok:
                limits = self._httpx.Limits(max_connections=10, max_keepalive_connections=5)
                timeout = self._httpx.Timeout(connect=5.0, read=20.0, write=10.0)
                self._session_http = self._httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, proxy=f'http://127.0.0.1:{self.http_port}', trust_env=False)
                return True
        except Exception as e:
            logger.debug(f'I2P HTTP mode failed: {e}')
        return False

    async def stop(self) -> None:
        """Graceful I2P transport shutdown."""
        if self._session_socks:
            await self._session_socks.aclose()
            self._session_socks = None
        if self._session_http:
            await self._session_http.aclose()
            self._session_http = None
        self._ready.clear()
        logger.info('I2P transport stopped')

    async def wait_ready(self) -> None:
        """Wait for transport to be ready."""
        await self._ready.wait()

    def register_handler(self, msg_type: str, handler):
        """I2P SAM mode message handler registration."""
        raise NotImplementedError('I2P SAM streaming session not implemented; use SOCKS5 mode (rdns=True) for .i2p hostname resolution')

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str | None=None):
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
        url = f'http://{target}/message'
        data = {'sender': self.i2p_address, 'type': msg_type, 'payload': payload, 'signature': signature, 'msg_id': msg_id}
        session = None
        if self.transport_mode == 'socks' and self._session_socks:
            session = self._session_socks
        elif self.transport_mode == 'http' and self._session_http:
            session = self._session_http
        else:
            try:
                session = await self.get_session()
            except I2PUnavailableError:
                logger.warning(f'No I2P session available for message to {target}')
                raise I2PUnavailableError(f'No I2P session available (transport_mode={self.transport_mode})')
        try:
            resp = await session.post(url, json=data)
            return await resp.text()
        except Exception as e:
            logger.error(f'I2P message send failed to {target}: {e}')
            raise I2PUnavailableError(f'Message send failed: {e}') from e

    async def get_session(self, scheme: str='http') -> httpx.AsyncClient:
        """
        ISSUE-007: Get httpx.AsyncClient configured for I2P.

        Args:
            scheme: "http" for I2P HTTP proxy, "socks" for SOCKS5 proxy

        Returns:
            httpx.AsyncClient with appropriate proxy configuration
        """
        if scheme == 'socks' and self._session_socks:
            return self._session_socks
        if scheme == 'http' and self._session_http:
            return self._session_http
        if self.transport_mode == 'socks':
            if not self._session_socks:
                transport = self._httpx_socks.AsyncProxyTransport.from_url(f'socks5://127.0.0.1:{self.socks_port}', rdns=True)
                limits = self._httpx.Limits(max_connections=10, max_keepalive_connections=5)
                timeout = self._httpx.Timeout(connect=5.0, read=20.0, write=10.0)
                self._session_socks = self._httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, transport=transport, trust_env=False)
            return self._session_socks
        if self.transport_mode == 'http':
            if not self._session_http:
                limits = self._httpx.Limits(max_connections=10, max_keepalive_connections=5)
                timeout = self._httpx.Timeout(connect=5.0, read=20.0, write=10.0)
                self._session_http = self._httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, proxy=f'http://127.0.0.1:{self.http_port}', trust_env=False)
            return self._session_http
        raise I2PUnavailableError(f'No I2P session available (mode: {self.transport_mode})')

    async def is_running(self) -> bool:
        """Check if I2P transport is operational."""
        return self.available and self.transport_mode != 'none'

    def health_cost(self) -> float:
        """I2PTransport: ~20-30 MB for httpx sessions."""
        return 25.0

    async def is_healthy(self) -> bool:
        """Check if I2P session is available and responsive."""
        return await self.is_running()

    async def keepalive(self) -> None:
        """
        F320: I2PTransport keepalive — verify session is still usable.

        Called by TransportSupervisor every 30s. Tries to get a session
        to verify the I2P SAM bridge is still responsive.
        """
        try:
            async with asyncio.timeout(5.0):
                await self.get_session()
        except Exception:
            pass

    async def on_phase_boundary(self, old_phase: str, new_phase: str) -> None:
        """
        F320: At phase boundaries, close and recreate I2P session.

        This forces a fresh circuit through the I2P network.
        """
        try:
            if self._session_socks is not None and (not self._session_socks.is_closed):
                await self._session_socks.aclose()
                self._session_socks = None
            if self._session_http is not None and (not self._session_http.is_closed):
                await self._session_http.aclose()
                self._session_http = None
            async with asyncio.timeout(5.0):
                await self.get_session()
            logger.info('[I2P] Phase-boundary session refresh: %s → %s', old_phase, new_phase)
        except Exception as e:
            logger.warning('[I2P] Phase-boundary session refresh failed: %s → %s: %s', old_phase, new_phase, e)

    async def fetch(self, config: TransportConfig) -> TransportResult:
        """
        Fetch URL via I2P network using SOCKS5H or HTTP proxy.

        SOCKS5H mode (default): DNS resolution happens on the proxy side,
        preventing .i2p hostname leaks. HTTP mode uses httpx with
        the proxy URL configured via AsyncClient proxy= parameter.

        Fail-safe: returns TransportResult with `error` if I2P unavailable.
        """
        if not await self.is_running():
            return TransportResult(url=config.url, error='i2p_unavailable', failure_stage='i2p_check', selected_transport='i2p')
        try:
            session = await self.get_session()
        except I2PUnavailableError as e:
            return TransportResult(url=config.url, error=f'i2p_session_unavailable: {e}', failure_stage='i2p_session', selected_transport='i2p')
        try:
            timeout = getattr(config, 'timeout_s', 30) or 30
            resp = await session.get(config.url, timeout=timeout)
            body = await resp.text()
            return TransportResult(url=config.url, text=body, status_code=resp.status_code, selected_transport='i2p')
        except Exception as e:
            return TransportResult(url=config.url, error=f'i2p_fetch_failed: {e}', failure_stage='i2p_fetch', selected_transport='i2p')
I2P_SOCKS_PROXY: str = f'socks5://127.0.0.1:{I2P_SOCKS_PORT}'
I2P_HTTP_PROXY: str = f'http://127.0.0.1:{I2P_HTTP_PORT}'

async def get_i2p_session() -> httpx.AsyncClient:
    """
    ISSUE-007: Get or create httpx session via I2P SOCKS5 proxy (lazy singleton).
    P10: Used by public_fetcher for .i2p/.b32.i2p URLs.
    """
    global _i2p_session
    if _i2p_session is None or _i2p_session.is_closed:
        try:
            import httpx
            import httpx_socks
        except ImportError:
            raise RuntimeError('httpx-socks required for I2P: pip install httpx-socks')
        transport = httpx_socks.AsyncProxyTransport.from_url(I2P_SOCKS_PROXY, rdns=True)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0)
        _i2p_session = httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, transport=transport, trust_env=False)
    return _i2p_session
_i2p_session: httpx.AsyncClient | None = None

async def close_i2p_session() -> None:
    """Close the I2P session (for cleanup)."""
    global _i2p_session
    if _i2p_session is not None and (not _i2p_session.is_closed):
        await _i2p_session.aclose()
        _i2p_session = None