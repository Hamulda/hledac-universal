"""
InMemory Transport — bounded queue for M1 8GB safety.
"""
import asyncio
from typing import Dict, Callable, Optional
import inspect

from .base import Transport

# Queue size bounds
_MAX_QUEUE_SIZE = 128


class InMemoryTransport(Transport):
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.handlers: Dict[str, Callable] = {}
        self.peers: Dict[str, 'InMemoryTransport'] = {}
        self._queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()

    async def start(self):
        self._task = asyncio.create_task(self._process_loop())
        self._ready.set()

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def wait_ready(self):
        await self._ready.wait()

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    def register_peer(self, peer_id: str, peer_transport: 'InMemoryTransport'):
        """Register a peer transport. Alias for register_peer."""
        self.peers[peer_id] = peer_transport

    # Aliases for federated/transport_inmemory.py.bak compatibility
    def add_peer(self, peer: 'InMemoryTransport'):
        """Add a peer (alias for register_peer). Bounded to prevent memory issues."""
        if len(self.peers) >= 10:
            # Max 10 peers - bounded to prevent memory issues
            raise RuntimeError("Max peers limit (10) reached")
        self.peers[peer.node_id] = peer
        peer.peers[self.node_id] = self

    async def receive(self) -> Dict:
        """Receive a message from the queue. Bounded timeout."""
        try:
            msg_type, data = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            return data
        except asyncio.TimeoutError:
            return {}

    async def poll_once(self):
        """Process a single incoming message. Bounded timeout."""
        try:
            msg_type, data = await asyncio.wait_for(self._queue.get(), timeout=0.01)
            handler = self.handlers.get(msg_type)
            if handler:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass

    async def send_message(self, target: str, msg_type: str, payload: Dict, signature: str, msg_id: Optional[str] = None):
        if target not in self.peers:
            return
        target_transport = self.peers[target]
        await target_transport._queue.put((msg_type, {
            'sender': self.node_id,
            'type': msg_type,
            'payload': payload,
            'signature': signature,
            'msg_id': msg_id
        }))

    async def _process_loop(self):
        while True:
            msg_type, data = await self._queue.get()
            handler = self.handlers.get(msg_type)
            if handler:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
