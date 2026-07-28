"""
_metal — Metal GPU Management Module
=====================================

Provides Metal GPU memory management for M1/M2/M3 Apple Silicon.
Exracted from DeepHermes3Engine to eliminate God Class anti-pattern.

Architecture:
- metal_device.py: GPU device abstraction, memory tracking
- model_loader.py: Model loading/unloading with hermes_cache integration
"""

from brain._metal.metal_device import MetalDevice, get_metal_device
from brain._metal.model_loader import MetalModelLoader

__all__ = [
    "MetalDevice",
    "get_metal_device",
    "MetalModelLoader",
]
