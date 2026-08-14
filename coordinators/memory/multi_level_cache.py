"""
Multi-Level Context Cache
=========================


Multi-level context cache with semantic search using FAISS and USearch HNSW.

Extracted from memory_coordinator.py (F320) — original line range: 1270-1684

Features:
- L1 (memory) + L2 (disk) hierarchy
- FAISS semantic index for similarity search
- USearch HNSW for approximate nearest neighbor search (Sprint 26)
- Thread-safe operations
- CacheType classification
- Configurable similarity threshold
- LFU eviction policy

Canonical import:
    from hledac.universal.coordinators.memory import MultiLevelContextCache

Types (CacheType, CacheLocation, CacheEntry) are defined in _core.py
to avoid duplication. Import from there or via this module.
"""

import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path
from typing import Any

from hledac.universal.coordinators.memory._core import (
    CacheEntry,
    CacheLocation,
    CacheType,
)
from hledac.universal.utils.lru_cache import LRUCache
from hledac.universal.utils.msgspec_json import decode_zstd as _decode_zstd
from hledac.universal.utils.msgspec_json import encode_zstd as _encode_zstd
from hledac.universal.utils._patterns import AsyncLazyLockDescriptor  # F320-REFACTOR-2

logger = logging.getLogger(__name__)

try:
    import numpy as np
    from numpy.typing import NDArray
    HAS_NUMPY = True
except ImportError:
    np = None
    NDArray = 'NDArray'
    HAS_NUMPY = False

try:
    import usearch
    USEARCH_AVAILABLE = True
except ImportError:
    usearch = None
    USEARCH_AVAILABLE = False


class MultiLevelContextCache:
    """
    Multi-level context cache with semantic search using FAISS.

    Features:
    - L1 (memory) + L2 (disk) hierarchy
    - FAISS semantic index for similarity search
    - USearch HNSW for approximate nearest neighbor search (Sprint 26)
    - Thread-safe operations
    - CacheType classification
    - Configurable similarity threshold
    - LFU eviction policy
    """
    __slots__ = (
        '_embedding_cache', '_embedding_cache_lock', '_hnsw_ef_construction',
        '_hnsw_ef_search', '_hnsw_index', '_hnsw_m', '_hnsw_max_elements',
        '_l1_freq', '_l2_freq', '_lock',
        'embedder', 'embedding_dim', 'embedding_model', 'embedding_to_cache_id',
        'faiss_available', 'l1_cache', 'l1_max_size_bytes', 'l2_cache',
        'l2_storage_path', 'max_entries', 'semantic_index', 'similarity_threshold',
        'stats',
    )

    # F320-Issue2: NFC-normalized text embedding cache — now per-instance
    _embedding_cache: dict[str, Any]
    _embedding_cache_lock: asyncio.Lock | None

    def __init__(
        self,
        embedding_model: str = 'nomic-ai/nomic-embed-text-v1.5',
        l1_max_size_mb: float = 100.0,
        l2_storage_path: str = 'cache_storage',
        similarity_threshold: float = 0.95,
        max_entries: int = 10000,
    ) -> None:
        """
        Initialize multi-level cache.

        Args:
            embedding_model: FastEmbed model name
            l1_max_size_mb: Maximum L1 cache size in MB
            l2_storage_path: Path for L2 disk cache
            similarity_threshold: Threshold for semantic similarity
            max_entries: Maximum total entries
        """
        self.embedding_model = embedding_model
        self.l1_max_size_bytes = int(l1_max_size_mb * 1024 * 1024)
        self.l2_storage_path = Path(l2_storage_path)
        self.l2_storage_path.mkdir(parents=True, exist_ok=True)
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self.embedder = None
        self.embedding_dim = 384
        self._initialize_embedder()
        self.l1_cache: LRUCache[str, CacheEntry] = LRUCache(max_size=max_entries)
        self.l2_cache: dict[str, CacheEntry] = {}
        self._l1_freq: dict[str, int] = {}
        self._l2_freq: dict[str, int] = {}
        try:
            import faiss
            self.semantic_index = faiss.IndexFlatIP(self.embedding_dim)
            self.faiss_available = True
        except ImportError:
            logger.warning('FAISS not available, semantic search disabled')
            self.semantic_index = None
            self.faiss_available = False
        self._hnsw_index = None
        self._hnsw_max_elements = 10000
        self._hnsw_m = 16
        self._hnsw_ef_construction = 200
        self._hnsw_ef_search = 50
        if USEARCH_AVAILABLE:
            self._init_hnsw()
        self.embedding_to_cache_id: dict[int, str] = {}
        self.stats = {
            'hits': 0, 'misses': 0, 'total_requests': 0,
            'l1_promotions': 0, 'l2_demotions': 0, 'evictions': 0,
            'similarities': [],
        }
        # ISSUE-2984: lazy lock — NEVER asyncio.Lock() at import/construct time
        self._lock: asyncio.Lock | None = None
        # ISSUE-ZOOMOUT: per-instance embedding cache (was class-level = bug)
        self._embedding_cache = {}
        self._embedding_cache_lock: asyncio.Lock | None = None
        self._load_l2_cache()
        self._rebuild_semantic_index()

    # F320-REFACTOR-2: async lazy lock descriptors (ISSUE-014 compliant)
    _get_lock = AsyncLazyLockDescriptor("_lock")

    # F320-Issue2: lazy lock for embedding cache
    async def _get_embedding_lock(self) -> asyncio.Lock | None:
        """Lazily create asyncio.Lock for embedding cache operations."""
        if self._embedding_cache_lock is None:
            try:
                self._embedding_cache_lock = asyncio.Lock()
            except Exception:
                return None
        return self._embedding_cache_lock

    def _init_hnsw(self) -> None:
        """Initialize usearch index for approximate nearest neighbor search (Sprint 26)."""
        if not USEARCH_AVAILABLE:
            return
        try:
            import usearch.index
            self._hnsw_index = usearch.index.Index(
                ndim=self.embedding_dim,
                metric='cos',
                dtype='f32',
                connectivity=self._hnsw_m,
                expansion_add=min(self._hnsw_ef_construction, 100),
                expansion_search=self._hnsw_ef_search,
            )
            logger.debug('USearch index initialized')
        except Exception as e:
            logger.warning(f'USearch index initialization failed: {e}')
            self._hnsw_index = None

    def _hnsw_search(self, query_emb: Any, k: int) -> list[int]:
        """Search usearch index for approximate nearest neighbors (Sprint 26)."""
        if self._hnsw_index is None:
            return []
        try:
            results = self._hnsw_index.search(query_emb.astype(np.float32), count=k)
            return [int(getattr(r, 'key', 0)) for r in results]
        except Exception:
            return []

    def _initialize_embedder(self) -> None:
        """fastembed REMOVED P0-1: MLXEmbedder used elsewhere; cache uses dummy embeddings."""
        self.embedder = None
        self.embedding_dim = 384

    def _serialize_to_json(self, data: Any) -> bytes:
        """Serialize data to JSON bytes using msgspec, compressed with zstd."""
        return _encode_zstd(data)

    def _deserialize_from_json(self, data: bytes) -> Any:
        """Deserialize from zstd-compressed JSON bytes via msgspec facade."""
        return _decode_zstd(data)

    def _load_l2_cache(self) -> None:
        """Load L2 cache from disk. Prefer zstd-compressed .json.zst, fallback to .json."""
        try:
            zst_file = self.l2_storage_path / 'l2_cache.json.zst'
            json_file = self.l2_storage_path / 'l2_cache.json'
            if zst_file.exists():
                with open(zst_file, 'rb') as f:
                    cache_bytes = f.read()
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning(
                        'L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh',
                        len(cache_bytes) // (1024 * 1024),
                    )
                    self.l2_cache = {}
                else:
                    self.l2_cache = self._deserialize_from_json(cache_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.zst)')
            elif json_file.exists():
                with open(json_file, 'rb') as f:
                    cache_bytes = f.read()
                if len(cache_bytes) > 50 * 1024 * 1024:
                    logger.warning(
                        'L2 cache too large (%d MB > 50MB limit) — skipping load, starting fresh',
                        len(cache_bytes) // (1024 * 1024),
                    )
                    self.l2_cache = {}
                else:
                    self.l2_cache = self._deserialize_from_json(cache_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.json legacy)')
            else:
                self.l2_cache = {}
        except Exception as e:
            logger.warning(f'Could not load L2 cache: {e}')
            self.l2_cache = {}

    def _save_l2_cache(self) -> None:
        """Save L2 cache to disk as zstd-compressed .json.zst."""
        try:
            cache_file = self.l2_storage_path / 'l2_cache.json.zst'
            with open(cache_file, 'wb') as f:
                f.write(self._serialize_to_json(self.l2_cache))
        except Exception as e:
            logger.warning(f'Could not save L2 cache: {e}')

    def _rebuild_semantic_index(self) -> None:
        """Rebuild FAISS semantic index from existing entries."""
        if not self.faiss_available:
            return
        try:
            import faiss
            self.semantic_index = faiss.IndexFlatIP(self.embedding_dim)
            self.embedding_to_cache_id.clear()
            all_entries = list(self.l1_cache.values()) + list(self.l2_cache.values())
            for entry in all_entries:
                if entry.embedding is not None:
                    embedding_id = len(self.embedding_to_cache_id)
                    self.embedding_to_cache_id[embedding_id] = entry.cache_id
                    self.semantic_index.add(entry.embedding.reshape(1, -1).astype('float32'))
        except Exception as e:
            logger.warning(f'Could not rebuild semantic index: {e}')

    async def _get_embedding_async(self, text: str) -> Any | None:
        """Get embedding for text using MLXEmbedder or FastEmbed (async).

        F320-Issue2: Results are cached by NFC-normalized text to avoid
        re-encoding the same string across cycles.
        """
        import unicodedata
        normalized = unicodedata.normalize('NFC', text)
        
        # Check cache (protected by lock)
        _emb_lock = await self._get_embedding_lock()
        if _emb_lock is not None:
            async with _emb_lock:
                cached = self._embedding_cache.get(normalized)
                if cached is not None:
                    return cached
        else:
            cached = self._embedding_cache.get(normalized)
            if cached is not None:
                return cached
        
        # Compute embedding (outside lock - expensive operation)
        embedding = None
        if self.embedder:
            try:
                if hasattr(self.embedder, 'encode_batch'):
                    # C7-FIX: Use asyncio.Runner() instead of new_event_loop/run_until_complete.
                    # Runner handles loop lifecycle automatically and is the modern Python 3.11+ pattern.
                    result = await self.embedder.encode_batch([text])
                    embedding = result[0] if result else None
                else:
                    embeddings = list(self.embedder.embed([text]))
                    if embeddings:
                        embedding = np.array(embeddings[0])
            except Exception as e:
                logger.debug(f'Embedding failed: {e}')
        
        # Store result (protected by lock)
        _emb_lock = await self._get_embedding_lock()
        if _emb_lock is not None:
            async with _emb_lock:
                self._embedding_cache[normalized] = embedding
        else:
            self._embedding_cache[normalized] = embedding
        
        return embedding

    def _get_embedding(self, text: str) -> Any | None:
        """Get embedding for text using MLXEmbedder or FastEmbed (sync wrapper).

        C7-FIX: Uses run_sync_async() from sync_bridge for M1 safety.
        Prefer async _get_embedding_async() when called from async context.
        """
        import unicodedata

        from hledac.universal.utils.sync_bridge import run_sync_async
        normalized = unicodedata.normalize('NFC', text)
        cached = self._embedding_cache.get(normalized)
        if cached is not None:
            return cached
        try:
            return run_sync_async(self._get_embedding_async(text))
        except Exception:
            return None

    async def get(
        self,
        input_data: Any,
        cache_type: CacheType = CacheType.COMPUTATION,
        threshold: float | None = None,
    ) -> Any | None:
        """
        Get cached result using semantic similarity search.

        Args:
            input_data: Input data to lookup
            cache_type: Type of cache entry
            threshold: Custom similarity threshold

        Returns:
            Cached content or None if not found
        """
        threshold = threshold or self.similarity_threshold
        self.stats['total_requests'] += 1
        input_text = str(input_data)
        similar_entry = await self._find_similar_entry(input_text, threshold)
        if similar_entry:
            async with await self._get_lock():
                self.stats['hits'] += 1
                self._update_access(similar_entry.cache_id)
                if similar_entry.cache_id in self.l2_cache:
                    self._promote_to_l1(similar_entry.cache_id)
            return similar_entry.content
        self.stats['misses'] += 1
        return None

    async def _find_similar_entry(self, input_text: str, threshold: float) -> CacheEntry | None:
        """Find semantically similar cache entry using usearch (Sprint 26) or FAISS fallback."""
        if self._hnsw_index is not None:
            return await self._find_similar_entry_hnsw(input_text, threshold)
        if not self.faiss_available or self.semantic_index is None:
            return None
        input_embedding = await self._get_embedding_async(input_text)
        if input_embedding is None:
            return None
        try:
            query_embedding = input_embedding.reshape(1, -1).astype('float32')
            D, I = self.semantic_index.search(query_embedding, 10)
            for idx, similarity in zip(I[0], D[0], strict=False):
                if float(similarity) >= threshold:
                    cache_id = self.embedding_to_cache_id.get(int(idx))
                    if not cache_id:
                        continue
                    entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                    if entry:
                        async with await self._get_lock():
                            self.stats['similarities'].append(float(similarity))
                        return entry
        except Exception as e:
            logger.debug(f'Similarity search failed: {e}')
        return None

    async def _find_similar_entry_hnsw(
        self,
        input_text: str,
        threshold: float,
    ) -> CacheEntry | None:
        """Find semantically similar cache entry using usearch (Sprint 26)."""
        input_embedding = await self._get_embedding_async(input_text)
        if input_embedding is None:
            return None
        try:
            indices = self._hnsw_search(input_embedding, k=10)
            for idx in indices:
                cache_id = self.embedding_to_cache_id.get(int(idx))
                if not cache_id:
                    continue
                entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                if entry:
                    async with await self._get_lock():
                        self.stats['similarities'].append(1.0)
                    return entry
        except Exception as e:
            logger.debug(f'USearch similarity search failed: {e}')
        return None

    async def set(
        self,
        input_data: Any,
        content: Any,
        cache_type: CacheType = CacheType.COMPUTATION,
    ) -> None:
        """
        Cache a computation result.

        Args:
            input_data: Input data (used as key)
            content: Result to cache
            cache_type: Type of cache entry
        """
        cache_id = hashlib.md5(str(input_data).encode()).hexdigest()[:16]
        if cache_id in self.l1_cache or cache_id in self.l2_cache:
            return
        input_text = str(input_data)
        embedding = await self._get_embedding_async(input_text)
        cache_entry = CacheEntry(
            cache_id=cache_id,
            content=content,
            embedding=embedding,
            access_count=1,
            last_accessed=time.time(),
            created_at=time.time(),
            size_bytes=sys.getsizeof(content),
            cache_type=cache_type,
            metadata={},
        )
        async with await self._get_lock():
            if embedding is not None and self.faiss_available:
                try:
                    embedding_id = len(self.embedding_to_cache_id)
                    self.embedding_to_cache_id[embedding_id] = cache_id
                    self.semantic_index.add(embedding.reshape(1, -1).astype('float32'))
                except Exception as e:
                    logger.debug(f'Could not add to semantic index: {e}')
            if self._get_l1_size_bytes() + cache_entry.size_bytes <= self.l1_max_size_bytes:
                self.l1_cache[cache_id] = cache_entry
                self.l1_cache.move_to_end(cache_id)
                self._l1_freq[cache_id] = 1
            else:
                self.l2_cache[cache_id] = cache_entry
                self._l2_freq[cache_id] = 1
                await asyncio.to_thread(self._save_l2_cache)
            self._check_eviction()

    def _get_l1_size_bytes(self) -> int:
        """Get total size of L1 cache."""
        return sum(entry.size_bytes for entry in self.l1_cache.values())

    def _update_access(self, cache_id: str) -> None:
        """Update access statistics and LFU frequency counter for cache entry.

        S3 fix: Inkrements _l1_freq or _l2_freq frequency counter for LFU eviction.
        Also calls _check_eviction() to prevent unbounded growth on read-heavy workloads.
        """
        current_time = time.time()
        if cache_id in self.l1_cache:
            entry = self.l1_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self.l1_cache.move_to_end(cache_id)
            self._l1_freq[cache_id] = self._l1_freq.get(cache_id, 0) + 1
        elif cache_id in self.l2_cache:
            entry = self.l2_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self._l2_freq[cache_id] = self._l2_freq.get(cache_id, 0) + 1
        self._check_eviction()

    def _promote_to_l1(self, cache_id: str) -> None:
        """Promote entry from L2 to L1 cache."""
        if cache_id not in self.l2_cache:
            return
        entry = self.l2_cache.pop(cache_id)
        if self._get_l1_size_bytes() + entry.size_bytes <= self.l1_max_size_bytes:
            self.l1_cache[cache_id] = entry
            self.stats['l1_promotions'] += 1
        else:
            self.l2_cache[cache_id] = entry
        self._save_l2_cache()

    def _check_eviction(self) -> None:
        """Check and perform LFU eviction if needed.

        S3 fix: LFU eviction replaces LRU. Frequency counters (_l1_freq, _l2_freq)
        track access count. Eviction targets least-frequently-used items first.
        Batch eviction removes 10% of entries at once to avoid O(n) per-item overhead.
        """
        while self._get_l1_size_bytes() > self.l1_max_size_bytes and self.l1_cache:
            lfu_id = min(self._l1_freq, key=self._l1_freq.get) if self._l1_freq else None
            if lfu_id and lfu_id in self.l1_cache:
                self.l1_cache.pop(lfu_id)
                self._l1_freq.pop(lfu_id, None)
            else:
                oldest_id, oldest_entry = self.l1_cache.popitem(last=False)
                self._l1_freq.pop(oldest_id, None)
            self.l2_cache[oldest_id] = oldest_entry
            self._l2_freq[oldest_id] = self._l1_freq.get(oldest_id, 1)
            self.stats['l2_demotions'] += 1
        total_entries = len(self.l1_cache) + len(self.l2_cache)
        if total_entries > self.max_entries and self.l2_cache:
            batch_size = max(1, int(self.max_entries * 0.1))
            evicted = 0
            for _ in range(min(batch_size, len(self.l2_cache))):
                if not self.l2_cache:
                    break
                lfu_id = min(self._l2_freq, key=self._l2_freq.get) if self._l2_freq else None
                if lfu_id and lfu_id in self.l2_cache:
                    del self.l2_cache[lfu_id]
                    self._l2_freq.pop(lfu_id, None)
                else:
                    oldest_id = min(
                        self.l2_cache.keys(),
                        key=lambda k: self.l2_cache[k].last_accessed,
                    )
                    del self.l2_cache[oldest_id]
                    self._l2_freq.pop(oldest_id, None)
                self.stats['evictions'] += 1
                evicted += 1
            if evicted > 0:
                self._save_l2_cache()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self.stats['hits'] + self.stats['misses']
        avg_similarity = 0.0
        if self.stats['similarities']:
            avg_similarity = sum(self.stats['similarities']) / len(self.stats['similarities'])
        return {
            'total_entries': len(self.l1_cache) + len(self.l2_cache),
            'l1_entries': len(self.l1_cache),
            'l2_entries': len(self.l2_cache),
            'hit_count': self.stats['hits'],
            'miss_count': self.stats['misses'],
            'hit_rate': self.stats['hits'] / total if total > 0 else 0.0,
            'l1_size_mb': self._get_l1_size_bytes() / (1024 * 1024),
            'avg_similarity_score': avg_similarity,
            'l1_promotions': self.stats['l1_promotions'],
            'l2_demotions': self.stats['l2_demotions'],
            'evictions': self.stats['evictions'],
        }

    async def clear(self, location: CacheLocation | None = None) -> None:
        """
        Clear cache entries.

        Args:
            location: Specific location to clear, or None for all
        """
        async with await self._get_lock():
            if location is None or location == CacheLocation.L1_MEMORY:
                self.l1_cache.clear()
                self._l1_freq.clear()
            if location is None or location == CacheLocation.L2_DISK:
                self.l2_cache.clear()
                self._l2_freq.clear()
                self._save_l2_cache()
            self._rebuild_semantic_index()
