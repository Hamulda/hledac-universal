"""
runtime/sidecar_legacy_adapters.py — F-ISSUE-005: Redirect to runtime.sidecars
================================================================================

This module is kept for backward compatibility.
All adapters have been migrated to the `runtime.sidecars` package:

    discovery/  — Onion, I2P, IPFS, DHT, CommonCrawl
    enrichment/  — BGP, BannerGrab, TIFeed
    forensics/   — DigitalGhost, Steganography

Import adapters directly from the new location:
    from runtime.sidecars.discovery import OnionDiscoverySidecarAdapter
    from runtime.sidecars.enrichment import BGPEnrichmentSidecarAdapter
    from runtime.sidecars.forensics import DigitalGhostSidecarAdapter

Or use the central re-export:
    from runtime.sidecars import OnionDiscoverySidecarAdapter
    from runtime.sidecars import BGPEnrichmentSidecarAdapter

This module re-exports everything from runtime.sidecars for seamless migration.
"""
from __future__ import annotations

# Re-export everything from the new canonical location
from runtime.sidecars import (  # noqa: F401
    OnionDiscoverySidecarAdapter,
    I2PDiscoverySidecarAdapter,
    IPFSDiscoverySidecarAdapter,
    DHTDiscoverySidecarAdapter,
    CommonCrawlSidecarAdapter,
    BGPEnrichmentSidecarAdapter,
    BannerGrabSidecarAdapter,
    TIFeedSidecarAdapter,
    DigitalGhostSidecarAdapter,
    SteganographySidecarAdapter,
)

# Backward-compatibility shim: bind_scheduler delegates to the new location
from runtime.sidecars._base import bind_scheduler as _bind_scheduler

# Re-export ensure_legacy_adapters_registered as a no-op (adapters auto-register)
def ensure_legacy_adapters_registered() -> None:
    """Ensure all legacy scheduler-backed sidecar adapters are registered.

    Idempotent — adapters are now registered via @SidecarRegistry.register
    at import time. Calling this function is a no-op, kept only for
    backward compatibility with existing call sites.
    """
    # Adapters auto-register via @SidecarRegistry.register decorator
    # when the runtime.sidecars package is imported above.
    return None
