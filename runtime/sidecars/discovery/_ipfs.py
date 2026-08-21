"""
runtime/sidecars/discovery/_ipfs.py — F-ISSUE-005: IPFSDiscoverySidecarAdapter
================================================================================

ARCHITECTURE NOTE (ISSUE-1 FIX):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_ipfs_discovery_sidecar()
  (Branch D in run_advisory_runner()).

CAPABILITY CHECK:
  - Protocol: "ipfs"
  - READY: At least one public IPFS gateway accessible
  - MISSING_IMPLEMENTATION: Only HTTP gateway fallback (no libp2p Kademlia/BitSwap)

NOTE:
  IPFS capability detection checks public gateway accessibility (ipfs.io, etc.)
  NOT full libp2p Kademlia/BitSwap which requires rust bindings.

REAL IMPLEMENTATION:
  This sidecar performs IPFS content discovery via HTTP gateways.
  Full IPFS (libp2p) requires rust p2p_harvest implementation.
"""

from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter


class IPFSDiscoverySidecarAdapter(DarknetSidecarAdapter):
    """F229: IPFS discovery — fetch unindexed content from IPFS network.

    F-ISSUE-005: Now uses DarknetSidecarAdapter for capability-aware execution.
    The sidecar only runs if IPFS gateway is accessible.

    NOTE: Not registered in SidecarRegistry - executed via orchestrator Branch D.
    """

    sidecar_id: str = "ipfs_discovery"
    env_gate: str = "HLEDAC_ENABLE_IPFS"
    ram_budget_mb: int = 80
    priority: int = 5
    scheduler_method_name: str = "_run_ipfs_discovery_sidecar"
    protocol: str = "ipfs"  # F-ISSUE-005: Required by DarknetSidecarAdapter
