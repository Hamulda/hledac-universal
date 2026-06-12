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
import os
import threading
from pathlib import Path

import numpy as np
from typing import Any, Callable

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
        # STORAGE-FIX-2: compaction scheduler state (bounded)
        "_insert_count_since_compact",
        "_last_compact_ts",
        "_compact_in_flight",
        # Sprint F264D: IVF-PQ vector quantization (opt-in)
        "_ivfpq_enabled",
        "_ivfpq_num_partitions",
        "_ivfpq_num_sub_vectors",
        "_ivfpq_trained",
        # Sprint F264E: adaptive auto-tuner
        "_autotune",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: object | None = None  # lancedb.LanceDBConnection
        self._table: object | None = None  # lancedb.Table
        self._embed_dim: int = _EMBEDDING_DIM
        self._boot_error: str | None = None
        self._initialized: bool = False
        self._lock = threading.Lock()
        # STORAGE-FIX-2: compaction scheduler
        self._insert_count_since_compact: int = 0
        self._last_compact_ts: float = 0.0
        self._compact_in_flight: bool = False
        # Sprint F264D: IVF-PQ vector quantization (opt-in, M1 8GB friendly).
        # Lazy: index trained on first search, requires >= 256 rows. Fail-soft.
        self._ivfpq_enabled: bool = (
            os.environ.get("HLEDAC_LANCEDB_QUANTIZE", "0") == "1"
        )
        self._ivfpq_num_partitions: int = max(
            8, min(256, int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "64")))
        )
        self._ivfpq_num_sub_vectors: int = max(
            4, min(64, int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "16")))
        )
        self._ivfpq_trained: bool = False
        # Sprint F264E: adaptive auto-tuner (opt-in, M1 8GB friendly). Single
        # source of truth shared with LanceDBIdentityStore. State persisted as
        # JSON in db_path for cross-session continuity.
        try:
            from knowledge.lancedb_auto_tuner import make_default_tuner
            self._autotune = make_default_tuner(
                table_name="semantic_dedup_v1",
                state_dir=db_path,
                num_sub_vectors=self._ivfpq_num_sub_vectors,
                vector_column="vector",
                key_column="finding_key",
            )
        except Exception:
            # Fail-soft — tuner is optional, never blocks __init__.
            self._autotune = None
        # SAFETY: SAFE_SYNC_BOUNDARY — _lock guards LanceDB table.search() and table.add()
        # operations in ann_search() and upsert(). Both are called from the embedding_pipeline
        # sync context (not async). The lock prevents concurrent LanceDB operations across threads
        # in the ThreadPoolExecutor. No await occurs inside this lock.

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
            logger.info("[ANN] ANN index initialized successfully")
            # Sprint F264D: lancedb.table_opened event with size_mb
            self._log_table_opened()
            return True

        except Exception as e:
            self._boot_error = str(e)
            self._initialized = True
            logger.warning(f"[ANN] ANN init failed: {e}")
            return False

    def _log_table_opened(self) -> None:
        """Sprint F264D: Log 'lancedb.table_opened' event with size_mb.

        M1 observability — measures table footprint for IVF-PQ benefit verification.
        Estimated: rows × embedding_dim × 4 bytes (float32) + PyArrow overhead.
        """
        try:
            if self._table is None:
                return
            row_count = self._table.count_rows()
            size_bytes = row_count * self._embed_dim * 4 + 8192
            size_mb = size_bytes / (1024 * 1024)
            logger.info(
                f"[ANN] lancedb.table_opened table=semantic_dedup_v1 "
                f"rows={row_count} size_mb={size_mb:.2f} path={self._db_path}"
            )
        except Exception as e:
            logger.debug(f"[ANN] lancedb.table_opened log failed: {e}")

    def _ensure_ivf_pq_index(self) -> None:
        """Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft, sync).

        Called from ann_search on first invocation. Gated by
        HLEDAC_LANCEDB_QUANTIZE=1. Skipped if table has < 256 rows. Double-checked
        under self._lock prevents concurrent training. Errors are logged + ignored
        → falls back to brute-force cosine.

        NOTE: Uses ``getattr`` for flags so the helper is safe under ``__new__``
        test-mock paths that bypass ``__init__``.
        """
        if not getattr(self, "_ivfpq_enabled", False):
            return
        if self._table is None or getattr(self, "_ivfpq_trained", False):
            return
        with self._lock:
            if self._ivfpq_trained:  # double-checked
                return
            try:
                row_count = self._table.count_rows()
                if row_count < 256:
                    logger.debug(
                        f"[ANN] IVF-PQ skipped: only {row_count} rows "
                        f"(need >= 256 for meaningful PQ training)"
                    )
                    self._ivfpq_trained = True  # mark as attempted
                    return
                # LanceDB Python API: tbl.create_index(metric, index_type, num_partitions, num_sub_vectors)
                self._table.create_index(
                    metric="cosine",
                    index_type="IVF_PQ",
                    num_partitions=getattr(self, "_ivfpq_num_partitions", 64),
                    num_sub_vectors=getattr(self, "_ivfpq_num_sub_vectors", 16),
                )
                self._ivfpq_trained = True
                logger.info(
                    f"[ANN] IVF-PQ trained: table=semantic_dedup_v1 rows={row_count} "
                    f"num_partitions={getattr(self, '_ivfpq_num_partitions', 64)} "
                    f"num_sub_vectors={getattr(self, '_ivfpq_num_sub_vectors', 16)}"
                )
            except Exception as e:
                self._ivfpq_trained = True  # don't retry on every call
                logger.warning(
                    f"[ANN] IVF-PQ training failed (fallback brute-force): {e}"
                )

    def ann_search(
        self,
        embedding: np.ndarray,
        top_k: int = 5,
        graph_filter: Callable[[list[str]], list[str]] | None = None,
    ) -> list[dict]:
        """
        ANN cosine search — returns list of dicts with finding_key, text_hash, score.

        P2-3 Enhancement — Graph-aware filtering:
          When ``graph_filter`` is provided, ANN candidates are expanded through
          the knowledge graph before re-scoring. Flow:

          1. ANN top-(top_k × 2) for broader candidate pool
          2. Extract candidate ``finding_key`` list
          3. ``graph_filter(candidate_keys)`` → expanded/filtered key list
          4. Fetch vectors for expanded keys + query
          5. Re-score with exact cosine, return top-K

        This enables graph-grounded deduplication: e.g. "find duplicates of
        entity E, but only among entities that share a DuckPGQ relation with E"
        (domain, IP range, AS ownership, etc.).

        M1 8GB safe: (top_k × 2) ANN fetch + O(20) re-scores = negligible.

        Args:
            embedding: 256d float32 query vector.
            top_k: Number of results to return.
            graph_filter: Optional callable(candidate_keys: list[str]) -> list[str].
                Return keys to include in final scoring. None = pure ANN (backward compat).

        Returns [] if not initialized or on any error (fail-open).
        Thread-safe via lock.
        """
        if self._boot_error is not None:
            return []
        if self._table is None:
            return []

        # Sprint F264D: lazy IVF-PQ training (after first search, off event loop)
        if self._ivfpq_enabled:
            self._ensure_ivf_pq_index()

        try:
            # Normalize embedding
            emb = embedding.astype(np.float32)
            if emb.ndim == 1:
                emb = emb.reshape(1, -1)
            norm = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8
            emb_norm = (emb / norm).squeeze(0).tolist()

            fetch_limit = top_k * 2 if graph_filter is not None else top_k

            with self._lock:
                results = (
                    self._table.search(emb_norm, vector_column_name="vector")
                    .metric("cosine")
                    .limit(fetch_limit)
                    .to_list()
                )

            # P2-3: Build candidate map {finding_key: (vector, text_hash, raw_distance)}
            candidates: dict[str, tuple[list[float], str, float]] = {}
            for r in results:
                fk = r.get("finding_key", "") or r.get("id", "")
                if not fk:
                    continue
                vec = r.get("vector", [])
                th = r.get("text_hash", "")
                dist = r.get("_distance", 1.0)
                if vec:
                    candidates[fk] = (vec, th, dist)

            if not candidates:
                return []

            # P2-3: Graph-filter expansion
            if graph_filter is not None and candidates:
                try:
                    candidate_keys = list(candidates.keys())
                    filtered_keys = graph_filter(candidate_keys)
                    # Keep only filtered keys (preserves order from graph_filter)
                    candidates = {k: v for k, v in candidates.items() if k in filtered_keys}
                except Exception as e:
                    # Fail-soft: graph filter error → fall back to pure ANN candidates
                    logger.debug(f"[ANN] graph_filter failed: {e}")

            if not candidates:
                return []

            # Re-score with exact cosine on expanded candidates
            q_vec = np.array(emb_norm, dtype=np.float32)
            scored: list[tuple[str, str, float]] = []
            for fk, (vec, th, _dist) in candidates.items():
                v = np.array(vec, dtype=np.float32)
                v_norm = v / (np.linalg.norm(v) + 1e-8)
                score = float(np.dot(q_vec, v_norm))
                score = max(0.0, min(1.0, score))
                if score >= _MIN_SCORE:
                    scored.append((fk, th, score))

            scored.sort(key=lambda x: x[2], reverse=True)
            output = [
                {"finding_key": fk, "text_hash": th, "score": sc}
                for fk, th, sc in scored[:top_k]
            ]
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

            # STORAGE-FIX-2: schedule compaction on fragment growth
            self._insert_count_since_compact += 1
            self._maybe_compact_blocking()

            # Sprint F264E: adaptive auto-tune (sync, under lock for thread safety).
            # P1-2 Enhancement: Now tunes BOTH num_partitions AND num_sub_vectors.
            if self._ivfpq_enabled and self._autotune is not None:
                try:
                    result = self._autotune.tune_if_due(
                        self._table,  # type: ignore[arg-type]
                        current_num_partitions=self._ivfpq_num_partitions,
                        current_num_sub_vectors=self._ivfpq_num_sub_vectors,
                        inserts_delta=1,
                    )
                    if result.changed():
                        self._ivfpq_num_partitions = result.new_partitions
                        self._ivfpq_num_sub_vectors = result.new_num_sub_vectors
                        logger.info(
                            f"[ANN] auto-tune adjusted "
                            f"num_partitions={result.old_partitions}->{result.new_partitions} "
                            f"num_sub_vectors={result.old_num_sub_vectors}->{result.new_num_sub_vectors} "
                            f"recall={result.recall:.3f} avg_ms={result.avg_search_ms:.2f}"
                        )
                except Exception:
                    # Fail-soft: any tuner error must not break upsert.
                    pass

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

                    oldest_ts = self._table.to_arrow().sort_by([("added_at", "asc")]).slice(0, to_delete)
                    # Use delete WHERE using LanceDB's filter API
                    keys_to_delete = oldest_ts["finding_key"].to_pylist()
                    for key in keys_to_delete:
                        self._table.delete(f"finding_key = '{key}'")
        except Exception as e:
            logger.debug(f"[ANN] evict failed: {e}")

    def _maybe_compact_blocking(self) -> None:
        """STORAGE-FIX-2: LanceDB compaction trigger (sync, fail-soft).

        Bound semantics:
          - Trigger: _insert_count_since_compact >= 1000 OR time >= 1h
          - Min interval: 60s
          - Fail-soft: any exception logged + ignored
        """
        if self._compact_in_flight:
            return
        if self._table is None:
            return
        import time as _t
        now = _t.time()
        count_due = self._insert_count_since_compact >= 1000
        time_due = (now - self._last_compact_ts) >= 3600.0
        if not (count_due or time_due):
            return
        if (now - self._last_compact_ts) < 60.0:
            return
        self._compact_in_flight = True
        try:
            if hasattr(self._table, "optimize"):
                self._table.optimize()
            elif hasattr(self._table, "compact_files"):
                self._table.compact_files()
            else:
                return
            self._insert_count_since_compact = 0
            self._last_compact_ts = _t.time()
            logger.debug("[ANN] compact ok (reset, ts=%d)", int(self._last_compact_ts))
        except Exception as e:
            logger.debug(f"[ANN] compact failed (fail-soft): {e}")
        finally:
            self._compact_in_flight = False

    def _get_oldest_timestamp(self) -> float | None:
        """Get timestamp of oldest entry."""
        try:

            oldest = self._table.to_arrow().sort_by([("added_at", "asc")]).slice(0, 1)
            if oldest.num_rows > 0:
                return oldest["added_at"][0].as_py()
            return None
        except Exception:
            return None

    def prewarm(self, top_k: int = 128) -> None:
        """
        F203I: Pre-warm the ANN index for faster first-query latency.

        Ensures index is initialized and pre-loads data via a dummy search.
        Reduces cold-start latency for RAG/dedup queries after embedding phase.

        Fail-soft: returns None on any error.

        Args:
            top_k: Number of entries to warm (default 128).
        """
        if self._boot_error is not None:
            return None

        if not self._check_memory_guard():
            return None

        if not self.init():
            return None

        try:
            import numpy as np

            # Dummy search to warm up the index
            dummy = np.zeros(self._embed_dim, dtype=np.float32)
            self.ann_search(dummy, top_k=min(top_k, 5))
        except Exception as e:
            logger.debug(f"[ANN] prewarm failed: {e}")
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

_ann_index: _ANNIndex | None = None
_ann_index_lock = threading.Lock()
# SAFETY: SAFE_SYNC_BOUNDARY — _ann_index_lock guards module-level singleton _ann_index.
# Double-checked locking pattern: fast path without lock when _ann_index is already set.
# No async callers.


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
    graph_filter: Callable[[list[str]], list[str]] | None = None,
) -> bool:
    """
    Check if an embedding matches any existing entry in ANN index.

    P2-3 Enhancement — Graph-aware filtering:
      When ``graph_filter`` is provided, ANN candidates are first expanded
      through the knowledge graph (via ``graph_filter`` callable) before
      exact cosine re-scoring. See ``_ANNIndex.ann_search`` for details.

    Flow:
    1. ANN search for top-(5×2) similar vectors (or graph-filtered pool)
    2. Graph expansion/filtering if graph_filter provided
    3. Exact cosine re-score on expanded candidates
    4. If score >= 0.90 → duplicate detected
    5. If no match → upsert current embedding (async-safe, best-effort)

    Args:
        embedding: 256d float32 numpy array
        text_hash: SHA256 of original text (for verification)
        finding_key: BLAKE2b key for this finding
        graph_filter: Optional callable(candidate_keys: list[str]) -> list[str].
            Expands/filters ANN candidates via graph relations.
            None = pure ANN (backward compatible).

    Returns:
        True if duplicate detected, False otherwise.
        Always returns False on any error (fail-open).
    """
    try:
        ann = get_ann_index()
        # SAFETY: SAFE_SYNC_BOUNDARY — _ann_index_lock guards the module-level singleton _ann_index.
        # get_ann_index() is called from embedding_pipeline sync context.
        # Double-checked locking pattern: fast path without lock when _ann_index is already set.
        if ann._boot_error is not None:
            return False

        results = ann.ann_search(embedding, top_k=5, graph_filter=graph_filter)
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
