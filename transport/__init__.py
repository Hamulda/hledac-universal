"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""

from .inmemory_transport import InMemoryTransport
from .transport_resolver import TransportResolver, TransportContext
from .gopher_transport import GopherTransport, get_gopher_transport
from .adapters import (
    TransportAdapter,
    TransportRouterAdapter,
    CurlCffiAdapter,
    Http2Adapter,
    TorAdapter,
    I2PAdapter,
    AioHttpAdapter,
)
from .base import TransportConfig, TransportResult

__all__ = [
    # Legacy exports
    'InMemoryTransport',
    'TransportResolver',
    'TransportContext',
    'GopherTransport',
    'get_gopher_transport',
    # F214: TransportSeam — adapters and DTOs
    'TransportAdapter',
    'TransportRouterAdapter',
    'CurlCffiAdapter',
    'Http2Adapter',
    'TorAdapter',
    'I2PAdapter',
    'AioHttpAdapter',
    'TransportConfig',
    'TransportResult',
]
