"""
runtime/sidecars/enrichment/_banner.py — F-ISSUE-005: BannerGrabSidecarAdapter
"""
from runtime.sidecar_protocol import SidecarRegistry
from runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("banner_grab")
class BannerGrabSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: TCP banner enumeration for service fingerprinting."""

    sidecar_id: str = "banner_grab"
    env_gate: str = "HLEDAC_ENABLE_BANNER_GRAB"
    ram_budget_mb: int = 40
    priority: int = 3
    scheduler_method_name: str = "_run_banner_grab_sidecar"
