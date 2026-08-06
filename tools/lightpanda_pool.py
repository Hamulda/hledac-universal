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

# Fixed pool size cap — prevents unbounded memory growth on large pool configs.
_POOL_QUEUE_MAX = 64

class LightpandaPool:
    """Pool of Lightpanda instances for concurrent JS rendering."""
    __slots__ = tuple(('_all_instances', '_available', '_size', '_started'))

    def __init__(self, size: int=2):
        self._size = size
        # S1-07 FIX: queue size scales with pool size but is capped at _POOL_QUEUE_MAX.
        # This prevents unbounded growth when size>>64 while ensuring all instances
        # can be queued for small pools. Pool exhaustion is signaled via QueueFull
        # backpressure to caller, not silent drop.
        self._available: asyncio.Queue = asyncio.Queue(maxsize=min(size * 2, _POOL_QUEUE_MAX))
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
                try:
                    self._available.put_nowait(lp)
                except asyncio.QueueFull:
                    logger.warning('[POOL] Instance %d queued but pool at capacity (%d)', i, _POOL_QUEUE_MAX)
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
        """Return instance to pool. Backpressures caller with QueueFull if pool is saturated."""
        try:
            self._available.put_nowait(instance)
        except asyncio.QueueFull:
            # Pool saturated — close the instance to prevent resource leak.
            # Caller should handle QueueFull and decide whether to retry.
            try:
                await instance.close()
            except Exception:
                pass
            raise

    async def close(self) -> None:
        """Terminate all Lightpanda instances in the pool."""
        for lp in self._all_instances:
            try:
                await lp.close()
            except Exception:
                pass
        self._all_instances.clear()
        self._started = False