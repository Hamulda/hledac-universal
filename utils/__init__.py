"""
Utility funkce pro UniversalResearchOrchestrator.

Obsahuje:
- PerformanceMonitor: Sledování výkonu
- WorkflowEngine: DAG-based workflow execution
- PredictivePlanner: Prediktivní plánování
- QualityValidator: Validace kvality
- Filtering: URL filtering a frontier management
- LanguageDetector: Detekce jazyka
- ParallelExecutionOptimizer: Paralelní optimalizace
- IntelligentResourceAllocator: M1 P/E core optimalizace
- AnomalyDetector: Detekce anomálií v resource metrikách
- PredictiveScaler: Prediktivní škálování workload
- ResourceMetrics: Dataclass pro resource metriky
- ResourceLimits: Limity pro M1 8GB systémy
- DataValidator: Validace dat (email, URL, JSON schema)
- QueryExpansion: Rozšiřování dotazů s doménovými synonymy
- Ranking: Reciprocal Rank Fusion pro kombinování výsledků
- IntelligentCache: Chytrý cache s LRU/LFU/ADAPTIVE eviction
"""

from .action_result import ActionResult  # NEW from sprint 68
from .async_utils import TaskResult, bounded_gather, bounded_map, map_as_completed  # Sprint 81 Fáze 2
from .bloom_filter import (
    BloomFilter,
    BloomFilterStats,
    ScalableBloomFilter,
    create_content_fingerprint,
    create_url_deduplicator,
)  # NEW from utils
from .deduplication import (
    ContentDeduplicator,
    DeduplicationConfig,
    DeduplicationEngine,
    DeduplicationMatch,
    DeduplicationResult,
    DeduplicationStats,
    DeduplicationStrategy,
    MetadataDeduplicator,
    QueryItem,
    SemanticDeduplicator,
    SimilarityScore,
)
from .encryption import DataEncryption, DecryptionResult, EncryptionResult  # NEW from utils
from .entity_extractor import EntityExtractor, ExtractedEntity, PatternType  # NEW from utils
from .execution_optimizer import (
    AnomalyDetector,
    ExecutionStrategy,
    IntelligentResourceAllocator,
    OptimizationLevel,
    ParallelExecutionOptimizer,
    PredictiveScaler,
    ResourceLimits,
    ResourceMetrics,
    ResourceType,
    TaskMetrics,
    TaskType,
    WorkerMetrics,
    create_m1_resource_allocator,
)
from .filtering import (
    EfficientFrontier,
    FastFilter,
    FilterStats,
    FrontierStats,
    get_fast_filter,
    get_frontier,
)
from .intelligent_cache import (
    CacheConfig,
    CacheEntry,
    CacheStats,
    EvictionStrategy,
    IntelligentCache,
    MemoryOptimizedURLSet,  # NEW from utils
    get_global_cache,
)
from .language import LanguageDetector, create_language_detector
from .lazy_imports import LazyImportManager, LazyLoader, lazy_import  # NEW from utils
from .performance_monitor import PerformanceMetrics, PerformanceMonitor, QualityValidator
from .predictive_planner import Prediction, PredictivePlanner, RollbackManager
from .query_expansion import (
    DomainSpecificExpansionStrategy,
    ExpansionConfig,
    # MSQES Expansion Strategies
    ExpansionStrategy,
    MultiStrategyExpander,
    QueryExpander,
    QueryVariation,
    SemanticExpansionStrategy,
    SyntacticExpansionStrategy,
    expand_query,
)
from .ranking import (
    RankedResult,
    ReciprocalRankFusion,
    RRFConfig,
    ScoreAggregator,
    fuse_results,
)
from .rate_limiter import (
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    with_rate_limit,
)  # NEW from stealth_toolkit integration
from .robots_parser import RobotsDocument, RobotsParser, Rule  # NEW from utils
from .semantic import (
    FilterResult,
    KeywordFilter,
    LightweightTokenizer,
    Model2VecEmbedding,
    SemanticFilter,
    SimpleEmbedding,
)
from .tech_detection import TechStackResult, TechStackSignature  # NEW from scanners
from .validation import (
    DataValidator,
    ValidationError,
    ValidationSeverity,
    create_sample_schema,
)
from .workflow_engine import Task, TaskStatus, TaskType, Workflow, WorkflowEngine


def _uuid7_stdlib() -> bool:
    """Check if stdlib uuid.uuid7 is available (Python 3.14+)."""
    import uuid as _uuid
    return hasattr(_uuid, "uuid7")


def uuid7() -> str:
    """
    Return a UUIDv7 string.

    Prefers stdlib uuid.uuid7() when available (Python 3.14+).
    Falls back to uuid.uuid4() for older runtimes.
    Returns str, not UUID object.
    """
    import uuid as _uuid

    if hasattr(_uuid, "uuid7"):
        return str(_uuid.uuid7())
    return str(_uuid.uuid4())


def get_uuid7_compat_status() -> dict:
    """Return compat shim status."""
    return {
        "stdlib_uuid7": _uuid7_stdlib(),
        "fallback": "uuid4" if not _uuid7_stdlib() else "uuid7",
    }


__all__ = [
    # NEW from sprint 68
    "ActionResult",
    # Performance
    "PerformanceMonitor",
    "QualityValidator",
    "PerformanceMetrics",
    # Workflow
    "WorkflowEngine",
    "Workflow",
    "Task",
    "TaskType",
    "TaskStatus",
    # Predictive
    "PredictivePlanner",
    "Prediction",
    "RollbackManager",
    # Filtering
    "FastFilter",
    "EfficientFrontier",
    "FilterStats",
    "FrontierStats",
    "get_fast_filter",
    "get_frontier",
    # Language
    "LanguageDetector",
    "create_language_detector",
    # Execution Optimization
    "ParallelExecutionOptimizer",
    "ExecutionStrategy",
    "TaskType",
    "TaskMetrics",
    "WorkerMetrics",
    # Validation
    "DataValidator",
    "ValidationError",
    "ValidationSeverity",
    "create_sample_schema",
    # Semantic
    "SemanticFilter",
    "KeywordFilter",
    "FilterResult",
    "SimpleEmbedding",
    "Model2VecEmbedding",
    "LightweightTokenizer",
    # Query Expansion
    "QueryExpander",
    "ExpansionConfig",
    "expand_query",
    # MSQES Expansion Strategies
    "ExpansionStrategy",
    "QueryVariation",
    "SemanticExpansionStrategy",
    "SyntacticExpansionStrategy",
    "DomainSpecificExpansionStrategy",
    "MultiStrategyExpander",
    # Deduplication
    "DeduplicationStrategy",
    "DeduplicationConfig",
    "QueryItem",
    "SimilarityScore",
    "DeduplicationMatch",
    "DeduplicationResult",
    "DeduplicationStats",
    "SemanticDeduplicator",
    "ContentDeduplicator",
    "MetadataDeduplicator",
    "DeduplicationEngine",
    # Ranking
    "ReciprocalRankFusion",
    "RRFConfig",
    "RankedResult",
    "ScoreAggregator",
    "fuse_results",
    # Intelligent Cache
    "IntelligentCache",
    "CacheConfig",
    "CacheEntry",
    "CacheStats",
    "EvictionStrategy",
    "get_global_cache",
    "MemoryOptimizedURLSet",
    # NEW from utils:
    "BloomFilter",
    "BloomFilterStats",
    "ScalableBloomFilter",
    "create_url_deduplicator",
    "create_content_fingerprint",
    "EntityExtractor",
    "ExtractedEntity",
    "PatternType",
    "LazyImportManager",
    "LazyLoader",
    "lazy_import",
    "RobotsParser",
    "RobotsDocument",
    "Rule",
    "TechStackSignature",
    "TechStackResult",
    # Encryption
    "DataEncryption",
    "EncryptionResult",
    "DecryptionResult",
    # Rate Limiter (from stealth_toolkit)
    "RateLimiter",
    "RateLimitConfig",
    "RateLimitExceeded",
    "with_rate_limit",
    # Sprint 81 Fáze 2 - Bounded Concurrency
    "bounded_map",
    "map_as_completed",
    "bounded_gather",
    "TaskResult",
    # UUID7 compat shim (F208N-D)
    "uuid7",
    "get_uuid7_compat_status",
]
