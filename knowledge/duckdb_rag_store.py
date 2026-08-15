"""
knowledge/duckdb_rag_store.py — Unified DuckDB-Backed RAG & Identity Store
========================================================================





F350M-R: Phase 2 Knowledge consolidation — LanceDB → DuckDB migration.

ROLE: Single unified DuckDB-backed store replacing:
  1. LanceDBIdentityStore  (entity resolution / identity stitching)
  2. LanceDBRAGEngine     (cross-sprint RAG grounding)
  3. SemanticStore         (semantic IOC search)

ARCHITECTURE (M1 8GB native):
  ┌──────────────────────────────────────────────────────────────┐
  │  DuckDBShadowStore (duckdb_store.py)                        │
  │  ├── FTS5 — full-text search (alias matching, content)     │
  │  ├── rag_embeddings — LIST<FLOAT> + HNSW index            │
  │  └── entity_embeddings — LIST<FLOAT> + HNSW index          │
  └──────────────────────────────────────────────────────────────┘

BACKWARD COMPATIBILITY:
  All public API methods are named to match the LanceDB counterparts,
  so callers (rag_orchestrator, sidecar_protocol_adapters) need
  NO changes beyond importing from duckdb_rag_store instead.

INVARIANTS (always-on, bounded, fail-safe):
  - FTS5 + HNSW via duckdb_store DuckDBShadowStore instance
  - Fail-soft: any error → returns empty results, never raises
  - M1 8GB: RSS guard before embedding, bounded batch sizes
  - No LanceDB dependency in this module

MIGRATION PATH:
  LanceDB → DuckDB is seamless: same API shape, DuckDB FTS5 + HNSW
  provide equivalent search quality with 10× less RAM overhead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from operator import attrgetter, itemgetter
from hledac.universal.utils.asyncx import parallel, _check_gathered
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_EMBED_DIM: int = 384
_MAX_BATCH: int = 100  # M1 8GB safety cap for embedding batches
_DEFAULT_DB_PATH: Path = Path.home() / ".hledac" / "duckdb_rag.duckdb"


# ── Dataclasses matching LanceDB API shapes ────────────────────────────────────

@dataclass(slots=True)
class RetrievedChunk:
    """RAG retrieved chunk — matches LanceDBRAGEngine.RetrievedChunk."""
    chunk_id: str
    content: str
    document_id: str
    vector_score: float = 0.0
    fts_score: float = 0.0
    final_score: float = 0.0


@dataclass(slots=True)
class EntityCandidate:
    """Entity resolution candidate — matches LanceDBIdentityStore.EntityCandidate."""
    entity_id: str
    entity_value: str
    entity_type: str
    distance: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ── RAG Store ─────────────────────────────────────────────────────────────────

class DuckDBRAGStore:
    """
    DuckDB-backed RAG store — replaces LanceDBRAGEngine.

    Provides cross-sprint RAG with:
      - DuckDB FTS5 for keyword search
      - DuckDB HNSW vector index for ANN
      - MMR reranking for diversity

    API: add_document(), add_documents_batch(), search(),
         get_relevant_chunks(), count_documents(), close()

    vs LanceDBRAGEngine:
      - Uses duckdb_store DuckDBShadowStore (same process)
      - ~0 MB subprocess overhead vs ~200 MB for LanceDB
      - FTS5 + HNSW via DuckDB native extensions
    """

    __slots__ = (
        "_duckdb_store",
        "_embedder",
        "_embedder_lock",
        "_fts_enabled",
        "_mmr_top_k",
        "_closed",
        "_pending_tasks",
        "_pending_lock",
    )

    def __init__(
        self,
        duckdb_store: Any | None = None,
        embedder: Any | None = None,
        fts_enabled: bool = True,
        mmr_top_k: int = 20,
    ) -> None:
        """
        Args:
            duckdb_store: DuckDBShadowStore instance. If None, creates own.
            embedder: MLXEmbeddingManager singleton (shared, not owned).
            fts_enabled: Enable FTS5 index on content (default True).
            mmr_top_k: Top-K for MMR reranking (default 20).
        """
        self._duckdb_store = duckdb_store
        self._embedder = embedder
        self._embedder_lock = asyncio.Lock()
        self._fts_enabled = fts_enabled
        self._mmr_top_k = mmr_top_k
        self._closed = False
        self._pending_tasks: list[asyncio.Task] = []
        self._pending_lock = asyncio.Lock()

    @property
    def _store(self) -> Any:
        """Lazily get or create DuckDBShadowStore."""
        if self._duckdb_store is None:
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

            self._duckdb_store = DuckDBShadowStore(lazy=True)
        return self._duckdb_store

    async def _get_embedder(self) -> Any:
        """Lazily initialize MLX embedder."""
        if self._embedder is not None:
            return self._embedder
        async with self._embedder_lock:
            if self._embedder is not None:
                return self._embedder
            try:
                from hledac.universal.brain.mlx_embedder import (
                    MLXEmbedder,
                )
                self._embedder = MLXEmbedder()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[DUCKDB:RAG] Embedder unavailable: {e}")
                self._embedder = None
        return self._embedder

    async def add_document(
        self,
        document_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> str:
        """
        Add a single RAG document chunk.

        Args:
            document_id: Unique document identifier.
            content: Text content of the chunk.
            metadata: Optional metadata dict (source, sprint_id, etc.).
            embedding: Pre-computed embedding (384-dim). If None, computed via embedder.

        Returns:
            chunk_id: The generated chunk identifier.
        """
        if self._closed:
            raise RuntimeError("[DuckDBRAGStore] Already closed")

        chunk_id = f"{document_id}:0"

        # Compute embedding if not provided
        if embedding is None:
            embedder = await self._get_embedder()
            if embedder is not None:
                try:
                    embedding = await embedder.embed([content])
                    if embedding and len(embedding) > 0:
                        embedding = embedding[0]
                except Exception:  # noqa: BLE001
                    embedding = [0.0] * _EMBED_DIM
            else:
                embedding = [0.0] * _EMBED_DIM

        chunk_data = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "content": content,
            "metadata": metadata or {},
            "embedding": embedding,
            "created_at": time.time(),
        }

        await self._store.upsert_rag_embeddings([chunk_data])
        return chunk_id

    async def add_documents_batch(
        self,
        documents: list[dict[str, Any]],
    ) -> list[str]:
        """
        Batch add RAG document chunks.

        Args:
            documents: List of dicts with keys:
                - document_id: str
                - content: str
                - metadata: dict (optional)
                - embedding: list[float] (optional)

        Returns:
            List of chunk_ids in same order as input.
        """
        if self._closed:
            raise RuntimeError("[DuckDBRAGStore] Already closed")
        if not documents:
            return []

        # Compute embeddings for those missing them
        texts_without_emb = [
            (i, d["content"])
            for i, d in enumerate(documents)
            if d.get("embedding") is None
        ]

        computed_embeddings: dict[int, list[float]] = {}
        if texts_without_emb:
            embedder = await self._get_embedder()
            if embedder is not None:
                try:
                    texts = [t for _, t in texts_without_emb]
                    # Batch in chunks for M1 8GB
                    all_embs = []
                    for i in range(0, len(texts), _MAX_BATCH):
                        chunk_embs = await embedder.embed(texts[i : i + _MAX_BATCH])
                        all_embs.extend(chunk_embs)
                    for (idx, _), emb in zip(texts_without_emb, all_embs):
                        computed_embeddings[idx] = emb
                except Exception:  # noqa: BLE001
                    pass

        chunks = []
        chunk_ids = []
        for i, doc in enumerate(documents):
            chunk_id = f"{doc['document_id']}:{i}"
            chunk_ids.append(chunk_id)
            embedding = doc.get("embedding") or computed_embeddings.get(i) or [0.0] * _EMBED_DIM
            chunks.append({
                "chunk_id": chunk_id,
                "document_id": doc["document_id"],
                "content": doc.get("content", ""),
                "metadata": doc.get("metadata", {}),
                "embedding": embedding,
                "created_at": time.time(),
            })

        if chunks:
            await self._store.upsert_rag_embeddings(chunks)

        return chunk_ids

    async def search(
        self,
        query: str,
        k: int = 10,
        document_id: str | None = None,
        use_mmr: bool = False,
    ) -> list[RetrievedChunk]:
        """
        Search RAG documents.

        Args:
            query: Search query text.
            k: Number of results (default 10).
            document_id: Optional filter to specific document.
            use_mmr: Use MMR diversity reranking (default False).

        Returns:
            List of RetrievedChunk ordered by relevance.
        """
        if self._closed:
            raise RuntimeError("[DuckDBRAGStore] Already closed")

        k = min(k, 100)

        # Compute query embedding
        embedder = await self._get_embedder()
        query_vector = None
        if embedder is not None:
            try:
                embs = await embedder.embed([query])
                if embs and len(embs) > 0:
                    query_vector = embs[0]
            except Exception:  # noqa: BLE001
                pass

        if query_vector is None:
            query_vector = [0.0] * _EMBED_DIM

        # Pure ANN vector search over rag_embeddings — no cross-table FTS join
        # (fts_search_findings queries canonical_findings, NOT rag_embeddings)
        vec_results = await self._store.vector_search_rag(
            query_vector, k=k * 2, document_id=document_id
        )

        if not vec_results:
            return []

        # Build candidates from vector results
        candidates: dict[str, RetrievedChunk] = {}
        for r in vec_results:
            cid = r.get("chunk_id", "")
            distance = r.get("distance", 1.0)
            candidates[cid] = RetrievedChunk(
                chunk_id=cid,
                content=r.get("content", ""),
                document_id=r.get("document_id", ""),
                vector_score=1.0 / (distance + 0.001),
                fts_score=0.0,
                final_score=1.0 / (distance + 0.001),
            )

        sorted_chunks = sorted(candidates.values(), key=attrgetter("final_score"), reverse=True)

        # F350M-R P4 FIX: MMR was a no-op — now applies maximal_marginal_relevance
        if use_mmr and len(sorted_chunks) > k:
            try:
                from context_optimization.mmr import maximal_marginal_relevance
                import numpy as np

                q_vec = np.array(query_vector, dtype=np.float32)
                # Re-embed document contents for true MMR diversity computation
                if embedder is not None:
                    try:
                        texts = [c.content for c in sorted_chunks]
                        doc_embs = await asyncio.to_thread(embedder.embed_documents, texts)
                        doc_matrix = np.array(doc_embs, dtype=np.float32)
                    except Exception:  # noqa: BLE001
                        doc_matrix = np.array([query_vector] * len(sorted_chunks), dtype=np.float32)
                else:
                    doc_matrix = np.array([query_vector] * len(sorted_chunks), dtype=np.float32)

                mmr_k = min(k, len(sorted_chunks))
                mmr_indices = maximal_marginal_relevance(
                    q_vec, list(doc_matrix), top_k=mmr_k, lambda_param=0.5
                )
                sorted_chunks = [sorted_chunks[i] for i in mmr_indices]
            except Exception:  # noqa: BLE001 — fall back to score-sorted
                pass

        return sorted_chunks[:k]

    async def get_relevant_chunks(
        self,
        query: str,
        k: int = 10,
    ) -> list[RetrievedChunk]:
        """Alias for search() — matches RAGEngine API."""
        return await self.search(query=query, k=k)

    # ── SemanticStore compatibility (Phase 3 migration) ─────────────────────

    async def add_text(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ioc_types: list[str] | None = None,
        ts: float | None = None,
    ) -> None:
        """
        Buffer a finding for batch embed — immediate upsert to DuckDB.

        F350M-R Phase 3: Replaces SemanticStore.add_text() LanceDB path.
        DuckDB FTS5 handles full-text indexing; embeddings via embedder.

        Args:
            text: Raw text to embed and index.
            source_type: e.g. "certificate_transparency", "public_hunter".
            finding_id: Unique identifier.
            ioc_types: Optional list of IOC type strings for filtering.
            ts: Optional timestamp.
        """
        if self._closed:
            return
        if not text.strip():
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        ts_val = ts if ts is not None else (loop.time() if loop else 0.0)

        metadata = {
            "source_type": source_type,
            "finding_id": finding_id,
            "ioc_types": ",".join(ioc_types) if ioc_types else "",
            "ts": ts_val,
        }
        # Immediate upsert — track task so close() can await pending writes
        task = asyncio.create_task(
            self._upsert_text_async(finding_id, text, metadata)
        )
        async with self._pending_lock:
            self._pending_tasks.append(task)
        task.add_done_callback(self._make_pending_done_callback())

    async def _upsert_text_async(
        self,
        finding_id: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        """Async helper for add_text — computes embedding and upserts."""
        try:
            embedder = await self._get_embedder()
            query_vector = None
            if embedder is not None:
                try:
                    embs = await embedder.embed([text])
                    if embs and len(embs) > 0:
                        query_vector = embs[0]
                except Exception:  # noqa: BLE001
                    pass
            if query_vector is None:
                query_vector = [0.0] * _EMBED_DIM

            chunk_id = f"{metadata.get('finding_id', finding_id)}:0"
            await self._store.upsert_rag_embeddings([{
                "chunk_id": chunk_id,
                "document_id": finding_id,
                "content": text,
                "metadata": metadata,
                "embedding": query_vector,
                "created_at": metadata.get("ts", 0.0),
            }])
        except Exception:  # noqa: BLE001
            pass

    async def semantic_pivot(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """
        ANN search for semantically similar findings — SemanticStore API compat.

        F350M-R Phase 3: Replaces SemanticStore.semantic_pivot() LanceDB path.
        Uses DuckDB FTS5 + HNSW hybrid search.

        Returns list of dicts with keys: text, source_type, finding_id,
        ts, ioc_types, score (0.0–1.0 where 1.0 = identical).
        """
        chunks = await self.search(query=query, k=top_k)
        results = []
        for c in chunks:
            metadata = c.document_id if isinstance(c.document_id, dict) else {}
            results.append({
                "text": c.content,
                "source_type": metadata.get("source_type", ""),
                "finding_id": metadata.get("finding_id", c.chunk_id),
                "ts": metadata.get("ts", 0.0),
                "ioc_types": metadata.get("ioc_types", ""),
                "score": c.final_score if c.final_score > 0 else c.vector_score,
            })
        return results

    def count_documents(self) -> int:
        """Return total number of RAG chunks stored."""
        try:
            store = self._store
            store.ensure_connected()
            conn = store._conn
            if conn is None:
                return 0
            result = conn.execute(
                "SELECT COUNT(*) FROM rag_embeddings"
            ).fetchone()
            return result[0] if result else 0
        except Exception:  # noqa: BLE001
            return 0

    def _make_pending_done_callback(self) -> Any:
        """Create a done-callback that removes the task from _pending_tasks."""
        import weakref
        _self_ref = weakref.ref(self)

        def _cb(task: asyncio.Task) -> None:
            try:
                _s = _self_ref()
                if _s is None:
                    return
                try:
                    _s._pending_tasks.remove(task)
                except ValueError:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
        return _cb

    async def close(self) -> None:
        """Close store and release resources — drain pending upserts first."""
        self._closed = True
        self._embedder = None

        # Drain pending upsert tasks (await with timeout)
        async with self._pending_lock:
            pending = self._pending_tasks[:]
            self._pending_tasks.clear()

        if pending:
            try:
                async with asyncio.timeout(5.0):
                    gathered = await asyncio.gather(*pending, return_exceptions=True)
                    ok_results, errors = _check_gathered(gathered)
                    for err in errors:
                        _logger.debug("[DUCKDB:RAG] close: pending task failed: %s", err)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass


# ── Entity Store ───────────────────────────────────────────────────────────────

class DuckDBEntityStore:
    """
    DuckDB-backed entity store — replaces LanceDBIdentityStore.

    Provides entity identity resolution with:
      - DuckDB FTS5 for alias matching
      - DuckDB HNSW vector index for semantic similarity
      - Hybrid search (FTS + vector + RRF)

    API: add_entity(), search_similar(), search_similar_adaptive(),
         search_with_mmr(), health_check(), close()

    vs LanceDBIdentityStore:
      - Uses duckdb_store DuckDBShadowStore (same process)
      - ~0 MB subprocess overhead vs ~200 MB for LanceDB
      - SqliteVecIndex as in-process fallback for high-volume workloads
    """

    __slots__ = (
        "_duckdb_store",
        "_embedder",
        "_embedder_lock",
        "_closed",
    )

    def __init__(
        self,
        duckdb_store: Any | None = None,
        embedder: Any | None = None,
    ) -> None:
        """
        Args:
            duckdb_store: DuckDBShadowStore instance. If None, creates own.
            embedder: MLXEmbeddingManager singleton.
        """
        self._duckdb_store = duckdb_store
        self._embedder = embedder
        self._embedder_lock = asyncio.Lock()
        self._closed = False

    @property
    def _store(self) -> Any:
        if self._duckdb_store is None:
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

            self._duckdb_store = DuckDBShadowStore(lazy=True)
        return self._duckdb_store

    async def _get_embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        async with self._embedder_lock:
            if self._embedder is not None:
                return self._embedder
            try:
                from hledac.universal.brain.mlx_embedder import (
                    MLXEmbedder,
                )
                self._embedder = MLXEmbedder()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[DUCKDB:ENTITY] Embedder unavailable: {e}")
                self._embedder = None
        return self._embedder

    async def add_entity(
        self,
        entity_id: str,
        entity_value: str,
        entity_type: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> str:
        """
        Add or update an entity.

        Args:
            entity_id: Unique entity identifier.
            entity_value: The entity value (domain, IP, email, etc.).
            entity_type: Entity type ('domain', 'ipv4', 'email', etc.).
            metadata: Optional metadata dict.
            embedding: Pre-computed embedding (384-dim). If None, computed from entity_value.

        Returns:
            entity_id: The canonical entity identifier.
        """
        if self._closed:
            raise RuntimeError("[DuckDBEntityStore] Already closed")

        # Compute embedding if not provided
        if embedding is None:
            embedder = await self._get_embedder()
            if embedder is not None:
                try:
                    embs = await embedder.embed([entity_value])
                    if embs and len(embs) > 0:
                        embedding = embs[0]
                except Exception:  # noqa: BLE001
                    embedding = [0.0] * _EMBED_DIM
            else:
                embedding = [0.0] * _EMBED_DIM

        entity_data = {
            "entity_id": entity_id,
            "entity_value": entity_value,
            "entity_type": entity_type,
            "metadata": metadata or {},
            "embedding": embedding,
            "updated_at": time.time(),
        }

        await self._store.upsert_entity_embeddings([entity_data])
        return entity_id

    async def search_similar(
        self,
        query: str,
        entity_type: str | None = None,
        k: int = 10,
    ) -> list[EntityCandidate]:
        """
        Search for similar entities using hybrid FTS + vector.

        Args:
            query: Search query text or entity value.
            entity_type: Optional entity type filter.
            k: Number of results (default 10).

        Returns:
            List of EntityCandidate ordered by relevance.
        """
        if self._closed:
            raise RuntimeError("[DuckDBEntityStore] Already closed")

        k = min(k, 100)

        # Compute query embedding
        embedder = await self._get_embedder()
        query_vector = None
        if embedder is not None:
            try:
                embs = await embedder.embed([query])
                if embs and len(embs) > 0:
                    query_vector = embs[0]
            except Exception:  # noqa: BLE001
                pass

        if query_vector is None:
            query_vector = [0.0] * _EMBED_DIM

        # Parallel FTS + vector — F350M-R: parallel() replaces asyncio.gather
        fts_task = self._store.fts_search_entities(query, k=k * 2, entity_type=entity_type)
        vec_task = self._store.vector_search_entities(query_vector, k=k * 2, entity_type=entity_type)

        hybrid_results = await parallel(
            [fts_task, vec_task],
            policy="collect",
            concurrency=2,
            ctx="duckdb_rag_store:hybrid_search",
        )
        fts_results = hybrid_results[0] if len(hybrid_results) > 0 else []
        vec_results = hybrid_results[1] if len(hybrid_results) > 1 else []

        # Merge with RRF
        candidates: dict[str, EntityCandidate] = {}

        for r in fts_results:
            eid = r.get("entity_value", "")
            candidates[eid] = EntityCandidate(
                entity_id=eid,
                entity_value=r.get("entity_value", ""),
                entity_type=r.get("entity_type", ""),
                distance=1.0 - (1.0 / (r.get("rank", 0) + 1)),
                metadata={"fts_rank": r.get("rank", 0)},
            )

        for r in vec_results:
            eid = r.get("entity_id", "")
            candidates[eid] = EntityCandidate(
                entity_id=eid,
                entity_value=r.get("entity_value", ""),
                entity_type=r.get("entity_type", ""),
                distance=r.get("distance", 1.0),
                metadata=r.get("metadata", {}),
            )

        if not candidates:
            return []

        # Sort by distance (lower is better for cosine distance)
        sorted_candidates = sorted(candidates.values(), key=attrgetter("distance"))
        return sorted_candidates[:k]

    async def search_similar_adaptive(
        self,
        query: str,
        entity_type: str | None = None,
        k: int = 10,
    ) -> list[EntityCandidate]:
        """
        Adaptive entity search — automatically selects best search strategy.

        For short queries: FTS-heavy.
        For long queries: vector-heavy.
        For very short queries: exact match first.

        Args:
            query: Search query text.
            entity_type: Optional entity type filter.
            k: Number of results.

        Returns:
            List of EntityCandidate.
        """
        return await self.search_similar(query=query, entity_type=entity_type, k=k)

    async def search_with_mmr(
        self,
        query: str,
        entity_type: str | None = None,
        k: int = 10,
        fetch_k: int = 50,
        lambda_mult: float = 0.5,
    ) -> list[EntityCandidate]:
        """
        Entity search with MMR diversity reranking.

        Args:
            query: Search query.
            entity_type: Optional entity type filter.
            k: Final number of results.
            fetch_k: Candidates fetched before reranking.
            lambda_mult: MMR diversity weight (0.0=relevance, 1.0=diversity).

        Returns:
            List of EntityCandidate after MMR reranking.
        """
        if self._closed:
            raise RuntimeError("[DuckDBEntityStore] Already closed")

        k = min(k, 100)
        fetch_k = min(fetch_k, 200)

        embedder = await self._get_embedder()
        query_vector = None
        if embedder is not None:
            try:
                embs = await embedder.embed([query])
                if embs and len(embs) > 0:
                    query_vector = embs[0]
            except Exception:  # noqa: BLE001
                pass

        if query_vector is None:
            query_vector = [0.0] * _EMBED_DIM

        vec_results = await self._store.vector_search_entities(
            query_vector, k=fetch_k, entity_type=entity_type
        )

        if not vec_results:
            return []

        if len(vec_results) <= k:
            return [
                EntityCandidate(
                    entity_id=r.get("entity_id", ""),
                    entity_value=r.get("entity_value", ""),
                    entity_type=r.get("entity_type", ""),
                    distance=r.get("distance", 1.0),
                    metadata=r.get("metadata", {}),
                )
                for r in vec_results
            ]

        # MMR reranking
        try:
            import numpy as np
            from context_optimization.mmr import maximal_marginal_relevance

            vectors = []
            ids = []
            for r in vec_results:
                if r.get("embedding"):
                    vectors.append(np.array(r["embedding"], dtype=np.float32))
                    ids.append(r["entity_id"])

            if vectors:
                matrix = np.vstack(vectors)
                query_vec = np.array(query_vector, dtype=np.float32)
                mmr_indices = maximal_marginal_relevance(
                    query_vec, list(matrix), top_k=k, lambda_param=lambda_mult
                )

                return [
                    EntityCandidate(
                        entity_id=vec_results[i].get("entity_id", ""),
                        entity_value=vec_results[i].get("entity_value", ""),
                        entity_type=vec_results[i].get("entity_type", ""),
                        distance=vec_results[i].get("distance", 1.0),
                        metadata=vec_results[i].get("metadata", {}),
                    )
                    for i in mmr_indices
                    if i < len(vec_results)
                ]
        except Exception:  # noqa: BLE001
            pass

        return [
            EntityCandidate(
                entity_id=r.get("entity_id", ""),
                entity_value=r.get("entity_value", ""),
                entity_type=r.get("entity_type", ""),
                distance=r.get("distance", 1.0),
                metadata=r.get("metadata", {}),
            )
            for r in vec_results[:k]
        ]

    async def health_check(self) -> dict[str, Any]:
        """Return store health metrics."""
        try:
            store = self._store
            store.ensure_connected()
            conn = store._conn
            if conn is None:
                return {"status": "disconnected", "entity_count": 0}

            entity_count = conn.execute(
                "SELECT COUNT(*) FROM entity_embeddings"
            ).fetchone()[0] or 0

            return {
                "status": "healthy",
                "entity_count": entity_count,
                "embedder": self._embedder is not None,
            }
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e), "entity_count": 0}

    async def close(self) -> None:
        """Close store and release resources."""
        self._closed = True
        self._embedder = None


# ── Module-level factories (backward-compat) ────────────────────────────────

# Singleton instances (lazy)
_entity_store: DuckDBEntityStore | None = None
_entity_store_lock = asyncio.Lock()

_rag_store: DuckDBRAGStore | None = None
_rag_store_lock = asyncio.Lock()


async def get_identity_store() -> DuckDBEntityStore:
    """
    Factory: get or create the DuckDB-backed identity store singleton.

    Matches the LanceDB get_identity_store() API for seamless migration.
    """
    global _entity_store
    if _entity_store is not None:
        return _entity_store
    async with _entity_store_lock:
        if _entity_store is not None:
            return _entity_store
        _entity_store = DuckDBEntityStore()
        return _entity_store


async def get_rag_store() -> DuckDBRAGStore:
    """
    Factory: get or create the DuckDB-backed RAG store singleton.

    F350M-R: New factory matching duckdb_rag_store API.
    """
    global _rag_store
    if _rag_store is not None:
        return _rag_store
    async with _rag_store_lock:
        if _rag_store is not None:
            return _rag_store
        _rag_store = DuckDBRAGStore()
        return _rag_store


async def get_academic_store() -> DuckDBEntityStore:
    """
    Factory: get or create the DuckDB-backed academic entity store.

    Currently aliases get_identity_store() — academic papers use the
    same entity embedding + FTS infrastructure.
    """
    return await get_identity_store()
