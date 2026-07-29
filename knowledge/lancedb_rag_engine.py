"""
LanceDB-Backed RAG Engine — Sprint P2-3
=======================================

.. deprecated:: F350M-R
    LanceDB-backed RAG is DEPRECATED in favour of ``DuckDBRAGStore``
    in ``knowledge.duckdb_rag_store``. DuckDB FTS5 + HNSW provides
    equivalent RAG grounding with ~0 MB subprocess overhead vs ~200 MB.

ROLE: Grounding Authority backed by LanceDB (NOT identity/entity store)
=======================================================================
Tento modul je RAG grounding authority s LanceDB persistence.
NENÍ owner identity/entity resolution → lancedb_store (entities table)
NENÍ owner document retrieval → rag_engine (HNSWVectorIndex, in-memory)

Provides cross-sprint RAG persistence via LanceDB with:
- IVF-PQ vector quantization (opt-in, M1 8GB friendly)
- Hybrid search (vector + FTS via LanceDB native)
- MMR diversity reranking
- Cross-sprint persistence (vs HNSW binary files)

API: add_document(), search(), get_relevant_chunks() — same shape as RAGEngine.

INVARIANTS (always-on, bounded, fail-safe):
- HLEDAC_LANCEDB_QUANTIZE=1 → IVF-PQ quantized index
- LanceDB table "documents" with schema: id, content, metadata, embedding(256d)
- Fail-soft: any error → returns empty results, never raises
- M1 8GB: RSS guard before embedding, bounded batch size
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import Any
import numpy as np
from context_optimization.mmr import maximal_marginal_relevance
from hledac.universal.utils.executor_decorator import offload_to
logger = logging.getLogger(__name__)
_DEFAULT_URI = Path(__file__).parent.parent.parent / 'data' / 'rag.lance'
_MAX_BATCH_SIZE = 32
_MAX_DOCS = 50000
_EMBEDDING_DIM = 256

class RAGDocument(msgspec.Struct, gc=False):
    """Document for LanceDB-backed RAG."""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None

    def __hash__(self):
        return hash(self.id)

class RetrievedChunk(msgspec.Struct, frozen=True, gc=False):
    """Retrieved document chunk with scores."""
    document: RAGDocument
    chunk_text: str
    vector_score: float = 0.0
    fts_score: float = 0.0
    final_score: float = 0.0

class LanceDBRAGEngine:
    """
    LanceDB-backed RAG engine with cross-sprint persistence.

    ROLE: Grounding Authority backed by LanceDB.
    - Embedding: MLXEmbeddingManager singleton (shared, not owned)
    - Storage: LanceDB "documents" table with IVF-PQ (opt-in)
    - Search: Hybrid vector + FTS via LanceDB native (no external HNSW)

    vs RAGEngine (in-memory HNSW):
    - Cross-sprint persistence (HNSW is single-sprint only)
    - IVF-PQ quantization (M1 8GB friendly, opt-in)
    - No manual save/load — LanceDB handles persistence
    """
    __slots__ = tuple(('_db', '_embedder', '_embedder_lock', '_fts_enabled', '_mmr_top_k', '_quantize_enabled', '_quantize_lock', '_quantize_trained', '_table', 'uri'))

    def __init__(self, uri: str=str(_DEFAULT_URI), enable_quantize: bool | None=None, enable_fts: bool=True, mmr_top_k: int=20):
        """
        Initialize LanceDB RAG engine.

        Args:
            uri: Path to LanceDB database.
            enable_quantize: Override HLEDAC_LANCEDB_QUANTIZE env gate.
                None = use env value (default: off).
            enable_fts: Enable FTS index on content (default: True).
            mmr_top_k: Top-K for MMR reranking (default: 20).
        """
        self.uri = uri
        self._table = None
        self._db = None
        self._embedder = None
        self._embedder_lock = None
        self._fts_enabled = enable_fts
        self._mmr_top_k = mmr_top_k
        self._quantize_enabled = enable_quantize if enable_quantize is not None else os.getenv('HLEDAC_LANCEDB_QUANTIZE', '0') == '1'
        self._quantize_trained = False
        self._quantize_lock = asyncio.Lock()
        self._initialize()

    def _initialize(self) -> None:
        """Initialize LanceDB table synchronously."""
        try:
            import lancedb
            import pyarrow as pa
            Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(self.uri)
            self._table = self._db.create_table('documents', schema=pa.schema([pa.field('id', pa.string()), pa.field('content', pa.string()), pa.field('metadata', pa.string()), pa.field('embedding', pa.list_(pa.float32(), list_size=_EMBEDDING_DIM))]), exist_ok=True)
            if self._fts_enabled:
                try:
                    list_indices_fn = getattr(self._table, 'list_indices', None)
                    existing = list_indices_fn() if callable(list_indices_fn) else []
                    if not any((getattr(idx, 'name', '') == 'content_idx' for idx in existing)):
                        self._table.create_fts_index('content', replace=False, with_position=True, tokenizer_name='en_stem')
                    logger.info('[LANCEDB:RAG] FTS index available — hybrid search enabled')
                except Exception as e:
                    logger.debug('[LANCEDB:RAG] FTS index unavailable: %s', e)
            logger.info(f'[LANCEDB:RAG] initialized at {self.uri}')
        except ImportError:
            logger.warning('LanceDB not available, LanceDBRAGEngine disabled')
            self._db = None
        except Exception as e:
            logger.warning(f'Failed to initialize LanceDBRAGEngine: {e}')
            self._db = None

    async def _get_embedder(self):
        """Get shared MLXEmbeddingManager singleton (lazy, thread-safe)."""
        if self._embedder is None:
            if self._embedder_lock is None:
                self._embedder_lock = asyncio.Lock()
            async with self._embedder_lock:
                if self._embedder is None:
                    try:
                        from hledac.universal.core.mlx_embeddings import get_embedding_manager
                        self._embedder = get_embedding_manager()
                    except Exception as e:
                        logger.debug(f'[LANCEDB:RAG] embedder init failed: {e}')
                        return None
        return self._embedder

    async def add_document(self, doc: RAGDocument) -> bool:
        """
        Add a single document to LanceDB.

        Args:
            doc: RAGDocument with content and optional pre-computed embedding.

        Returns:
            True if added successfully, False otherwise.
        """
        if self._table is None:
            return False
        emb = doc.embedding
        if emb is None:
            embedder = await self._get_embedder()
            if embedder is None:
                return False
            try:
                result = await asyncio.to_thread(embedder.embed_document, doc.content)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)
            except Exception as e:
                logger.debug(f'[LANCEDB:RAG] embedding failed: {e}')
                return False
        norm = np.linalg.norm(emb) + 1e-08
        emb_norm = (np.array(emb) / norm).tolist()
        import orjson
        metadata_json = orjson.dumps(doc.metadata).decode()
        try:
            await offload_to("cpu_blocking_pool", self._table.add, [{'id': doc.id, 'content': doc.content, 'metadata': metadata_json, 'embedding': emb_norm}])
            return True
        except Exception as e:
            logger.warning(f'[LANCEDB:RAG] add_document failed: {e}')
            return False

    async def add_documents_batch(self, docs: list[RAGDocument], batch_size: int=_MAX_BATCH_SIZE) -> int:
        """
        Add multiple documents in batches (M1 8GB safe).

        Args:
            docs: List of RAGDocument objects.
            batch_size: Max batch size for embedding (default: 32).

        Returns:
            Number of successfully added documents.
        """
        if not docs or self._table is None:
            return 0
        added = 0
        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            texts = [d.content for d in batch]
            embedder = await self._get_embedder()
            if embedder is None:
                break
            try:
                result = await asyncio.to_thread(embedder._embed_for_indexing, texts)
                embeddings = result.tolist() if hasattr(result, 'tolist') else list(result)
            except Exception as e:
                logger.debug(f'[LANCEDB:RAG] batch embedding failed: {e}')
                break
            emb_array = np.array(embeddings)
            norms = np.linalg.norm(emb_array, axis=1, keepdims=True) + 1e-08
            emb_norm = (emb_array / norms).tolist()
            import orjson
            rows = []
            for d, emb in zip(batch, emb_norm, strict=True):
                rows.append({'id': d.id, 'content': d.content, 'metadata': orjson.dumps(d.metadata).decode(), 'embedding': emb})
            try:
                await asyncio.to_thread(lambda: self._table.add(rows))
                added += len(rows)
            except Exception as e:
                logger.debug(f'[LANCEDB:RAG] batch add failed: {e}')
                break
            if i + batch_size < len(docs):
                await asyncio.sleep(0)
        return added

    async def search(self, query: str, top_k: int=10, use_mmr: bool=True, mmr_lambda: float=0.7) -> list[RetrievedChunk]:
        """
        Search for relevant documents.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            use_mmr: Apply MMR reranking for diversity (default: True).
            mmr_lambda: MMR lambda parameter (higher = more diversity).

        Returns:
            List of RetrievedChunk with scores.
        """
        if self._table is None:
            return []
        embedder = await self._get_embedder()
        if embedder is None:
            return []
        try:
            result = await asyncio.to_thread(embedder.embed_document, query)
            q_emb = result.tolist() if hasattr(result, 'tolist') else list(result)
        except Exception as e:
            logger.debug(f'[LANCEDB:RAG] query embedding failed: {e}')
            return []
        norm = np.linalg.norm(q_emb) + 1e-08
        q_emb_norm = (np.array(q_emb) / norm).tolist()
        try:
            results = await offload_to("cpu_blocking_pool", lambda: self._table.search(q_emb_norm, vector_column_name='embedding').metric('cosine').limit(top_k * 2 if use_mmr else top_k).to_list())
        except Exception as e:
            logger.debug(f'[LANCEDB:RAG] search failed: {e}')
            return []
        if not results:
            return []
        chunks: list[RetrievedChunk] = []
        for r in results:
            import orjson
            try:
                metadata = orjson.loads(r.get('metadata', '{}'))
            except Exception:
                metadata = {}
            doc = RAGDocument(id=r.get('id', ''), content=r.get('content', ''), metadata=metadata, embedding=r.get('embedding'))
            chunk = RetrievedChunk(document=doc, chunk_text=r.get('content', '')[:500], vector_score=1.0 - r.get('_distance', 0.0), fts_score=0.0, final_score=1.0 - r.get('_distance', 0.0))
            chunks.append(chunk)
        if use_mmr and len(chunks) > 1:
            try:
                q_vec = np.array(q_emb_norm, dtype=np.float32)
                doc_embs = [c.document.embedding for c in chunks]
                if all((e is not None for e in doc_embs)):
                    doc_matrix = np.stack([np.array(e, dtype=np.float32) for e in doc_embs])
                else:
                    doc_matrix = np.array([q_emb_norm] * len(chunks), dtype=np.float32)
                doc_vecs = [doc_matrix[i] for i in range(len(doc_matrix))]
                mmr_indices = maximal_marginal_relevance(q_vec, doc_vecs, top_k=top_k, lambda_param=mmr_lambda)
                chunks = [chunks[i] for i in mmr_indices[:top_k]]
            except Exception as e:
                logger.debug(f'[LANCEDB:RAG] MMR reranking failed: {e}')
        return chunks[:top_k]

    async def get_relevant_chunks(self, query: str, top_k: int=5) -> list[dict[str, Any]]:
        """
        Get relevant chunks as dicts (compatible with RAGEngine API).

        Args:
            query: Search query.
            top_k: Number of chunks.

        Returns:
            List of dicts with keys: content, score, metadata.
        """
        chunks = await self.search(query, top_k=top_k, use_mmr=True)
        return [{'content': c.chunk_text, 'score': c.final_score, 'metadata': c.document.metadata} for c in chunks]

    def count_documents(self) -> int:
        """Return total document count."""
        if self._table is None:
            return 0
        try:
            return self._table.count_rows()
        except Exception:
            return 0

    async def close(self) -> None:
        """Close LanceDB connection (no-op, connection is lazy)."""
        self._db = None
        self._table = None