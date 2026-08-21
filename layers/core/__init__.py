"""
Layers Core Module - Canonical Layer Architecture
================================================

Provides:
- Protocol definitions for all layers
- Abstract base class with common functionality
- Layer registry for centralized management
- M1 8GB optimized execution

Architecture:
    Protocol → BaseLayer → LayerRegistry → Concrete Layers

Usage:
    from layers.core import Layer, BaseLayer, LayerRegistry

    class MyLayer(BaseLayer):
        async def process(self, data: Any) -> Any:
            return data

    registry = LayerRegistry()
    registry.register('my', MyLayer())
"""

from __future__ import annotations

from layers.core.base import BaseLayer

# Core exports
from layers.core.protocol import (
    Layer,
    LayerContext,
    LayerEvent,
    LayerMountError,
    LayerStack,
    LayerUnmountError,
    create_uds_server,
    uds_fetch,
)
from layers.core.registry import LayerRegistry

__all__ = [
    # Protocol
    "Layer",
    "LayerContext",
    "LayerEvent",
    "LayerStack",
    "LayerMountError",
    "LayerUnmountError",
    "create_uds_server",
    "uds_fetch",
    # Base
    "BaseLayer",
    # Registry
    "LayerRegistry",
]
