"""
Wiring: Fulltext Index (Tantivy) → knowledge/rag_engine.py

Integration Point: knowledge/rag_engine.py BM25Index class
Benefit: 
  - mmap-backed: ~5MB RAM for 50K docs vs ~200MB Python
  - Zero-copy: documents read directly from mmap
  - Persistent: index survives process restart
  - No 50K document limit
  - Arrow IPC zero-copy results (blitz-01)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knowledge.rag_engine import Document

logger = logging.getLogger(__name__)

# ─── Rust backend availability ─────────────────────────────────────────────────

_rust_available: bool = False
_rust_module = None

try:
    from _core.rust_backend import get_accel
    _accel = get_accel()
    if _accel.is_available:
        _rust_module = getattr(_accel, 'fulltext', None)
        if _rust_module is not None:
            _rust_available = True
            logger.debug("Tantivy fulltext index: Rust backend available")
except Exception as e:
    logger.debug(f"Tantivy fulltext index: Rust backend not available: {e}")
    _accel = None
    _rust_module = None

# ─── Fallback Python implementation ────────────────────────────────────────────

def _python_create_index(
    index_path: str,
    documents: list[tuple[str, str]],
) -> None:
    """
    Pure Python fallback for fulltext index creation.
    
    Creates a simple in-memory index with term frequencies.
    This is a basic implementation - for production use Rust Tantivy.
    """
    import json
    from collections import defaultdict
    from pathlib import Path
    
    # Store documents
    docs = []
    for doc_id, content in documents:
        tokens = content.lower().split()
        docs.append({
            'id': doc_id,
            'content': content,
            'tokens': tokens,
            'token_set': set(tokens),
        })
    
    # Write to disk as JSON
    index_file = Path(index_path) / 'index.json'
    index_file.parent.mkdir(parents=True, exist_ok=True)
    index_file.write_text(json.dumps(docs))


def _python_search(
    index_path: str,
    query: str,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    """
    Pure Python fallback for fulltext search.
    
    Simple TF-IDF-like scoring.
    """
    import json
    from pathlib import Path
    from collections import Counter
    
    index_file = Path(index_path) / 'index.json'
    if not index_file.exists():
        return []
    
    try:
        docs = json.loads(index_file.read_text())
    except Exception:
        return []
    
    if not docs:
        return []
    
    query_tokens = query.lower().split()
    if not query_tokens:
        return []
    
    # Compute IDF
    n_docs = len(docs)
    doc_freqs = Counter()
    for doc in docs:
        doc_freqs.update(set(doc.get('tokens', [])))
    
    results = []
    for doc in docs:
        tokens = doc.get('tokens', [])
        token_set = doc.get('token_set', set())
        
        score = 0.0
        for term in query_tokens:
            if term in token_set:
                tf = tokens.count(term) / max(1, len(tokens))
                idf = 1.0 / max(1, doc_freqs.get(term, 1))
                score += tf * idf
        
        if score > 0:
            results.append((doc['id'], score))
    
    # Sort by score and return top-k
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def _python_doc_count(index_path: str) -> int:
    """Pure Python fallback for document count."""
    import json
    from pathlib import Path
    
    index_file = Path(index_path) / 'index.json'
    if not index_file.exists():
        return 0
    
    try:
        docs = json.loads(index_file.read_text())
        return len(docs)
    except Exception:
        return 0


def _python_delete_index(index_path: str) -> bool:
    """Pure Python fallback for index deletion."""
    import shutil
    from pathlib import Path
    
    path = Path(index_path)
    if not path.exists():
        return False
    
    try:
        shutil.rmtree(path)
        return True
    except Exception:
        return False


# ─── Public API ────────────────────────────────────────────────────────────────

def create_index(
    index_path: str,
    documents: list[tuple[str, str]],
) -> bool:
    """
    Create a fulltext index from documents.
    
    Args:
        index_path: Directory for the index
        documents: List of (doc_id, content) tuples
    
    Returns:
        True on success
    """
    if _rust_available and _rust_module is not None:
        try:
            _rust_module.fulltext_create_index(index_path, documents)
            return True
        except Exception as e:
            logger.warning(f"Rust fulltext create_index failed, using Python fallback: {e}")
    
    # Python fallback
    _python_create_index(index_path, documents)
    return True


def add_documents(
    index_path: str,
    documents: list[tuple[str, str]],
) -> bool:
    """
    Add documents to an existing index.
    
    Args:
        index_path: Directory of the existing index
        documents: List of (doc_id, content) tuples
    
    Returns:
        True on success
    """
    if _rust_available and _rust_module is not None:
        try:
            _rust_module.fulltext_add_documents(index_path, documents)
            return True
        except Exception as e:
            logger.warning(f"Rust fulltext add_documents failed, using Python fallback: {e}")
    
    # Python fallback: recreate with all documents
    # Load existing documents first
    import json
    from pathlib import Path
    
    index_file = Path(index_path) / 'index.json'
    existing_docs = []
    if index_file.exists():
        try:
            existing_docs = json.loads(index_file.read_text())
        except Exception:
            pass
    
    # Convert existing docs to (id, content) format
    all_docs = [(d['id'], d['content']) for d in existing_docs]
    all_docs.extend(documents)
    
    _python_create_index(index_path, all_docs)
    return True


def search(
    index_path: str,
    query: str,
    top_k: int = 10,
) -> list[tuple[str, float]]:
    """
    Search fulltext index and return top-K results.
    
    Args:
        index_path: Directory of the index
        query: Search query string
        top_k: Maximum number of results
    
    Returns:
        List of (doc_id, bm25_score) tuples, sorted by score descending
    """
    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.fulltext_search(index_path, query, top_k)
        except Exception as e:
            logger.warning(f"Rust fulltext search failed, using Python fallback: {e}")
    
    # Python fallback
    return _python_search(index_path, query, top_k)


def search_arrow(
    index_path: str,
    query: str,
    top_k: int = 10,
) -> bytes | None:
    """
    Search fulltext index and return results as Arrow IPC RecordBatch bytes.
    
    This is the ZERO-COPY PATH (ISSUE [BLITZ]-01):
    - Returns PyBytes containing Arrow IPC RecordBatchStream
    - Python calls pa.ipc.open_stream() for zero-copy deserialization
    - NO per-row Python object allocation
    
    Args:
        index_path: Directory of the index
        query: Search query string
        top_k: Maximum number of results
    
    Returns:
        Arrow IPC bytes or None on error
    """
    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.fulltext_search_arrow(index_path, query, top_k)
        except Exception as e:
            logger.debug(f"Rust fulltext search_arrow not available: {e}")
    
    return None


def doc_count(index_path: str) -> int:
    """Get document count from index."""
    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.fulltext_doc_count(index_path)
        except Exception:
            pass
    
    return _python_doc_count(index_path)


def delete_index(index_path: str) -> bool:
    """Delete a fulltext index directory."""
    if _rust_available and _rust_module is not None:
        try:
            return _rust_module.fulltext_delete_index(index_path)
        except Exception:
            pass
    
    return _python_delete_index(index_path)


def is_available() -> bool:
    """Check if Rust fulltext module is available."""
    return _rust_available


# ─── TantivyIndex wrapper class ────────────────────────────────────────────────

class TantivyIndex:
    """
    High-performance fulltext index wrapper using Tantivy.
    
    Provides mmap-backed BM25 search with zero-copy Arrow IPC results.
    Automatically falls back to pure Python if Rust unavailable.
    
    Usage:
        index = TantivyIndex("/tmp/search_index")
        
        # Add documents
        index.add_documents([
            ("doc1", "malware analysis report"),
            ("doc2", "phishing campaign details"),
        ])
        
        # Search
        results = index.search("malware", top_k=5)
        for doc_id, score in results:
            print(f"{doc_id}: {score:.3f}")
    """
    
    __slots__ = ('_index_path', '_initialized')
    
    def __init__(self, index_path: str | Path):
        self._index_path = str(index_path)
        self._initialized = False
    
    @property
    def index_path(self) -> str:
        """Get index directory path."""
        return self._index_path
    
    def add_documents(
        self,
        documents: list[tuple[str, str]],
        batch_size: int = 1000,
    ) -> None:
        """
        Add documents to the index.
        
        Args:
            documents: List of (doc_id, content) tuples
            batch_size: Documents per commit (default 1000)
        """
        if not self._initialized:
            # Create new index
            create_index(self._index_path, documents)
            self._initialized = True
        else:
            # Add to existing index
            add_documents(self._index_path, documents)
    
    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Search the index.
        
        Args:
            query: Search query (supports Tantivy query syntax)
            top_k: Maximum results
        
        Returns:
            List of (doc_id, score) tuples
        """
        return search(self._index_path, query, top_k)
    
    def search_arrow(
        self,
        query: str,
        top_k: int = 10,
    ) -> bytes | None:
        """
        Zero-copy search returning Arrow IPC bytes.
        
        Use pa.ipc.open_stream() for zero-copy deserialization.
        
        Args:
            query: Search query
            top_k: Maximum results
        
        Returns:
            Arrow IPC RecordBatchStream bytes or None
        """
        return search_arrow(self._index_path, query, top_k)
    
    def doc_count(self) -> int:
        """Get number of documents in index."""
        return doc_count(self._index_path)
    
    def delete(self) -> bool:
        """Delete the index."""
        return delete_index(self._index_path)
