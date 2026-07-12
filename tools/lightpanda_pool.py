"""
LightpandaPool — pool of Lightpanda instances for concurrent JS rendering.

Extracted from coordinators/fetch_coordinator.py (Sprint 45 refactor).
Provides a bounded pool of LightpandaManager instances.
"""
import asyncio
import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from hledac.universal.tools.lightpanda_manager import LightpandaManager
logger = logging.getLogger(__name__)

class LightpandaPool:
    """Pool of Lightpanda instances for concurrent JS rendering."""
    __slots__ = tuple(('_all_instances', '_available', '_size', '_started'))

    def __init__(self, size: int=2):
        self._size = size
        self._available: asyncio.Queue = asyncio.Queue(maxsize=max(4, size * 4))
        self._all_instances: list = []
        self._started = False

    async def start(self) -> None:
        """Initialize pool with N Lightpanda instances."""
        if self._started:
            return
        from hledac.universal.tools.lightpanda_manager import LightpandaManager
        for i in range(self._size):
            lp = LightpandaManager()
            try:
                await lp.ensure_running()
                self._all_instances.append(lp)
                await self._available.put(lp)
            except Exception as e:
                logger.warning(f'[POOL] Failed to start instance {i}: {e}')
        self._started = True
        logger.info(f'[POOL] Started {len(self._all_instances)} Lightpanda instances')

    async def get_instance(self) -> LightpandaManager:
        """Get available instance or wait."""
        if not self._started:
            await self.start()
        return await self._available.get()

    async def release(self, instance: LightpandaManager) -> None:
        """Return instance to pool."""
        await self._available.put(instance)

    async def close(self) -> None:
        """Terminate all Lightpanda instances in the pool."""
        for lp in self._all_instances:
            try:
                await lp.close()
            except Exception:
                pass
        self._all_instances.clear()
        self._started = False