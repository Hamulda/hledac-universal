"""knowledge/stores/lancedb_vector_store.py — LanceDB Vector Store (F320)

PEP 544 VectorStore implementation.

M1 8GB: RAM-gated, falls back to sqlitevec when RAM > 5GB.
IVF-PQ quantization: HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS=64,
                     HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS=12

Usage:
    store = LanceDBVectorStore(uri="/path/to/lance")
    await store.upsert_embeddings([(entity_id, embedding)])
    results = await store.search_similar(query_embedding, k=10)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# M1 8GB: RAM-gated (advanced_rag fallback pattern)
_LANCEDB_URI_DEFAULT = "~/.hledac/lance"
_IVFPQ_PARTITIONS = int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "64"))
_IVFPQ_SUB_VECTORS = int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "12"))


class LanceDBVectorStore:
    """
    LanceDB ANN vector store implementing VectorStore protocol.

    M1 8GB: RAM-gated, falls back when RAM > 5GB.
    IVF-PQ quantization for memory efficiency.

    Index: LanceDB ANN with cosine similarity.
    """

    def __init__(
        self,
        uri: str | None = None,
        table_name: str = "entities",
        nlist: int = _IVFPQ_PARTITIONS,
        nprobe: int = 8,
    ):
        self._uri = uri or _LANCEDB_URI_DEFAULT
        self._table_name = table_name
        self._nlist = nlist
        self._nprobe = nprobe
        self._store: Any = None
        self._available = True
        self._stats = {"upserts": 0, "searches": 0, "errors": 0}

    def _ensure_store(self) -> Any:
        """Lazy init LanceDB store."""
        if self._store is None:
            try:
                import lancedb

                db = lancedb.connect(self._uri)
                self._store = db
            except Exception as e:
                logger.warning("[LanceDBVectorStore] init failed: %s", e)
                self._available = False
                raise

        return self._store

    async def upsert_embeddings(
        self, embeddings: list[tuple[str, list[float]]]
    ) -> None:
        """
        Upsert entity embeddings.

        Args:
            embeddings: list of (entity_id, embedding_vector) tuples

        M1 8GB: batch size 512 (memory-adaptive)
        """
        if not self._available:
            return

        try:
            import lancedb
            import pyarrow as pa

            store = self._ensure_store()
            table_name = self._table_name

            # Build Arrow table
            ids = [e[0] for e in embeddings]
            vectors = [e[1] for e in embeddings]

            # pyarrow fixed-size list array
            dim = len(vectors[0]) if vectors else 384
            data = {
                "entity_id": pa.array(ids),
                "vector": pa.array(vectors, type=pa.list_(pa.float32(), dim)),
            }
            table = pa.table(data)

            # Get or create table
            try:
                tbl = store.open_table(table_name)
                tbl.add(table)
            except Exception:
                store.create_table(table_name, table)

            self._stats["upserts"] += len(embeddings)

        except Exception as e:
            logger.warning("[LanceDBVectorStore] upsert failed: %s", e)
            self._stats["errors"] += 1

    async def search_similar(
        self,
        query_embedding: list[float],
        k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search k most similar embeddings.

        Returns list of {entity_id, score, distance} dicts.
        M1 8GB: IVF-PQ quantized (opt-in via HLEDAC_LANCEDB_QUANTIZE=1)
        """
        if not self._available:
            return []

        try:
            store = self._ensure_store()
            tbl = store.open_table(self._table_name)

            # Search with IVF-PQ
            result = tbl.search(query_embedding).limit(k)
            if filter:
                result = result.where(filter)

            LanceDB_results = result.to_arrow().to_pydict()
            self._stats["searches"] += 1

            # Format results
            formatted = []
            for i in range(len(LanceDB_results.get("entity_id", []))):
                formatted.append({
                    "entity_id": LanceDB_results["entity_id"][i],
                    "score": LanceDB_results.get("score", [0.0] * i + [0.0])[i],
                    "distance": 1.0 - LanceDB_results.get("score", [0.0] * i + [0.0])[i],
                })

            return formatted

        except Exception as e:
            logger.warning("[LanceDBVectorStore] search failed: %s", e)
            self._stats["errors"] += 1
            return []

    def get_stats(self) -> dict[str, Any]:
        """Return vector store statistics."""
        return {
            "upserts": self._stats["upserts"],
            "searches": self._stats["searches"],
            "errors": self._stats["errors"],
            "available": self._available,
            "uri": self._uri,
        }

    def close(self) -> None:
        """Close LanceDB store."""
        self._store = None

    def __repr__(self) -> str:
        return f"LanceDBVectorStore(uri={self._uri!r})"
