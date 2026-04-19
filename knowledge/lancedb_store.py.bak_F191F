"""
LanceDB Identity Store - Hybrid vector + FTS search for entity resolution.

ROLE: Identity/Entity Store (NOT grounding authority)
=====================================================
Tento modul je identity/entity store pro entity resolution.
NENÍ owner context grounding - to je rag_engine.
NENÍ owner document retrieval - to je rag_engine HNSWVectorIndex.
NENÍ owner primary vector search - to je rag_engine.

Provides identity stitching capabilities using LanceDB with:
- Vector embeddings for semantic similarity
- Full-text search (FTS) for alias matching
- Hybrid search combining both approaches

Sprint 71: Bounded, fail-safe, MLX fallback for similarity.
Sprint 77: Embedding optimization (float16, writeback buffer, batched embedding, health check).
"""

import asyncio
import contextlib
import hashlib
import logging
import pickle
import time

import orjson
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import lmdb
import numpy as np

logger = logging.getLogger(__name__)

# Compiled similarity function - conditional import for MLX (fail gracefully in CI)
try:
    import mlx.core as mx

    @mx.compile
    def _cosine_sim_batch(a: mx.array, b: mx.array) -> mx.array:
        """MLX-compiled cosine similarity for batch processing."""
        a_n = a / mx.linalg.norm(a, axis=-1, keepdims=True)
        b_n = b / mx.linalg.norm(b, axis=-1, keepdims=True)
        return (a_n @ b_n.T).squeeze(0)

    MLX_AVAILABLE = True
except ImportError:
    # Numpy fallback for non-Metal environments (CI, testing)
    import numpy as np

    def _cosine_sim_batch(a, b):  # type: ignore
        """Numpy fallback for cosine similarity."""
        a_n = a / np.linalg.norm(a, axis=-1, keepdims=True)
        b_n = b / np.linalg.norm(b, axis=-1, keepdims=True)
        return (a_n @ b_n.T).squeeze()

    MLX_AVAILABLE = False


# Default database URI
_DEFAULT_URI = Path(__file__).parent.parent.parent / "data" / "identity.lance"


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

    # Sprint 76: Bounded limits
    _MAX_CACHE_SIZE = 1024**3  # 1GB
    _BINARY_FILTER_COUNT = 500
    _MMR_TOP_K = 50

    # Sprint 77: Writeback buffer limits
    _WRITEBACK_MAX = 1000

    def __init__(self, uri: str = str(_DEFAULT_URI), orchestrator=None):
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
        self._embedding_dim = 768

        # Sprint 76: LMDB embedding cache with float16 quantization
        self._cache_env = None
        self._cache_db = None
        self._init_cache()

        # Sprint 76: MLX embeddings (index mapping only, not full copies)
        self._mlx_embeddings = None
        self._mlx_ids = None
        self._mlx_id_to_idx = {}

        # Sprint 76: Binary embeddings for fast pre-filter
        self._binary_embeddings = None

        # Sprint 76: Lazy-loaded rerankers
        self._colbert_reranker = None
        self._flashrank_ranker = None
        self._colbert_loaded = False
        self._flashrank_loaded = False

        # Sprint 76: Memory prediction
        self._memory_history: Any = None  # deque, initialized in _init_cache
        self._eviction_threshold = 0.8

        # Sprint 76: usearch index (experimental)
        self._usearch_index = None
        self._usearch_loaded = False

        # Sprint 76: Compiled similarity
        self._compiled_similarity = None

        # Sprint 77: Embedder and MRL
        self._embedder = None
        self._embedder_type: Optional[str] = None
        self._embed_lock = asyncio.Lock()
        self._current_mrl_dim = 768
        self._mrl_enabled = False
        # Sprint 81 Fáze 4: MLXEmbeddingManager reference
        self._mlx_embed_manager = None
        # Sprint 81 Fáze 4: Numpy fallback dimension
        self._fallback_dim = 768

        # Sprint 77: Writeback buffer
        self._writeback_buffer: OrderedDict = OrderedDict()
        self._writeback_lock = asyncio.Lock()
        self._access_counts = defaultdict(int)

        # Sprint 77: Index build status
        self._index_build_status: Dict[str, Any] = {
            'in_progress': False,
            'started_at': None,
            'completed_at': None,
            'failed': False,
            'index_type': None,
            'progress_percent': 0
        }
        self._index_cache: Optional[bool] = None
        self._index_cache_time: float = 0.0
        self._index_build_deferred = False

        # Sprint 77: Metrics
        self._metrics = {
            'cache_hits': 0,
            'cache_misses': 0,
            'quantization_errors': deque(maxlen=100),
            'search_latencies': deque(maxlen=1000),
        }

        self._initialize()

    # =============================================================================
    # Sprint 76: LMDB Embedding Cache Methods
    # =============================================================================

    def _lmdb_put(self, key: str, data: Dict) -> None:
        """Synchronous LMDB put operation - zero-copy via orjson."""
        try:
            with self._cache_env.begin(write=True) as txn:
                # Sprint F180E: orjson místo pickle - zero-copy, rychlejší
                txn.put(key.encode(), orjson.dumps(data))
        except Exception as e:
            logger.debug(f"LMDB put failed: {e}")

    def _delete_cached_embedding(self, text_hash: str) -> None:
        """Delete embedding from cache."""
        try:
            with self._cache_env.begin(write=True) as txn:
                txn.delete(text_hash.encode())
        except Exception:
            pass

    async def _flush_writeback(self) -> None:
        """Flush writeback buffer to LMDB."""
        async with self._writeback_lock:
            items = list(self._writeback_buffer.items())
            self._writeback_buffer.clear()

        for key, val in items:
            try:
                await asyncio.to_thread(self._lmdb_put, key, val)
            except Exception:
                pass

    async def _initialize_embedder(self) -> bool:
        """Initialize embedder: MLX/GPU → CoreML/ANE → Numpy fallback."""
        # 1. MLXEmbeddingManager on GPU (primary) - Sprint 81 Fáze 4
        # Use shared singleton to avoid duplicate model loads
        try:
            from hledac.core.mlx_embeddings import get_embedding_manager
            self._mlx_embed_manager = get_embedding_manager()
            self._embedder = self._mlx_embed_manager
            self._embedder_type = 'mlx_gpu'
            logger.info(f"[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_embed_manager.model_path}, dim={self._mlx_embed_manager.EMBEDDING_DIM}")
            return True
        except ImportError:
            logger.debug("[EMBEDDER] mlx_embeddings not available, trying MLX direct")
        except Exception as e:
            logger.debug(f"[EMBEDDER] MLXEmbeddingManager init failed: {e}")

        # 2. CoreML on ANE (optional)
        try:
            import coremltools as ct
            model_path = Path.home() / '.hledac' / 'models' / 'modernbert-embed.mlpackage'
            if model_path.exists():
                self._embedder = ct.models.MLModel(str(model_path), compute_units=ct.ComputeUnit.ALL)
                self._embedder_type = 'coreml_ane'
                logger.info("[EMBEDDER] CoreML ANE embedder initialized")
                return True
        except Exception as e:
            logger.debug(f"[EMBEDDER] CoreML init failed: {e}")

        # 3. Numpy random fallback (Sprint 81 Fáze 4 - minimal footprint)
        logger.warning("[EMBEDDER] No hardware acceleration, using numpy fallback")
        self._embedder_type = 'numpy_fallback'
        self._fallback_dim = self._current_mrl_dim
        return True

    async def _embed_single(self, text: str) -> List[float]:
        """Embed single text via current embedder (for indexing - uses embed_document)."""
        # Sprint 81 Fáze 4: Support MLXEmbeddingManager, CoreML, and numpy fallback
        if self._embedder_type == 'numpy_fallback':
            # Minimal footprint fallback - random normalized embedding
            emb = np.random.randn(self._fallback_dim).astype(np.float32)
            norm = np.linalg.norm(emb)
            if norm > 0:
                emb = (emb / norm).tolist()
            return emb

        if self._embedder is None:
            return []
        try:
            if self._embedder_type == 'mlx_gpu':
                # MLXEmbeddingManager - use embed_document for indexing (task safety)
                result = await asyncio.to_thread(self._embedder.embed_document, text)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)
            elif self._embedder_type == 'coreml_ane':
                # CoreML model
                result = await asyncio.to_thread(self._embedder.predict, {'text': text})
                emb = result.get('embedding', [])
            else:
                # sentence_transformers or unknown - use encode (will validate in MLX path)
                result = await asyncio.to_thread(self._embedder.encode, text)
                emb = result.tolist() if hasattr(result, 'tolist') else list(result)

            # Truncate to MRL dimension
            if len(emb) > self._current_mrl_dim:
                emb = emb[:self._current_mrl_dim]
            return emb
        except Exception as e:
            logger.warning(f"[EMBED] Single embed failed: {e}")
            return []

    async def _embed_batch(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
        """Generate embeddings in batches - thread-safe (uses embed_document for indexing)."""
        # Sprint 81 Fáze 4: Support MLXEmbeddingManager, CoreML, and numpy fallback
        if not texts:
            return []

        if self._embedder_type == 'numpy_fallback':
            # Minimal footprint fallback - random normalized embeddings
            all_embs = []
            for _ in texts:
                emb = np.random.randn(self._fallback_dim).astype(np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                all_embs.append(emb.tolist())
            return all_embs

        all_embs = []

        async with self._embed_lock:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                try:
                    if self._embedder_type == 'mlx_gpu':
                        # MLXEmbeddingManager - use embed_document for indexing (task safety)
                        # Use internal _embed_for_indexing for batch support
                        emb_result = await asyncio.to_thread(
                            self._embedder._embed_for_indexing, batch
                        )
                        batch_embs = emb_result.tolist() if hasattr(emb_result, 'tolist') else list(emb_result)
                    elif self._embedder_type == 'coreml_ane':
                        # CoreML batch
                        result = await asyncio.to_thread(
                            self._embedder.predict, {'text': batch}
                        )
                        batch_embs = result.get('embeddings', [])
                    else:
                        # sentence_transformers or unknown
                        embs = await asyncio.to_thread(self._embedder.encode, batch)
                        batch_embs = embs.tolist() if hasattr(embs, 'tolist') else list(embs)

                    # Truncate each embedding
                    for emb in batch_embs:
                        if len(emb) > self._current_mrl_dim:
                            emb = emb[:self._current_mrl_dim]
                        all_embs.append(emb)
                except Exception:
                    # Fallback to single embedding
                    for t in batch:
                        all_embs.append(await self._embed_single(t))
        return all_embs

    def _compute_binary_signature(self, embedding: List[float]) -> int:
        """64-bit binary signature - numpy packbits (faster for 64 elements)."""
        arr = np.array(embedding[:64], dtype=np.float32) > 0
        packed = np.packbits(arr, bitorder='little')
        return int.from_bytes(packed.tobytes()[:8], 'little')

    def _compute_binary_signatures_batch(self, embeddings: List[List[float]]) -> List[int]:
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
        words = query_text.split()
        # If query contains quotes or is very short -> FTS
        if '"' in query_text or len(words) <= 2:
            return 'fts'
        # If query is long and has no uppercase/digits -> semantic -> vector
        if len(words) >= 10 and not any((w[0].isupper() or w[0].isdigit()) for w in words if w):
            return 'vector'
        return 'hybrid'

    def _rrf_fusion(self, fts_results: List[Dict], vec_results: List[Dict], top_k: int, k: int = 60) -> List[Dict]:
        """Reciprocal Rank Fusion with robust keying."""
        scores: Dict[str, float] = defaultdict(float)
        docs: Dict[str, Dict] = {}

        for rank, doc in enumerate(fts_results):
            key = doc.get('id') or doc.get('_rowid') or hashlib.md5(doc.get('text', '').encode()).hexdigest()
            scores[key] += 1.0 / (k + rank + 1)
            docs[key] = doc

        for rank, doc in enumerate(vec_results):
            key = doc.get('id') or doc.get('_rowid') or hashlib.md5(doc.get('text', '').encode()).hexdigest()
            scores[key] += 1.0 / (k + rank + 1)
            docs[key] = doc

        sorted_keys = sorted(scores, key=scores.get, reverse=True)
        return [docs[key] for key in sorted_keys[:top_k]]

    async def ensure_index(self, force: bool = False) -> None:
        """Create index with respect to available RAM and thermal state."""
        # Check RAM availability
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / (1024**3)

            if available_gb < 1.5:
                logger.warning("[INDEX] Critical memory (<1.5GB), skipping index build")
                return
            if available_gb < 3.0:
                logger.info("[INDEX] Low memory (<3GB), deferring index build")
                self._index_build_deferred = True
                return
        except Exception:
            pass

        # If we have deferred index build and now have enough memory, build it
        if self._index_build_deferred and not force:
            try:
                import psutil
                if psutil.virtual_memory().available / (1024**3) >= 3.0:
                    self._index_build_deferred = False
            except Exception:
                pass

    async def _warm_embedding_cache(self, queries: List[str], top_k: int = 50) -> None:
        """Pre-load embeddings for frequently used queries."""
        if not queries:
            return
        logger.info(f"[CACHE WARM] Warming {len(queries)} query embeddings")
        for query in queries[:top_k]:
            q_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
            if await self._get_cached_embedding(q_hash) is None:
                emb = await self._embed_single(query)
                if emb:
                    await self._store_embedding(q_hash, emb)
        logger.info("[CACHE WARM] Complete")

    async def _cache_maintenance_loop(self) -> None:
        """Background cache maintenance task."""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                await self._flush_writeback()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def health_check(self) -> Dict[str, Any]:
        """Check embedding store health."""
        result = {
            'healthy': True,
            'cache_size': len(self._writeback_buffer),
            'index_exists': False,
            'embedder_type': getattr(self, '_embedder_type', 'not_initialized'),
            'errors': []
        }
        try:
            # Check embedder
            if self._embedder is None:
                result['healthy'] = False
                result['errors'].append('embedder_not_initialized')

            # Flush writeback
            await self._flush_writeback()
            result['writeback_healthy'] = True

            # Check cache
            if self._cache_env is None:
                result['healthy'] = False
                result['errors'].append('cache_not_initialized')

        except Exception as e:
            result['healthy'] = False
            result['errors'].append(str(e))
        return result

    async def shutdown(self) -> None:
        """Cleanup resources."""
        # Cancel background tasks
        for task_name in ['_cache_maintenance_task']:
            task = getattr(self, task_name, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Flush writeback buffer
        await self._flush_writeback()

        # Close LMDB
        if self._cache_env is not None:
            try:
                self._cache_env.close()
            except Exception:
                pass

        # Clear MLX memory
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.eval([])
            mx.clear_cache()
        except Exception:
            pass

    def _init_cache(self) -> None:
        """Initialize LMDB cache for embeddings with float16 quantization."""
        try:
            cache_path = Path(self.uri).parent / 'embedding_cache'
            cache_path.mkdir(parents=True, exist_ok=True)
            self._cache_env = lmdb.open(str(cache_path), map_size=self._MAX_CACHE_SIZE)
            self._cache_db = self._cache_env.open_db()
            self._memory_history = deque(maxlen=10)
            logger.debug("LMDB embedding cache initialized")
        except Exception as e:
            logger.warning(f"Failed to init embedding cache: {e}")
            self._cache_env = None

    async def _get_cached_embedding(self, text_hash: str) -> Optional[List[float]]:
        """Get embedding from LMDB cache with writeback buffer."""
        if self._cache_env is None:
            return None

        # Check writeback buffer first
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
                        # Sprint F180E: orjson místo pickle - zero-copy read
                        data = orjson.loads(cached)
                        # Check TTL if present
                        if 'ttl' in data and 'stored_at' in data:
                            if time.time() - data['stored_at'] > data['ttl']:
                                return None, True  # Expired
                        emb_np = np.frombuffer(data['embedding'], dtype=np.float16)
                        return emb_np.astype(np.float32).tolist(), False
            except Exception:
                pass
            return None, False

        result = await asyncio.to_thread(_sync)
        if result is None or result[0] is None:
            self._metrics['cache_misses'] += 1
            return None
        if result[1]:  # Expired
            await asyncio.to_thread(self._delete_cached_embedding, text_hash)
            self._metrics['cache_misses'] += 1
            return None

        data, _ = result
        # Update access count and add to writeback buffer
        new_data = {
            'embedding': data.get('embedding'),
            'dtype': data.get('dtype', 'float16'),
            'dim': data.get('dim', 768),
            'ttl': data.get('ttl', 86400),
            'stored_at': data.get('stored_at', time.time()),
            'access_count': data.get('access_count', 0) + 1,
            'last_access': time.time()
        }

        async with self._writeback_lock:
            self._writeback_buffer[text_hash] = new_data
            # Flush oldest if buffer full
            if len(self._writeback_buffer) > self._WRITEBACK_MAX:
                flush_key, flush_val = self._writeback_buffer.popitem(last=False)
                flush_item = (flush_key, flush_val)
            else:
                flush_item = None

        # Flush outside lock
        if flush_item:
            await asyncio.to_thread(self._lmdb_put, flush_item[0], flush_item[1])

        self._metrics['cache_hits'] += 1
        return data

    async def _store_embedding(self, text_hash: str, embedding: List[float], ttl: Optional[float] = None) -> None:
        """Store embedding with float16 quantization (50% memory savings) and writeback buffer."""
        if self._cache_env is None:
            return

        try:
            emb_np = np.array(embedding, dtype=np.float16)
            data = {
                'embedding': emb_np.tobytes(),
                'dtype': 'float16',
                'dim': len(embedding),
                'ttl': ttl or 86400,
                'stored_at': time.time(),
                'access_count': 0,
            }

            # Add to writeback buffer
            async with self._writeback_lock:
                self._writeback_buffer[text_hash] = data
                # Flush oldest if buffer full
                if len(self._writeback_buffer) > self._WRITEBACK_MAX:
                    flush_key, flush_val = self._writeback_buffer.popitem(last=False)
                    flush_item = (flush_key, flush_val)
                else:
                    flush_item = None

            # Flush outside lock
            if flush_item:
                await asyncio.to_thread(self._lmdb_put, flush_item[0], flush_item[1])

        except Exception as e:
            logger.debug(f"Failed to store embedding: {e}")

    async def _warm_cache(self, top_k: int = 100) -> None:
        """Pre-load frequently accessed embeddings."""
        if not self._orch or not hasattr(self._orch, '_evidence_log') or self._orch._evidence_log is None:
            return
        try:
            recent = self._orch._evidence_log.get_recent_evidence(top_k)
            for ev in recent:
                text_hash = hashlib.sha256(ev.content.encode()).hexdigest()[:16]
                cached = await self._get_cached_embedding(text_hash)
                if cached is None and hasattr(ev, 'embedding') and ev.embedding:
                    await self._store_embedding(text_hash, ev.embedding)
            logger.info(f"Cache warmed with {top_k} embeddings")
        except Exception as e:
            logger.debug(f"Cache warming failed: {e}")

    async def _load_embeddings_to_mlx(self) -> None:
        """Load embeddings to MLX (index mapping only, not full copies)."""
        if self._table is None:
            return
        try:
            import mlx.core as mx
            data = self._table.to_lance().to_table(columns=['_embedding', 'id']).to_pydict()
            if len(data.get('_embedding', [])) == 0:
                return
            self._embedding_dim = len(data['_embedding'][0])
            self._mlx_embeddings = mx.array(data['_embedding'])
            self._mlx_ids = data['id']
            self._mlx_id_to_idx = {row_id: i for i, row_id in enumerate(data['id'])}

            # Binary embeddings - pack to 1 bit
            signs = (self._mlx_embeddings > 0).astype(mx.uint8)
            batch, dim = signs.shape
            padded_dim = ((dim + 7) // 8) * 8
            padded = mx.zeros((batch, padded_dim), dtype=mx.uint8)
            padded[:, :dim] = signs
            packed = mx.zeros((batch, padded_dim // 8), dtype=mx.uint8)
            for i in range(8):
                packed |= (padded[:, i::8] << (7 - i))
            self._binary_embeddings = packed
            logger.info(f"Loaded {len(data['_embedding'])} embeddings to MLX")
        except Exception as e:
            logger.warning(f"Failed to load embeddings to MLX: {e}")

    async def _ensure_compiled_similarity(self) -> None:
        """Compile similarity function with MLX."""
        if self._compiled_similarity is not None:
            return
        try:
            import mlx.core as mx

            def _cosine_sim_batch(q: mx.array, d: mx.array) -> mx.array:
                q_norm = q / (mx.linalg.norm(q, axis=-1, keepdims=True) + 1e-8)
                d_norm = d / (mx.linalg.norm(d, axis=-1, keepdims=True) + 1e-8)
                return q_norm @ d_norm.T

            self._compiled_similarity = mx.compile(_cosine_sim_batch)
            # Warmup
            dummy_q = mx.zeros((1, self._embedding_dim))
            dummy_d = mx.zeros((1, self._embedding_dim))
            _ = self._compiled_similarity(dummy_q, dummy_d)
            logger.info("Compiled similarity ready")
        except Exception as e:
            logger.debug(f"Compilation failed: {e}")
            self._compiled_similarity = None

    async def _mlx_rerank(self, query_emb: List[float], candidates: List[Dict], top_k: int) -> List[Dict]:
        """Rerank candidates using MLX cosine similarity."""
        if self._mlx_embeddings is None or len(candidates) == 0:
            return candidates[:top_k]

        await self._ensure_compiled_similarity()
        import mlx.core as mx

        cand_indices = []
        valid_candidates = []
        for c in candidates:
            idx = self._mlx_id_to_idx.get(c.get('id'))
            if idx is not None:
                cand_indices.append(idx)
                valid_candidates.append(c)

        if not valid_candidates:
            return candidates[:top_k]

        q = mx.array(query_emb).reshape(1, -1)
        d = self._mlx_embeddings[cand_indices]

        if self._compiled_similarity:
            scores = self._compiled_similarity(q, d)
        else:
            q_norm = q / (mx.linalg.norm(q, axis=-1, keepdims=True) + 1e-8)
            d_norm = d / (mx.linalg.norm(d, axis=-1, keepdims=True) + 1e-8)
            scores = q_norm @ d_norm.T

        scores_np = np.array(scores.squeeze(0))
        sorted_idx = np.argsort(scores_np)[::-1][:top_k]
        return [valid_candidates[i] for i in sorted_idx]

    async def _binary_prefilter(self, query_emb: List[float], candidates: List[Dict], count: int = 500) -> List[Dict]:
        """Fast pre-filter using binary embeddings (Hamming distance)."""
        if self._binary_embeddings is None or len(candidates) == 0:
            return candidates

        try:
            import mlx.core as mx
            cand_indices = []
            valid_candidates = []
            for c in candidates:
                idx = self._mlx_id_to_idx.get(c.get('id'))
                if idx is not None:
                    cand_indices.append(idx)
                    valid_candidates.append(c)

            if not valid_candidates:
                return candidates

            q = mx.sign(mx.array(query_emb)).astype(mx.uint8)
            q_padded = mx.zeros((1, self._binary_embeddings.shape[1]), dtype=mx.uint8)
            for i in range(8):
                q_padded |= ((q[:, i::8] & 1) << (7 - i))

            xor_result = q_padded ^ self._binary_embeddings[cand_indices]
            scores = []
            for i, idx in enumerate(cand_indices):
                xored = np.unpackbits(np.array(xor_result[i], dtype=np.uint8))
                score = np.sum(xored)
                scores.append((score, i))
            scores.sort(key=lambda x: x[0])
            top_indices = [i for _, i in scores[:count]]
            return [valid_candidates[i] for i in top_indices]
        except Exception as e:
            logger.debug(f"Binary prefilter failed: {e}")
            return candidates

    def _mmr(self, candidates: List[Dict], query_emb: List[float], lambda_param: float = 0.5, top_k: int = 30) -> List[Dict]:
        """Maximal Marginal Relevance - reduce duplicates in results."""
        if len(candidates) <= top_k:
            return candidates

        selected = []
        remaining = candidates.copy()
        query_emb_np = np.array(query_emb)

        while len(selected) < top_k and remaining:
            mmr_scores = []
            for doc in remaining:
                doc_emb = np.array(doc.get('_embedding', [0] * len(query_emb)))
                sim_to_query = np.dot(query_emb_np, doc_emb) / (np.linalg.norm(query_emb_np) * np.linalg.norm(doc_emb) + 1e-8)

                max_sim_to_selected = 0
                if selected:
                    selected_embs = np.array([s.get('_embedding', [0] * len(query_emb)) for s in selected])
                    sims = np.dot(selected_embs, doc_emb) / (np.linalg.norm(selected_embs, axis=1) * np.linalg.norm(doc_emb) + 1e-8)
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
            import usearch
            from usearch.index import Index

            if self._table.count_rows() < 1000:
                self._usearch_loaded = True
                return

            data = self._table.to_lance().to_table(columns=['_embedding', 'id']).to_pydict()
            if len(data.get('_embedding', [])) == 0:
                return

            self._usearch_index = Index(
                ndim=self._embedding_dim,
                metric='cos',
                dtype='f32',
                connectivity=16,
                expansion_add=128,
                expansion_search=64
            )
            for i, emb in enumerate(data['_embedding'][:10000]):
                self._usearch_index.add(i, np.array(emb, dtype=np.float32))
            logger.info(f"usearch index loaded with {len(data['_embedding'][:10000])} vectors")
        except Exception as e:
            logger.warning(f"usearch unavailable: {e}")
            self._usearch_index = None
        self._usearch_loaded = True

    async def _usearch_search(self, query_emb: List[float], count: int = 200) -> List[Dict]:
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
            logger.debug(f"usearch search failed: {e}")
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
        """Pre-emptive eviction based on prediction."""
        predicted = await self._predict_memory_pressure()
        if predicted > self._eviction_threshold:
            logger.info(f"Pre-emptive cache eviction: predicted={predicted:.2f}")

    async def _get_colbert_reranker(self):
        """Lazy load ColBERT."""
        if self._colbert_loaded:
            return self._colbert_reranker
        try:
            from knowledge.colbert_retriever import ColBERTReranker
            self._colbert_reranker = ColBERTReranker()
            self._colbert_loaded = True
            logger.info("ColBERT reranker loaded")
            return self._colbert_reranker
        except Exception as e:
            logger.warning(f"ColBERT load failed: {e}")
            return None

    async def _get_flashrank_ranker(self):
        """Lazy load FlashRank."""
        if self._flashrank_loaded:
            return self._flashrank_ranker
        try:
            from flashrank import Ranker
            self._flashrank_ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2")
            self._flashrank_loaded = True
            logger.info("FlashRank loaded")
            return self._flashrank_ranker
        except Exception as e:
            logger.warning(f"FlashRank load failed: {e}")
            return None

    def _initialize(self) -> None:
        """Initialize database and table."""
        try:
            import lancedb
            import pyarrow as pa

            # Ensure directory exists
            Path(self.uri).parent.mkdir(parents=True, exist_ok=True)

            # Connect to database
            self.db = lancedb.connect(self.uri)

            # Create table with schema
            self._table = self.db.create_table(
                "entities",
                schema=pa.schema([
                    pa.field("id", pa.string()),
                    pa.field("embedding", pa.list_(pa.float32(), list_size=768)),
                    pa.field("aliases", pa.list_(pa.string())),
                    pa.field("first_seen", pa.timestamp('s')),
                    pa.field("last_seen", pa.timestamp('s')),
                ]),
                exist_ok=True
            )

            # Create FTS index only if not already present
            try:
                existing_indices = getattr(self._table, 'list_indices', lambda: [])()
                if not any(getattr(idx, 'name', '') == 'aliases_fts' for idx in existing_indices):
                    self._table.create_fts_index("aliases", replace=False)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.debug(f"FTS index creation skipped: {e}")

            logger.info(f"LanceDB identity store initialized at {self.uri}")

        except ImportError:
            logger.warning("LanceDB not available, identity store disabled")
            self.db = None
        except Exception as e:
            logger.warning(f"Failed to initialize LanceDB: {e}")
            self.db = None

    async def add_entity(
        self,
        entity_id: str,
        embedding: List[float],
        aliases: List[str]
    ) -> bool:
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

        try:
            import pyarrow as pa

            now = datetime.now(timezone.utc)

            # Convert to pyarrow format
            data = [{
                "id": entity_id,
                "embedding": embedding,
                "aliases": aliases,
                "first_seen": now,
                "last_seen": now,
            }]

            # Add in thread to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._table.add(data)
            )

            return True

        except Exception as e:
            logger.warning(f"Failed to add entity: {e}")
            return False

    async def search_similar(
        self,
        embedding: List[float],
        text_hint: str = "",
        threshold: float = 0.85,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Search for similar entities.

        Args:
            embedding: Query embedding.
            text_hint: Optional text query for FTS.
            threshold: Similarity threshold (0-1).
            limit: Maximum results to return.

        Returns:
            List of matching entities with similarity scores.
        """
        if self._table is None:
            return []

        try:
            import pandas as pd

            loop = asyncio.get_running_loop()

            def _search():
                if text_hint:
                    # Hybrid search: vector + text
                    return (
                        self._table.search(query_type="hybrid")
                        .vector(embedding)
                        .text(text_hint)
                        .limit(limit)
                        .to_pandas()
                    )
                else:
                    # Pure vector search
                    return (
                        self._table.search(
                            embedding,
                            vector_column_name="embedding"
                        )
                        .limit(limit)
                        .to_pandas()
                    )

            df = await loop.run_in_executor(None, _search)

            # Filter by threshold
            if "_distance" in df.columns:
                # Convert distance to similarity (1 - distance for cosine)
                df["similarity"] = 1 - df["_distance"]
                df = df[df["similarity"] >= threshold]

            # Convert to list of dicts
            results = []
            for _, row in df.iterrows():
                results.append({
                    "id": row.get("id", ""),
                    "aliases": row.get("aliases", []),
                    "similarity": row.get("similarity", 0.0),
                    "first_seen": row.get("first_seen"),
                    "last_seen": row.get("last_seen"),
                })

            return results[:limit]

        except Exception as e:
            logger.warning(f"Search failed: {e}")
            return []

    async def compute_similarity(
        self,
        emb1: List[float],
        emb2: List[float]
    ) -> float:
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
                return float(result[0])
            else:
                # Numpy fallback
                a = np.array(emb1)
                b = np.array(emb2)
                a_n = a / np.linalg.norm(a)
                b_n = b / np.linalg.norm(b)
                return float(np.dot(a_n, b_n))
        except Exception as e:
            logger.warning(f"Similarity computation failed: {e}")
            return 0.0

    async def close(self) -> None:
        """Close database connection and cache."""
        if self.db is not None:
            try:
                self.db.close()
            except Exception:
                pass
        if self._cache_env is not None:
            try:
                self._cache_env.close()
            except Exception:
                pass

    # =============================================================================
    # Sprint 76: Extended search_similar with adaptive reranking
    # =============================================================================

    async def search_similar_adaptive(
        self,
        query_text: str,
        query_emb: List[float],
        top_k: int = 10
    ) -> List[Dict]:
        """
        Hybrid search with adaptive reranking and MMR (Sprint 76).

        Args:
            query_text: Original query text for reranking.
            query_emb: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of ranked documents.
        """
        # Narrow seam: self._orch._memory_mgr.get_reranking_context() je jediný entry point
        # pro thermal/battery awareness. Store funguje i bez orchestratoru (default values).
        ctx = {"thermal": "NORMAL", "on_battery": False, "available_gb": 8.0}
        try:
            if self._orch and hasattr(self._orch, '_memory_mgr') and self._orch._memory_mgr:
                ctx = self._orch._memory_mgr.get_reranking_context()
        except Exception:
            pass
        thermal = ctx.get("thermal", "NORMAL")
        on_battery = ctx.get("on_battery", False)
        available_gb = ctx.get("available_gb", 8.0)

        # Stage 1: Primary search - LanceDB vector
        try:
            candidates = await self.search_similar(query_emb, limit=200)
        except Exception:
            if self._usearch_index is not None:
                candidates = await self._usearch_search(query_emb, count=200)
            else:
                candidates = []

        if not candidates:
            return []

        # Stage 2: Binary pre-filter (if many candidates)
        if len(candidates) > 100:
            candidates = await self._binary_prefilter(query_emb, candidates, count=self._BINARY_FILTER_COUNT)

        # Stage 3: MMR diversity filter
        candidates = self._mmr(candidates, query_emb, top_k=min(self._MMR_TOP_K, len(candidates)))

        # Stage 4: Speculative reranking - skip if low variance
        scores = [c.get('similarity', 0.5) for c in candidates]
        if scores:
            mean_score = sum(scores) / len(scores)
            variance = sum((s - mean_score) ** 2 for s in scores) / len(scores)
            if variance < 0.1:
                return candidates[:top_k]

        # Stage 5: Adaptive reranking based on resources (available_gb from get_reranking_context)
        # ColBERT (GPU) - requires >4GB and cool temperature
        if available_gb > 4.0 and thermal not in ("HOT", "CRITICAL") and not on_battery:
            reranker = await self._get_colbert_reranker()
            if reranker:
                return await reranker.rerank(query_text, candidates, top_k)

        # FlashRank (CPU) - requires >2GB
        if available_gb > 2.0:
            reranker = await self._get_flashrank_ranker()
            if reranker:
                try:
                    from flashrank import RerankRequest
                    passages = [{"id": i, "text": c.get('text', '')} for i, c in enumerate(candidates[:50])]
                    request = RerankRequest(query=query_text, passages=passages)
                    results = reranker.rerank(request)
                    return [candidates[r['id']] for r in results[:top_k]]
                except Exception:
                    pass

        # Fallback: MLX rerank
        return await self._mlx_rerank(query_emb, candidates, top_k)


# Module-level singleton
_identity_store: Optional[LanceDBIdentityStore] = None


def get_identity_store() -> LanceDBIdentityStore:
    """Get or create the singleton identity store."""
    global _identity_store
    if _identity_store is None:
        _identity_store = LanceDBIdentityStore()
    return _identity_store
