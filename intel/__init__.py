"""
Intel — Backward-Compat Facade (stub-free)
==========================================

ARCHITECTURE (post-F350M-R Phase 2):
  recon/           — Canonical OSINT namespace (capability forest + primitives)
  network/         — Network primitives (passive_dns, bgp_monitor, etc.)
  intel/          — DEPRECATED facade → recon/ + network/ (emits DeprecationWarning)

RECON/ SUBSTRUCTURE:
  recon/dns/          — passive_dns, dns_tunnel_detector
  recon/cert/         — ct_log_scanner
  recon/network/      — bgp_monitor, passive_fingerprint
  recon/protocols/    — jarm_fingerprinter, gemini_transport
  recon/              — intel_seed + 35+ capability forest modules

DEPRECATED (F350M-R A4):
  Physical stub files removed — ``__getattr__`` handles all redirects.
  All ``from intel.X`` imports emit DeprecationWarning and delegate
  to canonical paths in ``recon/*`` or ``network/*``.

Migration (search+replace):
  ``from hledac.universal.intel.X`` → ``from hledac.universal.recon.X``
  ``from hledac.universal.intel.bgp_monitor`` → ``from hledac.universal.network.bgp_monitor``
  ``from hledac.universal.intel.passive_dns`` → ``from hledac.universal.recon.dns.passive_dns``
  ``from hledac.universal.intel.passive_fingerprint`` → ``from hledac.universal.network.passive_fingerprint``
  ``from hledac.universal.intel.ct_log_scanner`` → ``from hledac.universal.network.ct_log_scanner``
"""

from __future__ import annotations

import warnings
from importlib import import_module
from _core import aclose


# Canonical redirect map — all physical stubs removed (F350M-R A4-5)
_RECON_MAP: dict[str, str] = {
    # network/ primitives
    "bgp_monitor": "network.bgp_monitor",
    # F350M-R: Updated to canonical recon.passive_fingerprint (network/passive_fingerprint.py removed)
    "passive_fingerprint": "recon.passive_fingerprint",
    "ct_log_scanner": "network.ct_log_scanner",
    "passive_dns": "recon.dns.passive_dns",
    "dns_tunnel_detector": "recon.dns.dns_tunnel_detector",
    # recon/protocols/
    "jarm_fingerprinter": "recon.protocols.jarm_fingerprinter",
    "gemini_transport": "recon.protocols.gemini_transport",
    # recon/ root
    "intel_seed": "recon.intel_seed",
    # capability forest (recon/)
    "greynoise_lane": "recon.greynoise_lane",
    "shodan_lane": "recon.shodan_lane",
    "doh_lane": "recon.doh_lane",
    "network_reconnaissance_lane": "recon.network_reconnaissance_lane",
    "dark_web_lane": "recon.dark_web_lane",
    "wayback_cdx": "recon.wayback_cdx",
    "academic_search": "recon.academic_search",
    "archive_discovery": "recon.archive_discovery",
    "data_leak_hunter": "recon.data_leak_hunter",
    "github_secret_scanner": "recon.github_secret_scanner",
    "social_identity_miner": "recon.social_identity_miner",
    "temporal_archaeologist_adapter": "recon.temporal_archaeologist_adapter",
    "bgp_advisor_adapter": "recon.bgp_advisor_adapter",
    "leak_sentinel": "recon.leak_sentinel",
    "ct_log_client": "recon.ct_log_client",
    "onion_seed_manager": "recon.onion_seed_manager",
    "exposed_service_hunter": "recon.exposed_service_hunter",
    "bgp_passive_dns_adapter": "recon.bgp_passive_dns_adapter",
    "network_reconnaissance": "recon.network_reconnaissance",
    "temporal_analysis": "recon.temporal_analysis",
    "timeline_synthesizer": "recon.timeline_synthesizer",
    "academic_discovery": "recon.academic_discovery",
    "confidence_policy": "recon.confidence_policy",
    "entity_signal_extractor": "recon.entity_signal_extractor",
    "attribution_scorer": "recon.attribution_scorer",
    "kill_chain_tagger": "recon.kill_chain_tagger",
    "identity_stitching_canonical": "recon.identity_stitching_canonical",
    "cryptographic_intelligence": "recon.cryptographic_intelligence",
    "wayback_diff_miner": "recon.wayback_diff_miner",
    "wayback_cdx_deep_adapter": "recon.wayback_cdx_deep_adapter",
    "document_intelligence": "recon.document_intelligence",
    "advanced_image_osint": "recon.advanced_image_osint",
    "blockchain_analyzer": "recon.blockchain_analyzer",
    "workflow_orchestrator": "recon.workflow_orchestrator",
    "pattern_mining": "recon.pattern_mining",
    "exposure_correlator": "recon.exposure_correlator",
    "dark_web_intelligence": "recon.dark_web_intelligence",
    "stealth_crawler": "recon.stealth_crawler",
    "web_intelligence": "recon.web_intelligence",
    "browser_pool": "recon.browser_pool",
    "relationship_discovery": "recon.relationship_discovery",
    "pattern_mining_canonical": "recon.pattern_mining_canonical",
    "blockchain_analyzer_lane": "recon.blockchain_analyzer_lane",
    "input_detector": "recon.input_detector",
    "censys_lane": "recon.censys_lane",
    "commoncrawl_adapter": "recon.commoncrawl_adapter",
    "pastebin_monitor": "recon.pastebin_monitor",
    "identity_stitching": "recon.identity_stitching",
    "exposure_clients": "recon.exposure_clients",
    "lane": "recon.lane",
    "ct_lane": "recon.ct_lane",
    "bgp_lane": "recon.bgp_lane",
    # F350M-R A5: Added missing redirects
    "shodan_wrapper": "recon.shodan_wrapper",
    "whois_service": "recon.whois_service",
    "streaming_embedder": "recon.streaming_embedder",
}


def __getattr__(name: str):
    if name in _RECON_MAP:
        canonical = _RECON_MAP[name]
        warnings.warn(
            f"intel.{name} is deprecated — import from \"{canonical}\" directly instead.",
            DeprecationWarning,
            stacklevel=2,
    )
        return import_module(canonical)
    raise AttributeError(f"module 'intel' has no attribute {name!r}")
