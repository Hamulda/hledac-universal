"""
Sprint F200B — LanceDB ANN Fast Path for Semantic Dedup
========================================================

ROLE: Optional fast-path ANN index layered over SemanticDedupCache.
Does NOT replace LMDB persistence — adds cosine-similarity ANN search
for sub-10ms duplicate detection on cross-run data.

DIMENSION CONTRACT: 256d float32 (matches embedding_pipeline._EMBEDDING_DIM)

FAIL-OPEN: Any init/query error → returns duplicate=False, never raises.
ANN init failure stored in _ann_boot_error; all methods check this and
fall back to in-process LRU when ann is unavailable.

DATA FLOW:
  SemanticDedupCache.check_and_cache()
    → [existing LRU + LMDB path]
    → [NEW: ann.ann_search(emb) — fast path for cross-run persistence]
    → result

M1 MEMORY: ann_init() guarded by RSS < 6GB. Heavy LanceDB init skipped above threshold.
INDEX BOUND: MAX_ANN_ENTRIES=50_000 — bounded table, oldest entries evicted on overflow.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------
_EMBEDDING_DIM = 256  # Must match embedding_pipeline._EMBEDDING_DIM
_TABLE_NAME = "semantic_dedup_v1"
_MAX_ENTRIES = 50_000  # Bounded ANN index (M1 8GB safety)
_MIN_SCORE = 0.90  # Cosine similarity threshold (same as semantic dedup default)
_MEMORY_GUARD_GB = 6.0  # Skip ANN init above this RSS


# -----------------------------------------------------------------------
# LanceDB ANN wrapper
# -----------------------------------------------------------------------

class _ANNIndex:
    """
    LanceDB ANN index for semantic dedup fast path.

    Fail-soft: init errors stored in _boot_error, ann_search() returns []
    when unavailable. Safe to call from any thread.
    """

    __slots__ = (
        "_db_path",
        "_db",
        "_table",
        "_embed_dim",
        "_boot_error",
        "_initialized",
        "_lock",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: Optional[object] = None  # lancedb.LanceDBConnection
        self._table: Optional[object] = None  # lancedb.Table
        self._embed_dim: int = _EMBEDDING_DIM
        self._boot_error: Optional[str] = None
        self._initialized: bool = False
        self._lock = threading.Lock()

    def _check_memory_guard(self) -> bool:
        """Return True if ANN init is safe (RSS below threshold)."""
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
            return rss < _MEMORY_GUARD_GB * 1024**3
        except Exception:
            return True  # Fail-soft: allow init if check fails

    def init(self) -> bool:
        """
        Initialize LanceDB connection and table.

        Returns True on success, False on any error.
        Stores error string in _boot_error on failure.
        """
        if self._initialized:
            return self._boot_error is None

        if not self._check_memory_guard():
            self._boot_error = "memory pressure"
            return False

        try:
            import lancedb

            self._db_path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self._db_path))

            # Try to open existing table
            try:
                self._table = self._db.open_table(_TABLE_NAME)
                row_count = self._table.count_rows()
                logger.info(f"[ANN] Opened existing table with {row_count} rows")
            except Exception:
                # Create new table with schema
                import pyarrow as pa

                schema = pa.schema([
                    pa.field("finding_key", pa.string()),  # BLAKE2b key
                    pa.field("vector", pa.list_(pa.float32(), _EMBEDDING_DIM)),
                    pa.field("text_hash", pa.string()),  # SHA256 of original text
                    pa.field("added_at", pa.float64()),  # timestamp for LRU eviction
                ])
                self._table = self._db.create_table(_TABLE_NAME, schema=schema)
                logger.info(f"[ANN] Created new table at {self._db_path}")

            self._initialized = True
            self._boot_error = None
            logger.info(f"[ANN] ANN index initialized successfully")
            return True

        except Exception as e:
            self._boot_error = str(e)
            self._initialized = True
            logger.warning(f"[ANN] ANN init failed: {e}")
            return False

    def ann_search(self, embedding: np.ndarray, top_k: int = 5) -> list[dict]:
        """
        ANN cosine search — returns list of dicts with finding_key, text_hash, score.

        Returns [] if not initialized or on any error (fail-open).
        Thread-safe via lock.
        """
        if self._boot_error is not None:
            return []
        if self._table is None:
            return []

        try:
            # Normalize embedding
            emb = embedding.astype(np.float32)
            if emb.ndim == 1:
                emb = emb.reshape(1, -1)
            norm = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8
            emb_norm = (emb / norm).squeeze(0).tolist()

            with self._lock:
                results = (
                    self._table.search(emb_norm, vector_column_name="vector")
                    .metric("cosine")
                    .limit(top_k)
                    .to_list()
                )

            output = []
            for r in results:
                score = max(0.0, min(1.0, 1.0 - r.get("_distance", 1.0)))
                if score >= _MIN_SCORE:
                    output.append({
                        "finding_key": r.get("finding_key", ""),
                        "text_hash": r.get("text_hash", ""),
                        "score": score,
                    })
            return output

        except Exception as e:
            logger.debug(f"[ANN] ann_search failed: {e}")
            return []

    def upsert(self, finding_key: str, embedding: np.ndarray, text_hash: str) -> bool:
        """
        Upsert a single embedding into the ANN index.

        Returns True on success, False on error (fail-open).
        Thread-safe via lock.
        """
        if self._boot_error is not None:
            return False
        if self._table is None:
            return False

        try:
            import time

            emb = embedding.astype(np.float32)
            if emb.ndim == 2:
                emb = emb.squeeze(0)

            row = {
                "finding_key": finding_key,
                "vector": emb.tolist(),
                "text_hash": text_hash,
                "added_at": time.time(),
            }

            with self._lock:
                self._table.add([row])

            # Evict oldest if over cap
            self._maybe_evict()

            return True

        except Exception as e:
            logger.debug(f"[ANN] upsert failed: {e}")
            return False

    def _maybe_evict(self) -> None:
        """Evict oldest entries if table exceeds MAX_ENTRIES."""
        try:
            count = self._table.count_rows()
            if count > _MAX_ENTRIES:
                # Delete oldest 10%
                to_delete = int(count * 0.1)
                oldest_ts = self._get_oldest_timestamp()
                if oldest_ts is not None:
                    import pyarrow as pa

                    oldest_ts = self._table.to_arrow().sort_by([("added_at", "asc")]).slice(0, to_delete)
                    # Use delete WHERE using LanceDB's filter API
                    keys_to_delete = oldest_ts["finding_key"].to_pylist()
                    for key in keys_to_delete:
                        self._table.delete(f"finding_key = '{key}'")
        except Exception as e:
            logger.debug(f"[ANN] evict failed: {e}")

    def _get_oldest_timestamp(self) -> Optional[float]:
        """Get timestamp of oldest entry."""
        try:
            import pyarrow as pa

            oldest = self._table.to_arrow().sort_by([("added_at", "asc")]).slice(0, 1)
            if oldest.num_rows > 0:
                return oldest["added_at"][0].as_py()
            return None
        except Exception:
            return None

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._db is not None:
                try:
                    getattr(self._db, "close", lambda: None)()
                except Exception:
                    pass
            self._db = None
            self._table = None
            self._boot_error = None
            self._initialized = False


# -----------------------------------------------------------------------
# Public facade
# -----------------------------------------------------------------------

_ann_index: Optional[_ANNIndex] = None
_ann_index_lock = threading.Lock()


def get_ann_index(lmdb_path: str | None = None) -> _ANNIndex:
    """
    Get the singleton ANN index instance.

    Lazy-init on first call. Thread-safe.
    """
    global _ann_index
    if _ann_index is None:
        with _ann_index_lock:
            if _ann_index is None:
                from hledac.universal.paths import PATHS

                db_path = PATHS.hledac_home / "ann_index"
                _ann_index = _ANNIndex(db_path)
                _ann_index.init()
    return _ann_index


def check_ann_duplicate(
    embedding: np.ndarray,
    text_hash: str,
    finding_key: str,
) -> bool:
    """
    Check if an embedding matches any existing entry in ANN index.

    Flow:
    1. ANN search for top-5 similar vectors
    2. If score >= 0.90 → duplicate detected
    3. If no match → upsert current embedding (async-safe, best-effort)

    Args:
        embedding: 256d float32 numpy array
        text_hash: SHA256 of original text (for verification)
        finding_key: BLAKE2b key for this finding

    Returns:
        True if duplicate detected, False otherwise.
        Always returns False on any error (fail-open).
    """
    try:
        ann = get_ann_index()
        if ann._boot_error is not None:
            return False

        results = ann.ann_search(embedding, top_k=5)
        for r in results:
            # Verify text_hash matches (prevents hash collision false positives)
            if r.get("text_hash") == text_hash and r.get("score", 0) >= _MIN_SCORE:
                logger.debug(f"[ANN] Duplicate detected: key={finding_key[:16]}, score={r['score']:.3f}")
                return True

        # No match — upsert for future lookups
        ann.upsert(finding_key, embedding, text_hash)
        return False

    except Exception as e:
        logger.debug(f"[ANN] check_ann_duplicate failed: {e}")
        return False


def reset_ann_index() -> None:
    """Reset ANN index singleton (called on sprint teardown)."""
    global _ann_index
    with _ann_index_lock:
        if _ann_index is not None:
            try:
                _ann_index.close()
            except Exception:
                pass
        _ann_index = None