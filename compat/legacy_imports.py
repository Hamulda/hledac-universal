"""
compat/legacy_imports.py — F350M-R A4: Single Module Map for All Legacy Shim Redirects
=====================================================================================

This module is the SINGLE SOURCE OF TRUTH for all backward-compatibility shims
that redirect deprecated import paths to their canonical targets.

PATTERN:
  All deprecation is handled HERE — no scattered compat/*.py shim files.
  Each entry emits DeprecationWarning on import using a shared helper.

DEPRECATED MODULES (intel/ → recon/):
  All intel/* imports are redirected to recon/*.
  See _INTEL_RECON_MAP below.

DEPRECATED MODULES (compat/ legacy stubs):
  compat.core_* → canonical paths (see individual entries below).

RATIONALE:
  Rather than 60 individual intel/*.py stub files and 8 compat/*.py shims,
  this single module provides a programmatic, version-controlled map
  that tools (importlens, migration scripts) can consume.

MIGRATION:
  All callers should migrate to canonical paths.
  After the deprecation window, physical stub files in intel/ and compat/
  will be removed and only this module will remain (as a future import
  compatibility layer if needed).
"""

from __future__ import annotations

import warnings
from importlib import import_module
from typing import Final

__all__ = [
    "INTEL_RECON_MAP",
    "warn_deprecation",
    "lazy_import",
]


# ── intel/ → recon/ canonical map ────────────────────────────────────────────
# Kept in sync with intel/__init__.__getattr__._RECON_MAP.
# Format: "intel_module_name": "canonical_recon_module"

INTEL_RECON_MAP: Final[dict[str, str]] = {
    # primitives (recon/ subdirs)
    "bgp_monitor": "recon.network.bgp_monitor",
    "passive_fingerprint": "recon.network.passive_fingerprint",
    "passive_dns": "recon.dns.passive_dns",
    "dns_tunnel_detector": "recon.dns.dns_tunnel_detector",
    "ct_log_scanner": "recon.cert.ct_log_scanner",
    "jarm_fingerprinter": "recon.protocols.jarm_fingerprinter",
    "gemini_transport": "recon.protocols.gemini_transport",
    "intel_seed": "recon.intel_seed",
    # capability forest (recon/ root)
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
}


def warn_deprecation(deprecated: str, canonical: str, stacklevel: int = 2) -> None:
    """
    Emit a DeprecationWarning for a deprecated import path.

    Args:
        deprecated: the deprecated full import path (e.g. "intel.bgp_monitor")
        canonical:  the canonical import path (e.g. "recon.network.bgp_monitor")
        stacklevel: passed through to warnings.warn (default=2 means caller's frame)
    """
    warnings.warn(
        f"{deprecated} is deprecated — import from \"{canonical}\" directly instead.",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def lazy_import(name: str):
    """
    Import a canonical module, emitting a deprecation warning if it is
    a known legacy intel/ → recon/ redirect.

    Args:
        name: fully-qualified module name being accessed

    Returns:
        the imported module
    """
    parts = name.rsplit(".", 1)
    if len(parts) == 2 and parts[0] == "intel" and parts[1] in INTEL_RECON_MAP:
        canonical = INTEL_RECON_MAP[parts[1]]
        warn_deprecation(f"intel.{parts[1]}", canonical)
        return import_module(canonical)

    # Not a known legacy redirect — import normally (no warning)
    return import_module(name)
