"""
Re-export from recon.network.bgp_monitor — canonical implementation.

Migration (F350M-R):
  - network/bgp_monitor.py was a near-duplicate (99.7% similar)
  - recon/network/bgp_monitor.py is the canonical source
  - This file is kept for backward compatibility with code that
    imports from network.bgp_monitor

All production code should import from recon.network.bgp_monitor directly.
"""
from hledac.universal.recon.network.bgp_monitor import (  # noqa: F401, E402
    BGP_AVAILABLE,
    extract_public_ips_from_text,
    monitor_bgp,
    monitor_bgp_as_findings,
    bgp_enrich_to_canonical,
    enrich_ip_as_finding,
)

__all__ = [
    "BGP_AVAILABLE",
    "extract_public_ips_from_text",
    "monitor_bgp",
    "monitor_bgp_as_findings",
    "bgp_enrich_to_canonical",
    "enrich_ip_as_finding",
]
