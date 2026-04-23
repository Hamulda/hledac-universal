import asyncio
import hashlib
import logging
import time
from typing import Dict, List, Optional, Any

from hledac.universal.core.resource_governor import ResourceGovernor, Priority
from hledac.universal.dht.kademlia_node import KademliaNode
from hledac.universal.dht.local_graph import LocalGraphStore

logger = logging.getLogger(__name__)

MAX_SKETCH_ITEMS = 10_000


def stable_digest(s: str) -> str:
    """Stable digest for cross-node similarity."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def jaccard_from_lists(a: List[str], b: List[str]) -> float:
    if not a and not b:
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
    """
    def __init__(self, governor: ResourceGovernor, node_id: str, dht_node: KademliaNode, local_graph: LocalGraphStore):
        self.governor = governor
        self.node_id = node_id
        self.dht = dht_node
        self.local_graph = local_graph

        self._publish_task: Optional[asyncio.Task] = None
        self._running = True

        self._digests: List[str] = []

        # F196B: Track background tasks for proper cleanup
        self._background_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """F196B: Track background tasks for proper cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self):
        self._publish_task = self._track_task(self._publish_loop())

    async def stop(self):
        self._running = False
        # F196B: Cancel all tracked background tasks
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

    async def _refresh_digests(self):
        nodes = await self.local_graph.get_all_nodes(limit=MAX_SKETCH_ITEMS)
        digests = [stable_digest(n["id"]) for n in nodes]
        self._digests = digests[:MAX_SKETCH_ITEMS]

    async def _publish_loop(self):
        while self._running:
            await asyncio.sleep(3600)
            async with self.governor.reserve({"ram_mb": 50, "gpu": False}, Priority.LOW):
                await self._refresh_digests()
                key = f"sketch:{self.node_id}"
                # Safe serialization: list[str]
                payload = {"digests": self._digests, "ts": time.time(), "v": 1}
                await self.dht.store(key, payload)

    async def query_entity(self, entity: str, min_jaccard: float = 0.1) -> List[Dict[str, Any]]:
        """
        Query: compare local digests vs remote digests. If similarity high -> fetch subgraph (placeholder).
        """
        if not self._digests:
            await self._refresh_digests()

        results: List[Dict[str, Any]] = []
        # Iterate local DHT cache (best-effort)
        for key, (payload, _ts) in list(self.dht.data_store.items()):
            if not key.startswith("sketch:"):
                continue
            if not isinstance(payload, dict):
                continue
            other = payload.get("digests")
            if not isinstance(other, list):
                continue

            sim = jaccard_from_lists(self._digests, other)
            if sim >= min_jaccard:
                peer_id = key.split("sketch:", 1)[-1]
                results.append({"peer_id": peer_id, "similarity": sim})
        return results
