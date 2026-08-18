"""
Fetch Services Package — SRP-Compliant Service Layer
=====================================================

Services are organized by Single Responsibility Principle:

Base Services (from services.py):
    - DNSCacheService: DNS resolution with single-flight
    - RateLimiterService: Per-domain rate limiting
    - CircuitBreakerService: Circuit breaker state management
    - RetryPolicyService: Retry budget tracking
    - FetchServiceRegistry: Service container

Extended Services:
    - AIMDWindowService: AIMD window controller (from aimd.py)
    - PrivacyAllocatorService: Privacy budget management (from privacy.py)
    - SpeculativePrefetchService: Link prediction (from speculative.py)
    - EntropyFeedbackService: Anomaly detection (from entropy.py)
    - MicroSprintService: Sprint scheduling (from micro_sprint.py)
    - EvidenceSinkService: Evidence collection (from evidence.py)
    - TextNormalizerService: Unicode NFC normalization (from text_normalizer.py)

Usage:
    from hledac.universal.coordinators.fetch.services import (
        AIMDWindowService,
        PrivacyAllocatorService,
        SpeculativePrefetchService,
        EntropyFeedbackService,
        MicroSprintService,
        EvidenceSinkService,
        TextNormalizerService,
        FetchServiceRegistry,
    )

M1 8GB: All services use __slots__ and lazy initialization.
"""
from __future__ import annotations

# Base services (from parent services.py)
from ..services import (
    CircuitBreakerService,
    DNSCacheService,
    FetchOptions,
    FetchResult,
    FetchServiceConfig,
    FetchServiceRegistry,
    RateLimiterService,
    RetryPolicyService,
)

# AIMD Window Service
from .aimd import (
    AIMDConfig,
    AIMDWindowService,
    PyAIMDController,
)

# Privacy Allocator Service
from .privacy import (
    PrivacyAllocatorService,
    PrivacyBudgetEntry,
    PrivacyConfig,
    PrivacyLevel,
)

# Speculative Prefetch Service
from .speculative import (
    SpeculativeConfig,
    SpeculativePrefetchService,
    StreamingLinkExtractor,
    URLPriorityEntry,
)

# Entropy Feedback Service
from .entropy import (
    BlockingEntropyCalculator,
    EntropyConfig,
    EntropyFeedbackService,
    EntropyResult,
    StreamingEntropyCalculator,
)

# Micro Sprint Service
from .micro_sprint import (
    MicroSprintConfig,
    MicroSprintService,
    SprintResult,
    SprintTask,
)

# Evidence Sink Service
from .evidence import (
    EvidenceConfig,
    EvidenceRecord,
    EvidenceSinkService,
    InMemoryEvidenceStorage,
)

# Text Normalizer Service (C10: Rust nfc_normalize integration)
from .text_normalizer import (
    TextNormalizerService,
    get_text_normalizer,
)

__all__ = [
    # Base services
    'DNSCacheService',
    'RateLimiterService',
    'CircuitBreakerService',
    'RetryPolicyService',
    'FetchServiceRegistry',
    'FetchServiceConfig',
    'FetchOptions',
    'FetchResult',
    # AIMD
    'AIMDConfig',
    'AIMDWindowService',
    'PyAIMDController',
    # Privacy
    'PrivacyLevel',
    'PrivacyConfig',
    'PrivacyBudgetEntry',
    'PrivacyAllocatorService',
    # Speculative Prefetch
    'SpeculativeConfig',
    'URLPriorityEntry',
    'StreamingLinkExtractor',
    'SpeculativePrefetchService',
    # Entropy
    'EntropyConfig',
    'EntropyResult',
    'StreamingEntropyCalculator',
    'EntropyFeedbackService',
    'BlockingEntropyCalculator',
    # Micro Sprint
    'MicroSprintConfig',
    'SprintTask',
    'SprintResult',
    'MicroSprintService',
    # Evidence
    'EvidenceConfig',
    'EvidenceRecord',
    'EvidenceSinkService',
    'InMemoryEvidenceStorage',
    # Text Normalizer (C10: Rust nfc_normalize)
    'TextNormalizerService',
    'get_text_normalizer',
]
