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
from .http3_lane import (  # type: ignore[import-not-found]
    fetch_http3_aioquic,
    http_version_for_curl_cffi,
    record_from_curl_cffi_result,
    record_h3_support,
)
from .http3_lane import (
    is_enabled as http3_lane_enabled,
)
from .unified_transport import (  # noqa: E402
    TransportKind,
    TransportPolicy,
    POLICY_CLEARNET_H2,
    POLICY_STEALTH_CHROME,
    POLICY_STEALTH_SAFARI,
    POLICY_TOR,
    POLICY_I2P,
    get_transport_client,
    close_all_transports,
    fetch_via_unified,
    prefetch_dns,
    dns_cache_status,
)


def __getattr__(name: str):
    """Lazy imports to break circular dependency cycle and expose base.py lazy exports.

    Chain: base.py → __init__.py → transport_resolver.py → tor_transport.py → base.py
    (the above chain is already broken; tor_transport.py imports .base directly)

    Also delegates to base.py __getattr__ for TransportDecision, Lane, circuit breaker,
    HTTPX, and curl_cffi exports that are only available via lazy loading.
    """
    # transport_resolver lazy exports
    if name in ('RouteDecision', 'TransportContext', 'TransportResolver',
                'get_route_decision', 'is_i2p_available'):
        from . import transport_resolver
        return getattr(transport_resolver, name)
    # base.py lazy exports: router types
    if name in ('TransportDecision', 'Lane', 'route_transport'):
        from . import base
        return getattr(base, name)
    # base.py lazy exports: circuit breaker
    if name in ('get_breaker', 'CircuitBreaker', 'CircuitDecision'):
        from . import base
        return getattr(base, name)
    # base.py lazy exports: HTTPX
    if name in ('should_use_httpx_h2', 'fetch_via_httpx_h2'):
        from . import base
        return getattr(base, name)
    # base.py lazy exports: curl_cffi (tor variant also via base)
    if name in ('should_use_curl_cffi', 'fetch_via_curl_cffi', 'fetch_via_tor_curl_cffi'):
        from . import base
        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Transport ABC and adapters
    'Transport',
    'TransportAdapter',
    'TransportResolver',
    'TransportContext',
    'RouteDecision',
    'get_route_decision',
    'is_i2p_available',
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
    # Unified Transport (backwards compatible re-exports)
    'TransportKind',
    'TransportPolicy',
    'POLICY_CLEARNET_H2',
    'POLICY_STEALTH_CHROME',
    'POLICY_STEALTH_SAFARI',
    'POLICY_TOR',
    'POLICY_I2P',
    'get_transport_client',
    'close_all_transports',
    'fetch_via_unified',
    # DNS prefetch
    'prefetch_dns',
    'dns_cache_status',
    # Router types (lazy, from base.py __getattr__)
    'TransportDecision',
    'Lane',
    'route_transport',
    # Circuit breaker (lazy, from base.py __getattr__)
    'get_breaker',
    'CircuitBreaker',
    'CircuitDecision',
    # HTTPX (lazy, from base.py __getattr__)
    'should_use_httpx_h2',
    'fetch_via_httpx_h2',
    # curl_cffi (lazy, from base.py __getattr__)
    'should_use_curl_cffi',
    'fetch_via_curl_cffi',
    'fetch_via_tor_curl_cffi',
]
