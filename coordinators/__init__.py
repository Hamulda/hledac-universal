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
from .base import (
    UniversalCoordinator,
    OperationType,
    DecisionResponse,
    OperationResult,
    CoordinatorCapabilities,
    MemoryPressureLevel
)

# Core coordinators
from .research_coordinator import UniversalResearchCoordinator
from .execution_coordinator import UniversalExecutionCoordinator
from .security_coordinator import UniversalSecurityCoordinator
from .monitoring_coordinator import UniversalMonitoringCoordinator
from .memory_coordinator import (
    UniversalMemoryCoordinator,
    MemoryAllocation,
    MemoryStatistics,
    MemoryZone,
)

# Validation coordinator
from .validation_coordinator import (
    UniversalValidationCoordinator,
    ValidationSeverity,
    OutputFormat,
    ValidationResult,
    CleaningResult,
)

# Advanced coordinators (ACTIVE)
from .advanced_research_coordinator import (
    UniversalAdvancedResearchCoordinator,
    ExcavationConfig,
    ExcavationStrategy,
    ResearchPaper,
    ResearchThread,
    MetaPattern,
    ResearchTheory,
)
from .swarm_coordinator import (
    UniversalSwarmCoordinator,
    SwarmState,
    SwarmMetrics,
    AdaptiveStrategy,
    SwarmAgent,
)
from .meta_reasoning_coordinator import (
    UniversalMetaReasoningCoordinator,
    ReasoningStrategy,
    ReasoningStep,
    ReasoningChain,
    ThoughtNode,
)

# Performance optimization
from .performance_coordinator import (
    AgentPerformanceOptimizer,
    AgentPool,
    IntelligentLoadBalancer,
    AsyncExecutionOptimizer,
    LoadBalancingConfig,
    OptimizationReport,
    AgentMetrics,
)

# Benchmark coordinator
from .benchmark_coordinator import (
    AgentBenchmarker,
    BenchmarkConfig,
    BenchmarkReport,
    AgentBenchmarkResult,
    MemoryProfiler,
    run_agent_benchmarks,
    run_quick_performance_check,
)

# Resource allocator
from .resource_allocator import (
    IntelligentResourceAllocator,
    ResourceRequest,
    ResourceAllocation,
    ResourceType,
    Priority,
)

# Multi-agent coordination
from .agent_coordination_engine import (
    AgentCoordinationEngine,
    AgentType,
    TaskPriority,
    AgentCapability,
    AgentPerformance,
    TaskRequest,
    TaskResult,
    CoordinationStrategy,
    coordinated_search,
)

# Privacy enhanced research
from hledac.universal.project_types import PrivacyLevel
from .privacy_enhanced_research import (
    PrivacyEnhancedResearch,
    PrivacyConfig,
    DataRetention,
    AuditRecord,
    AnonymizedRequest,
    SanitizedResult,
    private_research,
)

# Research optimizer
from .research_optimizer import (
    ResearchOptimizer,
    OptimizationConfig,
    OptimizationStrategy,
    CachePolicy,
    QueryMetrics,
    OptimizedResult,
    optimized_research,
    create_optimized_pipeline,
)

# Registry
from .coordinator_registry import CoordinatorRegistry

# Coordinator catalog for domain-grouped lazy access
from ._catalog import catalog

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

    # Advanced research coordinators
    'UniversalAdvancedResearchCoordinator',
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

    # Registry
    'CoordinatorRegistry',

    # Catalog
    'catalog',
]