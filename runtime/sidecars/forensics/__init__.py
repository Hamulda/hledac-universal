"""
runtime/sidecars/forensics/__init__.py — F-ISSUE-005: Forensics Sidecars
=========================================================================

PEP 562 lazy loading — adapters loaded only when first accessed.
Adapters: DigitalGhost, Steganography.
"""
from __future__ import annotations

__all__ = [
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
        from hledac.universal.runtime.sidecars.forensics import _digital_ghost as _dg_mod
        from hledac.universal.runtime.sidecars.forensics import _steganography as _steg_mod

        _CACHE.update({
            "DigitalGhostSidecarAdapter": _dg_mod.DigitalGhostSidecarAdapter,
            "SteganographySidecarAdapter": _steg_mod.SteganographySidecarAdapter,
        })

    if name in _CACHE:
        return _CACHE[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
