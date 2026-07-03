"""
InMemory Transport — bounded queue for M1 8GB safety.

PRODUCTION IMPORT IS A LINT ERROR (TST001).
See tests/transports/ for the canonical location.
"""
import asyncio
import inspect
from collections.abc import Callable

# TST001: production code must not import this — only tests use it
# ruff: noqa: TST001

# Re-export from canonical location so existing test imports still work
from hledac.universal.transport.base import Transport  # noqa: F401

# Queue size bounds
_MAX_QUEUE_SIZE = 128


class InMemoryTransport(Transport):
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.handlers: dict[str, Callable] = {}
        self.peers: dict[str, InMemoryTransport] = {}
        self._queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop_event = asyncio.Event()

    async def start(self):
        self._task = asyncio.create_task(self._process_loop(), name="inmemory:process_loop")
        self._ready.set()

    async def stop(self):
        self._stop_event.set()
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

    def register_peer(self, peer_id: str, peer_transport: InMemoryTransport):
        self.peers[peer_id] = peer_transport

    def add_peer(self, peer: InMemoryTransport):
        """Add a peer. Bounded to 10 peers to prevent memory issues."""
        if len(self.peers) >= 10:
            raise RuntimeError("Max peers limit (10) reached")
        self.peers[peer.node_id] = peer
        peer.peers[self.node_id] = self

    async def receive(self) -> dict:
        try:
            async with asyncio.timeout(1.0):
                _, data = await self._queue.get()
            return data
        except TimeoutError:
            return {}

    async def poll_once(self):
        try:
            async with asyncio.timeout(0.01):
                msg_type, data = await self._queue.get()
            handler = self.handlers.get(msg_type)
            if handler:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            pass

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str | None = None):
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
            if self._stop_event.is_set():
                break
            try:
                async with asyncio.timeout(0.01):
                    msg_type, data = await self._queue.get()
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            handler = self.handlers.get(msg_type)
            if handler:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)

    def health_cost(self) -> float:
        return 5.0

    async def is_healthy(self) -> bool:
        return self.available and self._task is not None and not self._task.done()

    async def keepalive(self) -> None:
        pass

    async def on_phase_boundary(self, old_phase: str, new_phase: str) -> None:
        pass
