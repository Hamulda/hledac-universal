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
    "DuckDBReadStore": "knowledge.duckdb_read_store",
    "ActivationResult": "knowledge.duckdb_store",
    "ReplayResult": "knowledge.duckdb_store",
    "CanonicalFinding": "knowledge.duckdb_store",
    "create_owned_store": "knowledge.duckdb_store",
    # context_graph — lightweight (no heavy deps), kept eager
    "ContextGraph": "knowledge.context_graph",
    # graph_layer — heavy: kuzu, duckdb
    "KnowledgeGraphLayer": "knowledge.graph_layer",
    # rag_engine — heavy: numpy (3x), hledac.universal.core.mlx_embeddings, duckdb
    "RAGEngine": "knowledge.rag_engine",
    "RAGConfig": "knowledge.rag_engine",
    "Document": "knowledge.rag_engine",
    "RetrievedChunk": "knowledge.rag_engine",
    "BM25Index": "knowledge.rag_engine",
    "HNSWVectorIndex": "knowledge.rag_engine",
    # graph_rag — heavy: numpy (2x), hledac.universal.core.mlx_embeddings, duckdb
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

import warnings as _warnings

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
        PersistentKnowledgeLayer,
        KnowledgeNode,
        KnowledgeEdge,
        NodeType,
        EdgeType,
        KuzuDBBackend,
        JSONBackend,
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
                _lazy_legacycompat(),
            ))
            self._loaded = True

    def __getattr__(self, name: str) -> Any:
        self._ensure_loaded()
        try:
            return self._cache[name]
        except KeyError:
            raise AttributeError(name)

    def __dir__(self):
        self._ensure_loaded()
        return list(self._cache.keys())


_legacy_compat = _LegacyCompatModule()

# Canonical exports — no heavy modules loaded at import time
__all__ = sorted(_LAZY_EXPORT_MAP.keys()) + sorted(_LEGACY_NAMES)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORT_MAP:
        module_path = _LAZY_EXPORT_MAP[name]
        try:
            module = import_module(module_path)
        except ModuleNotFoundError as exc:
            name = exc.name or ""
            if name == "hledac" and module_path.startswith("hledac.universal."):
                # hledac package not on path — resolve to relative import
                local_path = module_path[len("hledac.universal."):]
                module = import_module(local_path)
            elif name.startswith("knowledge.") or name == "knowledge":
                # knowledge subpackage on path but module not found —
                # resolve to hledac.universal.knowledge.* path
                module = import_module("hledac.universal." + module_path)
            else:
                raise
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _LEGACY_NAMES:
        try:
            return _legacy_compat.__getattr__(name)
        except (ModuleNotFoundError, ImportError):  # ModuleNotFoundError for bare not-found; ImportError for relative-import failure in local mode
            if name in (
                "AtomicJSONKnowledgeGraph", "KnowledgeEntry", "get_atomic_storage",
                "PersistentKnowledgeLayer", "KnowledgeNode", "KnowledgeEdge",
                "NodeType", "EdgeType", "KuzuDBBackend", "JSONBackend",
            ):
                import importlib
                rel_path = "legacy.atomic_storage" if name not in (
                    "PersistentKnowledgeLayer", "KnowledgeNode", "KnowledgeEdge",
                    "NodeType", "EdgeType", "KuzuDBBackend", "JSONBackend",
                ) else "legacy.persistent_layer"
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
