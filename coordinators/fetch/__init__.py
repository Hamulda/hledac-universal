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

    from .services import (
        AIMDWindowService,
        AIMDConfig,
        PrivacyAllocatorService,
        PrivacyConfig,
        PrivacyLevel,
        SpeculativePrefetchService,
        SpeculativeConfig,
        EntropyFeedbackService,
        EntropyConfig,
        MicroSprintService,
        MicroSprintConfig,
        EvidenceSinkService,
        EvidenceConfig,
    )

Facade Coordinator:
    from hledac.universal.coordinators.fetch import FetchCoordinatorFacade

Usage:
    facade = FetchCoordinatorFacade()
    await facade.initialize()
    result = await facade.fetch(url, options)

Service Architecture (SRP/ISP Compliant):
    - coordinators/fetch/facade.py ≤ 200 LOC (pure delegating facade)
    - coordinators/fetch/services/ contains all business logic:
        - aimd.py: AIMD window controller
        - privacy.py: Privacy budget management
        - speculative.py: Link prediction
        - entropy.py: Anomaly detection
        - micro_sprint.py: Sprint scheduling
        - evidence.py: Evidence collection
"""

from __future__ import annotations

from .facade import FetchCoordinatorConfig, FetchCoordinatorFacade
from .services import (
    # AIMD
    AIMDConfig,
    AIMDWindowService,
    # Base services
    CircuitBreakerService,
    DNSCacheService,
    # Entropy
    EntropyConfig,
    EntropyFeedbackService,
    # Evidence
    EvidenceConfig,
    EvidenceSinkService,
    FetchOptions,
    FetchResult,
    FetchServiceConfig,
    FetchServiceRegistry,
    # Micro Sprint
    MicroSprintConfig,
    MicroSprintService,
    PrivacyAllocatorService,
    # Privacy
    PrivacyConfig,
    PrivacyLevel,
    RateLimiterService,
    RetryPolicyService,
    # Speculative
    SpeculativeConfig,
    SpeculativePrefetchService,
)

__all__ = [
    # Services
    "FetchServiceRegistry",
    "FetchServiceConfig",
    "DNSCacheService",
    "RateLimiterService",
    "CircuitBreakerService",
    "RetryPolicyService",
    "FetchOptions",
    "FetchResult",
    # AIMD
    "AIMDConfig",
    "AIMDWindowService",
    # Privacy
    "PrivacyLevel",
    "PrivacyConfig",
    "PrivacyAllocatorService",
    # Speculative
    "SpeculativeConfig",
    "SpeculativePrefetchService",
    # Entropy
    "EntropyConfig",
    "EntropyFeedbackService",
    # Micro Sprint
    "MicroSprintConfig",
    "MicroSprintService",
    # Evidence
    "EvidenceConfig",
    "EvidenceSinkService",
    # Facade
    "FetchCoordinatorFacade",
    "FetchCoordinatorConfig",
]
