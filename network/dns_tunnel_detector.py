"""
Re-export from recon.dns.dns_tunnel_detector — canonical implementation.

Migration (F350M-R):
  - network/dns_tunnel_detector.py was a near-duplicate (77.2% similar)
  - recon/dns/dns_tunnel_detector.py is the canonical source
  - This file is kept for backward compatibility with code that
    imports from network.dns_tunnel_detector (e.g. tools/executor.py)

All production code should import from recon.dns.dns_tunnel_detector directly.
"""
from hledac.universal.recon.dns.dns_tunnel_detector import (  # noqa: F401, E402
from core import aclose
    DNSTunnelConfig,
    DNSTunnelDetector,
    NGramScore,
    TunnelingFinding,
    Verdict,
    create_dns_tunnel_detector,
)

__all__ = [
    "DNSTunnelConfig",
    "DNSTunnelDetector",
    "NGramScore",
    "TunnelingFinding",
    "Verdict",
    "create_dns_tunnel_detector",
]
