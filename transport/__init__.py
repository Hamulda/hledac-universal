"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""


def __getattr__(name: str):
    """Lazy imports to break circular dependency cycle.

    All submodule imports are deferred to __getattr__ to avoid circular
    dependency: base.py ↔ __init__.py via transport_router/transport_resolver.
    """
    # Transport ABC and DTOs from base.py
    if name in ('Transport', 'TransportAdapter', 'TransportConfig', 'TransportResult'):
        from .base import Transport, TransportAdapter, TransportConfig, TransportResult

        return {'Transport': Transport, 'TransportAdapter': TransportAdapter,
                'TransportConfig': TransportConfig, 'TransportResult': TransportResult}[name]
    # Gopher transport
    if name in ('GopherTransport', 'get_gopher_transport'):
        from .gopher_transport import GopherTransport, get_gopher_transport

        return {'GopherTransport': GopherTransport, 'get_gopher_transport': get_gopher_transport}[name]
    # HTTP3 lane
    if name == 'fetch_http3_aioquic':
        from .http3_lane import fetch_http3_aioquic

        return fetch_http3_aioquic
    if name == 'http_version_for_curl_cffi':
        from .http3_lane import http_version_for_curl_cffi

        return http_version_for_curl_cffi
    if name == 'record_from_curl_cffi_result':
        from .http3_lane import record_from_curl_cffi_result

        return record_from_curl_cffi_result
    if name == 'record_h3_support':
        from .http3_lane import record_h3_support

        return record_h3_support
    if name == 'http3_lane_enabled':
        from .http3_lane import is_enabled as http3_lane_enabled

        return http3_lane_enabled
    # SILICON-03: Network.framework lane
    if name == 'fetch_nw_connection':
        from .nw_connection_lane import fetch_nw_connection

        return fetch_nw_connection
    if name == 'is_nw_connection_available':
        from .nw_connection_lane import is_nw_connection_available

        return is_nw_connection_available
    # SILICON-05: Network.framework QUIC/HTTP3 lane
    if name == 'fetch_nw_quic':
        from .nw_quic_lane import fetch_nw_quic

        return fetch_nw_quic
    if name == 'is_nw_quic_available':
        from .nw_quic_lane import is_nw_quic_available

        return is_nw_quic_available
    # Unified transport
    if name in ('TransportKind', 'TransportPolicy', 'POLICY_CLEARNET_H2', 'POLICY_STEALTH_CHROME',
                'POLICY_STEALTH_SAFARI', 'POLICY_TOR', 'POLICY_I2P', 'get_transport_client',
                'close_all_transports', 'fetch_via_unified', 'prefetch_dns', 'dns_cache_status'):
        from .unified_transport import (
            TransportKind, TransportPolicy, POLICY_CLEARNET_H2, POLICY_STEALTH_CHROME,
            POLICY_STEALTH_SAFARI, POLICY_TOR, POLICY_I2P, get_transport_client,
            close_all_transports, fetch_via_unified, prefetch_dns, dns_cache_status,
        )

        return {
            'TransportKind': TransportKind, 'TransportPolicy': TransportPolicy,
            'POLICY_CLEARNET_H2': POLICY_CLEARNET_H2, 'POLICY_STEALTH_CHROME': POLICY_STEALTH_CHROME,
            'POLICY_STEALTH_SAFARI': POLICY_STEALTH_SAFARI, 'POLICY_TOR': POLICY_TOR,
            'POLICY_I2P': POLICY_I2P, 'get_transport_client': get_transport_client,
            'close_all_transports': close_all_transports, 'fetch_via_unified': fetch_via_unified,
            'prefetch_dns': prefetch_dns, 'dns_cache_status': dns_cache_status,
        }[name]
    # transport_resolver lazy exports
    if name in ('RouteDecision', 'TransportContext', 'TransportResolver',
                'get_route_decision', 'async_get_route_decision',
                'is_i2p_available', 'async_is_tor_available'):
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
    # HEIST-06: Arti transport (lazy)
    if name in ('ArtiTransport', 'ArtiClient', 'get_arti_transport_singleton',
                'is_arti_available', 'is_arti_enabled'):
        from .arti_transport import (
            ArtiTransport, ArtiClient, get_arti_transport_singleton,
            is_arti_available, is_arti_enabled,
        )

        return {
            'ArtiTransport': ArtiTransport,
            'ArtiClient': ArtiClient,
            'get_arti_transport_singleton': get_arti_transport_singleton,
            'is_arti_available': is_arti_available,
            'is_arti_enabled': is_arti_enabled,
        }[name]
    # R4: Unified HTTP Transport (http_client.py)
    if name in ('HttpTransport', 'HttpResult', 'HttpTransportConfig', 'Profile', 'QoS',
                'get_semaphore_telemetry'):
        from .http_client import (
            HttpTransport, HttpResult, HttpTransportConfig,
            Profile, QoS, get_semaphore_telemetry,
        )

        return {
            'HttpTransport': HttpTransport,
            'HttpResult': HttpResult,
            'HttpTransportConfig': HttpTransportConfig,
            'Profile': Profile,
            'QoS': QoS,
            'get_semaphore_telemetry': get_semaphore_telemetry,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Transport ABC and adapters
    'Transport',
    'TransportAdapter',
    'TransportResolver',
    'TransportContext',
    'RouteDecision',
    'get_route_decision',
    'async_get_route_decision',
    'is_i2p_available',
    'async_is_tor_available',
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
    # SILICON-03: Network.framework user-space TCP lane
    'fetch_nw_connection',
    'is_nw_connection_available',
    # SILICON-05: Network.framework native QUIC/HTTP3 lane
    'fetch_nw_quic',
    'is_nw_quic_available',
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
    # HEIST-06: Arti transport
    'ArtiTransport',
    'ArtiClient',
    'get_arti_transport_singleton',
    'is_arti_available',
    'is_arti_enabled',
    # R4: Unified HTTP Transport
    'HttpTransport',
    'HttpResult',
    'HttpTransportConfig',
    'Profile',
    'QoS',
    'get_semaphore_telemetry',
]
