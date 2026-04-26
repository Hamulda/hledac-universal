"""
VectorStore - LanceDB-backed vector storage for semantic search.

ROLE: Primary vector storage for embedding pipeline.
Two separate indices: text (256d MRL) and image (1024d).

Features:
- Separate LanceDB indices for text and image embeddings
- Lazy initialization on first add_vectors call
- Singleton pattern via get_vector_store()
- Cosine similarity search
- P13: Text dimension updated to 256d (MRL - Matryoshka Representation Learning)

Data contracts:
- add_vectors: (list[str], np.ndarray shape (N, dim), str) → None
- query: (np.ndarray shape (1, dim), int, str) → list[tuple[str, float]]
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# LanceDB directory
_LANCEDB_ROOT = Path.home() / ".hledac" / "lancedb"
_TEXT_INDEX_PATH = _LANCEDB_ROOT / "text_index.lance"
_IMAGE_INDEX_PATH = _LANCEDB_ROOT / "image_index.lance"

# Dimensions per index type
_TEXT_DIM = 256  # MRL dimension for ModernBERT
_IMAGE_DIM = 1024

# Valid index types
_VALID_INDEX_TYPES = {"text", "image"}


class VectorStore:
    """
    LanceDB-backed vector store with separate text and image indices.

    Singleton: use get_vector_store() to get the instance.
    Lazy initialization: indices created on first add_vectors call.
    """

    def __init__(self):
        self._db = None
        self._text_table = None
        self._image_table = None
        self._initialized = False

    def _ensure_directory(self) -> None:
        """Ensure LanceDB directory exists."""
        _LANCEDB_ROOT.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """Initialize LanceDB connection and tables (lazy)."""
        if self._initialized:
            return

        self._ensure_directory()

        try:
            import lancedb
            import pyarrow as pa

            # Connect to LanceDB
            self._db = lancedb.connect(str(_LANCEDB_ROOT))

            # Text index schema (256d MRL)
            text_schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), _TEXT_DIM)),
            ])

            # Image index schema (1024d)
            image_schema = pa.schema([
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), _IMAGE_DIM)),
            ])

            # Create or open text table
            try:
                self._text_table = self._db.open_table("text_index")
                logger.debug("[VECTOR] Opened existing text_index table")
            except Exception:
                self._text_table = self._db.create_table(
                    "text_index",
                    schema=text_schema,
                    exist_ok=True
                )
                logger.info(f"[VECTOR] Created text_index at {_TEXT_INDEX_PATH}")

            # Create or open image table
            try:
                self._image_table = self._db.open_table("image_index")
                logger.debug("[VECTOR] Opened existing image_index table")
            except Exception:
                self._image_table = self._db.create_table(
                    "image_index",
                    schema=image_schema,
                    exist_ok=True
                )
                logger.info(f"[VECTOR] Created image_index at {_IMAGE_INDEX_PATH}")

            self._initialized = True
            logger.info("[VECTOR] LanceDB initialized successfully")

        except ImportError as e:
            logger.error(f"[VECTOR] LanceDB not available: {e}")
            raise RuntimeError("LanceDB is required for vector store. Install with: pip install 'lancedb>=0.2.5'")
        except Exception as e:
            logger.error(f"[VECTOR] Failed to initialize LanceDB: {e}")
            raise

    def add_vectors(
        self,
        ids: list[str],
        vectors: np.ndarray,
        index_type: str = "text"
    ) -> None:
        """
        Add vectors to the specified index.

        Args:
            ids: List of string IDs corresponding to vectors.
            vectors: numpy ndarray of shape (N, dim) where dim is 768 for text, 1024 for image.
            index_type: Either "text" or "image".

        Raises:
            ValueError: If index_type is invalid or dimensions don't match.
        """
        if index_type not in _VALID_INDEX_TYPES:
            raise ValueError(f"Invalid index_type: {index_type}. Must be 'text' or 'image'.")

        if len(ids) != len(vectors):
            raise ValueError(f"Length mismatch: {len(ids)} IDs vs {len(vectors)} vectors")

        # Lazy initialization
        self._init_db()

        # Validate dimensions
        expected_dim = _TEXT_DIM if index_type == "text" else _IMAGE_DIM
        if vectors.shape[1] != expected_dim:
            raise ValueError(
                f"Dimension mismatch for {index_type}: expected {expected_dim}, got {vectors.shape[1]}"
            )

        # Select table
        table = self._text_table if index_type == "text" else self._image_table

        try:
            import pyarrow as pa

            # Convert numpy arrays to PyArrow format
            data = []
            for i, (row_id, vector) in enumerate(zip(ids, vectors)):
                # Ensure float32
                vec_float32 = vector.astype(np.float32)
                data.append({
                    "id": str(row_id),
                    "vector": vec_float32.tolist(),
                })

            # Add to table
            table.add(data)
            logger.debug(f"[VECTOR] Added {len(ids)} vectors to {index_type}_index")

        except Exception as e:
            logger.error(f"[VECTOR] Failed to add vectors: {e}")
            raise

    # F203I: Streaming batch add
    async def add_vectors_streaming(
        self,
        ids: list[str],
        vectors: np.ndarray,
        index_type: str = "text",
        batch_size: int = 16,
    ) -> None:
        """
        F203I: Streaming batch add — yields control between chunks.

        Breaks large vector inserts into smaller batches, yielding to the event
        loop between chunks to reduce M1 8GB peak RSS during embedding phases.

        Args:
            ids: List of string IDs.
            vectors: numpy ndarray shape (N, dim).
            index_type: "text" or "image".
            batch_size: Max chunk size (capped at 16 for M1 safety).

        Fail-open: any error is logged but does not raise.
        """
        total = len(ids)
        for i in range(0, total, batch_size):
            chunk_ids = ids[i:i + batch_size]
            chunk_vecs = vectors[i:i + batch_size]
            try:
                self.add_vectors(chunk_ids, chunk_vecs, index_type)
            except Exception as e:
                logger.warning(f"[VECTOR] add_vectors_streaming chunk error at {i}: {e}")
            await asyncio.sleep(0)  # yield to event loop

    def query(
        self,
        vector: np.ndarray,
        k: int,
        index_type: str = "text"
    ) -> list[tuple[str, float]]:
        """
        Query the vector index for similar vectors.

        Args:
            vector: Query vector of shape (1, dim) or (dim,).
            k: Number of results to return.
            index_type: Either "text" or "image".

        Returns:
            List of tuples (id, similarity_score), sorted by similarity descending.

        Raises:
            ValueError: If index_type is invalid.
        """
        if index_type not in _VALID_INDEX_TYPES:
            raise ValueError(f"Invalid index_type: {index_type}. Must be 'text' or 'image'.")

        # Lazy initialization
        self._init_db()

        # Normalize vector shape
        if vector.ndim == 2:
            vector = vector.squeeze(0)
        vector = vector.astype(np.float32)

        # Validate dimensions
        expected_dim = _TEXT_DIM if index_type == "text" else _IMAGE_DIM
        if len(vector) != expected_dim:
            logger.warning(
                f"[VECTOR] Query dimension mismatch: expected {expected_dim}, got {len(vector)}. "
                f"Skipping query."
            )
            return []

        # Select table
        table = self._text_table if index_type == "text" else self._image_table

        try:
            # Search using LanceDB's vector search
            # LanceDB returns results with _distance column (cosine distance)
            results = (
                table.search(vector.tolist(), vector_column_name="vector")
                .limit(k)
                .to_pandas()
            )

            # Convert distance to similarity (1 - distance for cosine)
            output = []
            for _, row in results.iterrows():
                doc_id = str(row["id"])
                distance = row.get("_distance", 1.0)
                # Convert cosine distance to similarity
                similarity = 1.0 - distance
                output.append((doc_id, float(similarity)))

            logger.debug(f"[VECTOR] Query returned {len(output)} results from {index_type}_index")
            return output

        except Exception as e:
            logger.error(f"[VECTOR] Query failed: {e}")
            return []

    def close(self) -> None:
        """Close database connection."""
        if self._db is not None:
            try:
                self._db.close()
                logger.info("[VECTOR] LanceDB connection closed")
            except Exception:
                pass
            self._db = None
            self._text_table = None
            self._image_table = None
            self._initialized = False


# Singleton instance
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    """
    Get the singleton VectorStore instance.

    Returns:
        VectorStore singleton.
    """
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
