"""
hledac_types/ — Re-export shim for project_types.py

ISSUE 7.2: Split preparation. All types are currently in project_types.py.
This package provides a future-proof import path for incremental migration.

Usage (current):
    from hledac.universal.project_types import ResearchMode, AgentMetrics, ...

Usage (after migration):
    from hledac.universal.types import ResearchMode, AgentMetrics, ...
"""





    # Enums
    ActionResultType,
    ActionType,
    AgentState,
    AnonymizationLevel,
    BrowserType,
    CaptchaType,
    CommunicationPattern,
    ContentSource,
    EventType,
    ExplorationStrategy,
    LeakSource,
    MessagePriority,
    OperationType,
    PrivacyEventCategory,
    PrivacyLevel,
    ProcessingState,
    ProtocolType,
    ReasoningMode,
    ResearchMode,
    RiskLevel,
    SecurityLevel,
    Severity,
    SubAgentType,
    SystemState,
    OrchestratorState,
    QueryComplexity,
    ResearchPhase,
    ObfuscationLevel,
    WipeStandard,
    # Exceptions
    CircuitBreakerOpenError,
    MemoryPressureError,
    OfflineModeError,
    OrchestratorError,
    RateLimitExceeded,
    StagnationError,
    # Configs (msgspec.Struct)
    AgentManagerConfig,
    CoordinationConfig,
    CommunicationConfig,

from _core import aclose    DeepResearchConfig,
    GhostConfig,
    MemoryConfig,
    ModelConfig,
    PrivacyConfig,
    ResearchConfig,
    SecurityConfig,
    SNNConfig,
    StealthConfig,
    STDPParams,
    ReservoirConfig,
    # DTOs (msgspec.Struct)
    ActionResult,
    AnalyzerResult,
    ArchiveSnapshot,
    BranchDecision,
    CanonicalGroundingHints,
    DataLeakAlert,
    DecisionContext,
    DecisionRequest,
    DecisionResponse,
    DestructionResult,
    ExportHandoff,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExplorationNode,
    GhostAction,
    GhostMission,
    NeuromorphicEnergyReport,
    NeuronParameters,
    ProcessingMetrics,
    ProcessingResult,
    ProviderRequest,
    ProviderResult,
    RunCorrelation,
    SNNEncryptedContainer,
    SpikeData,
    StealthSession,
    CaptchaSolution,
    PrivacyStatus,
    SubAgentResult,
    ResearchResult,
    SystemMetrics,
    AgentMetrics,
    ComplexityAnalysis,
    # UNIFIED-004: Micro-sprint types
    MicroSprintPlan,
    MicroSprintResult,
)

__all__ = [
    # Enums
    'ActionResultType', 'ActionType', 'AgentState', 'AnonymizationLevel',
    'BrowserType', 'CaptchaType', 'CommunicationPattern', 'ContentSource',
    'EventType', 'ExplorationStrategy', 'LeakSource', 'MessagePriority',
    'OperationType', 'PrivacyEventCategory', 'PrivacyLevel', 'ProcessingState',
    'ProtocolType', 'ReasoningMode', 'ResearchMode', 'RiskLevel',
    'SecurityLevel', 'Severity', 'SubAgentType', 'SystemState',
    'OrchestratorState', 'QueryComplexity', 'ResearchPhase',
    'ObfuscationLevel', 'WipeStandard',
    # Exceptions
    'CircuitBreakerOpenError', 'MemoryPressureError', 'OfflineModeError',
    'OrchestratorError', 'RateLimitExceeded', 'StagnationError',
    # Configs
    'AgentManagerConfig', 'CoordinationConfig', 'CommunicationConfig',
    'DeepResearchConfig', 'GhostConfig', 'MemoryConfig', 'ModelConfig',
    'PrivacyConfig', 'ResearchConfig', 'SecurityConfig', 'SNNConfig',
    'StealthConfig', 'STDPParams', 'ReservoirConfig',
    # DTOs
    'ActionResult', 'AnalyzerResult', 'ArchiveSnapshot', 'BranchDecision',
    'CanonicalGroundingHints', 'DataLeakAlert', 'DecisionContext',
    'DecisionRequest', 'DecisionResponse', 'DestructionResult',
    'ExportHandoff', 'ExecutionContext', 'ExecutionRequest', 'ExecutionResult',
    'ExplorationNode', 'GhostAction', 'GhostMission', 'NeuromorphicEnergyReport',
    'NeuronParameters', 'ProcessingMetrics', 'ProcessingResult',
    'ProviderRequest', 'ProviderResult', 'RunCorrelation', 'SNNEncryptedContainer',
    'SpikeData', 'StealthSession', 'CaptchaSolution', 'PrivacyStatus',
    'SubAgentResult', 'ResearchResult', 'SystemMetrics', 'AgentMetrics',
    'ComplexityAnalysis',
    # UNIFIED-004: Micro-sprint types
    'MicroSprintPlan', 'MicroSprintResult',
]
