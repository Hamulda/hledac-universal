"""
LanceDB Identity Store - Hybrid vector + FTS search for entity resolution.

.. deprecated:: F350M-R




    LanceDB-backed stores are DEPRECATED in favour of DuckDB-backed stores:
    - ``DuckDBEntityStore`` in ``knowledge.duckdb_rag_store``
    - ``DuckDBRAGStore`` in ``knowledge.duckdb_rag_store``
    - ``get_identity_store()`` async factory in ``knowledge.duckdb_rag_store``

    DuckDB FTS5 + HNSW provides equivalent functionality with:
    - ~0 MB subprocess overhead vs ~200 MB for LanceDB
    - M1 8GB native (no subprocess)

    Migration: swap ``LanceDBIdentityStore`` → ``DuckDBEntityStore``.
    Same API shape; seamless drop-in replacement.

ROLE: Identity/Entity Store (NOT grounding authority)
=====================================================
Provides identity stitching capabilities using LanceDB with:
- Vector embeddings for semantic similarity
- Full-text search (FTS) for alias matching
- Hybrid search combining both approaches

Sprint 71: Bounded, fail-safe, MLX fallback for similarity.
Sprint 77: Embedding optimization (float16, writeback buffer, batched embedding, health check).
"""
import asyncio
from hledac.universal.utils.async_helpers import safe_wait_for, bounded_parallel_map
from hledac.universal.utils.locks import LazyAsyncioLock
import contextlib
import hashlib
import logging
import os
import sys
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str, loads as _msgspec_loads
import numpy as np
import orjson
from context_optimization.mmr import maximal_marginal_relevance
from hledac.universal.tools.file_cache import apply_nocache_to_path, madv_nocache_on_path  # R-03: was madv_free_reusable_on_path (broken)
logger = logging.getLogger(__name__)
_uma_budget = None

def _get_uma_budget():
    global _uma_budget
    if _uma_budget is None:
        from hledac.universal.utils.uma_budget import is_uma_critical
        _uma_budget = is_uma_critical
    return _uma_budget
try:
    import mlx.core as mx

    @mx.compile
    def _cosine_sim_batch(a: mx.array, b: mx.array) -> mx.array:
        """MLX-compiled cosine similarity for batch processing.

        Args:
            a: Query embeddings (B, D) or (D,) — auto-handles singleton query
            b: Candidate embeddings (N, D)

        Returns:
            Similarity scores (B, N) — squeeze to (N,) if B=1
        """
        if a.ndim == 1:
            a = a[None, :]
        eps = 1e-08
        a_norm = mx.linalg.norm(a, axis=-1, keepdims=True)
        b_norm = mx.linalg.norm(b, axis=-1, keepdims=True)
        a_n = a / (a_norm + eps)
        b_n = b / (b_norm + eps)
        return a_n @ b_n.T
    MLX_AVAILABLE = True
except ImportError:
    import numpy as np

    def _cosine_sim_batch(a, b):
        """Numpy fallback for cosine similarity."""
        a = np.asarray(a)
        b = np.asarray(b)
        if a.ndim == 1:
            a = a[None, :]
        eps = 1e-08
        a_n = a / (np.linalg.norm(a, axis=-1, keepdims=True) + eps)
        b_n = b / (np.linalg.norm(b, axis=-1, keepdims=True) + eps)
        return a_n @ b_n.T
    MLX_AVAILABLE = False
_COMPILED_CACHE: dict[str, mx.array] = {}
_RRF_RERANKER_CACHE: dict[str, Any] = {}

def _get_rrf_reranker() -> Any | None:
    """Lazy-init LanceDB RRF reranker. Returns None if rerankers unavailable."""
    if 'rrf' in _RRF_RERANKER_CACHE:
        return _RRF_RERANKER_CACHE['rrf']
    try:
        from lancedb.rerankers import RRFReranker  # lazy: lancedb in [ml] extra
        _RRF_RERANKER_CACHE['rrf'] = RRFReranker(K=60, return_score='all')
        return _RRF_RERANKER_CACHE['rrf']
    except ImportError:
        logger.debug('[LANCEDB:H] lancedb.rerankers unavailable — hybrid without RRF')
        _RRF_RERANKER_CACHE['rrf'] = None
        return None
    except Exception as e:
        logger.debug(f'[LANCEDB:H] RRFReranker init failed: {e}')
        _RRF_RERANKER_CACHE['rrf'] = None
        return None
_DEFAULT_URI = Path(__file__).parent.parent.parent / 'data' / 'identity.lance'
_HLEDAC_DEFAULT_CACHE_MB = 256
_HLEDAC_HARD_MAX_CACHE_MB = 512
_HLEDAC_LARGE_OVERRIDE_VAR = 'HLEDAC_ALLOW_LARGE_LANCEDB_CACHE'
_HLEDAC_CACHE_MB_VAR = 'HLEDAC_LANCEDB_CACHE_MB'

def _resolve_lancedb_cache_size() -> int:
    """Resolve LMDB map_size from env with M1-safe defaults."""
    import os
    override_enabled = os.environ.get(_HLEDAC_LARGE_OVERRIDE_VAR, '').strip() in ('1', 'true', 'True')
    raw = os.environ.get(_HLEDAC_CACHE_MB_VAR, '').strip()
    if raw:
        try:
            mb = int(raw)
            if mb <= 0:
                logger.warning(f'[LANCEDB_CACHE] Invalid {_HLEDAC_CACHE_MB_VAR}={raw}, using default {_HLEDAC_DEFAULT_CACHE_MB}MB')
                mb = _HLEDAC_DEFAULT_CACHE_MB
            elif not override_enabled and mb > _HLEDAC_HARD_MAX_CACHE_MB:
                logger.warning(f'[LANCEDB_CACHE] {mb}MB exceeds hard max {_HLEDAC_HARD_MAX_CACHE_MB}MB without {_HLEDAC_LARGE_OVERRIDE_VAR}=1, capping')
                mb = _HLEDAC_HARD_MAX_CACHE_MB
            return mb * 1024 * 1024
        except ValueError:
            logger.warning(f'[LANCEDB_CACHE] Non-integer {_HLEDAC_CACHE_MB_VAR}={raw}, using default {_HLEDAC_DEFAULT_CACHE_MB}MB')
            return _HLEDAC_DEFAULT_CACHE_MB * 1024 * 1024
    if override_enabled:
        return 1024 * 1024 * 1024
    return _HLEDAC_DEFAULT_CACHE_MB * 1024 * 1024
_WRITEBACK_MAX = 1000
_WRITEBACK_BATCH_SIZE = 100

# Multi-writer architecture: per-table queues + multiple writer tasks
# M5 fix: replaces single global queue with sharded per-table queues
_NUM_WRITERS = 4  # M1 8GB: 4 writers × I/O-bound = good parallelism
_queues: dict[str, asyncio.Queue[tuple[list[dict[str, Any]], float]]] = {}
_queues_lock = LazyAsyncioLock()
_writer_tasks: list[asyncio.Task] = []
_writer_shutdown = False


async def _get_table_queue(table_name: str) -> asyncio.Queue:
    """Get or create a per-table write queue (sharded by table name).

    Multiple tables → multiple queues → writes to different tables run in parallel.
    """
    async with _queues_lock:
        if table_name not in _queues:
            _queues[table_name] = asyncio.Queue(maxsize=100)
        return _queues[table_name]


async def _ensure_write_workers() -> None:
    """Start multiple write workers if not already running (idempotent).

    M5: Replaces single _ensure_write_worker with multi-writer pool.
    Each writer round-robins across all table queues.
    """
    global _writer_tasks, _writer_shutdown
    if _writer_tasks:
        return
    _writer_shutdown = False
    for i in range(_NUM_WRITERS):
        t = asyncio.create_task(_writer_loop(i))
        _writer_tasks.append(t)
    logger.info(f'[LANCEDB:QW] {_NUM_WRITERS} write workers started')


async def _collect_queue_items() -> list[tuple[asyncio.Queue, list[dict[str, Any]], float, str]]:
    """Collect up to 10 items across all table queues (round-robin)."""
    items: list[tuple[asyncio.Queue, list[dict[str, Any]], float, str]] = []
    async with _queues_lock:
        queue_names = list(_queues.keys())

    for qname in queue_names:
        if len(items) >= 10:
            break
        async with _queues_lock:
            q = _queues.get(qname)
            if q is None:
                continue

        if q.empty():
            continue
        try:
            batch, deadline = q.get_nowait()
            if batch is None:
                q.task_done()
                continue
            if time.monotonic() > deadline + 30:
                logger.debug('[LANCEDB:QW] Skipping stale batch (deadline expired)')
                q.task_done()
                continue
            items.append((q, batch, deadline, qname))
        except asyncio.QueueEmpty:  # noqa: BLE001
            pass
    return items


async def _write_batch_to_table(writer_id: int, qname: str, batches: list) -> None:
    """Write all records from batches to a single table."""
    all_records = []
    for batch, _ in batches:
        all_records.extend(batch)
    table = _get_table_for_queue_name(qname)
    if table is not None:
        try:
            await safe_wait_for(
                asyncio.to_thread(table.add, all_records),
                timeout=30.0,
                label=f'lancedb_table_add:{qname}'
            )
            logger.debug(f'[LANCEDB:QW] writer={writer_id} wrote {len(all_records)} records to {qname}')
        except asyncio.TimeoutError:
            logger.warning(f'[LANCEDB:QW] writer={writer_id} add timed out after 30s')
        except Exception as e:
            logger.warning(f'[LANCEDB:QW] writer={writer_id} add failed: {e}')


async def _writer_loop(writer_id: int) -> None:
    """Background worker that drains write queues round-robin.

    M5: Multi-writer — each worker collects from ALL table queues,
    allowing parallel writes to different tables.

    Batch collection: gather up to 10 items across all queues per tick
    to amortize queue overhead while staying responsive.
    """
    while True:
        try:
            items = await _collect_queue_items()
            if not items:
                await asyncio.sleep(0.01)
                continue

            # Group by table
            by_table: dict[str, list] = {}
            for q, batch, deadline, qname in items:
                if qname not in by_table:
                    by_table[qname] = []
                by_table[qname].append((batch, deadline))

            # Write to each table
            await bounded_parallel_map(
                by_table.items(),
                lambda x: _write_batch_to_table(writer_id, x[0], x[1]),
                concurrency=2,
                ordered=False,
                ctx="lancedb_writer",
            )

            # Mark all queues done
            for q, _, _, _ in items:
                q.task_done()

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass


# Registry: table_name → table object (for multi-writer writes)
_table_registry: dict[str, Any] = {}
_table_registry_lock = LazyAsyncioLock()


def _register_table(table_name: str, table: Any) -> None:
    """Register a table for multi-writer access."""
    global _table_registry
    _table_registry[table_name] = table


def _get_table_for_queue_name(table_name: str) -> Any:
    """Get table reference by queue name."""
    return _table_registry.get(table_name)


async def _get_write_queue() -> asyncio.Queue:
    """Legacy compatibility — returns the default table queue.

    Deprecated: prefer _get_table_queue(table_name) for sharded writes.
    """
    return await _get_table_queue('entities')


async def _ensure_write_worker(table: Any) -> None:
    """Legacy compatibility wrapper — starts multi-writer pool.

    Deprecated: use _ensure_write_workers() instead.
    Registers the table and starts the writer pool.
    """
    table_name = getattr(table, '_table_name', 'entities')
    _register_table(table_name, table)
    await _ensure_write_workers()


async def _write_worker(table: Any, queue: asyncio.Queue) -> None:
    """Legacy single-writer (redirected to multi-writer pool).

    This function is kept for backward compatibility with existing tests.
    The actual work is done by _writer_loop in the multi-writer pool.
    """
    # Drain the provided queue (redirect to new architecture)
    while True:
        try:
            batch, deadline = await queue.get()
            if batch is None:
                break
            if time.monotonic() > deadline + 30:
                logger.debug('[LANCEDB:QW] Skipping stale batch (deadline expired)')
                queue.task_done()
                continue
            try:
                await safe_wait_for(asyncio.to_thread(table.add, batch), timeout=30.0, label='lancedb_table_add')
            except asyncio.TimeoutError:
                logger.warning('[LANCEDB:QW] add timed out after 30s, skipping batch')
            except Exception as e:
                logger.warning(f'[LANCEDB:QW] add failed: {e}')
            finally:
                queue.task_done()
        except asyncio.CancelledError:
            queue.task_done()
            raise
        except Exception:  # noqa: BLE001
            pass

class LanceDBIdentityStore:
    """
    Identity store using LanceDB for entity resolution.

    ROLE: Identity/Entity Store (NOT grounding authority)
    ====================================================
    - entity identity storage (add_entity, search_similar)
    - NENÍ owner context grounding → rag_engine
    - NENÍ owner document retrieval → rag_engine HNSWVectorIndex
    - Embedding policy: MLXEmbeddingManager singleton přes _mlx_embed_manager
    - Thermal awareness coupling: volá self._orch._memory_mgr (optional, debt)

    LanceDB is OPT-IN via ``uv sync --extra ml`` (lancedb in [ml] extra).
    Primary store is SqliteVecIdentityStore (zero-process, M1-native, ~5MB).
    LanceDB kept as fallback for >1M vectors where Rust-side HNSW benefits.

    Features:
    - Hybrid search (vector + FTS)
    - Bounded storage
    - MLX acceleration for similarity computation
    - Fail-safe degradation
    - Sprint 76: LMDB embedding cache with float16 quantization (50% RAM savings)
    - Sprint 76: Binary embeddings for fast pre-filter (32x compression)
    - Sprint 76: MMR diversity filtering
    - Sprint 76: Adaptive reranking (ColBERT/FlashRank/MLX)
    - Sprint 76: usearch index support (lazy)
    """
    _MAX_CACHE_SIZE = _resolve_lancedb_cache_size()
    _BINARY_FILTER_COUNT = 500
    _MMR_TOP_K = 50
    _EVICTION_THRESHOLD_RATIO = 0.85
    # Compact thresholds
    _COMPACT_FRAGMENT_THRESHOLD = 1000  # Trigger compact after N inserts
    _COMPACT_TIME_THRESHOLD_S = 300.0  # Trigger compact after N seconds
    _COMPACT_MIN_INTERVAL_S = 60.0  # Minimum interval between compacts
    __slots__ = tuple((
        '_cache_db', '_cache_env', '_embedding_dim', '_mlx_embeddings', '_mlx_embeddings_total_count',
        '_mlx_id_to_idx', '_mlx_ids', '_mlx_load_chunk_size', '_orch', '_table', 'db', 'uri',
        '_binary_embeddings', '_compact_in_flight', '_insert_count_since_compact', '_last_compact_ts',
        # MRL-2 FIX: Added missing __slots__ attributes that were causing AttributeError
        '_colbert_reranker', '_flashrank_reranker', '_colbert_loaded', '_flashrank_loaded',
        '_memory_history', '_eviction_threshold', '_usearch_index', '_usearch_loaded', '_lancedb_has_fts',
        '_embedder', '_embedder_type', '_embed_lock', '_current_mrl_dim', '_mrl_enabled',
        '_mlx_embed_manager', '_fallback_dim', '_writeback_buffer', '_writeback_lock',
        '_access_counts', '_index_build_status', '_index_cache', '_index_cache_time',
        '_index_build_deferred', '_metrics', '_large_override_enabled', '_ivfpq_enabled',
        '_ivfpq_num_partitions', '_ivfpq_num_sub_vectors', '_usearch_index_multilingual',
        '_usearch_loaded_multilingual', '_usearch_labels_multilingual', '_multilingual_table_name',
        '_mrl_truncator', '_mrl_source_dim', '_mrl_target_dim',
    ))

    def __init__(self, uri: str=str(_DEFAULT_URI), orchestrator=None):
        """
        Initialize LanceDB identity store.

        Args:
            uri: Path to LanceDB database.
            orchestrator: Optional orchestrator reference for memory context.
        """
        self.uri = uri
        self.db = None
        self._table = None
        self._orch = orchestrator
        self._embedding_dim = 256
        self._cache_env = None
        self._cache_db = None
        self._init_cache()
        self._mlx_embeddings = None
        self._mlx_ids = None
        self._mlx_id_to_idx = {}
        self._mlx_embeddings_total_count = 0
        self._mlx_load_chunk_size = self._get_mlx_chunk_size()
        self._colbert_reranker = None
        self._flashrank_ranker = None
        self._colbert_loaded = False
        self._flashrank_loaded = False
        self._memory_history: Any = None
        self._eviction_threshold = 0.8
        self._usearch_index = None
        self._usearch_loaded = False
        self._lancedb_has_fts = False
        self._embedder = None
        self._embedder_type: str | None = None
        self._embed_lock = asyncio.Lock()
        self._current_mrl_dim = 256
        self._mrl_enabled = False
        self._mlx_embed_manager = None
        self._fallback_dim = 256
        self._writeback_buffer: dict = {}
        self._writeback_lock = asyncio.Lock()
        self._access_counts = defaultdict(int)
        self._index_build_status: dict[str, Any] = {'in_progress': False, 'started_at': None, 'completed_at': None, 'failed': False, 'index_type': None, 'progress_percent': 0}
        self._index_cache: bool | None = None
        self._index_cache_time: float = 0.0
        self._index_build_deferred = False
        self._metrics = {'cache_hits': 0, 'cache_misses': 0, 'quantization_errors': deque(maxlen=100), 'search_latencies': deque(maxlen=1000), 'cache_evictions': 0}
        self._large_override_enabled = _resolve_lancedb_cache_size() > _HLEDAC_HARD_MAX_CACHE_MB * 1024 * 1024
        self._ivfpq_enabled: bool = os.environ.get('HLEDAC_LANCEDB_QUANTIZE', '1') != '0'
        self._ivfpq_num_partitions: int = max(8, min(256, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS', '256'))))
        self._ivfpq_num_sub_vectors: int = max(4, min(64, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS', '8'))))
        # M1 8GB IVF-PQ nprobes search optimization (probes only ~3% of 256 partitions)
        # Avoids full table scan — ~97% RAM bandwidth reduction on M1 UMA
        self._ivfpq_nprobes: int = max(1, min(64, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NPROBES', '8'))))
        self._ivfpq_trained: bool = False
        self._ivfpq_lock: asyncio.Lock = asyncio.Lock()
        # Compact tracking
        self._compact_in_flight: bool = False
        self._insert_count_since_compact: int = 0
        self._last_compact_ts: float = 0.0
        # Binary embeddings (for fast pre-filter)
        self._binary_embeddings: Any = None
        try:
            from hledac.universal.knowledge.lancedb_auto_tuner import make_default_tuner
            self._autotune = make_default_tuner(table_name='entities', state_dir=Path(uri).parent, num_sub_vectors=self._ivfpq_num_sub_vectors, vector_column='embedding', key_column='id')
        except Exception:
            self._autotune = None
        self._initialize()

    def _get_mlx_chunk_size(self) -> int:
        """
        Sprint #15: Adaptive chunk sizing based on current memory pressure.

        Returns the computed chunk size based on UMA state. The result is
        cached in self._mlx_load_chunk_size by the caller (__init__).

        Returns:
            1_000 if state == "emergency" (minimal, fail-safe)
            3_000 if state == "critical" (reduced)
            5_000 if state == "warn" (moderate)
            1_000 if swap_detected (abort mid-load signal — use minimal)
            10_000 if state == "ok" / "soft_warn" / error (default, safe)
        """
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            uma = sample_uma_status()
            if uma.swap_detected:
                return 1000
            state = uma.state
            if state == 'emergency':
                return 1000
            if state == 'critical':
                return 3000
            if state == 'warn':
                return 5000
            # 'ok' / 'soft_warn' / fallback — use cached default
            return self._mlx_load_chunk_size
        except Exception:
            # Fail-safe: use cached value from previous call or default
            return self._mlx_load_chunk_size

    def _lmdb_put(self, key: str, data: dict) -> None:
        """Synchronous LMDB put operation - zero-copy via orjson."""
        try:
            with self._cache_env.begin(write=True) as txn:
                txn.put(key.encode(), _msgspec_dumps_str(data))
        except Exception as e:
            logger.debug(f'LMDB put failed: {e}')

    def _lmdb_put_multi(self, items: list[tuple[str, dict]]) -> None:
        """Synchronous batch LMDB put - single transaction for multiple items.

        S3: Reduces 100 individual txn.begin() calls to 1.
        """
        if not items:
            return
        try:
            with self._cache_env.begin(write=True) as txn:
                for key, data in items:
                    txn.put(key.encode(), _msgspec_dumps_str(data))
        except Exception as e:
            logger.debug(f'LMDB batch put failed ({len(items)} items): {e}')

    def _delete_cached_embedding(self, text_hash: str) -> None:
        """Delete embedding from cache."""
        try:
            with self._cache_env.begin(write=True) as txn:
                txn.delete(text_hash.encode())
        except Exception:  # noqa: BLE001
            pass

    async def _flush_writeback(self) -> None:
        """Flush writeback buffer to LMDB — single batch transaction."""
        async with self._writeback_lock:
            items = list(self._writeback_buffer.items())
            self._writeback_buffer.clear()
        if not items:
            return

        def _batch_put():
            try:
                with self._cache_env.begin(write=True) as txn:
                    for key, val in items:
                        txn.put(key.encode(), _msgspec_dumps_str(val))
                return None
            except Exception as e:
                logger.warning(f'LMDB batch put failed ({len(items)} items): {e}')
                return items
        failed_items = await asyncio.to_thread(_batch_put)
        if failed_items:
            async with self._writeback_lock:
                for key, val in failed_items:
                    if key not in self._writeback_buffer:
                        self._writeback_buffer[key] = val

    async def _initialize_embedder(self) -> bool:
        """Initialize embedder: MLX/GPU → CoreML/ANE → Numpy fallback."""
        try:
            from hledac.universal.core.mlx_embeddings import get_embedding_manager
            self._mlx_embed_manager = get_embedding_manager()
            self._embedder = self._mlx_embed_manager
            self._embedder_type = 'mlx_gpu'
            logger.info(f'[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_embed_manager.model_path}, dim={self._mlx_embed_manager.EMBEDDING_DIM}')
            return True
        except ImportError:
            logger.debug('[EMBEDDER] mlx_embeddings not available, trying MLX direct')
        except Exception as e:
            logger.debug(f'[EMBEDDER] MLXEmbeddingManager init failed: {e}')
        logger.warning('[EMBEDDER] No hardware acceleration, using numpy fallback')
        self._embedder_type = 'numpy_fallback'
        self._fallback_dim = self._current_mrl_dim
        return True

    async def _embed_single(self, text: str) -> list[float]:
        """Embed single text via current embedder (for indexing - uses embed_document)."""
        if self._embedder_type is None:
            await self._initialize_embedder()
        if self._embedder_type == 'numpy_fallback':
            # IO-4 fix: Deterministic hash-based fallback instead of np.random.randn
            # to avoid poisoning LanceDB ANN index with random vectors
            rng = np.random.default_rng(hash(text) & 0xFFFFFFFF)
            emb = rng.standard_normal(self._fallback_dim, dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = (emb / norm).tolist()
            return emb
        if self._embedder is None:
            return []
        try:
            if self._embedder_type == 'mlx_gpu':
                result = await asyncio.to_thread(self._embedder.embed_document, text)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)
            else:
                result = await asyncio.to_thread(self._embedder.encode, text)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)
            if len(emb) > self._current_mrl_dim:
                emb = emb[:self._current_mrl_dim]
            return emb
        except Exception as e:
            logger.warning(f'[EMBED] Single embed failed: {e}')
            return []

    async def _embed_batch(self, texts: list[str], batch_size: int=16) -> list[list[float]]:
        """Generate embeddings in batches - thread-safe (uses embed_document for indexing)."""
        if not texts:
            return []
        if self._embedder_type is None:
            await self._initialize_embedder()
        if self._embedder_type == 'numpy_fallback':
            # IO-4 fix: Deterministic hash-based fallback instead of np.random.randn
            # to avoid poisoning LanceDB ANN index with random vectors
            all_embs = []
            for i, text in enumerate(texts):
                rng = np.random.default_rng(hash((text, i)) & 0xFFFFFFFF)
                emb = rng.standard_normal(self._fallback_dim, dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                all_embs.append(emb.tolist())
            return all_embs
        all_embs = []
        if _get_uma_budget()():
            logger.warning(f'[EMBED] Skipping {len(texts)} embeddings — M1 critical memory pressure ({len(texts)} texts, batch_size={batch_size})')
            return [[0.0] * self._current_mrl_dim for _ in texts]
        async with self._embed_lock:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                try:
                    if self._embedder_type == 'mlx_gpu':
                        emb_result = await asyncio.to_thread(self._embedder._embed_for_indexing, batch)
                        batch_embs = emb_result.tolist() if hasattr(emb_result, 'tolist') else list(emb_result)
                    else:
                        embs = await asyncio.to_thread(self._embedder.encode, batch)
                        batch_embs = embs.tolist() if hasattr(embs, 'tolist') else list(embs)
                    for emb in batch_embs:
                        if len(emb) > self._current_mrl_dim:
                            emb = emb[:self._current_mrl_dim]
                        all_embs.append(emb)
                except Exception:
                    # E2-FIX: fallback to bounded_parallel_map — concurrent, M1-safe
                    # Concurrency=2: embedder is I/O-bound (CPU blocking), 2 avoids Metal saturation
                    emb_results = await bounded_parallel_map(
                        batch,
                        self._embed_single,
                        concurrency=2,
                        ordered=True,
                        ctx="lancedb_embed_fallback",
                    )
                    for emb in emb_results:
                        if emb is not None:
                            all_embs.append(emb)
        return all_embs

    def _compute_binary_signature(self, embedding: list[float]) -> int:
        """64-bit binary signature - numpy packbits (faster for 64 elements)."""
        arr = np.array(embedding[:64], dtype=np.float32) > 0
        packed = np.packbits(arr, bitorder='little')
        return int.from_bytes(packed.tobytes()[:8], 'little')

    def _compute_binary_signatures_batch(self, embeddings: list[list[float]]) -> list[int]:
        """MLX version for batched calculations."""
        try:
            import mlx.core as mx
            embs = mx.array([e[:64] for e in embeddings])
            bits = (embs > 0).astype(mx.uint64)
            powers = mx.array([1 << i for i in range(64)], dtype=mx.uint64)
            signatures = mx.sum(bits * powers, axis=1)
            return [int(s) for s in signatures]
        except Exception:
            return [self._compute_binary_signature(e) for e in embeddings]

    async def _detect_query_type(self, query_text: str) -> str:
        """Decide whether to use FTS, hybrid, or pure vector search."""
        if not query_text or not self._lancedb_has_fts:
            return 'vector'
        words = query_text.split()
        if '"' in query_text or len(words) <= 2:
            return 'fts'
        if len(words) >= 10 and (not any((w[0].isupper() or w[0].isdigit() for w in words if w))):
            return 'vector'
        return 'hybrid'

    def _rrf_fusion(self, fts_results: list[dict], vec_results: list[dict], top_k: int, k: int=60) -> list[dict]:
        """Reciprocal Rank Fusion with robust keying — NumPy vectorized."""
        scores: dict[str, float] = defaultdict(float)
        docs: dict[str, dict] = {}
        fts_keys, fts_ranks = ([], [])
        for rank, doc in enumerate(fts_results):
            key = doc.get('id') or doc.get('_rowid')
            if key is None:
                key = hashlib.md5(doc.get('text', '').encode()).hexdigest()
            fts_keys.append(key)
            fts_ranks.append(rank)
            docs[key] = doc
        vec_keys, vec_ranks = ([], [])
        for rank, doc in enumerate(vec_results):
            key = doc.get('id') or doc.get('_rowid')
            if key is None:
                key = hashlib.md5(doc.get('text', '').encode()).hexdigest()
            vec_keys.append(key)
            vec_ranks.append(rank)
            docs[key] = doc
        if fts_keys:
            fts_arr = np.array(fts_ranks, dtype=float)
            fts_arr = 1.0 / (k + fts_arr + 1)
            for key, score in zip(fts_keys, fts_arr, strict=True):
                scores[key] += score
        if vec_keys:
            vec_arr = np.array(vec_ranks, dtype=float)
            vec_arr = 1.0 / (k + vec_arr + 1)
            for key, score in zip(vec_keys, vec_arr, strict=True):
                scores[key] += score
        sorted_keys = sorted(scores, key=scores.get, reverse=True)
        return [docs[key] for key in sorted_keys[:top_k]]

    async def ensure_index(self, force: bool=False) -> None:
        """Create index with respect to available RAM and thermal state."""
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / 1024 ** 3
            if available_gb < 1.5:
                logger.warning('[INDEX] Critical memory (<1.5GB), skipping index build')
                return
            if available_gb < 3.0:
                logger.info('[INDEX] Low memory (<3GB), deferring index build')
                self._index_build_deferred = True
                return
        except Exception as e:
            logger.debug(f'[LANCE] memory check failed: {e}')
        if self._index_build_deferred and (not force):
            try:
                import psutil
                if psutil.virtual_memory().available / 1024 ** 3 >= 3.0:
                    self._index_build_deferred = False
            except Exception:  # noqa: BLE001
                pass

    async def _warm_embedding_cache(self, queries: list[str], top_k: int=50) -> None:
        """Pre-load embeddings for frequently used queries."""
        if not queries:
            return
        logger.info(f'[CACHE WARM] Warming {len(queries)} query embeddings')
        for query in queries[:top_k]:
            q_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
            if await self._get_cached_embedding(q_hash) is None:
                emb = await self._embed_single(query)
                if emb:
                    await self._store_embedding(q_hash, emb)
        logger.info('[CACHE WARM] Complete')

    async def _cache_maintenance_loop(self) -> None:
        """Background cache maintenance task."""
        while True:
            try:
                await asyncio.sleep(300)
                await self._flush_writeback()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass

    async def health_check(self) -> dict[str, Any]:
        """Check embedding store health."""
        result = {'healthy': True, 'cache_size': len(self._writeback_buffer), 'index_exists': False, 'embedder_type': getattr(self, '_embedder_type', 'not_initialized'), 'errors': [], **self.get_cache_telemetry()}
        try:
            if self._embedder is None:
                result['healthy'] = False
                result['errors'].append('embedder_not_initialized')
            await self._flush_writeback()
            result['writeback_healthy'] = True
            if self._cache_env is None:
                result['healthy'] = False
                result['errors'].append('cache_not_initialized')
        except Exception as e:
            result['healthy'] = False
            result['errors'].append(str(e))
        return result

    def get_cache_telemetry(self) -> dict[str, Any]:
        """F214OPT-C: Telemetry accessor for LanceDB cache bounds and stats."""
        result = {'lancedb_cache_limit_mb': self._MAX_CACHE_SIZE / (1024 * 1024), 'lancedb_cache_current_items': len(self._writeback_buffer), 'lancedb_cache_evictions': self._metrics.get('cache_evictions', 0), 'lancedb_cache_large_override_enabled': self._large_override_enabled}
        try:
            if self._cache_env is not None:
                info = self._cache_env.info()
                stat = self._cache_env.stat()
                result['lancedb_cache_map_size_bytes'] = info['map_size']
                result['lancedb_cache_used_bytes'] = stat['last_pgno'] * stat['psize']
                result['lancedb_cache_used_ratio'] = result['lancedb_cache_used_bytes'] / info['map_size']
        except Exception:  # noqa: BLE001
            pass
        return result

    async def shutdown(self) -> None:
        """Cleanup resources."""
        global _writer_tasks, _writer_shutdown
        for task_name in ['_cache_maintenance_task']:
            task = getattr(self, task_name, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        # M5: shutdown multi-writer pool
        if _writer_tasks:
            _writer_shutdown = True
            for t in _writer_tasks:
                t.cancel()
            for t in _writer_tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            _writer_tasks = []
        await self._flush_writeback()
        if self._cache_env is not None:
            try:
                self._cache_env.close()
            except Exception:  # noqa: BLE001
                pass
        if self._mlx_embeddings is not None:
            del self._mlx_embeddings
            self._mlx_embeddings = None
        if self._binary_embeddings is not None:
            del self._binary_embeddings
            self._binary_embeddings = None
        self._mlx_ids = None
        self._mlx_id_to_idx = {}
        self._mlx_embeddings_total_count = 0
        try:
            import gc
            gc.freeze()
        except Exception:  # noqa: BLE001
            pass
        try:
            import mlx.core as mx
            mx.eval([])
            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    def _init_cache(self) -> None:
        """Initialize LMDB cache for embeddings with float16 quantization."""
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            cache_path = Path(self.uri).parent / 'embedding_cache'
            cache_path.mkdir(parents=True, exist_ok=True)
            self._cache_env = open_lmdb_with_guard(cache_path, map_size=self._MAX_CACHE_SIZE)
            madv_nocache_on_path(cache_path)
            apply_nocache_to_path(cache_path)
            self._cache_db = self._cache_env.open_db()
            self._memory_history = deque(maxlen=10)
            logger.debug('LMDB embedding cache initialized')
        except Exception as e:
            logger.warning(f'Failed to init embedding cache: {e}')
            self._cache_env = None

    async def _get_cached_embedding(self, text_hash: str) -> list[float] | None:
        """Get embedding from LMDB cache with writeback buffer."""
        if self._cache_env is None:
            return None
        async with self._writeback_lock:
            if text_hash in self._writeback_buffer:
                data = self._writeback_buffer[text_hash]
                self._metrics['cache_hits'] += 1
                if data['dtype'] == 'float16':
                    emb = np.frombuffer(data['embedding'], dtype=np.float16)
                else:
                    emb = np.frombuffer(data['embedding'], dtype=np.float32)
                return emb.astype(np.float32).tolist()

        def _sync():
            try:
                with self._cache_env.begin() as txn:
                    cached = txn.get(text_hash.encode())
                    if cached:
                        data = _msgspec_loads(cached)
                        if 'ttl' in data and 'stored_at' in data:
                            if time.time() - data['stored_at'] > data['ttl']:
                                return (None, True)
                        emb_np = np.frombuffer(data['embedding'], dtype=np.float16)
                        return (emb_np.astype(np.float32).tolist(), False)
            except Exception:  # noqa: BLE001
                pass
            return (None, False)
        result = await asyncio.to_thread(_sync)
        if result is None or result[0] is None:
            self._metrics['cache_misses'] += 1
            return None
        if result[1]:
            await asyncio.to_thread(self._delete_cached_embedding, text_hash)
            self._metrics['cache_misses'] += 1
            return None
        data, _ = result
        new_data = {'embedding': data.get('embedding'), 'dtype': data.get('dtype', 'float16'), 'dim': data.get('dim', 768), 'ttl': data.get('ttl', 86400), 'stored_at': data.get('stored_at', time.time()), 'access_count': data.get('access_count', 0) + 1, 'last_access': time.time()}
        self._access_counts[text_hash] = self._access_counts.get(text_hash, 0) + 1
        async with self._writeback_lock:
            self._writeback_buffer[text_hash] = new_data
            if len(self._writeback_buffer) > _WRITEBACK_MAX:
                items_to_evict = sorted(self._writeback_buffer.items(), key=lambda x: self._access_counts.get(x[0], 0))[:_WRITEBACK_BATCH_SIZE]
                flush_items = items_to_evict
                for flush_key, _ in flush_items:
                    self._writeback_buffer.pop(flush_key, None)
                    self._access_counts.pop(flush_key, None)
            else:
                flush_items = []
        if flush_items:
            await asyncio.to_thread(self._lmdb_put_multi, flush_items)
            self._metrics['cache_evictions'] += len(flush_items)
        self._metrics['cache_hits'] += 1
        return data

    async def _store_embedding(self, text_hash: str, embedding: list[float], ttl: float | None=None) -> None:
        """Store embedding with float16 quantization (50% memory savings) and writeback buffer."""
        if self._cache_env is None:
            return
        try:
            info = self._cache_env.info()
            stat = self._cache_env.stat()
            map_size = info['map_size']
            cache_usage = stat['last_pgno'] * stat['psize']
            if cache_usage / map_size >= self._EVICTION_THRESHOLD_RATIO:
                logger.debug(f'[LANCEDB_CACHE] Skipping store — map near full ({cache_usage / map_size:.2f})')
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            emb_np = np.array(embedding, dtype=np.float16)
            data = {'embedding': emb_np.tobytes(), 'dtype': 'float16', 'dim': len(embedding), 'ttl': ttl or 86400, 'stored_at': time.time(), 'access_count': 0}
            self._access_counts[text_hash] = self._access_counts.get(text_hash, 0) + 1
            async with self._writeback_lock:
                self._writeback_buffer[text_hash] = data
                if len(self._writeback_buffer) > _WRITEBACK_MAX:
                    items_to_evict = sorted(self._writeback_buffer.items(), key=lambda x: self._access_counts.get(x[0], 0))[:_WRITEBACK_BATCH_SIZE]
                    flush_items = items_to_evict
                    for flush_key, _ in flush_items:
                        self._writeback_buffer.pop(flush_key, None)
                        self._access_counts.pop(flush_key, None)
                else:
                    flush_items = []
            if flush_items:
                await asyncio.to_thread(self._lmdb_put_multi, flush_items)
                self._metrics['cache_evictions'] += len(flush_items)
        except Exception as e:
            logger.debug(f'Failed to store embedding: {e}')

    async def _warm_cache(self, top_k: int=100) -> None:
        """Pre-load frequently accessed embeddings."""
        if not self._orch or not hasattr(self._orch, '_evidence_log') or self._orch._evidence_log is None:
            return
        try:
            recent = self._orch._evidence_log.get_recent_evidence(top_k)

            async def _process_evidence(ev_item: Any) -> None:
                text_hash = hashlib.sha256(ev_item.content.encode()).hexdigest()[:16]
                cached = await self._get_cached_embedding(text_hash)
                if cached is None and hasattr(ev_item, 'embedding') and ev_item.embedding:
                    await self._store_embedding(text_hash, ev_item.embedding)

            # E2-FIX: parallel warm_cache — bounded_concurrency=3 (I/O-bound, M1-safe)
            await bounded_parallel_map(
                recent,
                _process_evidence,
                concurrency=3,
                ordered=False,
                ctx="warm_cache",
            )
            logger.info(f'Cache warmed with {top_k} embeddings')
        except Exception as e:
            logger.debug(f'Cache warming failed: {e}')

    async def _load_embeddings_to_mlx(self) -> None:
        """
        Load embeddings to MLX using chunked streaming for M1 8GB safety.

        P6-fix: original loaded ALL embeddings at once (~400MB+ for 100k rows).
        Now streams in chunks of _mlx_load_chunk_size rows, building index incrementally.
        Memory budget: 10k rows × 256 dims × 4 bytes ≈ 10MB per chunk.
        F265FIX: Added RAM guard before loading — skip MLX path when available memory < 3GB.
        """
        if self._table is None:
            return
        try:
            import mlx.core as mx
            try:
                if self._orch and hasattr(self._orch, '_memory_mgr'):
                    available_gb = self._orch._memory_mgr.get_available_memory_gb()
                else:
                    available_gb = 8.0
                if available_gb < 3.0:
                    logger.debug(f'[LANCEDB_MLX] Skipping MLX load — only {available_gb:.1f}GB available (need >3GB for embedding table)')
                    return
            except Exception:  # noqa: BLE001
                pass
            total_count = self._table.count_rows()
            if total_count == 0:
                return
            MAX_MLX_ROWS = 50000
            if total_count > MAX_MLX_ROWS:
                logger.debug(f'[LANCEDB_MLX] Capping load from {total_count} to {MAX_MLX_ROWS} rows (M1 8GB GPU memory budget)')
                total_count = MAX_MLX_ROWS
            chunk_size = self._get_mlx_chunk_size()
            all_embeddings: list[mx.array] = []
            all_ids: list[str] = []
            id_to_idx_global: dict[str, int] = {}
            for offset in range(0, total_count, chunk_size):
                try:
                    from hledac.universal.core.resource_governor import sample_uma_status
                    uma = sample_uma_status()
                    if uma.swap_detected:
                        logger.debug(f'[LANCEDB_MLX] Aborting load at chunk offset {offset} — swap detected mid-load (used {uma.swap_used_gib:.1f}GiB)')
                        break
                except Exception:  # noqa: BLE001
                    pass
                limit = min(chunk_size, total_count - offset)
                # MRL-2 FIX: Column name is 'embedding' not '_embedding'
                # Table schema uses 'embedding' (from add_entity line 1471)
                chunk_data = self._table.to_lance().to_table(columns=['embedding', 'id'], offset=offset, limit=limit).to_pydict()
                if not chunk_data.get('embedding'):
                    continue
                raw_emb = chunk_data['embedding']
                import numpy as np
                if raw_emb and isinstance(raw_emb[0], (list, tuple)):
                    emb_np = np.ascontiguousarray(raw_emb, dtype=np.float32)
                else:
                    emb_np = np.ascontiguousarray(raw_emb, dtype=np.float32) if raw_emb else np.array([], dtype=np.float32)
                emb_chunk = mx.array(emb_np)
                ids_chunk = chunk_data['id']
                # MRL-2 FIX: Sign convention unified to (>= 0) for consistent Hamming.
                # Previous: (emb_chunk > 0) caused mismatch with _pack_query_to_binary.
                # Now: 1 if >= 0, 0 if < 0 — matches query packing convention.
                signs = (emb_chunk >= 0).astype(mx.uint8)
                batch, dim = signs.shape
                padded_dim = (dim + 7) // 8 * 8
                padded = mx.zeros((batch, padded_dim), dtype=mx.uint8)
                padded[:, :dim] = signs
                packed = mx.zeros((batch, padded_dim // 8), dtype=mx.uint8)
                for i in range(8):
                    packed |= padded[:, i::8] << 7 - i
                all_embeddings.append(packed)
                base_idx = len(all_ids)
                for i, row_id in enumerate(ids_chunk):
                    id_to_idx_global[str(row_id)] = base_idx + i
                all_ids.extend((str(r) for r in ids_chunk))
                self._mlx_embeddings_total_count = offset + limit
                logger.debug(f'MLX chunk {offset}-{offset + limit}/{total_count} loaded')
            if not all_embeddings:
                return
            self._mlx_embeddings = mx.concatenate(all_embeddings, axis=0)
            self._mlx_ids = all_ids
            self._mlx_id_to_idx = id_to_idx_global
            # MRL-2 FIX: Column name is 'embedding' not '_embedding'
            self._embedding_dim = len(chunk_data['embedding'][0]) if chunk_data.get('embedding') else 256
            logger.info(f'Loaded {len(all_ids)} embeddings to MLX in {len(all_embeddings)} chunks (M1 8GB safe)')
        except Exception as e:
            logger.warning(f'Failed to load embeddings to MLX: {e}')

    async def _mlx_rerank(self, query_emb: list[float], candidates: list[dict], top_k: int) -> list[dict]:
        """Rerank candidates using MLX cosine similarity.

        P4.2: Uses module-level _cosine_sim_batch (compiled once at import).
        Supports (B, D) × (N, D) → (B, N) for flexible batching.

        MRL-2 FIX: Uses float32 vectors from candidates dict directly,
        not packed uint8 from _mlx_embeddings (dtype mismatch bug).
        """
        if not candidates:
            return candidates[:top_k]

        # MRL-2 FIX: Extract float32 vectors from candidates, not uint8 from _mlx_embeddings
        cand_vectors = []
        valid_candidates = []
        for c in candidates:
            vec = c.get('vector') or c.get('_embedding')
            if vec and isinstance(vec, (list, np.ndarray)) and len(vec) > 0:
                cand_vectors.append(np.array(vec, dtype=np.float32))
                valid_candidates.append(c)

        if not valid_candidates:
            return candidates[:top_k]

        try:
            import mlx.core as mx
            q = mx.array(query_emb, dtype=mx.float32).reshape(1, -1)
            c_mx = mx.array(np.stack(cand_vectors).astype(np.float32))
            scores = _cosine_sim_batch(q, c_mx)
            scores_np = np.array(scores.squeeze(0))
            sorted_idx = np.argsort(scores_np)[::-1][:top_k]
            return [valid_candidates[i] for i in sorted_idx]
        except Exception:
            # Fallback to numpy cosine
            q_np = np.array(query_emb, dtype=np.float32)
            q_norm = q_np / (np.linalg.norm(q_np) + 1e-8)
            results = []
            for i, vec in enumerate(cand_vectors):
                v = vec.astype(np.float32)
                v_norm = v / (np.linalg.norm(v) + 1e-8)
                score = float(np.dot(q_norm, v_norm))
                results.append((i, score))
            results.sort(key=lambda x: x[1], reverse=True)
            return [valid_candidates[i] for i, _ in results[:top_k]]

    def _pack_query_to_binary(self, query_vec: list[float]) -> bytes:
        """Pack float32 query vector to packed binary bytes.

        Matches the big-endian packing used in _load_embeddings_to_mlx:
          signs = (emb > 0).astype(uint8)   -- 1 if >= 0, 0 if < 0
          packed[i] = bits[i*8]<<7 | bits[i*8+1]<<6 | ... | bits[i*8+7]<<0

        Returns:
            Packed bytes (num_bytes = (dim + 7) // 8).
        """
        import numpy as np
        arr = np.asarray(query_vec, dtype=np.float32)
        bits = (arr >= 0).astype(np.uint8)
        dim = len(bits)
        num_bytes = (dim + 7) // 8
        padded = np.zeros(num_bytes * 8, dtype=np.uint8)
        padded[:dim] = bits
        packed = np.zeros(num_bytes, dtype=np.uint8)
        for i in range(8):
            packed |= padded[i::8] << 7 - i
        return packed.tobytes()

    async def _binary_prefilter(self, query_emb: list[float], candidates: list[dict], count: int=500) -> list[dict]:
        """Fast pre-filter using binary embeddings (Hamming distance).

        Tier 0: Rust SIMD hamming (batch_hamming_scores) — correct bit-level popcount
        Tier 1: MLX fallback with popcount lookup table — correct bit-level popcount

        BUG FIX (vs dead-code path):
          - Old: checked _binary_embeddings (always None) → early return
          - Old: mx.sum(xor_res, axis=1) summed bytes, not bits — WRONG
          - Now: operates on _mlx_embeddings (always populated when binary path runs)
          - Now: uses popcount (Rust or MLX lookup) for correct bit-level Hamming
        """
        if self._mlx_embeddings is None or len(candidates) == 0:
            return candidates
        cand_indices = []
        valid_candidates = []
        for c in candidates:
            idx = self._mlx_id_to_idx.get(c.get('id'))
            if idx is not None:
                cand_indices.append(idx)
                valid_candidates.append(c)
        if not valid_candidates:
            return candidates
        num_bytes = self._mlx_embeddings.shape[1]
        try:
            from hledac.universal.compat.core_simd_similarity import batch_hamming_scores as _bhs
            query_packed = self._pack_query_to_binary(query_emb)
            candidates_flat = self._mlx_embeddings[cand_indices].tolist()
            all_bytes = b''.join((bytes((int(x) for x in row)) for row in candidates_flat))
            scores: list[float] = _bhs(query_packed, all_bytes, len(cand_indices), num_bytes)
            # MRL-2 FIX: Sort DESCENDING (highest similarity first) to match MLX path
            # Previous bug: ascending sort took least similar instead of most similar
            sorted_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:count]
            return [valid_candidates[i] for i in sorted_idx]
        except Exception:  # noqa: BLE001
            pass
        try:
            import mlx.core as mx
            q_packed = self._pack_query_to_binary(query_emb)
            q_mx = mx.array(list(q_packed), dtype=mx.uint8)
            d_mx = self._mlx_embeddings[cand_indices]
            xor_res = mx.bitwise_xor(q_mx, d_mx)
            _POPCOUNT = mx.array([bin(i).count('1') for i in range(256)], dtype=mx.float32)
            bits_set = _POPCOUNT[xor_res.astype(mx.uint8)]
            total_bits = float(num_bytes * 8)
            hamming_dist = mx.sum(bits_set, axis=1)
            scores = 1.0 - hamming_dist / total_bits
            scores_np = np.array(scores)
            top_indices = np.argsort(scores_np)[::-1][:count]
            return [valid_candidates[i] for i in top_indices]
        except Exception as e:
            logger.debug(f'Binary prefilter failed: {e}')
            return candidates

    def _mmr(self, candidates: list[dict], query_emb: list[float], lambda_param: float=0.5, top_k: int=30) -> list[dict]:
        """Maximal Marginal Relevance - reduce duplicates in results."""
        if len(candidates) <= top_k:
            return candidates
        selected = []
        remaining = candidates.copy()
        query_emb_np = np.array(query_emb)
        while len(selected) < top_k and remaining:
            mmr_scores = []
            for doc in remaining:
                # MRL-2 FIX: Column name is 'embedding' not '_embedding'
                doc_emb = np.array(doc.get('embedding', doc.get('_embedding', [0] * len(query_emb))))
                sim_to_query = np.dot(query_emb_np, doc_emb) / (np.linalg.norm(query_emb_np) * np.linalg.norm(doc_emb) + 1e-08)
                max_sim_to_selected = 0
                if selected:
                    # MRL-2 FIX: Column name is 'embedding' not '_embedding'
                    selected_embs = np.array([s.get('embedding', s.get('_embedding', [0] * len(query_emb))) for s in selected])
                    sims = np.dot(selected_embs, doc_emb) / (np.linalg.norm(selected_embs, axis=1) * np.linalg.norm(doc_emb) + 1e-08)
                    max_sim_to_selected = np.max(sims) if sims.size > 0 else 0
                mmr = lambda_param * sim_to_query - (1 - lambda_param) * max_sim_to_selected
                mmr_scores.append(mmr)
            best_idx = np.argmax(mmr_scores)
            selected.append(remaining.pop(best_idx))
        return selected

    async def _ensure_usearch_index(self) -> None:
        """Lazy load usearch index (experimental)."""
        if self._usearch_loaded or self._table is None:
            return
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / 1024 ** 3
            if available_gb < 4.0:
                logger.warning(f'[INDEX] M1 memory pressure ({available_gb:.1f}GB available), skipping usearch index build')
                self._usearch_loaded = True
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            from usearch.index import Index
            if self._table.count_rows() < 1000:
                self._usearch_loaded = True
                return
            # MRL-2 FIX: Column name is 'embedding' not '_embedding' (table schema uses 'embedding')
            data = self._table.to_lance().to_table(columns=['embedding', 'id']).to_pydict()
            if len(data.get('embedding', [])) == 0:
                return
            self._usearch_index = Index(ndim=self._embedding_dim, metric='cos', dtype='f32', connectivity=16, expansion_add=128, expansion_search=64)
            for i, emb in enumerate(data['embedding'][:10000]):
                self._usearch_index.add(i, np.array(emb, dtype=np.float32))
            logger.info(f"usearch index loaded with {len(data['embedding'][:10000])} vectors")
        except Exception as e:
            logger.warning(f'usearch unavailable: {e}')
            self._usearch_index = None
        self._usearch_loaded = True

    async def _usearch_search(self, query_emb: list[float], count: int=200) -> list[dict]:
        """Search using usearch (if available)."""
        if self._usearch_index is None:
            return []
        try:
            matches = self._usearch_index.search(np.array(query_emb, dtype=np.float32), count)
            ids = matches.keys if hasattr(matches, 'keys') else [m.key for m in matches]
            results = []
            for idx in ids:
                doc = self._table.get(str(idx))
                if doc:
                    results.append(doc)
            return results
        except Exception as e:
            logger.debug(f'usearch search failed: {e}')
            return []

    async def _predict_memory_pressure(self) -> float:
        """Predict memory pressure using LMDB stats."""
        if self._cache_env is None:
            return 0.0
        try:
            stat = self._cache_env.stat()
            cache_usage = stat['last_pgno'] * stat['psize']
            map_size = self._cache_env.info()['map_size']
            current_ratio = cache_usage / map_size
            self._memory_history.append(current_ratio)
            if len(self._memory_history) >= 3:
                y = np.array(list(self._memory_history))
                x = np.arange(len(y))
                slope = np.polyfit(x, y, 1)[0]
                predicted = y[-1] + slope * 3
                return min(1.0, predicted)
            return current_ratio
        except Exception:
            return 0.0

    async def _evict_if_needed(self) -> None:
        """F214OPT-C: Pre-emptive eviction when LMDB map is near full."""
        if self._cache_env is None:
            return
        try:
            info = self._cache_env.info()
            map_size = info['map_size']
            stat = self._cache_env.stat()
            cache_usage = stat['last_pgno'] * stat['psize']
            ratio = cache_usage / map_size
            if ratio < self._EVICTION_THRESHOLD_RATIO:
                return
            logger.info(f'[LANCEDB_CACHE] Eviction triggered: ratio={ratio:.2f}, map_size={map_size}')
            evicted = 0

            def _scan_and_evict():
                nonlocal evicted
                try:
                    with self._cache_env.begin() as txn:
                        cursor = txn.cursor()
                        entries = []
                        for key, value in cursor:
                            try:
                                data = _msgspec_loads(value)
                                entries.append((key, data))
                            except Exception:  # noqa: BLE001
                                pass
                    if not entries:
                        return
                    entries.sort(key=lambda x: (x[1].get('access_count', 0), x[1].get('last_access', 0)))
                    evict_count = max(1, len(entries) // 10)
                    to_evict = entries[:evict_count]
                    with self._cache_env.begin(write=True) as txn:
                        for key, _ in to_evict:
                            txn.delete(key)
                            evicted += 1
                except Exception as e:
                    logger.debug(f'[LANCEDB_CACHE] Eviction scan failed: {e}')
            await asyncio.to_thread(_scan_and_evict)
            if evicted > 0:
                self._metrics['cache_evictions'] = self._metrics.get('cache_evictions', 0) + evicted
                logger.info(f'[LANCEDB_CACHE] Evicted {evicted} entries')
        except Exception as e:
            logger.debug(f'[LANCEDB_CACHE] Eviction error: {e}')

    async def _get_colbert_reranker(self):
        """Lazy load ColBERT."""
        if self._colbert_loaded:
            return self._colbert_reranker
        try:
            from hledac.universal.knowledge.colbert_retriever import ColBERTReranker
            self._colbert_reranker = ColBERTReranker()
            self._colbert_loaded = True
            logger.info('ColBERT reranker loaded')
            return self._colbert_reranker
        except Exception as e:
            logger.warning(f'ColBERT load failed: {e}')
            return None

    async def _get_flashrank_ranker(self):
        """Lazy load FlashRank for retrieval path.

        Canonical owner: tools/reranker.py
        This is a compatibility wrapper serving the retrieval context only.
        Uses ms-marco-MiniLM-L-12-v2 model (same as canonical).
        """
        if self._flashrank_loaded:
            return self._flashrank_ranker
        try:
            from flashrank import Ranker
            self._flashrank_ranker = Ranker(model_name='ms-marco-MiniLM-L-12-v2')
            self._flashrank_loaded = True
            logger.info('FlashRank loaded')
            return self._flashrank_ranker
        except Exception as e:
            logger.warning(f'FlashRank load failed: {e}')
            return None

    def _validate_table_schema(self, expected_dim: int) -> bool:
        """Validate existing LanceDB table schema matches expected embedding dimension.

        ISSUE-P6-008: exist_ok=True silently ignores schema drift (dimension mismatch).
        This method explicitly validates that the existing table's embedding field
        matches self._embedding_dim before we open it. If dimensions don't match,
        we raise instead of silently corrupting ANN search results.

        Args:
            expected_dim: Expected embedding dimension (from self._embedding_dim).

        Returns:
            True if schema is valid or table doesn't exist yet.
            Raises RuntimeError if schema drift is detected.
        """
        try:
            existing_table = self.db.open_table('entities')
            lance_schema = existing_table.schema
            emb_field = next(
                (f for f in lance_schema.fields if f.name == 'embedding'),
                None
            )
            if emb_field is None:
                logger.warning('[LANCEDB] embedding field not found in existing table schema')
                return True
            actual_list_size = getattr(emb_field.type, 'list_size', None)
            if actual_list_size != expected_dim:
                logger.error(
                    f'[LANCEDB] Schema mismatch: expected dim={expected_dim}, '
                    f'existing table has dim={actual_list_size}. '
                    f'Run reembed_at_new_dimension() or delete the table at {self.uri}'
                )
                raise RuntimeError(
                    f'LanceDB schema drift detected: expected embedding dim={expected_dim}, '
                    f'existing table has dim={actual_list_size}. '
                    f'Delete the table or call reembed_at_new_dimension().'
                )
            return True
        except Exception as e:
            if 'TableNotFound' in type(e).__name__ or 'not exist' in str(e).lower():
                return True
            raise

    def _initialize(self) -> None:
        """Initialize database and table (lazy — lancedb is opt-in via [ml] extra).

        ISSUE-P6-008 FIX: Replaced create_table(..., exist_ok=True) with
        open_table + _validate_table_schema() to detect schema drift before
        dimension mismatch corrupts ANN search results.
        """
        try:
            import lancedb
            import pyarrow as pa
            Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
            self.db = lancedb.connect(self.uri)
            # ISSUE-P6-008: validate schema — raises RuntimeError on dimension mismatch
            self._validate_table_schema(self._embedding_dim)
            # Table validated, safe to open (no redundant create_table API call)
            self._table = self.db.open_table('entities')
            try:
                list_indices_fn = getattr(self._table, 'list_indices', None)
                existing_indices = list_indices_fn() if callable(list_indices_fn) else []
                if not any((getattr(idx, 'name', '') == 'aliases_idx' for idx in existing_indices)):
                    self._table.create_fts_index('aliases', replace=False, with_position=True, tokenizer_name='en_stem')
                self._lancedb_has_fts = True
                logger.info('[LANCEDB:H] FTS index available — hybrid search enabled')
            except Exception as e:
                self._lancedb_has_fts = False
                logger.debug('[LANCEDB:H] FTS index not available: %s', e)
            logger.info(f'LanceDB identity store initialized at {self.uri}')
            self._log_table_opened()
        except ImportError:
            logger.warning('LanceDB not available, identity store disabled')
            self.db = None
        except Exception as e:
            logger.warning(f'Failed to initialize LanceDB: {e}')
            self.db = None

    def _log_table_opened(self) -> None:
        """Sprint F264D: Log 'lancedb.table_opened' event with size_mb.

        M1 observability — measures table footprint for IVF-PQ benefit verification.
        Estimated: rows × embedding_dim × 4 bytes (float32) + PyArrow overhead.
        """
        try:
            if self._table is None:
                return
            row_count = self._table.count_rows()
            size_bytes = row_count * self._embedding_dim * 4 + 8192
            size_mb = size_bytes / (1024 * 1024)
            logger.info(f'[LANCEDB] lancedb.table_opened table=entities rows={row_count} size_mb={size_mb:.2f} uri={self.uri}')
        except Exception as e:
            logger.debug(f'[LANCEDB] lancedb.table_opened log failed: {e}')

    async def _ensure_ivf_pq_index_async(self) -> None:
        """Sprint F264D: Lazy IVF-PQ training (M1 8GB friendly, fail-soft).

        Called from add_entity/search_similar on first invocation. Gated by
        HLEDAC_LANCEDB_QUANTIZE=1. Skipped if table has < 256 rows (insufficient
        training data — IVF-PQ on small data degrades recall). Errors are logged
        + ignored → falls back to brute-force cosine. Double-checked locking
        prevents concurrent training on first parallel query burst.

        NOTE: Uses ``getattr`` for flags so the helper is safe under ``__new__``
        test-mock paths that bypass ``__init__``.
        """
        if not getattr(self, '_ivfpq_enabled', False):
            return
        if self._table is None or getattr(self, '_ivfpq_trained', False):
            return
        if not hasattr(self, '_ivfpq_lock'):
            self._ivfpq_lock = asyncio.Lock()
        async with self._ivfpq_lock:
            if self._ivfpq_trained:
                return
            try:
                row_count = self._table.count_rows()
                if row_count < 256:
                    logger.debug(f'[LANCEDB] IVF-PQ skipped: only {row_count} rows (need >= 256 for meaningful PQ training)')
                    self._ivfpq_trained = True
                    return
                loop = asyncio.get_running_loop()
                num_partitions = getattr(self, '_ivfpq_num_partitions', 64)
                num_sub_vectors = getattr(self, '_ivfpq_num_sub_vectors', 12)

                def _train() -> None:
                    self._table.create_index(metric='cosine', index_type='IVF_PQ', num_partitions=num_partitions, num_sub_vectors=num_sub_vectors, max_iterations=20)
                await asyncio.to_thread(_train)
                self._ivfpq_trained = True
                logger.info(f'[LANCEDB] IVF-PQ trained: table=entities rows={row_count} num_partitions={num_partitions} num_sub_vectors={num_sub_vectors}')
            except Exception as e:
                self._ivfpq_trained = True
                logger.warning(f'[LANCEDB] IVF-PQ training failed (fallback brute-force): {e}')

    async def add_entity(self, entity_id: str, embedding: list[float], aliases: list[str]) -> bool:
        """
        Add entity to identity store.

        Args:
            entity_id: Unique entity identifier.
            embedding: Vector embedding for semantic similarity.
            aliases: List of aliases/alternate names.

        Returns:
            True if added successfully, False otherwise.
        """
        if self._table is None:
            return False
        await self._ensure_ivf_pq_index_async()
        try:
            now = datetime.now(UTC)
            data = [{'id': entity_id, 'embedding': embedding, 'aliases': aliases, 'first_seen': now, 'last_seen': now}]
            # M5 multi-writer: register table and use per-table queue
            table_name = 'entities'
            _register_table(table_name, self._table)
            await _ensure_write_workers()
            queue = await _get_table_queue(table_name)
            deadline = time.monotonic() + 60.0
            try:
                # C3-05 FIX: bounded put with backpressure.
                # asyncio.Queue.put() is a coroutine in Python 3.10+ — awaiting
                # applies backpressure instead of silent drop via put_nowait().
                # safe_wait_for timeout enforces the caller's deadline.
                await safe_wait_for(queue.put((data, deadline)), timeout=max(0.0, deadline - time.monotonic()), label='lancedb_queue_put')
            except asyncio.TimeoutError:
                # C3-05: deadline expired — signal backpressure to caller.
                # Do NOT silently drop; let caller handle the backpressure signal.
                logger.warning('[LANCEDB:QW] Queue full, write dropped (deadline expired)')
                return False
            tuner = getattr(self, '_autotune', None)
            if tuner is not None and getattr(self, '_ivfpq_enabled', False):
                try:
                    result = await tuner.tune_if_due_async(self._table, current_num_partitions=self._ivfpq_num_partitions, current_num_sub_vectors=self._ivfpq_num_sub_vectors, inserts_delta=1)
                    if result.changed():
                        self._ivfpq_num_partitions = result.new_partitions
                        self._ivfpq_num_sub_vectors = result.new_num_sub_vectors
                        logger.info(f'[LANCEDB] auto-tune adjusted num_partitions={result.old_partitions}->{result.new_partitions} num_sub_vectors={result.old_num_sub_vectors}->{result.new_num_sub_vectors} recall={result.recall:.3f} avg_ms={result.avg_search_ms:.2f}')
                        await self._maybe_compact_async()
                except Exception:  # noqa: BLE001
                    pass
            return True
        except Exception as e:
            logger.warning(f'Failed to add entity: {e}')
            return False

    async def _maybe_compact_async(self) -> None:
        """Non-blocking compaction trigger; actual work in executor."""
        if self._compact_in_flight:
            return
        if self._table is None:
            return
        now = time.time()
        count_due = self._insert_count_since_compact >= self._COMPACT_FRAGMENT_THRESHOLD
        time_due = now - self._last_compact_ts >= self._COMPACT_TIME_THRESHOLD_S
        if not (count_due or time_due):
            return
        if now - self._last_compact_ts < self._COMPACT_MIN_INTERVAL_S:
            return
        self._compact_in_flight = True
        try:
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(self._maybe_compact_blocking)
        except Exception as e:
            try:
                self._metrics['compaction_failures'] += 1
            except Exception:  # noqa: BLE001
                pass
            logger.debug(f'[LANCEDB] compact dispatch failed: {e}')
        finally:
            self._compact_in_flight = False

    def _maybe_compact_blocking(self) -> None:
        """Run lancedb optimize/compact_files in calling thread. Fail-soft.

        LanceDB >= 0.4 API: Table.optimize() returns OptimizeResult.
        Older API used compact_files(). Try optimize() first, then
        compact_files(), else no-op. Never raises.
        """
        if self._table is None:
            return
        try:
            if hasattr(self._table, 'optimize'):
                self._table.optimize()
            elif hasattr(self._table, 'compact_files'):
                self._table.compact_files()
            else:
                return
            self._insert_count_since_compact = 0
            self._last_compact_ts = time.time()
            try:
                self._metrics['compaction_runs'] += 1
                self._metrics['last_compaction_ts'] = self._last_compact_ts
            except Exception:  # noqa: BLE001
                pass
            logger.debug('[LANCEDB] compact ok (reset, ts=%d)', int(self._last_compact_ts))
        except Exception as e:
            try:
                self._metrics['compaction_failures'] += 1
            except Exception:  # noqa: BLE001
                pass
            logger.debug(f'[LANCEDB] compact failed (fail-soft): {e}')

    async def search_similar(self, embedding: list[float], text_hint: str='', threshold: float=0.85, limit: int=20, query_type: str='auto') -> list[dict[str, Any]]:
        """
        Search for similar entities.

        Args:
            embedding: Query embedding.
            text_hint: Optional text query for FTS.
            threshold: Similarity threshold (0-1). Applied only for pure vector
                results; bypassed for RRF reranked hybrid (RRF is the final ranking).
            limit: Maximum results to return.
            query_type: Search mode — "auto" delegates to _detect_query_type(),
                or explicit "vector"/"fts"/"hybrid". Default "auto".
                AREA H+: 2026 cutting-edge — when "hybrid" + FTS available, applies
                native RRFReranker (LanceDB 0.8+) for 15-30% better OSINT recall.

        Returns:
            List of matching entities with similarity scores.
        """
        if self._table is None:
            return []
        await self._ensure_ivf_pq_index_async()
        try:
            import polars as pl
            effective_qt = query_type
            if effective_qt == 'auto':
                effective_qt = await self._detect_query_type(text_hint or '')
            _qt = effective_qt
            _emb = embedding
            _txt = text_hint
            _lim = limit
            loop = asyncio.get_running_loop()

            def _search():
                if _qt == 'hybrid' and _txt and self._lancedb_has_fts:
                    reranker = _get_rrf_reranker()
                    builder = self._table.search(query_type='hybrid', vector_column_name='embedding').vector(_emb).text(_txt).limit(_lim)
                    if reranker is not None:
                        builder = builder.rerank(reranker=reranker)
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        builder = builder.nprobes(_nprobes)
                    except TypeError:  # noqa: BLE001
                        pass  # Fallback for LanceDB versions without nprobes
                    return builder.to_polars()
                elif _qt == 'fts' and _txt and self._lancedb_has_fts:
                    return self._table.search(_txt, query_type='fts').limit(_lim).to_polars()
                elif _txt and (not self._lancedb_has_fts):
                    logger.debug('[LANCEDB:H] text_hint=%r ignored — FTS not supported in local LanceDB', str(_txt)[:50])
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        return self._table.search(_emb, vector_column_name='embedding').nprobes(_nprobes).limit(_lim).to_polars()
                    except TypeError:
                        return self._table.search(_emb, vector_column_name='embedding').limit(_lim).to_polars()
                else:
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        return self._table.search(_emb, vector_column_name='embedding').nprobes(_nprobes).limit(_lim).to_polars()
                    except TypeError:
                        return self._table.search(_emb, vector_column_name='embedding').limit(_lim).to_polars()
            df = await asyncio.to_thread(_search)
            if '_relevance_score' in df.columns:
                df = df.with_columns(pl.col('_relevance_score').alias('similarity'))
            elif '_distance' in df.columns:
                df = df.with_columns((1 - pl.col('_distance')).alias('similarity')).filter(pl.col('similarity') >= threshold)
            results = []
            for row in df.iter_rows(named=True):
                results.append({'id': row.get('id', ''), 'aliases': row.get('aliases', []), 'similarity': row.get('similarity', 0.0), 'first_seen': row.get('first_seen'), 'last_seen': row.get('last_seen')})
            return results[:limit]
        except Exception as e:
            logger.warning(f'Search failed: {e}')
            return []

    async def compute_similarity(self, emb1: list[float], emb2: list[float]) -> float:
        """
        Compute cosine similarity between two embeddings.

        Args:
            emb1: First embedding.
            emb2: Second embedding.

        Returns:
            Cosine similarity score (0-1).
        """
        try:
            if MLX_AVAILABLE:
                a = mx.array([emb1])
                b = mx.array([emb2])
                result = _cosine_sim_batch(a, b)
                return float(result[0, 0])
            else:
                a = np.array(emb1)
                b = np.array(emb2)
                a_n = a / np.linalg.norm(a)
                b_n = b / np.linalg.norm(b)
                return float(np.dot(a_n, b_n))
        except Exception as e:
            logger.warning(f'Similarity computation failed: {e}')
            return 0.0

    async def reembed_all(self) -> dict:
        """One-shot re-embed admin operation. NOT a per-sprint hot path.

        F265X: migrated to polars native path. Uses self._table.to_polars()
        to skip the intermediate Arrow allocation that pl.from_arrow(.to_arrow())
        required. Falls back to .to_pandas() on polars ImportError or if
        .to_polars() itself fails. Polars 1.x + LanceDB ≥0.9.
        """
        "\n        Re-embed all stored entities at new MRL dimension (256d).\n\n        Lazy migration: only run when explicitly called.\n        Use case: existing768d embeddings need re-embedding after dimension change.\n\n        Returns:\n            dict with 'reembedded' count, 'failed' count, 'skipped' count.\n        "
        try:
            import polars as pl
            use_polars = True
        except ImportError:
            use_polars = False
        logger.info('[REEMBED] Starting re-embedding at 256d dimension')
        stats = {'reembedded': 0, 'failed': 0, 'skipped': 0}
        if self._table is None:
            logger.warning('[REEMBED] No table available')
            return stats
        try:
            if use_polars:
                try:
                    all_data = self._table.to_polars()
                    total = all_data.height
                    if total == 0:
                        logger.info('[REEMBED] No entities to re-embed')
                        return stats
                except Exception:
                    use_polars = False
            if not use_polars:
                all_data = self._table.to_pandas()
                total = len(all_data)
                if all_data.empty:
                    logger.info('[REEMBED] No entities to re-embed')
                    return stats
            logger.info(f'[REEMBED] Found {total} entities to re-embed')
            batch_size = 16
            for i in range(0, total, batch_size):
                if use_polars:
                    batch = all_data.slice(i, batch_size)
                    rows = list(batch.iter_rows(named=True))
                else:
                    batch = all_data.iloc[i:i + batch_size]
                    rows = [row.to_dict() for _, row in batch.iterrows()]
                batch_len = len(rows)
                texts = [r.get('text') or r.get('content') or str(r.get('id', '')) for r in rows]
                try:
                    embeddings = await self._embed_batch(texts, batch_size=batch_size)
                    for idx, r in enumerate(rows):
                        entity_id = r['id']
                        if idx < len(embeddings) and embeddings[idx]:
                            self._table.merge_insert('id').on('id').execute([{'id': entity_id, 'embedding': embeddings[idx]}])
                            stats['reembedded'] += 1
                        else:
                            stats['skipped'] += 1
                except Exception as e:
                    logger.warning(f'[REEMBED] Batch failed: {e}')
                    stats['failed'] += batch_len
            logger.info(f'[REEMBED] Complete: {stats}')
        except Exception as e:
            logger.error(f'[REEMBED] Failed: {e}')
        return stats

    async def close(self) -> None:
        """Close database connection and cache."""
        self._writeback_buffer.clear()
        if self._mlx_embeddings is not None:
            del self._mlx_embeddings
            self._mlx_embeddings = None
        if self._binary_embeddings is not None:
            del self._binary_embeddings
            self._binary_embeddings = None
        self._mlx_ids = None
        self._mlx_id_to_idx = {}
        self._mlx_embeddings_total_count = 0
        try:
            import mlx.core as mx
            mx.eval([])
            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        if self.db is not None:
            try:
                self.db.close()
            except Exception:  # noqa: BLE001
                pass
        if self._cache_env is not None:
            try:
                self._cache_env.close()
            except Exception:  # noqa: BLE001
                pass

    async def search_similar_adaptive(self, query_text: str, query_emb: list[float], top_k: int=10) -> list[dict]:
        """
        Hybrid search with adaptive reranking and MMR (Sprint 76).

        Args:
            query_text: Original query text for reranking.
            query_emb: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of ranked documents.
        """
        ctx = {'thermal': 'NORMAL', 'on_battery': False, 'available_gb': 8.0}
        try:
            if self._orch and hasattr(self._orch, '_memory_mgr') and self._orch._memory_mgr:
                # ISSUE-P3-02: get_reranking_context() calls _on_battery_power() which uses
                # subprocess.run() - must run in thread pool to avoid blocking event loop
                ctx = await asyncio.to_thread(self._orch._memory_mgr.get_reranking_context)
        except Exception:  # noqa: BLE001
            pass
        thermal = ctx.get('thermal', 'NORMAL')
        on_battery = ctx.get('on_battery', False)
        available_gb = ctx.get('available_gb', 8.0)
        try:
            candidates = await self.search_similar(query_emb, text_hint=query_text or '', limit=200, query_type='auto', threshold=0.0)
        except Exception:
            if self._usearch_index is not None:
                candidates = await self._usearch_search(query_emb, count=200)
            else:
                candidates = []
        if not candidates:
            return []
        if len(candidates) > 100:
            candidates = await self._binary_prefilter(query_emb, candidates, count=self._BINARY_FILTER_COUNT)
        candidates = self._mmr(candidates, query_emb, top_k=min(self._MMR_TOP_K, len(candidates)))
        scores = [c.get('similarity', 0.5) for c in candidates]
        if scores:
            mean_score = sum(scores) / len(scores)
            variance = sum(((s - mean_score) ** 2 for s in scores)) / len(scores)
            if variance < 0.1:
                return candidates[:top_k]
        if available_gb > 4.0 and thermal not in ('HOT', 'CRITICAL') and (not on_battery):
            reranker = await self._get_colbert_reranker()
            if reranker:
                return await reranker.rerank(query_text, candidates, top_k)
        if available_gb > 2.0:
            reranker = await self._get_flashrank_ranker()
            if reranker:
                try:
                    from flashrank import RerankRequest
                    passages = [{'id': i, 'text': c.get('text', '')} for i, c in enumerate(candidates[:50])]
                    request = RerankRequest(query=query_text, passages=passages)
                    results = reranker.rerank(request)
                    return [candidates[r['id']] for r in results[:top_k]]
                except Exception:  # noqa: BLE001
                    pass
        return await self._mlx_rerank(query_emb, candidates, top_k)

    async def search_with_mmr(self, query_text: str, query_emb: list[float], top_k: int=10, lambda_mult: float=0.5, fetch_k: int=30) -> list[dict]:
        """
        Diversity-aware search using Maximal Marginal Relevance from context_optimization.

        Args:
            query_text: Original query text for reranking.
            query_emb: Query embedding vector.
            top_k: Number of results to return.
            lambda_mult: Balance relevance (1.0) vs diversity (0.0). Default 0.5.
            fetch_k: Number of candidates to fetch before reranking.

        Returns:
            List of diverse, relevant documents.
        """
        try:
            candidates = await self.search_similar(query_emb, text_hint=query_text or '', limit=fetch_k, query_type='auto', threshold=0.0)
        except Exception:
            if self._usearch_index is not None:
                candidates = await self._usearch_search(query_emb, count=fetch_k)
            else:
                candidates = []
        if not candidates:
            return []
        candidate_embs: list[np.ndarray] = []
        for c in candidates:
            # MRL-2 FIX: Primary key is 'embedding', fallback to '_embedding' for backward compat
            emb = c.get('embedding') or c.get('_embedding')
            if emb is not None:
                candidate_embs.append(np.array(emb, dtype='float32'))
            else:
                candidate_embs.append(np.zeros(len(query_emb), dtype='float32'))
        if not candidate_embs:
            return candidates[:top_k]
        query_emb_np = np.array(query_emb, dtype='float32')
        if query_emb_np.ndim == 1:
            query_emb_np = query_emb_np.reshape(1, -1)
        selected_indices = maximal_marginal_relevance(query_vector=query_emb_np, candidate_vectors=candidate_embs, top_k=top_k, lambda_param=lambda_mult)
        return [candidates[i] for i in selected_indices]

class SqliteVecIdentityStore:
    """
    M1-native entity identity store using sqlite-vec.

    ROLE: Identity/Entity Store — replaces LanceDBIdentityStore on M1 8GB.
    Provides add_entity() + search_similar() for entity stitching
    and entity-aware RAG fallback.

    Key differences from LanceDBIdentityStore:
    - Zero-process: no LanceDB subprocess (~200MB RAM saved)
    - In-process ANN via sqlite-vec SQLite extension (~5MB overhead)
    - Shared db path: uses same sprint_{id}.db as DuckDBShadowStore
    - No IVF-PQ auto-tune (not available in sqlite-vec)
    - No binary signature pre-filter (sqlite-vec limitation)

    API contract matches LanceDBIdentityStore:
        - add_entity(entity_id, embedding, aliases) -> bool
        - search_similar(embedding, text_hint, threshold, limit, query_type) -> list[dict]
        - search_similar_adaptive(query_text, query_emb, top_k) -> list[dict]
        - _embed_single(text) -> list[float] (internal)
    """
    MAX_DIM = 384
    __slots__ = tuple(('_embedding_manager', '_orch', '_sprint_id', '_store'))

    def __init__(self, uri: str='default', orchestrator=None) -> None:
        self._sprint_id = uri if uri else 'default'
        self._orch = orchestrator
        self._store: Any = None
        self._embedding_manager: Any = None

    async def _ensure_store(self) -> bool:
        """Lazily initialize SqliteVecStore."""
        if self._store is not None:
            return True
        try:
            from hledac.universal.utils.sqlite_vec_helpers import SqliteVecStore
            store = SqliteVecStore(sprint_id=self._sprint_id)
            ok = await store.initialize()
            if ok:
                self._store = store
                return True
            return False
        except Exception:
            return False

    async def _get_embedding_manager(self):
        """Lazily get MLX embedding manager."""
        if self._embedding_manager is None:
            from hledac.universal.core.mlx_embeddings import get_embedding_manager
            self._embedding_manager = get_embedding_manager()
        return self._embedding_manager

    async def _embed_single(self, text: str) -> list[float] | None:
        """Embed text via MLX."""
        try:
            mgr = await self._get_embedding_manager()
            emb = mgr.embed_query(text)
            return emb.tolist() if hasattr(emb, 'tolist') else list(emb)
        except Exception:
            return None

    async def add_entity(self, entity_id: str, embedding: list[float], aliases: list[str]) -> bool:
        """Add entity to identity store. API-compatible with LanceDBIdentityStore."""
        if not await self._ensure_store():
            return False
        try:
            return await self._store.upsert_entity(entity_id=entity_id, embedding=embedding, aliases=aliases)
        except Exception:
            return False

    async def search_similar(self, embedding: list[float], text_hint: str='', threshold: float=0.85, limit: int=20, query_type: str='auto') -> list[dict[str, Any]]:
        """
        ANN search for similar entities. API-compatible with LanceDBIdentityStore.

        Args:
            embedding: Query embedding vector.
            text_hint: Optional text for FTS (ignored — sqlite-vec has no FTS).
            threshold: Similarity threshold (0-1). Applied as 1 - vec0_distance.
            limit: Maximum results.
            query_type: "auto"/"vector"/"fts"/"hybrid" (fts/hybrid fall back to vector).

        Returns:
            List of matching entities with id, aliases, similarity, first_seen, last_seen.
        """
        if not await self._ensure_store():
            return []
        try:
            results = await self._store.search_entities(query_embedding=embedding, top_k=limit, threshold=1.0 - threshold)
            now = datetime.now(UTC)
            return [{'id': r.get('id') or r.get('item_id', ''), 'aliases': (r.get('metadata') or {}).get('aliases', []), 'similarity': r.get('similarity', 1.0 - r.get('distance', 1.0)), 'first_seen': now, 'last_seen': now} for r in results]
        except Exception:
            return []

    async def search_similar_adaptive(self, query_text: str, query_emb: list[float], top_k: int=10) -> list[dict[str, Any]]:
        """
        Hybrid search with adaptive reranking. API-compatible with LanceDBIdentityStore.

        sqlite-vec limitation: no native FTS, no FlashRank/ColBERT reranker.
        Falls back to pure ANN search with MLX cosine similarity fallback.

        Args:
            query_text: Query text (used for reranking context if available).
            query_emb: Query embedding vector.
            top_k: Number of results.

        Returns:
            List of ranked entity dicts.
        """
        if not await self._ensure_store():
            return []
        try:
            results = await self._store.search_entities(query_embedding=query_emb, top_k=min(top_k * 2, 100), threshold=0.0)
        except Exception:
            return []
        if not results:
            return []
        now = datetime.now(UTC)
        entities = [{'id': r.get('id') or r.get('item_id', ''), 'aliases': (r.get('metadata') or {}).get('aliases', []), 'similarity': r.get('similarity', 1.0 - r.get('distance', 1.0)), 'first_seen': now, 'last_seen': now, 'text': (r.get('metadata') or {}).get('aliases', [])} for r in results]
        try:
            from context_optimization.mmr import maximal_marginal_relevance
            import numpy as np
            query_emb_np = np.array(query_emb, dtype='float32')
            if query_emb_np.ndim == 1:
                query_emb_np = query_emb_np.reshape(1, -1)
            entity_vecs = [np.array([e.get('embedding', []) if isinstance(e.get('embedding'), list) else []], dtype='float32') for e in entities]
            selected_indices = maximal_marginal_relevance(query_vector=query_emb_np, candidate_vectors=entity_vecs, top_k=top_k, lambda_param=0.5)
            entities = [entities[i] for i in selected_indices]
        except Exception:  # noqa: BLE001
            pass
        return entities[:top_k]

    async def initialize(self) -> bool:
        """Explicit init (optional). Stores are lazy inited on first use."""
        return await self._ensure_store()
_identity_store: LanceDBIdentityStore | SqliteVecIdentityStore | None = None
_identity_store_lock = LazyAsyncioLock()

async def get_identity_store() -> LanceDBIdentityStore | SqliteVecIdentityStore:
    """Get or create the singleton identity store (async-safe).

    Phase 11.2: Primary is now SqliteVecIdentityStore (zero-process, M1-native).
    LanceDBIdentityStore is kept as a wired-but-dormant fallback for cases
    where sqlite-vec is unavailable (e.g., CI without sqlite-vec extension).

    To force LanceDB explicitly:
        store = LanceDBIdentityStore()
    """
    global _identity_store
    if _identity_store is None:
        async with _identity_store_lock:
            if _identity_store is None:
                _identity_store = SqliteVecIdentityStore()
                ok = await _identity_store._ensure_store()
                if not ok:
                    _identity_store = LanceDBIdentityStore()
                    logger.info('[IdentityStore] sqlite-vec unavailable, falling back to LanceDB')
    return _identity_store

class AcademicPaper:
    """Academic paper with metadata for LanceDB storage."""
    TABLE_NAME = 'academic_papers'
    __slots__ = ('abstract', 'authors', 'citation_count', 'doi', 'embedding', 'paper_id', 'source', 'title', 'url', 'year')

    def __init__(self, paper_id: str, title: str, abstract: str='', authors: list[str] | None=None, year: int | None=None, source: str='', doi: str='', url: str='', citation_count: int=0, embedding: list[float] | None=None) -> None:
        self.paper_id = paper_id
        self.title = title
        self.abstract = abstract
        self.authors = authors or []
        self.year = year
        self.source = source
        self.doi = doi
        self.url = url
        self.citation_count = citation_count
        self.embedding = embedding

    def to_dict(self) -> dict:
        """Convert to dict for LanceDB storage."""
        return {'paper_id': self.paper_id, 'title': self.title, 'abstract': self.abstract, 'authors': self.authors, 'year': self.year, 'source': self.source, 'doi': self.doi, 'url': self.url, 'citation_count': self.citation_count, 'embedding': self.embedding or [0.0] * 384}

class LanceDBAcademicStore:
    """
    Semantic search over academic papers discovered during research.

    Sprint F259: Canonical storage for academic papers from all adapters.
    Uses FastEmbed BAAI/bge-small-en-v1.5 (384d, 33MB) for M1 memory efficiency.

    Schema:
        - paper_id: unique identifier
        - title: paper title
        - abstract: paper abstract
        - authors: list of author names
        - year: publication year
        - source: adapter source (arxiv/s2orc/openalex/core/unpaywall)
        - doi: DOI string
        - url: paper URL
        - citation_count: number of citations
        - embedding: 384d FastEmbed vector
    """
    EMBEDDING_DIM = 384
    __slots__ = tuple(('_db', '_db_path', '_dim', '_embed_model', '_embedder', '_embedder_backend', '_initialized', '_lancedb_has_fts', '_table', '_ivfpq_nprobes'))

    def __init__(self, db_path: str | None=None, dim: int=384) -> None:
        """
        Args:
            db_path: Path to LanceDB database. If None, uses default.
            dim: Embedding dimension (default 384 for FastEmbed BAAI).
        """
        import lancedb
        from hledac.universal.paths import LMDB_ROOT
        self._dim = dim
        if db_path is None:
            db_path = str(LMDB_ROOT / 'academic_papers.lance')
        self._db_path = db_path
        self._db = lancedb.connect(db_path)
        self._table = None
        self._embedder = None
        self._embedder_backend: str | None = None
        self._embed_model = 'BAAI/bge-small-en-v1.5'
        self._initialized = False
        self._lancedb_has_fts = False
        # M1 8GB IVF-PQ nprobes (same heuristic as LanceDBIdentityStore)
        self._ivfpq_nprobes: int = max(1, min(64, int(os.environ.get('HLEDAC_LANCEDB_IVFPQ_NPROBES', '8'))))

    def _validate_table_schema(self, expected_dim: int) -> bool:
        """Validate existing LanceDB table schema matches expected embedding dimension.

        ISSUE-P6-008: exist_ok=True silently ignores schema drift (dimension mismatch).
        Validates that the existing table's embedding field matches self._dim.
        Raises RuntimeError if schema drift is detected.
        """
        try:
            existing_table = self._db.open_table(AcademicPaper.TABLE_NAME)
            lance_schema = existing_table.schema
            emb_field = next(
                (f for f in lance_schema.fields if f.name == 'embedding'),
                None
            )
            if emb_field is None:
                logger.warning('[LANCEDB] embedding field not found in existing table schema')
                return True
            actual_list_size = getattr(emb_field.type, 'list_size', None)
            if actual_list_size != expected_dim:
                logger.error(
                    f'[LANCEDB] Schema mismatch: expected dim={expected_dim}, '
                    f'existing table has dim={actual_list_size}. '
                    f'Delete the table or re-embed at new dimension.'
                )
                raise RuntimeError(
                    f'AcademicPaper schema drift detected: expected embedding dim={expected_dim}, '
                    f'existing table has dim={actual_list_size}. '
                    f'Delete the table.'
                )
            return True
        except Exception as e:
            if 'TableNotFound' in type(e).__name__ or 'not exist' in str(e).lower():
                return True
            raise

    async def initialize(self) -> None:
        """Initialize table and embedder.

        ISSUE-P6-008 FIX: Added _validate_table_schema() before create_table
        to detect schema drift and prevent silent ANN corruption.
        """
        if self._initialized:
            return
        import pyarrow as pa
        # ISSUE-P6-008: validate schema — raises RuntimeError on dimension mismatch
        self._validate_table_schema(self._dim)
        # Table validated, safe to open (no redundant create_table API call)
        self._table = self._db.open_table(AcademicPaper.TABLE_NAME)
        try:
            list_indices_fn = getattr(self._table, 'list_indices', None)
            existing = list_indices_fn() if callable(list_indices_fn) else []
            existing_names = {getattr(idx, 'name', '') for idx in existing}
            if 'title_idx' not in existing_names:
                self._table.create_fts_index('title', replace=False, with_position=True, tokenizer_name='en_stem')
            if 'abstract_idx' not in existing_names:
                self._table.create_fts_index('abstract', replace=False, with_position=True, tokenizer_name='en_stem')
            self._lancedb_has_fts = True
            logger.info('[LANCEDB:H] Academic FTS indexes (title, abstract) — hybrid search enabled')
        except Exception as e:
            self._lancedb_has_fts = False
            logger.debug(f'[LANCEDB:H] Academic FTS not available: {e}')
        await self._init_embedder()
        self._initialized = True

    async def _init_embedder(self) -> None:
        """Initialize embedder via MLX-first cascade.

        Invariant: random vector fallback is FORBIDDEN — silent ANN corruption.
        Raises RuntimeError on no backend (no np.random.randn fallback).
        MLX path is tried first (M1 ANE/GPU, zero-copy UMA).
        ``self._embedder_backend`` is set in every success path.
        """
        try:
            import mlx.core
            from hledac.universal.core.mlx_embeddings import get_mlx_embedder
            self._embedder = get_mlx_embedder()
            self._embedder_backend = 'mlx'
            return
        except (ImportError, Exception):  # noqa: BLE001
            pass
        raise RuntimeError(f"""_init_embedder: MLX backend unavailable.\n  Likely cause: running via `python3` instead of `uv run python`.\n  sys.executable: {sys.executable!r}\n  Fix: use `uv run python -m hledac.universal ...`\n  Verify: `uv run python -c 'from mlx_embeddings import load; print("OK")'`""")

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via the initialized embedder backend.

        ``self._embedder`` is guaranteed non-None by ``_init_embedder``,
        which raises ``RuntimeError`` on no backend (no silent fallback).
        ``MLXEmbeddingManager`` exposes ``.encode(texts)``.
        """
        if not texts:
            return []
        if self._embedder is None:
            raise RuntimeError('_embed_texts called before _init_embedder succeeded. Check that initialize() was awaited and a backend is installed.')
        import asyncio
        return await asyncio.to_thread(self._embedder.encode, texts)

    async def upsert_paper(self, paper: AcademicPaper) -> None:
        """
        Upsert a single academic paper.

        Args:
            paper: AcademicPaper instance to store.
        """
        if not self._initialized:
            await self.initialize()
        if paper.embedding is None:
            embeddings = await self._embed_texts([paper.title + ' ' + paper.abstract])
            paper.embedding = embeddings[0] if embeddings else [0.0] * self._dim
        self._table.merge_insert('paper_id').on('paper_id').execute([paper.to_dict()])

    async def upsert_papers(self, papers: list[AcademicPaper]) -> None:
        """
        Batch upsert academic papers.

        Args:
            papers: List of AcademicPaper instances.
        """
        if not self._initialized:
            await self.initialize()
        if not papers:
            return
        texts = [p.title + ' ' + p.abstract for p in papers]
        embeddings = await self._embed_texts(texts)
        for i, paper in enumerate(papers):
            if paper.embedding is None and i < len(embeddings):
                paper.embedding = embeddings[i]
        dicts = [p.to_dict() for p in papers]
        self._table.merge_insert('paper_id').on('paper_id').execute(dicts)

    async def _detect_query_type(self, query_text: str) -> str:
        """AREA H+: Decide whether to use FTS, hybrid, or pure vector search.
        Same heuristic as LanceDBIdentityStore for consistency.
        """
        if not query_text:
            return 'vector'
        if not self._lancedb_has_fts:
            return 'vector'
        words = query_text.split()
        if '"' in query_text or len(words) <= 2:
            return 'fts'
        if len(words) >= 10 and (not any((w[0].isupper() or w[0].isdigit() for w in words if w))):
            return 'vector'
        return 'hybrid'

    async def search_similar(self, query: str, top_k: int=10, filters: dict | None=None, query_type: str='auto') -> list[AcademicPaper]:
        """
        Semantic search for similar papers.

        Args:
            query: Search query text.
            top_k: Number of results to return.
            filters: Optional filters (e.g., {"source": "arxiv"}).
            query_type: Search mode — "auto" (default, uses _detect_query_type),
                or explicit "vector"/"fts"/"hybrid". AREA H+: "hybrid" applies
                native RRFReranker for 15-30% better recall on academic text.

        Returns:
            List of AcademicPaper instances.
        """
        if not self._initialized:
            await self.initialize()
        embeddings = await self._embed_texts([query])
        query_emb = embeddings[0] if embeddings else [0.0] * self._dim
        effective_qt = query_type
        if effective_qt == 'auto':
            effective_qt = await self._detect_query_type(query or '')
        _qt = effective_qt
        _q = query
        _emb = query_emb
        _k = top_k
        _filters = filters
        try:
            loop = asyncio.get_running_loop()

            def _search():
                if _qt == 'hybrid' and _q and self._lancedb_has_fts:
                    reranker = _get_rrf_reranker()
                    builder = self._table.search(query_type='hybrid', vector_column_name='embedding', fts_columns=['title', 'abstract']).vector(_emb).text(_q).limit(_k)
                    if reranker is not None:
                        builder = builder.rerank(reranker=reranker)
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        builder = builder.nprobes(_nprobes)
                    except TypeError:  # noqa: BLE001
                        pass
                    results = builder
                elif _qt == 'fts' and _q and self._lancedb_has_fts:
                    results = self._table.search(_q, query_type='fts', fts_columns=['title', 'abstract']).limit(_k)
                elif _q and (not self._lancedb_has_fts):
                    logger.debug('[LANCEDB:H] academic query=%r — FTS not available, vector only', str(_q)[:50])
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        results = self._table.search(_emb, vector_column_name='embedding').nprobes(_nprobes)
                    except TypeError:
                        results = self._table.search(_emb, vector_column_name='embedding')
                else:
                    # M1 8GB optimization: probe only ~3% of IVF_PQ partitions
                    _nprobes = self._ivfpq_nprobes
                    try:
                        results = self._table.search(_emb, vector_column_name='embedding').nprobes(_nprobes)
                    except TypeError:
                        results = self._table.search(_emb, vector_column_name='embedding')
                if _filters:
                    for key, value in _filters.items():
                        results = results.where(f"{key} = '{value}'")
                return results.to_list()
            rows = await asyncio.to_thread(_search)
        except Exception:
            return []
        papers = []
        for row in rows:
            papers.append(AcademicPaper(paper_id=row.get('paper_id', ''), title=row.get('title', ''), abstract=row.get('abstract', ''), authors=row.get('authors', []), year=row.get('year'), source=row.get('source', ''), doi=row.get('doi', ''), url=row.get('url', ''), citation_count=row.get('citation_count', 0), embedding=row.get('embedding')))
        return papers

    async def get_citation_context(self, paper_id: str, max_papers: int=20) -> list[AcademicPaper]:
        """
        Get papers that cite or are cited by the given paper.

        Args:
            paper_id: Paper ID to find citation context for.
            max_papers: Max papers to return.

        Returns:
            List of related AcademicPaper instances.
        """
        if not self._initialized:
            await self.initialize()
        try:
            _nprobes = self._ivfpq_nprobes
            try:
                _base = self._table.search([0.0] * self._dim, vector_column_name='embedding').nprobes(_nprobes).where(f"paper_id = '{paper_id}'").limit(1).to_list()
            except TypeError:
                _base = self._table.search([0.0] * self._dim, vector_column_name='embedding').where(f"paper_id = '{paper_id}'").limit(1).to_list()
            if not _base:
                return []
            try:
                similar = self._table.search(_base[0].get('embedding', [0.0] * self._dim), vector_column_name='embedding').nprobes(_nprobes).where(f"citation_count > {_base[0].get('citation_count', 0) * 0.5}").limit(max_papers).to_list()
            except TypeError:
                similar = self._table.search(_base[0].get('embedding', [0.0] * self._dim), vector_column_name='embedding').where(f"citation_count > {_base[0].get('citation_count', 0) * 0.5}").limit(max_papers).to_list()
            papers = []
            for row in similar:
                if row.get('paper_id') != paper_id:
                    papers.append(AcademicPaper(paper_id=row.get('paper_id', ''), title=row.get('title', ''), abstract=row.get('abstract', ''), authors=row.get('authors', []), year=row.get('year'), source=row.get('source', ''), doi=row.get('doi', ''), url=row.get('url', ''), citation_count=row.get('citation_count', 0), embedding=row.get('embedding')))
            return papers
        except Exception:
            return []

    async def close(self) -> None:
        """Close database connection."""
        if self._db is not None:
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass
_academic_store: LanceDBAcademicStore | None = None
_academic_store_lock = LazyAsyncioLock()

async def get_academic_store() -> LanceDBAcademicStore:
    """Get or create the singleton academic store (async-safe)."""
    global _academic_store
    if _academic_store is None:
        async with _academic_store_lock:
            if _academic_store is None:
                _academic_store = LanceDBAcademicStore()
    return _academic_store