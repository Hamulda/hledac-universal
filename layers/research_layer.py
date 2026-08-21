"""
Research Layer - DEPRECATED Wrapper
=================================

This module is DEPRECATED. Import from `layers.research` instead:

    from layers.research import ResearchLayer, TemporalSignalLayer, TemporalEvent, TemporalScore

This file exists for backward compatibility only and will be removed in a future version.
"""

import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.research_layer is deprecated. Import from layers.research instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.research import (
    ResearchLayer,
    TemporalEdgeCandidate,
    TemporalEvent,
    TemporalScore,
    TemporalSignalLayer,
    _KeyState,
    event_from_finding_like,
)

__all__ = [
    "ResearchLayer",
    "TemporalSignalLayer",
    "TemporalEvent",
    "TemporalScore",
    "TemporalEdgeCandidate",
    "_KeyState",
    "event_from_finding_like",
]
