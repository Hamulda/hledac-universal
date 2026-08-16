"""
Layer Protocol + LayerStack — DEPRECATED Wrapper
==============================================

.. deprecated::
    This module is deprecated. Use `layers.core.protocol` instead:

        from layers.core import Layer, LayerContext, LayerEvent, LayerStack

    This file exists for backward compatibility only and will be removed in a future version.
"""

# Deprecation warning
import warnings
warnings.warn(
    "layers.layer_protocol is deprecated. Import from layers.core.protocol instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from canonical module
from layers.core.protocol import (
    Layer,
    LayerContext,
    LayerEvent,
    LayerStack,
    LayerMountError,
    LayerUnmountError,
    create_uds_server,
    uds_fetch,
)

__all__ = [
    'Layer',
    'LayerContext',
    'LayerEvent',
    'LayerStack',
    'LayerMountError',
    'LayerUnmountError',
    'create_uds_server',
    'uds_fetch',
]
