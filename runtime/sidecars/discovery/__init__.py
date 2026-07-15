"""
runtime/sidecars/discovery/__init__.py — F-ISSUE-005: Discovery Sidecars
=========================================================================

PEP 562 lazy loading — adapters loaded only when first accessed.
Adapters: Onion, I2P, IPFS, DHT, CommonCrawl.
"""
from __future__ import annotations

__all__ = [
    "OnionDiscoverySidecarAdapter",
    "I2PDiscoverySidecarAdapter",
    "IPFSDiscoverySidecarAdapter",
    "DHTDiscoverySidecarAdapter",
    "CommonCrawlSidecarAdapter",
]

# Lazy-load cache: name -> class
_CACHE: dict[str, object] = {}
_LOADED = False


def __getattr__(name: str):
    global _LOADED
    if not _LOADED:
        _LOADED = True
        # Import submodules to trigger @SidecarRegistry.register
        from runtime.sidecars.discovery import _onion as _onion_mod
        from runtime.sidecars.discovery import _i2p as _i2p_mod
        from runtime.sidecars.discovery import _ipfs as _ipfs_mod
        from runtime.sidecars.discovery import _dht as _dht_mod
        from runtime.sidecars.discovery import _commoncrawl as _cc_mod

        _CACHE.update({
            "OnionDiscoverySidecarAdapter": _onion_mod.OnionDiscoverySidecarAdapter,
            "I2PDiscoverySidecarAdapter": _i2p_mod.I2PDiscoverySidecarAdapter,
            "IPFSDiscoverySidecarAdapter": _ipfs_mod.IPFSDiscoverySidecarAdapter,
            "DHTDiscoverySidecarAdapter": _dht_mod.DHTDiscoverySidecarAdapter,
            "CommonCrawlSidecarAdapter": _cc_mod.CommonCrawlSidecarAdapter,
        })

    if name in _CACHE:
        return _CACHE[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
