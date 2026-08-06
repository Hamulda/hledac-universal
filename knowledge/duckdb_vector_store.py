"""DuckDB Vector Store — F360: Extracted from DuckDBShadowStore.

Owns all DuckDB HNSW vector operations:
  - rag_embeddings table (cross-sprint RAG document chunks)

  - entity_embeddings table (entity identity vectors)
  - DuckDB array_cosine_distance with HNSW index

ARCHITECTURE:
  DuckDBVectorStore is COMPOSED into DuckDBCanonical (not inherited).
  DuckDBVectorStore requires a DuckDB connection (duckdb_conn).

  duckdb_store.py          ← DuckDBShadowStore (current monolithic)
  duckdb_vector_store.py   ← F360: Extracted vector operations
  duckdb_canonical.py      ← F360: DuckDBCanonical (future)

M1 8GB constraints:
  - k capped at 100 for all ANN searches (prevents runaway memory)
  - fetch_k capped at 200 for MMR
  - Sequential scan fallback when HNSW unavailable

vs lancedb_store.py:
  LanceDB-backed RAG is DEPRECATED in favour of DuckDB HNSW.
  lancedb_store.py is 101KB of dead code (0 production callers).
  duckdb_rag_store.py is a thin facade over DuckDBShadowStore.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import orjson

from hledac.universal.utils.async_helpers import _check_gathered

_logger = logging.getLogger(__name__)

# Maximum k for ANN search (M1 8GB safety cap)
_MAX_ANN_K = 100
# Maximum fetch_k for MMR (M1 8GB safety cap)
_MAX_MMR_FETCH_K = 200


class DuckDBVectorStore:
    """
    DuckDB HNSW vector operations — extracted from DuckDBShadowStore.

    Owns:
      - rag_embeddings table (chunk_id, document_id, content, metadata_json, embedding)
      - entity_embeddings table (entity_id, entity_value, entity_type, metadata_json, embedding)
      - All vector search operations using DuckDB array_cosine_distance + HNSW

    DuckDB 1.5+ native HNSW index with cosine distance.
    Falls back to sequential scan if HNSW extension unavailable.
    """

    __slots__ = (
        "_duckdb_conn",      # DuckDB connection (from DuckDBCanonical)
        "_executor",         # ThreadPoolExecutor for sync DuckDB calls
        "_initialized",      # Schema initialized flag
        "_rag_schema_initialized",
        "_entity_schema_initialized",
    )

    def __init__(
        self,
        duckdb_conn: Any,
        executor: Any,
    ) -> None:
        """
        Args:
            duckdb_conn: DuckDB connection object (from DuckDBCanonical._conn).
                        Must be already connected and schema-initialized.
            executor: ThreadPoolExecutor for async-to-sync DuckDB calls.
        """
        self._duckdb_conn = duckdb_conn
        self._executor = executor
        self._initialized = False
        self._rag_schema_initialized = False
        self._entity_schema_initialized = False

    @property
    def _conn(self) -> Any:
        """Expose connection for subclasses/facades."""
        return self._duckdb_conn

    # ── Schema init ────────────────────────────────────────────────────────────

    async def _ensure_schema(self) -> None:
        """Ensure rag_embeddings and entity_embeddings tables exist.

        Tables are created via 0003_fts5_vector_schema.sql migration.
        This method is a no-op once initialized (CREATE TABLE IF NOT EXISTS).
        """
        if self._rag_schema_initialized and self._entity_schema_initialized:
            return
        # Schema is managed by DuckDBCanonical._init_connection via migration.
        # Vector tables are created by DuckDBMigrationManager.
        self._rag_schema_initialized = True
        self._entity_schema_initialized = True
        self._initialized = True

    # ── RAG Embeddings ────────────────────────────────────────────────────────

    async def upsert_rag_embeddings(
        self,
        chunks: list[dict[str, Any]],
    ) -> int:
        """
        Batch upsert RAG document chunk embeddings.

        Stores in rag_embeddings table with LIST<FLOAT> vectors.
        Uses INSERT OR REPLACE for idempotent upserts.

        Args:
            chunks: List of dicts with keys:
                - chunk_id: str (primary key)
                - document_id: str
                - content: str
                - metadata: dict (serialized to JSON)
                - embedding: list[float] (384-dim)
                - created_at: float (unix timestamp)

        Returns:
            Number of chunks upserted.
        """
        if not chunks:
            return 0

        await self._ensure_schema()
        if self._duckdb_conn is None:
            return 0

        rows_inserted = 0

        # Sprint FXXX: Parallel chunk upsert via asyncio.gather()
        # Speedup: ~4-8× for large chunk lists on I/O-bound DuckDB writes.
        _CHUNK_PARALLEL_BATCH = 32  # M1 8GB: bounded concurrency

        async def _upsert_chunk(chunk: dict[str, Any]) -> bool:
            """Upsert single chunk, returns True on success."""
            try:
                # ARCH-DB-001: Validate chunk against RAGChunkContract
                from hledac.universal.knowledge.storage_contracts import validate_rag_chunk

                validated = validate_rag_chunk(chunk)
                if validated is None:
                    _logger.debug(
                        "[ARCH-DB-001] RAG chunk validation failed for chunk_id=%s",
                        chunk.get("chunk_id", "?"),
                    )
                    return False  # Skip write — validation failed

                # Validation passed — proceed with upsert
                embedding_list: list[float] = chunk.get("embedding", [])
                metadata_json = orjson.dumps(chunk.get("metadata", {}))
                self._duckdb_conn.execute(
                    """
                    INSERT OR REPLACE INTO rag_embeddings
                    (chunk_id, document_id, content, metadata_json, embedding, embedding_dim, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(chunk["chunk_id"]),
                        str(chunk["document_id"]),
                        str(chunk.get("content", "")),
                        metadata_json,
                        embedding_list,
                        len(embedding_list),
                        float(chunk.get("created_at", 0.0)),
                    ],
                )
                return True
            except Exception as e:  # noqa: BLE001 — best-effort per chunk
                _logger.debug(
                    "[DUCKDB:VEC] upsert_rag_embeddings failed for %s: %s",
                    chunk.get("chunk_id", "?"),
                    e,
                )
                return False

        # Process chunks in parallel batches to avoid unbounded concurrency
        for batch_start in range(0, len(chunks), _CHUNK_PARALLEL_BATCH):
            batch = chunks[batch_start : batch_start + _CHUNK_PARALLEL_BATCH]
            results = await asyncio.gather(*[_upsert_chunk(c) for c in batch], return_exceptions=True)
            ok_results, errors = _check_gathered(results)
            for err in errors:
                _logger.debug("[DUCKDB:VEC] upsert_rag_embeddings chunk failed: %s", err)
            rows_inserted += sum(1 for r in ok_results if r is True)

        return rows_inserted

    async def vector_search_rag(
        self,
        query_vector: list[float],
        k: int = 10,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search over rag_embeddings using DuckDB HNSW.

        Uses array_cosine_distance with HNSW index (or sequential scan fallback).
        M1 8GB: bounded to k <= 100 to prevent runaway memory.

        Args:
            query_vector: 384-dim query embedding.
            k: Number of results (default 10, max 100).
            document_id: Optional filter to specific document.

        Returns:
            List of dicts: {chunk_id, document_id, content, metadata_json, distance}
        """
        await self._ensure_schema()
        k = min(k, _MAX_ANN_K)

        if self._duckdb_conn is None:
            return []

        try:
            if document_id is not None:
                sql = """
                    SELECT
                        chunk_id,
                        document_id,
                        content,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM rag_embeddings
                    WHERE document_id = ?
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    self._duckdb_conn.execute, sql, [query_vector, document_id, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        chunk_id,
                        document_id,
                        content,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM rag_embeddings
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    self._duckdb_conn.execute, sql, [query_vector, k]
                ).fetchall()

            return [
                {
                    "chunk_id": str(r[0]),
                    "document_id": str(r[1]),
                    "content": r[2] or "",
                    "metadata": orjson.loads(r[3]) if r[3] else {},
                    "distance": float(r[4]) if r[4] is not None else 1.0,
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001 — HNSW unavailable or query error
            _logger.debug("[DUCKDB:VEC] vector_search_rag failed: %s", e)
            return []

    async def vector_search_rag_mmr(
        self,
        query_vector: list[float],
        k: int = 10,
        fetch_k: int = 50,
        lambda_mult: float = 0.5,
    ) -> list[dict[str, Any]]:
        """
        ANN search with Maximal Marginal Relevance (MMR) diversity.

        Fetches fetch_k candidates, then reranks using MMR to balance
        relevance (cosine similarity) with document diversity.

        Args:
            query_vector: 384-dim query embedding.
            k: Final number of results (after MMR, default 10, max 100).
            fetch_k: Number of candidates to fetch before reranking (max 200).
            lambda_mult: MMR diversity weight (0.0=all relevance, 1.0=all diversity).

        Returns:
            List of dicts: {chunk_id, document_id, content, metadata, distance}
        """
        await self._ensure_schema()
        k = min(k, _MAX_ANN_K)
        fetch_k = min(fetch_k, _MAX_MMR_FETCH_K)

        # Fetch candidates
        candidates = await self.vector_search_rag(query_vector, k=fetch_k)
        if not candidates:
            return []

        if len(candidates) <= k:
            return candidates

        # MMR reranking
        try:
            from context_optimization.mmr import maximal_marginal_relevance

            import numpy as np

            vectors: list[Any] = []
            ids: list[str] = []
            for c in candidates:
                if c.get("embedding") is not None:
                    vectors.append(np.array(c["embedding"], dtype=np.float32))
                    ids.append(c["chunk_id"])
                elif c.get("distance") is not None:
                    vectors.append(
                        np.array(query_vector, dtype=np.float32) * (1.0 - c["distance"])
                    )
                    ids.append(c["chunk_id"])

            if not vectors:
                return candidates[:k]

            matrix = np.vstack(vectors)
            query_vec = np.array(query_vector, dtype=np.float32)

            mmr_indices = maximal_marginal_relevance(
                query_vec, matrix, k=k, lambda_mult=lambda_mult
            )

            return [candidates[i] for i in mmr_indices if i < len(candidates)]

        except Exception as e:  # noqa: BLE001
            _logger.debug("[DUCKDB:VEC] MMR reranking failed: %s", e)
            return candidates[:k]

    # ── Entity Embeddings ──────────────────────────────────────────────────────

    async def upsert_entity_embeddings(
        self,
        entities: list[dict[str, Any]],
    ) -> int:
        """
        Batch upsert entity embeddings for identity resolution.

        Args:
            entities: List of dicts with keys:
                - entity_id: str (primary key)
                - entity_value: str
                - entity_type: str (e.g., 'domain', 'ipv4', 'email')
                - metadata: dict
                - embedding: list[float] (384-dim)
                - updated_at: float (unix timestamp)

        Returns:
            Number of entities upserted.
        """
        if not entities:
            return 0

        await self._ensure_schema()
        if self._duckdb_conn is None:
            return 0

        # Parallel entity upsert via asyncio.gather() — consistent with RAG path
        _ENTITY_PARALLEL_BATCH = 32  # M1 8GB: bounded concurrency

        async def _upsert_entity(entity: dict[str, Any]) -> bool:
            """Upsert single entity, returns True on success."""
            try:
                # ARCH-DB-001: Validate entity against EntityEmbeddingContract
                # before upsert. Fail-safe: validation errors are logged but
                # do NOT block storage — invalid data is skipped, not written.
                from hledac.universal.knowledge.storage_contracts import validate_entity_embedding

                validated = validate_entity_embedding(entity)
                if validated is None:
                    _logger.debug(
                        "[ARCH-DB-001] Entity embedding validation failed for entity_id=%s",
                        entity.get("entity_id", "?"),
                    )
                    return False  # Skip write — validation failed

                # Validation passed — proceed with upsert
                embedding_list: list[float] = entity.get("embedding", [])
                metadata_json = orjson.dumps(entity.get("metadata", {}))
                self._duckdb_conn.execute(
                    """
                    INSERT OR REPLACE INTO entity_embeddings
                    (entity_id, entity_value, entity_type, metadata_json, embedding, embedding_dim, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(entity["entity_id"]),
                        str(entity["entity_value"]),
                        str(entity.get("entity_type", "")),
                        metadata_json,
                        embedding_list,
                        len(embedding_list),
                        float(entity.get("updated_at", 0.0)),
                    ],
                )
                return True
            except Exception as e:  # noqa: BLE001
                _logger.debug(
                    "[DUCKDB:VEC] upsert_entity_embeddings failed for %s: %s",
                    entity.get("entity_id", "?"),
                    e,
                )
                return False

        # Process entities in parallel batches to avoid unbounded concurrency
        rows_inserted = 0
        for batch_start in range(0, len(entities), _ENTITY_PARALLEL_BATCH):
            batch = entities[batch_start : batch_start + _ENTITY_PARALLEL_BATCH]
            results = await asyncio.gather(*[_upsert_entity(e) for e in batch], return_exceptions=True)
            ok_results, errors = _check_gathered(results)
            for err in errors:
                _logger.debug("[DUCKDB:VEC] upsert_entity_embeddings batch failed: %s", err)
            rows_inserted += sum(1 for r in ok_results if r is True)

        return rows_inserted

    async def vector_search_entities(
        self,
        query_vector: list[float],
        k: int = 10,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN vector search over entity_embeddings.

        Used for entity identity clustering and alias resolution.

        Args:
            query_vector: 384-dim query embedding.
            k: Number of results (default 10, max 100).
            entity_type: Optional entity type filter.

        Returns:
            List of dicts: {entity_id, entity_value, entity_type, metadata_json, distance}
        """
        await self._ensure_schema()
        k = min(k, _MAX_ANN_K)

        if self._duckdb_conn is None:
            return []

        try:
            if entity_type is not None:
                sql = """
                    SELECT
                        entity_id,
                        entity_value,
                        entity_type,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM entity_embeddings
                    WHERE entity_type = ?
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    self._duckdb_conn.execute, sql, [query_vector, entity_type, k]
                ).fetchall()
            else:
                sql = """
                    SELECT
                        entity_id,
                        entity_value,
                        entity_type,
                        metadata_json,
                        array_cosine_distance(embedding, ?) AS distance
                    FROM entity_embeddings
                    ORDER BY distance ASC
                    LIMIT ?
                """
                rows = await asyncio.to_thread(
                    self._duckdb_conn.execute, sql, [query_vector, k]
                ).fetchall()

            return [
                {
                    "entity_id": str(r[0]),
                    "entity_value": str(r[1]),
                    "entity_type": r[2],
                    "metadata": orjson.loads(r[3]) if r[3] else {},
                    "distance": float(r[4]) if r[4] is not None else 1.0,
                }
                for r in rows
            ]

        except Exception as e:  # noqa: BLE001
            _logger.debug("[DUCKDB:VEC] vector_search_entities failed: %s", e)
            return []

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        """
        F360M: Close DuckDBVectorStore — no-op since DuckDB connection
        is owned by DuckDBShadowStore (composed, not owned).

        Called by DuckDBShadowStore._cleanup_vector_store() during shutdown.
        This method exists for API compatibility with the cleanup protocol.
        The actual DuckDB connection is closed by DuckDBShadowStore._do_sync_close().
        """
        # DuckDBVectorStore does NOT own the connection — DuckDBShadowStore does.
        # Setting _duckdb_conn to None signals that the store is closed.
        self._duckdb_conn = None
        self._initialized = False
        self._rag_schema_initialized = False
        self._entity_schema_initialized = False
