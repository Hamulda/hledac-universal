import asyncio
import logging
import json
import time
from hledac.universal.utils.uuid7 import new_runtime_id
from typing import Dict, Callable, Optional, Any
from pathlib import Path

from .base import Transport

logger = logging.getLogger(__name__)


class NymTransport(Transport):
    def __init__(self, data_dir: Optional[str] = None, nym_client_path: str = "nym-client",
                 websocket_port: int = 1977, max_queue_size: int = 100):
        # Lazy import check - raise RuntimeError if dependencies unavailable
        try:
            import websockets
        except ImportError:
            raise RuntimeError("NymTransport unavailable: missing websockets")

        self._websockets = websockets

        from hledac.universal.paths import NYM_ROOT
        if data_dir is None:
            self.data_dir = NYM_ROOT
        else:
            self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.nym_client_path = nym_client_path
        self.websocket_port = websocket_port
        self.max_queue_size = max_queue_size
        self.client_process = None
        self.websocket = None
        self.handlers: Dict[str, Callable] = {}
        self._ready = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._outgoing_queue = asyncio.Queue(maxsize=max_queue_size)
        self._sender_task = None
        self._receiver_task = None
        self._health_check_task = None
        self._stdout_task = None
        self._stderr_task = None
        self.nym_address = None
        self.circuit_breaker_open = False
        self.circuit_breaker_failures = 0
        self.circuit_breaker_threshold = 3
        self.circuit_breaker_timeout = 60
        self.circuit_breaker_last_failure = 0.0

    async def start(self):
        try:
            self.client_process = await asyncio.create_subprocess_exec(
                self.nym_client_path,
                '--id', 'hledac',
                '--config-dir', str(self.data_dir),
                '--port', str(self.websocket_port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        except FileNotFoundError:
            raise RuntimeError(f"nym-client not found at {self.nym_client_path}")
        logger.info("Nym client process started")

        self._stdout_task = asyncio.create_task(self._drain_stream(self.client_process.stdout, 'stdout'), name="nym:stdout_drain")
        self._stderr_task = asyncio.create_task(self._drain_stream(self.client_process.stderr, 'stderr'), name="nym:stderr_drain")

        for _ in range(10):
            try:
                self.websocket = await self._websockets.connect(f"ws://127.0.0.1:{self.websocket_port}")
                break
            except ConnectionRefusedError:
                await asyncio.sleep(1)
        else:
            raise RuntimeError("Nym client websocket not available after 10s")

        async def wait_for_self_address():
            while True:
                response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
                data = json.loads(response)
                if data.get('type') == 'selfAddress':
                    return data['address']
                else:
                    logger.debug(f"Ignored non-selfAddress message: {data.get('type')}")

        try:
            self.nym_address = await asyncio.wait_for(wait_for_self_address(), timeout=10.0)
            logger.info(f"Nym address: {self.nym_address}")
        except asyncio.TimeoutError:
            raise RuntimeError("Nym client did not send selfAddress")

        self._ready.set()
        self._sender_task = asyncio.create_task(self._sender_loop(), name="nym:sender")
        self._receiver_task = asyncio.create_task(self._receiver_loop(), name="nym:receiver")
        self._health_check_task = asyncio.create_task(self._health_check_loop(), name="nym:health_check")

    async def _drain_stream(self, stream, name: str):
        while True:
            try:
                line = await stream.readline()
                if not line:
                    break
                logger.debug(f"Nym {name}: {line.decode().strip()}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error draining nym {name}: {e}")
                break

    async def stop(self, graceful: bool = True):
        self._stop_event.set()
        if graceful:
            try:
                await asyncio.wait_for(self._outgoing_queue.join(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Outgoing queue not empty, discarding pending messages")
        for task in [self._sender_task, self._receiver_task, self._health_check_task,
                     self._stdout_task, self._stderr_task]:
            if task:
                task.cancel()
        for task in [self._sender_task, self._receiver_task, self._health_check_task,
                     self._stdout_task, self._stderr_task]:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.websocket:
            await self.websocket.close()
        if self.client_process:
            self.client_process.terminate()
            try:
                await asyncio.wait_for(self.client_process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Nym process did not terminate gracefully, killing")
                self.client_process.kill()
                await self.client_process.wait()

    async def wait_ready(self):
        await self._ready.wait()

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    async def send_message(self, target: str, msg_type: str, payload: Dict, signature: str, msg_id: Optional[str] = None):
        if self.circuit_breaker_open:
            raise RuntimeError("Circuit breaker open, cannot send via Nym")
        if msg_id is None:
            msg_id = new_runtime_id()
        message = {
            'type': 'send',
            'recipient': target,
            'data': {
                'type': msg_type,
                'payload': payload,
                'signature': signature,
                'msg_id': msg_id
            }
        }
        try:
            await asyncio.wait_for(self._outgoing_queue.put((msg_id, message)), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning(f"Outgoing queue full, dropping message {msg_id}")
            return

    async def _sender_loop(self):
        while not self._stop_event.is_set():
            msg_id = None
            try:
                msg_id, msg = await self._outgoing_queue.get()
                await self.websocket.send(json.dumps(msg))
                self._outgoing_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sender error for msg {msg_id}: {e}")
                self.circuit_breaker_failures += 1
                self.circuit_breaker_last_failure = time.time()
                if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
                    self.circuit_breaker_open = True
                self._outgoing_queue.task_done()

    async def _receiver_loop(self):
        while not self._stop_event.is_set():
            try:
                response = await self.websocket.recv()
                data = json.loads(response)
                if data.get('type') == 'received':
                    msg = data['message']
                    msg_type = msg.get('type')
                    handler = self.handlers.get(msg_type)
                    if handler:
                        await handler({
                            'sender': msg.get('sender'),
                            'type': msg_type,
                            'payload': msg.get('payload'),
                            'signature': msg.get('signature'),
                            'msg_id': msg.get('msg_id')
                        })
            except asyncio.CancelledError:
                break
            except self._websockets.exceptions.ConnectionClosed:
                logger.warning("Nym websocket closed, attempting reconnect")
                await self._reconnect()
            except Exception as e:
                logger.error(f"Receiver error: {e}")
                self.circuit_breaker_failures += 1
                self.circuit_breaker_last_failure = time.time()
                if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
                    self.circuit_breaker_open = True

    async def _reconnect(self):
        self.circuit_breaker_failures += 1
        self.circuit_breaker_last_failure = time.time()
        if self.circuit_breaker_failures >= self.circuit_breaker_threshold:
            self.circuit_breaker_open = True
            return
        for _ in range(10):
            try:
                self.websocket = await self._websockets.connect(f"ws://127.0.0.1:{self.websocket_port}")
                logger.info("Nym websocket reconnected")
                # Reset breaker state on successful reconnect
                self.circuit_breaker_open = False
                self.circuit_breaker_failures = 0
                return
            except ConnectionRefusedError:
                await asyncio.sleep(1)
        logger.error("Failed to reconnect Nym websocket")

    async def _health_check_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(30)
            if self.circuit_breaker_open:
                if time.time() - self.circuit_breaker_last_failure > self.circuit_breaker_timeout:
                    self.circuit_breaker_open = False
                    self.circuit_breaker_failures = 0
                    logger.info("Circuit breaker reset for Nym")
