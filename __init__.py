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

from importlib import import_module
from importlib.util import find_spec as _find_spec
from types import ModuleType
from typing import Any
import re as _re

# -----------------------------------------------------------------------------
# Lazy namespace bootstrap (runs once on first __getattr__ call, not at import)
# -----------------------------------------------------------------------------
_BOOTSTRAPPED: bool = False


def _ensure_bootstrap() -> None:
    """Lazily bootstrap the hledac namespace — called once on first attribute access."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    try:
        from hledac._namespace_bootstrap import ensure_namespace_paths

        ensure_namespace_paths()
    except (ImportError, Exception):  # noqa: BLE001
        pass  # noqa: BLE001  # fail-soft guard

# -----------------------------------------------------------------------------
# Auto-discovery configuration
# -----------------------------------------------------------------------------
# Ordered module paths for __getattr__ lookup.
# Earlier entries take priority on name collisions.
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
    "hledac.universal.fetching.public_fetcher",
    # Evidence network analyzer
    "hledac.universal.advanced_web.evidence_network_analyzer",
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
    # Sibling re-exports (hledac.core) — compat shims, no __all__
    "hledac.universal.compat.core_resilience",
    "hledac.universal.compat.core_http",
    "hledac.universal.compat.core_unified_ai_orchestrator",
    "hledac.universal.compat.core_watchdog",
    # MLX embeddings (local universal/core/mlx_embeddings.py)
    "hledac.universal.core.mlx_embeddings",
    # Sibling re-exports (hledac.security) — compat shims, no __all__
    "hledac.universal.compat.security_stealth_engine",
    "hledac.universal.compat.security_threat_intelligence",
    "hledac.universal.compat.security_quantum_resistant_crypto",
    "hledac.universal.compat.security_zkp_research_engine",
    "hledac.universal.compat.security_temporal_anonymizer",
    "hledac.universal.compat.security_zero_attribution_engine",
    # KeyManager: local security/key_manager.py
    "hledac.universal.security.key_manager",
    # Sibling re-exports (hledac.cortex) — compat shim, no __all__
    "hledac.universal.compat.cortex_director",
]

# Explicit whitelist: canonical public API per module.
# This is the single source of truth for what hledac.universal exposes.
# Adding a new symbol = add to this dict (or to submodule __all__ and add module to _AUTO_MODULE_PATHS).
# Keeping it as a static dict preserves the exact API surface from the original _LAZY_EXPORTS.
_EXPLICIT_ATTRS_BY_MODULE: dict[str, frozenset[str]] = {
    # Config — original _LAZY_EXPORTS entries
    "hledac.universal.config": frozenset({
        "UniversalConfig", "create_config", "load_config_from_file",
    }),
    # Pattern matcher
    "hledac.universal.utils.patterns.pattern_matcher": frozenset({
        "PatternHit", "ExtractedEntity", "get_pattern_pack_metadata",
        "extract_high_precision_entities", "get_pattern_matcher", "configure_patterns",
        "match_text", "reset_pattern_matcher", "get_default_bootstrap_patterns",
        "configure_default_bootstrap_patterns_if_empty", "benchmark_build", "benchmark_match",
    }),
    # DuckDB store
    "hledac.universal.knowledge.duckdb_store": frozenset({
        "DuckDBShadowStore", "ActivationResult", "ReplayResult",
        "CanonicalFinding", "create_owned_store",
    }),
    # Graph RAG
    "hledac.universal.knowledge.graph_rag": frozenset({"GraphRAGOrchestrator"}),
    # Public fetcher
    "hledac.universal.fetching.public_fetcher": frozenset({
        "async_fetch_public_text", "process_html_payload", "DEFAULT_UA",
        "MAX_BYTES_DEFAULT", "MAX_BYTES_HARD", "MAX_RETRIES", "FetchResult",
    }),
    # Evidence network analyzer
    "hledac.universal.advanced_web.evidence_network_analyzer": frozenset({
        "EvidenceNetworkAnalyzer", "EvidenceGraphNode",
        "EvidenceGraphEdge", "EvidenceGraph",
    }),
    # Transport
    "hledac.universal.transport.transport_resolver": frozenset({
        "TransportContext", "TransportResolver", "Transport",
    }),
    # Layers
    "hledac.universal.layers.temporal_signal_runtime": frozenset({
        "build_temporal_priority_hints",
    }),
    # Resource allocator
    "hledac.universal.resource_allocator": frozenset({"AdaptiveSemaphore"}),
    # Concurrency
    "hledac.universal.utils.concurrency": frozenset({
        "FETCH_SEMAPHORE", "adjust_fetch_workers",
    }),
    # Utils
    "hledac.universal.utils.action_result": frozenset({"ActionResult"}),
    "hledac.universal.utils": frozenset({"get_uuid7_compat_status"}),
    # Sibling re-exports (hledac.core) — compat shims, no __all__
    "hledac.universal.compat.core_resilience": frozenset({"AgentExecutionError", "CircuitBreakerOpen"}),
    "hledac.universal.compat.core_http": frozenset({"fetch_json", "safe_fetch"}),
    "hledac.universal.compat.core_unified_ai_orchestrator": frozenset({"UnifiedAIOrchestrator"}),
    "hledac.universal.compat.core_watchdog": frozenset({"Watchdog"}),
    # MLX embeddings
    "hledac.universal.core.mlx_embeddings": frozenset({
        "MLXEmbeddingManager", "get_embedding_manager", "get_mlx_embedder",
    }),
    # Sibling re-exports (hledac.security) — compat shims
    "hledac.universal.compat.security_stealth_engine": frozenset({"StealthEngine"}),
    "hledac.universal.compat.security_threat_intelligence": frozenset({"ThreatIntelligence"}),
    "hledac.universal.compat.security_quantum_resistant_crypto": frozenset({"QuantumResistantCrypto"}),
    "hledac.universal.compat.security_zkp_research_engine": frozenset({"ZKPResearchEngine"}),
    "hledac.universal.compat.security_temporal_anonymizer": frozenset({"TemporalAnonymizer"}),
    "hledac.universal.compat.security_zero_attribution_engine": frozenset({"ZeroAttributionEngine"}),
    # KeyManager: local
    "hledac.universal.security.key_manager": frozenset({"KeyManager"}),
    # Sibling re-exports (hledac.cortex)
    "hledac.universal.compat.cortex_director": frozenset({"GhostDirector"}),
}

# Ghost entries: deleted symbols that must raise ImportError with a helpful msg
_GHOST_ENTRIES: dict[str, str] = {
    "FullyAutonomousOrchestrator": "FullyAutonomousOrchestrator was removed. Use UniversalConfig + SprintScheduler.",
    "MARLCoordinator": "MARLCoordinator was deleted in a prior sprint. Search the git history.",
    "PressureLevel": "PressureLevel was deleted in a prior sprint. Search the git history.",
    "ParallelExecutionOptimizer": "ParallelExecutionOptimizer was deleted in a prior sprint. Search the git history.",
    "RayClusterManager": "RayClusterManager was deleted in a prior sprint. Search the git history.",
    "LanguageDetector": "LanguageDetector was deleted in a prior sprint. Search the git history.",
    "SemanticFilter": "SemanticFilter was deleted in a prior sprint. Search the git history.",
}

# -----------------------------------------------------------------------------
# Lazy attribute index — built once on first __getattr__ access
# Maps: attribute name → module path  (e.g. "DuckDBShadowStore" → "hledac.universal.knowledge.duckdb_store")
# Pre-populated from _EXPLICIT_ATTRS_BY_MODULE (authoritative whitelist).
# Modules without explicit entries contribute via __all__ scan at index build time.
# -----------------------------------------------------------------------------
_ATTRIBUTE_INDEX: dict[str, str] | None = None


def _build_index() -> dict[str, str]:
    """Build the attribute→module index once (paid once, amortised across all imports)."""
    idx: dict[str, str] = {}

    # 1. Populate from explicit whitelists — authoritative, zero extra imports
    for mod_path, explicit in _EXPLICIT_ATTRS_BY_MODULE.items():
        for name in explicit:
            idx.setdefault(name, mod_path)  # first wins (preserves _AUTO_MODULE_PATHS priority)

    # 2. Scan modules without explicit entries: contribute their __all__ (or public module attrs)
    # These are the few modules in _AUTO_MODULE_PATHS not covered by _EXPLICIT_ATTRS_BY_MODULE.
    # We import them lazily here — one-time cost, paid once at first __getattr__.
    for mod_path in _AUTO_MODULE_PATHS:
        if mod_path in _EXPLICIT_ATTRS_BY_MODULE:
            continue  # already covered by step 1
        try:
            mod = import_module(mod_path)
        except (ImportError, ModuleNotFoundError):
            continue
        explicit = _EXPLICIT_ATTRS_BY_MODULE.get(mod_path)
        if explicit is not None:
            for name in explicit:
                idx.setdefault(name, mod_path)
        else:
            # __all__-based module
            all_list: list[str] | None = getattr(mod, "__all__", None)
            if all_list is not None:
                for name in all_list:
                    if not name.startswith("_"):
                        idx.setdefault(name, mod_path)
            else:
                # Last resort: public attrs of the module itself (non-dunder, non-underscore)
                for name in dir(mod):
                    if not name.startswith("_"):
                        idx.setdefault(name, mod_path)

    return idx


# -----------------------------------------------------------------------------
# Runtime __getattr__ with index-accelerated lookup
# -----------------------------------------------------------------------------
_cache: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """Lazy-load symbols on first access via pre-built attribute index."""
    # Bootstrap namespace lazily on first attribute access
    _ensure_bootstrap()

    # Fast path: already cached
    if name in _cache:
        return _cache[name]

    # Build index once on first miss
    global _ATTRIBUTE_INDEX
    if _ATTRIBUTE_INDEX is None:
        _ATTRIBUTE_INDEX = _build_index()

    # Resolve via index
    mod_path = _ATTRIBUTE_INDEX.get(name)
    if mod_path is not None:
        try:
            mod = import_module(mod_path)
        except (ImportError, ModuleNotFoundError):
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


# -----------------------------------------------------------------------------
# load_optional: safe loader for arbitrary submodules
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# Public __all__ — union of all discoverable + ghost + special symbols
# Built dynamically so the list NEVER goes stale relative to module __all__.
# -----------------------------------------------------------------------------

# FullyAutonomousOrchestrator was removed from code but kept in __all__
# for back-compat warning if anyone tries to import it
_ghosts_and_special: frozenset[str] = frozenset({
    "FullyAutonomousOrchestrator",  # removed; kept for back-compat
    "MARLCoordinator",
    "PressureLevel",
    "ParallelExecutionOptimizer",
    "RayClusterManager",
    "LanguageDetector",
    "SemanticFilter",
})

# __all__ = union of all explicit attrs + ghosts + load_optional
_all_names: set[str] = set()
for explicit in _EXPLICIT_ATTRS_BY_MODULE.values():
    _all_names.update(explicit)

__all__ = sorted(_all_names | _ghosts_and_special | {"load_optional"})
