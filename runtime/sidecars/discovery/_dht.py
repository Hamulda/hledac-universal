"""
runtime/sidecars/discovery/_dht.py — F-ISSUE-005: DHTDiscoverySidecarAdapter
"""
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry
from hledac.universal.runtime.sidecars._base import SchedulerBackedSidecarAdapter


@SidecarRegistry.register("dht_discovery")
class DHTDiscoverySidecarAdapter(SchedulerBackedSidecarAdapter):
    """F214Q: DHT torrent discovery via BitTorrent DHT network.

    Coexists with `DHTSidecarAdapter` (F350M-R) which uses
    `discovery.dht_adapter.DHTAdapter`. The two paths are independent —
    this one delegates to the scheduler's pre-existing implementation
    which has its own Kademlia client wiring. Future work: pick one as
    canonical and deprecate the other.
    """

    sidecar_id: str = "dht_discovery"
    env_gate: str = "HLEDAC_ENABLE_DHT"
    ram_budget_mb: int = 100
    priority: int = 4
    scheduler_method_name: str = "_run_dht_sidecar"
