"""
F350M-FED-P: Federated Transports — public surface.

Sprint: F350M-FED-P / P2P Transport Activation 2026-06-04
Status: ACTIVE (opt-in via factory pattern)

This package replaces the empty `_LocalNodeTransport` stub with
a real, layered transport system. The seam contract is the
`NodeTransport` Protocol (see protocol.py):

    async def run(self, lane: str, query: str) -> list[dict]

REGISTERED TRANSPORTS
=====================

Name               Class                          Tier   Opt-in
-----------------  -----------------------------  -----  --------------------
"local"            _LocalNodeTransport (stub)     T0     default (backward compat)
"lane_dispatch"    LaneDispatchTransport          T1     HLEDAC_ENABLE_FEDERATED=1
"peer_node"        PeerNodeTransport              T2     HLEDAC_ENABLE_FEDERATED_P2P=1
"inmemory_peer"    InMemoryPeerNodeTransport      test   always available

LAYERED ACTIVATION
==================
- Default (legacy): _LocalNodeTransport — returns []. This is what
  the coordinator used before this sprint. Backward compatible.
- Tier 1: LaneDispatchTransport — dispatches per-lane to real
  backends (FetchCoordinator, StealthCrawler, Wayback/CommonCrawl).
  Always available when federated is enabled.
- Tier 2: PeerNodeTransport — real P2P mesh over UDP + Noise XX
  + mDNS / DNS-SD. Cross-host federation. Opt-in.
- Test: InMemoryPeerNodeTransport — no I/O, used by tests.

USAGE
=====
    from hledac.universal.federated.transports import NodeTransportFactory

    transport = NodeTransportFactory.create("lane_dispatch")
    findings = await transport.run("surface", "test query")
"""

from __future__ import annotations

# Import the Protocol + factory first (no side effects beyond the class def).
from .protocol import NodeTransport, NodeTransportFactory

# Import the lane_dispatch module — registers "lane_dispatch" on import.
from . import lane_dispatch as _lane_dispatch_mod  # noqa: F401  (registration side effect)

# Import the peer_node module — registers "peer_node" on import.
from . import peer_node as _peer_node_mod  # noqa: F401  (registration side effect)

# Import the inmemory_peer module — registers "inmemory_peer" on import.
from . import inmemory_peer as _inmem_mod  # noqa: F401  (registration side effect)

# NOTE: The legacy `_LocalNodeTransport` is NOT registered through the
# factory. It remains the direct default in `FederatedResearchCoordinator`
# (coordinator.py). This avoids a circular import (transports/ would
# otherwise import from coordinator/, which is being imported by the
# federated package init). The factory's _EmergencyLocalTransport
# remains the last-resort safety net for unknown names.
#
# To use the legacy stub via the factory, callers can either:
#   (a) import _LocalNodeTransport directly and inject it, or
#   (b) instantiate FederatedResearchCoordinator(transport=_LocalNodeTransport()).
NodeTransportFactory.set_default("inmemory_peer")


# Re-export key names for ergonomic imports.
from .lane_dispatch import (  # noqa: E402
    LANE_DISPATCH_MAX_FINDINGS,
    LANE_DISPATCH_TIMEOUT_S,
    LaneDispatchTransport,
)
from .peer_node import (  # noqa: E402
    PEER_NODE_HANDSHAKE_TIMEOUT_S,
    PEER_NODE_MAX_PEERS,
    PEER_NODE_MSG_MAX_BYTES,
    PeerNodeTransport,
    is_peer_node_enabled,
)
from .inmemory_peer import (  # noqa: E402
    INMEMORY_PEER_MAX_PEERS,
    InMemoryPeerNodeTransport,
)


__all__ = [
    "NodeTransport",
    "NodeTransportFactory",
    # Concrete classes (for type hints and direct construction):
    "LaneDispatchTransport",
    "PeerNodeTransport",
    "InMemoryPeerNodeTransport",
    # Bound constants (useful for downstream callers):
    "LANE_DISPATCH_TIMEOUT_S",
    "LANE_DISPATCH_MAX_FINDINGS",
    "PEER_NODE_MAX_PEERS",
    "PEER_NODE_HANDSHAKE_TIMEOUT_S",
    "PEER_NODE_MSG_MAX_BYTES",
    "INMEMORY_PEER_MAX_PEERS",
    # Env-gate helper:
    "is_peer_node_enabled",
]
