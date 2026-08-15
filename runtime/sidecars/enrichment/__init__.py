"""
runtime/sidecars/enrichment/__init__.py — F-ISSUE-005: Enrichment Sidecars
===========================================================================

PEP 562 lazy loading — adapters loaded only when first accessed.
Adapters: BGP, BannerGrab, TIFeed.
"""
from __future__ import annotations
from core import aclose

__all__ = [
    "BGPEnrichmentSidecarAdapter",
    "BannerGrabSidecarAdapter",
    "TIFeedSidecarAdapter",
]

# Lazy-load cache: name -> class
_CACHE: dict[str, object] = {}
_LOADED = False


def __getattr__(name: str):
    global _LOADED
    if not _LOADED:
        _LOADED = True
        from hledac.universal.runtime.sidecars.enrichment import _bgp as _bgp_mod
        from hledac.universal.runtime.sidecars.enrichment import _banner as _banner_mod
        from hledac.universal.runtime.sidecars.enrichment import _ti_feed as _ti_mod

        _CACHE.update({
            "BGPEnrichmentSidecarAdapter": _bgp_mod.BGPEnrichmentSidecarAdapter,
            "BannerGrabSidecarAdapter": _banner_mod.BannerGrabSidecarAdapter,
            "TIFeedSidecarAdapter": _ti_mod.TIFeedSidecarAdapter,
        })

    if name in _CACHE:
        return _CACHE[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
