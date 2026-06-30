"""
runtime/protocols/transport_protocol.py — F270: Transport Interface
===============================================================

Protocol for Tor/I2P/Nym/DHT transport adapters.
Extracted from SprintScheduler's TRANSPORT group (~5 attributes).

GHOST_INVARIANTS:
- Fail-safe: all transports return None on error
- Bounded: DHT node count limited
"""


from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TransportProtocol(Protocol):
    """
    Anonymous transport protocol.

    Implementations:
        - TorTransportAdapter
        - I2PTransportAdapter
        - NymTransportAdapter
        - DHTNodeAdapter

    Key methods:
        - fetch_via_tor: Tor-anonymized fetch
        - fetch_via_i2p: I2P-anonymized fetch
    """

    async def fetch_via_tor(self, url: str) -> bytes | None:
        """Fetch URL via Tor transport."""
        ...

    async def fetch_via_i2p(self, url: str) -> bytes | None:
        """Fetch URL via I2P transport."""
        ...

    async def dht_get(self, key: str) -> Any:
        """DHT key lookup."""
        ...

    async def dht_put(self, key: str, value: Any) -> None:
        """DHT key store."""
        ...
