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

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Secure Enclave abstraction (Sprint F206X)
import numpy as np
from hledac.universal.security.secure_enclave import (
    EnclaveAvailability,
    EnclaveStatus,
    SecureEnclaveBackend,
    SecureEnclaveError,
    build_batch_manifest,
    create_secure_enclave_backend,
)

# Sprint F800C: Lazy CoreML import — contained to compat seam only.
# RAGEngine is grounding authority, NOT model owner.
# Model lifecycle ownership stays in brain/model_manager.py.
# Moved from top-level to lazy to eliminate eager model-plane coupling.
COREML_AVAILABLE = False
COREML_MODEL_PATH = None

# Optional rank_bm25 for faster BM25 (Fix 4)
try:
    from rank_bm25 import BM25Okapi as _RankBM25
    RANK_BM25_AVAILABLE = True
except ImportError:
    _RankBM25 = None
    RANK_BM25_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class RAGConfig:
    """Konfigurace pro RAG"""
    enable_ultra_context: bool = True
    enable_spr_compression: bool = True
    enable_secure_enclave: bool = True
    compression_threshold: int = 50  # Počet chunků pro aktivaci komprese
    max_tokens: int = 128000  # Maximální kontext

    # Hybrid retrieval
    enable_hybrid_retrieval: bool = True
    dense_weight: float = 0.5  # Weight for dense retrieval
    sparse_weight: float = 0.5  # Weight for sparse retrieval (BM25)
    bm25_k1: float = 1.5  # BM25 parameter
    bm25_b: float = 0.75  # BM25 parameter
    chunk_size: int = 512
    chunk_overlap: int = 128

    # HNSW Vector Search configuration
    use_hnsw: bool = True
    hnsw_dim: int = 384  # Vector dimension (BAAI/bge-small-en-v1.5 produces 384D)
    hnsw_max_elements: int = 100000  # Maximum elements in index
    hnsw_M: int = 16  # Number of bi-directional links for each node
    hnsw_ef_construction: int = 200  # Size of dynamic candidate list
    hnsw_ef_search: int = 50  # Size of dynamic candidate list for search
    hnsw_index_path: str | None = None  # Path for persistent index storage
    hnsw_space: str = "cosine"  # Distance metric: "cosine", "l2", "ip"


@dataclass
class Document:
    """Document for retrieval"""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None

    def __hash__(self):
        return hash(self.id)


@dataclass
class RetrievedChunk:
    """Retrieved document chunk with scores"""
    document: Document
    chunk_text: str
    dense_score: float = 0.0
    sparse_score: float = 0.0
    final_score: float = 0.0


class BM25Index:
    """Simple BM25 implementation for sparse retrieval"""

    # Sprint F206L: Bounded document count to prevent unbounded term_doc_freqs growth
    MAX_BM25_DOCUMENTS: int = 50000

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: list[Document] = []
        self.doc_freqs: dict[str, int] = defaultdict(int)
        self.doc_lengths: list[int] = []
        self.avg_doc_length: float = 0.0
        self.term_doc_freqs: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.doc_count: int = 0
        # rank_bm25 library for faster BM25 (Fix 4)
        self._rank_bm25 = None

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization"""
        return re.findall(r'\b[a-zA-Z]+\b', text.lower())

    def add_document(self, doc: Document):
        """Add document to index. Silently drops if MAX_BM25_DOCUMENTS reached."""
        if len(self.documents) >= self.MAX_BM25_DOCUMENTS:
            return  # Fail-soft: ignore new documents beyond bound
        tokens = self._tokenize(doc.content)
        doc_length = len(tokens)

        self.documents.append(doc)
        self.doc_lengths.append(doc_length)

        # Count term frequencies in document
        term_counts = defaultdict(int)
        for token in tokens:
            term_counts[token] += 1

        # Update global statistics
        for term in term_counts:
            self.doc_freqs[term] += 1
            self.term_doc_freqs[term][len(self.documents) - 1] = term_counts[term]

        self.doc_count = len(self.documents)
        self.avg_doc_length = sum(self.doc_lengths) / self.doc_count if self.doc_count > 0 else 0

        # Initialize rank_bm25 if available (Fix 4)
        if RANK_BM25_AVAILABLE:
            tokenized_corpus = [self._tokenize(doc.content) for doc in self.documents]
            self._rank_bm25 = _RankBM25(tokenized_corpus)

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Search documents using BM25"""
        if not self.documents:
            return []

        query_tokens = self._tokenize(query)

        # Use numpy for both paths
        import numpy as np

        # Use rank_bm25 library if available (Fix 4)
        if self._rank_bm25 is not None:
            scores = self._rank_bm25.get_scores(query_tokens)
            top_indices = np.argsort(scores)[::-1][:top_k]
            return [(int(idx), float(scores[idx])) for idx in top_indices if scores[idx] > 0]

        # Fallback to pure Python implementation
        scores = np.zeros(self.doc_count)

        for term in query_tokens:
            if term not in self.doc_freqs:
                continue

            idf = np.log(
                (self.doc_count - self.doc_freqs[term] + 0.5) /
                (self.doc_freqs[term] + 0.5) + 1
            )

            for doc_id, term_freq in self.term_doc_freqs[term].items():
                doc_length = self.doc_lengths[doc_id]
                numerator = term_freq * (self.k1 + 1)
                denominator = term_freq + self.k1 * (
                    1 - self.b + self.b * (doc_length / self.avg_doc_length)
                )
                scores[doc_id] += idf * (numerator / denominator)

        # Get top-k
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(int(idx), float(scores[idx])) for idx in top_indices if scores[idx] > 0]


class HNSWVectorIndex:
    """
    HNSW (Hierarchical Navigable Small World) Vector Index for fast approximate
    nearest neighbor search.

    Uses hnswlib for C++ optimized approximate nearest neighbor search with:
    - <1ms search latency for 100K vectors
    - ~100MB memory per 100K 768-dim vectors
    - Dynamic index updates
    - Persistent storage support

    M1 8GB Optimized:
    - Configurable max_elements to control memory usage
    - Optional memory-mapped indices
    - Efficient C++ backend
    """

    def __init__(
        self,
        dim: int = 768,
        max_elements: int = 100000,
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 50,
        space: str = "cosine",
        index_path: str | None = None
    ):
        """
        Initialize HNSW Vector Index.

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

        self._index = None
        self._id_to_label: dict[str, int] = {}
        self._label_to_id: dict[int, str] = {}
        self._current_label = 0
        self._is_initialized = False

        # Try to import hnswlib
        try:
            import hnswlib
            self._hnswlib = hnswlib
            self._available = True
        except ImportError:
            logger.warning("hnswlib not available, HNSW index will use brute-force fallback")
            self._hnswlib = None
            self._available = False

        # Brute-force fallback storage
        self._vectors: dict[str, np.ndarray] = {}

    def _init_index(self):
        """Initialize the hnswlib index."""
        if not self._available or self._is_initialized:
            return

        try:
            # Map space string to hnswlib space
            space_map = {
                "cosine": "cosine",
                "l2": "l2",
                "ip": "ip",
                "euclidean": "l2"
            }
            hnsw_space = space_map.get(self.space, "cosine")

            self._index = self._hnswlib.Index(
                space=hnsw_space,
                dim=self.dim
            )
            self._index.init_index(
                max_elements=self.max_elements,
                ef_construction=self.ef_construction,
                M=self.M
            )
            self._index.set_ef(self.ef_search)
            self._is_initialized = True
            logger.info(f"HNSW index initialized: dim={self.dim}, max_elements={self.max_elements}")
        except Exception as e:
            logger.error(f"Failed to initialize HNSW index: {e}")
            self._available = False

    def add_vectors(self, vectors: np.ndarray, ids: list[str]) -> None:
        """
        Add vectors to the index.

        Args:
            vectors: Array of shape (n_vectors, dim) or (dim,) for single vector
            ids: List of unique string identifiers for each vector
        """
        if len(vectors) != len(ids):
            raise ValueError(f"Number of vectors ({len(vectors)}) must match number of ids ({len(ids)})")

        # Ensure 2D array
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)

        # Validate dimensions
        if vectors.shape[1] != self.dim:
            raise ValueError(f"Vector dimension {vectors.shape[1]} does not match index dimension {self.dim}")

        # Check for duplicate ids
        for id_ in ids:
            if id_ in self._id_to_label:
                raise ValueError(f"Duplicate id: {id_}")

        if self._available and not self._is_initialized:
            self._init_index()

        if self._available and self._is_initialized:
            # Add to HNSW index
            labels = []
            for id_ in ids:
                label = self._current_label
                self._id_to_label[id_] = label
                self._label_to_id[label] = id_
                labels.append(label)
                self._current_label += 1

            try:
                self._index.add_items(vectors, labels)
                logger.debug(f"Added {len(ids)} vectors to HNSW index")
            except Exception as e:
                logger.error(f"Failed to add vectors to HNSW index: {e}")
                # Fallback to brute-force
                self._available = False
                for id_, vec in zip(ids, vectors, strict=False):
                    self._vectors[id_] = vec.copy()
        else:
            # Brute-force fallback
            for id_, vec in zip(ids, vectors, strict=False):
                self._vectors[id_] = vec.copy()
            logger.debug(f"Added {len(ids)} vectors to brute-force storage")

    def search(
        self,
        query_vector: np.ndarray,
        k: int = 10,
        filter_ids: list[str] | None = None
    ) -> tuple[list[str], list[float]]:
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

        if self._available and self._is_initialized and len(self._id_to_label) > 0:
            try:
                # HNSW search
                labels, distances = self._index.knn_query(query_vector, k=min(k * 2, len(self._id_to_label)))
                labels = labels[0]
                distances = distances[0]

                # Convert labels to ids
                ids = [self._label_to_id.get(int(lbl), str(lbl)) for lbl in labels]

                # Apply filter if provided
                if filter_ids:
                    filter_set = set(filter_ids)
                    filtered_ids = []
                    filtered_distances = []
                    for id_, dist in zip(ids, distances, strict=False):
                        if id_ in filter_set:
                            filtered_ids.append(id_)
                            filtered_distances.append(float(dist))
                            if len(filtered_ids) >= k:
                                break
                    return filtered_ids, filtered_distances

                return ids[:k], [float(d) for d in distances[:k]]
            except Exception as e:
                logger.error(f"HNSW search failed, falling back to brute-force: {e}")
                return self._brute_force_search(query_vector[0], k, filter_ids)
        else:
            return self._brute_force_search(query_vector[0], k, filter_ids)

    def _brute_force_search(
        self,
        query_vector: np.ndarray,
        k: int,
        filter_ids: list[str] | None = None
    ) -> tuple[list[str], list[float]]:
        """Brute-force search fallback."""
        if not self._vectors:
            return [], []

        candidates = filter_ids if filter_ids else list(self._vectors.keys())
        if not candidates:
            return [], []

        scores = []
        query_norm = np.linalg.norm(query_vector)

        for id_ in candidates:
            if id_ not in self._vectors:
                continue
            vec = self._vectors[id_]

            if self.space == "cosine":
                vec_norm = np.linalg.norm(vec)
                if vec_norm == 0 or query_norm == 0:
                    similarity = 0.0
                else:
                    similarity = np.dot(query_vector, vec) / (query_norm * vec_norm)
                # Convert similarity to distance (0 = same, 2 = opposite)
                distance = 1.0 - similarity
            elif self.space in ("l2", "euclidean"):
                distance = np.linalg.norm(query_vector - vec)
            elif self.space == "ip":
                distance = -np.dot(query_vector, vec)  # Negative for ascending sort
            else:
                distance = np.linalg.norm(query_vector - vec)

            scores.append((id_, distance))

        # Sort by distance (ascending)
        scores.sort(key=lambda x: x[1])
        ids = [s[0] for s in scores[:k]]
        distances = [s[1] for s in scores[:k]]

        return ids, distances

    def batch_search(
        self,
        query_vectors: np.ndarray,
        k: int = 10,
        filter_ids: list[str] | None = None
    ) -> list[tuple[list[str], list[float]]]:
        """
        Batch search for multiple query vectors.

        Args:
            query_vectors: Array of shape (n_queries, dim)
            k: Number of results per query
            filter_ids: Optional list of ids to filter results

        Returns:
            List of (ids, distances) tuples for each query
        """
        results = []
        for query in query_vectors:
            ids, distances = self.search(query, k, filter_ids)
            results.append((ids, distances))
        return results

    def save_index(self, path: str | None = None) -> None:
        """
        Save index to disk.

        Args:
            path: Path to save index. Uses index_path from init if not provided.
        """
        save_path = path or self.index_path
        if not save_path:
            raise ValueError("No path provided for saving index")

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if self._available and self._is_initialized:
            try:
                index_file = str(save_path / "hnsw_index.bin")
                self._index.save_index(index_file)

                # Save id mappings
                np.savez(
                    save_path / "hnsw_metadata.npz",
                    id_to_label=self._id_to_label,
                    label_to_id=self._label_to_id,
                    current_label=self._current_label,
                    dim=self.dim,
                    max_elements=self.max_elements,
                    M=self.M,
                    ef_construction=self.ef_construction,
                    ef_search=self.ef_search,
                    space=self.space
                )
                logger.info(f"HNSW index saved to {save_path}")
            except Exception as e:
                logger.error(f"Failed to save HNSW index: {e}")
                raise

        # Always save brute-force vectors as backup
        if self._vectors:
            np.savez(
                save_path / "vectors.npz",
                **dict(self._vectors.items())
            )

    def load_index(self, path: str | None = None) -> None:
        """
        Load index from disk.

        Args:
            path: Path to load index from. Uses index_path from init if not provided.
        """
        load_path = path or self.index_path
        if not load_path:
            raise ValueError("No path provided for loading index")

        load_path = Path(load_path)

        if not load_path.exists():
            raise FileNotFoundError(f"Index path not found: {load_path}")

        # Try to load HNSW index
        index_file = load_path / "hnsw_index.bin"
        metadata_file = load_path / "hnsw_metadata.npz"

        if self._available and index_file.exists() and metadata_file.exists():
            try:
                # Load metadata
                metadata = np.load(metadata_file, allow_pickle=True)
                self._id_to_label = metadata["id_to_label"].item()
                self._label_to_id = metadata["label_to_id"].item()
                self._current_label = int(metadata["current_label"])
                self.dim = int(metadata["dim"])
                self.max_elements = int(metadata["max_elements"])
                self.M = int(metadata["M"])
                self.ef_construction = int(metadata["ef_construction"])
                self.ef_search = int(metadata["ef_search"])
                self.space = str(metadata["space"])

                # Initialize and load index
                self._init_index()
                self._index.load_index(str(index_file))
                self._index.set_ef(self.ef_search)

                logger.info(f"HNSW index loaded from {load_path}")
                return
            except Exception as e:
                logger.error(f"Failed to load HNSW index: {e}")
                self._available = False

        # Fallback: load brute-force vectors
        vectors_file = load_path / "vectors.npz"
        if vectors_file.exists():
            try:
                data = np.load(vectors_file)
                for key in data.files:
                    self._vectors[key] = data[key].copy()
                logger.info(f"Loaded {len(self._vectors)} vectors from {vectors_file}")
            except Exception as e:
                logger.error(f"Failed to load vectors: {e}")
                raise

    def get_stats(self) -> dict[str, Any]:
        """
        Get index statistics.

        Returns:
            Dictionary with index statistics
        """
        stats = {
            "dim": self.dim,
            "max_elements": self.max_elements,
            "current_elements": len(self._id_to_label) if self._available else len(self._vectors),
            "M": self.M,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "space": self.space,
            "using_hnsw": self._available and self._is_initialized,
            "index_path": self.index_path,
            "memory_usage_mb": self._estimate_memory_usage()
        }
        return stats

    def _estimate_memory_usage(self) -> float:
        """Estimate memory usage in MB."""
        if self._available and self._is_initialized:
            # HNSW: ~4 bytes per dimension per vector + index overhead
            num_vectors = len(self._id_to_label)
            vector_memory = num_vectors * self.dim * 4 / (1024 * 1024)
            # Index overhead: approximately 2x vector memory for typical M values
            index_overhead = vector_memory * 2
            return vector_memory + index_overhead
        else:
            # Brute-force: just vector storage
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
        if self._available and self._is_initialized:
            self._index.set_ef(ef_search)

    def resize_index(self, new_max_elements: int) -> None:
        """
        Resize the index to accommodate more elements.

        Args:
            new_max_elements: New maximum number of elements
        """
        if new_max_elements <= self.max_elements:
            return

        self.max_elements = new_max_elements
        if self._available and self._is_initialized:
            try:
                self._index.resize_index(new_max_elements)
                logger.info(f"Index resized to {new_max_elements} elements")
            except Exception as e:
                logger.error(f"Failed to resize index: {e}")


@dataclass
class RaptorNode:
    """Single node in RAPTOR summarization tree."""
    node_id: str
    level: int           # 0 = leaf chunk, 1+ = cluster summary
    text: str
    embedding: list[float]
    child_ids: list[str] = field(default_factory=list)
    cluster_id: int = -1

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "level": self.level,
            "text": self.text[:500],
            "embedding": self.embedding[:64],
            "child_ids": self.child_ids,
            "cluster_id": self.cluster_id
        }


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

    def __init__(self, config: RAGConfig = None):
        self.config = config or RAGConfig()

        # Lazy-loaded komponenty
        self._infinite_context = None
        self._spr_compressor = None
        self._secure_enclave: SecureEnclaveBackend | None = None
        self._enclave_status: EnclaveStatus | None = None
        self._retriever = None

        # HNSW Vector Index
        self._hnsw_index: HNSWVectorIndex | None = None
        self._document_map: dict[str, Document] = {}
        self._use_hnsw = self.config.use_hnsw

        # RAPTOR tree for hierarchical summarization
        self._raptor_nodes: dict[str, RaptorNode] = {}

        # Sprint 42: CoreML ANE embedder
        self._coreml_embedder = None
        self._mlx_embedder = None  # Will be set in initialize()

    async def initialize(self) -> None:
        """Inicializovat RAG engine"""
        logger.info("Initializing RAGEngine...")

        if self.config.enable_ultra_context:
            await self._init_ultra_context()

        if self.config.enable_spr_compression:
            await self._init_spr_compressor()

        if self.config.enable_secure_enclave:
            await self._init_secure_enclave()

        # Sprint 42: Initialize CoreML embedder (ANE)
        await self._init_coreml_embedder()

        logger.info("✓ RAGEngine initialized")

    async def _init_coreml_embedder(self) -> None:
        """Initialize CoreML embedder via lazy import (compat seam).

        RAGEngine is grounding authority, NOT model owner.
        CoreML model lifecycle stays in brain/model_manager.py.
        This method is the ONLY entry point for model-plane coupling.
        """
        # Lazy import: attempt to resolve CoreML availability at runtime
        try:
            from ..brain.model_manager import COREML_MODEL_PATH, get_model_manager
            coreml_available = COREML_MODEL_PATH is not None and COREML_MODEL_PATH.exists()
        except ImportError:
            coreml_available = False

        if not coreml_available:
            logger.debug("[COREML] CoreML model not available, skipping")
            return

        try:
            # Try to load MLX embedder
            try:
                from ...embeddings.modernbert_embedder import ModernBERTEmbedder
                self._mlx_embedder = ModernBERTEmbedder()
            except ImportError:
                self._mlx_embedder = None

            # Try to load CoreML model via model_manager compat seam
            mm = get_model_manager()
            self._coreml_embedder = mm._load_coreml_embedder()

            if self._coreml_embedder is not None:
                logger.info("[COREML] Using ANE-accelerated ModernBERT")
            else:
                logger.info("[COREML] CoreML not available, using MLX fallback")
        except Exception as e:
            logger.warning(f"[COREML] Failed to initialize embedder: {e}")
            self._mlx_embedder = None
            self._coreml_embedder = None

    async def _init_ultra_context(self) -> None:
        """Inicializovat InfiniteContextEngine"""
        try:
            from hledac.ultra_context.infinite_context_engine import InfiniteContextEngine
            self._infinite_context = InfiniteContextEngine()
            logger.info("✓ Ultra Context initialized")
        except Exception as e:
            logger.warning(f"Ultra Context not available: {e}")

    async def _init_spr_compressor(self) -> None:
        """Inicializovat SPR Compressor"""
        try:
            from hledac.ultra_context.spr_compressor import SPRCompressor, SPRConfig
            self._spr_compressor = SPRCompressor(
                SPRConfig(compression_ratio_target=0.5)
            )
            logger.info("✓ SPR Compressor initialized (50% target)")
        except Exception as e:
            logger.warning(f"SPR Compressor not available: {e}")

    async def _init_secure_enclave(self) -> None:
        """Inicializovat Secure Enclave"""
        self._secure_enclave, self._enclave_status = await create_secure_enclave_backend(
            enabled=self.config.enable_secure_enclave
        )
        avail = self._enclave_status.availability
        if avail == EnclaveAvailability.DISABLED:
            logger.info("Secure Enclave disabled by config")
        elif avail == EnclaveAvailability.UNAVAILABLE:
            logger.warning(f"Secure Enclave unavailable: {self._enclave_status.error_message}")
        elif avail == EnclaveAvailability.AVAILABLE:
            logger.info(f"✓ Secure Enclave initialized ({self._enclave_status.backend_name})")
        else:
            logger.warning(f"Secure Enclave fail-soft: {self._enclave_status.error_message}")

    async def query(
        self,
        query: str,
        context_chunks: list[str],
        use_compression: bool = None,
        secure: bool = False
    ) -> dict[str, Any]:
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
        # Auto-detect komprese
        if use_compression is None:
            use_compression = len(context_chunks) > self.config.compression_threshold

        logger.info(f"RAG query: {len(context_chunks)} chunks, compression={use_compression}")

        # 1. Komprese pokud je potřeba
        if use_compression and self._spr_compressor:
            context_chunks = await self._compress_chunks(context_chunks)

        # 2. Secure enclave pokud je požadováno
        if secure and self._secure_enclave:
            context_chunks = await self._secure_process(context_chunks)

        # 3. Sestavit kontext
        context = "\n\n".join(context_chunks)

        # 4. Detekovat komplexní query pro ToT
        is_complex = self._is_complex_query(query)

        return {
            "query": query,
            "context": context,
            "chunks_used": len(context_chunks),
            "compressed": use_compression,
            "secure": secure,
            "complex": is_complex,
        }

    async def _compress_chunks(self, chunks: list[str]) -> list[str]:
        """Komprimovat chunky pomocí SPR"""
        if not self._spr_compressor:
            return chunks

        compressed = []
        for chunk in chunks:
            try:
                result = await self._spr_compressor.compress(chunk)
                compressed.append(result.compressed_text)
            except Exception as e:
                logger.warning(f"Compression failed: {e}")
                compressed.append(chunk)

        return compressed

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

        # Only process if backend is available
        if not self._secure_enclave.is_available():
            logger.debug("Secure Enclave backend not available, skipping")
            return chunks

        try:
            manifest = build_batch_manifest(chunks)
            signed = await self._secure_enclave.sign_batch_digest(manifest)
            # Update status with signature info
            self._enclave_status.signed_batch_digest = signed.signature.hex()
            self._enclave_status.chunk_count = manifest.chunk_count
            self._enclave_status.availability = EnclaveAvailability.SIGNED
            logger.debug(
                f"Secure Enclave: signed batch digest for {manifest.chunk_count} chunks"
            )
        except SecureEnclaveError as e:
            logger.warning(f"Secure Enclave signing failed (fail-soft): {e}")
            self._enclave_status.availability = EnclaveAvailability.FAIL_SOFT
            self._enclave_status.error_message = str(e)
        except Exception as e:
            logger.warning(f"Secure Enclave unexpected error (fail-soft): {e}")
            self._enclave_status.availability = EnclaveAvailability.FAIL_SOFT
            self._enclave_status.error_message = str(e)

        # Always return chunks unchanged
        return chunks

    def _is_complex_query(self, query: str) -> bool:
        """Detekovat komplexní dotaz pro Tree of Thoughts"""
        complex_indicators = [
            "and", "then", "compare", "contrast", "analyze",
            "why", "how does", "relationship", "impact"
        ]
        return any(ind in query.lower() for ind in complex_indicators)

    # ============== HYBRID RETRIEVAL METHODS ==============

    async def hybrid_retrieve(
        self,
        query: str,
        documents: list[Document],
        top_k: int | None = None,
        filters: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
        """
        Retrieve relevant documents using hybrid search (dense + sparse).

        Args:
            query: Search query
            documents: List of documents to search
            top_k: Number of results to return
            filters: Optional metadata filters

        Returns:
            List of retrieved chunks with scores
        """
        if not self.config.enable_hybrid_retrieval:
            # Fallback to simple retrieval
            return [
                RetrievedChunk(
                    document=doc,
                    chunk_text=doc.content[:self.config.chunk_size],
                    final_score=1.0
                )
                for doc in documents[:top_k or 5]
            ]

        top_k = top_k or 10

        # Index documents
        bm25 = BM25Index(k1=self.config.bm25_k1, b=self.config.bm25_b)
        embeddings: dict[str, list[float]] = {}

        for doc in documents:
            bm25.add_document(doc)

        # Generate embeddings
        embeddings = await self._generate_embeddings([d.content for d in documents])
        doc_embeddings = {doc.id: embeddings[i] for i, doc in enumerate(documents)}

        # Dense retrieval (cosine similarity)
        query_embedding = (await self._generate_embeddings([query]))[0]
        dense_results = self._dense_retrieval(query_embedding, doc_embeddings, top_k * 2)

        # Sparse retrieval (BM25)
        sparse_results = bm25.search(query, top_k=top_k * 2)
        sparse_doc_ids = [(bm25.documents[idx].id, score) for idx, score in sparse_results]

        # Combine using weighted fusion
        doc_scores: dict[str, dict[str, float]] = defaultdict(lambda: {'dense': 0.0, 'sparse': 0.0})

        for doc_id, score in dense_results:
            doc_scores[doc_id]['dense'] = score

        # Normalize BM25 scores to 0-1
        max_sparse = max([s for _, s in sparse_doc_ids], default=1.0)
        for doc_id, score in sparse_doc_ids:
            doc_scores[doc_id]['sparse'] = score / max_sparse if max_sparse > 0 else 0

        # Calculate final scores
        results: list[RetrievedChunk] = []
        doc_map = {d.id: d for d in documents}

        for doc_id, scores in doc_scores.items():
            if doc_id not in doc_map:
                continue

            doc = doc_map[doc_id]

            # Check filters
            if filters and not self._matches_filters(doc, filters):
                continue

            final_score = (
                self.config.dense_weight * scores['dense'] +
                self.config.sparse_weight * scores['sparse']
            )

            chunk = RetrievedChunk(
                document=doc,
                chunk_text=doc.content[:self.config.chunk_size],
                dense_score=scores['dense'],
                sparse_score=scores['sparse'],
                final_score=final_score
            )
            results.append(chunk)

        # Sort by final score
        results.sort(key=lambda x: x.final_score, reverse=True)
        return results[:top_k]

    async def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for texts using cached FastEmbed or MLXEmbeddingManager.

        M1 8GB: TextEmbedding instance is cached in self._fastembed_embedder
        to avoid repeated model loading (memory fragmentation prevention).
        Falls back to MLXEmbeddingManager singleton if FastEmbed unavailable.
        """
        # Sprint 8TD: Cache FastEmbed instance to avoid repeated model loading
        if not hasattr(self, '_fastembed_embedder') or self._fastembed_embedder is None:
            try:
                from fastembed import TextEmbedding
                self._fastembed_embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
                logger.debug("[FastEmbed] TextEmbedding instance cached in RAGEngine")
            except ImportError:
                self._fastembed_embedder = False  # Mark as unavailable
                logger.debug("[FastEmbed] Not available, will use MLXEmbeddingManager fallback")

        if self._fastembed_embedder:
            try:
                embeddings = list(self._fastembed_embedder.embed(texts))
                return [list(e) for e in embeddings]
            except Exception as e:
                logger.warning(f"FastEmbed embed failed: {e}, falling back to MLXEmbeddingManager")

        # Fallback to MLXEmbeddingManager singleton
        try:
            from hledac.universal.core.mlx_embeddings import get_embedding_manager
            manager = get_embedding_manager()
            results = []
            for text in texts:
                # Use embed_document (sync) via asyncio.to_thread
                result = await asyncio.to_thread(manager.embed_document, text)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)
                results.append(emb)
            return results
        except Exception as e:
            logger.warning(f"MLXEmbeddingManager fallback failed: {e}")

        # Last resort: stable SHA256-based deterministic embeddings
        # FIX F800A: hash(t) is process-salted (PYTHONHASHSEED), not cross-run deterministic.
        # Using SHA256 digest to derive float values ensures identical output across runs.
        return [
            [
                float(digest[i % 32]) / 255.0
                for i in range(384)
            ]
            for t in texts
            for digest in [hashlib.sha256(t.encode()).digest()]
        ]

    def _dense_retrieval(
        self,
        query_embedding: list[float],
        doc_embeddings: dict[str, list[float]],
        top_k: int
    ) -> list[tuple[str, float]]:
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

    # ============== HNSW VECTOR SEARCH METHODS ==============

    def build_hnsw_index(
        self,
        documents: list[Document],
        embeddings: dict[str, list[float]] | None = None
    ) -> None:
        """
        Build HNSW index from documents.

        Args:
            documents: List of documents to index
            embeddings: Optional pre-computed embeddings {doc_id: embedding}
                       If not provided, embeddings will be generated
        """
        if not documents:
            logger.warning("No documents provided for HNSW indexing")
            return

        logger.info(f"Building HNSW index for {len(documents)} documents...")

        # Create HNSW index
        self._hnsw_index = HNSWVectorIndex(
            dim=self.config.hnsw_dim,
            max_elements=self.config.hnsw_max_elements,
            M=self.config.hnsw_M,
            ef_construction=self.config.hnsw_ef_construction,
            ef_search=self.config.hnsw_ef_search,
            space=self.config.hnsw_space,
            index_path=self.config.hnsw_index_path
        )

        # Store document mapping
        self._document_map = {doc.id: doc for doc in documents}

        # Get embeddings
        if embeddings is None:
            # Generate embeddings asynchronously
            logger.info("Generating embeddings for HNSW index...")
            # Run async embedding generation in sync context
            # Use thread-runner pattern: always safe regardless of call context
            try:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._generate_embeddings([d.content for d in documents])
                    )
                    embeddings_list = future.result(timeout=300)
                embeddings = {doc.id: emb for doc, emb in zip(documents, embeddings_list, strict=False)}
            except Exception as e:
                logger.error(f"Failed to generate embeddings: {e}")
                return

        # Add vectors to index
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
            logger.warning("No valid embeddings found for HNSW indexing")
            return

        vectors_array = np.array(valid_vectors, dtype=np.float32)
        self._hnsw_index.add_vectors(vectors_array, valid_ids)

        stats = self._hnsw_index.get_stats()
        logger.info(f"HNSW index built: {stats['current_elements']} vectors, "
                   f"{stats['memory_usage_mb']:.2f} MB, HNSW enabled: {stats['using_hnsw']}")

    def enable_hnsw(self, enable: bool = True) -> None:
        """
        Enable or disable HNSW search.

        Args:
            enable: True to enable HNSW, False to use brute-force
        """
        self._use_hnsw = enable
        logger.info(f"HNSW search {'enabled' if enable else 'disabled'}")

    def _hnsw_retrieval(
        self,
        query_embedding: list[float] | np.ndarray,
        top_k: int = 10,
        filters: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
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
            logger.warning("HNSW index not built, cannot perform retrieval")
            return []

        # Convert to numpy array
        if isinstance(query_embedding, list):
            query_embedding = np.array(query_embedding, dtype=np.float32)

        # Apply filters if provided
        filter_ids = None
        if filters:
            filter_ids = [
                doc_id for doc_id, doc in self._document_map.items()
                if self._matches_filters(doc, filters)
            ]
            if not filter_ids:
                return []

        # Search HNSW index
        ids, distances = self._hnsw_index.search(query_embedding, top_k, filter_ids)

        # Convert to RetrievedChunk
        results = []
        for doc_id, distance in zip(ids, distances, strict=False):
            if doc_id not in self._document_map:
                continue

            doc = self._document_map[doc_id]

            # Convert distance to similarity score (cosine similarity from distance)
            if self.config.hnsw_space == "cosine":
                similarity = 1.0 - distance  # distance = 1 - similarity for cosine
            elif self.config.hnsw_space in ("l2", "euclidean"):
                # Convert L2 distance to similarity (closer to 0 = more similar)
                similarity = 1.0 / (1.0 + distance)
            elif self.config.hnsw_space == "ip":
                similarity = -distance  # Negative was applied for ascending sort
            else:
                similarity = 1.0 - distance

            chunk = RetrievedChunk(
                document=doc,
                chunk_text=doc.content[:self.config.chunk_size],
                dense_score=float(similarity),
                sparse_score=0.0,
                final_score=float(similarity)
            )
            results.append(chunk)

        return results

    async def hybrid_retrieve_with_hnsw(
        self,
        query: str,
        documents: list[Document] | None = None,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        use_hnsw: bool | None = None
    ) -> list[RetrievedChunk]:
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

        # If HNSW is enabled and built, use it
        if should_use_hnsw and self._hnsw_index is not None:
            return await self._hybrid_retrieve_hnsw(query, top_k, filters)

        # Otherwise, fall back to standard hybrid retrieval
        if documents is None:
            raise ValueError("Documents required when HNSW index not built")

        return await self.hybrid_retrieve(query, documents, top_k, filters)

    async def _hybrid_retrieve_hnsw(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None
    ) -> list[RetrievedChunk]:
        """
        Internal hybrid retrieval using HNSW for dense search.
        """
        top_k = top_k or 10

        # Generate query embedding
        query_embedding = (await self._generate_embeddings([query]))[0]

        # Dense retrieval via HNSW
        dense_results = self._hnsw_retrieval(query_embedding, top_k * 2, filters)

        # Build BM25 index for sparse retrieval
        bm25 = BM25Index(k1=self.config.bm25_k1, b=self.config.bm25_b)
        for doc in self._document_map.values():
            bm25.add_document(doc)

        # Sparse retrieval (BM25)
        sparse_results = bm25.search(query, top_k=top_k * 2)
        sparse_doc_ids = [(bm25.documents[idx].id, score) for idx, score in sparse_results]

        # Combine using weighted fusion
        doc_scores: dict[str, dict[str, float]] = defaultdict(lambda: {'dense': 0.0, 'sparse': 0.0})

        for chunk in dense_results:
            doc_scores[chunk.document.id]['dense'] = chunk.dense_score

        # Normalize BM25 scores to 0-1
        max_sparse = max([s for _, s in sparse_doc_ids], default=1.0)
        for doc_id, score in sparse_doc_ids:
            doc_scores[doc_id]['sparse'] = score / max_sparse if max_sparse > 0 else 0

        # Calculate final scores
        results: list[RetrievedChunk] = []

        for doc_id, scores in doc_scores.items():
            if doc_id not in self._document_map:
                continue

            doc = self._document_map[doc_id]

            # Check filters
            if filters and not self._matches_filters(doc, filters):
                continue

            final_score = (
                self.config.dense_weight * scores['dense'] +
                self.config.sparse_weight * scores['sparse']
            )

            chunk = RetrievedChunk(
                document=doc,
                chunk_text=doc.content[:self.config.chunk_size],
                dense_score=scores['dense'],
                sparse_score=scores['sparse'],
                final_score=final_score
            )
            results.append(chunk)

        # Sort by final score
        results.sort(key=lambda x: x.final_score, reverse=True)
        return results[:top_k]

    def save_hnsw_index(self, path: str | None = None) -> None:
        """
        Save HNSW index to disk.

        Args:
            path: Path to save index. Uses config.hnsw_index_path if not provided.
        """
        if self._hnsw_index is None:
            raise ValueError("HNSW index not built")

        save_path = path or self.config.hnsw_index_path
        if not save_path:
            raise ValueError("No path provided for saving index")

        self._hnsw_index.save_index(save_path)

        # Also save document map
        try:
            import orjson
            doc_map_path = Path(save_path) / "document_map.json"
            with open(doc_map_path, 'wb') as f:
                f.write(orjson.dumps(self._document_map))
        except ImportError:
            import json
            doc_map_path = Path(save_path) / "document_map.json"
            with open(doc_map_path, 'w') as f:
                json.dump(self._document_map, f)

        logger.info(f"HNSW index and document map saved to {save_path}")

    def load_hnsw_index(self, path: str | None = None) -> None:
        """
        Load HNSW index from disk.

        Args:
            path: Path to load index from. Uses config.hnsw_index_path if not provided.
        """
        load_path = path or self.config.hnsw_index_path
        if not load_path:
            raise ValueError("No path provided for loading index")

        # Initialize HNSW index if not exists
        if self._hnsw_index is None:
            self._hnsw_index = HNSWVectorIndex(
                dim=self.config.hnsw_dim,
                max_elements=self.config.hnsw_max_elements,
                M=self.config.hnsw_M,
                ef_construction=self.config.hnsw_ef_construction,
                ef_search=self.config.hnsw_ef_search,
                space=self.config.hnsw_space,
                index_path=load_path
            )

        self._hnsw_index.load_index(load_path)

        # Load document map
        doc_map_path = Path(load_path) / "document_map.json"
        if doc_map_path.exists():
            try:
                import orjson
                with open(doc_map_path, 'rb') as f:
                    self._document_map = orjson.loads(f.read())
            except ImportError:
                import json
                with open(doc_map_path) as f:
                    self._document_map = json.load(f)

        logger.info(f"HNSW index loaded from {load_path}")

    def get_hnsw_stats(self) -> dict[str, Any] | None:
        """
        Get HNSW index statistics.

        Returns:
            Dictionary with index statistics, or None if index not built
        """
        if self._hnsw_index is None:
            return None
        return self._hnsw_index.get_stats()

    # ============== COREML CONVERSION (Sprint 42) ==============

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
            logger.warning("[COREML] No MLX embedder for conversion")
            return False

        # Pre-condition: Test accuracy before conversion
        try:
            chunks = await self._get_random_chunks(500)
            if len(chunks) < 100:
                logger.warning("[COREML] Not enough chunks for accuracy test")
                return False

            # Get original embeddings
            original_embs = []
            for chunk in chunks[:100]:
                emb = await self._mlx_embedder.embed(chunk) if hasattr(self._mlx_embedder, 'embed') else None
                if emb is not None:
                    original_embs.append(np.array(emb))

            if len(original_embs) < 50:
                logger.warning("[COREML] Not enough embeddings for test")
                return False

            # Simulate conversion accuracy test (in real impl, would convert and test)
            # For now, skip if we can't properly test
            logger.info("[COREML] Skipping conversion - accuracy test not implemented in mock")
            return False
        except Exception as e:
            logger.warning(f"[COREML] Accuracy test failed: {e}")
            return False

    # ============== RAPTOR HIERARCHICAL SUMMARIZATION ==============

    async def _embed_text(self, text: str) -> list[float]:
        """Embed text using CoreML if available, fallback to MLX."""
        # Sprint 42: Try CoreML first
        if self._coreml_embedder is not None:
            try:
                import numpy as np
                # CoreML inference
                input_dict = {"input": np.array([text])}
                result = self._coreml_embedder.predict(input_dict)
                # Handle different output formats - safely extract embedding
                if isinstance(result, dict):
                    # Try to find embedding in common output keys
                    output = None
                    for key in ("output", "embedding", "last_hidden_state", "hidden_state"):
                        if key in result:
                            output = result[key]
                            break
                    if output is None:
                        output = list(result.values())[0]
                else:
                    output = result

                # Convert to list
                if hasattr(output, 'tolist'):
                    output = output.tolist()

                # Handle different shapes: [[[dim]]], [[dim]], or [dim]
                embedding = []
                while isinstance(output, list) and len(output) > 0:
                    if isinstance(output[0], list):
                        output = output[0]
                    else:
                        embedding = output
                        break

                return embedding
            except Exception as e:
                logger.warning(f"[COREML] Inference failed, falling back to MLX: {e}")
                self._coreml_embedder = None  # Disable CoreML for next calls

        # Fallback to MLX/fastembed
        embeddings = await self._generate_embeddings([text])
        return embeddings[0] if embeddings else []

    async def _build_raptor_tree(
        self,
        documents: list[Document],
        max_levels: int = 2,
        max_docs: int = 50
    ) -> dict[str, RaptorNode]:
        """Build RAPTOR summarization tree. Returns node_id -> RaptorNode dict."""
        docs = documents[:max_docs]
        if len(docs) < 3:
            return {}

        nodes: dict[str, RaptorNode] = {}
        current_level_texts: list[str] = []
        current_level_embeddings: list[list[float]] = []

        # Level 0: leaf nodes
        for i, doc in enumerate(docs):
            node_id = f"raptor_L0_{i}"
            try:
                embedding = await self._embed_text(doc.content)
            except Exception:
                continue
            node = RaptorNode(
                node_id=node_id, level=0,
                text=doc.content[:2000], embedding=embedding
            )
            nodes[node_id] = node
            current_level_texts.append(doc.content[:2000])
            current_level_embeddings.append(embedding)

        # Build higher levels
        for level in range(1, max_levels + 1):
            if len(current_level_embeddings) < 3:
                break

            try:
                from sklearn.decomposition import PCA
                pca = PCA(n_components=2)
                reduced = pca.fit_transform(np.array(current_level_embeddings))
            except Exception as e:
                logger.warning(f"[RAPTOR] PCA failed at level {level}: {e}")
                break  # fail-safe: stop tree, flat retrieval still works

            n_clusters = max(2, min(8, len(current_level_embeddings) // 3))
            try:
                from sklearn.mixture import GaussianMixture
                gm = GaussianMixture(n_components=n_clusters, random_state=42, max_iter=50)
                cluster_labels = gm.fit_predict(reduced)
            except Exception as e:
                logger.warning(f"[RAPTOR] GMM failed at level {level}: {e}")
                break

            prev_level_node_ids = [nid for nid, n in nodes.items() if n.level == level - 1]
            new_texts: list[str] = []
            new_embeddings: list[list[float]] = []

            for cluster_id in range(n_clusters):
                cluster_indices = [i for i, l in enumerate(cluster_labels) if l == cluster_id]
                if not cluster_indices:
                    continue

                cluster_texts = [current_level_texts[i] for i in cluster_indices]
                combined = "\n\n".join(cluster_texts[:5])[:3000]
                summary = await self._summarize_cluster(combined, max_tokens=200)

                node_id = f"raptor_L{level}_c{cluster_id}"
                try:
                    embedding = await self._embed_text(summary)
                except Exception:
                    continue

                child_ids = [
                    prev_level_node_ids[i]
                    for i in cluster_indices
                    if i < len(prev_level_node_ids)
                ]
                nodes[node_id] = RaptorNode(
                    node_id=node_id, level=level,
                    text=summary, embedding=embedding,
                    child_ids=child_ids, cluster_id=cluster_id
                )
                new_texts.append(summary)
                new_embeddings.append(embedding)

            current_level_texts = new_texts
            current_level_embeddings = new_embeddings

        return nodes

    async def _summarize_cluster(self, text: str, max_tokens: int = 200) -> str:
        """Summarize cluster text via Hermes3 generate_structured(). Truncates on failure."""
        # Access Hermes via whichever attribute exists
        hermes = getattr(self, '_model_manager', None) or getattr(self, '_llm', None) or getattr(self, '_hermes_engine', None)
        if hermes is None:
            return text[:500]
        try:
            result = await hermes.generate_structured(
                prompt=f"Summarize the following research findings concisely:\n\n{text}",
                response_model=dict,
                max_tokens=max_tokens,
                priority=0.5  # Sprint 7I: explicit priority for batch routing
            )
            if isinstance(result, dict) and "summary" in result:
                return result["summary"].strip()
            if isinstance(result, str):
                return result.strip()
            return text[:500]
        except Exception as e:
            logger.warning(f"[RAPTOR] Cluster summarization failed: {e}")
            return text[:500]

    def _raptor_retrieve(
        self,
        query_embedding: list[float],
        nodes: dict[str, RaptorNode],
        top_k: int = 5
    ) -> list[RaptorNode]:
        """Retrieve top-K nodes from all RAPTOR levels by cosine similarity."""
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

    def _rrf_merge(
        self,
        list_a: list[Any],
        list_b: list[Any],
        top_k: int = 10,
        k: int = 60
    ) -> list[Any]:
        """Merge two ranked lists via Reciprocal Rank Fusion. Stable key = hash of content."""
        def _item_key(item) -> str:
            # Prefer URL, fall back to content hash
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
        logger.info("Closing RAGEngine...")
        self._infinite_context = None
        self._spr_compressor = None
        self._secure_enclave = None
        self._hnsw_index = None
        self._document_map.clear()
        logger.info("✓ RAGEngine closed")
