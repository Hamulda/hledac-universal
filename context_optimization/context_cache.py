"""
Multi-level Context Cache with FastEmbed (ONNX)
=========================================

OPTIMIZED: PyTorch backend removed in favor of ONNX Runtime via FastEmbed

This module provides memory-efficient multi-level caching using FastEmbed
with ONNX runtime, optimized for M1 MacBook Air (8GB RAM).

FastEmbed uses quantized ONNX models for maximum inference speed
and minimal memory footprint (~50MB vs ~420MB for PyTorch).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import statistics
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

try:
    import compression.zstd as _zstd
    ZSTD_AVAILABLE = True
except (ImportError, Exception):
    ZSTD_AVAILABLE = False
    _zstd = None

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json

try:
    import numpy as _np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    _np = None

# np is the internal alias; if numpy unavailable, raise ImportError at first use
np = _np

# Lazy imports for memory efficiency - faiss only loaded when needed
if TYPE_CHECKING:
    import faiss

logger = logging.getLogger(__name__)

try:
    from fastembed import TextEmbedding
    FASTEMBED_AVAILABLE = True
except ImportError:
    FASTEMBED_AVAILABLE = False
    logger.warning("FastEmbed not installed. Install with: pip install fastembed")

# MLX Embedding Manager (primary path for M1)
try:
    from hledac.core.mlx_embeddings import MLXEmbeddingManager
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    logger.debug("MLXEmbeddingManager not available")

L1_MEMORY = "l1_memory"
L2_DISK = "l2_disk"

SEMANTIC = "semantic"
COMPUTATION = "computation"
QUERY = "query"


def _ndarray_to_list(obj: Any) -> Any:
    """Convert numpy arrays to lists for JSON serialization."""
    if NUMPY_AVAILABLE and isinstance(obj, _np.ndarray):
        return obj.tolist()
    if is_dataclass(obj):
        return {k: _ndarray_to_list(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _ndarray_to_list(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ndarray_to_list(item) for item in obj]
    return obj


def _list_to_ndarray(obj: Any, target_type: Any = None) -> Any:
    """Convert lists back to numpy arrays after JSON deserialization."""
    if NUMPY_AVAILABLE and isinstance(obj, list) and target_type is not None:
        return _np.array(obj, dtype=target_type)
    if isinstance(obj, dict):
        return {k: _list_to_ndarray(v) for k, v in obj.items()}
    if isinstance(obj, list):
        # Heuristic: if it looks like an embedding (float list), keep as list
        return [_list_to_ndarray(item) for item in obj]
    return obj


def _serialize_cache(data: Dict[str, CacheEntry]) -> bytes:
    """Serialize cache data to bytes using orjson, compressed with zstd."""
    serializable = {}
    for k, v in data.items():
        entry_dict = {
            'cache_id': v.cache_id,
            'content': v.content,
            'embedding': v.embedding.tolist() if v.embedding is not None else None,
            'access_count': v.access_count,
            'last_accessed': v.last_accessed,
            'created_at': v.created_at,
            'size_bytes': v.size_bytes,
            'cache_type': v.cache_type.value if isinstance(v.cache_type, Enum) else v.cache_type,
            'metadata': v.metadata,
        }
        serializable[k] = entry_dict
    payload = orjson.dumps(serializable) if ORJSON_AVAILABLE else _json.dumps(serializable).encode()
    if ZSTD_AVAILABLE and _zstd is not None:
        return _zstd.compress(payload)
    return payload


def _deserialize_cache(data: bytes) -> Dict[str, CacheEntry]:
    """Deserialize cache data from bytes; zstd-compressed or raw orjson JSON."""
    raw: Any
    if ZSTD_AVAILABLE:
        try:
            raw = orjson.loads(_zstd.decompress(data))
        except Exception:
            # Backward compat: try raw JSON (old .json files)
            raw = orjson.loads(data) if ORJSON_AVAILABLE else _json.loads(data.decode())
    else:
        raw = orjson.loads(data) if ORJSON_AVAILABLE else _json.loads(data.decode())

    result = {}
    for k, v in raw.items():
        result[k] = CacheEntry(
            cache_id=v['cache_id'],
            content=v['content'],
            embedding=np.array(v['embedding']) if v['embedding'] is not None else None,
            access_count=v['access_count'],
            last_accessed=v['last_accessed'],
            created_at=v['created_at'],
            size_bytes=v['size_bytes'],
            cache_type=CacheType(v['cache_type']) if isinstance(v['cache_type'], str) else v['cache_type'],
            metadata=v['metadata'],
        )
    return result


class CacheType(Enum):
    """Types of cache entries."""
    SEMANTIC = SEMANTIC
    COMPUTATION = COMPUTATION
    QUERY = QUERY


class CacheLocation(Enum):
    """Cache location levels."""
    L1_MEMORY = L1_MEMORY
    L2_DISK = L2_DISK


@dataclass
class CacheEntry:
    """Single cache entry."""
    cache_id: str
    content: Any
    embedding: Optional[np.ndarray]
    access_count: int
    last_accessed: float
    created_at: float
    size_bytes: int
    cache_type: CacheType
    metadata: Dict[str, Any]


@dataclass
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
    
    def __init__(
        self,
        embedding_model: str = "snowflake/snowflake-arctic-embed-xs",
        l1_max_size_mb: float = 100.0,
        l2_storage_path: str = "cache_storage",
        similarity_threshnew: Optional[float] = None,
        similarity_threshold: Optional[float] = None,
        max_entries: int = 10000
    ):
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
        # Resolve threshold: prefer similarity_threshold, fallback to similarity_threshnew (legacy)
        effective_threshold: float = 0.95
        if similarity_threshold is not None:
            effective_threshold = max(0.0, min(1.0, similarity_threshold))
        elif similarity_threshnew is not None:
            effective_threshold = max(0.0, min(1.0, similarity_threshnew))

        # Embedding model - initialize BEFORE cache config (for FastEmbed cache_dir)
        self.embedding_model = embedding_model
        self.embedder = None
        self.embedding_dim = None
        self._embedder_type = None  # Track which embedder is used
        self._temp_l2_path = l2_storage_path  # Store for FastEmbed init

        # Initialize MLX Embedding Manager (primary for M1) or fallback to FastEmbed
        # Use shared singleton to avoid duplicate model loads
        if MLX_EMBED_AVAILABLE:
            try:
                from hledac.core.mlx_embeddings import get_embedding_manager
                self._mlx_manager = get_embedding_manager()
                self.embedder = self._mlx_manager
                self.embedding_dim = self._mlx_manager.EMBEDDING_DIM
                self._embedder_type = 'mlx'
                logger.info(f"[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_manager.model_path}, dim={self.embedding_dim}")
            except Exception as e:
                logger.warning(f"MLXEmbeddingManager init failed: {e}, falling back to FastEmbed")
                self._mlx_manager = None
                if FASTEMBED_AVAILABLE:
                    self._initialize_embedder()
                else:
                    logger.warning("FastEmbed not available, using dummy embeddings")
                    self.embedding_dim = 384
        elif FASTEMBED_AVAILABLE:
            self._initialize_embedder()
        else:
            logger.warning("FastEmbed not available, using dummy embeddings")
            self.embedding_dim = 384

        # Cache configuration
        self.l1_max_size_bytes = int(l1_max_size_mb * 1024 * 1024)
        self.l2_storage_path = Path(l2_storage_path)
        self.l2_storage_path.mkdir(parents=True, exist_ok=True)
        self.similarity_threshnew = effective_threshold  # Keep internal name for serialization stability
        self.max_entries = max_entries
        
        # Multi-level storage
        self.l1_cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.l2_cache: Dict[str, CacheEntry] = {}
        
        # Semantic search structures - lazy loaded
        self._semantic_index = None
        self.embedding_to_cache_id: Dict[int, str] = {}

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
        
        # Performance statistics
        self.stats: Dict[str, Any] = {
            "hits": 0,
            "misses": 0,
            "total_requests": 0,
            "l1_promotions": 0,
            "l2_demotions": 0,
            "evictions": 0,
            "similarities": []
        }
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Load existing L2 cache
        self._load_l2_cache()
        
        # Initialize FAISS index with existing embeddings
        self._rebuild_semantic_index()
    
    def _initialize_embedder(self):
        """Initialize FastEmbed embedder with minimal memory usage."""
        try:
            logger.info(f"Initializing FastEmbed embedder: {self.embedding_model}")

            # Use _temp_l2_path since l2_storage_path not set yet during init
            cache_path = self._temp_l2_path if hasattr(self, '_temp_l2_path') else "cache_storage"
            self.embedder = TextEmbedding(
                model_name=self.embedding_model,
                cache_dir=str(Path(cache_path) / "embeddings"),
                threads=4  # Optimize for M1
            )

            self.embedding_dim = self.embedder.embedding_size
            self._embedder_type = 'fastembed'
            logger.info(f"✅ FastEmbed embedder loaded (model: ~50MB, dim: {self.embedding_dim})")

        except Exception as e:
            logger.error(f"Failed to initialize FastEmbed: {e}")
            self.embedder = None
            self.embedding_dim = 384
    
    def _load_l2_cache(self):
        """Load L2 cache from disk. Prefer zstd-compressed .json.zst, fallback to .json."""
        try:
            zst_file = self.l2_storage_path / "l2_cache.json.zst"
            json_file = self.l2_storage_path / "l2_cache.json"
            if zst_file.exists():
                with open(zst_file, 'rb') as f:
                    self.l2_cache = _deserialize_cache(f.read())
                logger.info(f"Loaded {len(self.l2_cache)} entries from L2 cache (.zst)")
            elif json_file.exists():
                # Backward compat: read old plain JSON
                with open(json_file, 'rb') as f:
                    self.l2_cache = _deserialize_cache(f.read())
                logger.info(f"Loaded {len(self.l2_cache)} entries from L2 cache (.json legacy)")
            else:
                self.l2_cache = {}
        except Exception as e:
            logger.warning(f"Could not load L2 cache: {e}")
            self.l2_cache = {}

    def _save_l2_cache(self):
        """Save L2 cache to disk as zstd-compressed .json.zst."""
        try:
            cache_file = self.l2_storage_path / "l2_cache.json.zst"
            with open(cache_file, 'wb') as f:
                f.write(_serialize_cache(self.l2_cache))
        except Exception as e:
            logger.warning(f"Could not save L2 cache: {e}")
    
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
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding for text (uses query task for retrieval)."""
        if self.embedder is None:
            return None

        try:
            if self._embedder_type == 'mlx':
                # Sprint 87: Use embed_query() for retrieval (SEARCH_QUERY task)
                if hasattr(self.embedder, 'embed_query'):
                    result = self.embedder.embed_query(text)
                else:
                    result = self.embedder.encode(text)
                if hasattr(result, 'tolist'):
                    return _np.array(result.tolist()) if NUMPY_AVAILABLE else None
                return _np.array(result) if NUMPY_AVAILABLE else None
            else:
                # FastEmbed uses .embed()
                embeddings = list(self.embedder.embed([text]))
                if embeddings:
                    return _np.array(embeddings[0]) if NUMPY_AVAILABLE else None
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
        return None
    
    async def get(
        self,
        input_data: Any,
        cache_type: CacheType = CacheType.COMPUTATION,
        threshnew: Optional[float] = None
    ) -> Optional[Any]:
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
            self.stats["total_requests"] += 1
        
        # Convert input to string for embedding
        input_text = str(input_data)
        
        # Check semantic cache for similar entries
        similar_entry = await self._find_similar_entry(input_text, threshnew)
        
        if similar_entry:
            # Cache hit
            with self._lock:
                self.stats["hits"] += 1
                self._update_access(similar_entry.cache_id)
                
                # Promote to L1 if not already there
                if similar_entry.cache_id in self.l2_cache:
                    self._promote_to_l1(similar_entry.cache_id)
            
            return similar_entry.content
        
        # Cache miss - compute new result
        return None
    
    async def _find_similar_entry(
        self,
        input_text: str,
        threshnew: float
    ) -> Optional[CacheEntry]:
        """Find semantically similar cache entry."""
        input_embedding = self._get_embedding(input_text)
        
        if input_embedding is None:
            return None
        
        # Search for similar embeddings
        query_embedding = input_embedding.reshape(1, -1).astype('float32')
        D, I = self.semantic_index.search(query_embedding, 10)
        
        # Check if any similarity meets threshnew
        for idx, similarity in zip(I[0], D[0]):
            if float(similarity) >= threshnew:
                cache_id = self.embedding_to_cache_id.get(int(idx))
                
                if not cache_id:
                    continue
                
                # Get entry from L1 or L2
                entry = self.l1_cache.get(cache_id, self.l2_cache.get(cache_id))
                
                if entry:
                    # Record similarity for statistics
                    self.stats["similarities"].append(float(similarity))
                    return entry
        
        return None
    
    async def set(
        self,
        input_data: Any,
        content: Any,
        cache_type: CacheType = CacheType.COMPUTATION
    ):
        """
        Cache a computation result.
        
        Args:
            input_data: Input data
            content: Computation result to cache
            cache_type: Type of cache
        """
        # Generate cache ID
        cache_id = self._generate_cache_id(input_data)
        
        # Don't cache if already exists
        if cache_id in self.l1_cache or cache_id in self.l2_cache:
            return
        
        # Create cache entry
        input_text = str(input_data)
        embedding = self._get_embedding(input_text)
        
        cache_entry = CacheEntry(
            cache_id=cache_id,
            content=content,
            embedding=embedding,
            access_count=1,
            last_accessed=time.time(),
            created_at=time.time(),
            size_bytes=self._estimate_size(content),
            cache_type=cache_type,
            metadata={}
        )
        
        with self._lock:
            # Add to semantic index
            if embedding is not None:
                embedding_id = len(self.embedding_to_cache_id)
                self.embedding_to_cache_id[embedding_id] = cache_id
                self.semantic_index.add(embedding.reshape(1, -1).astype('float32'))
            
            # Add to L1 if space available
            if self._get_l1_size_bytes() + cache_entry.size_bytes <= self.l1_max_size_bytes:
                self.l1_cache[cache_id] = cache_entry
            else:
                # Add to L2
                self.l2_cache[cache_id] = cache_entry
                self._save_l2_cache()
            
            # Check eviction
            self._check_eviction()
    
    def _update_access(self, cache_id: str):
        """Update access statistics for cache entry."""
        current_time = time.time()
        
        if cache_id in self.l1_cache:
            entry = self.l1_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
            # Move to end (LRU)
            self.l1_cache.move_to_end(cache_id)
        elif cache_id in self.l2_cache:
            entry = self.l2_cache[cache_id]
            entry.access_count += 1
            entry.last_accessed = current_time
    
    def _promote_to_l1(self, cache_id: str):
        """Promote entry from L2 to L1 cache."""
        entry = self.l2_cache.pop(cache_id)
        
        # Check if L1 has space
        if self._get_l1_size_bytes() + entry.size_bytes <= self.l1_max_size_bytes:
            self.l1_cache[cache_id] = entry
            self.stats["l1_promotions"] += 1
        else:
            self.l2_cache[cache_id] = entry
        
        self._save_l2_cache()
    
    def _evict_from_l1(self):
        """Evict least recently used entries from L1."""
        # Evict 20% of entries
        evict_count = max(1, len(self.l1_cache) // 5)
        
        for _ in range(evict_count):
            if not self.l1_cache:
                break
            
            cache_id, entry = self.l1_cache.popitem(last=False)
            # Move to L2
            self.l2_cache[cache_id] = entry
            self.stats["l2_demotions"] += 1
        
        self._save_l2_cache()
    
    def _check_eviction(self):
        """Check and perform eviction if necessary."""
        total_entries = len(self.l1_cache) + len(self.l2_cache)
        
        # Evict if over max entries
        if total_entries > self.max_entries:
            self._evict_least_valuable()
        
        # Ensure L1 size limit
        if self._get_l1_size_bytes() > self.l1_max_size_bytes:
            self._evict_from_l1()
    
    def _evict_least_valuable(self):
        """Evict least valuable cache entries considering multiple factors."""
        all_entries = []
        
        # Collect all entries with their scores
        for cache_id, entry in self.l1_cache.items():
            score = self._calculate_eviction_score(entry, is_l1=True)
            all_entries.append((cache_id, entry, score))
        
        for cache_id, entry in self.l2_cache.items():
            score = self._calculate_eviction_score(entry, is_l1=False)
            all_entries.append((cache_id, entry, score))
        
        # Sort by score (lowest = least valuable)
        all_entries.sort(key=lambda x: x[2])
        
        # Evict bottom 10% or until under limit
        target_count = max(1, int(len(all_entries) * 0.1))
        evicted = 0
        
        for cache_id, entry, score in all_entries:
            if evicted >= target_count:
                break
            
            if cache_id in self.l1_cache:
                self.l1_cache.pop(cache_id)
                # Remove from semantic index
                self._remove_from_semantic_index(cache_id)
                evicted += 1
            elif cache_id in self.l2_cache:
                self.l2_cache.pop(cache_id)
                # Remove from semantic index
                self._remove_from_semantic_index(cache_id)
                evicted += 1
        
        self.stats["evictions"] += evicted
        self._save_l2_cache()
    
    def _calculate_eviction_score(self, entry: CacheEntry, is_l1: bool) -> float:
        """Calculate eviction score for entry."""
        current_time = time.time()
        age_hours = (current_time - entry.created_at) / 3600
        
        # Recency factor (newer = lower score)
        recency_score = 1.0 / (1.0 + age_hours)
        
        # Frequency factor (more access = higher score)
        frequency_score = min(1.0, entry.access_count / 10.0)
        
        # Size factor (larger = lower score)
        size_penalty = 1.0 / (1.0 + entry.size_bytes / (1024 * 1024))
        
        # L1 preference
        location_bonus = 1.2 if is_l1 else 1.0
        
        # Combined score
        return (recency_score * 0.4 + 
                frequency_score * 0.3 + 
                size_penalty * 0.2) * location_bonus
    
    def _remove_from_semantic_index(self, cache_id: str):
        """Remove entry from semantic index."""
        # Find embedding ID for cache ID
        embedding_ids_to_remove = []
        for embedding_id, cid in self.embedding_to_cache_id.items():
            if cid == cache_id:
                embedding_ids_to_remove.append(embedding_id)
        
        # Remove mappings (FAISS doesn't support removal, so we'll rebuild)
        for embedding_id in embedding_ids_to_remove:
            del self.embedding_to_cache_id[embedding_id]
        
        # Rebuild index if many removals
        if len(embedding_ids_to_remove) > 10:
            self._rebuild_semantic_index()
    
    def _get_l1_size_bytes(self) -> int:
        """Get current L1 cache size in bytes."""
        return sum(entry.size_bytes for entry in self.l1_cache.values())
    
    def _get_l2_size_bytes(self) -> int:
        """Get current L2 cache size in bytes."""
        return sum(entry.size_bytes for entry in self.l2_cache.values())
    
    async def invalidate(self, input_data: Any, cache_type: CacheType = CacheType.COMPUTATION):
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
    
    def clear(self, cache_type: Optional[CacheType] = None):
        """Clear cache entries."""
        with self._lock:
            if cache_type is None:
                # Clear all
                self.l1_cache.clear()
                self.l2_cache.clear()
                self._rebuild_semantic_index()
            else:
                # Clear specific type
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
        total_requests = max(1, self.stats["total_requests"])
        hit_rate = self.stats["hits"] / total_requests
        
        avg_similarity = 0.0
        if self.stats["similarities"]:
            avg_similarity = statistics.mean(self.stats["similarities"])
        
        return CacheStats(
            total_entries=len(self.l1_cache) + len(self.l2_cache),
            l1_entries=len(self.l1_cache),
            l2_entries=len(self.l2_cache),
            hit_count=self.stats["hits"],
            miss_count=self.stats["misses"],
            hit_rate=hit_rate,
            total_requests=total_requests,
            l1_size_mb=self._get_l1_size_bytes() / (1024 * 1024),
            l2_size_mb=self._get_l2_size_bytes() / (1024 * 1024),
            avg_similarity_score=avg_similarity
        )
    
    async def warm_cache(
        self,
        inputs: List[Any],
        compute_func: Callable,
        cache_type: CacheType = CacheType.COMPUTATION
    ):
        """Warm cache with pre-computed results."""
        print(f"Warming cache with {len(inputs)} entries...")
        
        tasks = []
        for input_data in inputs:
            # Check if already cached
            cached = await self.get(input_data, cache_type)
            if cached is None:
                # Compute and cache
                tasks.append(compute_func(input_data))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for input_data, result in zip(inputs, results):
            await self.set(input_data, result, cache_type)
        
        print("Cache warming complete")


def cache_decorator(cache: MultiLevelContextCache):
    """Decorator for caching function results."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Create input data from function arguments
            input_data = (func.__name__, args, kwargs)
            
            # Try to get from cache
            cached = await cache.get(input_data)
            
            if cached is not None:
                return cached
            
            # Compute and cache
            result = await func(*args, **kwargs)
            await cache.set(input_data, result)
            
            return result
        return wrapper
    return decorator


class CacheManager:
    """Manager for multiple cache instances."""
    
    def __init__(self):
        self.caches: Dict[str, MultiLevelContextCache] = {}
    
    def register_cache(self, name: str, cache: MultiLevelContextCache):
        """Register a cache instance."""
        self.caches[name] = cache
    
    def get_cache(self, name: str) -> Optional[MultiLevelContextCache]:
        """Get registered cache."""
        return self.caches.get(name)
    
    def clear_all(self):
        """Clear all registered caches."""
        for cache in self.caches.values():
            cache.clear()
    
    def get_all_stats(self) -> Dict[str, CacheStats]:
        """Get statistics for all caches."""
        return {name: cache.get_stats() for name, cache in self.caches.items()}


# Global cache manager
_cache_manager = CacheManager()


def get_cache_manager() -> CacheManager:
    """Get global cache manager."""
    return _cache_manager


# Global cache instance for convenience decorator
_global_context_cache = MultiLevelContextCache(
    l1_max_size_mb=256.0,  # Safe for 8GB RAM
    l2_storage_path=str(Path.home() / ".cache" / "hledac" / "context_cache")
)


def cached_context(
    func=None,
    *,
    exclude_self: bool = True,
    cache_type: CacheType = CacheType.QUERY
):
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
            # Exclude self from cache key if requested
            if exclude_self and len(args) > 0:
                # For methods, args[0] is self
                cache_args = (f.__name__,) + args[1:] + (kwargs,)
            else:
                cache_args = (f.__name__, args, kwargs)

            # Try cache
            cached = await _global_context_cache.get(cache_args, cache_type)
            if cached is not None:
                return cached

            # Execute and cache
            result = await f(*args, **kwargs)
            await _global_context_cache.set(cache_args, result, cache_type)
            return result
        return wrapper

    if func is not None:
        # Called as @cached_context
        return decorator(func)
    else:
        # Called as @cached_context(exclude_self=True)
        return decorator
