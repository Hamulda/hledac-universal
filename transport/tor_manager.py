"""TorManager – lightweight Tor controller wrapper with circuit isolation."""
import asyncio
import importlib.util
import logging
import time
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
_STEM_SPEC = importlib.util.find_spec('stem')
STEM_AVAILABLE = _STEM_SPEC is not None
if STEM_AVAILABLE:
    from stem import Signal
    from stem.control import Controller
else:
    Signal = None
    Controller = None

class TorManager:
    """Manages Tor circuits with bounded concurrency and isolation."""
    MAX_CIRCUITS = 5
    CIRCUIT_REUSE_SECONDS = 60
    DEFAULT_CONTROL_PORT = 9051
    __slots__ = tuple(('_available', '_circuits', '_control_password', '_control_port', '_controller', '_data_dir', '_lock'))

    def __init__(self, data_dir: Path | None=None, control_port: int=DEFAULT_CONTROL_PORT, control_password: str=''):
        self._data_dir = data_dir or Path.home() / '.hledac' / 'tor_state'
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._control_port = control_port
        self._control_password = control_password
        self._controller: Controller | None = None
        self._circuits: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._available = STEM_AVAILABLE

    async def ensure_connected(self) -> bool:
        """Ensure Tor controller is connected. Returns True if successful."""
        if not self._available:
            return False
        if self._controller is not None and self._controller.is_alive():
            return True
        try:
            loop = asyncio.get_running_loop()
            self._controller = await loop.run_in_executor(None, lambda: Controller.from_port(port=self._control_port))
            if self._control_password:
                await loop.run_in_executor(None, lambda: self._controller.authenticate(password=self._control_password))
            else:
                await asyncio.to_thread(self._controller.authenticate)
            logger.info(f'[TOR] Controller connected on port {self._control_port}')
            return True
        except Exception as e:
            logger.warning(f'[TOR] Connection failed: {e}')
            self._controller = None
            return False

    async def get_circuit_for_domain(self, domain: str) -> str | None:
        """Get or create an isolated circuit for domain. Returns circuit ID or None."""
        if not self._available:
            return None
        if not await self.ensure_connected():
            return None
        async with self._lock:
            now = time.monotonic()
            if domain in self._circuits:
                circuit = self._circuits[domain]
                if circuit.get('expires_at', 0) > now:
                    return circuit['id']
                else:
                    del self._circuits[domain]
            if len(self._circuits) >= self.MAX_CIRCUITS:
                oldest = min(self._circuits.items(), key=lambda x: x[1]['created_at'])
                del self._circuits[oldest[0]]
            try:
                loop = asyncio.get_running_loop()
                circ_id = await loop.run_in_executor(None, lambda: self._controller.new_circuit())
                self._circuits[domain] = {'id': circ_id, 'created_at': now, 'expires_at': now + self.CIRCUIT_REUSE_SECONDS}
                return circ_id
            except Exception as e:
                logger.warning(f'[TOR] Circuit creation failed for {domain}: {e}')
                return None

    async def rotate_circuit(self) -> bool:
        """
        Force new Tor circuit via STEM controller.
        Returns True on success, False if Tor is unavailable or rotation fails.
        """
        if not self._available:
            return False
        if not await self.ensure_connected():
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self._controller.signal(Signal.NEWNYM))
            await asyncio.sleep(1)
            logger.info('[TOR] Circuit rotated successfully')
            return True
        except Exception as e:
            logger.error(f'[TOR] rotate_circuit failed: {e}')
            return False

    async def close(self):
        """Close Tor controller."""
        if self._controller and self._controller.is_alive():
            try:
                await asyncio.to_thread(self._controller.close)
            except Exception as e:
                logger.warning(f'[TOR] Close failed: {e}')