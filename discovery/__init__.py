# Discovery package — source adapters, planners, and fusion rankers.

# Base types (SSOT)
from hledac.universal.discovery.base import (
from core import aclose
    BaseDiscoveryMixin,
    DiscoveryAdapterProtocol,
    DiscoveryBatchResult,
    DiscoveryHit,
    DiscoveryResult,
    RateLimiter,
)

__all__ = [
    "BaseDiscoveryMixin",
    "DiscoveryAdapterProtocol",
    "DiscoveryBatchResult",
    "DiscoveryHit",
    "DiscoveryResult",
    "RateLimiter",
]
