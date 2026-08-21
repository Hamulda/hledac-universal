"""
runtime/sidecars/__init__.py — F-ISSUE-005: Sidecars Package
============================================================

Central re-export point for all scheduler-backed sidecar adapters.
Kept for backward compatibility — new code should import directly from
the category packages (runtime.sidecars.discovery, etc.).

Categories:
  - discovery:  Onion, I2P, IPFS, DHT, CommonCrawl
  - enrichment: BGP, BannerGrab, TIFeed
  - forensics:   DigitalGhost, Steganography

PEP 562 lazy loading — each category is loaded only when first accessed.
"""

from __future__ import annotations

# Re-export SidecarRegistry for convenience
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry

__all__ = [
    # discovery
    "OnionDiscoverySidecarAdapter",
    "I2PDiscoverySidecarAdapter",
    "IPFSDiscoverySidecarAdapter",
    "DHTDiscoverySidecarAdapter",
    "CommonCrawlSidecarAdapter",
    # enrichment
    "BGPEnrichmentSidecarAdapter",
    "BannerGrabSidecarAdapter",
    "TIFeedSidecarAdapter",
    # forensics
    "DigitalGhostSidecarAdapter",
    "SteganographySidecarAdapter",
]

# Lazy-load cache: name -> class
_CACHE: dict[str, object] = {}
_LOADED = False


def __getattr__(name: str):
    global _LOADED
    if not _LOADED:
        _LOADED = True
        # Import all category packages (triggers @SidecarRegistry.register)
        # Using local imports to avoid namespace pollution
        from hledac.universal.runtime.sidecars.discovery import (
            CommonCrawlSidecarAdapter,
            DHTDiscoverySidecarAdapter,
            I2PDiscoverySidecarAdapter,
            IPFSDiscoverySidecarAdapter,
            OnionDiscoverySidecarAdapter,
        )
        from hledac.universal.runtime.sidecars.enrichment import (
            BannerGrabSidecarAdapter,
            BGPEnrichmentSidecarAdapter,
            TIFeedSidecarAdapter,
        )
        from hledac.universal.runtime.sidecars.forensics import DigitalGhostSidecarAdapter, SteganographySidecarAdapter

        _CACHE.update(
            {
                "OnionDiscoverySidecarAdapter": OnionDiscoverySidecarAdapter,
                "I2PDiscoverySidecarAdapter": I2PDiscoverySidecarAdapter,
                "IPFSDiscoverySidecarAdapter": IPFSDiscoverySidecarAdapter,
                "DHTDiscoverySidecarAdapter": DHTDiscoverySidecarAdapter,
                "CommonCrawlSidecarAdapter": CommonCrawlSidecarAdapter,
                "BGPEnrichmentSidecarAdapter": BGPEnrichmentSidecarAdapter,
                "BannerGrabSidecarAdapter": BannerGrabSidecarAdapter,
                "TIFeedSidecarAdapter": TIFeedSidecarAdapter,
                "DigitalGhostSidecarAdapter": DigitalGhostSidecarAdapter,
                "SteganographySidecarAdapter": SteganographySidecarAdapter,
            }
        )

    if name in _CACHE:
        return _CACHE[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
