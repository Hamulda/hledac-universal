"""
runtime/sidecars/enrichment/_banner.py — F-ISSUE-005: BannerGrabSidecarAdapter
================================================================================

ARCHITECTURE NOTE (FIX-5):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_banner_grab_sidecar()
  (Branch D in run_advisory_runner()).

  FIX-5: Removed @SidecarRegistry.register() decorator that caused dual execution.

CAPABILITY CHECK:
  - Protocol: "" (empty = clearnet/enrichment, no transport capability check)
  - MISSING_IMPLEMENTATION — active TCP probing not yet implemented

NOTE:
  Banner grab performs active TCP probing for service fingerprinting.
  Currently a stub - needs integration with masscan/nmap for real implementation.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter


# FIX-5: Inherit from DarknetSidecarAdapter for proper capability handling
# but set protocol="" to indicate clearnet (no transport dependency)
class BannerGrabSidecarAdapter(DarknetSidecarAdapter):
    """F229: TCP banner enumeration for service fingerprinting.

    FIX-5: Now uses DarknetSidecarAdapter with explicit protocol="" for
    clearnet classification. Not registered in SidecarRegistry - executed
    via orchestrator Branch D only.
    """

    sidecar_id: str = "banner_grab"
    env_gate: str = "HLEDAC_ENABLE_BANNER_GRAB"
    ram_budget_mb: int = 40
    priority: int = 3
    scheduler_method_name: str = "_run_banner_grab_sidecar"
    protocol: str = ""  # FIX-5: Empty = clearnet, no transport capability check
    capability_check_enabled: bool = False  # FIX-5: No transport dependency
