"""
Transport layer for federated learning.
Provides autonomous transport selection via TransportResolver.
"""

# Dictionary dispatch tables for lazy imports (reduces __getattr__ complexity)
_IMPORT_DISPATCH: dict[str, tuple[str, tuple[str, ...]] | tuple[str, str]] = {
    # Transport ABC and DTOs from base.py
    "Transport": (".base", "Transport"),
    "TransportAdapter": (".base", "TransportAdapter"),
    "TransportConfig": (".base", "TransportConfig"),
    "TransportResult": (".base", "TransportResult"),
    # Gopher transport
    "GopherTransport": (".gopher_transport", "GopherTransport"),
    "get_gopher_transport": (".gopher_transport", "get_gopher_transport"),
    # HTTP3 lane
    "fetch_http3_aioquic": (".http3_lane", "fetch_http3_aioquic"),
    "http_version_for_curl_cffi": (".http3_lane", "http_version_for_curl_cffi"),
    "record_from_curl_cffi_result": (".http3_lane", "record_from_curl_cffi_result"),
    "record_h3_support": (".http3_lane", "record_h3_support"),
    "http3_lane_enabled": (".http3_lane", "is_enabled"),
    # SILICON-03: Network.framework lane
    "fetch_nw_connection": (".nw_connection_lane", "fetch_nw_connection"),
    "is_nw_connection_available": (".nw_connection_lane", "is_nw_connection_available"),
    # SILICON-05: Network.framework QUIC/HTTP3 lane
    "fetch_nw_quic": (".nw_quic_lane", "fetch_nw_quic"),
    "is_nw_quic_available": (".nw_quic_lane", "is_nw_quic_available"),
    # Unified transport
    "TransportKind": (".unified_transport", "TransportKind"),
    "TransportPolicy": (".unified_transport", "TransportPolicy"),
    "POLICY_CLEARNET_H2": (".unified_transport", "POLICY_CLEARNET_H2"),
    "POLICY_STEALTH_CHROME": (".unified_transport", "POLICY_STEALTH_CHROME"),
    "POLICY_STEALTH_SAFARI": (".unified_transport", "POLICY_STEALTH_SAFARI"),
    "POLICY_TOR": (".unified_transport", "POLICY_TOR"),
    "POLICY_I2P": (".unified_transport", "POLICY_I2P"),
    "get_transport_client": (".unified_transport", "get_transport_client"),
    "close_all_transports": (".unified_transport", "close_all_transports"),
    "fetch_via_unified": (".unified_transport", "fetch_via_unified"),
    "prefetch_dns": (".unified_transport", "prefetch_dns"),
    "dns_cache_status": (".unified_transport", "dns_cache_status"),
    # HEIST-06: Arti transport
    "ArtiTransport": (".arti_transport", "ArtiTransport"),
    "ArtiClient": (".arti_transport", "ArtiClient"),
    "get_arti_transport_singleton": (".arti_transport", "get_arti_transport_singleton"),
    "is_arti_available": (".arti_transport", "is_arti_available"),
    "is_arti_enabled": (".arti_transport", "is_arti_enabled"),
    # R4: Unified HTTP Transport
    "HttpTransport": (".http_client", "HttpTransport"),
    "HttpResult": (".http_client", "HttpResult"),
    # R15: Per-host rate limiting (Rust primary, Python fallback)
    "check_rate_limit": (".rate_limiter", "check_rate_limit"),
    "get_rate_limiter_stats": (".rate_limiter", "get_rate_limiter_stats"),
    "RateLimiter": (".rate_limiter", "RateLimiter"),
    "HttpTransportConfig": (".http_client", "HttpTransportConfig"),
    "Profile": (".http_client", "Profile"),
    "QoS": (".http_client", "QoS"),
    "get_semaphore_telemetry": (".http_client", "get_semaphore_telemetry"),
    # ISSUE #8: profile-based shared httpx client pool.
    # ONLY sanctioned way to obtain an httpx client outside transport/.
    "get_or_create_httpx_client": (".client_pool", "get_or_create_httpx_client"),
    "ClientProfile": (".client_pool", "ClientProfile"),
    "close_all_clients": (".client_pool", "close_all_clients"),
    "close_stealth_client": (".client_pool", "close_stealth_client"),
    "get_client_pool_status": (".client_pool", "get_client_pool_status"),
}

_SUBMODULE_DISPATCH: dict[str, str] = {
    # transport_resolver exports
    "RouteDecision": "transport_resolver",
    "TransportContext": "transport_resolver",
    "TransportResolver": "transport_resolver",
    "get_route_decision": "transport_resolver",
    "async_get_route_decision": "transport_resolver",
    "is_i2p_available": "transport_resolver",
    "async_is_tor_available": "transport_resolver",
    # base.py lazy exports: router types
    "TransportDecision": "base",
    "Lane": "base",
    "route_transport": "base",
    # base.py lazy exports: circuit breaker
    "get_breaker": "base",
    "CircuitBreaker": "base",
    "CircuitDecision": "base",
    # base.py lazy exports: HTTPX
    "should_use_httpx_h2": "base",
    "fetch_via_httpx_h2": "base",
    # base.py lazy exports: curl_cffi
    "should_use_curl_cffi": "base",
    "fetch_via_curl_cffi": "base",
    "fetch_via_tor_curl_cffi": "base",
}


# F-ISSUE-005: Capability registry exports
_CAPABILITY_DISPATCH: dict[str, tuple[str, tuple[str, ...]] | tuple[str, str]] = {
    "TransportCapability": (".capability_registry", "TransportCapability"),
    "get_capability": (".capability_registry", "get_capability"),
    "is_protocol_ready": (".capability_registry", "is_protocol_ready"),
    "get_all_capabilities": (".capability_registry", "get_all_capabilities"),
    "get_capability_summary": (".capability_registry", "get_capability_summary"),
    "clear_capability_cache": (".capability_registry", "clear_capability_cache"),
    "get_skip_reason": (".capability_registry", "get_skip_reason"),
}

# Module-level import cache
_IMPORT_CACHE: dict[str, object] = {}


def __getattr__(name: str):
    """Lazy imports to break circular dependency cycle.

    All submodule imports are deferred to __getattr__ to avoid circular
    dependency: base.py ↔ __init__.py via transport_router/transport_resolver.

    F-ISSUE-005: Also handles capability registry exports.
    """
    if name in _IMPORT_CACHE:
        return _IMPORT_CACHE[name]
    if name in _IMPORT_DISPATCH:
        module_path, attr_name = _IMPORT_DISPATCH[name]
        import importlib

        module = importlib.import_module(f"{__name__}{module_path}")
        obj = getattr(module, attr_name)
        _IMPORT_CACHE[name] = obj
        return obj
    # F-ISSUE-005: Capability registry lazy imports
    if name in _CAPABILITY_DISPATCH:
        module_path, attr_name = _CAPABILITY_DISPATCH[name]
        import importlib

        module = importlib.import_module(f"{__name__}{module_path}")
        obj = getattr(module, attr_name)
        _IMPORT_CACHE[name] = obj
        return obj
    if name in _SUBMODULE_DISPATCH:
        submodule = _SUBMODULE_DISPATCH[name]
        import importlib

        module = importlib.import_module(f"{__name__}.{submodule}")
        obj = getattr(module, name)
        _IMPORT_CACHE[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Transport ABC and adapters
    "Transport",
    "TransportAdapter",
    "TransportResolver",
    "TransportContext",
    "RouteDecision",
    "get_route_decision",
    "async_get_route_decision",
    "is_i2p_available",
    "async_is_tor_available",
    "GopherTransport",
    "get_gopher_transport",
    # F-ISSUE-005: Transport Capability Registry
    "TransportCapability",
    "get_capability",
    "is_protocol_ready",
    "get_all_capabilities",
    "get_capability_summary",
    "clear_capability_cache",
    "get_skip_reason",
    # DTOs
    "TransportConfig",
    "TransportResult",
    # P1-2 HTTP/3 lane (see transport/http3_lane.py for invariants)
    "fetch_http3_aioquic",
    "http_version_for_curl_cffi",
    "http3_lane_enabled",
    "record_from_curl_cffi_result",
    "record_h3_support",
    # SILICON-03: Network.framework user-space TCP lane
    "fetch_nw_connection",
    "is_nw_connection_available",
    # SILICON-05: Network.framework native QUIC/HTTP3 lane
    "fetch_nw_quic",
    "is_nw_quic_available",
    # Unified Transport (backwards compatible re-exports)
    "TransportKind",
    "TransportPolicy",
    "POLICY_CLEARNET_H2",
    "POLICY_STEALTH_CHROME",
    "POLICY_STEALTH_SAFARI",
    "POLICY_TOR",
    "POLICY_I2P",
    "get_transport_client",
    "close_all_transports",
    "fetch_via_unified",
    # DNS prefetch
    "prefetch_dns",
    "dns_cache_status",
    # Router types (lazy, from base.py __getattr__)
    "TransportDecision",
    "Lane",
    "route_transport",
    # Circuit breaker (lazy, from base.py __getattr__)
    "get_breaker",
    "CircuitBreaker",
    "CircuitDecision",
    # HTTPX (lazy, from base.py __getattr__)
    "should_use_httpx_h2",
    "fetch_via_httpx_h2",
    # curl_cffi (lazy, from base.py __getattr__)
    "should_use_curl_cffi",
    "fetch_via_curl_cffi",
    "fetch_via_tor_curl_cffi",
    # HEIST-06: Arti transport
    "ArtiTransport",
    "ArtiClient",
    "get_arti_transport_singleton",
    "is_arti_available",
    "is_arti_enabled",
    # R4: Unified HTTP Transport
    "HttpTransport",
    "HttpResult",
    "HttpTransportConfig",
    "Profile",
    "QoS",
    "get_semaphore_telemetry",
]
