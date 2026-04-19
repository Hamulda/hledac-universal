"""
Universal Package — Minimal Export Surface
=========================================

Explicit exports only. Use load_optional() for optional module access.

Active parts:
- Config: from .config
- public_fetcher: from .fetching.public_fetcher
- pattern_matcher: from .patterns.pattern_matcher
- duckdb_store: from .knowledge.duckdb_store
"""

from importlib import import_module

# Config
from .config import UniversalConfig, create_config, load_config_from_file

# Public fetcher
from .fetching.public_fetcher import (
    async_fetch_public_text,
    process_html_payload,
    DEFAULT_UA,
    MAX_BYTES_DEFAULT,
    MAX_BYTES_HARD,
    MAX_RETRIES,
    FetchResult,
)

# Pattern matcher
from .patterns.pattern_matcher import (
    PatternHit,
    ExtractedEntity,
    get_pattern_pack_metadata,
    extract_high_precision_entities,
    get_pattern_matcher,
    configure_patterns,
    match_text,
    reset_pattern_matcher,
    get_default_bootstrap_patterns,
    configure_default_bootstrap_patterns_if_empty,
    benchmark_build,
    benchmark_match,
)

# DuckDB store
from .knowledge.duckdb_store import (
    DuckDBShadowStore,
    ActivationResult,
    ReplayResult,
    CanonicalFinding,
    create_owned_store,
)


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
    # Loader
    "load_optional",
]
