"""
runtime/sidecars/discovery/_commoncrawl.py — F-ISSUE-005: CommonCrawlSidecarAdapter
================================================================================

ARCHITECTURE NOTE (ISSUE-1 FIX):
  This adapter is NOT registered in SidecarRegistry to avoid dual-path execution.
  It is executed exclusively via SidecarOrchestrator._run_commoncrawl_sidecar()
  (Branch C in run_advisory_runner()).

CAPABILITY CHECK:
  - Protocol: N/A (this is HTTP-based)
  - MISSING_IMPLEMENTATION: CommonCrawl CDX API access not implemented

NOTE:
  CommonCrawl is a clearnet HTTP API, not a darknet protocol.
  However, it follows the same stub pattern as darknet sidecars.

REAL IMPLEMENTATION:
  This sidecar is a placeholder. Real implementation would use
  CommonCrawl CDX API to discover historical crawl data.
"""
from hledac.universal.runtime.sidecars._darknet_base import DarknetSidecarAdapter
from core import aclose


class CommonCrawlSidecarAdapter(DarknetSidecarAdapter):
    """F250F: CommonCrawl CDX domain discovery.

    F-ISSUE-005: Now uses DarknetSidecarAdapter for capability-aware execution.
    The sidecar is marked as MISSING_IMPLEMENTATION.

    NOTE: Not registered in SidecarRegistry - executed via orchestrator Branch C.
    """

    sidecar_id: str = "commoncrawl"
    env_gate: str = "HLEDAC_ENABLE_COMMONCRAWL"
    ram_budget_mb: int = 60
    priority: int = 3
    scheduler_method_name: str = "_run_commoncrawl_sidecar"
    # F-ISSUE-005: Protocol not applicable for clearnet HTTP API
    protocol: str = ""  # Empty for clearnet protocols

    # CommonCrawl doesn't have a "transport" capability, but we mark it
    # as missing implementation since _run_commoncrawl_sidecar is not implemented
    skip_on_missing: bool = True  # Always skip until implemented
