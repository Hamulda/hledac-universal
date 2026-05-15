"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""

from .inmemory_transport import InMemoryTransport
from .transport_resolver import TransportResolver, TransportContext
from .gopher_transport import GopherTransport, get_gopher_transport
from .base import (
    Transport,
    TransportAdapter,
    TransportConfig,
    TransportResult,
)

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
