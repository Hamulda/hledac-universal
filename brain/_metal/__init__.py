"""
_metal — D-SPINE private Metal GPU layer (ISSUE #16)
====================================================

D-SPINE private subfolder (see brain/_batch/__init__.py for the layout).
Metal GPU memory management for M1/M2/M3 Apple Silicon, extracted from
DeepHermes3Engine.

LIVE STATUS: ``metal_device.py`` (MetalDevice) IS wired into the engine for
telemetry (active/peak memory, tier thresholds). ``model_loader.py``
(MetalModelLoader / ModelSwapManager) is currently ORPHANED — the engine uses
its own inline load/unload and ``brain/model_swap_manager.py`` instead.

Architecture:
- metal_device.py: GPU device abstraction, memory tracking (wired → engine)
- model_loader.py: Model loading/unloading with hermes_cache integration (orphaned)
"""

from hledac.universal.brain._metal.metal_device import MetalDevice, get_metal_device
from hledac.universal.brain._metal.model_loader import MetalModelLoader

__all__ = [
    "MetalDevice",
    "get_metal_device",
    "MetalModelLoader",
]
