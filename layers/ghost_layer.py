"""
Ghost Layer - DEPRECATED Wrapper
================================

This module is DEPRECATED. Import from `layers.ghost` instead:

    from layers.ghost import GhostLayer, SystemContext, VMThreatLevel

This file exists for backward compatibility only and will be removed in a future version.
"""
import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.ghost_layer is deprecated. Import from layers.ghost instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.ghost import (
    GhostLayer,
    SystemContext,
    VMThreatLevel,
)

__all__ = ['GhostLayer', 'SystemContext', 'VMThreatLevel']
