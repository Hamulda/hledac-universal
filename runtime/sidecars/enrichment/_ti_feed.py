"""
runtime/sidecars/enrichment/_ti_feed.py — F-ISSUE-005: TIFeedSidecarAdapter
================================================================================

ARCHITECTURE NOTE (FIX-5):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_ti_feed_sidecar()
  (Branch D in run_advisory_runner()).

  FIX-5: Removed @SidecarRegistry.register() decorator that caused dual execution.

CAPABILITY CHECK:
  - Protocol: "" (empty = clearnet/TI, no transport capability check)
  - MISSING_IMPLEMENTATION — NVD + CISA KEV integration not yet implemented

NOTE:
  TI feed advisory fetches structured threat intelligence from:
  - NVD (National Vulnerability Database) API
  - CISA KEV (Known Exploited Vulnerabilities) catalog
  Uses httpx for REST API calls - no P2P transport needed.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter
from _core import aclose


# FIX-5: Inherit from DarknetSidecarAdapter for proper capability handling
# but set protocol="" to indicate clearnet (no transport dependency)
class TIFeedSidecarAdapter(DarknetSidecarAdapter):
    """F252: Threat intelligence feed advisory (NVD + CISA KEV).

    FIX-5: Now uses DarknetSidecarAdapter with explicit protocol="" for
    clearnet classification. Not registered in SidecarRegistry - executed
    via orchestrator Branch D only.
    """

    sidecar_id: str = "ti_feed"
    env_gate: str = "HLEDAC_ENABLE_TI_FEEDS"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_ti_feed_sidecar"
    protocol: str = ""  # FIX-5: Empty = clearnet, no transport capability check
    capability_check_enabled: bool = False  # FIX-5: No transport dependency
