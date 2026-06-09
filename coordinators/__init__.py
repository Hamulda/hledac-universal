"""
Universal Coordinators
=======================

Consolidated coordinators for Hledac Universal Orchestrator v4.0.

Domain Organization (via CoordinatorCatalog):
    from coordinators import catalog
    catalog.domains                       # List all domains
    catalog.get('core')                   # Get domain coordinator mappings
    catalog.load('UniversalMemoryCoordinator')  # Lazy load

Domain Groups:
    - core: Research, Execution, Security, Monitoring, Memory, Validation
    - advanced: AdvancedResearch, Swarm, MetaReasoning, PrivacyEnhanced
    - optimization: Performance, Benchmark, Resource, ResearchOptimizer
    - infrastructure: Base, Registry, Mixins
    - specialized: Fetch, Graph, Archive, Claims, Multimodal, Render, AgentCoordination

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
    PrivacyLevel,  # type: ignore[ty:unresolved-import]  # pre-existing absolute import — module not in project (historical namespace)
)

# Coordinator catalog for domain-grouped lazy access
from ._catalog import catalog

# Benchmark coordinator (DEPRECATED 2026-06-03 → _deprecated/benchmark_coordinator_shim)
from ._deprecated.benchmark_coordinator_shim import (
    AgentBenchmarker,
    AgentBenchmarkResult,
    BenchmarkConfig,
    BenchmarkReport,
    MemoryProfiler,
    run_agent_benchmarks,
    run_quick_performance_check,
)

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
    ThoughtNode,
    UniversalMetaReasoningCoordinator,
)
from .monitoring_coordinator import UniversalMonitoringCoordinator

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

# Resource allocator
from .resource_allocator import (
    IntelligentResourceAllocator,
    Priority,
    ResourceAllocation,
    ResourceRequest,
    ResourceType,
)
from .security_coordinator import UniversalSecurityCoordinator
from .swarm_coordinator import (
    AdaptiveStrategy,
    SwarmAgent,
    SwarmMetrics,
    SwarmState,
    UniversalSwarmCoordinator,
)

# Validation coordinator
from .validation_coordinator import (
    CleaningResult,
    OutputFormat,
    UniversalValidationCoordinator,
    ValidationResult,
    ValidationSeverity,
)

# LEGACY IMPORTS - Deprecated, moved to legacy/coordinators/
# These imports will be removed in v5.0
try:
    import warnings
    warnings.warn(
        "Quantum, NAS, and FederatedLearning coordinators are deprecated. "
        "They have been moved to legacy/coordinators/. "
        "These imports will be removed in v5.0.",
        DeprecationWarning,
        stacklevel=2
    )
except ImportError:
    pass

__all__ = [
    # Base classes and types
    'UniversalCoordinator',
    'OperationType',
    'DecisionResponse',
    'OperationResult',
    'CoordinatorCapabilities',
    'MemoryPressureLevel',

    # Core coordinators
    'UniversalResearchCoordinator',
    'UniversalExecutionCoordinator',
    'UniversalSecurityCoordinator',
    'UniversalMonitoringCoordinator',
    'UniversalMemoryCoordinator',

    # Memory management
    'MemoryAllocation',
    'MemoryStatistics',
    'MemoryZone',

    # Validation coordinator
    'UniversalValidationCoordinator',
    'ValidationSeverity',
    'OutputFormat',
    'ValidationResult',
    'CleaningResult',

    # Universal research coordinator
    'UniversalResearchCoordinator',
    'ResearchDepth',
    'HierarchicalPlan',
    'ExcavationConfig',
    'ExcavationStrategy',
    'ResearchPaper',
    'ResearchThread',
    'MetaPattern',
    'ResearchTheory',

    # Swarm intelligence
    'UniversalSwarmCoordinator',
    'SwarmState',
    'SwarmMetrics',
    'AdaptiveStrategy',
    'SwarmAgent',

    # Meta-reasoning
    'UniversalMetaReasoningCoordinator',
    'ReasoningStrategy',
    'ReasoningStep',
    'ReasoningChain',
    'ThoughtNode',

    # Performance optimization
    'AgentPerformanceOptimizer',
    'AgentPool',
    'IntelligentLoadBalancer',
    'AsyncExecutionOptimizer',
    'LoadBalancingConfig',
    'OptimizationReport',
    'AgentMetrics',

    # Benchmark coordinator
    'AgentBenchmarker',
    'BenchmarkConfig',
    'BenchmarkReport',
    'AgentBenchmarkResult',
    'MemoryProfiler',
    'run_agent_benchmarks',
    'run_quick_performance_check',

    # Resource allocator
    'IntelligentResourceAllocator',
    'ResourceRequest',
    'ResourceAllocation',
    'ResourceType',
    'Priority',

    # Multi-agent coordination
    'AgentCoordinationEngine',
    'AgentType',
    'TaskPriority',
    'AgentCapability',
    'AgentPerformance',
    'TaskRequest',
    'TaskResult',
    'CoordinationStrategy',
    'coordinated_search',

    # Privacy enhanced research
    'PrivacyEnhancedResearch',
    'PrivacyConfig',
    'PrivacyLevel',
    'DataRetention',
    'AuditRecord',
    'AnonymizedRequest',
    'SanitizedResult',
    'private_research',

    # Research optimizer
    'ResearchOptimizer',
    'OptimizationConfig',
    'OptimizationStrategy',
    'CachePolicy',
    'QueryMetrics',
    'OptimizedResult',
    'optimized_research',
    'create_optimized_pipeline',

    # Catalog
    'catalog',
]


# ---------------------------------------------------------------------------
# Usage patterns (F4.1 — aggregator docs)
# ---------------------------------------------------------------------------
# Two ways to consume a coordinator:
#
# 1) Eager import (simple, but loads deps at import-time):
#    from hledac.universal.coordinators.memory_coordinator import UniversalMemoryCoordinator
#    coordinator = UniversalMemoryCoordinator(...)
#
# 2) Lazy load via the domain catalog (preferred for M1 — only pays import
#    cost when the coordinator is actually needed):
#    from hledac.universal.coordinators import catalog
#    MemoryCoordinator = catalog.load("UniversalMemoryCoordinator")
#    coordinator = MemoryCoordinator(...)
#
# Introspection (no coordinator modules imported, zero RAM):
#    catalog.domains                        # ['core', 'advanced', 'optimization', 'infrastructure', 'specialized']
#    catalog.list_all()                     # {domain: [name, ...], ...}
#    catalog.list_domain("core")            # ['UniversalResearchCoordinator', 'UniversalExecutionCoordinator', ...]
#    catalog.search("memory")               # case-insensitive substring match
#    catalog.is_known("FetchCoordinator")   # True/False without importing
#
# Domain groups (see _catalog.py):
#   - core          : Research, Execution, Security, Monitoring, Memory, Validation
#   - advanced      : Swarm, MetaReasoning, PrivacyEnhanced
#   - optimization  : Performance, Benchmark* (deprecated), Resource, ResearchOptimizer
#   - infrastructure: Base, Registry, Mixins, enums
#   - specialized   : Fetch, Graph, Archive, Claims, Multimodal, Render, AgentCoordination
#
# * Benchmark coordinator moved to _deprecated/ on 2026-06-03 (F3.3).
#   Importing it still works but emits DeprecationWarning.
