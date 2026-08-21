"""
Universal Package — Minimal Export Surface

Explicit exports only. Use load_optional() for optional module access.

Active parts (all lazy-loaded via __getattr__):
- Config: lazy
- public_fetcher: lazy (httpx/curl_cffi cost at import time)
- pattern_matcher: lazy
- duckdb_store: lazy
- resource/concurrency: lazy

Auto-discovery mechanism (PEP 810 __getattr__):
- Each submodule declares its public API via __all__
- __getattr__ walks the known module paths and imports on first use
- Ghost modules (deleted symbols) are hardcoded exceptions
- No hand-maintained symbol-to-path mapping required

Adding a new public symbol:
1. Add it to the submodule's __all__ (NOT to this file)
2. Add the module to _AUTO_MODULE_PATHS if it's a new submodule
3. Done — no edit needed to this file for symbol additions
"""

import re as _re
import threading
import weakref
from importlib import import_module
from importlib.util import find_spec as _find_spec
from types import ModuleType
from typing import Any

_BOOTSTRAP_LOCK = threading.RLock()  # RLock: reentrant pro případ rekurzivního volání z _bootstrap_* chain
_BOOTSTRAPPED: bool = False


def _ensure_bootstrap() -> None:
    """Lazily bootstrap the hledac namespace — called once on first attribute access.

    Thread-safe double-checked locking:
    1. Fast path: if _BOOTSTRAPPED, return immediately (no lock acquired)
    2. Slow path: acquire lock, re-check, perform bootstrap, set flag
    """
    global _BOOTSTRAPPED

    # Fast path: already bootstrapped (no lock needed)
    if _BOOTSTRAPPED:
        return

    # Slow path: acquire lock and bootstrap
    with _BOOTSTRAP_LOCK:
        # Double-check after acquiring lock
        if _BOOTSTRAPPED:
            return

        try:
            from hledac.universal.hledac._namespace_bootstrap import ensure_namespace_paths

            ensure_namespace_paths()
            _BOOTSTRAPPED = True  # Set ONLY after successful bootstrap
        except ImportError:
            # ImportError propagates — namespace is broken, not silently ignorable
            raise


from _lazy_index import build_module_index

_AUTO_MODULE_PATHS = [
    # Config
    "hledac.universal.config",
    # Pattern matcher
    "hledac.universal.utils.patterns.pattern_matcher",
    # DuckDB store
    "hledac.universal.knowledge.duckdb_store",
    # Graph RAG
    "hledac.universal.knowledge.graph_rag",
    # Public fetcher
    # Transport
    "hledac.universal.transport.transport_resolver",
    # Layers
    "hledac.universal.layers.temporal_signal_runtime",
    # Resource allocator
    "hledac.universal.resource_allocator",
    # Concurrency
    "hledac.universal.utils.concurrency",
    # Utils
    "hledac.universal.utils.action_result",
    "hledac.universal.utils",
    # Sibling re-exports (hledac.core) — bootstrap wired in _namespace_bootstrap._bootstrap_core()
    "hledac.core",
    # MLX embeddings (hledac.core.mlx_embeddings — sibling re-export)
    "hledac.core.mlx_embeddings",
    # Sibling re-exports (hledac.security) — bootstrap wired in _namespace_bootstrap._bootstrap_security()
    "hledac.security",
]

# Explicit whitelist: canonical public API per module.
# This is the single source of truth for what hledac.universal exposes.
# Adding a new symbol = add to this dict (or to submodule __all__ and add module to _AUTO_MODULE_PATHS).
# Keeping it as a static dict preserves the exact API surface from the original _LAZY_EXPORTS.
_EXPLICIT_ATTRS_BY_MODULE: dict[str, frozenset[str]] = {
    # Config — original _LAZY_EXPORTS entries
    "hledac.universal.config": frozenset(
        {
            "UniversalConfig",
            "create_config",
            "load_config_from_file",
        }
    ),
    # Pattern matcher
    "hledac.universal.utils.patterns.pattern_matcher": frozenset(
        {
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
        }
    ),
    # DuckDB store
    "hledac.universal.knowledge.duckdb_store": frozenset(
        {
            "DuckDBShadowStore",
            "ActivationResult",
            "ReplayResult",
            "CanonicalFinding",
            "create_owned_store",
        }
    ),
    # Graph RAG
    "hledac.universal.knowledge.graph_rag": frozenset({"GraphRAGOrchestrator"}),
    # Public fetcher
    "hledac.universal.fetching.public_fetcher": frozenset(
        {
            "async_fetch_public_text",
            "process_html_payload",
            "DEFAULT_UA",
            "MAX_BYTES_DEFAULT",
            "MAX_BYTES_HARD",
            "MAX_RETRIES",
            "FetchResult",
        }
    ),
    # Evidence network analyzer
    "hledac.universal.transport.transport_resolver": frozenset(
        {
            "TransportContext",
            "TransportResolver",
            "Transport",
        }
    ),
    # Layers
    "hledac.universal.layers.temporal_signal_runtime": frozenset(
        {
            "build_temporal_priority_hints",
        }
    ),
    # Resource allocator
    "hledac.universal.resource_allocator": frozenset({"AdaptiveSemaphore"}),
    # Concurrency
    "hledac.universal.utils.concurrency": frozenset(
        {
            "FETCH_SEMAPHORE",
            "adjust_fetch_workers",
        }
    ),
    # Utils
    "hledac.universal.utils.action_result": frozenset({"ActionResult"}),
    "hledac.universal.utils": frozenset({"get_uuid7_compat_status"}),
    # Sibling re-exports (hledac.core) — wired via _namespace_bootstrap._bootstrap_core()
    "hledac.core": frozenset({"AgentExecutionError", "CircuitBreakerOpen", "CircuitBreakerOpenError"}),
    "hledac.core.mlx_embeddings": frozenset(
        {
            "MLXEmbeddingManager",
            "get_embedding_manager",
            "get_mlx_embedder",
        }
    ),
    # Sibling re-exports (hledac.security) — wired via _namespace_bootstrap._bootstrap_security()
    "hledac.security": frozenset(
        {
            "KeyManager",
            "StealthEngine",
            "ThreatIntelligence",
            "QuantumResistantCrypto",
            "ZKPResearchEngine",
            "TemporalAnonymizer",
            "ZeroAttributionEngine",
        }
    ),
}

# Ghost entries: deleted/migrated symbols that must raise ImportError with a helpful msg
_GHOST_ENTRIES: dict[str, str] = {
    "GhostDirector": (
        "GhostDirector was a non-functional compat stub and has been removed (F350M-R A-01). "
        "No replacement — this symbol never existed in any backend."
    ),
    "AgentExecutionError": (
        "AgentExecutionError is at hledac.core.resilience.AgentExecutionError. "
        "Use: from hledac.core.resilience import AgentExecutionError"
    ),
    "CircuitBreakerOpen": (
        "CircuitBreakerOpen was removed (F350M-R A-01). "
        "Use hledac.core.resilience.CircuitBreakerOpenError instead. "
        "From hledac.core.resilience import CircuitBreakerOpenError"
    ),
    "UnifiedAIOrchestrator": (
        "UnifiedAIOrchestrator was a non-functional compat stub and has been removed (F350M-R A-01). No replacement."
    ),
    "Watchdog": ("Watchdog is at hledac.core.watchdog.Watchdog. Use: from hledac.core.watchdog import Watchdog"),
    "fetch_json": (
        "fetch_json was a compat stub removed in F350M-R A-01. "
        "Use hledac.universal.fetching.public_fetcher.async_fetch_public_text directly."
    ),
    "safe_fetch": (
        "safe_fetch was a compat stub removed in F350M-R A-01. "
        "Use hledac.universal.fetching.public_fetcher.async_fetch_public_text directly."
    ),
    "StealthEngine": (
        "StealthEngine is at hledac.security.StealthEngine. Use: from hledac.security import StealthEngine"
    ),
    "ThreatIntelligence": (
        "ThreatIntelligence is at hledac.security.ThreatIntelligence. "
        "Use: from hledac.security import ThreatIntelligence"
    ),
    "QuantumResistantCrypto": (
        "QuantumResistantCrypto is at hledac.security.QuantumResistantCrypto. "
        "Use: from hledac.security import QuantumResistantCrypto"
    ),
    "ZKPResearchEngine": (
        "ZKPResearchEngine is at hledac.security.ZKPResearchEngine. Use: from hledac.security import ZKPResearchEngine"
    ),
    "TemporalAnonymizer": (
        "TemporalAnonymizer is at hledac.security.TemporalAnonymizer. "
        "Use: from hledac.security import TemporalAnonymizer"
    ),
    "ZeroAttributionEngine": (
        "ZeroAttributionEngine is at hledac.security.ZeroAttributionEngine. "
        "Use: from hledac.security import ZeroAttributionEngine"
    ),
}

_ATTRIBUTE_INDEX: dict[str, str] | None = None


def _build_index() -> dict[str, str]:
    """Build the attribute→module index once (paid once, amortised across all imports)."""
    return build_module_index(_AUTO_MODULE_PATHS, _EXPLICIT_ATTRS_BY_MODULE)


_cache: dict[str, Any] = {}
# Sprint-scoped: clear cache between sprints to prevent symbol accumulation.
# weakref.WeakValueDictionary cannot be used here because _cache stores
# both module objects and primitive values (int, str, etc.) — WeakValueDictionary
# only holds weakly-referenced objects and would prematurely evict modules.


def __getattr__(name: str) -> Any:
    """Lazy-load symbols on first access via pre-built attribute index."""
    # Bootstrap namespace lazily on first attribute access
    _ensure_bootstrap()

    # Fast path: already cached
    if name in _cache:
        return _cache[name]

    # __all__ is defined at module level — serve it directly
    if name == "__all__":
        return _cache.setdefault(name, __all__)

    global _ATTRIBUTE_INDEX
    if _ATTRIBUTE_INDEX is None:
        _ATTRIBUTE_INDEX = _build_index()

    # Resolve via index
    mod_path = _ATTRIBUTE_INDEX.get(name)
    if mod_path is not None:
        try:
            mod = import_module(mod_path)
        except ImportError, ModuleNotFoundError:
            # Index was built from stale data — rebuild and retry once
            _ATTRIBUTE_INDEX = _build_index()
            mod_path = _ATTRIBUTE_INDEX.get(name)
            if mod_path is None:
                if name in _GHOST_ENTRIES:
                    raise ImportError(_GHOST_ENTRIES[name])
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
            mod = import_module(mod_path)

        val = getattr(mod, name)
        _cache[name] = val
        return val

    # Not in index — ghost or truly absent
    if name in _GHOST_ENTRIES:
        raise ImportError(_GHOST_ENTRIES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def clear_cache() -> None:
    """Clear the symbol cache — called during sprint winddown.

    F350M-R G7: Clears module-level _cache to prevent memory growth
    during multi-sprint sessions. After clear, symbols are re-resolved
    on next access via __getattr__.
    """
    _cache.clear()


_IDENTIFIER_RE = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def load_optional(name: str) -> ModuleType:
    """Load an optional submodule by name.

    Args:
        name: Full module name relative to hledac.universal,
              e.g. 'coordinators' or 'layers'
    Returns:
        The imported module.
    Raises:
        ImportError: If the module cannot be imported or name is invalid.
    """
    if not _IDENTIFIER_RE.match(name):
        raise ImportError(f"Invalid module name: {name!r}")
    if not _find_spec(name, package="hledac.universal"):
        raise ImportError(f"Module spec not found: {name!r}")
    return import_module(name, package="hledac.universal")


# __all__ = union of all explicit attrs + load_optional
_all_names: set[str] = set()
for explicit in _EXPLICIT_ATTRS_BY_MODULE.values():
    _all_names.update(explicit)

__all__ = sorted(_all_names | {"load_optional", "clear_cache"})
