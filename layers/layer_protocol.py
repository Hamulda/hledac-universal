"""
Layer Protocol + LayerStack — Inversion of Control for Cross-Cutting Concerns

Design principles:
- Each layer is a Protocol (PEP 544): mount(ctx), unmount(ctx), on_event(event)
- LayerStack mounts layers in order, events propagate through in same order
- No global state — LayerStack is injected where needed
- UNIX domain socket for communication_layer (zero-copy on M1)
- StealthLayer is a Layer, not a transport — decoupled from advanced_web/

M1 8GB: All layer I/O is lazy — no heavy imports at module load.
"""
from __future__ import annotations


import asyncio
import logging
import socket
from abc import abstractmethod
from collections.abc import Callable, Coroutine, Set
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable, TYPE_CHECKING

from hledac.universal.utils.async_helpers import safe_create_task

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ─── Layer Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class Layer(Protocol):
    """
    Layer Protocol — all cross-cutting concerns implement this.

    Lifecycle:
        mount() → layer starts, registers with ctx
        unmount() → layer stops, cleans up
        on_event() → event propagates through mounted layers in order
    """

    layer_name: str

    async def mount(self, ctx: LayerContext) -> None:
        """Mount layer — called when layer is added to stack."""
        ...

    async def unmount(self, ctx: LayerContext) -> None:
        """Unmount layer — called when layer is removed or stack shuts down."""
        ...

    async def on_event(
        self, ctx: LayerContext, event: LayerEvent
    ) -> LayerEvent | None:
        """
        Handle event — return modified event or None to stop propagation.

        Events propagate through layers in mount order.
        A layer returning None halts propagation to subsequent layers.
        """
        ...


class LayerContext:
    """
    Shared context passed to all layers — minimal interface.

    Layers access subsystems via ctx.get(name) — no direct imports.
    This decouples layers from each other and from the orchestrator.
    """

    __slots__ = ("_services", "_meta", "_lock")

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._meta: dict[str, Any] = {}  # sprint_id, query, mode
        self._lock = asyncio.Lock()

    def set(self, key: str, value: Any) -> None:
        """Register a service or metadata."""
        self._services[key] = value

    def get(self, key: str) -> Any | None:
        """Get a registered service or metadata."""
        return self._services.get(key)

    def meta(self, key: str, default: Any = None) -> Any:
        """Get sprint metadata."""
        return self._meta.get(key, default)

    def set_meta(self, **kwargs: Any) -> None:
        """Set sprint metadata."""
        self._meta.update(kwargs)

    # ── Typed accessors for known services ────────────────────────────────

    @property
    def sprint_id(self) -> str | None:
        return self._meta.get("sprint_id")

    @property
    def query(self) -> str | None:
        return self._meta.get("query")

    @property
    def mode(self) -> str | None:
        return self._meta.get("mode")

    @property
    def memory_pressure(self) -> float:
        """Current memory pressure 0.0–1.0."""
        return self._meta.get("memory_pressure", 0.0)

    @memory_pressure.setter
    def memory_pressure(self, value: float) -> None:
        self._meta["memory_pressure"] = max(0.0, min(1.0, value))

    @property
    def cancel_event(self) -> asyncio.Event:
        """Cancellation event for this sprint."""
        return self._meta.get("cancel_event", asyncio.Event())


@dataclass
class LayerEvent:
    """Event that propagates through the LayerStack."""

    type: str  # "memory_pressure", "sprint_start", "sprint_end", ...
    data: dict[str, Any] = field(default_factory=dict)
    halted: bool = False  # True if a layer halted propagation

    def halt(self) -> None:
        """Stop propagation to subsequent layers."""
        self.halted = True


# ─── LayerStack ────────────────────────────────────────────────────────────────


class LayerStack:
    """
    Ordered stack of layers — mounts/unmounts in order, propagates events.

    Inversion-of-control: the stack calls layers, not the other way around.
    No global singleton — LayerStack is instantiated by SprintScheduler
    and passed to components that need it.

    Mount order = execution order for on_event propagation.
    Unmount order = reverse of mount order.
    """

    def __init__(self) -> None:
        self._layers: list[Layer] = []
        self._ctx: LayerContext | None = None
        self._mounted: bool = False
        self._lock = asyncio.Lock()

    # ── Layer management ─────────────────────────────────────────────────

    def add(self, layer: Layer, *, index: int | None = None) -> None:
        """
        Add a layer to the stack.

        Args:
            layer: Layer to add
            index: Insert position (append at end if None)
        """
        if index is None:
            self._layers.append(layer)
        else:
            self._layers.insert(index, layer)

    def remove(self, layer: Layer) -> bool:
        """Remove a layer from the stack."""
        try:
            self._layers.remove(layer)
            return True
        except ValueError:
            return False

    def get(self, name: str) -> Layer | None:
        """Get layer by layer_name."""
        for layer in self._layers:
            if getattr(layer, "layer_name", None) == name:
                return layer
        return None

    @property
    def layers(self) -> list[Layer]:
        """Read-only view of mounted layers."""
        return list(self._layers)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def mount(self, ctx: LayerContext) -> None:
        """
        Mount all layers in order.

        Each layer.mount(ctx) is called sequentially.
        On error, layers already mounted are unmounted in reverse.
        """
        from hledac.universal.utils.async_helpers import safe_wait_for

        if self._mounted:
            logger.warning("LayerStack already mounted")
            return

        self._ctx = ctx
        mounted: list[Layer] = []

        for layer in self._layers:
            name = getattr(layer, "layer_name", repr(layer))
            try:
                logger.debug(f"Mounting layer: {name}")
                await safe_wait_for(
                    layer.mount(ctx), timeout=30.0, label=f"mount:{name}"
                )
                mounted.append(layer)
            except Exception as e:
                logger.error(f"Layer mount failed: {name} — {e}")
                # Unmount already-mounted layers in reverse
                for rollback in reversed(mounted):
                    rname = getattr(rollback, "layer_name", repr(rollback))
                    try:
                        await safe_wait_for(
                            rollback.unmount(ctx), timeout=10.0, label=f"unmount:{rname}"
                        )
                    except Exception as rollback_err:
                        logger.warning(
                            f"Rollback unmount failed: {rname} — {rollback_err}"
                        )
                self._layers.clear()
                self._mounted = False
                raise

        self._mounted = True
        logger.info(f"LayerStack mounted ({len(self._layers)} layers)")

    async def unmount(self, ctx: LayerContext) -> None:
        """
        Unmount all layers in reverse order.

        Runs even if some layers fail — best-effort cleanup.
        """
        from hledac.universal.utils.async_helpers import safe_wait_for

        if not self._mounted:
            return

        for layer in reversed(self._layers):
            name = getattr(layer, "layer_name", repr(layer))
            try:
                await safe_wait_for(
                    layer.unmount(ctx), timeout=10.0, label=f"unmount:{name}"
                )
            except Exception as e:
                logger.warning(f"Layer unmount error: {name} — {e}")

        self._mounted = False
        logger.info("LayerStack unmounted")

    async def on_event(self, ctx: LayerContext, event: LayerEvent) -> LayerEvent:
        """
        Propagate event through layers in mount order.

        Each layer receives the event and can:
        - Pass it through unchanged → next layer receives it
        - Modify it → next layer receives modified event
        - Return None → halt propagation (event.halted = True)

        Returns the (possibly modified) event.
        """
        from hledac.universal.utils.async_helpers import safe_wait_for

        for layer in self._layers:
            if event.halted:
                break
            name = getattr(layer, "layer_name", repr(layer))
            try:
                result = await safe_wait_for(
                    layer.on_event(ctx, event),
                    timeout=30.0,
                    label=f"on_event:{name}",
                )
                if result is None:
                    event.halt()
            except Exception as e:
                logger.warning(f"Layer on_event error: {name} — {e}")

        return event

    # ── Convenience ────────────────────────────────────────────────────────

    async def broadcast(
        self, ctx: LayerContext, event_type: str, data: dict[str, Any] | None = None
    ) -> LayerEvent:
        """Broadcast an event to all layers."""
        event = LayerEvent(type=event_type, data=data or {})
        return await self.on_event(ctx, event)

    @property
    def is_mounted(self) -> bool:
        return self._mounted


# ─── Layer Errors ─────────────────────────────────────────────────────────────


class LayerMountError(Exception):
    """Raised when layer mount fails."""


class LayerUnmountError(Exception):
    """Raised when layer unmount fails."""


# ─── UNIX Domain Socket Transport ─────────────────────────────────────────────
# Zero-copy IPC for communication between processes on M1.


async def create_uds_server(
    path: str,
    handler: Callable[[Any], Coroutine[Any, Any, None]],
    *,
    backlog: int = 16,
) -> asyncio.AbstractServer:
    """
    Create a UNIX domain socket server.

    Args:
        path: Socket path (use abstract namespace on Darwin: b"\\x00name")
        handler: Async callback receiving (msg: dict, addr: str | None)
        backlog: Maximum pending connections (maps to sock.listen(backlog))

    Returns:
        asyncio.Server instance
    """
    loop = asyncio.get_running_loop()
    server = await loop.create_unix_server(
        lambda: _UDSProtocol(handler),
        path,
        backlog=backlog,
    )
    return server


class _UDSProtocol(asyncio.Protocol):
    """One-shot protocol — read msgpack, call handler, close."""

    __slots__ = ("_handler", "_buffer", "_transport")

    def __init__(self, handler: Callable[[Any], Coroutine[Any, Any, None]]) -> None:
        self._handler = handler
        self._buffer = bytearray()
        self._transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport

    def data_received(self, data: bytes) -> None:
        self._buffer.extend(data)
        # Try to parse msgpack
        try:
            import msgspec

            msg = msgspec.msgpack.decode(self._buffer)
            # F320: asyncio.ensure_future deprecated in Python 3.14+
            # safe_create_task: eager_start=True (3.12+), loop probe (F228G)
            safe_create_task(self._handler(msg), name="layer_protocol:msg_handler")
        except Exception:
            return  # Wait for more data
        finally:
            if self._transport:
                self._transport.close()

    def eof_received(self) -> None:
        if self._transport:
            self._transport.close()


async def uds_fetch(
    path: str,
    message: dict[str, Any],
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """
    Send a msgpack message over UNIX domain socket and get reply.

    Zero-copy on M1: kernel copies directly between processes.

    Args:
        path: Socket path
        message: Message to send (msgpack-encoded)
        timeout: Request timeout

    Returns:
        Response dict or None on error
    """
    try:
        import msgspec

        reader, writer = await safe_wait_for(
            asyncio.open_unix_connection(path), timeout=timeout, label="uds_connect"
        )
        try:
            writer.write(msgspec.msgpack.encode(message))
            await writer.drain()
            response_bytes = await safe_wait_for(reader.read(65536), timeout=timeout, label="uds_read")
            if response_bytes:
                return msgspec.msgpack.decode(response_bytes)
        finally:
            writer.close()
            await writer.wait_closed()
    except Exception as e:
        logger.debug(f"UDS fetch failed: {e}")
        return None
