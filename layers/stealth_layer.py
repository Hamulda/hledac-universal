"""
Stealth Layer - DEPRECATED Wrapper
===============================

This module is DEPRECATED. Import from `layers.stealth` instead:

    from layers.stealth import StealthLayer, BehaviorSimulator, BehaviorPattern, ProfileGenerator, FingerprintProfile

This file exists for backward compatibility only and will be removed in a future version.
"""

import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.stealth_layer is deprecated. Import from layers.stealth instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.stealth import (
    BehaviorPattern,
    BehaviorSimulator,
    EvasionCategory,
    EvasionScript,
    FingerprintProfile,
    MouseMovement,
    ProfileGenerator,
    SimulationConfig,
    StealthLayer,
)

__all__ = [
    "StealthLayer",
    "BehaviorSimulator",
    "BehaviorPattern",
    "ProfileGenerator",
    "FingerprintProfile",
    "EvasionCategory",
    "EvasionScript",
    "SimulationConfig",
    "MouseMovement",
]
