"""
Fetch Package — Service Layer + Coordinator Facade
================================================

Services:
    from .services import (
        FetchServiceRegistry,
        FetchServiceConfig,
        DNSCacheService,
        RateLimiterService,
        CircuitBreakerService,
        RetryPolicyService,
        FetchOptions,
        FetchResult,
    )

Facade Coordinator:
    from hledac.universal.coordinators.fetch import FetchCoordinatorFacade

Usage:
    facade = FetchCoordinatorFacade()
    await facade.initialize()
    result = await facade.fetch(url, options)
"""
from __future__ import annotations

from .facade import FetchCoordinatorConfig, FetchCoordinatorFacade
from .services import (
    CircuitBreakerService,
    DNSCacheService,
    FetchOptions,
    FetchResult,
    FetchServiceConfig,
    FetchServiceRegistry,
    RateLimiterService,
    RetryPolicyService,
    )
from _core import aclose

__all__ = [
    # Services
    'FetchServiceRegistry',
    'FetchServiceConfig',
    'DNSCacheService',
    'RateLimiterService',
    'CircuitBreakerService',
    'RetryPolicyService',
    'FetchOptions',
    'FetchResult',
    # Facade
    'FetchCoordinatorFacade',
    'FetchCoordinatorConfig',
]
