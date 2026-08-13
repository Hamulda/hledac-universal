"""
RAGEngine - Ultra Context + SPR Compression + Hybrid Retrieval + HNSW Vector Search

ROLE: Grounding Authority (NOT identity/entity store)








====================================================
Tento modul je grounding authority pro context augmentation.
NENÍ owner identity/entity resolution - to je lancedb_store.
NENÍ owner embedding computation - to je MLXEmbeddingManager singleton.

Integruje:
- InfiniteContextEngine pro velké kontexty
- SPRCompressor pro sémantickou kompresi
- SecureEnclave pro citlivá data
- Hybrid Retrieval: Dense + Sparse (BM25) fusion
- HNSW Vector Search for fast approximate nearest neighbor search
- MLX-native execution
"""
import asyncio
import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
import os
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from pathlib import Path
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str, loads as _msgspec_loads
from hledac.universal.utils.asyncx import parallel
from hledac.universal.core.lazy_imports import lazy
from operator import attrgetter, itemgetter
# SWARM-010: Feature flag imports for registry compliance
from hledac.universal.core.feature_flags import FeatureFlags, FeatureFlag
if TYPE_CHECKING:
    pass
_dd_int: defaultdict[str, int] = defaultdict(int)
_dense_sparse_factory: defaultdict[str, dict[str, float]] = defaultdict(lambda: {'dense': 0.0, 'sparse': 0.0})
from hledac.universal.security.secure_enclave import EnclaveAvailability, EnclaveStatus, SecureEnclaveBackend, SecureEnclaveError, build_batch_manifest, create_secure_enclave_backend
COREML_AVAILABLE = False
COREML_MODEL_PATH = None
try:
    from rank_bm25 import BM25Okapi as _RankBM25
    RANK_BM25_AVAILABLE = True
except ImportError:
    _RankBM25: type | None = None
    RANK_BM25_AVAILABLE = False
import numpy as np
logger = logging.getLogger(__name__)

class RAGConfig(Struct):
    """Konfigurace pro RAG — Sprint F330: env var defaults consistent with knowledge/ pattern.
    
    SWARM-010: All flags now use FeatureFlags for registry compliance.
    """
    # SWARM-010: Use FeatureFlags getters for type-appropriate access
    enable_ultra_context: bool = FeatureFlags.get(FeatureFlag.RAG_ULTRA_CONTEXT)
    enable_spr_compression: bool = FeatureFlags.get(FeatureFlag.RAG_SPR_COMPRESSION)
    enable_secure_enclave: bool = FeatureFlags.get(FeatureFlag.RAG_SECURE_ENCLAVE)
    compression_threshold: int = FeatureFlags.get_int(FeatureFlag.RAG_COMPRESSION_THRESHOLD, 50)
    max_tokens: int = FeatureFlags.get_int(FeatureFlag.RAG_MAX_TOKENS, 128000)
    enable_hybrid_retrieval: bool = FeatureFlags.get(FeatureFlag.RAG_HYBRID_RETRIEVAL)
    dense_weight: float = FeatureFlags.get_float(FeatureFlag.RAG_DENSE_WEIGHT, 0.5)
    sparse_weight: float = FeatureFlags.get_float(FeatureFlag.RAG_SPARSE_WEIGHT, 0.5)
    bm25_k1: float = FeatureFlags.get_float(FeatureFlag.RAG_BM25_K1, 1.5)
    bm25_b: float = FeatureFlags.get_float(FeatureFlag.RAG_BM25_B, 0.75)
    chunk_size: int = FeatureFlags.get_int(FeatureFlag.RAG_CHUNK_SIZE, 512)
    chunk_overlap: int = FeatureFlags.get_int(FeatureFlag.RAG_CHUNK_OVERLAP, 128)
    use_hnsw: bool = FeatureFlags.get(FeatureFlag.RAG_USE_HNSW)
    hnsw_dim: int = FeatureFlags.get_int(FeatureFlag.RAG_HNSW_DIM, 384)
    hnsw_max_elements: int = FeatureFlags.get_int(FeatureFlag.RAG_HNSW_MAX_ELEMENTS, 100000)
    hnsw_M: int = FeatureFlags.get_int(FeatureFlag.RAG_HNSW_M, 16)
    hnsw_ef_construction: int = FeatureFlags.get_int(FeatureFlag.RAG_HNSW_EF_CONSTRUCTION, 200)
    hnsw_ef_search: int = FeatureFlags.get_int(FeatureFlag.RAG_HNSW_EF_SEARCH, 50)
    hnsw_index_path: str | None = FeatureFlags.get_str(FeatureFlag.RAG_HNSW_INDEX_PATH, None)
    hnsw_space: str = FeatureFlags.get_str(FeatureFlag.RAG_HNSW_SPACE, 'cosine')

class Document(Struct, frozen=True):
    """Document for retrieval"""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    # frozen=True auto-generates __hash__ and __eq__

class RetrievedChunk(Struct, frozen=True):
    """Retrieved document chunk with scores"""
    document: Document
    chunk_text: str
    dense_score: float = 0.0
    sparse_score: float = 0.0
    final_score: float = 0.0

class BM25Index:
    """Simple BM25 implementation for sparse retrieval"""
    MAX_BM25_DOCUMENTS: int = 50000
    __slots__ = tuple(('_MAX_DOC_FREQS', '_MAX_TERM_DOC_PAIRS', '_rank_bm25', '_term_doc_pair_count', 'avg_doc_length', 'b', 'doc_count', 'doc_freqs', 'doc_lengths', 'documents', 'k1', 'term_doc_freqs'))

    def __init__(self, k1: float=1.5, b: float=0.75):
        self.k1 = k1
        self.b = b
        self.documents: list[Document] = []
        self.doc_freqs: dict[str, int] = defaultdict(int)
        self.doc_lengths: list[int] = []
        self.avg_doc_length: float = 0.0
        self.term_doc_freqs: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.doc_count: int = 0
        self._rank_bm25 = None
        self._MAX_DOC_FREQS: int = 25000
        self._MAX_TERM_DOC_PAIRS: int = 100000
        self._term_doc_pair_count: int = 0

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization"""
        return re.findall('\\b[a-zA-Z]+\\b', text.lower())

    def add_document(self, doc: Document):
        """Add document to index. Silently drops if MAX_BM25_DOCUMENTS reached."""
        if len(self.documents) >= self.MAX_BM25_DOCUMENTS:
            return
        tokens = self._tokenize(doc.content)
        doc_length = len(tokens)
        self.documents.append(doc)
        self.doc_lengths.append(doc_length)
        term_counts: dict[str, int] = defaultdict(int)
        for token in tokens:
            term_counts[token] += 1
        while len(self.doc_freqs) >= self._MAX_DOC_FREQS:
            lfu_term = min(self.doc_freqs, key=lambda k: self.doc_freqs.get(k, 0))
            self.doc_freqs.pop(lfu_term, 0)
            if lfu_term in self.term_doc_freqs:
                self._term_doc_pair_count -= len(self.term_doc_freqs.pop(lfu_term))
        for term in term_counts:
            if self._term_doc_pair_count >= self._MAX_TERM_DOC_PAIRS:
                break
            if term not in self.doc_freqs:
                if self._term_doc_pair_count + 1 > self._MAX_TERM_DOC_PAIRS:
                    continue
            self.doc_freqs[term] += 1
            self.term_doc_freqs[term][len(self.documents) - 1] = term_counts[term]
            self._term_doc_pair_count += 1
        self.doc_count = len(self.documents)
        self.avg_doc_length = sum(self.doc_lengths) / self.doc_count if self.doc_count > 0 else 0
        if RANK_BM25_AVAILABLE and _RankBM25 is not None:
            tokenized_corpus = [self._tokenize(doc.content) for doc in self.documents]
            self._rank_bm25 = _RankBM25(tokenized_corpus)

    def search(self, query: str, top_k: int=10) -> list[tuple[int, float]]:
        """Search documents using BM25"""
        if not self.documents:
            return []
        query_tokens = self._tokenize(query)
        import numpy as np
        if self._rank_bm25 is not None:
            scores = self._rank_bm25.get_scores(query_tokens)
            top_indices = np.argsort(scores)[::-1][:top_k]
            return [(int(idx), float(scores[idx])) for idx in top_indices if scores[idx] > 0]
        scores = np.zeros(self.doc_count)
        for term in query_tokens:
            if term not in self.doc_freqs:
                continue
            idf = np.log((self.doc_count - self.doc_freqs[term] + 0.5) / (self.doc_freqs[term] + 0.5) + 1)
            for doc_id, term_freq in self.term_doc_freqs[term].items():
                doc_length = self.doc_lengths[doc_id]
                numerator = term_freq * (self.k1 + 1)
                denominator = term_freq + self.k1 * (1 - self.b + self.b * (doc_length / self.avg_doc_length))
                scores[doc_id] += idf * (numerator / denominator)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(idx), float(scores[idx])) for idx in top_indices if scores[idx] > 0]

# ---------------------------------------------------------------------------
# ISSUE-011: TantivyFulltextIndex — Rust mmap-backed BM25 replacement
# ---------------------------------------------------------------------------

# Lazy import of Rust fulltext module (feature-gated: fulltext).
# When the Rust extension is compiled WITHOUT --features fulltext, this
# ImportError triggers the Python BM25Index fallback transparently.
_RUST_FULLTEXT: Any = None
# R6: Centralized Rust access via core.rust_backend
from hledac.universal.core.rust_backend import rust

_RUST_FULLTEXT = rust.fulltext
_RUST_FULLTEXT_AVAILABLE: bool = False
if _RUST_FULLTEXT is not None:
    _RUST_FULLTEXT_AVAILABLE = getattr(_RUST_FULLTEXT, 'fulltext_is_available', lambda: False)()

# Environment override: set HLEDAC_DISABLE_RUST_FULLTEXT=1 to force Python BM25 fallback.
# SWARM-010: Use FeatureFlags for registry compliance
if FeatureFlags.get(FeatureFlag.DISABLE_RUST_FULLTEXT):
    _RUST_FULLTEXT_AVAILABLE = False
    _RUST_FULLTEXT = None

# Default Tantivy index directory (mmap-backed, persistent across restarts).
_DEFAULT_FULLTEXT_INDEX_DIR: str | None = os.environ.get(
    'HLEDAC_FULLTEXT_INDEX_DIR',
    str(Path.home() / '.hledac' / 'fulltext_index')
) if os.environ.get('HLEDAC_FULLTEXT_INDEX_DIR') else str(Path.home() / '.hledac' / 'fulltext_index')


class TantivyFulltextIndex:
    """Tantivy-backed BM25 fulltext index with Python BM25 fallback.

    ISSUE-011: Replaces the pure-Python BM25Index (88-163) with a Rust Tantivy
    mmap-backed index that provides:
    - ~5MB RAM for 50K documents (vs ~200MB Python BM25Index)
    - Zero-copy access via mmap — documents read directly from disk
    - Persistent index across process restarts (no rebuild)
    - Parallel search via Tantivy's rayon thread pool
    - No MAX_BM25_DOCUMENTS limit (Tantivy scales to millions)

    When the Rust fulltext module is not available (not compiled with
    --features fulltext), this class transparently falls back to the
    pure-Python BM25Index with identical API.

    M1 8GB safe: mmap uses demand paging — only hot pages consume RAM.
    Index directory uses ~5MB for 50K documents (OS page cache managed
    by kernel).

    Usage:
        index = TantivyFulltextIndex(index_path="/path/to/index")
        index.add_document(doc)
        results = index.search("query", top_k=10)
    """

    __slots__ = tuple((
        '_bm25_fallback',  # Python BM25Index fallback when Rust unavailable
        '_documents',      # List[Document] — mirror for fallback sync
        '_index_path',     # str — Tantivy index directory (mmap-backed)
        '_needs_commit',   # bool — True when documents added but not committed
        '_rust_available', # bool — cached availability check
    ))

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        index_path: str | None = None,
    ):
        """Initialize Tantivy-backed fulltext index with Python fallback.

        Args:
            k1: BM25 k1 parameter (term frequency saturation). Used only for
                Python fallback — Tantivy uses its own default BM25.
            b: BM25 b parameter (length normalization). Used only for
                Python fallback.
            index_path: Directory for Tantivy mmap index. If None, uses
                HLEDAC_FULLTEXT_INDEX_DIR env or ~/.hledac/fulltext_index.
        """
        self._index_path: str = index_path or _DEFAULT_FULLTEXT_INDEX_DIR
        self._rust_available: bool = _RUST_FULLTEXT_AVAILABLE
        self._documents: list[Document] = []
        self._needs_commit: bool = False

        if not self._rust_available:
            # Initialize Python fallback
            self._bm25_fallback: BM25Index = BM25Index(k1=k1, b=b)
        else:
            self._bm25_fallback = None  # type: ignore[assignment]

        logger.debug(
            'TantivyFulltextIndex: %s (index=%s)',
            'Rust Tantivy' if self._rust_available else 'Python BM25 fallback',
            self._index_path,
        )

    @property
    def using_tantivy(self) -> bool:
        """Check if Tantivy is being used."""
        return self._rust_available and _RUST_FULLTEXT is not None

    def _get_rust_module(self) -> Any:
        """Get the Rust fulltext module, raising ImportError if unavailable."""
        if not self._rust_available or _RUST_FULLTEXT is None:
            raise ImportError('Rust fulltext module not available')
        return _RUST_FULLTEXT

    @staticmethod
    def _get_doc_id(doc: Any) -> str:
        """Extract document ID from Document (rag_engine) or SearchDocument (search_index).

        Document uses `id` attribute; SearchDocument uses `url` attribute.
        Falls back to string hash if neither is present.
        """
        if hasattr(doc, 'id'):
            return str(doc.id)
        if hasattr(doc, 'url'):
            return str(doc.url)
        return str(hash(doc))

    def add_document(self, doc: Document | Any) -> None:
        """Add document to index.

        Accepts Document (rag_engine) or SearchDocument (search_index) —
        any type with a `.content` attribute works.

        When using Tantivy: documents are accumulated in memory and committed
        to mmap on first search (batch commit for efficiency).

        When using Python fallback: delegates to BM25Index.add_document().
        """
        self._documents.append(doc)
        self._needs_commit = True

        if not self._rust_available:
            self._bm25_fallback.add_document(doc)

    def index(self, documents: list[Document | Any]) -> None:
        """Index a batch of documents. Delegates to add_document() per doc.

        Compatibility with search_index.LocalSearchSeam API.
        """
        for doc in documents:
            self.add_document(doc)

    def _commit_pending(self) -> None:
        """Commit accumulated documents to Tantivy mmap index.

        Batches all pending documents into a single Tantivy commit for
        I/O efficiency. Clears _needs_commit and _documents on success.
        """
        if not self._needs_commit or not self._rust_available:
            return

        if not self._documents:
            self._needs_commit = False
            return

        try:
            rust_mod = self._get_rust_module()
            docs_for_rust: list[tuple[str, str]] = [
                (self._get_doc_id(doc), doc.content) for doc in self._documents
            ]
            # Use create_index for first batch, add_documents for subsequent
            index_exists = Path(self._index_path).exists() and Path(self._index_path).joinpath('meta.json').exists()
            if index_exists:
                rust_mod.fulltext_add_documents(self._index_path, docs_for_rust)
            else:
                rust_mod.fulltext_create_index(self._index_path, docs_for_rust)
            self._needs_commit = False
            logger.debug(
                'Tantivy: committed %d documents to %s',
                len(docs_for_rust),
                self._index_path,
            )
        except Exception as e:
            logger.warning(
                'Tantivy commit failed, falling back to Python BM25: %s', e
            )
            self._rust_available = False
            # Build fallback from accumulated documents
            self._bm25_fallback = BM25Index()
            for doc in self._documents:
                self._bm25_fallback.add_document(doc)

    async def commit_pending_async(self) -> None:
        """Async version of _commit_pending with peak load coordinator admission.

        UNIFIED-001: Acquires admission from GlobalPeakLoadCoordinator before
        Tantivy indexing to prevent OOM when multiple subsystems compete for memory.
        Tantivy mmap indexing typically allocates ~2 GB for large batches.

        This method should be called from async contexts instead of _commit_pending().
        """
        if not self._needs_commit or not self._rust_available:
            return

        if not self._documents:
            self._needs_commit = False
            return

        # UNIFIED-001: Acquire admission from peak load coordinator
        peak_guard = None
        try:
            from hledac.universal.core.peak_load_coordinator import (
                ResourceClass,
                TaskPriority,
                get_peak_coordinator,
            )
            coordinator = get_peak_coordinator()
            if coordinator is not None:
                # Tantivy mmap indexing: ~2000 MB peak allocation
                peak_guard = await coordinator.acquire(
                    ResourceClass.TANTIVY_INDEX,
                    estimated_mb=2000.0,
                    priority=TaskPriority.NORMAL,
                    owner=f"tantivy_commit:{len(self._documents)}_docs",
                    timeout_s=10.0,
                )
        except (ImportError, TimeoutError) as e:
            logger.debug(f"[UNIFIED-001] Tantivy commit admission failed: {e}")
            # Fail-open: proceed without admission if coordinator unavailable
        except Exception as e:
            logger.debug(f"[UNIFIED-001] Tantivy commit admission error (fail-open): {e}")

        # UNIFIED-001: Wrap actual work in peak_guard context to ensure release
        if peak_guard is not None:
            async with peak_guard:
                self._commit_pending()
        else:
            self._commit_pending()

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Search documents using BM25.

        When using Tantivy: commits pending documents first, then queries
        the mmap-backed index. Results are (doc_index, score) matching
        BM25Index API for backward compatibility.

        When using Python fallback: delegates to BM25Index.search().

        Args:
            query: Search query string.
            top_k: Maximum number of results to return.

        Returns:
            List of (document_index, bm25_score) tuples, sorted by score desc.
            Empty list on no matches or error.
        """
        # Commit pending documents before search (write consistency)
        if self._needs_commit and self._rust_available:
            self._commit_pending()

        if self._rust_available:
            try:
                rust_mod = self._get_rust_module()
                # ISSUE [BLITZ]-01: Zero-copy Arrow path — try first,
                # fall back to legacy tuple-path on any error.
                ipc_bytes = rust_mod.fulltext_search_arrow(
                    self._index_path, query, top_k
                )
                if ipc_bytes and len(ipc_bytes) > 8:
                    # MODERN-25-EXT: Use unified Arrow IPC helper
                    from hledac.universal.knowledge.duckdb_store import arrow_ipc_to_record_batch
                    batch = arrow_ipc_to_record_batch(ipc_bytes, source="rag_engine")
                    if batch is None:
                        return []  # empty schema-only batch

                    # Columnar access: doc_id column + score column → mapped tuples
                    doc_id_to_idx: dict[str, int] = {
                        doc.id: i for i, doc in enumerate(self._documents)
                    }
                    doc_id_col = batch.column(0).to_pylist()
                    score_col = batch.column(1).to_pylist()
                    mapped: list[tuple[int, float]] = []
                    for doc_id, score in zip(doc_id_col, score_col):
                        idx = doc_id_to_idx.get(doc_id)
                        if idx is not None:
                            mapped.append((idx, float(score)))
                    return mapped

                # Fallback: legacy tuple path
                results = rust_mod.fulltext_search(self._index_path, query, top_k)
                doc_id_to_idx: dict[str, int] = {
                    doc.id: i for i, doc in enumerate(self._documents)
                }
                mapped: list[tuple[int, float]] = []
                for doc_id, score in results:
                    idx = doc_id_to_idx.get(doc_id)
                    if idx is not None:
                        mapped.append((idx, score))
                return mapped
            except Exception as e:
                logger.warning(
                    'Tantivy search failed, falling back to Python BM25: %s', e
                )
                self._rust_available = False
                # Build fallback from accumulated documents
                self._bm25_fallback = BM25Index()
                for doc in self._documents:
                    self._bm25_fallback.add_document(doc)

        # Python BM25 fallback
        return self._bm25_fallback.search(query, top_k)

    @property
    def documents(self) -> list[Document]:
        """Get indexed documents. Matches BM25Index.documents API."""
        if self._rust_available:
            return self._documents
        return self._bm25_fallback.documents

    def clear(self) -> None:
        """Clear all documents from index.

        Deletes Tantivy index directory and resets in-memory state.
        """
        self._documents.clear()
        self._needs_commit = False

        if self._rust_available:
            try:
                rust_mod = self._get_rust_module()
                rust_mod.fulltext_delete_index(self._index_path)
            except Exception:  # noqa: BLE001
                pass

        if not self._rust_available and self._bm25_fallback is not None:
            self._bm25_fallback = BM25Index()

    def close(self) -> None:
        """Commit pending documents and release resources.

        Safe to call multiple times.
        """
        if self._needs_commit:
            self._commit_pending()
        self._documents.clear()

class HNSWVectorIndex:
    """
    USearch-based Vector Index for fast approximate nearest neighbor search.

    Uses usearch for C++ optimized HNSW with Metal SIMD (M1 accelerated):
    - <1ms search latency for 100K vectors
    - ~100MB memory per 100K 768-dim vectors
    - Dynamic index updates
    - Persistent storage support

    M1 8GB Optimized:
    - Configurable max_elements to control memory usage
    - Efficient C++ backend with Metal SIMD
    - Brute-force fallback when index unavailable
    """
    __slots__ = tuple(('M', '_available', '_current_label', '_id_to_label', '_index', '_is_initialized', '_label_to_id', '_usearch', '_vectors', 'dim', 'ef_construction', 'ef_search', 'index_path', 'max_elements', 'space'))

    def __init__(self, dim: int=768, max_elements: int=100000, M: int=16, ef_construction: int=200, ef_search: int=50, space: str='cosine', index_path: str | None=None):
        """
        Initialize USearch Vector Index.

        Args:
            dim: Vector dimension (default 768 for typical embeddings)
            max_elements: Maximum number of vectors in index
            M: Number of bi-directional links for each node (higher = better recall, more memory)
            ef_construction: Size of dynamic candidate list for construction (higher = better quality)
            ef_search: Size of dynamic candidate list for search (higher = better recall)
            space: Distance metric - "cosine", "l2", or "ip" (inner product)
            index_path: Optional path for persistent index storage
        """
        self.dim = dim
        self.max_elements = max_elements
        self.M = M
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self.space = space
        self.index_path = index_path
        self._index: Any = None
        self._id_to_label: dict[str, int] = {}
        self._label_to_id: dict[int, str] = {}
        self._current_label = 0
        self._is_initialized = False
        try:
            import usearch
            self._usearch = usearch
            self._available = True
        except ImportError:
            logger.warning('usearch not available, USearch index will use brute-force fallback')
            self._usearch = None
            self._available = False
        self._vectors: dict[str, np.ndarray] = {}

    def _init_index(self):
        """Initialize the usearch index."""
        if not self._available or self._is_initialized:
            return
        try:
            space_map = {'cosine': 'cos', 'l2': 'l2', 'ip': 'ip', 'euclidean': 'l2'}
            usearch_metric = space_map.get(self.space, 'cos')
            import usearch.index
            # Adaptive expansion_add: higher for large indices (>100k vectors) for better HNSW quality
            # usearch supports up to 1024, use 200-300 range for large indices
            element_count = getattr(self._index, 'size', 0) if self._index is not None else 0
            if element_count > 100_000 or self.max_elements > 100_000:
                exp_add = min(self.ef_construction, 300)
            else:
                exp_add = min(self.ef_construction, 200)
            self._index = usearch.index.Index(ndim=self.dim, metric=usearch_metric, dtype='f32', connectivity=self.M, expansion_add=exp_add, expansion_search=self.ef_search)
            self._is_initialized = True
            logger.info(f'USearch index initialized: dim={self.dim}, max_elements={self.max_elements}, expansion_add={exp_add}')
        except Exception as e:
            logger.error(f'Failed to initialize USearch index: {e}')
            self._available = False

    def add_vectors(self, vectors: np.ndarray, ids: list[str]) -> None:
        """
        Add vectors to the index.

        Args:
            vectors: Array of shape (n_vectors, dim) or (dim,) for single vector
            ids: List of unique string identifiers for each vector
        """
        if len(vectors) != len(ids):
            raise ValueError(f'Number of vectors ({len(vectors)}) must match number of ids ({len(ids)})')
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        if vectors.shape[1] != self.dim:
            raise ValueError(f'Vector dimension {vectors.shape[1]} does not match index dimension {self.dim}')
        for id_ in ids:
            if id_ in self._id_to_label:
                raise ValueError(f'Duplicate id: {id_}')
        if self._available and (not self._is_initialized):
            self._init_index()
        if self._available and self._is_initialized and (self._index is not None):
            for id_ in ids:
                label = self._current_label
                self._id_to_label[id_] = label
                self._label_to_id[label] = id_
                self._current_label += 1
            try:
                for i, vec in enumerate(vectors):
                    label = self._id_to_label[ids[i]]
                    self._index.add(label, vec.astype(np.float32))
                logger.debug(f'Added {len(ids)} vectors to USearch index')
            except Exception as e:
                logger.error(f'Failed to add vectors to USearch index: {e}')
                self._available = False
                for id_, vec in zip(ids, vectors, strict=False):
                    self._vectors[id_] = vec.copy()
        else:
            for id_, vec in zip(ids, vectors, strict=False):
                self._vectors[id_] = vec.copy()
            logger.debug(f'Added {len(ids)} vectors to brute-force storage')

    def search(self, query_vector: np.ndarray, k: int=10, filter_ids: list[str] | None=None) -> tuple[list[str], list[float]]:
        """
        Search for k nearest neighbors.

        Args:
            query_vector: Query vector of shape (dim,)
            k: Number of results to return
            filter_ids: Optional list of ids to filter results

        Returns:
            Tuple of (list of ids, list of distances/scores)
        """
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)
        if self._available and self._is_initialized and (self._index is not None) and (len(self._id_to_label) > 0):
            try:
                results = self._index.search(query_vector[0].astype(np.float32), count=min(k * 2, len(self._id_to_label)))
                ids = []
                distances = []
                for match in results:
                    id_str = self._label_to_id.get(int(getattr(match, 'key', 0)), '')
                    if id_str:
                        ids.append(id_str)
                        distances.append(float(getattr(match, 'distance', 2.0)))
                if filter_ids:
                    filter_set = set(filter_ids)
                    filtered_ids = []
                    filtered_distances = []
                    for id_, dist in zip(ids, distances, strict=False):
                        if id_ in filter_set:
                            filtered_ids.append(id_)
                            filtered_distances.append(dist)
                            if len(filtered_ids) >= k:
                                break
                    return (filtered_ids, filtered_distances)
                return (ids[:k], distances[:k])
            except Exception as e:
                logger.error(f'USearch search failed, falling back to brute-force: {e}')
                return self._brute_force_search(query_vector[0], k, filter_ids)
        else:
            return self._brute_force_search(query_vector[0], k, filter_ids)

    def _brute_force_search(self, query_vector: np.ndarray, k: int, filter_ids: list[str] | None=None) -> tuple[list[str], list[float]]:
        """Brute-force search fallback."""
        if not self._vectors:
            return ([], [])
        candidates = filter_ids if filter_ids else list(self._vectors.keys())
        if not candidates:
            return ([], [])
        scores = []
        query_norm = np.linalg.norm(query_vector)
        for id_ in candidates:
            if id_ not in self._vectors:
                continue
            vec = self._vectors[id_]
            if self.space == 'cosine':
                vec_norm = np.linalg.norm(vec)
                if vec_norm == 0 or query_norm == 0:
                    similarity = 0.0
                else:
                    similarity = np.dot(query_vector, vec) / (query_norm * vec_norm)
                distance = 1.0 - similarity
            elif self.space in ('l2', 'euclidean'):
                distance = np.linalg.norm(query_vector - vec)
            elif self.space == 'ip':
                distance = -np.dot(query_vector, vec)
            else:
                distance = np.linalg.norm(query_vector - vec)
            scores.append((id_, distance))
        scores.sort(key=lambda x: x[1])
        ids = [s[0] for s in scores[:k]]
        distances = [s[1] for s in scores[:k]]
        return (ids, [float(d) for d in distances])

    def batch_search(self, query_vectors: np.ndarray, k: int=10, filter_ids: list[str] | None=None) -> list[tuple[list[str], list[float]]]:
        """
        Batch search for multiple query vectors using native usearch batch API.

        Args:
            query_vectors: Array of shape (n_queries, dim)
            k: Number of results per query
            filter_ids: Optional list of ids to filter results (not supported in batch, use post-filter)

        Returns:
            List of (ids, distances) tuples for each query
        """
        if query_vectors.ndim == 1:
            query_vectors = query_vectors.reshape(1, -1)

        # Use native usearch batch search (v2.26+ supports VectorOrVectorsLike)
        if self._available and self._is_initialized and self._index is not None:
            try:
                batch_results = self._index.search(query_vectors.astype(np.float32), count=k)
                results = []
                # BatchMatches supports indexing for individual query results
                for i in range(len(query_vectors)):
                    matches = batch_results[i] if hasattr(batch_results, '__iter__') else batch_results
                    ids = []
                    distances = []
                    for match in matches:
                        key = int(getattr(match, 'key', 0))
                        dist = float(getattr(match, 'distance', 2.0))
                        label_id = self._label_to_id.get(key, str(key))
                        if filter_ids is None or label_id in filter_ids:
                            ids.append(label_id)
                            distances.append(dist)
                    results.append((ids, distances))
                return results
            except Exception as e:
                logger.warning(f'Batch search failed, falling back to loop: {e}')

        # Fallback: sequential search with post-filtering
        results = []
        for query in query_vectors:
            ids, distances = self.search(query, k, filter_ids)
            results.append((ids, distances))
        return results

    def save_index(self, path: str | None=None) -> None:
        """
        Save index to disk.

        Args:
            path: Path to save index. Uses index_path from init if not provided.
        """
        save_path = path or self.index_path
        if not save_path:
            raise ValueError('No path provided for saving index')
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        if self._available and self._is_initialized and (self._index is not None):
            try:
                index_file = str(save_path / 'usearch_index.usearch')
                self._index.save(index_file)
                import orjson
                meta = {'id_to_label': self._id_to_label, 'label_to_id': self._label_to_id, 'current_label': self._current_label, 'dim': self.dim, 'max_elements': self.max_elements, 'M': self.M, 'ef_construction': self.ef_construction, 'ef_search': self.ef_search, 'space': self.space}
                (save_path / 'usearch_metadata.orjson').write_text(_msgspec_dumps_str(meta))
                logger.info(f'USearch index saved to {save_path}')
            except Exception as e:
                logger.error(f'Failed to save USearch index: {e}')
                raise
        if self._vectors:
            np.savez(save_path / 'vectors.npz', **dict(self._vectors.items()))

    def load_index(self, path: str | None=None) -> None:
        """
        Load index from disk.

        Args:
            path: Path to load index from. Uses index_path from init if not provided.
        """
        load_path = path or self.index_path
        if not load_path:
            raise ValueError('No path provided for loading index')
        load_path = Path(load_path)
        if not load_path.exists():
            raise FileNotFoundError(f'Index path not found: {load_path}')
        index_file = load_path / 'usearch_index.usearch'
        orjson_meta = load_path / 'usearch_metadata.orjson'
        if self._available and index_file.exists() and orjson_meta.exists():
            try:
                import orjson
                meta = _msgspec_loads(orjson_meta.read_bytes())
                self._id_to_label = meta['id_to_label']
                self._label_to_id = {int(k): v for k, v in meta['label_to_id'].items()}
                self._current_label = int(meta['current_label'])
                self.dim = int(meta['dim'])
                self.max_elements = int(meta['max_elements'])
                self.M = int(meta['M'])
                self.ef_construction = int(meta['ef_construction'])
                self.ef_search = int(meta['ef_search'])
                self.space = str(meta['space'])
                self._init_index()
                if self._index is not None:
                    self._index.load(index_file)
                logger.info(f'USearch index loaded from {load_path}')
                return
            except Exception as e:
                logger.error(f'Failed to load USearch index: {e}')
                self._available = False
        vectors_file = load_path / 'vectors.npz'
        if vectors_file.exists():
            try:
                data = np.load(vectors_file)
                for key in data.files:
                    self._vectors[key] = data[key].copy()
                logger.info(f'Loaded {len(self._vectors)} vectors from {vectors_file}')
            except Exception as e:
                logger.error(f'Failed to load vectors: {e}')
                raise

    def get_stats(self) -> dict[str, Any]:
        """
        Get index statistics.

        Returns:
            Dictionary with index statistics
        """
        stats = {'dim': self.dim, 'max_elements': self.max_elements, 'current_elements': len(self._id_to_label) if self._available else len(self._vectors), 'M': self.M, 'ef_construction': self.ef_construction, 'ef_search': self.ef_search, 'space': self.space, 'using_usearch': self._available and self._is_initialized, 'index_path': self.index_path, 'memory_usage_mb': self._estimate_memory_usage()}
        return stats

    def _estimate_memory_usage(self) -> float:
        """Estimate memory usage in MB."""
        if self._available and self._is_initialized:
            num_vectors = len(self._id_to_label)
            vector_memory = num_vectors * self.dim * 4 / (1024 * 1024)
            index_overhead = vector_memory * 2
            return vector_memory + index_overhead
        else:
            if not self._vectors:
                return 0.0
            sample_vec = next(iter(self._vectors.values()))
            bytes_per_vector = sample_vec.nbytes
            return len(self._vectors) * bytes_per_vector / (1024 * 1024)

    def update_ef_search(self, ef_search: int) -> None:
        """
        Update ef_search parameter for search quality/speed tradeoff.

        Args:
            ef_search: New ef_search value (higher = better recall, slower)
        """
        self.ef_search = ef_search

    def resize_index(self, new_max_elements: int) -> None:
        """
        Resize the index to accommodate more elements.

        Args:
            new_max_elements: New maximum number of elements
        """
        if new_max_elements <= self.max_elements:
            return
        self.max_elements = new_max_elements
        logger.debug(f'USearch index resize requested to {new_max_elements} (not directly supported)')

class RaptorNode(Struct, frozen=True):
    """Single node in RAPTOR summarization tree."""
    node_id: str
    level: int
    text: str
    embedding: list[float]
    child_ids: list[str] = field(default_factory=list)
    cluster_id: int = -1

    def to_dict(self) -> dict:
        return {'node_id': self.node_id, 'level': self.level, 'text': self.text[:500], 'embedding': self.embedding[:64], 'child_ids': self.child_ids, 'cluster_id': self.cluster_id}

class RAGEngine:
    """
    RAG engine s Ultra Context a SPR kompresí.

    ROLE: Grounding Authority (NOT identity/entity store)
    =====================================================
    - context grounding (hybrid_retrieve, HNSWVectorIndex, RAPTOR)
    - NENÍ owner identity/entity resolution → lancedb_store
    - NENÍ owner embedding cache → MLXEmbeddingManager singleton
    - Embedding policy: _fastembed_embedder (cached per-instance), fallback → MLXEmbeddingManager

    Features:
    - 6-stupňový pipeline: Query → Retrieval → Rerank → Compress → Generate → Validate
    - Ultra Context pro 50+ chunků
    - SPR Compression (50% redukce)
    - Secure Enclave pro citlivá data
    - Automatic ToT detection
    - HNSW Vector Search for fast approximate nearest neighbor search
    """
    __slots__ = tuple(('_bm25_hnsw_cache', '_bm25_hnsw_doc_count', '_coreml_embedder', '_document_map', '_enclave_status', '_hnsw_index', '_infinite_context', '_mlx_embedder', '_raptor_nodes', '_retriever', '_secure_enclave', '_spr_compressor', '_tantivy_fulltext', '_use_hnsw', 'config'))

    def __init__(self, config: RAGConfig | None=None):
        self.config = config or RAGConfig()
        self._infinite_context = None
        self._spr_compressor = None
        self._secure_enclave: SecureEnclaveBackend | None = None
        self._enclave_status: EnclaveStatus | None = None
        self._retriever = None
        self._hnsw_index: HNSWVectorIndex | None = None
        self._document_map: dict[str, Document] = {}
        self._use_hnsw = self.config.use_hnsw
        self._raptor_nodes: dict[str, RaptorNode] = {}
        self._coreml_embedder = None
        self._mlx_embedder = None
        # ISSUE-021-FIX #2: BM25 cache pro HNSW — avoids O(n) rebuild na kazde query
        # ISSUE-021-FIX #2: BM25 cache pro HNSW — avoids O(n) rebuild na kazde query
        self._bm25_hnsw_cache: BM25Index | TantivyFulltextIndex | None = None
        self._bm25_hnsw_doc_count: int = 0
        # ISSUE-011: Tantivy Rust fulltext index (mmap-backed, ~5MB RAM for 50K docs)
        self._tantivy_fulltext: TantivyFulltextIndex | None = None

    async def initialize(self) -> None:
        """Inicializovat RAG engine"""
        logger.info('Initializing RAGEngine...')
        if self.config.enable_ultra_context:
            await self._init_ultra_context()
        if self.config.enable_spr_compression:
            await self._init_spr_compressor()
        if self.config.enable_secure_enclave:
            await self._init_secure_enclave()
        await self._init_coreml_embedder()
        logger.info('✓ RAGEngine initialized')

    async def _init_coreml_embedder(self) -> None:
        """Initialize CoreML embedder via lazy import (compat seam).

        RAGEngine is grounding authority, NOT model owner.
        CoreML model lifecycle stays in brain/model_manager.py.
        This method is the ONLY entry point for model-plane coupling.
        """
        try:
            from hledac.universal.brain.model_manager import COREML_MODEL_PATH, get_model_manager
            coreml_available = COREML_MODEL_PATH is not None and COREML_MODEL_PATH.exists()
        except ImportError:
            coreml_available = False
        if not coreml_available:
            logger.debug('[COREML] CoreML model not available, skipping')
            return
        try:
            try:
                from embeddings.modernbert_embedder import ModernBERTEmbedder
                self._mlx_embedder = ModernBERTEmbedder()
            except ImportError:
                self._mlx_embedder = None
            mm = get_model_manager()
            self._coreml_embedder = mm._load_coreml_embedder()
            if self._coreml_embedder is not None:
                logger.info('[COREML] Using ANE-accelerated ModernBERT')
            else:
                logger.info('[COREML] CoreML not available, using MLX fallback')
        except Exception as e:
            logger.warning(f'[COREML] Failed to initialize embedder: {e}')
            self._mlx_embedder = None
            self._coreml_embedder = None

    async def _init_ultra_context(self) -> None:
        """Inicializovat InfiniteContextEngine"""
        try:
            from hledac.universal.ultra_context.infinite_context_engine import InfiniteContextEngine
            self._infinite_context = InfiniteContextEngine()
            logger.info('✓ Ultra Context initialized')
        except Exception as e:
            logger.warning(f'Ultra Context not available: {e}')

    async def _init_spr_compressor(self) -> None:
        """Inicializovat SPR Compressor"""
        try:
            from hledac.universal.ultra_context.spr_compressor import SPRCompressor, SPRConfig
            self._spr_compressor = SPRCompressor(SPRConfig(compression_ratio_target=0.5))
            logger.info('✓ SPR Compressor initialized (50% target)')
        except Exception as e:
            logger.warning(f'SPR Compressor not available: {e}')

    async def _init_secure_enclave(self) -> None:
        """Inicializovat Secure Enclave"""
        self._secure_enclave, self._enclave_status = await create_secure_enclave_backend(enabled=self.config.enable_secure_enclave)
        avail = self._enclave_status.availability
        if avail == EnclaveAvailability.DISABLED:
            logger.info('Secure Enclave disabled by config')
        elif avail == EnclaveAvailability.UNAVAILABLE:
            logger.warning(f'Secure Enclave unavailable: {self._enclave_status.error_message}')
        elif avail == EnclaveAvailability.AVAILABLE:
            logger.info(f'✓ Secure Enclave initialized ({self._enclave_status.backend_name})')
        else:
            logger.warning(f'Secure Enclave fail-soft: {self._enclave_status.error_message}')

    async def query(self, query: str, context_chunks: list[str], use_compression: bool | None=None, secure: bool=False) -> dict[str, Any]:
        """
        Procesovat RAG query.

        Args:
            query: Uživatelský dotaz
            context_chunks: Kontextové chunky
            use_compression: Použít kompresi (auto-detect pokud None)
            secure: Použít secure enclave

        Returns:
            Výsledek RAG query
        """
        if use_compression is None:
            use_compression = len(context_chunks) > self.config.compression_threshold
        logger.info(f'RAG query: {len(context_chunks)} chunks, compression={use_compression}')
        if use_compression and self._spr_compressor:
            context_chunks = await self._compress_chunks(context_chunks)
        if secure and self._secure_enclave:
            context_chunks = await self._secure_process(context_chunks)
        context = '\n\n'.join(context_chunks)
        is_complex = self._is_complex_query(query)
        return {'query': query, 'context': context, 'chunks_used': len(context_chunks), 'compressed': use_compression, 'secure': secure, 'complex': is_complex}

    async def _compress_chunks(self, chunks: list[str]) -> list[str]:
        """Komprimovat chunky pomocí SPR — paralelně přes bounded TaskGroup.

        M1 8GB: GRAPH_RAG limit (3,2,1,1) z ConcurrencyBudgetRegistry zamezuje
        Metal alloc pressure. 50 chunků × 10 ms serial → ~170 ms parallel při limit=3.

        Dynamic concurrency: adapts to memory pressure (lower = fewer concurrent).
        Per-chunk timeout: prevents one stuck chunk from blocking the entire batch.
        """
        if not self._spr_compressor:
            return chunks
        from hledac.universal.utils.asyncx import parallel, safe_wait_for
        _CHUNK_TIMEOUT_S = 5.0

        async def _compress_one(chunk: str) -> str:
            try:
                result = await safe_wait_for(self._spr_compressor.compress(chunk), timeout=_CHUNK_TIMEOUT_S, label='rag_compress')
                return result.compressed_text
            except asyncio.TimeoutError:
                logger.warning(f'Chunk compression timed out after {_CHUNK_TIMEOUT_S}s')
                return chunk
            except Exception as e:
                logger.warning(f'Compression failed: {e}')
                return chunk
        memory_pressure = getattr(self, '_memory_pressure', 0.0)
        if memory_pressure >= 0.8:
            concurrency = 1
        elif memory_pressure >= 0.5:
            concurrency = 2
        else:
            concurrency = 3
        coros = [_compress_one(chunk) for chunk in chunks]
        _build = await parallel(coros, concurrency=concurrency, policy="collect", ctx='rag:compress', logger_instance=logger)
        return _build.ok

    async def _secure_process(self, chunks: list[str]) -> list[str]:
        """
        Process chunks through Secure Enclave for batch attestation.

        IMPORTANT: This does NOT mutate chunk text. The enclave is used for
        hardware-backed attestation of chunk batch existence via signed digest.

        Architecture:
        - Build canonical BatchManifest (chunk_count, per-chunk SHA-256, batch_digest)
        - Request one signature for the batch digest (NOT per-chunk)
        - Store signature in enclave status for telemetry
        - Return chunks unchanged
        """
        if not self._secure_enclave or not self._enclave_status:
            return chunks
        if not self._secure_enclave.is_available():
            logger.debug('Secure Enclave backend not available, skipping')
            return chunks
        try:
            manifest = build_batch_manifest(chunks)
            signed = await self._secure_enclave.sign_batch_digest(manifest)
            self._enclave_status.signed_batch_digest = signed.signature.hex()
            self._enclave_status.chunk_count = manifest.chunk_count
            self._enclave_status.availability = EnclaveAvailability.SIGNED
            logger.debug(f'Secure Enclave: signed batch digest for {manifest.chunk_count} chunks')
        except SecureEnclaveError as e:
            logger.warning(f'Secure Enclave signing failed (fail-soft): {e}')
            self._enclave_status.availability = EnclaveAvailability.FAIL_SOFT
            self._enclave_status.error_message = str(e)
        except Exception as e:
            logger.warning(f'Secure Enclave unexpected error (fail-soft): {e}')
            self._enclave_status.availability = EnclaveAvailability.FAIL_SOFT
            self._enclave_status.error_message = str(e)
        return chunks

    def _is_complex_query(self, query: str) -> bool:
        """Detekovat komplexní dotaz pro Tree of Thoughts"""
        complex_indicators = ['and', 'then', 'compare', 'contrast', 'analyze', 'why', 'how does', 'relationship', 'impact']
        return any((ind in query.lower() for ind in complex_indicators))

    async def hybrid_retrieve(self, query: str, documents: list[Document], top_k: int | None=None, filters: dict[str, Any] | None=None) -> list[RetrievedChunk]:
        """
        Retrieve relevant documents using hybrid search (dense + sparse).

        ISSUE-021: Parallel retrieval — embed + BM25 paralelně přes asyncio.gather.
        embed(query + docs) a BM25.index_build běží concurrent:
        - MLX GPU embed: [query] + [doc_contents] v jednom batch call
        - CPU: BM25 add_documents v thread pool
        - Po embed dokončení: dense_retrieval + sparse BM25.search → fusion

        Args:
            query: Search query
            documents: List of documents to search
            top_k: Number of results to return
            filters: Optional metadata filters

        Returns:
            List of retrieved chunks with scores
        """
        if not self.config.enable_hybrid_retrieval:
            return [RetrievedChunk(document=doc, chunk_text=doc.content[:self.config.chunk_size], final_score=1.0) for doc in documents[:top_k or 5]]
        top_k = top_k or 10
        # ISSUE-011: Tantivy Rust fulltext with transparent Python BM25 fallback.
        # TantivyFulltextIndex auto-detects Rust availability and uses mmap-backed
        # Tantivy when available (~5ms search vs ~200ms Python BM25 for 50K docs).
        bm25 = TantivyFulltextIndex(
            k1=self.config.bm25_k1, b=self.config.bm25_b,
            index_path=_DEFAULT_FULLTEXT_INDEX_DIR,
        )

        # ISSUE-021 + ISSUE-021-FIX: Paralelní init — BM25 build (CPU) a embed (GPU) concurrent.
        # Fallback SHA256 embeddings pokud MLX embed selže — BM25 výsledky neplýtváme.
        # NOTE: _bm25_build je sync (asyncio.to_thread() ne协调 async funkce správně)
        def _bm25_build() -> None:
            for doc in documents:
                bm25.add_document(doc)

        doc_contents = [d.content for d in documents]
        embed_coro = self._generate_embeddings([query] + doc_contents)
        build_result = await parallel(
            [embed_coro, asyncio.to_thread(_bm25_build)],
            policy="log",
            ctx="rag:hybrid_init",
        )
        all_embeddings: list[list[float]] = build_result.ok[0] if build_result.ok else []
        # ISSUE-021-FIX #1: Sha256 fallback embeddings — BM25 už je postaven (thread pool dokončil)
        if not all_embeddings:
            all_embeddings = [
                [float(b) / 255.0 for b in hashlib.sha256(t.encode()).digest()]
                for t in [query] + doc_contents
            ]
        query_embedding = all_embeddings[0]
        doc_embeddings_list = all_embeddings[1:]
        doc_embeddings = {doc.id: doc_embeddings_list[i] for i, doc in enumerate(documents)}

        # Dense + sparse retrieval — F350M-R: parallel() replaces asyncio.gather (oba CPU-bound)
        # ISSUE-S3: sequential → parallel: dense_retrieval (numpy dot) + BM25.search běží concurrent
        dense_coro = asyncio.to_thread(self._dense_retrieval, query_embedding, doc_embeddings, top_k * 2)
        sparse_coro = asyncio.to_thread(bm25.search, query, top_k=top_k * 2)
        hybrid_results = await parallel(
            [dense_coro, sparse_coro],
            policy="collect",
            concurrency=2,
            ctx="rag_engine:dense_sparse_retrieval",
        )
        dense_results = hybrid_results[0] if len(hybrid_results) > 0 else []
        sparse_results = hybrid_results[1] if len(hybrid_results) > 1 else []
        sparse_doc_ids = [(bm25.documents[idx].id, score) for idx, score in sparse_results]
        doc_scores: dict[str, dict[str, float]] = _dense_sparse_factory.copy()
        for doc_id, score in dense_results:
            doc_scores[doc_id]['dense'] = score
        max_sparse = max([s for _, s in sparse_doc_ids], default=1.0)
        for doc_id, score in sparse_doc_ids:
            doc_scores[doc_id]['sparse'] = score / max_sparse if max_sparse > 0 else 0
        results: list[RetrievedChunk] = []
        doc_map = {d.id: d for d in documents}
        for doc_id, scores in doc_scores.items():
            if doc_id not in doc_map:
                continue
            doc = doc_map[doc_id]
            if filters and (not self._matches_filters(doc, filters)):
                continue
            final_score = self.config.dense_weight * scores['dense'] + self.config.sparse_weight * scores['sparse']
            chunk = RetrievedChunk(document=doc, chunk_text=doc.content[:self.config.chunk_size], dense_score=scores['dense'], sparse_score=scores['sparse'], final_score=final_score)
            results.append(chunk)
        results.sort(key=attrgetter("final_score"), reverse=True)
        return results[:top_k]

    async def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using UnifiedEmbeddingManager (MLX primary).

        Priority: MLXEmbeddingManager (ModernBERT) → SHA256 hash fallback.
        FastEmbed removed — unified MLX is faster on M1 8GB.

        M1 8GB: MLXEmbeddingManager runs on GPU via unified memory, no CPU transfer.
        """
        if not hasattr(self, '_mlx_embedder') or self._mlx_embedder is None:
            try:
                from hledac.universal.brain.unified_embedding_manager import get_unified_embedder
                self._mlx_embedder = get_unified_embedder(dim=512)
            except Exception as e:
                logger.debug('[MLX] UnifiedEmbeddingManager init failed: %s', e)
                self._mlx_embedder = False
        if self._mlx_embedder and hasattr(self._mlx_embedder, 'embed'):
            try:
                result = self._mlx_embedder.embed(texts)
                return result
            except Exception as e:
                logger.warning('[MLX] embed failed: %s', e)
        return [[float(digest[i % 32]) / 255.0 for i in range(512)] for t in texts for digest in [hashlib.sha256(t.encode()).digest()]]

    def _dense_retrieval(self, query_embedding: list[float], doc_embeddings: dict[str, list[float]], top_k: int) -> list[tuple[str, float]]:
        """Dense retrieval using cosine similarity."""
        import numpy as np
        scores = []
        query_norm = np.linalg.norm(query_embedding)
        for doc_id, doc_embedding in doc_embeddings.items():
            doc_norm = np.linalg.norm(doc_embedding)
            if doc_norm == 0 or query_norm == 0:
                similarity = 0.0
            else:
                similarity = np.dot(query_embedding, doc_embedding) / (query_norm * doc_norm)
            scores.append((doc_id, float(similarity)))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _matches_filters(self, doc: Document, filters: dict[str, Any]) -> bool:
        """Check if document matches filters."""
        for key, value in filters.items():
            if doc.metadata.get(key) != value:
                return False
        return True

    def build_hnsw_index(self, documents: list[Document], embeddings: dict[str, list[float]] | None=None) -> None:
        """
        Build HNSW index from documents.

        Args:
            documents: List of documents to index
            embeddings: Optional pre-computed embeddings {doc_id: embedding}
                       If not provided, embeddings will be generated
        """
        if not documents:
            logger.warning('No documents provided for HNSW indexing')
            return
        logger.info(f'Building HNSW index for {len(documents)} documents...')
        self._hnsw_index = HNSWVectorIndex(dim=self.config.hnsw_dim, max_elements=self.config.hnsw_max_elements, M=self.config.hnsw_M, ef_construction=self.config.hnsw_ef_construction, ef_search=self.config.hnsw_ef_search, space=self.config.hnsw_space, index_path=self.config.hnsw_index_path)
        self._document_map = {doc.id: doc for doc in documents}
        # ISSUE-021-FIX #2: Invalidate BM25 cache when documents change
        self._bm25_hnsw_cache = None
        self._bm25_hnsw_doc_count = 0
        if embeddings is None:
            logger.info('Generating embeddings for HNSW index...')
            try:
                # P1-1: asyncio.run() inside sync method is M1 Metal crash vector.
                # _generate_embeddings is async but its internals are sync MLX — use bridge.
                from hledac.universal.utils.sync_bridge import run_sync_async
                embeddings_list = run_sync_async(self._generate_embeddings([d.content for d in documents]))
                embeddings = {doc.id: emb for doc, emb in zip(documents, embeddings_list)}
            except Exception as e:
                logger.error(f'Failed to generate embeddings: {e}')
                return
        valid_ids = []
        valid_vectors = []
        for doc in documents:
            if doc.id in embeddings:
                valid_ids.append(doc.id)
                valid_vectors.append(embeddings[doc.id])
            elif doc.embedding:
                valid_ids.append(doc.id)
                valid_vectors.append(doc.embedding)
        if not valid_vectors:
            logger.warning('No valid embeddings found for HNSW indexing')
            return
        vectors_array = np.array(valid_vectors, dtype=np.float32)

        # ── SILICON-02: Try GPU-accelerated vector insertion ──────
        _gpu_inserted = False
        if FeatureFlags.get(FeatureFlag.METAL_HNSW):
            try:
                from hledac.universal.knowledge.metal_hnsw import MetalHNSWBuilder
                gpu_builder = MetalHNSWBuilder(
                    dim=self.config.hnsw_dim,
                    M=self.config.hnsw_M,
                    ef_construction=self.config.hnsw_ef_construction,
                    max_elements=self.config.hnsw_max_elements,
                    metric=self.config.hnsw_space,
                )
                if gpu_builder.gpu_enabled:
                    # Build GPU-accelerated index with labels matching
                    # HNSWVectorIndex._current_label so search results
                    # resolve to correct document IDs.
                    label_start = self._hnsw_index._current_label
                    gpu_index = gpu_builder.build(
                        vectors_array, valid_ids, label_offset=label_start
                    )
                    # Replace internal USearch index and populate mappings.
                    # Labels in gpu_index are label_start..label_start+N-1,
                    # matching _label_to_id keys populated below.
                    self._hnsw_index._index = gpu_index
                    for i, doc_id in enumerate(valid_ids):
                        label = label_start + i
                        self._hnsw_index._id_to_label[doc_id] = label
                        self._hnsw_index._label_to_id[label] = doc_id
                    self._hnsw_index._current_label = label_start + len(valid_ids)
                    self._hnsw_index._is_initialized = True
                    _gpu_inserted = True
                    gpu_stats = gpu_builder.get_stats()
                    logger.info(
                        f"HNSW index built (Metal GPU): {gpu_stats['total_vectors']} vectors, "
                        f"{gpu_stats['build_time_s']:.2f}s "
                        f"(gpu_batches={gpu_stats['gpu_batches']}, "
                        f"label_range=[{label_start}, {label_start + len(valid_ids) - 1}])"
                    )
            except ImportError:
                logger.debug("[HNSW] Metal HNSW module not available")
            except Exception as e:
                logger.debug(f"[HNSW] Metal GPU build failed: {e}")

        if not _gpu_inserted:
            self._hnsw_index.add_vectors(vectors_array, valid_ids)
        stats = self._hnsw_index.get_stats()
        logger.info(f"HNSW index built: {stats['current_elements']} vectors, {stats['memory_usage_mb']:.2f} MB, HNSW enabled: {stats['using_hnsw']}")

    def enable_hnsw(self, enable: bool=True) -> None:
        """
        Enable or disable HNSW search.

        Args:
            enable: True to enable HNSW, False to use brute-force
        """
        self._use_hnsw = enable
        logger.info(f"HNSW search {('enabled' if enable else 'disabled')}")

    def _hnsw_retrieval(self, query_embedding: list[float] | np.ndarray, top_k: int=10, filters: dict[str, Any] | None=None) -> list[RetrievedChunk]:
        """
        Retrieve documents using HNSW index.

        Args:
            query_embedding: Query embedding vector
            top_k: Number of results to return
            filters: Optional metadata filters

        Returns:
            List of retrieved chunks with scores
        """
        if self._hnsw_index is None:
            logger.warning('HNSW index not built, cannot perform retrieval')
            return []
        if isinstance(query_embedding, list):
            query_embedding = np.array(query_embedding, dtype=np.float32)
        filter_ids = None
        if filters:
            filter_ids = [doc_id for doc_id, doc in self._document_map.items() if self._matches_filters(doc, filters)]
            if not filter_ids:
                return []
        ids, distances = self._hnsw_index.search(query_embedding, top_k, filter_ids)
        results = []
        for doc_id, distance in zip(ids, distances, strict=False):
            if doc_id not in self._document_map:
                continue
            doc = self._document_map[doc_id]
            if self.config.hnsw_space == 'cosine':
                similarity = 1.0 - distance
            elif self.config.hnsw_space in ('l2', 'euclidean'):
                similarity = 1.0 / (1.0 + distance)
            elif self.config.hnsw_space == 'ip':
                similarity = -distance
            else:
                similarity = 1.0 - distance
            chunk = RetrievedChunk(document=doc, chunk_text=doc.content[:self.config.chunk_size], dense_score=float(similarity), sparse_score=0.0, final_score=float(similarity))
            results.append(chunk)
        return results

    async def hybrid_retrieve_with_hnsw(self, query: str, documents: list[Document] | None=None, top_k: int | None=None, filters: dict[str, Any] | None=None, use_hnsw: bool | None=None) -> list[RetrievedChunk]:
        """
        Retrieve relevant documents using hybrid search (dense + sparse) with optional HNSW.

        This is an enhanced version of hybrid_retrieve that uses HNSW for fast
        dense retrieval when available.

        Args:
            query: Search query
            documents: List of documents to search (only needed if HNSW not built)
            top_k: Number of results to return
            filters: Optional metadata filters
            use_hnsw: Override HNSW usage (None = use config setting)

        Returns:
            List of retrieved chunks with scores
        """
        should_use_hnsw = use_hnsw if use_hnsw is not None else self._use_hnsw
        if should_use_hnsw and self._hnsw_index is not None:
            return await self._hybrid_retrieve_hnsw(query, top_k, filters)
        if documents is None:
            raise ValueError('Documents required when HNSW index not built')
        return await self.hybrid_retrieve(query, documents, top_k, filters)

    async def _hybrid_retrieve_hnsw(self, query: str, top_k: int | None=None, filters: dict[str, Any] | None=None) -> list[RetrievedChunk]:
        """
        Internal hybrid retrieval using HNSW for dense search.

        ISSUE-021: Paralelní — embed(query) + BM25.build běží concurrent.
        ANN HNSW search (Rust, GIL-free) běží sequential po embed.
        """
        top_k = top_k or 10

        # ISSUE-021-FIX #2 + ISSUE-011: BM25 cache with Tantivy mmap-backed index.
        # TantivyFulltextIndex auto-detects Rust availability; falls back to Python
        # BM25Index API-compatibly when the Rust fulltext module is not compiled.
        doc_count = len(self._document_map)
        if self._bm25_hnsw_cache is None or self._bm25_hnsw_doc_count != doc_count:
            # Cache miss: rebuild fulltext index (Tantivy mmap or Python BM25 fallback)
            bm25 = TantivyFulltextIndex(
                k1=self.config.bm25_k1, b=self.config.bm25_b,
                index_path=_DEFAULT_FULLTEXT_INDEX_DIR,
            )

            def _bm25_build() -> None:
                for doc in self._document_map.values():
                    bm25.add_document(doc)

            embed_coro = self._generate_embeddings([query])
            build_result = await parallel(
                [embed_coro, asyncio.to_thread(_bm25_build)],
                policy="log",
                ctx="rag:hnsw_init",
            )
            all_embeddings: list[list[float]] = build_result.ok[0] if build_result.ok else []
            # ISSUE-021-FIX #1: Sha256 fallback — BM25 už je postaven
            if not all_embeddings:
                all_embeddings = [
                    [float(b) / 255.0 for b in hashlib.sha256(query.encode()).digest()]
                ]
            query_embedding = all_embeddings[0]
            # Uložit do cache
            self._bm25_hnsw_cache = bm25
            self._bm25_hnsw_doc_count = doc_count
        else:
            # Cache hit: použít cached BM25, embed pouze query
            bm25 = self._bm25_hnsw_cache
            embed_coro = self._generate_embeddings([query])
            build_result = await parallel(
                [embed_coro],
                policy="log",
                ctx="rag:hnsw_embed",
            )
            all_embeddings: list[list[float]] = build_result.ok[0] if build_result.ok else []
            if not all_embeddings:
                all_embeddings = [
                    [float(b) / 255.0 for b in hashlib.sha256(query.encode()).digest()]
                ]
            query_embedding = all_embeddings[0]

        # ANN HNSW search (Rust/GIL-free) + BM25 search — F350M-R: parallel() replaces asyncio.gather
        # ISSUE-S3: sequential → parallel: HNSW search + BM25.search běží concurrent
        dense_coro = asyncio.to_thread(self._hnsw_retrieval, query_embedding, top_k * 2, filters)
        sparse_coro = asyncio.to_thread(bm25.search, query, top_k=top_k * 2)
        hybrid_results = await parallel(
            [dense_coro, sparse_coro],
            policy="collect",
            concurrency=2,
            ctx="rag_engine:hnsw_bm25_retrieval",
        )
        dense_results = hybrid_results[0] if len(hybrid_results) > 0 else []
        sparse_results = hybrid_results[1] if len(hybrid_results) > 1 else []
        sparse_doc_ids = [(bm25.documents[idx].id, score) for idx, score in sparse_results]
        doc_scores: dict[str, dict[str, float]] = _dense_sparse_factory.copy()
        for chunk in dense_results:
            doc_scores[chunk.document.id]['dense'] = chunk.dense_score
        max_sparse = max([s for _, s in sparse_doc_ids], default=1.0)
        for doc_id, score in sparse_doc_ids:
            doc_scores[doc_id]['sparse'] = score / max_sparse if max_sparse > 0 else 0
        results: list[RetrievedChunk] = []
        for doc_id, scores in doc_scores.items():
            if doc_id not in self._document_map:
                continue
            doc = self._document_map[doc_id]
            if filters and (not self._matches_filters(doc, filters)):
                continue
            final_score = self.config.dense_weight * scores['dense'] + self.config.sparse_weight * scores['sparse']
            chunk = RetrievedChunk(document=doc, chunk_text=doc.content[:self.config.chunk_size], dense_score=scores['dense'], sparse_score=scores['sparse'], final_score=final_score)
            results.append(chunk)
        results.sort(key=attrgetter("final_score"), reverse=True)
        return results[:top_k]

    def save_hnsw_index(self, path: str | None=None) -> None:
        """
        Save HNSW index to disk.

        Args:
            path: Path to save index. Uses config.hnsw_index_path if not provided.
        """
        if self._hnsw_index is None:
            raise ValueError('HNSW index not built')
        save_path = path or self.config.hnsw_index_path
        if not save_path:
            raise ValueError('No path provided for saving index')
        self._hnsw_index.save_index(save_path)
        try:
            doc_map_path = Path(save_path) / 'document_map.json'
            with open(doc_map_path, 'w') as f:
                f.write(_msgspec_dumps_str(self._document_map))
        except ImportError:
            import orjson
            doc_map_path = Path(save_path) / 'document_map.json'
            with open(doc_map_path, 'wb') as f:
                f.write(orjson.dumps(self._document_map))
        logger.info(f'HNSW index and document map saved to {save_path}')

    def load_hnsw_index(self, path: str | None=None) -> None:
        """
        Load HNSW index from disk.

        Args:
            path: Path to load index from. Uses config.hnsw_index_path if not provided.
        """
        load_path = path or self.config.hnsw_index_path
        if not load_path:
            raise ValueError('No path provided for loading index')
        if self._hnsw_index is None:
            self._hnsw_index = HNSWVectorIndex(dim=self.config.hnsw_dim, max_elements=self.config.hnsw_max_elements, M=self.config.hnsw_M, ef_construction=self.config.hnsw_ef_construction, ef_search=self.config.hnsw_ef_search, space=self.config.hnsw_space, index_path=load_path)
        self._hnsw_index.load_index(load_path)
        doc_map_path = Path(load_path) / 'document_map.json'
        if doc_map_path.exists():
            try:
                with open(doc_map_path, 'rb') as f:
                    self._document_map = _msgspec_loads(f.read())
            except ImportError:
                import orjson
                with open(doc_map_path, 'rb') as f:
                    self._document_map = orjson.loads(f.read())
        logger.info(f'HNSW index loaded from {load_path}')

    def get_hnsw_stats(self) -> dict[str, Any] | None:
        """
        Get HNSW index statistics.

        Returns:
            Dictionary with index statistics, or None if index not built
        """
        if self._hnsw_index is None:
            return None
        return self._hnsw_index.get_stats()

    async def _get_random_chunks(self, n: int) -> list[str]:
        """Return up to n random text chunks from documents."""
        import random
        if not self._document_map:
            return []
        docs = list(self._document_map.values())
        if len(docs) <= n:
            return [doc.content for doc in docs]
        sampled = random.sample(docs, n)
        return [doc.content for doc in sampled]

    async def _ensure_coreml_model(self) -> bool:
        """
        Convert ModernBERT to CoreML if not already done.
        Returns True if conversion succeeded or already exists.
        """
        if COREML_MODEL_PATH is None:
            return False
        if COREML_MODEL_PATH.exists():
            return True
        if self._mlx_embedder is None:
            logger.warning('[COREML] No MLX embedder for conversion')
            return False
        try:
            chunks = await self._get_random_chunks(500)
            if len(chunks) < 100:
                logger.warning('[COREML] Not enough chunks for accuracy test')
                return False
            original_embs = []
            for chunk in chunks[:100]:
                emb = await self._mlx_embedder.embed(chunk) if hasattr(self._mlx_embedder, 'embed') else None
                if emb is not None:
                    original_embs.append(np.array(emb))
            if len(original_embs) < 50:
                logger.warning('[COREML] Not enough embeddings for test')
                return False
            logger.info('[COREML] Skipping conversion - accuracy test not implemented in mock')
            return False
        except Exception as e:
            logger.warning(f'[COREML] Accuracy test failed: {e}')
            return False

    async def _embed_text(self, text: str) -> list[float]:
        """Embed text using CoreML if available, fallback to MLX."""
        if self._coreml_embedder is not None:
            try:
                import numpy as np
                input_dict = {'input': np.array([text])}
                result = self._coreml_embedder.predict(input_dict)
                if isinstance(result, dict):
                    output = None
                    for key in ('output', 'embedding', 'last_hidden_state', 'hidden_state'):
                        if key in result:
                            output = result[key]
                            break
                    if output is None:
                        output = list(result.values())[0]
                else:
                    output = result
                if hasattr(output, 'tolist'):
                    output = output.tolist()  # type: ignore[union-size]
                embedding = []
                while isinstance(output, list) and output:
                    if isinstance(output[0], list):
                        output = output[0]
                    else:
                        embedding = output
                        break
                return embedding
            except Exception as e:
                logger.warning(f'[COREML] Inference failed, falling back to MLX: {e}')
                self._coreml_embedder = None
        embeddings = await self._generate_embeddings([text])
        return embeddings[0] if embeddings else []

    async def _build_raptor_tree(self, documents: list[Document], max_levels: int=2, max_docs: int=50) -> dict[str, RaptorNode]:
        """Build RAPTOR summarization tree. Returns node_id -> RaptorNode dict."""
        docs = documents[:max_docs]
        if len(docs) < 3:
            return {}
        nodes: dict[str, RaptorNode] = {}
        current_level_texts: list[str] = []
        current_level_embeddings: list[list[float]] = []
        for i, doc in enumerate(docs):
            node_id = f'raptor_L0_{i}'
            try:
                embedding = await self._embed_text(doc.content)
            except Exception:
                continue
            node = RaptorNode(node_id=node_id, level=0, text=doc.content[:2000], embedding=embedding)
            nodes[node_id] = node
            current_level_texts.append(doc.content[:2000])
            current_level_embeddings.append(embedding)
        for level in range(1, max_levels + 1):
            if len(current_level_embeddings) < 3:
                break
            try:
                from sklearn.decomposition import PCA
                pca = PCA(n_components=2)
                reduced = pca.fit_transform(np.array(current_level_embeddings))
            except Exception as e:
                logger.warning(f'[RAPTOR] PCA failed at level {level}: {e}')
                break
            n_clusters = max(2, min(8, len(current_level_embeddings) // 3))
            try:
                from sklearn.mixture import GaussianMixture
                gm = GaussianMixture(n_components=n_clusters, random_state=42, max_iter=50)
                cluster_labels = gm.fit_predict(reduced)
            except Exception as e:
                logger.warning(f'[RAPTOR] GMM failed at level {level}: {e}')
                break
            prev_level_node_ids = [nid for nid, n in nodes.items() if n.level == level - 1]
            new_texts: list[str] = []
            new_embeddings: list[list[float]] = []
            for cluster_id in range(n_clusters):
                cluster_indices = [i for i, l in enumerate(cluster_labels) if l == cluster_id]
                if not cluster_indices:
                    continue
                cluster_texts = [current_level_texts[i] for i in cluster_indices]
                combined = '\n\n'.join(cluster_texts[:5])[:3000]
                summary = await self._summarize_cluster(combined, max_tokens=200)
                node_id = f'raptor_L{level}_c{cluster_id}'
                try:
                    embedding = await self._embed_text(summary)
                except Exception:
                    continue
                child_ids = [prev_level_node_ids[i] for i in cluster_indices if i < len(prev_level_node_ids)]
                nodes[node_id] = RaptorNode(node_id=node_id, level=level, text=summary, embedding=embedding, child_ids=child_ids, cluster_id=cluster_id)
                new_texts.append(summary)
                new_embeddings.append(embedding)
            current_level_texts = new_texts
            current_level_embeddings = new_embeddings
        return nodes

    async def _summarize_cluster(self, text: str, max_tokens: int=200) -> str:
        """Summarize cluster text via Hermes3 generate_structured(). Truncates on failure."""
        # ISSUE [LLM-SEC-001]: Sanitize cluster text before LLM consumption
        sanitize = lazy('.prompt_injection_validator.sanitize_for_llm')()
        text = sanitize(text[:3000]) if sanitize else text[:3000]

        hermes = getattr(self, '_model_manager', None) or getattr(self, '_llm', None) or getattr(self, '_hermes_engine', None)
        if hermes is None:
            return text[:500]
        try:
            result = await hermes.generate_structured(prompt=f'Summarize the following research findings concisely:\n\n{text}', response_model=dict, max_tokens=max_tokens, priority=0.5)
            if isinstance(result, dict) and 'summary' in result:
                return result['summary'].strip()
            if isinstance(result, str):
                return result.strip()
            return text[:500]
        except Exception as e:
            logger.warning(f'[RAPTOR] Cluster summarization failed: {e}')
            return text[:500]

    def _raptor_retrieve(self, query_embedding: list[float], nodes: dict[str, RaptorNode], top_k: int=5) -> list[RaptorNode]:
        """Retrieve top-K nodes from all RAPTOR levels by cosine similarity."""
        import numpy as np
        if not nodes:
            return []
        q = np.array(query_embedding)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        scores: list[tuple[float, RaptorNode]] = []
        for node in nodes.values():
            if not node.embedding:
                continue
            v = np.array(node.embedding)
            v_norm = np.linalg.norm(v)
            if v_norm == 0:
                continue
            sim = float(np.dot(q, v) / (q_norm * v_norm))
            scores.append((sim, node))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [node for _, node in scores[:top_k]]

    def _rrf_merge(self, list_a: list[Any], list_b: list[Any], top_k: int=10, k: int=60) -> list[Any]:
        """Merge two ranked lists via Reciprocal Rank Fusion. Stable key = hash of content."""

        def _item_key(item) -> str:
            url = getattr(item, 'url', None) or getattr(item, 'source_url', None)
            if url:
                return str(url)
            content = getattr(item, 'content', None) or getattr(item, 'text', None) or str(item)
            return hashlib.md5(content[:200].encode(errors='ignore')).hexdigest()
        scores: dict[str, float] = {}
        items: dict[str, Any] = {}
        for rank, item in enumerate(list_a):
            key = _item_key(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            items[key] = item
        for rank, item in enumerate(list_b):
            key = _item_key(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            items[key] = item
        sorted_keys = sorted(scores.keys(), key=lambda k_: scores[k_], reverse=True)
        return [items[k] for k in sorted_keys[:top_k]]

    async def close(self) -> None:
        """Zavřít engine"""
        logger.info('Closing RAGEngine...')
        self._infinite_context = None
        self._spr_compressor = None
        self._secure_enclave = None
        self._hnsw_index = None
        self._document_map.clear()
        logger.info('✓ RAGEngine closed')