"""TorManager – lightweight Tor controller wrapper with circuit isolation."""
import asyncio
import logging
import time
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Fail-safe import of stem
try:
    from stem.control import Controller
    from stem import Signal
    STEM_AVAILABLE = True
except ImportError:
    STEM_AVAILABLE = False
    Controller = None
    Signal = None


class TorManager:
    """Manages Tor circuits with bounded concurrency and isolation."""

    MAX_CIRCUITS = 5
    CIRCUIT_REUSE_SECONDS = 60
    DEFAULT_CONTROL_PORT = 9051

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        control_port: int = DEFAULT_CONTROL_PORT,
        control_password: str = "",
    ):
        self._data_dir = data_dir or Path.home() / ".hledac" / "tor_state"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._control_port = control_port
        self._control_password = control_password
        self._controller: Optional[Controller] = None
        self._circuits: Dict[str, Dict[str, Any]] = {}  # domain -> circuit info
        self._lock = asyncio.Lock()
        self._available = STEM_AVAILABLE

    async def ensure_connected(self) -> bool:
        """Ensure Tor controller is connected. Returns True if successful."""
        if not self._available:
            return False
        if self._controller is not None and self._controller.is_alive():
            return True
        try:
            # Run stem operations in thread pool to avoid blocking
            loop = asyncio.get_running_loop()
            self._controller = await loop.run_in_executor(
                None,
                lambda: Controller.from_port(port=self._control_port)
            )
            if self._control_password:
                await loop.run_in_executor(
                    None,
                    lambda: self._controller.authenticate(password=self._control_password)
                )
            else:
                await loop.run_in_executor(None, self._controller.authenticate)
            logger.info(f"[TOR] Controller connected on port {self._control_port}")
            return True
        except Exception as e:
            logger.warning(f"[TOR] Connection failed: {e}")
            self._controller = None
            return False

    async def get_circuit_for_domain(self, domain: str) -> Optional[str]:
        """Get or create an isolated circuit for domain. Returns circuit ID or None."""
        if not self._available:
            return None
        if not await self.ensure_connected():
            return None

        async with self._lock:
            now = time.monotonic()  # použij time.monotonic() místo loop.time()

            # Check existing circuit
            if domain in self._circuits:
                circuit = self._circuits[domain]
                if circuit.get('expires_at', 0) > now:
                    return circuit['id']
                else:
                    # Expired – remove it
                    del self._circuits[domain]

            # Enforce MAX_CIRCUITS limit
            if len(self._circuits) >= self.MAX_CIRCUITS:
                # Remove oldest circuit
                oldest = min(self._circuits.items(), key=lambda x: x[1]['created_at'])
                del self._circuits[oldest[0]]

            # Create new circuit
            try:
                loop = asyncio.get_running_loop()
                circ_id = await loop.run_in_executor(
                    None,
                    lambda: self._controller.new_circuit()
                )
                self._circuits[domain] = {
                    'id': circ_id,
                    'created_at': now,
                    'expires_at': now + self.CIRCUIT_REUSE_SECONDS
                }
                return circ_id
            except Exception as e:
                logger.warning(f"[TOR] Circuit creation failed for {domain}: {e}")
                return None

    # ========================================================================
    # P7: Circuit rotation
    # ========================================================================

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
            await loop.run_in_executor(
                None,
                lambda: self._controller.signal(Signal.NEWNYM)
            )
            await asyncio.sleep(1)  # Wait for new circuit to establish
            logger.info("[TOR] Circuit rotated successfully")
            return True
        except Exception as e:
            logger.error(f"[TOR] rotate_circuit failed: {e}")
            return False

    async def close(self):
        """Close Tor controller."""
        if self._controller and self._controller.is_alive():
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._controller.close)
            except Exception as e:
                logger.warning(f"[TOR] Close failed: {e}")
