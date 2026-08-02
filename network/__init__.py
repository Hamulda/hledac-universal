"""
Network Infrastructure & OSINT Re-exports
========================================

This module serves DUAL role after F350M-R Phase 2 migration:

INFRASTRUCTURE (canonical, unique to network/):
  - session_runtime: HTTP session management with curl_cffi
  - tor_manager: Tor proxy management
  - ipfs_client: IPFS gateway client
  - i2p_client: I2P SAM protocol client
  - zeronet_client: ZeroNet JSON API client
  - freenet_client: Freenet/Hyphanet FProxy client
  - ipv6_recon: IPv6 reconnaissance (RDAP, WHOIS)
  - banner_grabber: TCP banner enumeration
  - domain_concurrency: Per-domain concurrency control
  - favicon_hasher: Favicon hashing (XXH3)
  - js_bundle_extractor: JavaScript bundle analysis
  - js_source_map_extractor: Source map extraction
  - open_storage_scanner: S3/open storage discovery

OSINT RE-EXPORTS (lazy — avoid circular import with recon/):
  - dns_tunnel_detector → recon.dns.dns_tunnel_detector
  - passive_dns         → recon.dns.passive_dns
  - passive_fingerprint  → recon.passive_fingerprint
  - bgp_monitor         → recon.bgp_advisor_adapter
  - ct_log_scanner      → recon.cert.ct_log_scanner
  - network_intelligence → NetworkIntelAdapter wrapper

Canonical OSINT namespace: recon/ (see recon/__init__.py)
Backward-compat facade: intel/ (see intel/__init__.py)
"""

from __future__ import annotations

import importlib
import importlib.util
from importlib import import_module


# ── Lazy OSINT re-exports via __getattr__ ─────────────────────────────────────
# Avoids circular import: network/__init__ → recon.dns.passive_dns → session_runtime → network/__init__

_OSINT_TARGETS: dict[str, str] = {
    # dns_tunnel_detector
    "DNSTunnelDetector": "recon.dns.dns_tunnel_detector",
    "DNSTunnelConfig": "recon.dns.dns_tunnel_detector",
    "TunnelingFinding": "recon.dns.dns_tunnel_detector",
    "NGramScore": "recon.dns.dns_tunnel_detector",
    "create_dns_tunnel_detector": "recon.dns.dns_tunnel_detector",
    "DNS_TUNNEL_DETECTOR_AVAILABLE": "recon.dns.dns_tunnel_detector",
    # passive_dns
    "PassiveDNSResolver": "recon.dns.passive_dns",
    "PassiveDNSAdapter": "recon.dns.passive_dns",
    "DOH_RESOLVERS": "recon.dns.passive_dns",
    "PASSIVE_DNS_AVAILABLE": "recon.dns.passive_dns",
    # passive_fingerprint (K2: fixed path, was recon.network.passive_fingerprint)
    "PassiveFingerprintAdapter": "recon.passive_fingerprint",
    "PassiveTechStackAdapter": "recon.passive_fingerprint",
    "ServiceFingerprint": "recon.passive_fingerprint",
    "FingerprintResult": "recon.passive_fingerprint",
    "TechStack": "recon.passive_fingerprint",
    "PASSIVE_FINGERPRINT_AVAILABLE": "recon.passive_fingerprint",
    # bgp_monitor (K2: fixed path, was recon.network.bgp_monitor → canonical is recon.bgp_advisor_adapter)
    "BGPAdvisorAdapter": "recon.bgp_advisor_adapter",
    "create_bgp_advisor_adapter": "recon.bgp_advisor_adapter",
    "BGP_AVAILABLE": "recon.bgp_advisor_adapter",
    # ct_log_scanner
    "CTLogScanner": "recon.cert.ct_log_scanner",
    "ct_log_scanner_available": "recon.cert.ct_log_scanner",
    # network_intelligence (local re-export)
    "NetworkIntelAdapter": "network.network_intelligence",
    "NetworkIntelResult": "network.network_intelligence",
    "MAX_NETWORKINTEL_TARGETS": "network.network_intelligence",
}


def __getattr__(name: str):
    # Infrastructure modules — load eagerly from local . modules
    if name in (
        "session_runtime",
        "tor_manager",
        "ipfs_client",
        "i2p_client",
        "zeronet_client",
        "freenet_client",
        "ipv6_recon",
        "banner_grabber",
        "domain_concurrency",
        "favicon_hasher",
        "js_bundle_extractor",
        "js_source_map_extractor",
        "open_storage_scanner",
    ):
        return import_module(f".{name}", __package__)
    # OSINT re-exports — delegate to recon/ lazily
    if name in _OSINT_TARGETS:
        target = _OSINT_TARGETS[name]
        # target like "recon.dns.dns_tunnel_detector" → "recon.dns"
        parts = target.rsplit(".", 2)
        mod = import_module(f".{parts[0]}.{parts[1]}", "hledac.universal")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Infrastructure module availability flags (eager, at import time) ───────────

def _try_import(module_name: str) -> bool:
    try:
        importlib.import_module(f".{module_name}", __package__)
        return True
    except Exception:
        return False

DNS_TUNNEL_DETECTOR_AVAILABLE: bool = _try_import("dns_tunnel_detector")
BANNER_GRABBER_AVAILABLE: bool = _try_import("banner_grabber")
IPV6_RECON_AVAILABLE: bool = _try_import("ipv6_recon")
IPFS_AVAILABLE: bool = _try_import("ipfs_client")
ZERONET_AVAILABLE: bool = _try_import("zeronet_client")
FREENET_AVAILABLE: bool = _try_import("freenet_client")


__all__ = [
    "DNS_TUNNEL_DETECTOR_AVAILABLE",
    "PASSIVE_DNS_AVAILABLE",
    "PASSIVE_FINGERPRINT_AVAILABLE",
    "BANNER_GRABBER_AVAILABLE",
    "IPV6_RECON_AVAILABLE",
    "NETWORK_INTEL_AVAILABLE",
    "IPFS_AVAILABLE",
    "ZERONET_AVAILABLE",
    "FREENET_AVAILABLE",
]
