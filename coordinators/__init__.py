"""
Universal Coordinators
=======================

Consolidated coordinators for Hledac Universal Orchestrator v4.0.

Domain Organization (via CoordinatorCatalog):
    from hledac.universal.coordinators import catalog
    catalog.domains                       # List all domains
    catalog.get('core')                   # Get domain coordinator mappings
    catalog.load('UniversalMemoryCoordinator')  # Lazy load

Domain Groups:
    - core: Research, Execution, Security, Monitoring, Memory, Validation
    - advanced: AdvancedResearch, Swarm, MetaReasoning, PrivacyEnhanced
    - optimization: Performance, Benchmark, Resource, ResearchOptimizer
    - infrastructure: Base, Registry, Mixins
    - specialized: Fetch, Graph, Archive, Multimodal, Render, AgentCoordination

Legacy coordinators moved to legacy/coordinators/:
- quantum_coordinator (moved 2025-02-14)
- nas_coordinator (moved 2025-02-14)
- federated_learning_coordinator (moved 2025-02-14)
- memory_coordinator (old version, moved 2025-02-14)

See LEGACY_MIGRATION.md for details.
"""

# Base classes and types
# Privacy enhanced research
from hledac.universal.project_types import (
    PrivacyLevel,
)

# Coordinator catalog for domain-grouped lazy access
from ._catalog import catalog

# Benchmark coordinator — DEPRECATED 2026-07-05, archived to archive/coordinators_deprecated_2026_07_05/
# Direct imports: use benchmarks/ scripts (benchmarks/live_measurement_kpi.py, etc.)
# Multi-agent coordination
from .agent_coordination_engine import (
    AgentCapability,
    AgentCoordinationEngine,
    AgentPerformance,
    AgentType,
    CoordinationStrategy,
    TaskPriority,
    TaskRequest,
    TaskResult,
    coordinated_search,
)
from .base import (
    CoordinatorCapabilities,
    DecisionResponse,
    MemoryPressureLevel,
    OperationResult,
    OperationType,
    UniversalCoordinator,
)

# Registry
from .execution_coordinator import UniversalExecutionCoordinator
from .memory_coordinator import (
    MemoryAllocation,
    MemoryStatistics,
    MemoryZone,
    UniversalMemoryCoordinator,
)
from .meta_reasoning_coordinator import (
    ReasoningChain,
    ReasoningStep,
    ReasoningStrategy,
    SprintClock,  # BLITZ-04
    ThoughtNode,
    UniversalMetaReasoningCoordinator,
)
from .monitoring_coordinator import UniversalMonitoringCoordinator
from .opsec_coordinator import OpsECCoordinator

# Performance optimization
from .performance_coordinator import (
    AgentMetrics,
    AgentPerformanceOptimizer,
    AgentPool,
    AsyncExecutionOptimizer,
    IntelligentLoadBalancer,
    LoadBalancingConfig,
    OptimizationReport,
)
from .privacy_enhanced_research import (
    AnonymizedRequest,
    AuditRecord,
    DataRetention,
    PrivacyConfig,
    PrivacyEnhancedResearch,
    SanitizedResult,
    private_research,
)

# Research coordinator exports (ACTIVE)
from .research_coordinator import (
    ExcavationConfig,
    ExcavationStrategy,
    HierarchicalPlan,
    MetaPattern,
    ResearchDepth,
    ResearchPaper,
    ResearchTheory,
    ResearchThread,
    UniversalResearchCoordinator,
)

# Core coordinators
# Research optimizer
from .research_optimizer import (
    CachePolicy,
    OptimizationConfig,
    OptimizationStrategy,
    OptimizedResult,
    QueryMetrics,
    ResearchOptimizer,
    create_optimized_pipeline,
    optimized_research,
)

# DEPRECATED [ARCH-001]: resource_allocator imports removed
# Use _core.topology, _core.memory_pressure, and _core.resource_governor instead
from .security_coordinator import (
    SecurityContext,
    SecurityCoordinator,
    SecurityLevel,
    SecurityResult,
    UniversalSecurityCoordinator,
)
from .tot_checkpointer import (  # UNIFIED-005
    TransactionalToTCheckpointer,
)

# swarm_coordinator: deprecated - use lazy import via __getattr__
# Validation coordinator
from .validation_coordinator import (
    CleaningResult,
    OutputFormat,
    UniversalValidationCoordinator,
    ValidationResult,
    ValidationSeverity,
)

# NOTE: Quantum, NAS, and FederatedLearning coordinators were moved to
# legacy/coordinators/ in v4.0. The deprecation warning for these is
# removed since they were never imported here (dead code elimination).

__all__ = [
    # Base classes and types
    "UniversalCoordinator",
    "OperationType",
    "DecisionResponse",
    "OperationResult",
    "CoordinatorCapabilities",
    "MemoryPressureLevel",
    # Core coordinators
    "UniversalResearchCoordinator",
    "UniversalExecutionCoordinator",
    "UniversalSecurityCoordinator",
    "UniversalMonitoringCoordinator",
    "UniversalMemoryCoordinator",
    "SecurityCoordinator",
    "OpsECCoordinator",
    # Security DTOs
    "SecurityLevel",
    "SecurityContext",
    "SecurityResult",
    # Memory management
    "MemoryAllocation",
    "MemoryStatistics",
    "MemoryZone",
    # Validation coordinator
    "UniversalValidationCoordinator",
    "ValidationSeverity",
    "OutputFormat",
    "ValidationResult",
    "CleaningResult",
    # Universal research coordinator
    "UniversalResearchCoordinator",
    "ResearchDepth",
    "HierarchicalPlan",
    "ExcavationConfig",
    "ExcavationStrategy",
    "ResearchPaper",
    "ResearchThread",
    "MetaPattern",
    "ResearchTheory",
    # Swarm intelligence
    "UniversalSwarmCoordinator",
    "SwarmState",
    "SwarmMetrics",
    "AdaptiveStrategy",
    "SwarmAgent",
    # Meta-reasoning
    "UniversalMetaReasoningCoordinator",
    "ReasoningStrategy",
    "ReasoningStep",
    "ReasoningChain",
    "SprintClock",  # BLITZ-04
    "ThoughtNode",
    "TransactionalToTCheckpointer",  # UNIFIED-005
    # Performance optimization
    "AgentPerformanceOptimizer",
    "AgentPool",
    "IntelligentLoadBalancer",
    "AsyncExecutionOptimizer",
    "LoadBalancingConfig",
    "OptimizationReport",
    "AgentMetrics",
    # Resource allocator
    "IntelligentResourceAllocator",
    "ResourceRequest",
    "ResourceAllocation",
    "ResourceType",
    "Priority",
    # Multi-agent coordination
    "AgentCoordinationEngine",
    "AgentType",
    "TaskPriority",
    "AgentCapability",
    "AgentPerformance",
    "TaskRequest",
    "TaskResult",
    "CoordinationStrategy",
    "coordinated_search",
    # Privacy enhanced research
    "PrivacyEnhancedResearch",
    "PrivacyConfig",
    "PrivacyLevel",
    "DataRetention",
    "AuditRecord",
    "AnonymizedRequest",
    "SanitizedResult",
    "private_research",
    # Research optimizer
    "ResearchOptimizer",
    "OptimizationConfig",
    "OptimizationStrategy",
    "CachePolicy",
    "QueryMetrics",
    "OptimizedResult",
    "optimized_research",
    "create_optimized_pipeline",
    # Catalog
    "catalog",
]
