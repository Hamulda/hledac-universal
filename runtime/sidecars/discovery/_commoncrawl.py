"""
runtime/sidecars/discovery/_commoncrawl.py — F-ISSUE-005: CommonCrawlSidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry
from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("commoncrawl")
class CommonCrawlSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F250F: CommonCrawl CDX domain discovery.

    Placeholder — the scheduler method `_run_commoncrawl_sidecar` is not
    yet implemented (previously called from `sidecar_orchestrator` via
    `getattr` and silently no-op'd). This adapter documents the gap and
    makes it observable in `SidecarRegistry.get_all_registered()` output.
    """

    sidecar_id: str = "commoncrawl"
    env_gate: str = "HLEDAC_ENABLE_COMMONCRAWL"
    ram_budget_mb: int = 60
    priority: int = 3
    scheduler_method_name: str = "_run_commoncrawl_sidecar"
    missing_method_expected: bool = True
