"""
runtime/sidecars/enrichment/_ti_feed.py — F-ISSUE-005: TIFeedSidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry

from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("ti_feed")
class TIFeedSidecarAdapter(SchedulerBackedSidecarAdapter):
    """F252: Threat intelligence feed advisory (NVD + CISA KEV).

    Fetches structured TI feeds (NVD API + CISA KEV catalog) in parallel
    via SprintScheduler._run_ti_feed_sidecar(). Registered adapters are
    dispatched with parallel_ok for bounded concurrent execution.
    """

    sidecar_id: str = "ti_feed"
    env_gate: str = "HLEDAC_ENABLE_TI_FEEDS"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_ti_feed_sidecar"
