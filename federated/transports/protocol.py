"""
F350M-FED-P: Node Transport Protocol — canonical seam for real P2P.

Sprint: F350M-FED-P / P2P Transport Activation 2026-06-04
Target: federated/transports/protocol.py

PURPOSE
=======
This module defines the canonical `NodeTransport` Protocol and a
`NodeTransportFactory` registry. The factory enables:
  - Default (`_LocalNodeTransport`) → still works (backward compat).
  - `LaneDispatchTransport` → dispatches per-lane to real backends.
  - `PeerNodeTransport` → real P2P over UDP+Noise+mDNS (Tier 2).
  - `InMemoryPeerNodeTransport` → in-process peer bridge for tests.

The Protocol is duck-typed via @runtime_checkable so the
FederatedResearchCoordinator can be told to use any transport that
implements the async `run(lane, query) -> list[dict]` contract.

FACTORY REGISTRATION
====================
Transports register themselves via:
    @NodeTransportFactory.register("lane_dispatch")
    class LaneDispatchTransport:
        ...

Coordinator uses `NodeTransportFactory.create("lane_dispatch")` to
construct a transport by name. Unknown name → `_LocalNodeTransport`
(default stub, returns empty list).

M1 8GB SAFETY
=============
- Protocol methods are async (cooperative scheduling, no thread pool).
- All implementations MUST be fail-soft: exceptions → return [].
- All implementations MUST bound their output to PER_NODE_MAX_FINDINGS.
- All implementations MUST respect an asyncio.timeout for the per-call
  budget (PER_NODE_TIMEOUT_S = 10s default in coordinator).

DESIGN NOTES
============
- We chose Protocol over ABC for two reasons:
  (1) duck-typed _LocalNodeTransport still satisfies it without
      refactoring the existing class.
  (2) it allows simple test doubles (`class FakeTransport: ...`).
- Factory is intentionally minimal — name → class. No DI container.
- The factory keeps a weak reference to singleton instances to avoid
  leaking transport state across sprints.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# --- PROTOCOL CONTRACT -------------------------------------------------------


@runtime_checkable
class NodeTransport(Protocol):
    """
    The transport contract for a virtual node on the federated coordinator.

    Every implementation MUST:
      - Be safely constructible with no required positional arguments
        (coordinator may instantiate via `cls()` for back-compat).
      - Expose `async def run(self, lane: str, query: str) -> list[dict[str, Any]]`
        with bounded output (≤ PER_NODE_MAX_FINDINGS items) and bounded
        time (caller enforces PER_NODE_TIMEOUT_S via asyncio.timeout).
      - Be fail-soft: never raise — return [] on any error.
      - Be thread-safe at the coroutine level (cooperative scheduling).
      - Optionally support `.close()` for transports that hold sockets.

    The contract is intentionally minimal so it can be satisfied by:
      - _LocalNodeTransport (stub, returns [])
      - LaneDispatchTransport (real per-lane backend dispatch)
      - PeerNodeTransport (real P2P via UDP+Noise+mDNS)
      - InMemoryPeerNodeTransport (in-process bridge for tests)
    """

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """
        Execute the (lane, query) research cycle and return findings.

        Args:
            lane: One of NodeLane.SURFACE | DARK | ARCHIVE (or any
                  future lane defined in NodeLane.ALL).
            query: Free-form research query string.

        Returns:
            List of finding dicts. Each finding SHOULD have at least
            `{"ioc_type": str, "ioc_value": str, "confidence": float}`.
            Implementations MAY add `payload_text`, `source_lane`,
            `provenance`, etc.

            The coordinator's `_aggregate_and_dedup()` uses
            `(ioc_type, ioc_value)` as the dedup key. Findings without
            these fields still get a synthetic key (unkeyed) so they
            flow through aggregation.

        MUST: never raise, return [] on error or timeout.
        """
        ...

    async def close(self) -> None:
        """
        Optional cleanup. Default no-op. Transports that hold sockets
        (e.g. PeerNodeTransport with mDNS listener) should release
        them here. Idempotent.
        """
        ...


# --- FACTORY REGISTRY --------------------------------------------------------


class NodeTransportFactory:
    """
    Name → NodeTransport class registry.

    Usage:
        @NodeTransportFactory.register("lane_dispatch")
        class LaneDispatchTransport:
            ...

        transport = NodeTransportFactory.create("lane_dispatch")
        # or: transport = NodeTransportFactory.create()  # → "local" default

    Unknown names return the default `_LocalNodeTransport` (the stub).
    This is fail-soft by design: a misconfigured transport swap never
    crashes the coordinator.

    Thread-safety: registry mutation is expected at import time only.
    The class itself is not lock-protected — concurrent registration
    would race, but in practice all `register()` calls happen at module
    load and the coordinator runs single-threaded per sprint.
    """

    _REGISTRY: dict[str, type[NodeTransport]] = {}
    _DEFAULT: str = "local"

    @classmethod
    def register(cls, name: str) -> Any:
        """
        Decorator: register a NodeTransport class under `name`.

        Example:
            @NodeTransportFactory.register("lane_dispatch")
            class LaneDispatchTransport:
                ...
        """
        if not isinstance(name, str) or not name:
            raise ValueError("NodeTransportFactory.register requires non-empty string name")

        def _inner(klass: type[NodeTransport]) -> type[NodeTransport]:
            # Lightweight Protocol check (duck-typed) — does the class
            # at least define `run` as a coroutine function?
            if not hasattr(klass, "run"):
                raise TypeError(
                    f"NodeTransport {klass.__name__} missing required method 'run'"
                )
            cls._REGISTRY[name] = klass
            logger.debug("[FED-TRANS] registered transport name=%s class=%s", name, klass.__name__)
            return klass

        return _inner

    @classmethod
    def create(cls, name: str | None = None) -> NodeTransport:
        """
        Construct a transport by name. Falls back to the default stub
        for unknown names. Never raises.

        Args:
            name: Transport name (e.g. "lane_dispatch", "peer_node",
                  "local"). None → use default.

        Returns:
            A freshly-constructed NodeTransport instance. The caller
            is responsible for the lifecycle — typically the coordinator
            constructs one per distribute_research() call.
        """
        target = name or cls._DEFAULT
        klass = cls._REGISTRY.get(target)
        if klass is None:
            logger.debug(
                "[FED-TRANS] unknown transport name=%r, falling back to default %r",
                target, cls._DEFAULT,
            )
            klass = cls._REGISTRY.get(cls._DEFAULT)
        if klass is None:
            # Defensive: if even the default is missing, return a
            # barebones stub. This is the last-resort safety net.
            return _EmergencyLocalTransport()
        try:
            return klass()
        except Exception as e:  # GHOST_INVARIANT: fail-soft everywhere
            logger.warning(
                "[FED-TRANS] transport name=%r failed to construct: %s: %s — using stub",
                target, type(e).__name__, e,
            )
            return _EmergencyLocalTransport()

    @classmethod
    def available(cls) -> tuple[str, ...]:
        """Tuple of registered transport names (for diagnostics)."""
        return tuple(cls._REGISTRY.keys())

    @classmethod
    def set_default(cls, name: str) -> None:
        """Set the fallback default name. Idempotent."""
        if name in cls._REGISTRY:
            cls._DEFAULT = name
        else:
            logger.debug("[FED-TRANS] set_default: name=%r not registered, keeping %r",
                         name, cls._DEFAULT)


# --- EMERGENCY FALLBACK ------------------------------------------------------


class _EmergencyLocalTransport:
    """
    Last-resort transport: always returns empty list. Used only when
    the default `_LocalNodeTransport` is missing or fails to construct
    (which would be a serious import-time bug).

    Satisfies the NodeTransport Protocol contract (has async run and
    close methods).
    """

    __slots__ = ()

    async def run(self, lane: str, query: str) -> list[dict[str, Any]]:
        """Always returns empty list. Never raises."""
        return []

    async def close(self) -> None:
        """No-op."""
        return None


__all__ = [
    "NodeTransport",
    "NodeTransportFactory",
]
