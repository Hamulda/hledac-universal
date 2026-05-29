"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""

from .base import (
    Transport,
    TransportAdapter,
    TransportConfig,
    TransportResult,
)
from .gopher_transport import GopherTransport, get_gopher_transport
from .inmemory_transport import InMemoryTransport
from .transport_resolver import TransportContext, TransportResolver

__all__ = [
    # Transport ABC and adapters
    'Transport',
    'TransportAdapter',
    # Legacy exports
    'InMemoryTransport',
    'TransportResolver',
    'TransportContext',
    'GopherTransport',
    'get_gopher_transport',
    # DTOs
    'TransportConfig',
    'TransportResult',
]
