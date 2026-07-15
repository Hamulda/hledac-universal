"""
runtime/source_finding_config.py
================================

Bridge configuration dataclasses for CT, Wayback, PDNS, DOH, RDAP sources.

Extracted from runtime/source_finding_bridge.py (Issue #12).

Architecture:
    - msgspec.Struct for zero-copy performance (5-10× faster than dataclass)
    - Frozen instances (immutable after construction)
    - Bounded collections with explicit max sizes
    - Fail-safe defaults — no field is None
"""
from __future__ import annotations

from typing import Final

import msgspec


# ---------------------------------------------------------------------------
# Rejection reason system
# ---------------------------------------------------------------------------

RejectionReason = str

# Backward-compatible rejection record (dict-based for flexibility)
# Old code used Rejection = str; new code passes dicts with reason/domain/value
Rejection = dict


# Standard rejection reasons
REJECTION_MISSING_DOMAIN: Final[str] = "missing_domain"
REJECTION_MISSING_VALUE: Final[str] = "missing_value"
REJECTION_LOW_INFORMATION: Final[str] = "low_information"
REJECTION_DUPLICATE_CANDIDATE: Final[str] = "duplicate_candidate"
REJECTION_UNSUPPORTED_SHAPE: Final[str] = "unsupported_shape"
REJECTION_WILDCARD_DOMAIN: Final[str] = "wildcard_domain"
REJECTION_PRIVATE_OR_RESERVED_DOMAIN: Final[str] = "private_or_reserved_domain"
REJECTION_STORAGE_UNAVAILABLE: Final[str] = "storage_unavailable"
REJECTION_QUALITY_GATE: Final[str] = "quality_gate"
REJECTION_CANDIDATE_BUILT_NOT_STORED: Final[str] = "candidate_built_not_stored"


# ---------------------------------------------------------------------------
# Bridge configuration structs
# ---------------------------------------------------------------------------

_BRIDGE_CONFIDENCE_MAX: Final = 1.0
_BRIDGE_CONFIDENCE_MIN: Final = 0.0


class BridgeConfig(msgspec.Struct, frozen=True):
    """
    Base configuration for any source → CanonicalFinding bridge.

    Fields:
        source_type: canonical source identifier string
        confidence: base confidence score in (0.0, 1.0]
        salt: blake2b domain-separation salt (non-empty ASCII)
    """

    source_type: str
    confidence: float
    salt: str

    def __post_init__(self) -> None:
        if not self.source_type:
            raise ValueError("source_type must be non-empty")
        if not (0.0 < self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in (0.0, 1.0], got {self.confidence}"
            )
        if not self.salt:
            raise ValueError("salt must be non-empty")


class CTBridgeConfig(BridgeConfig, frozen=True):
    """
    Certificate Transparency bridge configuration.

    Defaults (match original _CT_* constants in source_finding_bridge.py):
        source_type = "ct"
        confidence = 0.65
        salt = "ctbridge"
    """

    source_type: str = "ct"
    confidence: float = 0.65
    salt: str = "ctbridge"


class WaybackBridgeConfig(BridgeConfig, frozen=True):
    """
    Wayback Machine (CDX diff) bridge configuration.

    Defaults (match original _WAYBACK_* constants in source_finding_bridge.py):
        source_type = "wayback_diff"
        confidence = 0.75
        salt = "waybackbridge"
    """

    source_type: str = "wayback_diff"
    confidence: float = 0.75
    salt: str = "waybackbridge"


class PDNSBridgeConfig(BridgeConfig, frozen=True):
    """
    Passive DNS bridge configuration.

    Defaults (match original _PDNS_* constants in source_finding_bridge.py):
        source_type = "passive_dns"
        confidence = 0.50
        salt = "pdnsbridge"
    """

    source_type: str = "passive_dns"
    confidence: float = 0.50
    salt: str = "pdnsbridge"


class DOHBridgeConfig(BridgeConfig, frozen=True):
    """
    DNS-over-HTTPS (DOH) bridge configuration.

    Defaults (match original _DOH_* constants in source_finding_bridge.py):
        source_type = "doh"
        confidence = 0.55
        salt = "dohbridge"
    """

    source_type: str = "doh"
    confidence: float = 0.55
    salt: str = "dohbridge"


class RDAPBridgeConfig(BridgeConfig, frozen=True):
    """
    RDAP enrichment bridge configuration.

    Defaults (match original _RDAP_* constants in source_finding_bridge.py):
        source_type = "rdap_enrichment"
        confidence = 0.70
        salt = "rdapbridge"
    """

    source_type: str = "rdap_enrichment"
    confidence: float = 0.70
    salt: str = "rdapbridge"


# ---------------------------------------------------------------------------
# Default instances (singleton pattern — frozen msgspec.Struct is safe)
# ---------------------------------------------------------------------------

CT_BRIDGE: Final[CTBridgeConfig] = CTBridgeConfig()
WAYBACK_BRIDGE: Final[WaybackBridgeConfig] = WaybackBridgeConfig()
PDNS_BRIDGE: Final[PDNSBridgeConfig] = PDNSBridgeConfig()
DOH_BRIDGE: Final[DOHBridgeConfig] = DOHBridgeConfig()
RDAP_BRIDGE: Final[RDAPBridgeConfig] = RDAPBridgeConfig()


# ---------------------------------------------------------------------------
# Global output bounds
# ---------------------------------------------------------------------------

MAX_BRIDGE_OUTPUT: Final[int] = 500
MAX_PAYLOAD_TEXT_CHARS: Final[int] = 2000
MAX_PROVENANCE_ITEMS: Final[int] = 20
MAX_SAMPLE_REJECTIONS: Final[int] = 5
MAX_CT_QUARANTINE_SAMPLES: Final[int] = 10
MAX_EXPANSION_CLUE_EXAMPLES: Final[int] = 5


# ---------------------------------------------------------------------------
# Private data — domain/IP classification
# ---------------------------------------------------------------------------

_PRIVATE_HOSTNAMES: Final[frozenset[str]] = frozenset({
    "localhost",
    "invalid",
    "test",
})

_PRIVATE_IP_PREFIXES: Final[tuple[str, ...]] = (
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "127.",
    "0.",
    "255.",
    "169.254.",
    "::1",
    "fe80:",
    "fc00:",
    "fd00:",
)


def is_private_hostname(hostname: str) -> bool:
    """Return True if hostname is a known private/reserved name."""
    return hostname in _PRIVATE_HOSTNAMES


def is_private_ip_prefix(value: str) -> bool:
    """Return True if value starts with a private/reserved IP prefix."""
    for prefix in _PRIVATE_IP_PREFIXES:
        if value.startswith(prefix):
            return True
    return False


def is_private_host(value: str) -> bool:
    """Return True if value is a private hostname or IP."""
    return is_private_hostname(value) or is_private_ip_prefix(value)


__all__ = [
    # Types
    "Rejection",
    "RejectionReason",
    # Rejection reason constants
    "REJECTION_MISSING_DOMAIN",
    "REJECTION_MISSING_VALUE",
    "REJECTION_LOW_INFORMATION",
    "REJECTION_DUPLICATE_CANDIDATE",
    "REJECTION_UNSUPPORTED_SHAPE",
    "REJECTION_WILDCARD_DOMAIN",
    "REJECTION_PRIVATE_OR_RESERVED_DOMAIN",
    "REJECTION_STORAGE_UNAVAILABLE",
    "REJECTION_QUALITY_GATE",
    "REJECTION_CANDIDATE_BUILT_NOT_STORED",
    # Bridge configs
    "BridgeConfig",
    "CTBridgeConfig",
    "WaybackBridgeConfig",
    "PDNSBridgeConfig",
    "DOHBridgeConfig",
    "RDAPBridgeConfig",
    # Default instances
    "CT_BRIDGE",
    "WAYBACK_BRIDGE",
    "PDNS_BRIDGE",
    "DOH_BRIDGE",
    "RDAP_BRIDGE",
    # Bounds
    "MAX_BRIDGE_OUTPUT",
    "MAX_PAYLOAD_TEXT_CHARS",
    "MAX_PROVENANCE_ITEMS",
    "MAX_SAMPLE_REJECTIONS",
    "MAX_CT_QUARANTINE_SAMPLES",
    "MAX_EXPANSION_CLUE_EXAMPLES",
    # Helpers
    "is_private_hostname",
    "is_private_ip_prefix",
    "is_private_host",
]
