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
    # RL (deleted ghost module — stub to fail fast if imported)
    "MARLCoordinator": "_ghost_deleted",
    # Runtime (deleted ghost module)
    "PressureLevel": "_ghost_deleted",

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
    # Resource allocator
    "AdaptiveSemaphore",
    # Loader
    "load_optional",
]