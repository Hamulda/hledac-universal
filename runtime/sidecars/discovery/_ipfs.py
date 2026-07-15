"""
runtime/sidecars/discovery/_ipfs.py — F-ISSUE-005: IPFSDiscoverySidecarAdapter
"""
from runtime.sidecar_protocol import SidecarRegistry
from runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("ipfs_discovery")
class IPFSDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F229: IPFS discovery — fetch unindexed content from IPFS network.

    Note: the previous `sidecar_orchestrator._run_ipfs_discovery_sidecar`
    wrapper called `_run_ipfs_enrichment_sidecar` on the scheduler — a typo
    that caused silent no-op execution. This adapter binds the CORRECT
    method name, restoring IPFS discovery functionality.
    """

    sidecar_id: str = "ipfs_discovery"
    env_gate: str = "HLEDAC_ENABLE_IPFS"
    ram_budget_mb: int = 80
    priority: int = 5
    scheduler_method_name: str = "_run_ipfs_discovery_sidecar"
