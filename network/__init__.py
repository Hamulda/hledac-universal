"""
Network Infrastructure & OSINT Re-exports
========================================

This module serves DUAL role after F350M-R Phase 2 migration:

INFRASTRUCTURE (canonical, unique to network/):
  - session_runtime: HTTP session management with curl_cffi
  - tor_manager: Tor proxy management
  - ipfs_client: IPFS gateway client
  - i2p_client: I2P SAM protocol client
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
  - passive_fingerprint  → recon.network.passive_fingerprint
  - bgp_monitor         → recon.network.bgp_monitor
  - ct_log_scanner      → recon.cert.ct_log_scanner
  - network_intelligence → NetworkIntelAdapter wrapper

Canonical OSINT namespace: recon/ (see recon/__init__.py)
Backward-compat facade: intel/ (see intel/__init__.py)
"""

import importlib
import importlib.util
from importlib import import_module


# ── Lazy OSINT re-exports via __getattr__ ─────────────────────────────────────
# Avoids circular import: network/__init__ → recon.dns.passive_dns → session_runtime → network/__init__

_OSINT_TARGETS = {
    "DNSTunnelDetector": "recon.dns.dns_tunnel_detector",
    "DNSTunnelConfig": "recon.dns.dns_tunnel_detector",
    "TunnelingFinding": "recon.dns.dns_tunnel_detector",
    "NGramScore": "recon.dns.dns_tunnel_detector",
    "create_dns_tunnel_detector": "recon.dns.dns_tunnel_detector",
    "DNS_TUNNEL_DETECTOR_AVAILABLE": "recon.dns.dns_tunnel_detector",
    "PassiveDNSResolver": "recon.dns.passive_dns",
    "PassiveDNSAdapter": "recon.dns.passive_dns",
    "DOH_RESOLVERS": "recon.dns.passive_dns",
    "PASSIVE_DNS_AVAILABLE": "recon.dns.passive_dns",
    "PassiveFingerprint": "recon.network.passive_fingerprint",
    "PassiveFingerprintAdapter": "recon.network.passive_fingerprint",
    "PASSIVE_FINGERPRINT_AVAILABLE": "recon.network.passive_fingerprint",
    "BGP_AVAILABLE": "recon.network.bgp_monitor",
    "extract_public_ips_from_text": "recon.network.bgp_monitor",
    "monitor_bgp": "recon.network.bgp_monitor",
    "monitor_bgp_as_findings": "recon.network.bgp_monitor",
    "bgp_enrich_to_canonical": "recon.network.bgp_monitor",
    "enrich_ip_as_finding": "recon.network.bgp_monitor",
    "CTLogScanner": "recon.cert.ct_log_scanner",
    "ct_log_scanner_available": "recon.cert.ct_log_scanner",
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
        mod = import_module(f".{target.split('.', 2)[2]}", "hledac.universal")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Infrastructure modules — eager imports ────────────────────────────────────

_DNS_SPEC = importlib.util.find_spec("hledac.universal.network.dns_tunnel_detector")
DNS_TUNNEL_DETECTOR_AVAILABLE = _DNS_SPEC is not None
if DNS_TUNNEL_DETECTOR_AVAILABLE:
    from .dns_tunnel_detector import (  # noqa: F401, E402
        DNSTunnelConfig,
        DNSTunnelDetector,
        NGramScore,
        TunnelingFinding,
        create_dns_tunnel_detector,
    )

_BGRAB_SPEC = importlib.util.find_spec("hledac.universal.network.banner_grabber")
BANNER_GRABBER_AVAILABLE = _BGRAB_SPEC is not None
if BANNER_GRABBER_AVAILABLE:
    from .banner_grabber import (  # noqa: F401, E402
        MAX_BANNER_GRABS,
        PORT_TIMEOUTS,
        BannerGrabber,
        BannerGrabberAdapter,
        BannerResult,
    )

_IPV6_SPEC = importlib.util.find_spec("hledac.universal.network.ipv6_recon")
IPV6_RECON_AVAILABLE = _IPV6_SPEC is not None
if IPV6_RECON_AVAILABLE:
    from .ipv6_recon import (  # noqa: F401, E402
        MAX_IPV6_TARGETS,
        IPv6Recon,
        IPv6ReconAdapter,
        IPv6Result,
    )

_IPFS_SPEC = importlib.util.find_spec("hledac.universal.network.ipfs_client")
IPFS_AVAILABLE = _IPFS_SPEC is not None
if IPFS_AVAILABLE:
    from .ipfs_client import (  # noqa: F401, E402
        MAX_FILE_SIZE_BYTES,
        ipfs_content_to_finding_dict,
        ipfs_fetch_as_findings,
        ipfs_search_as_findings,
    )


__all__ = [
    "DNS_TUNNEL_DETECTOR_AVAILABLE",
    "PASSIVE_DNS_AVAILABLE",
    "PASSIVE_FINGERPRINT_AVAILABLE",
    "BANNER_GRABBER_AVAILABLE",
    "IPV6_RECON_AVAILABLE",
    "NETWORK_INTEL_AVAILABLE",
    "IPFS_AVAILABLE",
]
