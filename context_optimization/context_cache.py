"""
Multi-level Context Cache with FastEmbed (ONNX)
=========================================

OPTIMIZED: PyTorch backend removed in favor of ONNX Runtime via FastEmbed

This module provides memory-efficient multi-level caching using FastEmbed
with ONNX runtime, optimized for M1 MacBook Air (8GB RAM).

FastEmbed uses quantized ONNX models for maximum inference speed
and minimal memory footprint (~50MB vs ~420MB for PyTorch).
"""
import hashlib
import logging
import statistics
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.async_helpers import safe_gather_ok
import compression.zstd as _zstd
ZSTD_AVAILABLE = True
try:
    from hledac.universal.utils.msgspec_json import ORJSON_AVAILABLE
except ImportError:
    ORJSON_AVAILABLE = False
from hledac.universal.utils.msgspec_json import decode, encode
try:
    import msgspec as _msgspec_lib
    _MSGSPEC_AVAILABLE = True
except ImportError:
    _msgspec_lib = None
    _MSGSPEC_AVAILABLE = False
try:
    import numpy as _np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    _np = None
np = _np
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
FASTEMBED_AVAILABLE = False
try:
    from compat.core_mlx_embeddings import MLXEmbeddingManager
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    logger.debug('MLXEmbeddingManager not available')
L1_MEMORY = 'l1_memory'
L2_DISK = 'l2_disk'
SEMANTIC = 'semantic'
COMPUTATION = 'computation'
QUERY = 'query'

def _list_to_ndarray(obj: Any, target_type: Any=None) -> Any:
    """Convert lists back to numpy arrays after JSON deserialization."""
    if NUMPY_AVAILABLE and isinstance(obj, list) and (target_type is not None):
        return _np.array(obj, dtype=target_type)
    if isinstance(obj, dict):
        return {k: _list_to_ndarray(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_list_to_ndarray(item) for item in obj]
    return obj

def _serialize_cache(data: dict[str, CacheEntry]) -> bytes:
    """Serialize cache data to bytes using msgspec.CacheEntry struct wire format + zstd.

    Sprint F264: each internal ``CacheEntry`` is wrapped as a typed
    ``utils.msgspec_json.CacheEntry(key, value, ttl=3600)`` struct; the
    full internal payload is JSON-encoded into the ``value`` field so
    no field (embeddings, enums, metadata) is lost in the wire format.
    Falls back to the untyped facade if msgspec is unavailable.
    """
    from hledac.universal.utils.msgspec_json import CacheEntry as _MsgspecCacheEntry
    if not _MSGSPEC_AVAILABLE or _msgspec_lib is None:
        return _serialize_cache_untyped(data)
    serializable: dict[str, _MsgspecCacheEntry] = {}
    for k, v in data.items():
        entry_dict = _entry_to_dict(v)
        serializable[k] = _MsgspecCacheEntry(key=v.cache_id, value=_msgspec_lib.json.encode(entry_dict).decode('utf-8'), ttl=3600)
    payload = _msgspec_lib.json.encode(serializable)
    if ZSTD_AVAILABLE and _zstd is not None:
        return _zstd.compress(payload)
    return payload

def _serialize_cache_untyped(data: dict[str, CacheEntry]) -> bytes:
    """Legacy untyped serializer — used only when msgspec is unavailable."""
    serializable: dict[str, dict[str, Any]] = {}
    for k, v in data.items():
        serializable[k] = _entry_to_dict(v)
    payload = encode(serializable)
    if ZSTD_AVAILABLE and _zstd is not None:
        return _zstd.compress(payload)
    return payload

def _deserialize_cache(data: bytes) -> dict[str, CacheEntry]:
    """Deserialize cache data from bytes via msgspec.CacheEntry typed fast path.

    Sprint F264: tries ``decode_typed(raw, dict[str, CacheEntry])`` first
    (zero-alloc typed decode). On ``msgspec.ValidationError`` (unknown
    fields, missing optionals, type drift) or any decode error, falls
    back to the untyped dict parser so on-disk legacy payloads keep
    working (schema-drift tolerance).
    """
    from hledac.universal.utils.msgspec_json import CacheEntry as _MsgspecCacheEntry
    from hledac.universal.utils.msgspec_json import decode_typed
    if ZSTD_AVAILABLE:
        try:
            payload: bytes = _zstd.decompress(data)
        except Exception:
            payload = data
    else:
        payload = data
    if _MSGSPEC_AVAILABLE and _msgspec_lib is not None:
        try:
            struct_map = decode_typed(payload, dict[str, _MsgspecCacheEntry])
            if isinstance(struct_map, dict):
                result: dict[str, CacheEntry] = {}
                for k, struct_entry in struct_map.items():
                    if not isinstance(struct_entry, _MsgspecCacheEntry):
                        raise _msgspec_lib.ValidationError('struct_map contains non-CacheEntry value')
                    try:
                        inner = _msgspec_lib.json.decode(struct_entry.value.encode('utf-8'))
                    except Exception:
                        continue
                    if not isinstance(inner, dict):
                        continue
                    result[k] = _dict_to_entry(inner, fallback_key=k)
                return result
        except (_msgspec_lib.ValidationError, _msgspec_lib.DecodeError, Exception):
            pass
    try:
        raw = decode(payload)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for k, v in raw.items():
        if not isinstance(v, dict) or 'cache_id' not in v:
            continue
        try:
            result[k] = _dict_to_entry(v, fallback_key=k)
        except Exception:
            continue
    return result

def _entry_to_dict(v: CacheEntry) -> dict[str, Any]:
    """Convert internal ``CacheEntry`` dataclass to a JSON-serializable dict."""
    return {'cache_id': v.cache_id, 'content': v.content, 'embedding': v.embedding.tolist() if v.embedding is not None else None, 'access_count': v.access_count, 'last_accessed': v.last_accessed, 'created_at': v.created_at, 'size_bytes': v.size_bytes, 'cache_type': v.cache_type.value if isinstance(v.cache_type, Enum) else v.cache_type, 'metadata': v.metadata}

def _dict_to_entry(v: dict[str, Any], fallback_key: str='') -> CacheEntry:
    """Reconstruct internal ``CacheEntry`` dataclass from a deserialized dict."""
    embedding_raw = v.get('embedding')
    cache_type_raw = v.get('cache_type')
    return CacheEntry(cache_id=v.get('cache_id', fallback_key), content=v.get('content'), embedding=np.array(embedding_raw) if embedding_raw is not None else None, access_count=v.get('access_count', 0), last_accessed=v.get('last_accessed', 0.0), created_at=v.get('created_at', 0.0), size_bytes=v.get('size_bytes', 0), cache_type=CacheType(cache_type_raw) if isinstance(cache_type_raw, str) else cache_type_raw, metadata=v.get('metadata') or {})

class CacheType(Enum):
    """Types of cache entries."""
    SEMANTIC = SEMANTIC
    COMPUTATION = COMPUTATION
    QUERY = QUERY

class CacheLocation(Enum):
    """Cache location levels."""
    L1_MEMORY = L1_MEMORY
    L2_DISK = L2_DISK

@dataclass(slots=True)
class CacheEntry:
    """Single cache entry."""
    cache_id: str
    content: Any
    embedding: np.ndarray | None
    access_count: int
    last_accessed: float
    created_at: float
    size_bytes: int
    cache_type: CacheType
    metadata: dict[str, Any]

@dataclass(slots=True)
class CacheStats:
    """Cache performance statistics."""
    total_entries: int
    l1_entries: int
    l2_entries: int
    hit_count: int
    miss_count: int
    hit_rate: float
    total_requests: int
    l1_size_mb: float
    l2_size_mb: float
    avg_similarity_score: float

class MultiLevelContextCache:
    """
    Multi-level context cache with FastEmbed (ONNX) backend.

    Model: BAAI/bge-small-en-v1.5 or snowflake/snowflake-arctic-embed-xs (~50-130MB)
    Backend: ONNX Runtime (quantized)
    Purpose: Multi-level caching with semantic similarity

    Advantages:
    - ~50MB vs ~420MB for PyTorch-based all-mpnet-base-v2
    - ONNX Runtime for M1 optimization
    - Instant loading, minimal cnew start penalty
    - Low memory footprint (~100MB peak)
    - L1 (memory) + L2 (disk) hierarchy
    """
    __slots__ = tuple(('_embedder_type', '_embedding_cache', '_mlx_manager', '_semantic_index', '_temp_l2_path', 'embedder', 'embedding_dim', 'embedding_model', 'embedding_to_cache_id', 'l1_cache', 'l1_max_size_bytes', 'l2_cache', 'l2_storage_path', 'max_entries', 'similarity_threshnew'))

    def __init__(self, embedding_model: str='snowflake/snowflake-arctic-embed-xs', l1_max_size_mb: float=100.0, l2_storage_path: str='cache_storage', similarity_threshnew: float | None=None, similarity_threshold: float | None=None, max_entries: int=10000):
        """
        Initialize multi-level cache.

        Args:
            embedding_model: FastEmbed model name
            l1_max_size_mb: Maximum L1 cache size in MB
            l2_storage_path: Path for L2 disk cache
            similarity_threshnew: Threshold for semantic similarity (legacy typo - use similarity_threshold)
            similarity_threshold: Threshold for semantic similarity (0.0-1.0)
            max_entries: Maximum total entries
        """
        effective_threshold: float = 0.95
        if similarity_threshold is not None:
            effective_threshold = max(0.0, min(1.0, similarity_threshold))
        elif similarity_threshnew is not None:
            effective_threshold = max(0.0, min(1.0, similarity_threshnew))
        self.embedding_model = embedding_model
        self.embedder = None
        self.embedding_dim = None
        self._embedder_type = None
        self._temp_l2_path = l2_storage_path
        if MLX_EMBED_AVAILABLE:
            try:
                from compat.core_mlx_embeddings import get_embedding_manager
                self._mlx_manager = get_embedding_manager()
                self.embedder = self._mlx_manager
                self.embedding_dim = self._mlx_manager.EMBEDDING_DIM
                self._embedder_type = 'mlx'
                logger.info(f'[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_manager.model_path}, dim={self.embedding_dim}')
            except Exception as e:
                logger.warning(f'MLXEmbeddingManager init failed: {e}, using dummy embeddings')
                self._mlx_manager = None
                self.embedder = None
                self.embedding_dim = 384
                self._embedder_type = None
        elif FASTEMBED_AVAILABLE:
            self._initialize_embedder()
        else:
            logger.warning('MLXEmbeddingManager not available, using dummy embeddings')
            self.embedding_dim = 384
        self.l1_max_size_bytes = int(l1_max_size_mb * 1024 * 1024)
        self.l2_storage_path = Path(l2_storage_path)
        self.l2_storage_path.mkdir(parents=True, exist_ok=True)
        self.similarity_threshnew = effective_threshold
        self.max_entries = max_entries
        self.l1_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.l2_cache: dict[str, CacheEntry] = {}
        self._semantic_index = None
        self.embedding_to_cache_id: dict[int, str] = {}
        self._embedding_cache: dict[str, Any] = {}

    @property
    def semantic_index(self):
        """Lazy-loaded FAISS semantic index."""
        if self._semantic_index is None:
            import faiss
            self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        return self._semantic_index

    def _ensure_faiss(self):
        """Ensure faiss is imported before use."""
        if self._semantic_index is None:
            import faiss
            self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        self.stats: dict[str, Any] = {'hits': 0, 'misses': 0, 'total_requests': 0, 'l1_promotions': 0, 'l2_demotions': 0, 'evictions': 0, 'similarities': []}
        self._lock = threading.RLock()
        self._load_l2_cache()
        self._rebuild_semantic_index()

    def _load_l2_cache(self):
        """Load L2 cache from disk. Prefer zstd-compressed .json.zst, fallback to .json."""
        try:
            zst_file = self.l2_storage_path / 'l2_cache.json.zst'
            json_file = self.l2_storage_path / 'l2_cache.json'
            if zst_file.exists():
                with open(zst_file, 'rb') as f:
                    compressed_bytes = f.read()
                if len(compressed_bytes) > 20 * 1024 * 1024:
                    logger.warning('Context L2 cache too large — skipping')
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_cache(compressed_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.zst)')
            elif json_file.exists():
                with open(json_file, 'rb') as f:
                    cache_bytes = f.read()
                if len(cache_bytes) > 20 * 1024 * 1024:
                    logger.warning('Context L2 cache too large — skipping')
                    self.l2_cache = {}
                else:
                    self.l2_cache = _deserialize_cache(cache_bytes)
                logger.info(f'Loaded {len(self.l2_cache)} entries from L2 cache (.json legacy)')
            else:
                self.l2_cache = {}
        except Exception as e:
            logger.warning(f'Could not load L2 cache: {e}')
            self.l2_cache = {}

    def _save_l2_cache(self):
        """Save L2 cache to disk as zstd-compressed .json.zst."""
        try:
            cache_file = self.l2_storage_path / 'l2_cache.json.zst'
            with open(cache_file, 'wb') as f:
                f.write(_serialize_cache(self.l2_cache))
        except Exception as e:
            logger.warning(f'Could not save L2 cache: {e}')

    def _rebuild_semantic_index(self):
        """Rebuild semantic index from existing cache entries."""
        import faiss
        self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        self.embedding_to_cache_id.clear()
        all_entries = list(self.l1_cache.values()) + list(self.l2_cache.values())
        for entry in all_entries:
            if entry.embedding is not None:
                embedding_id = len(self.embedding_to_cache_id)
                self.embedding_to_cache_id[embedding_id] = entry.cache_id
                self._semantic_index.add(entry.embedding.reshape(1, -1).astype('float32'))

    def _generate_cache_id(self, content: Any) -> str:
        """Generate unique cache ID for content."""
        content_str = str(content)
        return hashlib.md5(content_str.encode()).hexdigest()[:16]

    def _estimate_size(self, content: Any) -> int:
        """Estimate size of content in bytes."""
        import sys
        return sys.getsizeof(content)

    def _get_embedding(self, text: str) -> np.ndarray | None:
        """Get embedding for text (uses query task for retrieval).

        F320-Issue2: Results are cached by NFC-normalized text to avoid
        re-encoding the same string across cycles."""
        import unicodedata
        normalized = unicodedata.normalize('NFC', text)
        cached = self._embedding_cache.get(normalized)
        if cached is not None:
            return cached
        if self.embedder is None:
            return None
        try:
            if self._embedder_type == 'mlx':
                if hasattr(self.embedder, 'embed_query'):
                    result = self.embedder.embed_query(text)
                else:
                    result = self.embedder.encode(text)
                if hasattr(result, 'tolist'):
                    return _np.array(result.tolist()) if NUMPY_AVAILABLE else None
                return _np.array(result) if NUMPY_AVAILABLE else None
            else:
                embeddings = list(self.embedder.embed([text]))
                if embeddings:
                    return _np.array(embeddings[0]) if NUMPY_AVAILABLE else None
        except Exception as e:
            logger.warning(f'Embedding failed: {e}')
        self._embedding_cache[normalized] = result
        return result

    async def get(self, input_data: Any, cache_type: CacheType=CacheType.COMPUTATION, threshnew: float | None=None) -> Any | None:
        """
        Get cached result or compute if not cached.

        Args:
            input_data: Input data to cache
            cache_type: Type of cache
            threshnew: Custom similarity threshnew

        Returns:
            Cached content or None if not found
        """
        if threshnew is None:
            threshnew = self.similarity_threshnew
        with self._lock:
            self.stats['total_requests'] += 1
        input_text = str(input_data)
        similar_entry = await self._find_similar_entry(input_text, threshnew)
        if similar_entry:
            with self._lock:
                self.stats['hits'] += 1
                self._update_access(similar_entry.cache_id)
                if similar_entry.cache_id in self.l2_cache:
                    self._promote_to_l1(similar_entry.cache_id)
            return similar_entry.content
        return None

    async def _find_similar_entry(self, input_text: str, threshnew: float) -> CacheEntry | None:
        """Find semantically similar cache entry."""
        input_embedding = self._get_embedding(input_text)
        if input_embedding is None:
            return None
        query_embedding = input_embedding.reshape(1, -1).astype('float32')
        distances, indices = self.semantic_index.search(query_embedding, 10)
        for idx, similarity in zip(indices[0], distances[0], strict=False):
            if float(similarity) >= threshnew:
                cache_id = self.embedding_to_cache_id.get(int(idx))
                if not cache_id:
                    continue
                entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                if entry:
                    self.stats['similarities'].append(float(similarity))
                    return entry
        return None

    async def set(self, input_data: Any, content: Any, cache_type: CacheType=CacheType.COMPUTATION):
        """
        Cache a computation result.

        Args:
            input_data: Input data
            content: Computation result to cache
            cache_type: Type of cache
        """
        cache_id = self._generate_cache_id(input_data)
        if cache_id in self.l1_cache or cache_id in self.l2_cache:
            return
        input_text = str(input_data)
        embedding = self._get_embedding(input_text)
        cache_entry = CacheEntry(cache_id=cache_id, content=content, embedding=embedding, access_count=1, last_accessed=time.time(), created_at=time.time(), size_bytes=self._estimate_size(content), cache_type=cache_type, metadata={})
        with self._lock:
            if embedding is not None:
                embedding_id = len(self.embedding_to_cache_id)
                self.embedding_to_cache_id[embedding_id] = cache_id
                self.semantic_index.add(embedding.reshape(1, -1).astype('float32'))
            if self._get_l1_size_bytes() + cache_entry.size_bytes <= self.l1_max_size_bytes:
                self.l1_cache[cache_id] = cache_entry
            else:
                self.l2_cache[cache_id] = cache_entry
                self._save_l2_cache()
            self._check_eviction()

    def _update_access(self, cache_id: str):
        """Update access statistics for cache entry."""
        current_time = time.time()
        if cache_id in self.l1_cache:
            entry = self.l1_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            self.l1_cache.move_to_end(cache_id)
        elif cache_id in self.l2_cache:
            entry = self.l2_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time

    def _promote_to_l1(self, cache_id: str):
        """Promote entry from L2 to L1 cache."""
        entry = self.l2_cache.pop(cache_id)
        if self._get_l1_size_bytes() + entry.size_bytes <= self.l1_max_size_bytes:
            self.l1_cache[cache_id] = entry
            self.stats['l1_promotions'] += 1
        else:
            self.l2_cache[cache_id] = entry
        self._save_l2_cache()

    def _evict_from_l1(self):
        """Evict least recently used entries from L1."""
        evict_count = max(1, len(self.l1_cache) // 5)
        for _ in range(evict_count):
            if not self.l1_cache:
                break
            cache_id, entry = self.l1_cache.popitem(last=False)
            self.l2_cache[cache_id] = entry
            self.stats['l2_demotions'] += 1
        self._save_l2_cache()

    def _check_eviction(self):
        """Check and perform eviction if necessary."""
        total_entries = len(self.l1_cache) + len(self.l2_cache)
        if total_entries > self.max_entries:
            self._evict_least_valuable()
        if self._get_l1_size_bytes() > self.l1_max_size_bytes:
            self._evict_from_l1()

    def _evict_least_valuable(self):
        """Evict least valuable cache entries considering multiple factors."""
        all_entries = []
        for cache_id, entry in self.l1_cache.items():
            score = self._calculate_eviction_score(entry, is_l1=True)
            all_entries.append((cache_id, entry, score))
        for cache_id, entry in self.l2_cache.items():
            score = self._calculate_eviction_score(entry, is_l1=False)
            all_entries.append((cache_id, entry, score))
        all_entries.sort(key=lambda x: x[2])
        target_count = max(1, int(len(all_entries) * 0.1))
        evicted = 0
        for cache_id, entry, score in all_entries:
            if evicted >= target_count:
                break
            if cache_id in self.l1_cache:
                self.l1_cache.pop(cache_id)
                self._remove_from_semantic_index(cache_id)
                evicted += 1
            elif cache_id in self.l2_cache:
                self.l2_cache.pop(cache_id)
                self._remove_from_semantic_index(cache_id)
                evicted += 1
        self.stats['evictions'] += evicted
        self._save_l2_cache()

    def _calculate_eviction_score(self, entry: CacheEntry, is_l1: bool) -> float:
        """Calculate eviction score for entry."""
        current_time = time.time()
        age_hours = (current_time - entry.created_at) / 3600
        recency_score = 1.0 / (1.0 + age_hours)
        frequency_score = min(1.0, entry.access_count / 10.0)
        size_penalty = 1.0 / (1.0 + entry.size_bytes / (1024 * 1024))
        location_bonus = 1.2 if is_l1 else 1.0
        return (recency_score * 0.4 + frequency_score * 0.3 + size_penalty * 0.2) * location_bonus

    def _remove_from_semantic_index(self, cache_id: str):
        """Remove entry from semantic index."""
        embedding_ids_to_remove = []
        for embedding_id, cid in self.embedding_to_cache_id.items():
            if cid == cache_id:
                embedding_ids_to_remove.append(embedding_id)
        for embedding_id in embedding_ids_to_remove:
            del self.embedding_to_cache_id[embedding_id]
        if len(embedding_ids_to_remove) > 10:
            self._rebuild_semantic_index()

    def _get_l1_size_bytes(self) -> int:
        """Get current L1 cache size in bytes."""
        return sum((entry.size_bytes for entry in self.l1_cache.values()))

    def _get_l2_size_bytes(self) -> int:
        """Get current L2 cache size in bytes."""
        return sum((entry.size_bytes for entry in self.l2_cache.values()))

    async def invalidate(self, input_data: Any, cache_type: CacheType=CacheType.COMPUTATION):
        """Invalidate cache entry."""
        cache_id = self._generate_cache_id(input_data)
        invalidated = False
        with self._lock:
            if cache_id in self.l1_cache:
                del self.l1_cache[cache_id]
                invalidated = True
            if cache_id in self.l2_cache:
                del self.l2_cache[cache_id]
                invalidated = True
            if invalidated:
                self._remove_from_semantic_index(cache_id)
                self._save_l2_cache()

    def clear(self, cache_type: CacheType | None=None):
        """Clear cache entries."""
        with self._lock:
            if cache_type is None:
                self.l1_cache.clear()
                self.l2_cache.clear()
                self._rebuild_semantic_index()
            else:
                to_remove = []
                for cache_id, entry in self.l1_cache.items():
                    if entry.cache_type == cache_type:
                        to_remove.append(cache_id)
                for cache_id in to_remove:
                    del self.l1_cache[cache_id]
                    self._remove_from_semantic_index(cache_id)
                to_remove = []
                for cache_id, entry in self.l2_cache.items():
                    if entry.cache_type == cache_type:
                        to_remove.append(cache_id)
                for cache_id in to_remove:
                    del self.l2_cache[cache_id]
                    self._remove_from_semantic_index(cache_id)
                self._save_l2_cache()

    def get_stats(self) -> CacheStats:
        """Get comprehensive cache statistics."""
        total_requests = max(1, self.stats['total_requests'])
        hit_rate = self.stats['hits'] / total_requests
        avg_similarity = 0.0
        if self.stats['similarities']:
            avg_similarity = statistics.mean(self.stats['similarities'])
        return CacheStats(total_entries=len(self.l1_cache) + len(self.l2_cache), l1_entries=len(self.l1_cache), l2_entries=len(self.l2_cache), hit_count=self.stats['hits'], miss_count=self.stats['misses'], hit_rate=hit_rate, total_requests=total_requests, l1_size_mb=self._get_l1_size_bytes() / (1024 * 1024), l2_size_mb=self._get_l2_size_bytes() / (1024 * 1024), avg_similarity_score=avg_similarity)

    async def warm_cache(self, inputs: list[Any], compute_func: Callable, cache_type: CacheType=CacheType.COMPUTATION):
        """Warm cache with pre-computed results."""
        print(f'Warming cache with {len(inputs)} entries...')
        tasks = []
        for input_data in inputs:
            cached = await self.get(input_data, cache_type)
            if cached is None:
                tasks.append(compute_func(input_data))
        results = await safe_gather_ok(*tasks, label='context_cache:789')
        for input_data, result in zip(inputs, results, strict=False):
            await self.set(input_data, result, cache_type)
        print('Cache warming complete')

def cache_decorator(cache: MultiLevelContextCache):
    """Decorator for caching function results."""

    def decorator(func):

        @wraps(func)
        async def wrapper(*args, **kwargs):
            input_data = (func.__name__, args, kwargs)
            cached = await cache.get(input_data)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            await cache.set(input_data, result)
            return result
        return wrapper
    return decorator

class CacheManager:
    """Manager for multiple cache instances."""
    __slots__ = tuple(('caches',))

    def __init__(self):
        self.caches: dict[str, MultiLevelContextCache] = {}

    def register_cache(self, name: str, cache: MultiLevelContextCache):
        """Register a cache instance."""
        self.caches[name] = cache

    def get_cache(self, name: str) -> MultiLevelContextCache | None:
        """Get registered cache."""
        return self.caches.get(name)

    def clear_all(self):
        """Clear all registered caches."""
        for cache in self.caches.values():
            cache.clear()

    def get_all_stats(self) -> dict[str, CacheStats]:
        """Get statistics for all caches."""
        return {name: cache.get_stats() for name, cache in self.caches.items()}
_cache_manager = CacheManager()

def get_cache_manager() -> CacheManager:
    """Get global cache manager."""
    return _cache_manager
_global_context_cache = MultiLevelContextCache(l1_max_size_mb=128.0, l2_storage_path=str(Path.home() / '.cache' / 'hledac' / 'context_cache'))

def cached_context(func=None, *, exclude_self: bool=True, cache_type: CacheType=CacheType.QUERY):
    """
    Convenience decorator for caching method results using global cache.

    Args:
        func: Function to decorate (used when called without params)
        exclude_self: If True, exclude 'self' argument from cache key
        cache_type: Type of cache entry

    Usage:
        @cached_context
        async def search(self, query: str):
            ...

        @cached_context(cache_type=CacheType.SEMANTIC)
        async def get_related(self, node_id: str):
            ...
    """

    def decorator(f):

        @wraps(f)
        async def wrapper(*args, **kwargs):
            if exclude_self and args:
                cache_args = (f.__name__,) + args[1:] + (kwargs,)
            else:
                cache_args = (f.__name__, args, kwargs)
            cached = await _global_context_cache.get(cache_args, cache_type)
            if cached is not None:
                return cached
            result = await f(*args, **kwargs)
            await _global_context_cache.set(cache_args, result, cache_type)
            return result
        return wrapper
    if func is not None:
        return decorator(func)
    else:
        return decorator