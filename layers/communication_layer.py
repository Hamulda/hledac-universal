"""
Communication Layer - DEPRECATED Wrapper
====================================

This module is DEPRECATED. Import from `layers.communication` instead:

    from layers.communication import CommunicationLayer, ContentCleaner, SimpleHTMLCleaner, OutputFormat, CleaningResult

This file exists for backward compatibility only and will be removed in a future version.
"""
import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.communication_layer is deprecated. Import from layers.communication instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.communication import (
    CommunicationLayer,
    ContentCleaner,
    SimpleHTMLCleaner,
    OutputFormat,
    CleaningResult,
    InMemoryMessageBroker,
)

__all__ = [
    'CommunicationLayer',
    'ContentCleaner',
    'SimpleHTMLCleaner',
    'OutputFormat',
    'CleaningResult',
    'InMemoryMessageBroker',
]
