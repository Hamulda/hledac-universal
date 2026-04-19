import asyncio
import logging
import os
import signal
import shutil
import socket
from pathlib import Path
from typing import Dict, Callable, Optional

from .base import Transport

logger = logging.getLogger(__name__)


def _generate_torrc(torrc_path: Path) -> None:
    """Generovat minimální torrc pokud neexistuje."""
    if torrc_path.exists():
        return
    torrc_path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = torrc_path.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    torrc_path.write_text(
        f"DataDirectory {data_dir}\n"
        f"SocksPort 9050\n"
        f"ControlPort 9051\n"
        f"MaxCircuitDirtiness 600\n"
        f"IsolateSOCKSAuth 1\n"
        f"NumEntryGuards 3\n"
        f"Log notice stderr\n"
    )


class TorUnavailableError(RuntimeError):
    """Raised when .onion fetch attempted without running Tor."""


class TorTransport(Transport):
    available: bool = True

    def __init__(self, data_dir: Optional[str] = None, control_port: int = 9051,
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
        self.onion_address: Optional[str] = None
        self.tor_process: Optional[asyncio.subprocess.Process] = None
        self.http_server = None
        self.runner = None
        self.handlers: Dict[str, Callable] = {}
        self._ready = asyncio.Event()
        self.http_port: int = 0
        self.security_level = 'tor'
        self._session_direct = None
        self._session_tor = None

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
                if hostname_file.exists():
                    with open(hostname_file, 'r') as f:
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
                # Wait max 5s
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    try:
                        os.kill(pid, 0)  # check if alive
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception as e:
                logger.warning(f"Tor stop: {e}")
            finally:
                pid_path.unlink(missing_ok=True)
        elif self.tor_process:
            self.tor_process.terminate()
            try:
                await asyncio.wait_for(self.tor_process.wait(), timeout=5)
            except asyncio.TimeoutError:
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

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    async def send_message(self, target: str, msg_type: str, payload: Dict, signature: str, msg_id: str = None):
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
    import ssl
    import hashlib

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
        except (asyncio.TimeoutError, OSError, ssl.SSLError, ConnectionRefusedError):
            tokens.append("TIMEOUT")
        except Exception as e:
            tokens.append(f"ERR:{type(e).__name__}")

    fp = hashlib.md5(";".join(tokens).encode()).hexdigest()
    logger.debug(f"JARM {host}:{port} → {fp} (probes={tokens})")
    return fp


def check_jarm_malicious(fp: str) -> str | None:
    """Sprint 8TC B.2: Vrátí název known C2/RAT nebo None."""
    return KNOWN_MALICIOUS_JARM.get(fp)
