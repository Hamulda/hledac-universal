"""
Fulltext search domain — Tantivy mmap-backed BM25 + Python fallback.

ISSUE-011: Tantivy fulltext replacement for Python BM25Index.

Rust backend (hledac_rust_extensions):
    - fulltext_create_index(index_path, documents) → bool
    - fulltext_add_documents(index_path, documents) → bool
    - fulltext_search(index_path, query, top_k) → list[tuple[str, float]]
    - fulltext_search_arrow(index_path, query, top_k) → bytes | None (Arrow IPC)
    - fulltext_doc_count(index_path) → int
    - fulltext_delete_index(index_path) → bool
    - fulltext_is_available() → bool

Python fallback (rank_bm25.BM25Okapi):
    - Used when Rust fulltext module not compiled with --features fulltext
    - duckdb_fts_store.py uses this for per-source BM25 indexes
    - rag_engine.py uses TantivyFulltextIndex class for hybrid RAG

M1 8GB: Tantivy uses mmap — only accessed pages consume RAM (~5MB for 50K docs).
Python BM25Okapi: ~200MB RAM for 50K docs.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_initialized = False
_rust_fulltext: Any = None


def _get_rust_fulltext() -> Any:
    """Thread-safe lazy access to Rust fulltext module."""
    global _initialized, _rust_fulltext
    if _initialized:
        return _rust_fulltext

    with _lock:
        if _initialized:
            return _rust_fulltext

        try:
            from hledac_rust_extensions import hledac_rust_extensions as ext

            _rust_fulltext = getattr(ext, "fulltext", None)
        except Exception:
            _rust_fulltext = None

        _initialized = True
        return _rust_fulltext


class _RustFulltextDomain:
    """
    Rust Tantivy fulltext domain.

    Provides mmap-backed BM25 search with Arrow IPC zero-copy results.
    Falls back to Python BM25Okapi when Rust unavailable.
    """

    __slots__ = ("_ext", "_available")

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
        self._available = getattr(ext, "fulltext_is_available", lambda: False)()

    @property
    def is_available(self) -> bool:
        """Check if Rust fulltext module is available."""
        return self._available

    def fulltext_is_available(self) -> bool:
        """Check if Rust fulltext module is available."""
        return self._available

    def create_index(
        self,
        index_path: str,
        documents: list[tuple[str, str]],
    ) -> bool:
        """Create a new Tantivy index from documents."""
        if not self._available:
            return False
        try:
            return self._ext.fulltext_create_index(index_path, documents)
        except Exception:
            return False

    def add_documents(
        self,
        index_path: str,
        documents: list[tuple[str, str]],
    ) -> bool:
        """Add documents to existing Tantivy index."""
        if not self._available:
            return False
        try:
            return self._ext.fulltext_add_documents(index_path, documents)
        except Exception:
            return False

    def search(
        self,
        index_path: str,
        query: str,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Search Tantivy index, return (doc_id, score) tuples."""
        if not self._available:
            return []
        try:
            return self._ext.fulltext_search(index_path, query, top_k)
        except Exception as e:
            logger.debug(f"Tantivy search failed: {e}")
            return []

    def search_arrow(
        self,
        index_path: str,
        query: str,
        top_k: int = 10,
    ) -> bytes | None:
        """Search Tantivy index, return Arrow IPC RecordBatch bytes (zero-copy)."""
        if not self._available:
            return None
        try:
            return self._ext.fulltext_search_arrow(index_path, query, top_k)
        except Exception as e:
            logger.debug(f"Tantivy search_arrow failed: {e}")
            return None

    def doc_count(self, index_path: str) -> int:
        """Get document count in Tantivy index."""
        if not self._available:
            return 0
        try:
            return self._ext.fulltext_doc_count(index_path)
        except Exception:
            return 0

    def delete_index(self, index_path: str) -> bool:
        """Delete Tantivy index directory."""
        if not self._available:
            return False
        try:
            return self._ext.fulltext_delete_index(index_path)
        except Exception:
            return False


class _PythonFulltextDomain:
    """
    Pure Python fulltext domain using rank_bm25.BM25Okapi.

    Used as fallback when Rust fulltext module is not compiled.
    duckdb_fts_store.py uses this for per-source BM25 indexes.
    """

    __slots__ = ("_indexes", "_lock")

    def __init__(self) -> None:
        self._indexes: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def is_available(self) -> bool:
        """Always available (pure Python)."""
        return True

    def fulltext_is_available(self) -> bool:
        """Always available (pure Python)."""
        return True

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace tokenizer."""
        return text.lower().split()

    def create_index(
        self,
        index_path: str,
        documents: list[tuple[str, str]],
    ) -> bool:
        """Create Python BM25 index from documents."""
        if not documents:
            return True

        with self._lock:
            try:
                from rank_bm25 import BM25Okapi

                doc_contents = [doc[1] for doc in documents]  # (doc_id, content) → content
                self._indexes[index_path] = {
                    "bm25": BM25Okapi(doc_contents, tokenizer=self._tokenize),
                    "doc_ids": [doc[0] for doc in documents],
                    "doc_contents": doc_contents,
                }
                return True
            except ImportError:
                logger.warning("rank_bm25 not available for Python fallback")
                return False
            except Exception as e:
                logger.error(f"Python fulltext create_index failed: {e}")
                return False

    def add_documents(
        self,
        index_path: str,
        documents: list[tuple[str, str]],
    ) -> bool:
        """Add documents to Python BM25 index (rebuilds entire index)."""
        if not documents:
            return True

        with self._lock:
            # Merge with existing documents
            existing = self._indexes.get(index_path)
            if existing:
                existing_doc_ids = existing["doc_ids"]
                existing_contents = existing["doc_contents"]
                new_doc_ids = [doc[0] for doc in documents]
                new_contents = [doc[1] for doc in documents]

                # Simple merge: append new documents
                all_doc_ids = existing_doc_ids + new_doc_ids
                all_contents = existing_contents + new_contents
            else:
                all_doc_ids = [doc[0] for doc in documents]
                all_contents = [doc[1] for doc in documents]

            try:
                from rank_bm25 import BM25Okapi

                self._indexes[index_path] = {
                    "bm25": BM25Okapi(all_contents, tokenizer=self._tokenize),
                    "doc_ids": all_doc_ids,
                    "doc_contents": all_contents,
                }
                return True
            except ImportError:
                return False
            except Exception as e:
                logger.error(f"Python fulltext add_documents failed: {e}")
                return False

    def search(
        self,
        index_path: str,
        query: str,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Search using Python rank_bm25 fallback."""
        with self._lock:
            idx = self._indexes.get(index_path)
            if not idx:
                return []

            try:
                bm25 = idx["bm25"]
                doc_ids = idx["doc_ids"]
                scores = bm25.get_scores(query.split())

                if top_k >= len(doc_ids):
                    top_k = len(doc_ids)

                top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

                return [(doc_ids[i], scores[i]) for i in top_indices if scores[i] > 0]
            except Exception as e:
                logger.debug(f"Python fulltext search failed: {e}")
                return []

    def search_arrow(
        self,
        index_path: str,
        query: str,
        top_k: int = 10,
    ) -> bytes | None:
        """Arrow search not available in Python fallback."""
        return None

    def doc_count(self, index_path: str) -> int:
        """Get document count from Python index."""
        with self._lock:
            idx = self._indexes.get(index_path)
            if not idx:
                return 0
            return len(idx["doc_ids"])

    def delete_index(self, index_path: str) -> bool:
        """Delete Python index."""
        with self._lock:
            if index_path in self._indexes:
                del self._indexes[index_path]
            return True


def get_domain(ext: object | None = None) -> _RustFulltextDomain | _PythonFulltextDomain:
    """Return Rust or Python domain based on extension availability.

    Args:
        ext: Optional extension module. If None, uses thread-safe lazy loading.
    """
    if ext is not None:
        return _RustFulltextDomain(ext)

    # Lazy loading with thread safety
    rust_ext = _get_rust_fulltext()
    if rust_ext is not None:
        return _RustFulltextDomain(rust_ext)

    return _PythonFulltextDomain()


__all__ = [
    "_RustFulltextDomain",
    "_PythonFulltextDomain",
    "get_domain",
]
