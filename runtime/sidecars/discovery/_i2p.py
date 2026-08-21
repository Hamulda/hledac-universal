"""
runtime/sidecars/discovery/_i2p.py — F-ISSUE-005: I2PDiscoverySidecarAdapter
================================================================================

ARCHITECTURE NOTE (ISSUE-1 FIX):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_i2p_discovery_sidecar()
  (Branch D in run_advisory_runner()).

CAPABILITY CHECK:
  - Protocol: "i2p"
  - READY: I2P SAM v3 client connected and session active
  - STUB: I2P HTTP/SOCKS proxy available but SAM v3 not connected
  - UNAVAILABLE: I2P SAM bridge not running on port 7656

REAL IMPLEMENTATION:
  This sidecar performs actual I2P eepsite discovery via SAM v3.
  If I2P SAM is unavailable, falls back to SOCKS5 if available.
"""

from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter


class I2PDiscoverySidecarAdapter(DarknetSidecarAdapter):
    """F2P: I2P .i2p discovery via I2P transport.

    F-ISSUE-005: Now uses DarknetSidecarAdapter for capability-aware execution.
    The sidecar only runs if I2P transport is READY.

    NOTE: Not registered in SidecarRegistry - executed via orchestrator Branch D.
    """

    sidecar_id: str = "i2p_discovery"
    env_gate: str = "HLEDAC_ENABLE_I2P"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_i2p_discovery_sidecar"
    protocol: str = "i2p"  # F-ISSUE-005: Required by DarknetSidecarAdapter
