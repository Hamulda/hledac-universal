"""
runtime/sidecars/discovery/_dht.py — F-ISSUE-005: DHTDiscoverySidecarAdapter
================================================================================

ARCHITECTURE NOTE (ISSUE-1 FIX):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_dht_sidecar()
  (Branch D in run_advisory_runner()).

CAPABILITY CHECK:
  - Protocol: "dht"
  - READY: DHT network accessible and responding
  - STUB: KademliaNode initialized but _transport is None (simulated mode)
  - MISSING_IMPLEMENTATION: DHT crawler not integrated with sidecar orchestrator

NOTE:
  The original DHT implementation uses KademliaNode which may be in simulated
  mode (transport=None). Real DHT requires rust p2p_harvest implementation.

REAL IMPLEMENTATION:
  This sidecar performs DHT crawling via the configured transport.
  If DHT is in stub mode, no real DHT crawling occurs.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter


class DHTDiscoverySidecarAdapter(DarknetSidecarAdapter):
    """F214Q: DHT torrent discovery via BitTorrent DHT network.

    F-ISSUE-005: Now uses DarknetSidecarAdapter for capability-aware execution.
    The sidecar only runs if DHT transport is not in stub mode.

    NOTE: Not registered in SidecarRegistry - executed via orchestrator Branch D.
    """

    sidecar_id: str = "dht_discovery"
    env_gate: str = "HLEDAC_ENABLE_DHT"
    ram_budget_mb: int = 100
    priority: int = 4
    scheduler_method_name: str = "_run_dht_sidecar"
    protocol: str = "dht"  # F-ISSUE-005: Required by DarknetSidecarAdapter
