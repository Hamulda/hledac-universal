import asyncio
import hashlib
import logging
import time
from typing import Any, TYPE_CHECKING
from hledac.universal.core.resource_governor import Priority, ResourceGovernor
from hledac.universal.dht.kademlia_node import DHTStoreProtocol, LocalGraphReaderProtocol
from hledac.universal.utils.async_helpers import parallel, safe_create_task

if TYPE_CHECKING:
    from hledac.universal.dht.kademlia_node import KademliaNode
    from hledac.universal.dht.local_graph import LocalGraphStore

logger = logging.getLogger(__name__)
MAX_SKETCH_ITEMS = 10000

def stable_digest(s: str) -> str:
    """Stable digest for cross-node similarity."""
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def jaccard_from_lists(a: list[str], b: list[str]) -> float:
    if not a and (not b):
        return 1.0
    if not a or not b:
        return 0.0
    sa = set(a)
    sb = set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0

class SketchExchange:
    """
    Sketch-first exchange (CI-safe):
    - Publishes a bounded list of stable digests.
    - Compares via Jaccard on digests.

    F350M-R: Uses Protocol interfaces instead of concrete dependencies,
    reducing coupling from 4 to 2 direct dependencies.
    """
    __slots__ = tuple(('_background_tasks', '_digests', '_publish_task', '_running', 'dht', 'governor', 'local_graph', 'node_id'))

    def __init__(
        self,
        governor: ResourceGovernor,
        node_id: str,
        dht_node: DHTStoreProtocol,
        local_graph: LocalGraphReaderProtocol,
    ):
        self.governor = governor
        self.node_id = node_id
        self.dht = dht_node
        self.local_graph = local_graph
        self._publish_task: asyncio.Task | None = None
        self._running = True
        self._digests: list[str] = []
        self._background_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """F196B: Track background tasks for proper cleanup."""
        task = safe_create_task(coro, name='sketch:background')
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self):
        self._publish_task = self._track_task(self._publish_loop())

    async def stop(self):
        self._running = False
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        # Also cancel _publish_task directly — it is NOT in _background_tasks
        # (it is overwritten on each start() call, discarding the old ref).
        if self._publish_task is not None and not self._publish_task.done():
            self._publish_task.cancel()
            tasks = [self._publish_task] + tasks
        # PEP-705 / F3XX: parallel(policy="log") replaces asyncio.gather.
        # CancelledError re-raised per I6; non-Exception BaseException per I7.
        if tasks:
            await parallel(tasks, policy="log", ctx="_shutdown_tasks")

    async def _refresh_digests(self):
        nodes = await self.local_graph.get_all_nodes(limit=MAX_SKETCH_ITEMS)
        digests = [stable_digest(n['id']) for n in nodes]
        self._digests = digests[:MAX_SKETCH_ITEMS]

    async def _publish_loop(self):
        while self._running:
            await asyncio.sleep(3600)
            async with self.governor.reserve({'ram_mb': 50, 'gpu': False}, Priority.LOW):
                await self._refresh_digests()
                key = f'sketch:{self.node_id}'
                payload = {'digests': self._digests, 'ts': time.time(), 'v': 1}
                await self.dht.store(key, payload)

    async def query_entity(self, entity: str, min_jaccard: float=0.1) -> list[dict[str, Any]]:
        """
        Query: compare local digests vs remote digests. If similarity high -> fetch subgraph (placeholder).
        F350M-R: Uses get_all_entries() Protocol method instead of direct data_store access.
        """
        if not self._digests:
            await self._refresh_digests()
        results: list[dict[str, Any]] = []
        entries = await self.dht.get_all_entries()
        for key, (payload, _ts) in entries:
            if not key.startswith('sketch:'):
                continue
            if not isinstance(payload, dict):
                continue
            other = payload.get('digests')
            if not isinstance(other, list):
                continue
            sim = jaccard_from_lists(self._digests, other)
            if sim >= min_jaccard:
                peer_id = key.split('sketch:', 1)[-1]
                results.append({'peer_id': peer_id, 'similarity': sim})
        return results