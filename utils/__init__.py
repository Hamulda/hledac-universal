from __future__ import annotations

# Phase 0 alias: register `utils` as a top-level module so absolute
# `from utils.X` imports resolve regardless of how the package is launched
# (python -m hledac.universal, IDE, or direct script). See __main__.py
# Phase 0 hook for the original symptom; this is the canonical fix.
import sys as _sys

_sys.modules.setdefault('utils', _sys.modules[__name__])

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

from .action_result import ActionResult  # NEW from sprint 68  # noqa: E402
from .async_utils import TaskResult, bounded_gather, bounded_map, map_as_completed  # Sprint 81 Fáze 2  # noqa: E402
from .bloom_filter import (  # noqa: E402
    BloomFilter,
    BloomFilterStats,
    create_content_fingerprint,
    create_url_deduplicator,
)  # NEW from utils
from .deduplication import (  # noqa: E402
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
from .encryption import DataEncryption, DecryptionResult, EncryptionResult  # NEW from utils  # noqa: E402
from .entity_extractor import EntityExtractor, ExtractedEntity, PatternType  # NEW from utils  # noqa: E402
from .execution_optimizer import (  # noqa: E402
    AnomalyDetector,  # noqa: F401  # .execution_optimizer.AnomalyDetector
    ExecutionStrategy,
    IntelligentResourceAllocator,  # noqa: F401  # .execution_optimizer.IntelligentResourceAllocator
    OptimizationLevel,  # noqa: F401  # .execution_optimizer.OptimizationLevel
    ParallelExecutionOptimizer,
    PredictiveScaler,  # noqa: F401  # .execution_optimizer.PredictiveScaler
    ResourceLimits,  # noqa: F401  # .execution_optimizer.ResourceLimits
    ResourceMetrics,  # noqa: F401  # .execution_optimizer.ResourceMetrics
    ResourceType,  # noqa: F401  # .execution_optimizer.ResourceType
    TaskMetrics,
    TaskType,
    WorkerMetrics,
    create_m1_resource_allocator,  # noqa: F401  # .execution_optimizer.create_m1_resource_allocator
)
from .filtering import (  # noqa: E402
    EfficientFrontier,
    FastFilter,
    FilterStats,
    FrontierStats,
    get_fast_filter,
    get_frontier,
)
from .intelligent_cache import (  # noqa: E402
    CacheConfig,
    CacheEntry,
    CacheStats,
    EvictionStrategy,
    IntelligentCache,
    MemoryOptimizedURLSet,  # NEW from utils
    get_global_cache,
)
from .language import LanguageDetector, create_language_detector  # noqa: E402
from .lazy_imports import LazyImportManager, LazyLoader, lazy_import  # NEW from utils  # noqa: E402
from .performance_monitor import PerformanceMetrics, PerformanceMonitor, QualityValidator  # noqa: E402
from .predictive_planner import Prediction, PredictivePlanner, RollbackManager  # noqa: E402
from .query_expansion import (  # noqa: E402
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
from .ranking import (  # noqa: E402
    RankedResult,
    ReciprocalRankFusion,
    RRFConfig,
    ScoreAggregator,
    fuse_results,
)
from .rate_limiter import (  # noqa: E402
    RateLimitConfig,
    RateLimiter,
    RateLimitExceeded,
    with_rate_limit,
)  # NEW from stealth_toolkit integration
from .robots_parser import RobotsDocument, RobotsParser, Rule  # NEW from utils  # noqa: E402
from .semantic import (  # noqa: E402
    FilterResult,
    KeywordFilter,
    LightweightTokenizer,
    Model2VecEmbedding,
    SemanticFilter,
    SimpleEmbedding,
)
from .tech_detection import TechStackResult, TechStackSignature  # NEW from scanners  # noqa: E402
from .validation import (  # noqa: E402
    DataValidator,
    ValidationError,
    ValidationSeverity,
    create_sample_schema,
)
from .workflow_engine import Task, TaskStatus, TaskType, Workflow, WorkflowEngine  # noqa: E402
from .patterns.pattern_matcher import (  # noqa: E402
    PatternHit,
    extract_high_precision_entities,
    get_backend_info,
    get_default_bootstrap_patterns,
    get_pattern_matcher,
    match_text,
    prewarm,
    reset_pattern_matcher,
)


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
