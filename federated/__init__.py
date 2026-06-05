"""
F350M-FED: Federated research package — public surface.

Sprint: F350M-FED / Federated Activation 2026-06-04
Status: ACTIVE (gated on HLEDAC_ENABLE_FEDERATED=1)

This package provides the federated research capability for hledac.universal.
Activation is intentionally opt-in (off by default) to keep the M1 8GB
default sprint path unchanged.

The public surface is:
    FederatedResearchCoordinator  — multi-virtual-node orchestrator
    FederatedResult              — aggregated output type
    NodeResult                   — per-node diagnostic type
    NodeLane                     — lane identifiers (surface/dark/archive)
    FederatedQTable              — bounded in-memory Q-table
    FederatedBridge              — lazy Q-table persistence + optional hybrid
    MAX_VIRTUAL_NODES            — hard cap (3) on simultaneous nodes
    is_federated_enabled()       — module-level env-var gate check

Transports (F350M-FED-P — P2P Transport Activation):
    NodeTransport                       — Protocol contract
    NodeTransportFactory                — name → transport registry
    LaneDispatchTransport               — Tier 1: real per-lane backend dispatch
    PeerNodeTransport                   — Tier 2: real P2P (UDP + Noise XX + mDNS)
    InMemoryPeerNodeTransport           — test bridge (no I/O)
    is_peer_node_enabled()              — env-gate for Tier 2

See coordinator.py for full architecture, bounds, and invariants.
See qtable.py for the in-memory RL slice used per lane.
See bridge.py for the lazy Protocol facade to loops.research_loop + LMDB.
See transports/ for the real per-lane and P2P transport layer.

LEGACY STUB: sketches.py (the original placeholder) is retained for
backward compatibility but is no longer the canonical entry. Importing
from hledac.universal.federated should use this __init__.py instead.
"""

from __future__ import annotations

from typing import Any

from .bridge import (
    BRIDGE_CROSS_SPRINT_PERSIST,
    BRIDGE_LAZY_HYBRID,
    BRIDGE_LIGHTWEIGHT_ONLY,
    FederatedBridge,
    HYBRID_MAX_INSTANCES,
    LMDB_MAX_ENTRIES,
    LMDB_PERSIST_DEBOUNCE_S,
    LMDB_PERSIST_KEY,
    QTableProtocol,
)
from .coordinator import (
    AGGREGATION_MAX_FINDINGS,
    DISTRIBUTE_TOTAL_TIMEOUT_S,
    FederatedResearchCoordinator,
    FederatedResult,
    MAX_VIRTUAL_NODES,
    NodeLane,
    NodeResult,
    PER_NODE_MAX_FINDINGS,
    PER_NODE_TIMEOUT_S,
    is_federated_enabled,
)
from .qtable import MAX_QTABLE_ENTRIES, FederatedQTable

# Transports — imported lazily through __getattr__ to avoid pulling
# heavy modules (cryptography, zeroconf) into the cold-start path when
# the federated capability is disabled (the default).

__all__ = [
    # Core
    "FederatedResearchCoordinator",
    "FederatedResult",
    "NodeResult",
    "NodeLane",
    "FederatedQTable",
    # Bridge (F350M-FED-P3)
    "FederatedBridge",
    "QTableProtocol",
    "BRIDGE_LIGHTWEIGHT_ONLY",
    "BRIDGE_LAZY_HYBRID",
    "BRIDGE_CROSS_SPRINT_PERSIST",
    # Bounds
    "MAX_VIRTUAL_NODES",
    "MAX_QTABLE_ENTRIES",
    "PER_NODE_MAX_FINDINGS",
    "PER_NODE_TIMEOUT_S",
    "DISTRIBUTE_TOTAL_TIMEOUT_S",
    "AGGREGATION_MAX_FINDINGS",
    "LMDB_MAX_ENTRIES",
    "LMDB_PERSIST_DEBOUNCE_S",
    "LMDB_PERSIST_KEY",
    "HYBRID_MAX_INSTANCES",
    # Gate
    "is_federated_enabled",
    # Transports (F350M-FED-P) — lazy via __getattr__
    "NodeTransport",
    "NodeTransportFactory",
    "LaneDispatchTransport",
    "PeerNodeTransport",
    "InMemoryPeerNodeTransport",
    "is_peer_node_enabled",
]


_TRANSPORT_EXPORTS: dict[str, str] = {
    "NodeTransport": "federated.transports.protocol.NodeTransport",
    "NodeTransportFactory": "federated.transports.protocol.NodeTransportFactory",
    "LaneDispatchTransport": "federated.transports.lane_dispatch.LaneDispatchTransport",
    "PeerNodeTransport": "federated.transports.peer_node.PeerNodeTransport",
    "InMemoryPeerNodeTransport": "federated.transports.inmemory_peer.InMemoryPeerNodeTransport",
    "is_peer_node_enabled": "federated.transports.peer_node.is_peer_node_enabled",
}


def __getattr__(name: str) -> Any:
    """
    Lazy attribute resolution for the federated package.

    Defers heavy work (logger init, RL slice allocation, transport
    module imports with cryptography/zeroconf) until first attribute
    access. This keeps the M1 sprint cold start lean when the
    federated capability is disabled (the default).

    Transport names (e.g. ``LaneDispatchTransport``) are loaded on
    first access via importlib — this preserves the M1 8GB cold-start
    budget while still exposing the full transport surface.
    """
    if name in __all__:
        if name in _TRANSPORT_EXPORTS:
            import importlib
            module_path, attr = _TRANSPORT_EXPORTS[name].rsplit(".", 1)
            mod = importlib.import_module(module_path)
            value = getattr(mod, attr)
            # Cache for subsequent accesses.
            globals()[name] = value
            return value
        # All public names are already bound above; this branch should
        # not normally fire. Provided for forward-compat with dynamic
        # attribute injection.
        return globals().get(name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r} "
        f"(known: {sorted(__all__)})"
    )
