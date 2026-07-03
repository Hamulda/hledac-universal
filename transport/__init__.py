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
from .http3_lane import (  # type: ignore[import-not-found]  # P1-2: bounded HTTP/3 lane
    fetch_http3_aioquic,
    http_version_for_curl_cffi,
    record_from_curl_cffi_result,
    record_h3_support,
)
from .http3_lane import (
    is_enabled as http3_lane_enabled,
)
from .transport_resolver import TransportContext, TransportResolver

__all__ = [
    # Transport ABC and adapters
    'Transport',
    'TransportAdapter',
    # InMemoryTransport moved to tests/transports/inmemory_transport.py (TST001 guard)
    'TransportResolver',
    'TransportContext',
    'GopherTransport',
    'get_gopher_transport',
    # DTOs
    'TransportConfig',
    'TransportResult',
    # P1-2 HTTP/3 lane (see transport/http3_lane.py for invariants)
    'fetch_http3_aioquic',
    'http_version_for_curl_cffi',
    'http3_lane_enabled',
    'record_from_curl_cffi_result',
    'record_h3_support',
]
