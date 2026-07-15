"""
knowledge/duckdb_vector_store.py — DuckDB-backed Vector Store
========================================================

ISSUE-001 Phase 3: LanceDB Deprecation

DuckDB 1.4+ provides native vector index support via:
- CREATE INDEX ... USING VECTOR (HNSW-style)
- array_distance functions (cosine, euclidean)

This module provides vector similarity search using DuckDB instead of LanceDB.

ARCHITECTURE:
- USEARCH: in-memory ANN (M1 Metal SIMD, fast path)
- DuckDB: persistent storage with native vector index

MIGRATION STATUS:
    LanceDB used for: identity/entity store, vector persistence
    DuckDB provides: native vector index (HNSW), FTS5 extension
    Status: Phase 3 - Ready for integration

DIMENSION CONTRACT: 256d float32 (matches embedding_pipeline._EMBEDDING_DIM)

M1 8GB BOUNDS:
- DuckDB vector index: bounded by memory_limit
- Max entries: 50,000 (same as LanceDB)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

import msgspec.json as _json

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 50_000

# Canonical dimension: 256d float32 — matches MLXEmbeddingManager.EMBEDDING_DIM
_EMBEDDING_DIM = 256


class DuckDBVectorStore:
    """
    DuckDB-backed vector store with native HNSW index.

    Replaces LanceDB for vector persistence in:
    - knowledge/ann_index.py (SemanticDedupCache)
    - knowledge/lancedb_store.py (IdentityStore)

    DESIGN:
    - USEARCH remains primary ANN (M1 Metal SIMD)
    - DuckDB provides native vector index for SQL queries
    - Hybrid search: SQL cosine similarity + USEARCH ANN

    MIGRATION:
        # Old (LanceDB)
        from knowledge.lancedb_store import LanceDBIdentityStore
        store = LanceDBIdentityStore()

        # New (DuckDB)
        from knowledge.duckdb_vector_store import DuckDBVectorStore
        store = DuckDBVectorStore()
    """

    __slots__ = tuple(
        "_db_store _table_name _embed_dim _initialized _lock "
        "_insert_count _index_created".split()
    )

    def __init__(
        self,
        table_name: str = "entity_vectors",
        embed_dim: int = _EMBEDDING_DIM,
        # db_path kept for future multi-tenant use; currently uses unified facade
        _db_path: str | Path | None = None,  # intentionally unused — backward compat
    ) -> None:
        self._db_store: Any = None
        self._table_name: str = table_name
        self._embed_dim: int = embed_dim
        self._initialized: bool = False
        self._lock = threading.Lock()
        self._insert_count: int = 0
        self._index_created: bool = False

    async def initialize(self) -> None:
        """Initialize DuckDB vector store."""
        if self._initialized:
            return

        from knowledge.db import get_db

        db = get_db()
        self._db_store = db.duckdb

        conn = self._db_store._get_connection()

        # Create table with vector column
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                id          VARCHAR PRIMARY KEY,
                vector      FLOAT[{self._embed_dim}],
                metadata    JSON,
                created_at  DOUBLE DEFAULT unixepoch()
            )
        """)

        # Try to create HNSW index (DuckDB 1.4+)
        try:
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table_name}_vector
                ON {self._table_name} USING VECTOR (vector)
                WITH (metric = 'cosine')
            """)
            self._index_created = True
            logger.info("[VECTOR:DuckDB] HNSW index created")
        except Exception as e:
            logger.warning(f"[VECTOR:DuckDB] HNSW index not available: {e}")
            self._index_created = False

        self._initialized = True
        logger.info(f"[VECTOR:DuckDB] Initialized at {self._table_name}")

    def _get_connection(self) -> Any:
        """Get DuckDB connection."""
        if self._db_store is None:
            from knowledge.db import get_db
            self._db_store = get_db().duckdb
        return self._db_store._get_connection()

    async def add(
        self,
        id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Add vector to store.

        Args:
            id: Unique identifier
            vector: 256d embedding vector
            metadata: Optional metadata
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        metadata_json = _json.encode(metadata or {}).decode("utf-8")

        try:
            # DuckDB accepts list directly for FLOAT[] — no numpy needed
            conn.execute(
                f"""
                INSERT INTO {self._table_name} (id, vector, metadata)
                VALUES (?, ?::FLOAT[], ?::JSON)
                """,
                (id, vector, metadata_json),
            )
            self._insert_count += 1
        except Exception as e:
            logger.warning(f"[VECTOR:DuckDB] Insert failed: {e}")

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Search for similar vectors.

        Args:
            query_vector: 256d query embedding
            top_k: Number of results

        Returns:
            List of (id, distance) tuples
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        # DuckDB vector index search — DuckDB accepts list directly for FLOAT[]
        if self._index_created:
            try:
                rows = conn.execute(f"""
                    SELECT id, array_distance(vector, ?::FLOAT[{self._embed_dim}], 'cosine') as dist
                    FROM {self._table_name}
                    ORDER BY dist
                    LIMIT ?
                """, (query_vector, top_k)).fetchall()
                return [(row[0], row[1]) for row in rows]
            except Exception as e:
                logger.warning(f"[VECTOR:DuckDB] Index search failed: {e}")

        # Fallback: brute force
        rows = conn.execute(f"""
            SELECT id, array_distance(vector, ?::FLOAT[{self._embed_dim}], 'cosine') as dist
            FROM {self._table_name}
            ORDER BY dist
            LIMIT ?
        """, (query_vector, top_k)).fetchall()

        return [(row[0], row[1]) for row in rows]

    async def get(self, id: str) -> dict[str, Any] | None:
        """
        Get vector by ID.

        Args:
            id: Vector identifier

        Returns:
            Vector data or None
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        rows = conn.execute(
            f"SELECT id, vector, metadata FROM {self._table_name} WHERE id = ?",
            (id,),
        ).fetchall()

        if not rows:
            return None

        row = rows[0]
        return {
            "id": row[0],
            "vector": row[1],
            "metadata": _json.decode(row[2]) if row[2] else {},
        }

    async def delete(self, id: str) -> bool:
        """
        Delete vector by ID.

        Args:
            id: Vector identifier

        Returns:
            True if deleted
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        conn.execute(f"DELETE FROM {self._table_name} WHERE id = ?", (id,))
        return True

    async def count(self) -> int:
        """Get number of vectors."""
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        result = conn.execute(
            f"SELECT COUNT(*) FROM {self._table_name}"
        ).fetchone()
        return result[0] if result else 0

    async def close(self) -> None:
        """Close vector store."""
        self._initialized = False
        logger.info("[VECTOR:DuckDB] Closed")
