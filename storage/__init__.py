"""
Storage trinity re-export facade — F206AH / ISSUE #15
=====================================================

The canonical storage implementations live in ``hledac.universal.knowledge.*``
(and are declared canonical in ``runtime_authority_manifest.py``). This package
is a **zero-cost re-export facade** so external scripts keep a stable
``hledac.universal.storage`` import path without duplicating implementation.

Lazy loading is provided by PEP 562 ``__getattr__``: no heavy module
(DuckDB / LMDB / LanceDB / RAG) is imported until the symbol is first accessed.
This keeps importing ``hledac.universal.storage`` essentially free and fully
M1 8GB-safe.

Note: ``knowledge/ioc_graph.py`` is currently an empty stub — the real graph
storage class (``DuckPGQGraph``) lives in ``hledac.universal.graph``. The
canonical graph accumulation surface is ``GraphService`` (below), which is the
class declared canonical in ``runtime_authority_manifest.py``.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    # DuckDB — canonical findings store (canonical write seam)
    "DuckDBShadowStore",
    # LMDB — entity / claim metadata, whisper cache
    "UnifiedLMDBStore",
    # LanceDB — ANN / RAG embeddings
    "LanceDBIdentityStore",
    "LanceDBAcademicStore",
    # RAG engine
    "RAGEngine",
    # Graph accumulation (canonical per runtime_authority_manifest.py)
    "GraphService",
    # Storage Trinity orchestrator
    "StorageTrinity",
]

_FACADE_MAP: dict[str, str] = {
    "DuckDBShadowStore": "hledac.universal.knowledge.duckdb_store",
    "UnifiedLMDBStore": "hledac.universal.knowledge.lmdb_subdb",
    "LanceDBIdentityStore": "hledac.universal.knowledge.lancedb_store",
    "LanceDBAcademicStore": "hledac.universal.knowledge.lancedb_store",
    "RAGEngine": "hledac.universal.knowledge.rag_engine",
    "GraphService": "hledac.universal.knowledge.graph_service",
    "StorageTrinity": "hledac.universal.knowledge.storage_trinity",
}


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export of canonical ``knowledge.*`` storage classes."""
    module_path = _FACADE_MAP.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path)
    try:
        return getattr(module, name)
    except AttributeError as exc:  # pragma: no cover - defensive
        raise AttributeError(
            f"storage facade target {module_path!r} no longer exports {name!r}"
        ) from exc


def __dir__() -> list[str]:
    return sorted(__all__)
