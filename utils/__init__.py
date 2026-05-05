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
from .performance_monitor import PerformanceMonitor, QualityValidator, PerformanceMetrics
from .workflow_engine import WorkflowEngine, Workflow, Task, TaskType, TaskStatus
from .predictive_planner import PredictivePlanner, Prediction, RollbackManager
from .filtering import (
    FastFilter,
    EfficientFrontier,
    FilterStats,
    FrontierStats,
    get_fast_filter,
    get_frontier,
)
from .language import LanguageDetector, create_language_detector
from .execution_optimizer import (
    ParallelExecutionOptimizer,
    ExecutionStrategy,
    TaskType,
    TaskMetrics,
    WorkerMetrics,
    IntelligentResourceAllocator,
    create_m1_resource_allocator,
    ResourceType,
    OptimizationLevel,
    ResourceMetrics,
    ResourceLimits,
    AnomalyDetector,
    PredictiveScaler,
)
from .validation import (
    DataValidator,
    ValidationError,
    ValidationSeverity,
    create_sample_schema,
)
from .semantic import (
    SemanticFilter,
    KeywordFilter,
    FilterResult,
    SimpleEmbedding,
    Model2VecEmbedding,
    LightweightTokenizer,
)
from .query_expansion import (
    QueryExpander, 
    ExpansionConfig, 
    expand_query,
    # MSQES Expansion Strategies
    ExpansionStrategy,
    QueryVariation,
    SemanticExpansionStrategy,
    SyntacticExpansionStrategy,
    DomainSpecificExpansionStrategy,
    MultiStrategyExpander,
)
from .ranking import (
    ReciprocalRankFusion,
    RRFConfig,
    RankedResult,
    ScoreAggregator,
    fuse_results,
)
from .deduplication import (
    DeduplicationStrategy,
    DeduplicationConfig,
    QueryItem,
    SimilarityScore,
    DeduplicationMatch,
    DeduplicationResult,
    DeduplicationStats,
    SemanticDeduplicator,
    ContentDeduplicator,
    MetadataDeduplicator,
    DeduplicationEngine,
)
from .intelligent_cache import (
    IntelligentCache,
    CacheConfig,
    CacheEntry,
    CacheStats,
    EvictionStrategy,
    get_global_cache,
    MemoryOptimizedURLSet,  # NEW from utils
)
from .bloom_filter import (
    BloomFilter,
    BloomFilterStats,
    ScalableBloomFilter,
    create_url_deduplicator,
    create_content_fingerprint,
)  # NEW from utils
from .entity_extractor import EntityExtractor, ExtractedEntity, PatternType  # NEW from utils
from .lazy_imports import LazyImportManager, LazyLoader, lazy_import  # NEW from utils
from .robots_parser import RobotsParser, RobotsDocument, Rule  # NEW from utils
from .tech_detection import TechStackSignature, TechStackResult  # NEW from scanners
from .encryption import DataEncryption, EncryptionResult, DecryptionResult  # NEW from utils
from .rate_limiter import (
    RateLimiter,
    RateLimitConfig,
    RateLimitExceeded,
    with_rate_limit,
)  # NEW from stealth_toolkit integration
from .async_utils import bounded_map, map_as_completed, bounded_gather, TaskResult  # Sprint 81 Fáze 2


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
