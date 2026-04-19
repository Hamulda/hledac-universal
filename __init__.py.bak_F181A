"""
Universal Orchestrator Package v5.0 - Autonomous
===============================================

Consolidated autonomous orchestrator combining all capabilities:

Main exports:
- AutonomousOrchestrator: Fully autonomous orchestrator (v5.0)
- create_autonomous_orchestrator: Factory function
- UniversalConfig: Configuration class
- ResearchMode, OrchestratorState: Enums
- DiscoveryDepth, ResearchPhase: Autonomous enums

Legacy (for backward compatibility):
- UniversalResearchOrchestrator -> AutonomousOrchestrator
- create_universal_orchestrator -> create_autonomous_orchestrator

Layers (internal use):
- GhostLayer: GhostDirector integration
- MemoryLayer: M1 memory management
- CoordinationLayer: Coordinator delegation
- SecurityLayer: Obfuscation, secure destruction
- StealthLayer: Browser, evasion, CAPTCHA
- ResearchLayer: Deep research, citations
- PrivacyLayer: VPN/Tor, PGP, audit

Autonomous Tools:
- AgentCoordinationEngine: Multi-agent orchestration
- ResearchOptimizer: Caching, deduplication
- PrivacyEnhancedResearch: Anonymization
- QueryExpander: Query variations
- ReciprocalRankFusion: Result fusion
- IntelligentCache: LRU/LFU/ADAPTIVE

Example:
    from hledac.universal import create_autonomous_orchestrator, DiscoveryDepth
    
    orchestrator = await create_autonomous_orchestrator(
        mode=ResearchMode.AUTONOMOUS,
        m1_optimized=True
    )
    
    result = await orchestrator.research(
        "quantum computing",
        depth=DiscoveryDepth.EXTREME
    )
    print(result.synthesized_answer)
"""

from .config import UniversalConfig, create_config, load_config_from_file

# NEW: Fully Autonomous Orchestrator (v6.0 Unified)
from .autonomous_orchestrator import (
    FullyAutonomousOrchestrator,
    autonomous_research,
    DiscoveryDepth,
    ResearchPhase,
    SourceType,
    AutonomousStrategy,
    ResearchSource,
    ResearchFinding,
    ComprehensiveResearchResult,
    ResilientExecutionManager,
    # NEW: Unified Tool Registry & Autonomous Workflow
    UnifiedToolRegistry,
    ToolCategory,
    ToolCapability,
    AutonomousWorkflowEngine,
    WorkflowState,
    deep_research,
)

# Legacy compatibility aliases
AutonomousOrchestrator = FullyAutonomousOrchestrator
UniversalResearchOrchestrator = FullyAutonomousOrchestrator
create_universal_orchestrator = autonomous_research
create_autonomous_orchestrator = autonomous_research

from .types import (
    ActionType,
    AgentState,
    BrowserType,
    CaptchaType,
    DecisionRequest,
    DecisionResponse,
    ExecutionContext,
    ExplorationStrategy,
    ObfuscationLevel,
    OperationType,
    OrchestratorState,
    ResearchMode,
    ResearchResult,
    RiskLevel,
    SubAgentResult,
    SubAgentType,
    SystemState,
    WipeStandard,
)

# NEW: Research Context and Evidence Log
from .research_context import (
    ResearchContext,
    BudgetState,
    Entity,
    EntityType,
    Hypothesis,
    HypothesisStatus,
    ErrorRecord,
    ErrorSeverity,
)
from .evidence_log import (
    EvidenceLog,
    EvidenceEvent,
)

# NEW: Capability System (M1 8GB Optimization)
from .capabilities import (
    Capability,
    CapabilityRegistry,
    CapabilityRouter,
    ModelLifecycleManager,
    create_default_registry,
)

# Import Layers with Capabilities Manager
from .layers import (
    LayerManager,
    get_layer_manager,
    UnifiedCapabilitiesManager,
    get_capabilities_manager,
)

# Import coordinators
from .coordinators import (
    UniversalCoordinator,
    UniversalResearchCoordinator,
    UniversalExecutionCoordinator,
    UniversalSecurityCoordinator,
    UniversalMonitoringCoordinator,
    CoordinatorRegistry,
    OperationType as CoordinatorOperationType,
    DecisionResponse as CoordinatorDecisionResponse,
    OperationResult as CoordinatorOperationResult,
    # Autonomous coordinators
    AgentCoordinationEngine,
    AgentType,
    TaskPriority,
    AgentCapability,
    TaskRequest,
    TaskResult,
    CoordinationStrategy,
    coordinated_search,
    PrivacyEnhancedResearch,
    PrivacyConfig,
    PrivacyLevel,
    DataRetention,
    private_research,
    ResearchOptimizer,
    OptimizationConfig,
    OptimizationStrategy,
    CachePolicy,
    QueryMetrics,
    OptimizedResult,
    optimized_research,
    create_optimized_pipeline,
)

# Import autonomous utilities
from .utils import (
    QueryExpander,
    ExpansionConfig,
    expand_query,
    ReciprocalRankFusion,
    RRFConfig,
    RankedResult,
    ScoreAggregator,
    fuse_results,
    IntelligentCache,
    CacheConfig,
    CacheEntry,
    CacheStats,
    EvictionStrategy,
    get_global_cache,
    # Rate Limiter (from stealth_toolkit integration)
    RateLimiter,
    RateLimitConfig,
    RateLimitExceeded,
    with_rate_limit,
)

# Unified Research Engine - Complete integration of all research tools
try:
    from .enhanced_research import (
        UnifiedResearchEngine,
        UnifiedResearchConfig,
        ResearchDepth,
        QueryType,
        ResearchFinding,
        UnifiedResearchResult,
        deep_research,
        create_unified_research_engine,
    )
    UNIFIED_RESEARCH_AVAILABLE = True
except ImportError as e:
    UNIFIED_RESEARCH_AVAILABLE = False
    import logging
    logging.getLogger(__name__).debug(
        f"Unified research engine not available: {e}"
    )

__version__ = "5.0.0-autonomous"

__all__ = [
    # NEW: Autonomous Orchestrator
    "AutonomousOrchestrator",
    "create_autonomous_orchestrator",
    "DiscoveryDepth",
    "ResearchPhase",

    # NEW: Research Context & Evidence Log
    "ResearchContext",
    "BudgetState",
    "Entity",
    "EntityType",
    "Hypothesis",
    "HypothesisStatus",
    "ErrorRecord",
    "ErrorSeverity",
    "EvidenceLog",
    "EvidenceEvent",

    # NEW: Capability System (M1 8GB Optimization)
    "Capability",
    "CapabilityRegistry",
    "CapabilityRouter",
    "ModelLifecycleManager",
    "create_default_registry",

    # NEW: Unified Capabilities Manager (All Layers + Coordinators + Utils)
    "LayerManager",
    "get_layer_manager",
    "UnifiedCapabilitiesManager",
    "get_capabilities_manager",
    
    # Legacy compatibility
    "UniversalResearchOrchestrator",
    "create_universal_orchestrator",
    
    # Configuration
    "UniversalConfig",
    "create_config",
    "load_config_from_file",
    
    # Types - Core
    "ResearchMode",
    "OrchestratorState",
    "SystemState",
    "AgentState",
    "SubAgentType",
    "ActionType",
    "OperationType",
    "ResearchResult",
    "SubAgentResult",
    "ExecutionContext",
    "DecisionRequest",
    "DecisionResponse",
    "BrowserType",
    "CaptchaType",
    "ExplorationStrategy",
    "ObfuscationLevel",
    "RiskLevel",
    "WipeStandard",
    
    # Coordinators
    "UniversalCoordinator",
    "UniversalResearchCoordinator",
    "UniversalExecutionCoordinator",
    "UniversalSecurityCoordinator",
    "UniversalMonitoringCoordinator",
    "CoordinatorRegistry",
    "CoordinatorOperationType",
    "CoordinatorDecisionResponse",
    "CoordinatorOperationResult",
    
    # Autonomous Coordinators
    "AgentCoordinationEngine",
    "AgentType",
    "TaskPriority",
    "AgentCapability",
    "TaskRequest",
    "TaskResult",
    "CoordinationStrategy",
    "coordinated_search",
    "PrivacyEnhancedResearch",
    "PrivacyConfig",
    "PrivacyLevel",
    "DataRetention",
    "private_research",
    "ResearchOptimizer",
    "OptimizationConfig",
    "OptimizationStrategy",
    "CachePolicy",
    "QueryMetrics",
    "OptimizedResult",
    "optimized_research",
    "create_optimized_pipeline",
    
    # Autonomous Utilities
    "QueryExpander",
    "ExpansionConfig",
    "expand_query",
    "ReciprocalRankFusion",
    "RRFConfig",
    "RankedResult",
    "ScoreAggregator",
    "fuse_results",
    "IntelligentCache",
    "CacheConfig",
    "CacheEntry",
    "CacheStats",
    "EvictionStrategy",
    "get_global_cache",
    # Stealth Toolkit Integration
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceeded",
    "with_rate_limit",
]

# Update __all__ for unified research engine
if UNIFIED_RESEARCH_AVAILABLE:
    __all__.extend([
        # Unified Research Engine
        "UnifiedResearchEngine",
        "UnifiedResearchConfig",
        "ResearchDepth",
        "QueryType",
        "ResearchFinding",
        "UnifiedResearchResult",
        "deep_research",
        "create_unified_research_engine",
        "UNIFIED_RESEARCH_AVAILABLE",
    ])

# =============================================================================
# ENHANCED FULLY AUTONOMOUS ORCHESTRATOR v6.0 - COMPLETE INTEGRATION
# =============================================================================
# Nový plně autonomní orchestrátor integrující ABSOLUTNĚ VŠE:
# - Deep Research (archives, dark web, steganography)
# - Temporal Analysis
# - OSINT Intelligence
# - Self-Healing & Recovery
# - AI-Powered Synthesis

try:
    from .autonomous_orchestrator_enhanced import (
        FullyAutonomousOrchestrator,
        SourceType,
        AutonomousStrategy,
        ResearchFinding,
        ResearchSource,
        ComprehensiveResearchResult,
        autonomous_research,
        ResilientExecutionManager,
    )
    
    # Alias pro jednodušší použití
    UltimateOrchestrator = FullyAutonomousOrchestrator
    
    ENHANCED_ORCHESTRATOR_AVAILABLE = True
    
except ImportError as e:
    ENHANCED_ORCHESTRATOR_AVAILABLE = False
    import logging
    logging.getLogger(__name__).debug(
        f"Enhanced orchestrator not available (some dependencies missing): {e}"
    )

# Update __all__ pro enhanced orchestrator
if ENHANCED_ORCHESTRATOR_AVAILABLE:
    __all__.extend([
        # Enhanced Orchestrator
        "FullyAutonomousOrchestrator",
        "UltimateOrchestrator",
        "ENHANCED_ORCHESTRATOR_AVAILABLE",
        
        # Enhanced Types
        "SourceType",
        "AutonomousStrategy",
        "ResearchFinding",
        "ResearchSource",
        "ComprehensiveResearchResult",
        
        # Convenience Functions
        "autonomous_research",
        "ResilientExecutionManager",
    ])


# =============================================================================
# SUPREME INTEGRATION EXPORTS — NOW LAZY (F12C normalization)
# =============================================================================
# PRE-F12C: These were EAGER imports that overrode the _LAZY_SUBPACKAGES entries.
# POST-F12C: All knowledge/tools/security symbols are served exclusively via
# PEP 562 __getattr__ lazy loading from _LAZY_SUBPACKAGES (lines 452-488).
# This restores lazy-loading discipline and removes import-time side-effects.
#
# Canonical owners (verified F12C):
#   knowledge.*  → knowledge/__init__.py (graph_layer, rag_engine, entity_linker)
#   tools.*      → tools/__init__.py (reranker, content_miner, adapters)
#   security.*   → security/__init__.py (pii_gate, vault_manager, encryption)
#
# External consumers (legacy/, tests/) import directly from subpackages, not here.
# SUPREME_INTEGRATION_AVAILABLE was always False — removed as dead attribution.
#
# Heavy deps (torch, sklearn, networkx, scipy) are now truly deferred until
# first attribute access, not loaded at package import time.
#
# KNOWN COLLISION (non-blocking, pre-existing):
#   ResearchFinding: enhanced_research.py:223 (@dataclass) vs
#                   legacy/autonomous_orchestrator.py:2956 (@dataclass)
#   Neither is pydantic — both are @dataclass.
#   Resolution paths:
#     - Eager default: .autonomous_orchestrator facade → legacy/ definition
#       (facade loads legacy/autonomous_orchestrator.py at import time)
#     - Enhanced conditional: only if UNIFIED_RESEARCH_AVAILABLE=True,
#       via conditional eager import of enhanced_research.py (NOT lazy)
#   ResearchFinding is NOT in _LAZY_SUBPACKAGES — no lazy resolution applies.
#   Collision is contained: neither definition overwrites the other in __all__.

SUPREME_INTEGRATION_AVAILABLE = False

# =============================================================================
# PEP 562 LAZY EXPORTS - Defer heavy subpackage imports
# Root cause: knowledge/tools/security/autonomy pull intelligence/* modules
# which have eager torch/sklearn/networkx/scipy imports at module level.
# Lazy __getattr__ defers loading until first attribute access.
# =============================================================================

_LAZY_SUBPACKAGES = {
    # Knowledge Components (from Supreme) - pulls torch via graph_rag
    # F1200B FIX: Corrected path from .knowledge.persistent_layer to .legacy.persistent_layer
    "PersistentKnowledgeLayer": (".legacy.persistent_layer", "PersistentKnowledgeLayer"),
    "KnowledgeNode": (".legacy.persistent_layer", "KnowledgeNode"),
    "KnowledgeEdge": (".legacy.persistent_layer", "KnowledgeEdge"),
    "NodeType": (".legacy.persistent_layer", "NodeType"),
    "EdgeType": (".legacy.persistent_layer", "EdgeType"),
    "KuzuDBBackend": (".legacy.persistent_layer", "KuzuDBBackend"),
    "JSONBackend": (".legacy.persistent_layer", "JSONBackend"),
    "GraphRAGOrchestrator": (".knowledge.graph_rag", "GraphRAGOrchestrator"),
    "KnowledgeGraphBuilder": (".knowledge.graph_builder", "KnowledgeGraphBuilder"),
    # Supreme Tools - pulls pandas via reranker
    "LightweightReranker": (".tools", "LightweightReranker"),
    "RerankResult": (".tools", "RerankResult"),
    "RerankRequest": (".tools", "RerankRequest"),
    "RerankerConfig": (".tools", "RerankerConfig"),
    "RerankerFactory": (".tools", "RerankerFactory"),
    "RustMiner": (".tools", "RustMiner"),
    "MiningResult": (".tools", "MiningResult"),
    "create_rust_miner": (".tools", "create_rust_miner"),
    # Supreme Security
    "SecurityGate": (".security", "SecurityGate"),
    "PIICategory": (".security", "PIICategory"),
    "PIIMatch": (".security", "PIIMatch"),
    "SanitizationResult": (".security", "SanitizationResult"),
    "create_security_gate": (".security", "create_security_gate"),
    "quick_sanitize": (".security", "quick_sanitize"),
    "LootManager": (".security", "LootManager"),
    "RamDiskVault": (".security", "RamDiskVault"),
}

# Also lazily export non-existent symbols that were previously in __all__ but not defined
# NOTE: SUPREME_INTEGRATION_AVAILABLE intentionally omitted from fallback attrs.
# Actual load state is tracked by the try/except block below (always False currently).
# Adding it back here with False would be a duplicate of the runtime truth.
_FALLBACK_ATTRS = {
    # Availability flags — truthful state after F12G reconciliation
    # ENHANCED: autonomous_orchestrator_enhanced.py does NOT exist → False
    "ENHANCED_ORCHESTRATOR_AVAILABLE": False,
    # INTEGRATED: orchestrator_integration.py EXISTS → True
    "INTEGRATED_ORCHESTRATOR_AVAILABLE": True,
    # BUDGET: budget_manager.py only at cache/ not root → False
    "BUDGET_MANAGER_AVAILABLE": False,
    # UNIFIED: enhanced_research.py exists → set by try/except below
    "UNIFIED_RESEARCH_AVAILABLE": False,
}


def __getattr__(name):
    """PEP 562 lazy attribute access - defer heavy subpackage imports."""
    # Check if this is a lazily-exported subpackage item
    if name in _LAZY_SUBPACKAGES:
        from importlib import import_module
        pkg_path, attr_name = _LAZY_SUBPACKAGES[name]
        module = import_module(pkg_path, package=__name__)
        value = getattr(module, attr_name)
        # Cache in globals to avoid repeated lookups
        globals()[name] = value
        return value
    # Fallback for availability flags that were set at module level
    if name in _FALLBACK_ATTRS:
        return _FALLBACK_ATTRS[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """PEP 562 module introspection - include lazy exports."""
    # Combine module-level names with lazy exports
    return list(__all__) + list(_LAZY_SUBPACKAGES.keys()) + list(_FALLBACK_ATTRS.keys())

# =============================================================================
# PHASE C INTEGRATION - Full Autonomy with Connected Coordinators
# =============================================================================

# Pre-initialize so assignment is single in __all__ extend below
INTEGRATED_ORCHESTRATOR_AVAILABLE = False
try:
    from .orchestrator_integration import (
        IntegratedOrchestrator,
        integrated_research,
    )
    INTEGRATED_ORCHESTRATOR_AVAILABLE = True

    # Make IntegratedOrchestrator the default for advanced usage
    AdvancedOrchestrator = IntegratedOrchestrator

except ImportError as e:
    import logging
    logging.getLogger(__name__).debug(
        f"Integrated orchestrator not available (optional dependencies): {e}"
    )

# Update __all__ for integrated orchestrator
if INTEGRATED_ORCHESTRATOR_AVAILABLE:
    __all__.extend([
        # Phase C Integration
        "IntegratedOrchestrator",
        "AdvancedOrchestrator",
        "integrated_research",
        "INTEGRATED_ORCHESTRATOR_AVAILABLE",
    ])

# =============================================================================
# BUDGET MANAGER - Resource Control for Autonomous Workflows
# =============================================================================

try:
    from .budget_manager import (
        BudgetManager,
        BudgetConfig,
        BudgetState,
        BudgetStatus,
        EvidenceLog,
        StopReason,
        create_budget_manager,
        create_quick_budget,
        create_deep_budget,
    )
    BUDGET_MANAGER_AVAILABLE = True

except ImportError as e:
    BUDGET_MANAGER_AVAILABLE = False
    import logging
    logging.getLogger(__name__).debug(
        f"BudgetManager not available: {e}"
    )

# Update __all__ for BudgetManager
if BUDGET_MANAGER_AVAILABLE:
    __all__.extend([
        # Budget Management
        "BudgetManager",
        "BudgetConfig",
        "BudgetState",
        "BudgetStatus",
        "EvidenceLog",
        "StopReason",
        "create_budget_manager",
        "create_quick_budget",
        "create_deep_budget",
        "BUDGET_MANAGER_AVAILABLE",
    ])
