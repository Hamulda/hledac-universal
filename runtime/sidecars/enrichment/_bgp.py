"""
runtime/sidecars/enrichment/_bgp.py — F-ISSUE-005: BGPEnrichmentSidecarAdapter
"""
from runtime.sidecar_protocol import SidecarRegistry
from runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("bgp_enrichment")
class BGPEnrichmentSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: BGP enrichment — AS path analysis for IP/ASN in query."""

    sidecar_id: str = "bgp_enrichment"
    env_gate: str = "HLEDAC_ENABLE_BGP"
    ram_budget_mb: int = 60
    priority: int = 5
    scheduler_method_name: str = "_run_bgp_enrichment_sidecar"
