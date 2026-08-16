"""
Knowledge komponenty pro UniversalResearchOrchestrator.

Obsahuje:

- KnowledgeGraphLayer: KuzuDB-based persistent knowledge graph (KuzuDB)
- AtomicJSONKnowledgeGraph: RAM-efficient JSON storage (bez DB závislostí)
- ContextGraph: Simple in-memory context graph
- RAGEngine: Ultra Context + SPR Compression
- PersistentKnowledgeLayer: KuzuDB + Model2Vec for semantic search
- GraphRAGOrchestrator: Multi-hop reasoning over knowledge graph
- KnowledgeGraphBuilder: Regex-based fact extraction

Lazy facade — heavy modules (duckdb, numpy, mlx, aiohttp, igraph, psutil)
are NOT imported at module load time. Access any export and the actual
module is loaded on demand. This dramatically reduces `import knowledge`
first-access cost.
"""


from importlib import import_module
from typing import Any

# Maps public export name → module path (used by __getattr__)
_LAZY_EXPORT_MAP: dict[str, str] = {
    # duckdb_store — heavy: duckdb, psutil, msgspec
    "DuckDBShadowStore": "knowledge.duckdb_store",
    "ActivationResult": "knowledge.duckdb_store",
    "DuckDBArrowBuilder": "knowledge.duckdb_arrow_builder",
    "ArrowBuildConfig": "knowledge.duckdb_arrow_builder",
    "ReplayResult": "knowledge.duckdb_store",
    "CanonicalFinding": "knowledge.duckdb_store",
    "create_owned_store": "knowledge.duckdb_store",
    # [META]-005: TimeSeriesSplicer — unified millisecond-aligned timeline
    "TimeSeriesSplicer": "knowledge.time_series_splicer",
    "TimelineEvent": "knowledge.time_series_splicer",
    "get_time_series_splicer": "knowledge.time_series_splicer",
    "CtLogAdapter": "knowledge.time_series_splicer",
    "GitCommitAdapter": "knowledge.time_series_splicer",
    "TelegramAdapter": "knowledge.time_series_splicer",
    "BlockchainAdapter": "knowledge.time_series_splicer",
    "HttpAdapter": "knowledge.time_series_splicer",
    "WarcAdapter": "knowledge.time_series_splicer",
    "PassiveDnsAdapter": "knowledge.time_series_splicer",
    "to_timestamp_ns": "knowledge.time_series_splicer",
    "from_timestamp_ns": "knowledge.time_series_splicer",
    # graph_attachment — lightweight (no heavy deps)
    "GraphAttachmentStore": "knowledge.graph_attachment",

    # rag_engine — heavy: numpy (3x), hledac.universal._core.mlx_embeddings, duckdb
    "RAGEngine": "knowledge.rag_engine",
    "RAGConfig": "knowledge.rag_engine",
    "Document": "knowledge.rag_engine",
    "RetrievedChunk": "knowledge.rag_engine",
    "BM25Index": "knowledge.rag_engine",
    "HNSWVectorIndex": "knowledge.rag_engine",
    # graph_rag — heavy: numpy (2x), hledac.universal._core.mlx_embeddings, duckdb
    "GraphRAGOrchestrator": "knowledge.graph_rag",
    "CentralityScores": "knowledge.graph_rag",
    "Community": "knowledge.graph_rag",
    "GraphContradiction": "knowledge.graph_rag",
    # graph_builder — lightweight (no heavy deps), kept eager
    "KnowledgeGraphBuilder": "knowledge.graph_builder",
    # entity_linker — heavy: aiohttp
    "EntityLinker": "knowledge.entity_linker",
    "EntityCandidate": "knowledge.entity_linker",
    "LinkedEntity": "knowledge.entity_linker",
    "SimpleCache": "knowledge.entity_linker",
    "link_entities": "knowledge.entity_linker",
    "resolve_entity": "knowledge.entity_linker",
    "get_linker": "knowledge.entity_linker",
    # ISSUE-001 Phase 2: DuckDB-backed stores (SQLite3 migration)
    "DuckDBAuditStore": "knowledge.duckdb_audit_store",
    "CTLogCacheStore": "knowledge.duckdb_ct_cache_store",
    "ForensicsMetadataStore": "knowledge.duckdb_forensics_store",
    # F350M-R Phase 2: Unified DuckDB-backed RAG + Identity (replaces LanceDB)
    "DuckDBRAGStore": "knowledge.duckdb_rag_store",
    "DuckDBEntityStore": "knowledge.duckdb_rag_store",
    "get_identity_store": "knowledge.duckdb_rag_store",
    "get_rag_store": "knowledge.duckdb_rag_store",
    # ISSUE [ULTIMATE]-004: DuckDB CVE/CWE Correlation Matrix
    "CveCorrelationMatrix": "knowledge.duckdb_cve_matrix",
    "CveMatch": "knowledge.duckdb_cve_matrix",
    "get_cve_matrix": "knowledge.duckdb_cve_matrix",
    # ISSUE [ULTIMATE]-004: CVE Data Loader
    "update_cve_matrix": "knowledge.cve_data_loader",
}

# Legacy compat — same names used by _LazyLegacyCompatModule
_LEGACY_NAMES: frozenset[str] = frozenset(
    (
        "AtomicJSONKnowledgeGraph",
        "KnowledgeEntry",
        "get_atomic_storage",
        "PersistentKnowledgeLayer",
        "KnowledgeNode",
        "KnowledgeEdge",
        "NodeType",
        "EdgeType",
        "KuzuDBBackend",
        "JSONBackend",
    )
)

import warnings as _warnings  # noqa: E402
from _core import aclose

# Sprint 8VC: atomic_storage and persistent_layer moved to legacy/
# Legacy imports are LAZY (deferred) to prevent import-time coupling.
# They are accessible ONLY via _lazy_legacycompat() to enforce boundary quarantine.
# Canonical sprint consumers should use knowledge.duckdb_store instead.


def _lazy_legacycompat():
    """Deferred import of legacy storage types to avoid import-time coupling.

    This is a COMPATIBILITY SEAM ONLY. Canonical sprint code should use
    duckdb_store instead. The legacy types are re-exported here for explicit
    backward-compatible consumers only.
    """
    _warnings.warn(
        "knowledge.atomic_storage is DEPRECATED. Use knowledge.duckdb_store instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from ..legacy.atomic_storage import AtomicJSONKnowledgeGraph, KnowledgeEntry, get_atomic_storage

    _warnings.warn(
        "knowledge.persistent_layer is DEPRECATED. Use knowledge.duckdb_store instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from ..legacy.persistent_layer import (
        EdgeType,
        JSONBackend,
        KnowledgeEdge,
        KnowledgeNode,
        KuzuDBBackend,
        NodeType,
        PersistentKnowledgeLayer,
    )
    return (
        AtomicJSONKnowledgeGraph,
        KnowledgeEntry,
        get_atomic_storage,
        PersistentKnowledgeLayer,
        KnowledgeNode,
        KnowledgeEdge,
        NodeType,
        EdgeType,
        KuzuDBBackend,
        JSONBackend,
    )


class _LegacyCompatModule:
    """Lazy wrapper that defers legacy imports until first attribute access."""

    __slots__ = ("_loaded", "_cache")

    def __init__(self):
        self._loaded = False
        self._cache: dict[str, Any] = {}

    def _ensure_loaded(self):
        if not self._loaded:
            self._cache = dict(zip(
                (
                    "AtomicJSONKnowledgeGraph",
                    "KnowledgeEntry",
                    "get_atomic_storage",
                    "PersistentKnowledgeLayer",
                    "KnowledgeNode",
                    "KnowledgeEdge",
                    "NodeType",
                    "EdgeType",
                    "KuzuDBBackend",
                    "JSONBackend",
                ),
                _lazy_legacycompat(), strict=False,
            ))
            self._loaded = True

    def __getattr__(self, name: str) -> Any:
        self._ensure_loaded()
        try:
            return self._cache[name]
        except KeyError as err:
            raise AttributeError(name) from err

    def __dir__(self):
        self._ensure_loaded()
        return list(self._cache.keys())


_legacy_compat = _LegacyCompatModule()

# MODERN-36 PERFORMANCE FIX: Module-level cache tracking for memory leak prevention.
# Tracks which modules have been loaded via __getattr__ so they can be
# cleaned up when the module is unloaded or the process exits.
_LOADED_MODULES: dict[str, frozenset[str]] = {}  # module_path -> set of exported names

def _track_module_load(module_path: str, name: str) -> None:
    """Track a loaded module for cleanup."""
    if module_path not in _LOADED_MODULES:
        _LOADED_MODULES[module_path] = frozenset()
    _LOADED_MODULES[module_path] = _LOADED_MODULES[module_path] | {name}

def _get_loaded_modules() -> dict[str, frozenset[str]]:
    """Return copy of loaded modules registry."""
    return dict(_LOADED_MODULES)

def _clear_knowledge_cache(clear_sys_modules: bool = True) -> int:
    """
    Clear all loaded module symbols from globals() and optionally from sys.modules.
    
    Args:
        clear_sys_modules: If True (default), also remove loaded modules from
            sys.modules to fully unload them and free their memory.
    
    Returns:
        Number of module symbols cleared from globals().
    
    MODERN-36 PERFORMANCE FIX: Call this from shutdown hooks to prevent
    memory leaks from accumulated globals() entries and sys.modules references.
    Typically called when the process is exiting or when memory pressure is high.
    
    Note: Setting clear_sys_modules=True fully unloads the modules, which may
    break existing references. Only use when the application is shutting down.
    """
    import sys
    cleared = 0
    g = globals()
    unloaded_modules: list[str] = []
    
    for module_path, names in list(_LOADED_MODULES.items()):
        for name in names:
            if name in g:
                g[name] = None
                cleared += 1
        
        # MODERN-36 FIX: Also clear from sys.modules for full module unload
        if clear_sys_modules:
            # Module names are prefixed with package path
            module_names_to_remove = [
                f"hledac.universal.{module_path}",
                module_path,
            ]
            for mod_name in module_names_to_remove:
                if mod_name in sys.modules:
                    sys.modules.pop(mod_name, None)
                    unloaded_modules.append(mod_name)
    
    _LOADED_MODULES.clear()
    return cleared

# Canonical exports — no heavy modules loaded at import time
__all__ = sorted(_LAZY_EXPORT_MAP.keys()) + sorted(_LEGACY_NAMES)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORT_MAP:
        module_path = _LAZY_EXPORT_MAP[name]
        # SECURITY: module_path comes from _LAZY_EXPORT_MAP which is a
        # hardcoded dict — no user input reaches here directly. Defense-in-depth:
        # validate it doesn't contain path traversal or shell chars.
        if ".." in module_path or module_path.startswith("/") or not module_path.replace(".", "").replace("_", "").isalnum():
            raise AttributeError(f"unsafe module path: {module_path!r}")
        try:
            module = import_module(module_path)
        except ModuleNotFoundError as exc:
            missing_name = exc.name or ""
            if missing_name == "hledac" and module_path.startswith("hledac.universal."):
                # hledac package not on path — resolve to relative import
                local_path = module_path[len("hledac.universal."):]
                module = import_module(local_path)
            elif missing_name.startswith("knowledge.") or missing_name == "knowledge":
                # knowledge subpackage on path but module not found —
                # resolve to hledac.universal.knowledge.* path
                module = import_module("hledac.universal." + module_path)
            else:
                raise
        value = getattr(module, name)
        globals()[name] = value
        # MODERN-36: Track loaded module for cleanup
        _track_module_load(module_path, name)
        return value
    if name in _LEGACY_NAMES:
        try:
            return _legacy_compat.__getattr__(name)
        except (ModuleNotFoundError, ImportError):  # ModuleNotFoundError for bare not-found; ImportError for relative-import failure in local mode  # noqa: E501
            if name in (
                "AtomicJSONKnowledgeGraph", "KnowledgeEntry", "get_atomic_storage",
                "PersistentKnowledgeLayer", "KnowledgeNode", "KnowledgeEdge",
                "NodeType", "EdgeType", "KuzuDBBackend", "JSONBackend",
            ):
                import importlib
                # SECURITY: whitelist-based path construction — only known-safe
                # module names are resolved. No arbitrary module loading.
                _KNOWN_LEGACY_MODULES = frozenset([
                    "legacy.atomic_storage", "legacy.persistent_layer",
                    "persistent_layer", "atomic_storage",
                ])
                rel_path = "legacy.atomic_storage" if name not in (
                    "PersistentKnowledgeLayer", "KnowledgeNode", "KnowledgeEdge",
                    "NodeType", "EdgeType", "KuzuDBBackend", "JSONBackend",
                ) else "legacy.persistent_layer"
                # Validate rel_path is in whitelist before import
                if rel_path not in _KNOWN_LEGACY_MODULES:
                    raise AttributeError(f"unknown legacy module: {rel_path!r}") from err
                try:
                    mod = importlib.import_module(rel_path)
                except ModuleNotFoundError:
                    # Local mode: try without legacy prefix
                    if name in ("PersistentKnowledgeLayer", "KnowledgeNode", "KnowledgeEdge",
                                "NodeType", "EdgeType", "KuzuDBBackend", "JSONBackend"):
                        mod = importlib.import_module("persistent_layer")
                    else:
                        mod = importlib.import_module("atomic_storage")
                return getattr(mod, name)
            raise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
