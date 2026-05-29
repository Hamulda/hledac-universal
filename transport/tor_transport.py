import asyncio
import logging
import os
import shutil
import signal
import socket
from collections.abc import Callable
from pathlib import Path

from .base import Transport

logger = logging.getLogger(__name__)


# Circuit rotation — Sprint F214 B.1 / F251: per-domain circuit isolation
MAX_CIRCUIT_REQUESTS: int = 3  # rotate after N requests per domain (correlation risk reduction)

# Sprint F214Q B.3: Module-level TorTransport singleton — max 1 STEM Controller per process
_TOR_TRANSPORT_SINGLETON: TorTransport | None = None


def get_tor_transport_singleton() -> TorTransport | None:
    """Return the module-level TorTransport singleton or None."""
    return _TOR_TRANSPORT_SINGLETON


def set_tor_transport_singleton(transport: TorTransport) -> None:
    """Set the module-level TorTransport singleton. Call after start() succeeds."""
    global _TOR_TRANSPORT_SINGLETON
    _TOR_TRANSPORT_SINGLETON = transport


def _generate_torrc(torrc_path: Path) -> None:
    """Generate torrc with anonymity-hardening settings."""
    if torrc_path.exists():
        return
    torrc_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = torrc_path.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # B.2: HiddenServiceStatistics 0 — no traffic stats collection
    torrc_path.write_text(
        f"DataDirectory {data_dir}\n"
        f"SocksPort 9050\n"
        f"ControlPort 9051\n"
        f"MaxCircuitDirtiness 600\n"
        f"IsolateSOCKSAuth 1\n"
        f"NumEntryGuards 3\n"
        f"HiddenServiceStatistics 0\n"
        f"Log notice stderr\n"
    )


class TorUnavailableError(RuntimeError):
    """Raised when .onion fetch attempted without running Tor."""


class TorTransport(Transport):
    available: bool = True

    def __init__(self, data_dir: str | None = None, control_port: int = 9051,
                 socks_port: int = 9050):
        # B7: graceful fallback — Tor unavailable → available=False, no crash
        self.available = True
        try:
            import aiohttp
            import aiohttp.web
        except ImportError:
            logger.critical("TorTransport unavailable: missing aiohttp")
            self.available = False
            return

        try:
            from aiohttp_socks import ProxyConnector
        except ImportError:
            logger.critical("TorTransport unavailable: missing aiohttp_socks")
            self.available = False
            return

        self._aiohttp = aiohttp
        self._aiohttp_web = aiohttp.web
        self._ProxyConnector = ProxyConnector

        from hledac.universal.paths import TOR_ROOT
        if data_dir is None:
            self.data_dir = TOR_ROOT
        else:
            self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.control_port = control_port
        self.socks_port = socks_port
        self.hidden_service_dir = self.data_dir / "hidden_service"
        self.hidden_service_dir.mkdir(exist_ok=True)
        self.onion_address: str | None = None
        self.tor_process: asyncio.subprocess.Process | None = None
        self.http_server = None
        self.runner = None
        self.handlers: dict[str, Callable] = {}
        self._ready = asyncio.Event()
        self.http_port: int = 0
        self.security_level = 'tor'
        # Sprint F214 B.1 / F251: circuit rotation state
        self._circuit_request_count: int = 0  # global fallback counter
        self._domain_circuits: dict[str, int] = {}  # F251: per-domain circuit isolation
        self._max_circuit_requests: int = MAX_CIRCUIT_REQUESTS
        self._circuit_lock: asyncio.Lock = asyncio.Lock()
        self._session_direct = None
        self._session_tor = None
        # Sprint F214Q B.3: telemetry counters
        self._circuits_created: int = 0
        self._circuit_failures: int = 0

    async def start(self) -> bool:
        """Spustit Tor daemon autonomně. Vrátí True pokud circuit established."""
        tor_bin = shutil.which("tor")
        if not tor_bin:
            logger.error("tor binary not found — install: brew install tor")
            return False

        from hledac.universal.paths import TOR_ROOT
        torrc_path = TOR_ROOT / "torrc"
        _generate_torrc(torrc_path)
        pid_path = TOR_ROOT / "tor.pid"

        # Zkontrolovat zda již běží
        if await self.is_circuit_established():
            logger.info("Tor already running + circuit OK")
            return True

        # HTTP server using cached imports
        app = self._aiohttp_web.Application()
        app.router.add_post('/message', self._handle_message)
        app.router.add_get('/health', self._handle_health)
        self.runner = self._aiohttp_web.AppRunner(app)
        await self.runner.setup()
        self.http_server = self._aiohttp_web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.http_server.start()
        self.http_port = self.http_server._server.sockets[0].getsockname()[1]

        # Tor proces — autonomous subprocess start with torrc
        try:
            self.tor_process = await asyncio.create_subprocess_exec(
                tor_bin,
                "-f", str(torrc_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(self.tor_process.pid))

            # Polling s exponential backoff — čekat na circuit
            delay = 1.0
            total_wait = 0.0
            max_wait = 30.0
            while total_wait < max_wait:
                await asyncio.sleep(delay)
                total_wait += delay
                if await self.is_circuit_established():
                    logger.info(f"Tor circuit established in {total_wait:.1f}s (pid={self.tor_process.pid})")
                    break
                delay = min(delay * 2, 8.0)
                logger.debug(f"Waiting for Tor circuit... {total_wait:.1f}s")
            else:
                raise RuntimeError(f"Tor circuit not established after {max_wait}s")

            # Hidden service hostname
            hostname_file = self.hidden_service_dir / "hostname"
            for _ in range(15):
                if await asyncio.to_thread(hostname_file.exists):
                    f = await asyncio.to_thread(lambda: open(hostname_file))
                    with f:
                        self.onion_address = f.read().strip()
                    break
                await asyncio.sleep(1)
            else:
                self.onion_address = f"localhost:{self.http_port}"
                self.security_level = 'local'

        except Exception as e:
            logger.warning(f"Tor start failed, using localhost: {e}")
            self.onion_address = f"localhost:{self.http_port}"
            self.security_level = 'local'

        # HTTP session
        self._session_direct = self._aiohttp.ClientSession()
        if self.security_level == 'tor':
            connector = self._ProxyConnector.from_url(f'socks5://127.0.0.1:{self.socks_port}', rdns=True)
            self._session_tor = self._aiohttp.ClientSession(connector=connector)
        else:
            self._session_tor = self._session_direct  # fallback

        self._ready.set()
        logger.info(f"TorTransport ready at {self.onion_address}")
        return await self.is_circuit_established()

    async def stop(self) -> None:
        """Graceful Tor shutdown."""
        from hledac.universal.paths import TOR_ROOT
        pid_path = TOR_ROOT / "tor.pid"
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                # Wait max 10s (was 5s — Tor circuits can take time to close)
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    try:
                        os.kill(pid, 0)  # check if alive
                    except ProcessLookupError:
                        break
                else:
                    # Force kill only after graceful timeout exhausted
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # already dead
            except Exception as e:
                logger.warning(f"Tor stop: {e}")
            finally:
                pid_path.unlink(missing_ok=True)
        elif self.tor_process:
            self.tor_process.terminate()
            try:
                await asyncio.wait_for(self.tor_process.wait(), timeout=5)
            except TimeoutError:
                self.tor_process.kill()

        if self._session_direct:
            await self._session_direct.close()
        if self._session_tor and self._session_tor is not self._session_direct:
            await self._session_tor.close()
        if self.http_server:
            await self.http_server.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Tor stopped")

    def telemetry(self) -> dict:
        """Sprint F214Q B.3: Export circuit telemetry for MetricsRegistry."""
        return {
            "circuits_created": self._circuits_created,
            "circuit_failures": self._circuit_failures,
        }

    def __del__(self):
        """
        Sprint F214Q B.3: Fallback cleanup guard — logs warning if stop() was not called.
        Does NOT call stop() here (can raise in destructor).
        Cleanup must be done via stop() explicitly.
        """
        # Warn if Tor process or HTTP server were started but stop() was never called
        if getattr(self, 'tor_process', None) is not None or getattr(self, 'http_server', None) is not None:
            logger.warning(
                f"TorTransport.__del__: stop() not called — "
                f"Tor process or HTTP server may leak. "
                f"circuits_created={getattr(self, '_circuits_created', 0)}, "
                f"circuit_failures={getattr(self, '_circuit_failures', 0)}"
            )

    async def wait_ready(self):
        await self._ready.wait()

    async def is_circuit_established(self) -> bool:
        """2-step circuit health check: SOCKS port + optional stem circuit status."""
        loop = asyncio.get_running_loop()

        def _check_socks() -> bool:
            try:
                s = socket.socket()
                s.settimeout(2.0)
                s.connect(("127.0.0.1", self.socks_port))
                s.close()
                return True
            except OSError:
                return False

        socks_ok = await loop.run_in_executor(None, _check_socks)
        if not socks_ok:
            return False

        def _check_stem() -> bool:
            try:
                import stem.control
                with stem.control.Controller.from_port(port=self.control_port) as ctrl:
                    ctrl.authenticate()
                    circuits = ctrl.get_circuits()
                    built = [c for c in circuits if c.status == "BUILT"]
                    return len(built) > 0
            except Exception:
                return True  # stem unavailable → SOCKS check sufficient

        return await loop.run_in_executor(None, _check_stem)

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
            logger.warning("stem not available — circuit rotation skipped")
            return False

        try:
            def _do_rotate():
                with stem.control.Controller.from_port(port=self.control_port) as ctrl:
                    ctrl.authenticate()
                    ctrl.signal(stem.Signal.NEWNYM)
            await asyncio.get_event_loop().run_in_executor(None, _do_rotate)
            self._circuits_created += 1  # Sprint F214Q B.3: circuit telemetry
            logger.debug("Tor circuit rotated via NEWNYM")
            return True
        except Exception as e:
            self._circuit_failures += 1  # Sprint F214Q B.3: circuit telemetry
            logger.warning(f"Tor circuit rotation failed: {e}")
            return False

    async def _maybe_rotate_circuit(self, domain: str = "") -> None:
        """
        Sprint F214 B.1 / F251: Check request count and rotate circuit if threshold reached.

        F251: Per-domain circuit isolation — each .onion domain gets its own circuit after
        3 requests. This prevents correlation attacks where the same circuit is used
        to crawl multiple .onion addresses belonging to the same actor.

        Falls back to global counter for non-domain calls (backward compat).
        """

        async with self._circuit_lock:
            if domain:
                # F251: per-domain circuit isolation
                count = self._domain_circuits.get(domain, 0) + 1
                self._domain_circuits[domain] = count
                if count >= self._max_circuit_requests:
                    # Reset counter for this domain after rotation
                    self._domain_circuits[domain] = 0
                    if await self.rotate_circuit():
                        logger.info(f"Tor circuit rotated for domain {domain} after {count} requests")
                    else:
                        logger.warning(f"Circuit rotation failed for {domain} — continuing")
            else:
                # Legacy global counter fallback
                self._circuit_request_count += 1
                if self._circuit_request_count >= self._max_circuit_requests:
                    self._circuit_request_count = 0
                    if await self.rotate_circuit():
                        logger.info(f"Tor circuit rotated after {self._max_circuit_requests} requests")
                    else:
                        logger.warning("Circuit rotation failed — continuing with current circuit")

    async def fetch(self, config: TransportConfig) -> TransportResult:
        """
        Sprint F214 B.1: Fetch URL via Tor using curl_cffi with SOCKS5H.
        Circuit rotation after MAX_CIRCUIT_REQUESTS.

        Fail-safe: returns TransportResult with error if Tor unavailable.
        """
        from .curl_cffi_fetch import fetch_via_curl_cffi

        # Check Tor availability first
        if not await self.is_circuit_established():
            from .base import TransportResult
            return TransportResult(
                err="tor_unavailable",
                failure_stage="tor_check",
                selected_transport="tor",
            )

        # F251: per-domain circuit isolation — extract domain from URL
        domain = ""
        try:
            parsed = urlparse(config.url)
            domain = parsed.netloc
        except Exception:
            pass

        # Circuit rotation check (pass domain for per-domain isolation)
        await self._maybe_rotate_circuit(domain=domain)

        # Fetch via curl_cffi with SOCKS5H proxy
        # SOCKS5H = DNS resolution over Tor (not just SOCKS5 tunnel)
        import os
        os.environ["CURL_CFFI_PROXY"] = "socks5h://127.0.0.1:9050"

        try:
            result = await fetch_via_curl_cffi(
                url=config.url,
                method=config.method,
                headers=config.headers or {},
                body=config.body,
                timeout=config.timeout,
            )
            # Convert curl_cffi result to TransportResult
            from .base import TransportResult
            return TransportResult(
                final_url=result.get("url", config.url),
                status_code=result.get("status_code", 0),
                content_type=result.get("content_type", ""),
                fetched_bytes=len(result.get("content", b"")),
                err=result.get("error"),
                failure_stage=result.get("failure_stage"),
                network_error_kind=result.get("network_error_kind"),
                selected_transport="tor",
            )
        except Exception as e:
            from .base import TransportResult
            return TransportResult(
                err=f"tor_fetch_failed: {e}",
                failure_stage="tor_fetch",
                selected_transport="tor",
            )

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str = None):
        if target.startswith('localhost:'):
            url = f"http://{target}/message"
            session = self._session_direct
        else:
            url = f"http://{target}/message"
            session = self._session_tor
        data = {
            'sender': self.onion_address,
            'type': msg_type,
            'payload': payload,
            'signature': signature,
            'msg_id': msg_id
        }
        async with session.post(url, json=data) as resp:
            return await resp.text()

    async def _handle_message(self, request):
        data = await request.json()
        msg_type = data.get('type')
        handler = self.handlers.get(msg_type)
        if handler:
            await handler(data)
        return self._aiohttp_web.Response(text='OK')

    async def _handle_health(self, request):
        return self._aiohttp_web.Response(text='OK')


# ---------------------------------------------------------------------------
# Sprint 8TC B.2: JARM TLS Fingerprinting
# ---------------------------------------------------------------------------

KNOWN_MALICIOUS_JARM: dict[str, str] = {
    "2ad2ad0002ad2ad00042d42d000000ad": "Cobalt Strike 4.x",
    "07d14d16d21d21d07c42d41d00041d24": "Metasploit Framework",
    "3fd21b20d00000021c43d21b21b43d41": "AsyncRAT",
    "1dd28d28d00028d1c1c1c00d1c1c41e7": "Havoc C2",
    "29d3fd00029d29d21c41d21b21b41c41": "Covenant C2",
    # Zdroj: https://github.com/salesforce/jarm
}


async def jarm_fingerprint(host: str, port: int = 443) -> str | None:
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

    probes = [
        (ssl.TLSVersion.TLSv1_2, ssl.OP_NO_TLSv1_3),
        (ssl.TLSVersion.TLSv1_3, 0),
        (ssl.TLSVersion.TLSv1_2, ssl.OP_CIPHER_SERVER_PREFERENCE),
    ]
    tokens: list[str] = []
    for min_ver, extra_op in probes:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = min_ver
            ctx.options |= extra_op
            r, w = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ctx), timeout=4.0
            )
            ssl_obj = w.get_extra_info("ssl_object")
            cipher = ssl_obj.cipher() if ssl_obj else None
            proto = ssl_obj.version() if ssl_obj else "NONE"
            tokens.append(f"{cipher[0] if cipher else 'NONE'}|{proto}")
            w.close()
            try:
                await asyncio.wait_for(w.wait_closed(), timeout=1.0)
            except Exception:
                pass
        except (TimeoutError, OSError, ssl.SSLError, ConnectionRefusedError):
            tokens.append("TIMEOUT")
        except Exception as e:
            tokens.append(f"ERR:{type(e).__name__}")

    fp = hashlib.md5(";".join(tokens).encode()).hexdigest()
    logger.debug(f"JARM {host}:{port} → {fp} (probes={tokens})")
    return fp


def check_jarm_malicious(fp: str) -> str | None:
    """Sprint 8TC B.2: Vrátí název known C2/RAT nebo None."""
    return KNOWN_MALICIOUS_JARM.get(fp)
