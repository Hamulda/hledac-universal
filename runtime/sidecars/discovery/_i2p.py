"""
runtime/sidecars/discovery/_i2p.py — F-ISSUE-005: I2PDiscoverySidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry
from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("i2p_discovery")
class I2PDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F2P: I2P .i2p discovery via I2P transport."""

    sidecar_id: str = "i2p_discovery"
    env_gate: str = "HLEDAC_ENABLE_I2P"
    ram_budget_mb: int = 50
    priority: int = 4
    scheduler_method_name: str = "_run_i2p_discovery_sidecar"
