from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

from core.env_config import ENV  # noqa: E402
from collections.abc import Callable
from pathlib import Path
from typing import Any

# RC-9: orjson for 2-3× faster JSON serialization (nym websocket messages)
try:
    import orjson

    _ORJSON_AVAILABLE = True
except ImportError:
    _ORJSON_AVAILABLE = False

from hledac.universal.utils.msgspec_json import encode as _msgspec_encode, decode as _msgspec_decode
from hledac.universal.utils.uuid7 import new_runtime_id

from .base import Transport

logger = logging.getLogger(__name__)

# RC-9: JSON helpers with orjson fallback (2-3× faster than Python json)
def _nym_json_dumps(obj: Any) -> str:
    """Serialize to JSON string. orjson is 2-3× faster."""
    if _ORJSON_AVAILABLE:
        return orjson.dumps(obj).decode("utf-8")
    return _msgspec_encode(obj).decode()


def _nym_json_loads(data: str | bytes) -> Any:
    """Parse JSON string. orjson accepts both bytes and str."""
    if _ORJSON_AVAILABLE:
        return orjson.loads(data)
    return _msgspec_decode(data)


# Sprint F250: Nym client availability check
NYM_CLIENT_AVAILABLE: bool = shutil.which("nym-client") is not None
# Fallback: check HLEDAC_NYM_SOCKS_PROXY env var
if not NYM_CLIENT_AVAILABLE:
    NYM_CLIENT_AVAILABLE = bool(ENV.get_str("HLEDAC_NYM_SOCKS_PROXY"))

# Module-level singleton
_NYM_TRANSPORT_SINGLETON: Any = None


def set_nym_transport_singleton(transport: Any) -> None:
    global _NYM_TRANSPORT_SINGLETON
    _NYM_TRANSPORT_SINGLETON = transport


class NymTransport(Transport):
    def __init__(self, data_dir: str | None = None, nym_client_path: str = "nym-client",
                 websocket_port: int = 1977, max_queue_size: int = 100):
        # Lazy import check - raise RuntimeError if dependencies unavailable
        try:
            # ty: `websockets` is an optional transport dep installed via the
            # `[nym]` extra. Suppress the unresolved-import check at the
            # `type: ignore` (no specific rule) level — `websockets` is
            # imported in a try/except for runtime availability anyway.
            import websockets  # type: ignore
        except ImportError:
            raise RuntimeError("NymTransport unavailable: missing websockets")  # noqa: B904

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
        self.handlers: dict[str, Callable] = {}
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
        # Sprint F250: fail-soft if nym-client not available
        if not NYM_CLIENT_AVAILABLE:
            logger.info("[Nym] nym-client not found — transport disabled")
            self.available = False
            return

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
            logger.info("[Nym] nym-client not found at %s — transport disabled", self.nym_client_path)
            self.available = False
            return
        self.available = True
        set_nym_transport_singleton(self)

        self._stdout_task = asyncio.create_task(self._drain_stream(self.client_process.stdout, 'stdout'), name="nym:stdout_drain")  # noqa: E501
        self._stderr_task = asyncio.create_task(self._drain_stream(self.client_process.stderr, 'stderr'), name="nym:stderr_drain")  # noqa: E501

        for _ in range(10):
            try:
                self.websocket = await self._websockets.connect(f"ws://127.0.0.1:{self.websocket_port}")
                break
            except ConnectionRefusedError:
                await asyncio.sleep(1)
        else:
            raise RuntimeError("Nym client websocket not available after 10s")

        async def wait_for_self_address():
            # ty: self.websocket is `WebsocketClientProtocol | None` at the
            # class level. After the 10-retry connect loop above it is
            # guaranteed to be set (loop breaks on success, `else` raises).
            # Rebind to a local so ty sees a non-Optional type.
            ws = self.websocket
            if ws is None:
                raise RuntimeError("Nym websocket unavailable after connect loop")
            while True:
                async with asyncio.timeout(5.0):
                    response = await ws.recv()
                data = _nym_json_loads(response)
                if data.get('type') == 'selfAddress':
                    return data['address']
                else:
                    logger.debug(f"Ignored non-selfAddress message: {data.get('type')}")

        try:
            async with asyncio.timeout(10.0):
                await wait_for_self_address()
            logger.info(f"Nym address: {self.nym_address}")
        except TimeoutError:
            raise RuntimeError("Nym client did not send selfAddress")  # noqa: B904

        self._ready.set()
        self._sender_task = asyncio.create_task(self._sender_loop(), name="nym:sender")
        self._receiver_task = asyncio.create_task(self._receiver_loop(), name="nym:receiver")
        self._health_check_task = asyncio.create_task(self._health_check_loop(), name="nym:health_check")

    # F320: TransportSupervisor integration
    def health_cost(self) -> float:
        """NymTransport: ~50-80 MB for websocket + process + queues."""
        return 60.0

    async def is_healthy(self) -> bool:
        """Check if Nym websocket is connected and circuit breaker is closed."""
        if not self.available:
            return False
        if self.circuit_breaker_open:
            return False
        # Websocket is healthy if not closed
        ws = self.websocket
        if ws is None:
            return False
        try:
            # Simple ping via closed property check
            return not getattr(ws, "closed", True)
        except Exception:  # noqa: BLE001
            return False

    async def keepalive(self) -> None:
        """
        F320: NymTransport keepalive — check websocket and process health.

        Called by TransportSupervisor every 30s. We piggyback on the existing
        health_check_loop logic: check if we need to reset the circuit breaker.
        """
        if not self.available:
            return
        # If circuit breaker is open and timeout has passed, reset it
        if self.circuit_breaker_open:
            if time.time() - self.circuit_breaker_last_failure > self.circuit_breaker_timeout:
                self.circuit_breaker_open = False
                self.circuit_breaker_failures = 0
                logger.info("[Nym] Circuit breaker reset via keepalive")
        # Check if process is still alive
        if self.client_process and self.client_process.returncode is not None:
            logger.warning("[Nym] Nym process exited with code %s", self.client_process.returncode)
            self.available = False

    async def on_phase_boundary(self, old_phase: str, new_phase: str) -> None:
        """
        F320: At phase boundaries, reset circuit breaker state.

        Circuit rotation for Nym is the circuit_breaker timeout — we reset
        the failure counter at each phase boundary so Nym starts fresh.
        """
        if self.circuit_breaker_open or self.circuit_breaker_failures > 0:
            logger.info(
                "[Nym] Phase boundary circuit reset: failures=%d, open=%s",
                self.circuit_breaker_failures,
                self.circuit_breaker_open,
            )
        self.circuit_breaker_open = False
        self.circuit_breaker_failures = 0
        self.circuit_breaker_last_failure = 0.0

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
                async with asyncio.timeout(5.0):
                    await self._outgoing_queue.join()
            except TimeoutError:
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
                async with asyncio.timeout(5.0):
                    await self.client_process.wait()
            except TimeoutError:
                logger.warning("Nym process did not terminate gracefully, killing")
                self.client_process.kill()
                await self.client_process.wait()

    async def wait_ready(self):
        await self._ready.wait()

    def register_handler(self, msg_type: str, handler: Callable):
        self.handlers[msg_type] = handler

    async def send_message(self, target: str, msg_type: str, payload: dict, signature: str, msg_id: str | None = None):
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
            async with asyncio.timeout(1.0):
                await self._outgoing_queue.put((msg_id, message))
        except TimeoutError:
            logger.warning(f"Outgoing queue full, dropping message {msg_id}")
            return

    async def _sender_loop(self):
        while not self._stop_event.is_set():
            msg_id = None
            try:
                msg_id, msg = await self._outgoing_queue.get()
                # ty: self.websocket is `None` until start() succeeds. The
                # _sender_loop is only created after `_ready.set()` in start(),
                # so a non-None binding is invariant. Rebind for narrowing.
                ws = self.websocket
                if ws is None:
                    raise RuntimeError("Nym websocket unavailable in sender loop")
                await ws.send(_nym_json_dumps(msg))
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
                # ty: same invariant as _sender_loop — _receiver_task is
                # created after `_ready.set()`, so websocket is non-None.
                ws = self.websocket
                if ws is None:
                    raise RuntimeError("Nym websocket unavailable in receiver loop")
                response = await ws.recv()
                data = _nym_json_loads(response)
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
            try:
                async with asyncio.timeout(35.0):
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except TimeoutError:
                # Should not happen, but handle gracefully
                continue
            if self.circuit_breaker_open:
                if time.time() - self.circuit_breaker_last_failure > self.circuit_breaker_timeout:
                    self.circuit_breaker_open = False
                    self.circuit_breaker_failures = 0
                    logger.info("Circuit breaker reset for Nym")

