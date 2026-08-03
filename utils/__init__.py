import sys as _sys  # noqa: E402 — Phase 0 alias before any other imports

# Phase 0 alias: register `utils` as a top-level module so absolute
# `from utils.X` imports resolve regardless of how the package is launched
# (python -m hledac.universal, IDE, or direct script). See __main__.py
_sys.modules.setdefault("utils", _sys.modules[__name__])

"""
Utility funkce pro UniversalResearchOrchestrator.

PEP 562 __getattr__ lazy loading — submodules se načítají až při prvním
použití, ne při importu balíčku. Eliminuje ~3300ms overhead při importu
runtime.sprint_scheduler.

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
- IntelligentCache: Chytrý cache s LRU/LFU/ADADAPTIVE eviction
"""

from typing import TYPE_CHECKING

# Lazy submodule registry — maps name → (module, names to expose)
# Loaded on demand to eliminate ~3300ms import overhead for callers that only
# need one submodule (e.g. sprint_scheduler → async_helpers).
_SUBMODULE_REGISTRY: dict[str, tuple[str, tuple[str, ...]]] = {
    "action_result": (".action_result", ("ActionResult",)),
    "async_utils": (".async_utils", ("TaskResult", "bounded_map", "map_as_completed")),
    "bloom_filter": (".bloom_filter", ("BloomFilter", "BloomFilterStats", "create_content_fingerprint", "create_url_deduplicator")),
    "deduplication": (".deduplication", ("ContentDeduplicator", "DeduplicationConfig", "DeduplicationEngine", "DeduplicationMatch", "DeduplicationResult", "DeduplicationStats", "DeduplicationStrategy", "MetadataDeduplicator", "QueryItem", "SemanticDeduplicator", "SimilarityScore")),
    "encryption": (".encryption", ("DataEncryption", "DecryptionResult", "EncryptionResult")),
    "entity_extractor": (".entity_extractor", ("EntityExtractor", "ExtractedEntity", "PatternType")),
    "execution_optimizer": (".execution_optimizer", ("AnomalyDetector", "ExecutionStrategy", "IntelligentResourceAllocator", "OptimizationLevel", "ParallelExecutionOptimizer", "PredictiveScaler", "ResourceLimits", "ResourceMetrics", "ResourceType", "TaskMetrics", "TaskType", "WorkerMetrics", "create_m1_resource_allocator")),
    "filtering": (".filtering", ("EfficientFrontier", "FastFilter", "FilterStats", "FrontierStats", "get_fast_filter", "get_frontier")),
    "intelligent_cache": (".intelligent_cache", ("CacheConfig", "CacheEntry", "CacheStats", "EvictionStrategy", "IntelligentCache", "MemoryOptimizedURLSet", "get_global_cache")),
    "language": (".language", ("LanguageDetector", "create_language_detector")),
    "lazy_imports": (".lazy_imports", ("LazyImportManager", "LazyLoader", "lazy_import")),
    "patterns_pattern_matcher": (".patterns.pattern_matcher", ("PatternHit", "extract_high_precision_entities", "get_backend_info", "get_default_bootstrap_patterns", "get_pattern_matcher", "match_text", "prewarm", "reset_pattern_matcher")),
    "performance_monitor": (".performance_monitor", ("PerformanceMetrics", "PerformanceMonitor", "QualityValidator")),
    "predictive_planner": (".predictive_planner", ("Prediction", "PredictivePlanner", "RollbackManager")),
    "query_expansion": (".query_expansion", ("DomainSpecificExpansionStrategy", "ExpansionConfig", "ExpansionStrategy", "MultiStrategyExpander", "QueryExpander", "QueryVariation", "SemanticExpansionStrategy", "SyntacticExpansionStrategy", "expand_query")),
    "ranking": (".ranking", ("RankedResult", "ReciprocalRankFusion", "RRFConfig", "ScoreAggregator", "fuse_results")),
    "rate_limiter": (".rate_limiter", ("RateLimitConfig", "RateLimiter", "RateLimitExceeded", "with_rate_limit")),
    "rayon_channel": (".rayon_channel", ("dispatch_cpu", "dispatch_io", "dispatch_mixed", "dispatch_rayon", "dispatch_cpu_batch", "dispatch_mixed_batch")),
    "rayon_hash": (".rayon_hash", ("simhash_single", "simhash_batch", "quality_gate_assess", "blake3_hash_batch", "xxhash_batch", "normalize_text_batch", "compute_fingerprints")),
    "ioc_extract": (".ioc_extract", ("extract_iocs_batch", "extract_iocs_single", "extract_iocs_from_findings")),
    "subinterpreter_pool": (".subinterpreter_pool", ("run_in_subinterpreter", "run_batch_in_subinterpreters", "shutdown_pool")),
    "robots_parser": (".robots_parser", ("RobotsDocument", "RobotsParser", "Rule")),
    "semantic": (".semantic", ("FilterResult", "KeywordFilter", "LightweightTokenizer", "Model2VecEmbedding", "SemanticFilter", "SimpleEmbedding")),
    "tech_detection": (".tech_detection", ("TechStackResult", "TechStackSignature")),
    "validation": (".validation", ("DataValidator", "ValidationError", "ValidationSeverity", "create_sample_schema")),
    "config_introspection": (".config_introspection", ("safe_attr_get",)),
    "workflow_engine": (".workflow_engine", ("Task", "TaskStatus", "TaskType", "Workflow", "WorkflowEngine")),
}

# Cache for loaded submodules — prevents re-import
_LOADED_SUBMODULES: dict[str, object] = {}

# Lightweight submodules — imported eagerly (sub-ms, no heavy deps)
# Only needed here for static analysis tools (pyright, IDE autocomplete)
if TYPE_CHECKING:
    from .action_result import ActionResult
    from .async_utils import TaskResult, bounded_map, map_as_completed
    from .bloom_filter import BloomFilter, BloomFilterStats, create_content_fingerprint, create_url_deduplicator
    from .deduplication import ContentDeduplicator, DeduplicationConfig, DeduplicationEngine, DeduplicationMatch, DeduplicationResult, DeduplicationStats, DeduplicationStrategy, MetadataDeduplicator, QueryItem, SemanticDeduplicator, SimilarityScore
    from .encryption import DataEncryption, DecryptionResult, EncryptionResult
    from .entity_extractor import EntityExtractor, ExtractedEntity, PatternType
    from .execution_optimizer import AnomalyDetector, ExecutionStrategy, IntelligentResourceAllocator, OptimizationLevel, ParallelExecutionOptimizer, PredictiveScaler, ResourceLimits, ResourceMetrics, ResourceType, TaskMetrics, TaskType, WorkerMetrics, create_m1_resource_allocator
    from .filtering import EfficientFrontier, FastFilter, FilterStats, FrontierStats, get_fast_filter, get_frontier
    from .intelligent_cache import CacheConfig, CacheEntry, CacheStats, EvictionStrategy, IntelligentCache, MemoryOptimizedURLSet, get_global_cache
    from .language import LanguageDetector, create_language_detector
    from .lazy_imports import LazyImportManager, LazyLoader, lazy_import
    from .patterns.pattern_matcher import PatternHit, extract_high_precision_entities, get_backend_info, get_default_bootstrap_patterns, get_pattern_matcher, match_text, prewarm, reset_pattern_matcher
    from .performance_monitor import PerformanceMetrics, PerformanceMonitor, QualityValidator
    from .predictive_planner import Prediction, PredictivePlanner, RollbackManager
    from .query_expansion import DomainSpecificExpansionStrategy, ExpansionConfig, ExpansionStrategy, MultiStrategyExpander, QueryExpander, QueryVariation, SemanticExpansionStrategy, SyntacticExpansionStrategy, expand_query
    from .ranking import RankedResult, ReciprocalRankFusion, RRFConfig, ScoreAggregator, fuse_results
    from .rate_limiter import RateLimitConfig, RateLimiter, RateLimitExceeded, with_rate_limit
    from .robots_parser import RobotsDocument, RobotsParser, Rule
    from .semantic import FilterResult, KeywordFilter, LightweightTokenizer, Model2VecEmbedding, SemanticFilter, SimpleEmbedding
    from .tech_detection import TechStackResult, TechStackSignature
    from .validation import DataValidator, ValidationError, ValidationSeverity, create_sample_schema
    from .config_introspection import safe_attr_get
    from .workflow_engine import Task, TaskStatus, TaskType, Workflow, WorkflowEngine


def __getattr__(name: str):
    """
    PEP 562 — Lazy submodule loading.

    Handles two cases:
      1. Submodule access: utils.async_helpers → loads .async_utils
      2. Attribute access: from utils import BloomFilter → loads .bloom_filter
         and returns the named export from that submodule

    Caches loaded submodules in _LOADED_SUBMODULES to avoid re-import.

    Args:
        name: Submodule name (e.g. 'async_helpers') or exported name
              (e.g. 'BloomFilter', 'PerformanceMonitor')

    Returns:
        Loaded submodule object OR the named export from a submodule.

    Raises:
        AttributeError: If name not in registry.
    """
    # Case 1: Submodule already loaded
    if name in _LOADED_SUBMODULES:
        return _LOADED_SUBMODULES[name]

    # Case 2: Submodule in registry (e.g. 'async_helpers')
    if name in _SUBMODULE_REGISTRY:
        rel_path, _ = _SUBMODULE_REGISTRY[name]
        from importlib import import_module
        mod = import_module(rel_path, package=__name__)
        _LOADED_SUBMODULES[name] = mod
        return mod

    # Case 3: Find which submodule exports this name and load it
    # Scan registry to find the submodule that provides `name`
    for rel_path, names in _SUBMODULE_REGISTRY.values():
        if name in names:
            from importlib import import_module
            mod = import_module(rel_path, package=__name__)
            _LOADED_SUBMODULES[rel_path.lstrip(".")] = mod
            return getattr(mod, name)

    # Unknown name — raise AttributeError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """
    PEP 562 — Directory of public names for tab-completion.

    Returns all submodule names + utility functions.
    """
    return list(__all__) + list(_SUBMODULE_REGISTRY)


# ── Utility functions (eager, sub-ms) ──────────────────────────────────────────

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


async def run_cmd(cmd: list[str], timeout: float = 15.0) -> str:
    """
    Run a subprocess command asynchronously via asyncio.create_subprocess_exec.

    Args:
        cmd: Command list (e.g. ['curl', '-s', 'https://example.com']).
        timeout: Maximum seconds to wait (default 15.0).

    Returns:
        stdout as string, or empty string on failure/timeout.

    M1 8GB note: subprocess runs in ThreadPool, never blocks the event loop.
    """
    import asyncio
    import subprocess
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(timeout):
                stdout, _ = await process.communicate()
        except TimeoutError:
            try:
                process.terminate()
                try:
                    async with asyncio.timeout(2.0):
                        await process.wait()
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except Exception:  # noqa: BLE001
                pass
            return ""
        if process.returncode == 0 and stdout:
            return stdout.decode("utf-8", errors="replace")
        return ""
    except Exception:  # noqa: BLE001
        return ""


# ── __all__ — static list for static analysis tools (pyright, IDE) ─────────────
# NOTE: This is a superset of all possible exports. Dynamic access is via __getattr__.

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
    # Config introspection
    "safe_attr_get",
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
    # bounded_gather intentionally omitted — canonical is async_helpers.bounded_gather
    "bounded_map",
    "map_as_completed",
    "TaskResult",
    # UUID7 compat shim (F208N-D)
    "uuid7",
    "get_uuid7_compat_status",
    # Subprocess runner (stealth_crawler, etc.)
    "run_cmd",
    # Lazy-loaded submodules (accessible via __getattr__)
    # For IDE autocomplete of lazy submodules, use:
    #   from hledac.universal.utils.async_helpers import safe_create_task
    # which triggers __getattr__('async_helpers') lazily
    "_SUBMODULE_REGISTRY",
    "_LOADED_SUBMODULES",
]
