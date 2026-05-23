"""
Universal Package — Minimal Export Surface
=========================================

Explicit exports only. Use load_optional() for optional module access.

Active parts (all lazy-loaded via __getattr__):
- Config: lazy
- public_fetcher: lazy (aiohttp cost at import time)
- pattern_matcher: lazy
- duckdb_store: lazy
- resource/concurrency: lazy
"""

from importlib import import_module

# Lazy export map — defers all heavy module imports to first-use
# F214-PERF: eliminates ~48ms of eager import cost at boot
_LAZY_EXPORTS = {
    # Config
    "UniversalConfig": "hledac.universal.config",
    "create_config": "hledac.universal.config",
    "load_config_from_file": "hledac.universal.config",

    # Pattern matcher
    "PatternHit": "hledac.universal.patterns.pattern_matcher",
    "ExtractedEntity": "hledac.universal.patterns.pattern_matcher",
    "get_pattern_pack_metadata": "hledac.universal.patterns.pattern_matcher",
    "extract_high_precision_entities": "hledac.universal.patterns.pattern_matcher",
    "get_pattern_matcher": "hledac.universal.patterns.pattern_matcher",
    "configure_patterns": "hledac.universal.patterns.pattern_matcher",
    "match_text": "hledac.universal.patterns.pattern_matcher",
    "reset_pattern_matcher": "hledac.universal.patterns.pattern_matcher",
    "get_default_bootstrap_patterns": "hledac.universal.patterns.pattern_matcher",
    "configure_default_bootstrap_patterns_if_empty": "hledac.universal.patterns.pattern_matcher",
    "benchmark_build": "hledac.universal.patterns.pattern_matcher",
    "benchmark_match": "hledac.universal.patterns.pattern_matcher",

    # DuckDB store
    "DuckDBShadowStore": "hledac.universal.knowledge.duckdb_store",
    "ActivationResult": "hledac.universal.knowledge.duckdb_store",
    "ReplayResult": "hledac.universal.knowledge.duckdb_store",
    "CanonicalFinding": "hledac.universal.knowledge.duckdb_store",
    "create_owned_store": "hledac.universal.knowledge.duckdb_store",

    # Resource allocator
    "AdaptiveSemaphore": "hledac.universal.resource_allocator",

    # Concurrency utilities
    "FETCH_SEMAPHORE": "hledac.universal.utils.concurrency",
    "adjust_fetch_workers": "hledac.universal.utils.concurrency",
    # Utils
    "ActionResult": "hledac.universal.utils.action_result",
    "get_uuid7_compat_status": "hledac.universal.utils",
    # Transport
    "TransportContext": "hledac.universal.transport.transport_resolver",
    "TransportResolver": "hledac.universal.transport.transport_resolver",
    # Layers
    "build_temporal_priority_hints": "hledac.universal.layers.temporal_signal_runtime",
    # D ghost modules (fail fast with helpful msg)
    "MARLCoordinator": "_ghost_deleted",
    "PressureLevel": "_ghost_deleted",

    # === SIBLING PACKAGE RE-EXPORTS (via _shims to avoid cross-dep chain) ===
    # hledac.core (sibling pkg — re-export via local shims)
    "AgentExecutionError": "hledac.universal._shims.core_resilience",
    "CircuitBreakerOpen": "hledac.universal._shims.core_resilience",
    "fetch_json": "hledac.universal._shims.core_http",
    "safe_fetch": "hledac.universal._shims.core_http",
    "UnifiedAIOrchestrator": "hledac.universal._shims.core_unified_ai_orchestrator",
    "Watchdog": "hledac.universal._shims.core_watchdog",
    # mlx_embeddings: local universal/core/mlx_embeddings.py wraps sibling;
    # import from universal resolves to local, sibling import fails gracefully
    "MLXEmbeddingManager": "hledac.universal.core.mlx_embeddings",
    "get_embedding_manager": "hledac.universal.core.mlx_embeddings",

    # hledac.security (sibling pkg — re-export via local shims)
    "StealthEngine": "hledac.universal._shims.security_stealth_engine",
    "ThreatIntelligence": "hledac.universal._shims.security_threat_intelligence",
    "QuantumResistantCrypto": "hledac.universal._shims.security_quantum_resistant_crypto",
    "ZKPResearchEngine": "hledac.universal._shims.security_zkp_research_engine",
    "TemporalAnonymizer": "hledac.universal._shims.security_temporal_anonymizer",
    "ZeroAttributionEngine": "hledac.universal._shims.security_zero_attribution_engine",
    # KeyManager: exists locally in universal/security/key_manager.py
    "KeyManager": "hledac.universal.security.key_manager",

    # hledac.cortex (sibling pkg — re-export via local shim)
    "GhostDirector": "hledac.universal._shims.cortex_director",

    # hledac.tools.preserved_logic.* (sibling pkg — ghost stubs for non-existent modules)
    "ParallelExecutionOptimizer": "_ghost_deleted",
    "RayClusterManager": "_ghost_deleted",
    "LanguageDetector": "_ghost_deleted",
    "SemanticFilter": "_ghost_deleted",
    # === END SIBLING RE-EXPORTS ===

    # Public fetcher
    "async_fetch_public_text": "hledac.universal.fetching.public_fetcher",
    "process_html_payload": "hledac.universal.fetching.public_fetcher",
    "DEFAULT_UA": "hledac.universal.fetching.public_fetcher",
    "MAX_BYTES_DEFAULT": "hledac.universal.fetching.public_fetcher",
    "MAX_BYTES_HARD": "hledac.universal.fetching.public_fetcher",
    "MAX_RETRIES": "hledac.universal.fetching.public_fetcher",
    "FetchResult": "hledac.universal.fetching.public_fetcher",
}


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if module_name == "_ghost_deleted":
        raise ImportError(
            f"{name!r} was deleted in a prior sprint. "
            "Search the git history for the last commit that had it, "
            "or remove the import."
        )

    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == "hledac" and module_name.startswith("hledac.universal."):
            local_path = module_name[len("hledac.universal."):]
            module = import_module(local_path)
        else:
            raise

    value = getattr(module, name)
    globals()[name] = value
    return value


def load_optional(name: str):
    """Load an optional module by name.

    Args:
        name: Full module name relative to hledac.universal, e.g. 'coordinators' or 'layers'

    Returns:
        The imported module.

    Raises:
        ImportError: If the module cannot be imported.
    """
    return import_module(name, package="hledac.universal")


__all__ = [
    # Config
    "UniversalConfig",
    "create_config",
    "load_config_from_file",
    # Public fetcher
    "async_fetch_public_text",
    "process_html_payload",
    "DEFAULT_UA",
    "MAX_BYTES_DEFAULT",
    "MAX_BYTES_HARD",
    "MAX_RETRIES",
    "FetchResult",
    # Pattern matcher
    "PatternHit",
    "ExtractedEntity",
    "get_pattern_pack_metadata",
    "extract_high_precision_entities",
    "get_pattern_matcher",
    "configure_patterns",
    "match_text",
    "reset_pattern_matcher",
    "get_default_bootstrap_patterns",
    "configure_default_bootstrap_patterns_if_empty",
    "benchmark_build",
    "benchmark_match",
    # DuckDB store
    "DuckDBShadowStore",
    "ActivationResult",
    "ReplayResult",
    "CanonicalFinding",
    "create_owned_store",
    # Concurrency
    "FETCH_SEMAPHORE",
    "adjust_fetch_workers",
    # Utils
    "ActionResult",
    "get_uuid7_compat_status",
    # Transport
    "TransportContext",
    "TransportResolver",
    # Layers
    "build_temporal_priority_hints",
    # Deleted ghost modules (fail fast with helpful message)
    "MARLCoordinator",
    "PressureLevel",
    # Sibling re-exports (hledac.core)
    "AgentExecutionError",
    "CircuitBreakerOpen",
    "fetch_json",
    "safe_fetch",
    "UnifiedAIOrchestrator",
    "Watchdog",
    "MLXEmbeddingManager",
    "get_embedding_manager",
    # Sibling re-exports (hledac.security)
    "StealthEngine",
    "ThreatIntelligence",
    "QuantumResistantCrypto",
    "ZKPResearchEngine",
    "TemporalAnonymizer",
    "ZeroAttributionEngine",
    "KeyManager",
    # Sibling re-exports (hledac.cortex)
    "GhostDirector",
    # Ghost stubs (hledac.tools.preserved_logic.*)
    "ParallelExecutionOptimizer",
    "RayClusterManager",
    "LanguageDetector",
    "SemanticFilter",
    # Resource allocator
    "AdaptiveSemaphore",
    # Loader
    "load_optional",
]