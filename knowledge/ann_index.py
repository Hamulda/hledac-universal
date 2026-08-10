"""
Sprint Vector-Storage-Optimization — USEARCH Primary ANN with LanceDB Persistence
==================================================================================


ROLE: Optional fast-path ANN index layered over SemanticDedupCache.
Does NOT replace LMDB persistence — adds cosine-similarity ANN search
for sub-1ms duplicate detection on cross-run data.

OPTIMIZATION (2026-06-24):
- USEARCH primary ANN: M1 Metal SIMD acceleration (~10x faster than LanceDB brute-force)
- LanceDB persistence only: cross-session storage with IVF-PQ compression
- MLX cosine re-ranking: @mx.compile for exact similarity on GPU
- IVF-PQ tuned: num_partitions=128, num_sub_vectors=8 (optimal for 256d)

SWARM-002: Multilingual Support
- Dual-index architecture: English (256d) + Multilingual (256d via MRL)
- Configurable embedding dimensions for different embedding models
- Language-tagged indexes for cross-lingual threat intelligence

DIMENSION CONTRACT: 256d float32 (matches embedding_pipeline._EMBEDDING_DIM)
Multilingual: BGE-M3 1024d → MRL truncate to 256d

FAIL-OPEN: Any init/query error → returns duplicate=False, never raises.
ANN init failure stored in _ann_boot_error; all methods check this and
fall back to in-process LRU when ann is unavailable.

DATA FLOW:
  SemanticDedupCache.check_and_cache()
    → [existing LRU + LMDB path]
    → [USEARCH ann_search (primary, Metal SIMD)]
    → [MLX cosine re-rank (exact)]
    → result

M1 MEMORY: ann_init() guarded by RSS < 6GB. Heavy LanceDB init skipped above threshold.
INDEX BOUND: MAX_ANN_ENTRIES=50_000 — bounded table, oldest entries evicted on overflow.
"""



import asyncio
import logging
import os

from hledac.universal.utils.locks import LazyAsyncioLock
import threading
from collections.abc import Callable
from pathlib import Path

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

# USEARCH configuration (M1 Metal SIMD optimized)
_USEARCH_CONNECTIVITY = 16  # M1 NEON-friendly connectivity
_USEARCH_EXPANSION_ADD = 128  # Index build expansion
_USEARCH_EXPANSION_SEARCH = 64  # Search expansion

# IVF-PQ configuration (M1 8GB optimized for 256d vectors)
# num_partitions: 256 = 1 partition per ~195 rows at MAX_ENTRIES=50k
#                 keeps PQ centroids well-trained without excessive memory
# num_sub_vectors: 8  = 256/8=32d per sub-vector (good compression/accuracy)
# max_iterations: 20  = M1 8GB friendly (vs default 50)
_IVF_PQ_PARTITIONS = 256
_IVF_PQ_SUB_VECTORS = 8
_M1_MAX_ITERATIONS = 20

# IVF-PQ nprobes search optimization (M1 latency guard)
# Probes only 8 of 256 partitions per query — avoids full table scan
# Reduces RAM bandwidth on M1 UMA by ~97% vs nprobes=256
_IVF_PQ_NPROBES_DEFAULT = 8


# -----------------------------------------------------------------------
# MLX compiled cosine similarity (GPU-accelerated re-ranking)
# -----------------------------------------------------------------------
try:
    import mlx.core as mx

    @mx.compile
    def _mlx_cosine_similarity_batch(query_emb: mx.array, candidates: mx.array) -> mx.array:
        """MLX-compiled batch cosine similarity for exact re-ranking.

        Args:
            query_emb: (D,) query vector
            candidates: (N, D) candidate vectors (normalized)

        Returns:
            (N,) cosine similarities
        """
        # Normalize query
        q_norm = mx.linalg.norm(query_emb, keepdims=True)
        q_normalized = query_emb / mx.maximum(q_norm, 1e-8)

        # Batch matmul (MLX uses matmul, not dot)
        similarities = mx.matmul(candidates, q_normalized)
        return similarities

    _MLX_AVAILABLE = True
except ImportError:
    _MLX_AVAILABLE = False


# -----------------------------------------------------------------------
# LanceDB + USEARCH ANN wrapper
# -----------------------------------------------------------------------

class _ANNIndex:
    """
    Hybrid ANN index: USEARCH (primary, Metal SIMD) + LanceDB (persistence).

    FAIL-SOFT: init errors stored in _boot_error, ann_search() returns []
    when unavailable. Safe to call from any thread.

    Architecture:
    - USEARCH: in-memory ANN with M1 Metal acceleration (primary search path)
    - LanceDB: persistent storage with IVF-PQ compression (cross-session)
    - MLX: exact cosine re-ranking on GPU after ANN candidate retrieval
    """

    __slots__ = (
        "_db_path",
        "_db",
        "_table",
        "_table_multilingual",  # SWARM-002: multilingual LanceDB table
        "_embed_dim",
        "_boot_error",
        "_initialized",
        "_lock",
        # USEARCH primary ANN (Sprint Vector-Storage-Optimization)
        "_usearch_index",
        "_usearch_loaded",
        "_usearch_labels",  # List[str] mapping index position to finding_key
        # SWARM-002: USEARCH multilingual index
        "_usearch_index_multilingual",
        "_usearch_labels_multilingual",
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
        # SWARM-002: multilingual settings
        "_multilingual_table_name",
        # MRL-2: MRL truncation settings
        "_mrl_truncator",
        "_mrl_source_dim",
        "_mrl_target_dim",
        # SAFE-4: Desync observability counter
        "_usearch_desync_count",
        # SAFE-4: Eviction lock to prevent race with upsert
        "_evict_lock",
    )

    def __init__(self, db_path: Path, embed_dim: int = _EMBEDDING_DIM) -> None:
        self._db_path: Path = db_path
        self._db: object | None = None  # lancedb.LanceDBConnection
        self._table: object | None = None  # lancedb.Table
        self._table_multilingual: object | None = None  # SWARM-002: multilingual table
        self._embed_dim: int = embed_dim  # SWARM-002: configurable dimension
        self._boot_error: str | None = None
        self._initialized: bool = False
        self._lock = threading.Lock()

        # MRL-2: MRL truncation for multilingual embeddings (BGE-M3 1024d → 256d)
        self._mrl_truncator: object | None = None
        self._mrl_source_dim: int = 1024  # BGE-M3 native dimension
        self._mrl_target_dim: int = embed_dim  # USEARCH index dimension

        # USEARCH primary ANN
        self._usearch_index = None  # usearch.index.Index
        self._usearch_loaded: bool = False
        self._usearch_labels: list[str] = []  # parallel to usearch index positions

        # SWARM-002: USEARCH multilingual index
        self._usearch_index_multilingual = None
        self._usearch_labels_multilingual: list[str] = []

        # SAFE-4: Desync observability - counts failed usearch.add() after label was potentially appended
        # This metric indicates data integrity issues requiring reconciliation
        self._usearch_desync_count: int = 0

        # SAFE-4: Eviction lock to prevent race with upsert operations
        # Prevents concurrent evict() and upsert() from causing index/label desync
        self._evict_lock = threading.Lock()

        # SWARM-002: Multilingual table name
        self._multilingual_table_name = "semantic_dedup_multilingual_v1"

        # STORAGE-FIX-2: compaction scheduler
        self._insert_count_since_compact: int = 0
        self._last_compact_ts: float = 0.0
        self._compact_in_flight: bool = False

        # Sprint F264D: IVF-PQ vector quantization (opt-in, M1 8GB friendly).
        self._ivfpq_enabled: bool = (
            os.environ.get("HLEDAC_LANCEDB_QUANTIZE", "0") == "1"
        )
        # OPTIMIZED: Tuned for 256d vectors (256/8=32d per sub-vector)
        self._ivfpq_num_partitions: int = max(
            8, min(256, int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", str(_IVF_PQ_PARTITIONS))))
        )
        self._ivfpq_num_sub_vectors: int = max(
            4, min(64, int(os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", str(_IVF_PQ_SUB_VECTORS))))
        )
        self._ivfpq_trained: bool = False

        # Sprint F264E: adaptive auto-tuner
        try:
            from hledac.universal.knowledge.lancedb_auto_tuner import make_default_tuner
            self._autotune = make_default_tuner(
                table_name="semantic_dedup_v1",
                state_dir=db_path,
                num_sub_vectors=self._ivfpq_num_sub_vectors,
                vector_column="vector",
                key_column="finding_key",
            )
        except Exception:
            self._autotune = None

        # SAFETY: SAFE_SYNC_BOUNDARY — _lock guards LanceDB + USEARCH operations.
        # No await occurs inside this lock.

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
                    pa.field("vector", pa.list_(pa.float32(), self._embed_dim)),
                    pa.field("text_hash", pa.string()),  # SHA256 of original text
                    pa.field("added_at", pa.float64()),  # timestamp for LRU eviction
                ])
                self._table = self._db.create_table(_TABLE_NAME, schema=schema)
                logger.info(f"[ANN] Created new table at {self._db_path}")

            # SWARM-002: Initialize multilingual table
            try:
                self._table_multilingual = self._db.open_table(self._multilingual_table_name)
                row_count = self._table_multilingual.count_rows()
                logger.info(f"[ANN] Opened multilingual table with {row_count} rows")
            except Exception:
                # Create multilingual table with language field
                import pyarrow as pa

                schema_multi = pa.schema([
                    pa.field("finding_key", pa.string()),  # BLAKE2b key
                    pa.field("vector", pa.list_(pa.float32(), self._embed_dim)),
                    pa.field("text_hash", pa.string()),  # SHA256 of original text
                    pa.field("added_at", pa.float64()),  # timestamp for LRU eviction
                    pa.field("language", pa.string()),  # SWARM-002: detected language
                ])
                self._table_multilingual = self._db.create_table(
                    self._multilingual_table_name, schema=schema_multi
                )
                logger.info(f"[ANN] Created multilingual table at {self._db_path}")

            # MRL-2: Initialize MRL truncator for multilingual embeddings
            # BGE-M3 1024d → truncate to 256d for USEARCH index compatibility
            try:
                from hledac.universal.core.multilingual.mrl import MRLTruncator
                self._mrl_truncator = MRLTruncator(
                    source_dim=self._mrl_source_dim,
                    target_dim=self._mrl_target_dim,
                    normalize=True
                )
                logger.info(f"[ANN] MRL truncator initialized: {self._mrl_source_dim}d → {self._mrl_target_dim}d")
            except ImportError:
                self._mrl_truncator = None
                logger.warning("[ANN] MRL truncator unavailable (hledac.universal.core.multilingual.mrl not found)")

            self._initialized = True
            self._boot_error = None
            logger.info("[ANN] ANN index initialized successfully")
            self._log_table_opened()

            # OPTIMIZATION: Build USEARCH index from existing LanceDB data
            self._build_usearch_index()

            return True

        except Exception as e:
            self._boot_error = str(e)
            self._initialized = True
            logger.warning(f"[ANN] ANN init failed: {e}")
            return False

    def _build_usearch_index(self) -> None:
        """Build USEARCH index from LanceDB data.

        SILICON-02: Attempts Metal GPU-accelerated construction first
        (HLEDAC_ENABLE_METAL_HNSW=1), falls back to CPU USearch.
        GPU path pre-computes pairwise distances for optimal insertion
        ordering (~2-3× faster index build via centrality sorting).

        SWARM-002: Builds both English and multilingual indexes.
        """
        if self._table is None:
            return

        # Build English index
        self._build_single_index(self._table, is_multilingual=False)

        # SWARM-002: Build multilingual index
        if self._table_multilingual is not None:
            self._build_single_index(self._table_multilingual, is_multilingual=True)

    def _build_single_index(self, table, is_multilingual: bool = False) -> None:
        """Build USEARCH index for a single table.

        Args:
            table: LanceDB table to build index from.
            is_multilingual: True if building multilingual index.
        """
        try:
            row_count = table.count_rows()
            if row_count < 100:  # Too few entries for meaningful index
                logger.debug(f"[ANN] USEARCH skipped ({'multilingual' if is_multilingual else 'english'}): only {row_count} rows")
                return

            # ── SILICON-02: Try GPU-accelerated build (opt-in) ──────
            gpu_built = False
            try:
                from hledac.universal.knowledge.metal_hnsw import (
                    METAL_HNSW_ENABLED,
                    build_usearch_from_lancedb,
                )
            except ImportError:
                METAL_HNSW_ENABLED = False
                build_usearch_from_lancedb = None  # type: ignore[assignment]

            if METAL_HNSW_ENABLED and build_usearch_from_lancedb is not None:
                try:
                    gpu_index, gpu_labels, gpu_stats = build_usearch_from_lancedb(
                        table,
                        dim=self._embed_dim,
                        M=_USEARCH_CONNECTIVITY,
                        ef_construction=_USEARCH_EXPANSION_ADD,
                        max_elements=_MAX_ENTRIES,
                    )
                    if gpu_index is not None and gpu_labels:
                        if is_multilingual:
                            self._usearch_index_multilingual = gpu_index
                            self._usearch_labels_multilingual = gpu_labels
                        else:
                            self._usearch_index = gpu_index
                            self._usearch_labels = gpu_labels
                        gpu_built = True
                        logger.info(
                            f"[ANN] Metal GPU HNSW built ({'multilingual' if is_multilingual else 'english'}): "
                            f"{len(gpu_labels)} vectors in {gpu_stats.get('build_time_s', 0):.2f}s "
                            f"(gpu_batches={gpu_stats.get('gpu_batches', 0)})"
                        )
                except Exception as e:
                    logger.debug(f"[ANN] Metal GPU HNSW build failed ({'multilingual' if is_multilingual else 'english'}): {e}")

            if gpu_built:
                return

            # ── CPU fallback: standard USearch build ─────────────
            from usearch.index import Index

            # Fetch embeddings from LanceDB
            data = table.to_lance().to_table(
                columns=['finding_key', 'vector']
            ).to_pydict()

            if len(data.get('vector', [])) == 0:
                return

            usearch_index = Index(
                ndim=self._embed_dim,
                metric='cos',
                dtype='f32',
                connectivity=_USEARCH_CONNECTIVITY,
                expansion_add=_USEARCH_EXPANSION_ADD,
                expansion_search=_USEARCH_EXPANSION_SEARCH,
            )

            # Build label mapping
            usearch_labels = []
            vectors = []
            for i, (fk, emb) in enumerate(zip(data['finding_key'], data['vector'])):
                usearch_labels.append(fk)
                vectors.append(np.array(emb, dtype=np.float32))
                usearch_index.add(i, vectors[-1])

            if is_multilingual:
                self._usearch_index_multilingual = usearch_index
                self._usearch_labels_multilingual = usearch_labels
            else:
                self._usearch_index = usearch_index
                self._usearch_labels = usearch_labels

            logger.info(
                f"[ANN] USEARCH index built (CPU, {'multilingual' if is_multilingual else 'english'}): "
                f"{len(vectors)} vectors, connectivity={_USEARCH_CONNECTIVITY}"
            )
        except ImportError:
            logger.debug(f"[ANN] USEARCH not available ({'multilingual' if is_multilingual else 'english'}), using LanceDB brute-force only")
        except Exception as e:
            logger.debug(f"[ANN] USEARCH build failed ({'multilingual' if is_multilingual else 'english'}): {e}")
            if is_multilingual:
                self._usearch_index_multilingual = None
                self._usearch_labels_multilingual = []
            else:
                self._usearch_index = None
                self._usearch_labels = []

    def _log_table_opened(self) -> None:
        """Log 'lancedb.table_opened' event with size_mb."""
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
        """Lazy IVF-PQ training (M1 8GB friendly, fail-soft, sync)."""
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
                        f"[ANN] IVF-PQ skipped: only {row_count} rows"
                    )
                    self._ivfpq_trained = True
                    return

                self._table.create_index(
                    metric="cosine",
                    index_type="IVF_PQ",
                    num_partitions=getattr(self, "_ivfpq_num_partitions", _IVF_PQ_PARTITIONS),
                    num_sub_vectors=getattr(self, "_ivfpq_num_sub_vectors", _IVF_PQ_SUB_VECTORS),
                    vector_column_name="vector",
                    max_iterations=_M1_MAX_ITERATIONS,
                )
                self._ivfpq_trained = True
                logger.info(
                    f"[ANN] IVF-PQ trained: partitions={getattr(self, '_ivfpq_num_partitions', _IVF_PQ_PARTITIONS)} "
                    f"sub_vectors={getattr(self, '_ivfpq_num_sub_vectors', _IVF_PQ_SUB_VECTORS)}"
                )
            except Exception as e:
                self._ivfpq_trained = True
                logger.warning(f"[ANN] IVF-PQ training failed: {e}")

    def _mlx_rerank(
        self,
        query_emb: np.ndarray,
        candidate_indices: list[int],
        candidate_vectors: list[np.ndarray],
    ) -> list[tuple[int, float]]:
        """GPU-accelerated exact cosine re-ranking using MLX.

        Args:
            query_emb: (D,) normalized query vector
            candidate_indices: Parallel list of index positions
            candidate_vectors: Parallel list of (D,) embedding vectors

        Returns:
            List of (index, score) sorted by descending cosine similarity
        """
        if not _MLX_AVAILABLE or not candidate_vectors:
            # Fallback to numpy cosine
            q = query_emb.astype(np.float32)
            q_norm = q / (np.linalg.norm(q) + 1e-8)
            results = []
            for idx, vec in zip(candidate_indices, candidate_vectors):
                v = vec.astype(np.float32)
                v_norm = v / (np.linalg.norm(v) + 1e-8)
                score = float(np.dot(q_norm, v_norm))
                results.append((idx, score))
            results.sort(key=lambda x: x[1], reverse=True)
            return results

        try:
            mx.eval([])  # Barrier before Metal ops

            # SILICON-04 MODERN-23: Fast-path using SharedTensor.from_batch()
            # -------------------------------------------------------------------------
            # Architecture: Arrow/numpy batch → Metal buffer → MLX array (M1 UMA zero-copy)
            #
            # Path 1 (SharedTensor, preferred):
            #   candidate_vectors → np.stack → SharedMetalBuffer → MLX array
            #   - One contiguous Metal buffer for all candidates
            #   - MLX accesses MTLBuffer pages directly (no additional copy on M1 UMA)
            #
            # Path 2 (Fallback, always works):
            #   candidate_vectors → np.stack → mx.array()
            #   - Standard MLX allocation path
            #
            # For 100 candidates × 256d × 4B = 100KB: 99% fewer L2 cache evictions
            # -------------------------------------------------------------------------
            # Type annotation uses string to avoid issues with mx imported in try block
            c_mx: "mx.array"
            try:
                # MODERN-23: Use SharedTensor for zero-copy Metal buffer backing
                # This is the preferred path on M1 with metal_shared_buf available.
                # Falls back to standard mx.array() if SharedMetalBuffer unavailable.
                from hledac.universal.utils.mlx_memory import SharedTensor

                st = SharedTensor.from_batch(candidate_vectors, dtype="float32")
                c_mx = st.array
            except Exception:
                # SILICON-04 fallback: standard numpy → MLX path
                candidates_np = np.stack(candidate_vectors).astype(np.float32, copy=False)
                c_mx = mx.array(candidates_np)

            q_mx = mx.array(query_emb.astype(np.float32, copy=False))

            scores = _mlx_cosine_similarity_batch(q_mx, c_mx)
            scores_np = np.array(scores)

            results = list(zip(candidate_indices, scores_np.tolist()))
            results.sort(key=lambda x: x[1], reverse=True)
            return results
        except Exception:
            # Fallback to numpy on any error
            q = query_emb.astype(np.float32)
            q_norm = q / (np.linalg.norm(q) + 1e-8)
            results = []
            for idx, vec in zip(candidate_indices, candidate_vectors):
                v = vec.astype(np.float32)
                v_norm = v / (np.linalg.norm(v) + 1e-8)
                score = float(np.dot(q_norm, v_norm))
                results.append((idx, score))
            results.sort(key=lambda x: x[1], reverse=True)
            return results

    # ------------------------------------------------------------------
    # Helper methods for ann_search (reduce cyclomatic complexity)
    # ------------------------------------------------------------------

    def _collect_usearch_candidates(
        self, query_np: np.ndarray, fetch_limit: int
    ) -> dict[str, tuple[list[float], str, float]]:
        """Collect ANN candidates from USEARCH index (M1 Metal SIMD).

        MRL-2 FIX: Fetch actual vectors from LanceDB for each key returned by USEARCH.
        Previous code returned empty vectors [] which caused cosine similarity = 0.0
        and all candidates to be filtered out by _MIN_SCORE (0.90).

        Returns empty dict on any error (fail-open).
        """
        if self._usearch_index is None:
            return {}
        try:
            matches = self._usearch_index.search(query_np, fetch_limit)
            candidates: dict[str, tuple[list[float], str, float]] = {}
            usearch_keys: list[str] = []
            for match in matches:
                idx = int(match.key)
                if idx < len(self._usearch_labels):
                    fk = self._usearch_labels[idx]
                    score = float(1.0 - match.distance)
                    candidates[fk] = ([], "", score)
                    usearch_keys.append(fk)

            # MRL-2 FIX: Fetch actual vectors from LanceDB for USEARCH keys
            # This ensures re-ranking gets real vectors instead of zeros
            if usearch_keys and self._table is not None:
                with self._lock:
                    try:
                        # Fetch vectors for all USEARCH keys in one query
                        result_df = self._table.to_lance().query().where(
                            f"finding_key IN ({','.join(repr(k) for k in usearch_keys)})"
                        ).select(["finding_key", "vector", "text_hash"]).to_list()

                        # Build lookup map
                        vector_map: dict[str, tuple[list[float], str]] = {}
                        for row in result_df:
                            fk = row.get("finding_key", "")
                            if fk:
                                vec = row.get("vector", [])
                                th = row.get("text_hash", "")
                                vector_map[fk] = (vec, th)

                        # Update candidates with actual vectors
                        for fk in usearch_keys:
                            if fk in vector_map:
                                vec, th = vector_map[fk]
                                old_score = candidates.get(fk, ([], "", 0.0))[2]
                                candidates[fk] = (vec, th, old_score)
                    except Exception:
                        # Fallback: vectors will be zeros → score will be 0.0
                        # But at least the keys are still searchable
                        pass

            return candidates
        except Exception as e:
            logger.debug(f"[ANN] USEARCH search failed: {e}")
            return {}

    def _collect_lancedb_candidates(
        self, emb_norm: np.ndarray, fetch_limit: int
    ) -> dict[str, tuple[list[float], str, float]]:
        """Collect ANN candidates from LanceDB fallback.

        M1 8GB optimization: nprobes=8 probes only 8/256 partitions (~3%% of index)
        instead of scanning the full table — ~97%% RAM bandwidth reduction.
        Returns empty dict on any error (fail-open).
        """
        with self._lock:
            try:
                results = (
                    self._table.search(emb_norm.tolist(), vector_column_name="vector")
                    .metric("cosine")
                    .nprobes(_IVF_PQ_NPROBES_DEFAULT)
                    .limit(fetch_limit)
                    .to_list()
                )
            except TypeError:
                # Fallback for LanceDB versions without nprobes on builder
                results = (
                    self._table.search(emb_norm.tolist(), vector_column_name="vector")
                    .metric("cosine")
                    .limit(fetch_limit)
                    .to_list()
                )

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
        return candidates

    def _apply_graph_filter_if_needed(
        self,
        candidates: dict[str, tuple[list[float], str, float]],
        graph_filter: Callable[[list[str]], list[str]] | None,
    ) -> dict[str, tuple[list[float], str, float]]:
        """Apply optional graph-aware filtering to ANN candidates.

        Returns unchanged candidates if graph_filter is None or on any error.
        """
        if graph_filter is None:
            return candidates
        try:
            filtered_keys = graph_filter(list(candidates.keys()))
            return {k: v for k, v in candidates.items() if k in filtered_keys}
        except Exception as e:
            logger.debug(f"[ANN] graph_filter failed: {e}")
            return candidates

    # SWARM-002: Multilingual index search helpers
    def _collect_usearch_candidates_multilingual(
        self, query_np: np.ndarray, fetch_limit: int
    ) -> dict[str, tuple[list[float], str, float]]:
        """Collect ANN candidates from multilingual USEARCH index (M1 Metal SIMD).

        MRL-2 FIX: Fetch actual vectors from multilingual LanceDB for each key.
        Previous code returned empty vectors [] which caused cosine similarity = 0.0
        and all candidates to be filtered out by _MIN_SCORE (0.90).

        Returns empty dict on any error (fail-open).
        """
        if self._usearch_index_multilingual is None:
            return {}
        try:
            matches = self._usearch_index_multilingual.search(query_np, fetch_limit)
            candidates: dict[str, tuple[list[float], str, float]] = {}
            usearch_keys: list[str] = []
            for match in matches:
                idx = int(match.key)
                if idx < len(self._usearch_labels_multilingual):
                    fk = self._usearch_labels_multilingual[idx]
                    score = float(1.0 - match.distance)
                    candidates[fk] = ([], "", score)
                    usearch_keys.append(fk)

            # MRL-2 FIX: Fetch actual vectors from multilingual LanceDB
            if usearch_keys and self._table_multilingual is not None:
                with self._lock:
                    try:
                        # Fetch vectors for all multilingual USEARCH keys
                        result_df = self._table_multilingual.to_lance().query().where(
                            f"finding_key IN ({','.join(repr(k) for k in usearch_keys)})"
                        ).select(["finding_key", "vector", "text_hash"]).to_list()

                        vector_map: dict[str, tuple[list[float], str]] = {}
                        for row in result_df:
                            fk = row.get("finding_key", "")
                            if fk:
                                vec = row.get("vector", [])
                                th = row.get("text_hash", "")
                                vector_map[fk] = (vec, th)

                        for fk in usearch_keys:
                            if fk in vector_map:
                                vec, th = vector_map[fk]
                                old_score = candidates.get(fk, ([], "", 0.0))[2]
                                candidates[fk] = (vec, th, old_score)
                    except Exception:
                        pass

            return candidates
        except Exception as e:
            logger.debug(f"[ANN] Multilingual USEARCH search failed: {e}")
            return {}

    def _collect_lancedb_candidates_multilingual(
        self, emb_norm: np.ndarray, fetch_limit: int
    ) -> dict[str, tuple[list[float], str, float]]:
        """Collect ANN candidates from multilingual LanceDB fallback.

        Returns empty dict on any error (fail-open).
        """
        if self._table_multilingual is None:
            return {}
        with self._lock:
            try:
                results = (
                    self._table_multilingual.search(emb_norm.tolist(), vector_column_name="vector")
                    .metric("cosine")
                    .nprobes(_IVF_PQ_NPROBES_DEFAULT)
                    .limit(fetch_limit)
                    .to_list()
                )
            except TypeError:
                # Fallback for LanceDB versions without nprobes on builder
                results = (
                    self._table_multilingual.search(emb_norm.tolist(), vector_column_name="vector")
                    .metric("cosine")
                    .limit(fetch_limit)
                    .to_list()
                )

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
        return candidates

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ann_search(
        self,
        embedding: np.ndarray,
        top_k: int = 5,
        graph_filter: Callable[[list[str]], list[str]] | None = None,
        language: str | None = None,
    ) -> list[dict]:
        """
        Hybrid ANN search: USEARCH (primary) → MLX cosine (exact re-rank).

        OPTIMIZATION: USEARCH provides ~10x faster ANN than LanceDB brute-force
        on M1 Metal. MLX provides exact cosine re-ranking on GPU.

        SWARM-002: Language-aware search:
        - If language is 'en' or None → search English index (ModernBERT)
        - If language is non-English → search multilingual index (BGE-M3)
        - Cross-lingual: searches both indexes and merges results

        P2-3 Enhancement — Graph-aware filtering:
          When ``graph_filter`` is provided, ANN candidates are expanded through
          the knowledge graph before re-scoring.

        Returns [] if not initialized or on any error (fail-open).
        Thread-safe via lock.
        """
        # Early exits for error states
        if self._boot_error is not None:
            return []
        if self._table is None:
            return []

        # Lazy IVF-PQ training
        if self._ivfpq_enabled:
            self._ensure_ivf_pq_index()

        try:
            # Normalize embedding
            emb = embedding.astype(np.float32)
            if emb.ndim == 1:
                emb = emb.reshape(1, -1)
            norm = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-8
            emb_norm = (emb / norm).squeeze(0)

            fetch_limit = top_k * 3 if graph_filter is not None else top_k * 2

            # Collect candidates: USEARCH primary → LanceDB fallback
            candidates: dict[str, tuple[list[float], str, float]] = {}

            # SWARM-002: Determine which indexes to search based on language
            search_english = language is None or language == 'en'
            search_multilingual = language is not None and language != 'en'

            query_np = np.array(emb_norm, dtype=np.float32)

            # Step 1: Search English index (USEARCH or LanceDB)
            if search_english:
                english_candidates = self._collect_usearch_candidates(query_np, fetch_limit)
                if not english_candidates:
                    english_candidates = self._collect_lancedb_candidates(emb_norm, fetch_limit)
                candidates.update(english_candidates)

            # SWARM-002: Search multilingual index
            if search_multilingual:
                multilingual_candidates = self._collect_usearch_candidates_multilingual(query_np, fetch_limit)
                if not multilingual_candidates:
                    multilingual_candidates = self._collect_lancedb_candidates_multilingual(emb_norm, fetch_limit)
                candidates.update(multilingual_candidates)

            if not candidates:
                return []

            # Step 3: Optional graph-aware filtering
            candidates = self._apply_graph_filter_if_needed(candidates, graph_filter)
            if not candidates:
                return []

            # Step 4: MLX exact cosine re-ranking
            candidate_items = list(candidates.items())
            indices = list(range(len(candidate_items)))
            vectors = [
                np.array(v[0], dtype=np.float32) if v[0]
                else np.zeros(self._embed_dim, dtype=np.float32)
                for v in candidate_items
            ]

            reranked = self._mlx_rerank(np.array(emb_norm, dtype=np.float32), indices, vectors)

            # Step 5: Build output with score thresholding
            output = []
            for idx, score in reranked[:top_k]:
                fk, (_, th, _dist) = candidate_items[idx]
                score = max(0.0, min(1.0, score))
                if score >= _MIN_SCORE:
                    output.append({
                        "finding_key": fk,
                        "text_hash": th or "",
                        "score": score,
                        "language": language or "en"
                    })

            return output

        except Exception as e:
            logger.debug(f"[ANN] ann_search failed: {e}")
            return []

    def upsert(
        self,
        finding_key: str,
        embedding: np.ndarray,
        text_hash: str,
        language: str | None = None,
    ) -> bool:
        """
        Upsert into both USEARCH (primary) and LanceDB (persistence).

        SWARM-002: If language is non-English, upserts to multilingual index.

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

            # SWARM-002: Route to appropriate table based on language
            is_multilingual = language is not None and language != 'en'
            target_table = self._table_multilingual if is_multilingual else self._table

            # MRL-2: Truncate multilingual embeddings to target dimension
            # BGE-M3 1024d → 256d for USEARCH index compatibility
            if is_multilingual and self._mrl_truncator is not None:
                try:
                    emb = self._mrl_truncator.truncate(emb)
                except Exception as e:
                    logger.debug(f"[ANN] MRL truncation failed: {e}, using full embedding")
            target_usearch = self._usearch_index_multilingual if is_multilingual else self._usearch_index
            target_labels = self._usearch_labels_multilingual if is_multilingual else self._usearch_labels

            # Add to LanceDB (source of truth for persistence)
            row = {
                "finding_key": finding_key,
                "vector": emb.tolist(),
                "text_hash": text_hash,
                "added_at": time.time(),
            }
            if is_multilingual:
                row["language"] = language

            with self._lock:
                target_table.add([row])

                # MRL-2 FIX: Moved USEARCH ops inside lock to prevent race condition
                # Previous code had data race between table.add() and usearch.add()
                # SAFE-4 FIX: Atomic label↔vector sync - add to USEARCH FIRST, then append label
                # If usearch.add() fails, label is NOT appended (no desync)
                # If usearch.add() succeeds but label.append() fails, vector is orphaned but
                # that's recoverable; desynced label→wrong-vector is NOT
                if target_usearch is not None:
                    try:
                        new_idx = len(target_labels)
                        # Add vector FIRST (source of truth for search)
                        target_usearch.add(new_idx, emb)
                        # Only append label after confirmed vector insert
                        target_labels.append(finding_key)
                    except Exception as e:
                        logger.error(f"[ANN] USEARCH upsert FAILED (vector not added): {e}")
                        # Increment desync counter for observability
                        self._usearch_desync_count += 1

            # Evict oldest if over cap (SAFE-4: evict lock prevents race with concurrent upserts)
            with self._evict_lock:
                self._maybe_evict()

            # Compaction scheduler
            self._insert_count_since_compact += 1
            self._maybe_compact_blocking()

            # Adaptive auto-tune
            if self._ivfpq_enabled and self._autotune is not None:
                try:
                    result = self._autotune.tune_if_due(
                        self._table,
                        current_num_partitions=self._ivfpq_num_partitions,
                        current_num_sub_vectors=self._ivfpq_num_sub_vectors,
                        inserts_delta=1,
                    )
                    if result.changed():
                        self._ivfpq_num_partitions = result.new_partitions
                        self._ivfpq_num_sub_vectors = result.new_num_sub_vectors
                        logger.info(
                            f"[ANN] auto-tune: partitions={result.old_partitions}->{result.new_partitions} "
                            f"sub_vectors={result.old_num_sub_vectors}->{result.new_num_sub_vectors}"
                        )
                except Exception:  # noqa: BLE001
                    pass

            return True

        except Exception as e:
            logger.debug(f"[ANN] upsert failed: {e}")
            return False

    def _maybe_evict(self) -> None:
        """Evict oldest entries if table exceeds MAX_ENTRIES (both English and multilingual).

        MRL-2 FIX: Evict by index in REVERSE order to prevent position shifting.
        Previous code removed labels one by one, causing label↔index desync.
        Now: collect indices first, sort descending, remove from end to beginning.

        PERFORMANCE OPTIMIZATION: Combined to_arrow() call instead of two separate calls.

        NOTE: Caller must hold _evict_lock before calling this method.
        """
        # Evict English index
        try:
            count = self._table.count_rows()
            if count > _MAX_ENTRIES:
                to_delete = int(count * 0.1)
                # MRL-2 OPTIMIZATION: Single to_arrow() call instead of two
                # Previous: _get_oldest_timestamp() + another to_arrow() = 2× full table copy
                oldest_table = self._table.to_arrow().sort_by([("added_at", "asc")]).slice(0, to_delete)
                if oldest_table.num_rows > 0:
                    keys_to_delete = oldest_table["finding_key"].to_pylist()

                    # MRL-2 FIX: Collect indices to remove, sort descending, remove from end
                    indices_to_remove = []
                    for key in keys_to_delete:
                        if key in self._usearch_labels:
                            idx = self._usearch_labels.index(key)
                            indices_to_remove.append((idx, key))

                    # Sort by index descending so we remove from end first
                    indices_to_remove.sort(key=lambda x: x[0], reverse=True)

                    for idx, key in indices_to_remove:
                        self._table.delete(f"finding_key = '{key}'")
                        try:
                            self._usearch_index.remove(idx)
                        except Exception:  # noqa: BLE001
                            pass
                        # Remove from list after index removal (now safe because we're going backward)
                        try:
                            self._usearch_labels.remove(key)
                        except ValueError:
                            pass  # Already removed
        except Exception as e:
            logger.debug(f"[ANN] English evict failed: {e}")

        # SWARM-002: Evict multilingual index
        try:
            if self._table_multilingual is not None:
                count_multi = self._table_multilingual.count_rows()
                if count_multi > _MAX_ENTRIES:
                    to_delete = int(count_multi * 0.1)
                    oldest_multi = self._table_multilingual.to_arrow().sort_by([("added_at", "asc")]).slice(0, to_delete)
                    keys_to_delete = oldest_multi["finding_key"].to_pylist()

                    # MRL-2 FIX: Same approach for multilingual index
                    indices_to_remove = []
                    for key in keys_to_delete:
                        if key in self._usearch_labels_multilingual:
                            idx = self._usearch_labels_multilingual.index(key)
                            indices_to_remove.append((idx, key))

                    indices_to_remove.sort(key=lambda x: x[0], reverse=True)

                    for idx, key in indices_to_remove:
                        self._table_multilingual.delete(f"finding_key = '{key}'")
                        try:
                            self._usearch_index_multilingual.remove(idx)
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            self._usearch_labels_multilingual.remove(key)
                        except ValueError:
                            pass
        except Exception as e:
            logger.debug(f"[ANN] Multilingual evict failed: {e}")

    def _maybe_compact_blocking(self) -> None:
        """LanceDB compaction trigger (sync, fail-soft)."""
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
            logger.debug("[ANN] compact ok")
        except Exception as e:
            logger.debug(f"[ANN] compact failed: {e}")
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

    def get_desync_stats(self) -> dict[str, int]:
        """
        Return desync observability metrics for SAFE-4 monitoring.

        SAFE-4: Exposes USEARCH label↔vector desync detection metrics.
        Call this periodically to detect data integrity issues.

        Returns:
            dict with:
            - usearch_desync_count: Number of failed usearch.add() calls
                                   (vector not added but LanceDB succeeded)
            - usearch_index_size: Current number of vectors in USEARCH index
            - usearch_labels_size: Current number of labels in parallel list
            - lancedb_rows: Current number of rows in LanceDB
        """
        try:
            usearch_size = len(self._usearch_labels) if self._usearch_labels else 0
            if self._usearch_index is not None:
                try:
                    usearch_size = int(self._usearch_index.size)
                except Exception:
                    pass
            lancedb_size = 0
            if self._table is not None:
                try:
                    lancedb_size = self._table.count_rows()
                except Exception:
                    pass
            return {
                'usearch_desync_count': self._usearch_desync_count,
                'usearch_index_size': usearch_size,
                'usearch_labels_size': len(self._usearch_labels) if self._usearch_labels else 0,
                'lancedb_rows': lancedb_size,
            }
        except Exception:
            return {
                'usearch_desync_count': self._usearch_desync_count,
                'usearch_index_size': 0,
                'usearch_labels_size': 0,
                'lancedb_rows': 0,
            }

    def prewarm(self, top_k: int = 128) -> None:
        """
        Pre-warm the ANN index for faster first-query latency.

        Ensures USEARCH index is loaded and pre-warms Metal memory.
        """
        if self._boot_error is not None:
            return

        if not self._check_memory_guard():
            return

        if not self.init():
            return

        try:
            # Dummy search to warm up USEARCH
            dummy = np.zeros(self._embed_dim, dtype=np.float32)
            self.ann_search(dummy, top_k=min(top_k, 5))
        except Exception as e:
            logger.debug(f"[ANN] prewarm failed: {e}")

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._db is not None:
                try:
                    close_fn = getattr(self._db, "close", None)
                    if callable(close_fn):
                        close_fn()
                except Exception:  # noqa: BLE001
                    pass
            self._db = None
            self._table = None
            self._table_multilingual = None  # SWARM-002
            self._usearch_index = None
            self._usearch_labels = []
            self._usearch_index_multilingual = None  # SWARM-002
            self._usearch_labels_multilingual = []
            self._boot_error = None
            self._initialized = False


# -----------------------------------------------------------------------
# Public facade
# -----------------------------------------------------------------------

_ann_index: _ANNIndex | None = None
_ann_index_lock = threading.Lock()


def get_ann_index(embed_dim: int = _EMBEDDING_DIM) -> _ANNIndex:
    """
    Get the singleton ANN index instance (sync, thread-safe).

    SWARM-002: embed_dim parameter for configurable embedding dimensions.

    Lazy-init on first call. Thread-safe via threading.Lock double-checked locking.
    """
    global _ann_index
    if _ann_index is None:
        with _ann_index_lock:
            if _ann_index is None:
                from hledac.universal.paths import PATHS

                db_path = PATHS.hledac_home / "ann_index"
                _ann_index = _ANNIndex(db_path, embed_dim=embed_dim)
                _ann_index.init()
    return _ann_index


_ann_index_async_lock = LazyAsyncioLock()


async def get_ann_index_async(embed_dim: int = _EMBEDDING_DIM) -> _ANNIndex:
    """
    Get the singleton ANN index instance (async-safe).

    SWARM-002: embed_dim parameter for configurable embedding dimensions.

    Lazy-init on first call. Async-safe via asyncio.Lock double-checked locking.
    """
    global _ann_index
    if _ann_index is None:
        async with _ann_index_async_lock:
            if _ann_index is None:
                from hledac.universal.paths import PATHS

                db_path = PATHS.hledac_home / "ann_index"
                _ann_index = _ANNIndex(db_path, embed_dim=embed_dim)
                _ann_index.init()
    return _ann_index


def check_ann_duplicate(
    embedding: np.ndarray,
    text_hash: str,
    finding_key: str,
    graph_filter: Callable[[list[str]], list[str]] | None = None,
    language: str | None = None,
) -> bool:
    """
    Check if an embedding matches any existing entry in ANN index.

    SWARM-002: language parameter for cross-lingual duplicate detection.

    Flow:
    1. USEARCH search for top-(top_k × 2) similar vectors (or graph-filtered pool)
    2. Graph expansion/filtering if graph_filter provided
    3. MLX exact cosine re-score on expanded candidates
    4. If score >= 0.90 → duplicate detected
    5. If no match → upsert current embedding

    Returns:
        True if duplicate detected, False otherwise.
        Always returns False on any error (fail-open).
    """
    try:
        ann = get_ann_index()
        if ann._boot_error is not None:
            return False

        results = ann.ann_search(embedding, top_k=5, graph_filter=graph_filter, language=language)
        for r in results:
            if r.get("text_hash") == text_hash and r.get("score", 0) >= _MIN_SCORE:
                logger.debug(f"[ANN] Duplicate detected: key={finding_key[:16]}, score={r['score']:.3f}")
                return True

        ann.upsert(finding_key, embedding, text_hash, language=language)
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
            except Exception:  # noqa: BLE001
                pass
        _ann_index = None
