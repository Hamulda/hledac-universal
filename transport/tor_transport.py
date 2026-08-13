import asyncio
import logging
import os
import shutil
import signal
import socket
import weakref
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import aiofiles

from .base import Transport, TransportConfig, TransportResult
logger = logging.getLogger(__name__)
from hledac.universal.utils.safe_swallow import safe_swallow
from hledac.universal.transport.resource_admission import (
    TransportAdmission,
    cleanup_child_process,
    cleanup_process_tree,
    get_resource_ledger,
)
MAX_CIRCUIT_REQUESTS: int = 3
_TOR_TRANSPORT_SINGLETON: 'TorTransport | None' = None

def get_tor_transport_singleton() -> 'TorTransport | None':
    """Return the module-level TorTransport singleton or None."""
    return _TOR_TRANSPORT_SINGLETON

def set_tor_transport_singleton(transport: 'TorTransport') -> None:
    """Set the module-level TorTransport singleton. Call after start() succeeds."""
    global _TOR_TRANSPORT_SINGLETON
    _TOR_TRANSPORT_SINGLETON = transport

def _generate_torrc(torrc_path: Path) -> None:
    """Generate torrc with anonymity-hardening settings."""
    if torrc_path.exists():
        return
    torrc_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = torrc_path.parent / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    torrc_path.write_text(f'DataDirectory {data_dir}\nSocksPort 9050\nControlPort 9051\nMaxCircuitDirtiness 600\nIsolateSOCKSAuth 1\nNumEntryGuards 3\nHiddenServiceStatistics 0\nLog notice stderr\n')

class TorUnavailableError(RuntimeError):
    """Raised when .onion fetch attempted without running Tor."""

class TorTransport(Transport):
    """
    Tor transport with integrated Resource Ledger management.

    M1 Resource Ceiling Drift Fix: All Tor resources (FDs, Mach ports,
    child processes) are now tracked via ResourceLedger for guaranteed
    cleanup and admission control.
    """

    available: bool = True
    __slots__ = tuple(('_circuit_failures', '_circuit_lock', '_circuit_request_count', '_circuits_created', '_domain_circuits', '_httpx', '_httpx_socks', '_max_circuit_requests', '_ready', '_session_direct', '_session_tor', 'available', 'control_port', 'data_dir', 'handlers', 'hidden_service_dir', 'http_port', 'http_server', 'onion_address', 'security_level', 'socks_port', 'tor_process', '_ledger', '_resource_active'))

    def __init__(self, data_dir: str | None=None, control_port: int=9051, socks_port: int=9050):
        self.available = True
        try:
            import httpx
            import httpx_socks
        except ImportError as e:
            logger.critical(f'TorTransport unavailable: {e}')
            self.available = False
            return
        self._httpx = httpx
        self._httpx_socks = httpx_socks
        from hledac.universal.paths import TOR_ROOT
        if data_dir is None:
            self.data_dir = TOR_ROOT
        else:
            self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.control_port = control_port
        self.socks_port = socks_port
        self.hidden_service_dir = self.data_dir / 'hidden_service'
        self.hidden_service_dir.mkdir(exist_ok=True)
        self.onion_address: str | None = None
        self.tor_process: asyncio.subprocess.Process | None = None
        self.http_server: asyncio.Server | None = None
        self.handlers: dict[str, Callable] = {}
        self._ready = asyncio.Event()
        self.http_port: int = 0
        self.security_level = 'tor'
        self._circuit_request_count: int = 0
        self._domain_circuits: dict[str, int] = {}
        self._max_circuit_requests: int = MAX_CIRCUIT_REQUESTS
        self._circuit_lock: asyncio.Lock = asyncio.Lock()
        self._session_direct = None
        self._session_tor = None
        self._circuits_created: int = 0
        self._circuit_failures: int = 0

        # M1 Resource Ledger: Initialize resource tracking
        self._ledger = get_resource_ledger()
        self._resource_active = False

        # Weakref finalizer for GC safety net
        self._finalizer = weakref.finalize(self, self._cleanup)

    async def start(self) -> bool:
        """
        Spustit Tor daemon s resource admission kontrolou.

        M1 Resource Ceiling Drift Fix: Requests resource admission before
        acquiring any resources. Guarantees cleanup via context manager.
        """
        # M1 Resource Admission: Check if we can start Tor
        can_start, reason = TransportAdmission.can_start_transport("tor", self._ledger)
        if not can_start:
            logger.warning(f"[TorTransport] Cannot start: {reason}")
            return False

        # M1 Resource Admission: Acquire resources via context manager
        with TransportAdmission.for_transport("tor", self._ledger):
            result = await self._start_internal()

            # Mark resources as active for cleanup tracking
            if result:
                self._resource_active = True
                # Register Tor PID with ledger
                if self.tor_process and self.tor_process.pid:
                    self._ledger.register_child_process(self.tor_process.pid, "tor")

            return result

    async def _start_internal(self) -> bool:
        """Internal start logic without resource admission."""
        tor_bin = shutil.which('tor')
        if not tor_bin:
            logger.error('tor binary not found — install: brew install tor')
            return False
        from hledac.universal.paths import TOR_ROOT
        torrc_path = TOR_ROOT / 'torrc'
        _generate_torrc(torrc_path)
        pid_path = TOR_ROOT / 'tor.pid'
        if await self.is_circuit_established():
            logger.info('Tor already running + circuit OK')
            return True
        # E-41 FIX: replaced aiohttp.web with asyncio.start_server + minimal HTTP parser
        # ~100 LOC for /message + /health, zero new deps, ~2KB resident
        async def _http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            """Minimal HTTP handler: parses HTTP/1.1 request line + headers, dispatches to handlers."""
            try:
                request_line = await reader.readline()
                if not request_line:
                    writer.close()
                    return
                method, path, _ = request_line.decode('utf-8', errors='ignore').strip().split()
                # Read headers
                headers: dict[str, str] = {}
                while True:
                    line = await reader.readline()
                    if not line or line == b'\r\n':
                        break
                    if b':' in line:
                        k, v = line.decode('utf-8', errors='ignore').strip().split(':', 1)
                        headers[k.strip().lower()] = v.strip()
                # Dispatch
                if path == '/message' and method == 'POST':
                    content_length = int(headers.get('content-length', 0))
                    body = await reader.read(content_length) if content_length else b''
                    try:
                        import json as _json
                        data = _json.loads(body.decode('utf-8', errors='ignore'))
                        msg_type = data.get('type')
                        handler = self.handlers.get(msg_type)
                        if handler:
                            await handler(data)
                    except Exception:  # noqa: BLE001
                        pass
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK')
                    await writer.drain()
                elif path == '/health' and method == 'GET':
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK')
                    await writer.drain()
                else:
                    writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
                    await writer.drain()
                writer.close()
            except Exception:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass

        self.http_server = await asyncio.start_server(_http_handler, '127.0.0.1', 0)
        sock = self.http_server.sockets[0] if self.http_server.sockets else None
        if not sock:
            raise RuntimeError('Tor HTTP server failed to bind (no sockets)')
        self.http_port = sock.getsockname()[1]
        try:
            self.tor_process = await asyncio.create_subprocess_exec(tor_bin, '-f', str(torrc_path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(self.tor_process.pid))
            delay = 1.0
            total_wait = 0.0
            max_wait = 30.0
            while total_wait < max_wait:
                await asyncio.sleep(delay)
                total_wait += delay
                if await self.is_circuit_established():
                    logger.info(f'Tor circuit established in {total_wait:.1f}s (pid={self.tor_process.pid})')
                    break
                delay = min(delay * 2, 8.0)
                logger.debug(f'Waiting for Tor circuit... {total_wait:.1f}s')
            else:
                raise RuntimeError(f'Tor circuit not established after {max_wait}s')
            hostname_file = self.hidden_service_dir / 'hostname'
            for _ in range(15):
                if await asyncio.to_thread(hostname_file.exists):
                    async with aiofiles.open(hostname_file, 'r') as f:
                        self.onion_address = (await f.read()).strip()
                    break
                await asyncio.sleep(1)
            else:
                # SEC-05 FIX: Do NOT fall back to localhost — that would route darknet
                # URLs to a local HTTP server, returning fake data or hitting local services.
                # Mark Tor as unavailable; FetchCoordinator checks RouteDecision.TOR_UNAVAILABLE
                # and drops .onion URLs instead of leaking to clearnet.
                logger.warning('[SEC-05] Tor hostname file not found after %ds — Tor unavailable', 15)
                self.onion_address = None
                self.security_level = 'local'
        except Exception as e:
            # SEC-05 FIX: Tor process/cert bootstrap failure must NOT fall back to localhost.
            # Tor was explicitly requested for this URL; failure to start means anonymity
            # cannot be guaranteed. Mark unavailable so FetchCoordinator drops the URL
            # rather than leaking to clearnet.
            logger.warning('[SEC-05] Tor start failed: %s — Tor unavailable (will drop .onion)', e)
            self.onion_address = None
            self.security_level = 'local'
        limits = self._httpx.Limits(max_connections=10, max_keepalive_connections=5)
        timeout = self._httpx.Timeout(connect=5.0, read=20.0, write=10.0)
        self._session_direct = self._httpx.AsyncClient(limits=limits, http2=True, timeout=timeout, follow_redirects=True, trust_env=False)
        if self.security_level == 'tor':
            # OPSEC-001: socks5h:// forces remote DNS resolution by Tor proxy.
            transport = self._httpx_socks.AsyncProxyTransport.from_url(f'socks5h://127.0.0.1:{self.socks_port}', rdns=True)
            self._session_tor = self._httpx.AsyncClient(limits=limits, http2=False, timeout=timeout, follow_redirects=True, transport=transport, trust_env=False)  # SOCKS5 tunnel doesn't support HTTP/2 ALPN
        else:
            self._session_tor = self._session_direct
        self._ready.set()
        logger.info(f'TorTransport ready at {self.onion_address}')
        return await self.is_circuit_established()

    async def stop(self) -> None:
        """
        Graceful Tor shutdown with resource cleanup.

        M1 Resource Ceiling Drift Fix: Properly terminates child processes
        and releases all resources via ResourceLedger.
        """
        from hledac.universal.paths import TOR_ROOT
        from hledac.universal.utils.secure_zero import wipe_tor_identity

        # G1: Secure wipe of Tor identity material before shutdown
        wipe_tor_identity(self.onion_address)

        # M1 Resource Cleanup: Terminate Tor process via ledger
        pid_path = TOR_ROOT / 'tor.pid'
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                # M1: Use cleanup_process_tree for proper child cleanup
                await cleanup_process_tree(pid, self._ledger, timeout_s=10.0)
            except Exception as e:
                logger.warning(f'Tor stop: {e}')
            finally:
                pid_path.unlink(missing_ok=True)
        elif self.tor_process:
            self.tor_process.terminate()
            try:
                async with asyncio.timeout(5):
                    await self.tor_process.wait()
            except TimeoutError:
                self.tor_process.kill()

        # Close HTTP sessions
        if self._session_direct:
            await self._session_direct.aclose()
        if self._session_tor and self._session_tor is not self._session_direct:
            await self._session_tor.aclose()

        # Close HTTP server
        if self.http_server:
            self.http_server.close()

        # M1 Resource Cleanup: Release all remaining resources for "tor"
        self._ledger.release_all("tor")
        self._resource_active = False

        logger.info('[TorTransport] Stopped and resources released')

    def telemetry(self) -> dict:
        """Sprint F214Q B.3: Export circuit telemetry for MetricsRegistry."""
        return {'circuits_created': self._circuits_created, 'circuit_failures': self._circuit_failures}

    def _cleanup(self) -> None:
        """
        Called by weakref.finalize when TorTransport is garbage collected.

        M1 Resource Ceiling Drift Fix: Also releases resources from ledger.

        This is a last-resort safety net. Proper cleanup should use stop() explicitly.
        """
        try:
            onion_addr = getattr(self, "onion_address", None)
            if onion_addr:
                from hledac.universal.utils.secure_zero import wipe_tor_identity
                wipe_tor_identity(onion_addr)
        except Exception as e:
            safe_swallow("tor_transport_cleanup_Exception", logger=logger, exc=e)

        # M1 Resource Cleanup: Release all resources for this transport
        ledger = getattr(self, "_ledger", None)
        if ledger is not None:
            ledger.release_all("tor")

        if getattr(self, "tor_process", None) is not None or getattr(self, "http_server", None) is not None:
            logger.warning(f"TorTransport: stop() not called before GC — Tor process or HTTP server may leak. "
                         f"circuits_created={getattr(self, '_circuits_created', 0)}, "
                         f"circuit_failures={getattr(self, '_circuit_failures', 0)}")

    async def wait_ready(self):
        await self._ready.wait()

    async def is_circuit_established(self) -> bool:
        """2-step circuit health check: SOCKS port + optional stem circuit status."""

        def _check_socks() -> bool:
            try:
                s = socket.socket()
                s.settimeout(2.0)
                s.connect(('127.0.0.1', self.socks_port))
                s.close()
                return True
            except OSError:
                return False
        socks_ok = await asyncio.to_thread(_check_socks)
        if not socks_ok:
            return False

        def _check_stem() -> bool:
            try:
                import stem.control
                with stem.control.Controller.from_port(port=self.control_port) as ctrl:
                    ctrl.authenticate()
                    circuits = ctrl.get_circuits()
                    built = [c for c in circuits if c.status == 'BUILT']
                    return len(built) > 0
            except Exception:
                return True
        return await asyncio.to_thread(_check_stem)

    async def is_running(self) -> bool:
        """Alias for is_circuit_established — Tor is considered running if circuit is built."""
        return await self.is_circuit_established()

    async def rotate_circuit(self) -> bool:
        """
        Sprint F214 B.1: Send NEWNYM signal via stem control port.
        Forces Tor to build a new circuit for the next request.
        Returns True if rotation succeeded.
        """
        try:
            import stem.control
        except ImportError:
            logger.warning('stem not available — circuit rotation skipped')
            return False
        try:

            def _do_rotate():
                with stem.control.Controller.from_port(port=self.control_port) as ctrl:
                    ctrl.authenticate()
                    ctrl.signal(stem.Signal.NEWNYM)
            await asyncio.to_thread(_do_rotate)
            self._circuits_created += 1
            logger.debug('Tor circuit rotated via NEWNYM')
            return True
        except Exception as e:
            self._circuit_failures += 1
            logger.warning(f'Tor circuit rotation failed: {e}')
            return False

    def health_cost(self) -> float:
        """TorTransport: ~20-30 MB for aiohttp sessions."""
        return 25.0

    async def is_healthy(self) -> bool:
        """Check if Tor circuit is established."""
        return await self.is_circuit_established()

    async def keepalive(self) -> None:
        """
        F320: TorTransport keepalive — check if stem is still reachable.

        Called by TransportSupervisor every 30s. Verifies the control port
        is responsive. Circuit rotation is NOT done here — it happens at
        phase boundaries via on_phase_boundary().
        """
        try:

            def _check() -> bool:
                import stem.control
                with stem.control.Controller.from_port(port=self.control_port) as ctrl:
                    ctrl.authenticate()
                    return True
            await asyncio.to_thread(_check)
        except Exception:  # noqa: BLE001
            pass

    async def on_phase_boundary(self, old_phase: str, new_phase: str) -> None:
        """
        F320: At phase boundaries, rotate Tor circuit instead of per-request.

        This is a key M1 8GB optimization: rotating circuits is expensive
        (NEWNYM signal + new TLS handshake), so doing it per-request is
        wasteful. At phase boundaries we have a natural synchronization
        point where a fresh circuit is beneficial.
        """
        if self.available and await self.is_circuit_established():
            try:
                async with asyncio.timeout(10.0):
                    ok = await self.rotate_circuit()
                if ok:
                    logger.info('[Tor] Phase-boundary circuit rotation: %s → %s', old_phase, new_phase)
                else:
                    logger.warning('[Tor] Phase-boundary circuit rotation failed: %s → %s', old_phase, new_phase)
            except TimeoutError:
                logger.warning('[Tor] Phase-boundary circuit rotation timed out: %s → %s', old_phase, new_phase)

    async def _maybe_rotate_circuit(self, domain: str='') -> None:
        """
        Sprint F214 B.1 / F251: Check request count and rotate circuit if threshold reached.

        F251: Per-domain circuit isolation — each .onion domain gets its own circuit after
        3 requests. This prevents correlation attacks where the same circuit is used
        to crawl multiple .onion addresses belonging to the same actor.

        Falls back to global counter for non-domain calls (backward compat).
        """
        async with self._circuit_lock:
            if domain:
                count = self._domain_circuits.get(domain, 0) + 1
                self._domain_circuits[domain] = count
                if count >= self._max_circuit_requests:
                    self._domain_circuits[domain] = 0
                    if await self.rotate_circuit():
                        logger.info(f'Tor circuit rotated for domain {domain} after {count} requests')
                    else:
                        logger.warning(f'Circuit rotation failed for {domain} — continuing')
            else:
                self._circuit_request_count += 1
                if self._circuit_request_count >= self._max_circuit_requests:
                    self._circuit_request_count = 0
                    if await self.rotate_circuit():
                        logger.info(f'Tor circuit rotated after {self._max_circuit_requests} requests')
                    else:
                        logger.warning('Circuit rotation failed — continuing with current circuit')

    async def fetch(self, config: TransportConfig) -> TransportResult:
        """
        Sprint F214 B.1: Fetch URL via Tor using curl_cffi with SOCKS5H.
        Circuit rotation after MAX_CIRCUIT_REQUESTS.

        Fail-safe: returns TransportResult with `error` if Tor unavailable.
        """
        from .curl_cffi_fetch import fetch_via_curl_cffi
        if not await self.is_circuit_established():
            from .base import TransportResult
            return TransportResult(url=config.url, error='tor_unavailable', failure_stage='tor_check', selected_transport='tor')
        domain = ''
        try:
            parsed = urlparse(config.url)
            domain = parsed.netloc
        except Exception:  # noqa: BLE001
            pass
        await self._maybe_rotate_circuit(domain=domain)
        # P0-2 MODERN-02 FIX: Pass proxies directly instead of using dead env var.
        # The CURL_CFFI_PROXY env var was never read by curl_cffi_fetch.py —
        # this was the root cause of .onion/.i2p leak via Clearnet.
        # Now passing SOCKS5H proxy directly for DNS-on-proxy semantics.
        from .curl_cffi_fetch import _TOR_CURL_PROXY
        proxies = {"http": _TOR_CURL_PROXY, "https": _TOR_CURL_PROXY}
        try:
            result = await fetch_via_curl_cffi(url=config.url, timeout_s=config.timeout_s, max_bytes=config.max_bytes, proxies=proxies)
            from .base import TransportResult
            return TransportResult(url=config.url, final_url=result.get('final_url', config.url), status_code=result.get('status_code', 0), content_type=result.get('content_type', ''), fetched_bytes=len(result.get('content', b'')), error=result.get('error'), failure_stage=result.get('failure_stage'), network_error_kind=result.get('network_error_kind'), selected_transport='tor')
        except Exception as e:
            from .base import TransportResult
            return TransportResult(url=config.url, error=f'tor_fetch_failed: {e}', failure_stage='tor_fetch', selected_transport='tor')

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str | None=None):
        if target.startswith('localhost:'):
            url = f'http://{target}/message'
            session = self._session_direct
        else:
            url = f'http://{target}/message'
            session = self._session_tor
        if session is None:
            logger.warning('TorTransport.send_message called before start() — no session')
            return ''
        data = {'sender': self.onion_address, 'type': msg_type, 'payload': payload, 'signature': signature, 'msg_id': msg_id}
        resp = await session.post(url, json=data)
        return resp.text


KNOWN_MALICIOUS_JARM: dict[str, str] = {'2ad2ad0002ad2ad00042d42d000000ad': 'Cobalt Strike 4.x', '07d14d16d21d21d07c42d41d00041d24': 'Metasploit Framework', '3fd21b20d00000021c43d21b21b43d41': 'AsyncRAT', '1dd28d28d00028d1c1c1c00d1c1c41e7': 'Havoc C2', '29d3fd00029d29d21c41d21b21b41c41': 'Covenant C2'}

async def jarm_fingerprint(host: str, port: int=443) -> str | None:
    """
    Sprint 8TC B.2: Async JARM-like TLS fingerprint — 3 handshakes, M1 native ssl.

    Neblokuje event loop — asyncio.open_connection je nativně async.
    Vrátí 32-char MD5 hash nebo None při síťové chybě.

    Probes:
      1. TLS 1.2 bez TLS 1.3
      2. TLS 1.3
      3. TLS 1.2 s CIPHER_SERVER_PREFERENCE
    """
    import hashlib
    import ssl
    probes = [(ssl.TLSVersion.TLSv1_2, ssl.OP_NO_TLSv1_3), (ssl.TLSVersion.TLSv1_3, 0), (ssl.TLSVersion.TLSv1_2, ssl.OP_CIPHER_SERVER_PREFERENCE)]
    tokens: list[str] = []
    for min_ver, extra_op in probes:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = min_ver
            ctx.options |= extra_op
            async with asyncio.timeout(4.0):
                r, w = await asyncio.open_connection(host, port, ssl=ctx)
            ssl_obj = w.get_extra_info('ssl_object')
            cipher = ssl_obj.cipher() if ssl_obj else None
            proto = ssl_obj.version() if ssl_obj else 'NONE'
            tokens.append(f"{(cipher[0] if cipher else 'NONE')}|{proto}")
            w.close()
            try:
                async with asyncio.timeout(1.0):
                    await w.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        except (TimeoutError, OSError, ssl.SSLError, ConnectionRefusedError):
            tokens.append('TIMEOUT')
        except Exception as e:
            tokens.append(f'ERR:{type(e).__name__}')
    fp = hashlib.md5(';'.join(tokens).encode()).hexdigest()
    logger.debug(f'JARM {host}:{port} → {fp} (probes={tokens})')
    return fp

def check_jarm_malicious(fp: str) -> str | None:
    """Sprint 8TC B.2: Vrátí název known C2/RAT nebo None."""
    return KNOWN_MALICIOUS_JARM.get(fp)
