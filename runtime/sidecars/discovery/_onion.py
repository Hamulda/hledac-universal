"""
runtime/sidecars/discovery/_onion.py — F-ISSUE-005: OnionDiscoverySidecarAdapter
================================================================================

ARCHITECTURE NOTE (ISSUE-1 FIX):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_onion_discovery_sidecar()
  (Branch D in run_advisory_runner()).

  If registered in both SidecarRegistry AND orchestrator, the same sidecar would
  run twice per sprint - once via run_plugin_sidecars() (Branch E) and once via
  orchestrator methods (Branch D).

CAPABILITY CHECK:
  - Protocol: "tor"
  - READY: Tor SOCKS5 proxy running and circuit established
  - STUB: TorTransport.start() not called or circuit not established
  - UNAVAILABLE: Tor binary not found

REAL IMPLEMENTATION:
  This sidecar performs actual .onion crawling via Tor transport.
  If Tor is unavailable, the sidecar skips with clear logging.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter
from core import aclose


class OnionDiscoverySidecarAdapter(DarknetSidecarAdapter):
    """F251: Dark web .onion discovery via Tor transport.

    F-ISSUE-005: Now uses DarknetSidecarAdapter for capability-aware execution.
    The sidecar only runs if Tor transport is READY.

    NOTE: Not registered in SidecarRegistry - executed via orchestrator Branch D.
    """

    sidecar_id: str = "onion_discovery"
    env_gate: str = "HLEDAC_ENABLE_TOR"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_onion_discovery_sidecar"
    protocol: str = "tor"  # F-ISSUE-005: Required by DarknetSidecarAdapter
