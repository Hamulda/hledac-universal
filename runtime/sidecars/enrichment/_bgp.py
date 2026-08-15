"""
runtime/sidecars/enrichment/_bgp.py — F-ISSUE-005: BGPEnrichmentSidecarAdapter
================================================================================

ARCHITECTURE NOTE (FIX-5):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_bgp_enrichment_sidecar()
  (Branch D in run_advisory_runner()).

  FIX-5: Removed @SidecarRegistry.register() decorator that caused dual execution.

CAPABILITY CHECK:
  - Protocol: "" (empty = clearnet/enrichment, no transport capability check)
  - BGP enrichment uses REST APIs (RIPE RIS, RouteViews, CAIDA) - no P2P transport

NOTE:
  BGP enrichment performs AS path analysis for IP/ASN in query results.
  Uses CAIDA AS Organizations dataset and RIPE RIS API for BGP data.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter
from core import aclose


# FIX-5: Inherit from DarknetSidecarAdapter for proper capability handling
# but set protocol="" to indicate clearnet (no transport dependency)
class BGPEnrichmentSidecarAdapter(DarknetSidecarAdapter):
    """F229: BGP enrichment — AS path analysis for IP/ASN in query.

    FIX-5: Now uses DarknetSidecarAdapter with explicit protocol="" for
    clearnet classification. Not registered in SidecarRegistry - executed
    via orchestrator Branch D only.
    """

    sidecar_id: str = "bgp_enrichment"
    env_gate: str = "HLEDAC_ENABLE_BGP"
    ram_budget_mb: int = 60
    priority: int = 5
    scheduler_method_name: str = "_run_bgp_enrichment_sidecar"
    protocol: str = ""  # FIX-5: Empty = clearnet, no transport capability check
    capability_check_enabled: bool = False  # FIX-5: No transport dependency
