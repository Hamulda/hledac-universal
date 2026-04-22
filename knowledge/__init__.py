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
"""

from .graph_layer import KnowledgeGraphLayer
from .context_graph import ContextGraph
from .rag_engine import RAGEngine, RAGConfig, Document, RetrievedChunk, BM25Index, HNSWVectorIndex

# Sprint 8VC: atomic_storage and persistent_layer moved to legacy/
# Legacy imports are LAZY (deferred) to prevent import-time coupling.
# They are accessible ONLY via _lazy_legacycompat() to enforce boundary quarantine.
# Canonical sprint consumers should use knowledge.duckdb_store instead.

import warnings as _warnings


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
    """Lazy wrapper that defers legacy imports until first attribute access.

    This prevents the legacy world from being loaded when sprint path modules
    import from knowledge/, while still allowing explicit backward-compatible
    consumers to access legacy types via attribute access.
    """

    __slots__ = ("_loaded", "_cache")

    def __init__(self):
        self._loaded = False
        self._cache = {}

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

    def __getattr__(self, name):
        self._ensure_loaded()
        try:
            return self._cache[name]
        except KeyError:
            raise AttributeError(name)

    def __dir__(self):
        self._ensure_loaded()
        return list(self._cache.keys())


_legacy_compat = _LegacyCompatModule()
from .graph_rag import (
    GraphRAGOrchestrator,
    CentralityScores,
    Community,
    GraphContradiction,
)
from .graph_builder import KnowledgeGraphBuilder
from .entity_linker import (
    EntityLinker,
    EntityCandidate,
    LinkedEntity,
    SimpleCache,
    link_entities,
    resolve_entity,
    get_linker,
)

# Canonical exports — no legacy coupling at import time
__all__ = [
    "KnowledgeGraphLayer",
    "ContextGraph",
    "RAGEngine",
    "RAGConfig",
    "Document",
    "RetrievedChunk",
    "BM25Index",
    "HNSWVectorIndex",
    "GraphRAGOrchestrator",
    "KnowledgeGraphBuilder",
    "EntityLinker",
    "EntityCandidate",
    "LinkedEntity",
    "SimpleCache",
    "link_entities",
    "resolve_entity",
    "get_linker",
    "CentralityScores",
    "Community",
    "GraphContradiction",
    # Legacy compat — accessed via __getattr__ lazy loading
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
]


# Module-level __getattr__ for lazy legacy re-exports (PEP 562)
# Only triggered for explicit attribute access, not for module-level code.
_LEGACY_NAMES = frozenset(
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


def __getattr__(name):
    if name in _LEGACY_NAMES:
        return _legacy_compat.__getattr__(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
