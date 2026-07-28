"""
runtime/sidecars/discovery/_onion.py — F-ISSUE-005: OnionDiscoverySidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry
from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("onion_discovery")
class OnionDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F251: Dark web .onion discovery via Tor transport."""

    sidecar_id: str = "onion_discovery"
    env_gate: str = "HLEDAC_ENABLE_TOR"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_onion_discovery_sidecar"
