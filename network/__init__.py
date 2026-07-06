"""
Network Analysis Module

Network-based OSINT and threat detection capabilities:
- DNS Tunneling Detector: Cascade detection with entropy, N-gram, and MLX LSTM
- PCAP streaming analysis with constant memory
- Passive DNS (DoH multi-resolver)
- Passive Fingerprinting (Shodan, GreyNoise, CIRCL, VT, SecurityTrails)
- Banner Grabbing (TCP async, Tor + curl_cffi)
- IPv6 Recon (RDAP, WHOIS, DoH AAAA, BGP peer)
- NetworkIntelAdapter (unified wrapper)

M1 8GB Optimized: Streaming algorithms, <1GB memory regardless of PCAP size
"""
from __future__ import annotations

import importlib.util

# Optional deps detection via find_spec — avoids exception overhead
# Pattern: if spec exists, import eagerly; else set _AVAILABLE=False + stubs

# ── DNS Tunneling Detector ────────────────────────────────────────────────────
_DNS_SPEC = importlib.util.find_spec("hledac.universal.network.dns_tunnel_detector")
DNS_TUNNEL_DETECTOR_AVAILABLE = _DNS_SPEC is not None
if DNS_TUNNEL_DETECTOR_AVAILABLE:
    from .dns_tunnel_detector import (
        DNSTunnelConfig,
        DNSTunnelDetector,
        NGramScore,
        TunnelingFinding,
        create_dns_tunnel_detector,
    )
else:
    DNSTunnelDetector = None  # type: ignore
    DNSTunnelConfig = None  # type: ignore
    TunnelingFinding = None  # type: ignore
    NGramScore = None  # type: ignore
    create_dns_tunnel_detector = None  # type: ignore

# ── Passive DNS ────────────────────────────────────────────────────────────────
_PDNS_SPEC = importlib.util.find_spec("hledac.universal.network.passive_dns")
PASSIVE_DNS_AVAILABLE = _PDNS_SPEC is not None
if PASSIVE_DNS_AVAILABLE:
    from .passive_dns import (
        DOH_RESOLVERS,
        PassiveDNSAdapter,
        PassiveDNSResolver,
    )
else:
    PassiveDNSResolver = None  # type: ignore
    PassiveDNSAdapter = None  # type: ignore
    DOH_RESOLVERS = None  # type: ignore

# ── Passive Fingerprint ────────────────────────────────────────────────────────
_PFGP_SPEC = importlib.util.find_spec("hledac.universal.network.passive_fingerprint")
PASSIVE_FINGERPRINT_AVAILABLE = _PFGP_SPEC is not None
if PASSIVE_FINGERPRINT_AVAILABLE:
    from .passive_fingerprint import (
        PassiveFingerprint,
        PassiveFingerprintAdapter,
    )
else:
    PassiveFingerprint = None  # type: ignore
    PassiveFingerprintAdapter = None  # type: ignore

# ── Banner Grabber ─────────────────────────────────────────────────────────────
_BGRAB_SPEC = importlib.util.find_spec("hledac.universal.network.banner_grabber")
BANNER_GRABBER_AVAILABLE = _BGRAB_SPEC is not None
if BANNER_GRABBER_AVAILABLE:
    from .banner_grabber import (
        MAX_BANNER_GRABS,
        PORT_TIMEOUTS,
        BannerGrabber,
        BannerGrabberAdapter,
        BannerResult,
    )
else:
    BannerGrabber = None  # type: ignore
    BannerGrabberAdapter = None  # type: ignore
    BannerResult = None  # type: ignore
    MAX_BANNER_GRABS = 100  # type: ignore
    PORT_TIMEOUTS = {}  # type: ignore

# ── IPv6 Recon ────────────────────────────────────────────────────────────────
_IPV6_SPEC = importlib.util.find_spec("hledac.universal.network.ipv6_recon")
IPV6_RECON_AVAILABLE = _IPV6_SPEC is not None
if IPV6_RECON_AVAILABLE:
    from .ipv6_recon import (
        MAX_IPV6_TARGETS,
        IPv6Recon,
        IPv6ReconAdapter,
        IPv6Result,
    )
else:
    IPv6Recon = None  # type: ignore
    IPv6ReconAdapter = None  # type: ignore
    IPv6Result = None  # type: ignore
    MAX_IPV6_TARGETS = 50  # type: ignore

# ── Network Intelligence Adapter ──────────────────────────────────────────────
_NINTEL_SPEC = importlib.util.find_spec("hledac.universal.network.network_intelligence")
NETWORK_INTEL_AVAILABLE = _NINTEL_SPEC is not None
if NETWORK_INTEL_AVAILABLE:
    from .network_intelligence import (
        MAX_NETWORKINTEL_TARGETS,
        NetworkIntelAdapter,
        NetworkIntelResult,
    )
else:
    NetworkIntelAdapter = None  # type: ignore
    NetworkIntelResult = None  # type: ignore
    MAX_NETWORKINTEL_TARGETS = 20  # type: ignore

__all__ = [
    "DNS_TUNNEL_DETECTOR_AVAILABLE",
    "PASSIVE_DNS_AVAILABLE",
    "PASSIVE_FINGERPRINT_AVAILABLE",
    "BANNER_GRABBER_AVAILABLE",
    "IPV6_RECON_AVAILABLE",
    "NETWORK_INTEL_AVAILABLE",
]

if DNS_TUNNEL_DETECTOR_AVAILABLE:
    __all__.extend([
        "DNSTunnelDetector",
        "DNSTunnelConfig",
        "TunnelingFinding",
        "NGramScore",
        "create_dns_tunnel_detector",
    ])

if PASSIVE_DNS_AVAILABLE:
    __all__.extend([
        "PassiveDNSResolver",
        "PassiveDNSAdapter",
        "DOH_RESOLVERS",
    ])

if PASSIVE_FINGERPRINT_AVAILABLE:
    __all__.extend([
        "PassiveFingerprint",
        "PassiveFingerprintAdapter",
    ])

if BANNER_GRABBER_AVAILABLE:
    __all__.extend([
        "BannerGrabber",
        "BannerGrabberAdapter",
        "BannerResult",
        "MAX_BANNER_GRABS",
        "PORT_TIMEOUTS",
    ])

if IPV6_RECON_AVAILABLE:
    __all__.extend([
        "IPv6Recon",
        "IPv6ReconAdapter",
        "IPv6Result",
        "MAX_IPV6_TARGETS",
    ])

if NETWORK_INTEL_AVAILABLE:
    __all__.extend([
        "NetworkIntelAdapter",
        "NetworkIntelResult",
        "MAX_NETWORKINTEL_TARGETS",
    ])
