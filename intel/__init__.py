"""
Intel — OSINT Intelligence Lanes
================================

Sprint 8.7: Naming overlap resolution

Canonical location for OSINT intelligence adapters previously in network/.
Naming convention: *-lane.py (proven, readable).

MIGRATED (2026-07-02):
  network/bgp_monitor.py         → intel/bgp_monitor.py
  network/ct_log_scanner.py     → intel/ct_log_scanner.py
  network/dns_tunnel_detector.py → intel/dns_tunnel_detector.py
  network/gemini_transport.py   → intel/gemini_transport.py
  network/ipfs_client.py        → intel/ipfs_client.py (RE-EXPORTED, canonical: network/ipfs_client.py)
  network/jarm_fingerprinter.py → intel/jarm_fingerprinter.py
  network/passive_dns.py        → intel/passive_dns.py
  network/passive_fingerprint.py → intel/passive_fingerprint.py

EXISTING lanes (in intelligence/):
  greynoise_lane.py, shodan_lane.py, doh_lane.py,
  network_reconnaissance_lane.py, dark_web_lane.py,
  wayback_cdx.py, academic_search.py, archive_discovery.py,
  network_intelligence.py (in network/), exposed_service_hunter.py,
  data_leak_hunter.py, github_secret_scanner.py, social_identity_miner.py,
  temporal_archaeologist_adapter.py, bgp_advisor_adapter.py,
  leak_sentinel.py, ct_log_client.py, commoncrawl_adapter.py,
  onion_seed_manager.py, exposed_service_hunter.py, ...
"""
from __future__ import annotations

import importlib.util


# Re-export from intelligence/ (existing lanes)
from intelligence.greynoise_lane import *  # noqa: F401, E402, F403
from intelligence.shodan_lane import *  # noqa: F401, E402, F403
from intelligence.doh_lane import *  # noqa: F401, E402, F403
from intelligence.network_reconnaissance_lane import *  # noqa: F401, E402, F403
from intelligence.dark_web_lane import *  # noqa: F401, E402, F403
from intelligence.wayback_cdx import *  # noqa: F401, E402, F403
from intelligence.academic_search import *  # noqa: F401, E402, F403
from intelligence.archive_discovery import *  # noqa: F401, E402, F403
from intelligence.data_leak_hunter import *  # noqa: F401, E402, F403
from intelligence.github_secret_scanner import *  # noqa: F401, E402, F403
from intelligence.social_identity_miner import *  # noqa: F401, E402, F403
from intelligence.temporal_archaeologist_adapter import *  # noqa: F401, E402, F403
from intelligence.bgp_advisor_adapter import *  # noqa: F401, E402, F403
from intelligence.leak_sentinel import *  # noqa: F401, E402, F403
from intelligence.ct_log_client import *  # noqa: F401, E402, F403
_COMMCRWL_SPEC = importlib.util.find_spec("intelligence.commcrawl_adapter")
if _COMMCRWL_SPEC is not None:
    from intelligence.commcrawl_adapter import *  # noqa: F401, E402, F403, E402
from intelligence.onion_seed_manager import *  # noqa: F401, E402, F403, E402
from intelligence.exposed_service_hunter import *  # noqa: F401, E402, F403, E402
from intelligence.bgp_passive_dns_adapter import *  # noqa: F401, E402, F403, E402
from intelligence.network_reconnaissance import *  # noqa: F401, E402, F403, E402
from intelligence.temporal_analysis import *  # noqa: F401, E402, F403, E402
from intelligence.timeline_synthesizer import *  # noqa: F401, E402, F403, E402
from intelligence.academic_discovery import *  # noqa: F401, E402, F403, E402
from intelligence.confidence_policy import *  # noqa: F401, E402, F403, E402
from intelligence.entity_signal_extractor import *  # noqa: F401, E402, F403, E402
from intelligence.attribution_scorer import *  # noqa: F401, E402, F403, E402
from intelligence.kill_chain_tagger import *  # noqa: F401, E402, F403, E402
from intelligence.identity_stitching_canonical import *  # noqa: F401, E402, F403, E402
from intelligence.cryptographic_intelligence import *  # noqa: F401, E402, F403, E402
from intelligence.wayback_diff_miner import *  # noqa: F401, E402, F403, E402
from intelligence.wayback_cdx_deep_adapter import *  # noqa: F401, E402, F403, E402
from intelligence.document_intelligence import *  # noqa: F401, E402, F403, E402
from intelligence.advanced_image_osint import *  # noqa: F401, E402, F403, E402
from intelligence.blockchain_analyzer import *  # noqa: F401, E402, F403, E402
from intelligence.workflow_orchestrator import *  # noqa: F401, E402, F403, E402
from intelligence.pattern_mining import *  # noqa: F401, E402, F403, E402
from intelligence.exposure_correlator import *  # noqa: F401, E402, F403, E402

# Re-export from intel/ (migrated from network/)
from intel.bgp_monitor import *  # noqa: F401, E402, F403, E402
from intel.ct_log_scanner import *  # noqa: F401, E402, F403, E402
from intel.dns_tunnel_detector import *  # noqa: F401, E402, F403, E402
from intel.gemini_transport import *  # noqa: F401, E402, F403, E402
from hledac.universal.network.ipfs_client import *  # noqa: F401, E402
from intel.jarm_fingerprinter import *  # noqa: F401, E402, F403, E402
from intel.passive_dns import *  # noqa: F401, E402, F403, E402
from intel.passive_fingerprint import *  # noqa: F401, E402, F403, E402
