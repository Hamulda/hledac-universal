"""
Transport Layer — Canonical Protocol Definitions
================================================

Sprint F214: Transport Protocol Separation

PROBLEM:
  public_fetcher.py imported directly from:
    - transport.circuit_breaker (CircuitBreaker, CircuitDecision, get_breaker)
    - transport.httpx_transport (should_use_httpx_h2, fetch_via_httpx_h2)
    - transport.curl_cffi_transport (should_use_curl_cffi)
    - transport.curl_cffi_fetch (fetch_via_curl_cffi)
    - transport.transport_router (route_transport, TransportDecision)

  This created tight coupling: fetcher knew the internal structure of multiple
  transport implementations. Adding a new transport required modifying public_fetcher.

SOLUTION:
  - Define TransportConfig (input) and TransportResult (output) as clean DTOs
  - Keep TransportDecision/Lane from transport_router (already a good abstraction)
  - public_fetcher imports TransportDecision from transport.base (single import point)
  - Circuit breaker functions also re-exported from transport.base

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                      public_fetcher.py                         │
  │         imports ONLY from transport.base:                      │
  │           - TransportDecision, Lane (from transport_router)    │
  │           - get_breaker, CircuitBreaker, CircuitDecision       │
  │           - should_use_httpx_h2, fetch_via_httpx_h2             │
  │           - should_use_curl_cffi, fetch_via_curl_cffi           │
  │           - route_transport                                    │
  └─────────────────────────────────────────────────────────────────┘

INVARIANTS:
  [TP-1] public_fetcher imports ONLY from transport.base and transport.provider_utils
  [TP-2] TransportConfig/TransportResult are frozen dataclasses (immutable)
  [TP-3] Circuit breaker state flows through config, not direct fetcher state
  [TP-4] All transport functions are fail-soft — exceptions handled gracefully
  [TP-5] CancelledError is always re-raised by transport functions
"""

# ---------------------------------------------------------------------------
# Fetch Adapter — TransportAdapter ABC for HTTP fetch operations
# ---------------------------------------------------------------------------

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .circuit_breaker import CircuitBreaker, CircuitDecision, get_breaker
    from .httpx_transport import fetch_via_httpx_h2, should_use_httpx_h2
    from .curl_cffi_transport import should_use_curl_cffi
    from .curl_cffi_fetch import fetch_via_curl_cffi
    from .transport_router import Lane, route_transport, TransportDecision


@dataclass(frozen=True)
class TransportConfig:
    """
    Immutable configuration for a fetch operation.

    Contains all parameters needed by any transport provider.
    Fail-soft: missing fields use sensible defaults.
    """
    url: str
    timeout_s: float = 35.0
    max_bytes: int = 2_000_000
    use_stealth: bool = False
    use_js: bool = False
    cache_safe: bool = False
    # Circuit breaker domain (extracted from URL by public_fetcher)
    circuit_breaker_domain: str = ""
    # Retry state
    retry_after_status: Optional[int] = None
    suggested_concurrency: str = "medium"


@dataclass(frozen=True)
class TransportResult:
    """
    Immutable result from a transport fetch operation.

    Contains everything public_fetcher needs to construct FetchResult.
    All fields have defaults so existing callers are unaffected.
    """
    # Core response
    url: str
    final_url: str = ""
    status_code: int = 0
    content_type: str = ""
    text: Optional[str] = None
    fetched_bytes: int = 0
    declared_length: int = -1
    elapsed_ms: float = 0.0

    # Error handling
    error: Optional[str] = None
    failure_stage: Optional[str] = None
    network_error_kind: Optional[str] = None

    # Transport telemetry
    selected_transport: str = ""
    http_version: Optional[str] = None

    # Decode info
    decode_replaced: bool = False
    decode_replacement_count: int = 0

    # Redirect info
    redirected: bool = False
    redirect_target: Optional[str] = None

    # XML recovery (feed ingress)
    xml_recovered: bool = False
    xml_source_hint: bool = False

    # Body read error
    body_read_error: bool = False

    # Fallback tracking
    transport_fallback_reason: Optional[str] = None


class TransportAdapter(ABC):
    """
    Abstract base class for HTTP fetch transports.

    All fetch adapters implement this interface so callers can swap
    transports without changing call sites. Adding a new transport
    means implementing this interface, not modifying multiple files.

    Contract:
      - fetch() is fail-soft: exceptions are caught and returned as error results
      - CancelledError is always re-raised
      - All I/O happens inside fetch()
    """

    @abstractmethod
    async def fetch(self, config: TransportConfig) -> TransportResult:
        """
        Execute a fetch operation with the given config.

        Args:
            config: TransportConfig with URL, timeout, max_bytes, etc.

        Returns:
            TransportResult with response data or error details.

        Raises:
            asyncio.CancelledError: on cancellation (not caught)
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the adapter name for telemetry (e.g., 'curl_cffi', 'httpx_h2')."""
        ...

    @property
    def supports_stealth(self) -> bool:
        """Return True if this adapter supports stealth/JA3 fingerprinting."""
        return False

    @property
    def supports_http2(self) -> bool:
        """Return True if this adapter supports HTTP/2."""
        return False

    @property
    def supports_tor(self) -> bool:
        """Return True if this adapter routes through Tor."""
        return False

    @property
    def supports_i2p(self) -> bool:
        """Return True if this adapter routes through I2P."""
        return False


# ---------------------------------------------------------------------------
# Transport ABC — Abstract base for node-transport overlays
# Implemented by: InMemoryTransport, TorTransport, NymTransport, I2PTransport
# ---------------------------------------------------------------------------


class Transport(ABC):
    """
    Abstract base class for node-transport overlays.

    All node transports (InMemory, Tor, Nym, I2P) inherit from this ABC.
    Provides the async lifecycle methods and message handler registration
    that node transports need.

    Contract:
      - start(), stop(), wait_ready() are async
      - register_handler() is sync (or raise NotImplementedError)
      - is_running() is a concrete method using self.available
    """

    available: bool = True

    @abstractmethod
    async def start(self) -> bool:
        """Start the transport and return True if successful."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport gracefully."""
        ...

    @abstractmethod
    async def wait_ready(self) -> None:
        """Wait until the transport is ready to handle requests."""
        ...

    @abstractmethod
    def register_handler(self, msg_type: str, handler) -> None:
        """
        Register a message handler for the given message type.

        Args:
            msg_type: Message type identifier
            handler: Callable to handle the message

        Raises:
            NotImplementedError: if the transport does not support handlers
        """
        ...

    async def is_running(self) -> bool:
        """Check if the transport is operational."""
        return self.available


# ---------------------------------------------------------------------------
# Re-exports from transport_router (TransportDecision, Lane)
# These provide the lane-selection abstraction without requiring
# public_fetcher to import from transport_router directly.
# Kept as lazy __getattr__ to avoid import chain failures when transport.base
# is imported as top-level (no hledac.universal parent in sys.modules).
# ---------------------------------------------------------------------------

__all__ = [
    # Transport ABC (node-transport overlay base)
    'Transport',
    # DTOs
    'TransportConfig',
    'TransportResult',
    # Adapter interface
    'TransportAdapter',
    # Router types
    'TransportDecision',
    'Lane',
    # Circuit breaker re-exports (backward compatibility)
    'get_breaker',
    'CircuitBreaker',
    'CircuitDecision',
    # HTTPX transport functions
    'should_use_httpx_h2',
    'fetch_via_httpx_h2',
    # curl_cffi transport functions
    'should_use_curl_cffi',
    'fetch_via_curl_cffi',
    # Router
    'route_transport',
]


def __getattr__(name: str):
    if name in ('TransportDecision', 'Lane', 'route_transport'):
        from . import transport_router
        return getattr(transport_router, name)
    if name in ('get_breaker', 'CircuitBreaker', 'CircuitDecision'):
        from . import circuit_breaker
        return getattr(circuit_breaker, name)
    if name in ('should_use_httpx_h2', 'fetch_via_httpx_h2'):
        from . import httpx_transport
        return getattr(httpx_transport, name)
    if name in ('should_use_curl_cffi', 'fetch_via_curl_cffi'):
        from . import curl_cffi_transport
        if name == 'should_use_curl_cffi':
            return curl_cffi_transport.should_use_curl_cffi
        from . import curl_cffi_fetch
        return curl_cffi_fetch.fetch_via_curl_cffi
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")