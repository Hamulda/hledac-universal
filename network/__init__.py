"""
Network Analysis Module
=======================

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

# Lazy loading for optional components
DNS_TUNNEL_DETECTOR_AVAILABLE = False
try:
    from .dns_tunnel_detector import (
        DNSTunnelConfig,
        DNSTunnelDetector,
        NGramScore,
        TunnelingFinding,
        create_dns_tunnel_detector,
    )
    DNS_TUNNEL_DETECTOR_AVAILABLE = True
except ImportError:
    DNSTunnelDetector = None  # type: ignore
    DNSTunnelConfig = None  # type: ignore
    TunnelingFinding = None  # type: ignore
    NGramScore = None  # type: ignore
    create_dns_tunnel_detector = None  # type: ignore

# ── Passive DNS ────────────────────────────────────────────────────────────────
PASSIVE_DNS_AVAILABLE = False
try:
    from .passive_dns import (
        DOH_RESOLVERS,
        PassiveDNSAdapter,
        PassiveDNSResolver,
    )
    PASSIVE_DNS_AVAILABLE = True
except ImportError:
    PassiveDNSResolver = None  # type: ignore
    PassiveDNSAdapter = None  # type: ignore
    DOH_RESOLVERS = None  # type: ignore

# ── Passive Fingerprint ──────────────────────────────────────────────────────
PASSIVE_FINGERPRINT_AVAILABLE = False
try:
    from .passive_fingerprint import (
        PassiveFingerprint,
        PassiveFingerprintAdapter,
    )
    PASSIVE_FINGERPRINT_AVAILABLE = True
except ImportError:
    PassiveFingerprint = None  # type: ignore
    PassiveFingerprintAdapter = None  # type: ignore

# ── Banner Grabber ────────────────────────────────────────────────────────────
BANNER_GRABBER_AVAILABLE = False
try:
    from .banner_grabber import (
        MAX_BANNER_GRABS,
        PORT_TIMEOUTS,
        BannerGrabber,
        BannerGrabberAdapter,
        BannerResult,
    )
    BANNER_GRABBER_AVAILABLE = True
except ImportError:
    BannerGrabber = None  # type: ignore
    BannerGrabberAdapter = None  # type: ignore
    BannerResult = None  # type: ignore
    MAX_BANNER_GRABS = 100  # type: ignore
    PORT_TIMEOUTS = {}  # type: ignore

# ── IPv6 Recon ────────────────────────────────────────────────────────────────
IPV6_RECON_AVAILABLE = False
try:
    from .ipv6_recon import (
        MAX_IPV6_TARGETS,
        IPv6Recon,
        IPv6ReconAdapter,
        IPv6Result,
    )
    IPV6_RECON_AVAILABLE = True
except ImportError:
    IPv6Recon = None  # type: ignore
    IPv6ReconAdapter = None  # type: ignore
    IPv6Result = None  # type: ignore
    MAX_IPV6_TARGETS = 50  # type: ignore

# ── Network Intelligence Adapter ─────────────────────────────────────────────
NETWORK_INTEL_AVAILABLE = False
try:
    from .network_intelligence import (
        MAX_NETWORKINTEL_TARGETS,
        NetworkIntelAdapter,
        NetworkIntelResult,
    )
    NETWORK_INTEL_AVAILABLE = True
except ImportError:
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
