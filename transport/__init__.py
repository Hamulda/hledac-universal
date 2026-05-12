"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""

from .base import Transport
from .inmemory_transport import InMemoryTransport
from .transport_resolver import TransportResolver, TransportContext
from .gopher_transport import GopherTransport, get_gopher_transport

__all__ = [
    'Transport',
    'InMemoryTransport',
    'TransportResolver',
    'TransportContext',
    'GopherTransport',
    'get_gopher_transport',
]
