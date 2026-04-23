"""
Intelligent Cache with ML-Powered Eviction

Adaptive caching system that learns access patterns and optimizes
eviction strategy (LRU/LFU/hybrid) based on workload characteristics.

Optimized for M1 8GB with memory-conscious design.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

import numpy as np

# Sprint 5N: Lazy MLX import - MLX is optional, not a hard dependency
_MLX_AVAILABLE = None
_MLX_CORE = None

def _get_mlx():
    """Lazy import MLX core - returns None if MLX not available."""
    global _MLX_AVAILABLE, _MLX_CORE
    if _MLX_AVAILABLE is None:
        try:
            import mlx.core as mx
            _MLX_CORE = mx
            _MLX_AVAILABLE = True
        except ImportError:
            _MLX_AVAILABLE = False
            _MLX_CORE = None
    return _MLX_CORE

logger = logging.getLogger(__name__)


class EvictionStrategy(Enum):
    """Cache eviction strategies."""
    LRU = "lru"           # Least Recently Used
    LFU = "lfu"           # Least Frequently Used
    ADAPTIVE = "adaptive" # ML-powered hybrid


@dataclass
class CacheConfig:
    """Configuration for intelligent cache."""
    max_size_bytes: int = 100 * 1024 * 1024  # 100MB default for M1 8GB
    max_entries: int = 10000
    default_ttl: int = 3600  # 1 hour
    strategy: EvictionStrategy = EvictionStrategy.ADAPTIVE
    persistence_path: Optional[str] = None
    enable_ml: bool = False  # Disabled by default to save memory
    warm_keys: Optional[List[str]] = None  # Keys to pre-load (Fix 4)
    warm_loader: Optional[Callable] = None  # Async loader function (Fix 4)


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""
    key: str
    value: Any
    size_bytes: int
    created_at: float
    expires_at: float
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)


@dataclass
class CacheStats:
    """Cache performance statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_size_bytes: int = 0
    entry_count: int = 0
    hit_rate: float = 0.0


class _ARC:
    """
    Adaptive Replacement Cache (ARC) - O(1) eviction policy.

    Maintains four lists:
    - T1: Recently used pages (recency)
    - T2: Frequently used pages (both recency and frequency)
    - B1: Ghosts of recently evicted T1 pages
    - B2: Ghosts of recently evicted T2 pages

    Uses OrderedDict for O(1) operations on list boundaries.
    """

    def __init__(self, max_entries: int, max_size_bytes: int):
        self.max_entries = max_entries
        self.max_size_bytes = max_size_bytes

        # T1: Recently used (recency only)
        self._t1: OrderedDict = OrderedDict()
        # T2: Frequently used (recency + frequency)
        self._t2: OrderedDict = OrderedDict()
        # B1: Ghosts from T1
        self._b1: OrderedDict = OrderedDict()
        # B2: Ghosts from T2
        self._b2: OrderedDict = OrderedDict()

        # Current sizes
        self._current_entries = 0
        self._current_bytes = 0

    def _get_size(self, key: str, cache: Dict[str, CacheEntry]) -> int:
        """Get size of entry from cache."""
        entry = cache.get(key)
        return entry.size_bytes if entry else 0

    def on_access(self, key: str, size: int, cache: Dict[str, CacheEntry]) -> None:
        """Record cache hit - move from T1 to T2 or update in T2."""
        # Check T1
        if key in self._t1:
            self._t1.move_to_end(key)
            # Move to T2
            self._t2[key] = self._t1.pop(key)
        elif key in self._t2:
            self._t2.move_to_end(key)
        elif key in self._b1:
            # Hit in B1 - expand T1
            self._b1.pop(key)
            self._t1[key] = size
            self._current_entries += 1
            self._current_bytes += size
        elif key in self._b2:
            # Hit in B2 - expand T2
            self._b2.pop(key)
            self._t2[key] = size
            self._current_entries += 1
            self._current_bytes += size

    def evict_one(self, cache: Dict[str, CacheEntry]) -> Optional[str]:
        """Evict one item and return its key. Returns None if nothing to evict."""
        # Determine target list (T1 or T2)
        # Simple heuristic: if T1 > T2, evict from T1
        if len(self._t1) > len(self._t2) and self._t1:
            key, size = self._t1.popitem(last=False)
            # Move to B1 ghost
            self._b1[key] = size
            self._current_entries -= 1
            self._current_bytes -= size
            return key
        elif self._t2:
            key, size = self._t2.popitem(last=False)
            # Move to B2 ghost
            self._b2[key] = size
            self._current_entries -= 1
            self._current_bytes -= size
            return key
        elif self._t1:
            key, size = self._t1.popitem(last=False)
            self._current_entries -= 1
            self._current_bytes -= size
            return key
        return None

    def on_set(self, key: str, size: int) -> None:
        """Record new item set."""
        if key in self._t1 or key in self._t2:
            return  # Already handled by on_access
        if key in self._b1:
            self._b1.pop(key)
            self._t2[key] = size
        elif key in self._b2:
            self._b2.pop(key)
            self._t2[key] = size
        else:
            self._t1[key] = size
            self._current_entries += 1
        self._current_bytes += size


class IntelligentCache:
    """
    ML-enhanced intelligent cache with ARC eviction.

    Features:
    - ARC (Adaptive Replacement Cache) for O(1) eviction
    - Automatic memory management for M1 8GB
    - Async operations for non-blocking access
    - Optional persistence to disk
    - sys.getsizeof for size estimation

    Example:
        cache = IntelligentCache(CacheConfig(max_size_bytes=50*1024*1024))
        await cache.initialize()

        await cache.set("key", value, ttl=300)
        result = await cache.get("key")
    """

    def __init__(self, config: Optional[CacheConfig] = None):
        """
        Initialize intelligent cache.

        Args:
            config: Cache configuration
        """
        self.config = config or CacheConfig()

        # Main storage
        self._cache: Dict[str, CacheEntry] = {}
        self._access_order: OrderedDict = OrderedDict()  # For LRU
        self._frequency: Dict[str, int] = defaultdict(int)  # For LFU

        # ARC eviction (Fix 4)
        self._arc = _ARC(self.config.max_entries, self.config.max_size_bytes)

        # Statistics
        self._stats = CacheStats()

        # State
        self._initialized = False
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

        # F196B: Track background tasks for proper cleanup
        self._background_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """F196B: Track background tasks for proper cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

        # Persistence
        if self.config.persistence_path:
            self._persistence_path = Path(self.config.persistence_path)
            self._persistence_path.mkdir(parents=True, exist_ok=True)
        else:
            self._persistence_path = None

        # Cache warming (Fix 4)
        self._warm_keys = getattr(self.config, 'warm_keys', None)
        self._warm_loader = getattr(self.config, 'warm_loader', None)

        logger.debug(f"IntelligentCache created (ARC eviction)")
    
    async def initialize(self) -> bool:
        """
        Initialize cache and load persisted data.
        
        Returns:
            True if initialization successful
        """
        if self._initialized:
            return True
        
        async with self._lock:
            # Load persisted cache if available
            if self._persistence_path:
                await self._load_persisted()

            # Start background cleanup task — F196B: track for cleanup
            self._cleanup_task = self._track_task(
                self._background_cleanup()
            )

            # Cache warming (Fix 4)
            if self._warm_keys and self._warm_loader:
                await self._warm_cache(self._warm_keys, self._warm_loader)

            self._initialized = True
            logger.info(f"IntelligentCache initialized (max: {self.config.max_size_bytes / 1024 / 1024:.1f} MB)")
            return True
    
    async def close(self) -> None:
        """Close cache and cleanup resources."""
        # F196B: Cancel all tracked background tasks
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Persist cache if configured
        if self._persistence_path:
            await self._persist()

        # Clear memory
        self._cache.clear()
        self._access_order.clear()
        self._frequency.clear()

        self._initialized = False
        logger.info("IntelligentCache closed")
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        if not self._initialized:
            return None

        async with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._stats.misses += 1
                return None

            # Check expiration
            if time.time() > entry.expires_at:
                await self._remove_entry(key)
                self._stats.misses += 1
                return None

            # Update access patterns
            entry.access_count += 1
            entry.last_accessed = time.time()
            self._frequency[key] += 1

            # Update LRU order
            if key in self._access_order:
                self._access_order.move_to_end(key)

            # Update ARC on access (Fix 4)
            self._arc.on_access(key, entry.size_bytes, self._cache)

            self._stats.hits += 1
            self._update_hit_rate()

            return entry.value
    
    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        size_bytes: Optional[int] = None
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
            size_bytes: Size hint for value (auto-calculated if None)

        Returns:
            True if successfully cached
        """
        if not self._initialized:
            return False

        async with self._lock:
            # Calculate size
            if size_bytes is None:
                size_bytes = self._estimate_size(value)

            # Check if entry too large
            if size_bytes > self.config.max_size_bytes * 0.1:  # Max 10% of total
                logger.warning(f"Entry too large ({size_bytes} bytes), skipping cache")
                return False

            # Make room if needed (Fix 4: use ARC)
            await self._evict_if_needed(size_bytes)

            # Create entry
            now = time.time()
            entry = CacheEntry(
                key=key,
                value=value,
                size_bytes=size_bytes,
                created_at=now,
                expires_at=now + (ttl or self.config.default_ttl),
                last_accessed=now
            )

            # Store
            self._cache[key] = entry
            self._access_order[key] = None
            self._frequency[key] = 0

            # Update ARC (Fix 4)
            self._arc.on_set(key, size_bytes)

            # Update stats
            self._stats.total_size_bytes += size_bytes
            self._stats.entry_count = len(self._cache)

            return True
    
    async def delete(self, key: str) -> bool:
        """
        Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            if key not in self._cache:
                return False
            
            await self._remove_entry(key)
            return True
    
    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self._frequency.clear()
            self._stats.total_size_bytes = 0
            self._stats.entry_count = 0
            logger.info("Cache cleared")
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        self._update_hit_rate()
        self._stats.entry_count = len(self._cache)
        return self._stats
    
    async def _remove_entry(self, key: str) -> None:
        """Remove entry from all data structures."""
        if key not in self._cache:
            return
        
        entry = self._cache[key]
        self._stats.total_size_bytes -= entry.size_bytes
        
        del self._cache[key]
        self._access_order.pop(key, None)
        self._frequency.pop(key, None)
    
    async def _evict_if_needed(self, required_bytes: int) -> None:
        """KVP-based eviction: O(1) scoring of top-10 ARC candidates only."""
        max_size = self.config.max_size_bytes
        max_entries = self.config.max_entries

        while (self._stats.total_size_bytes + required_bytes > max_size or
               len(self._cache) >= max_entries) and self._cache:

            # KVP heuristic: top-10 kandidáti z ARC access_order (nejstarší)
            candidates = list(self._access_order.keys())[:10]
            if not candidates:
                # fallback na první položku v cache
                key_to_evict = next(iter(self._cache))
                await self._remove_entry(key_to_evict)
                self._stats.evictions += 1
                continue

            now = time.time()
            total_accesses = max(self._stats.hits + self._stats.misses, 1)
            total_size = max(self._stats.total_size_bytes, 1024)  # aspoň 1 KB
            hit_rate = self._stats.hit_rate or 0.1  # avoid zero

            scored = []
            for key in candidates:
                if key not in self._cache:
                    continue
                entry = self._cache[key]

                # === KVP zero-shot formula (O(1) optimalizovaný) ===
                # Sprint 39/5N: Použijeme mx.exp() místo polynomiální aproximace (lazy import)
                recency_seconds = now - getattr(entry, 'last_accessed', entry.created_at)
                recency_m = recency_seconds / 60.0  # Normalizace na minuty
                mx = _get_mlx()
                if mx is not None:
                    # mx.exp(-x) pro decay faktor
                    recency_factor = float(mx.exp(mx.array(-recency_m)).item())
                else:
                    # Fallback: polynomiální aproximace když MLX není dostupný
                    recency_factor = 1.0 / (1.0 + recency_m + recency_m * recency_m / 2)

                freq_norm = entry.access_count / max(total_accesses, 1)
                size_norm = entry.size_bytes / max(total_size, 1)

                # Core utility: freq * recency_decay * size_penalty * global_hit_context
                utility = (freq_norm *
                          recency_factor *
                          (1.0 / (1.0 + size_norm)) *
                          hit_rate)

                scored.append((utility, key))

            # Seřadit podle utility (nejnižší = nejhorší = evict)
            scored.sort(key=lambda x: x[0])
            key_to_evict = scored[0][1]

            await self._remove_entry(key_to_evict)
            self._stats.evictions += 1
    
    def _select_eviction_candidate(self) -> Optional[str]:
        """Select key to evict based on strategy."""
        if not self._cache:
            return None
        
        if self.config.strategy == EvictionStrategy.LRU:
            # Least Recently Used
            return next(iter(self._access_order))
        
        elif self.config.strategy == EvictionStrategy.LFU:
            # Least Frequently Used
            min_freq = min(self._frequency.values())
            candidates = [k for k, v in self._frequency.items() if v == min_freq]
            return candidates[0] if candidates else None
        
        else:  # ADAPTIVE
            # Hybrid: combination of recency and frequency
            now = time.time()
            min_score = float('inf')
            candidate = None
            
            for key, entry in self._cache.items():
                recency = now - entry.last_accessed
                frequency = max(1, self._frequency.get(key, 1))
                score = recency / frequency  # Lower is better
                
                if score > min_score:
                    min_score = score
                    candidate = key
            
            return candidate

    def _estimate_size(self, value: Any) -> int:
        """Estimate size of value in bytes using sys.getsizeof (Fix 4)."""
        return sys.getsizeof(value)
    
    def _update_hit_rate(self) -> None:
        """Update hit rate statistic."""
        total = self._stats.hits + self._stats.misses
        if total > 0:
            self._stats.hit_rate = self._stats.hits / total
    
    async def _background_cleanup(self) -> None:
        """Background task for periodic cleanup."""
        while True:
            try:
                await asyncio.sleep(60)  # Cleanup every minute
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    async def _cleanup_expired(self) -> None:
        """Remove expired entries."""
        now = time.time()
        expired = [
            key for key, entry in self._cache.items()
            if now > entry.expires_at
        ]
        
        for key in expired:
            await self._remove_entry(key)
        
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired entries")
    
    async def _persist(self) -> None:
        """Persist cache to disk."""
        if not self._persistence_path:
            return
        
        try:
            data = {
                key: {
                    "value": entry.value,
                    "expires_at": entry.expires_at,
                    "access_count": entry.access_count
                }
                for key, entry in self._cache.items()
                if time.time() < entry.expires_at  # Only persist non-expired
            }
            
            persist_file = self._persistence_path / "cache_data.json"
            with open(persist_file, 'w') as f:
                json.dump(data, f, default=str)
            
            logger.info(f"Persisted {len(data)} entries to disk")
            
        except Exception as e:
            logger.error(f"Failed to persist cache: {e}")
    
    async def _load_persisted(self) -> None:
        """Load persisted cache from disk."""
        if not self._persistence_path:
            return
        
        persist_file = self._persistence_path / "cache_data.json"
        if not persist_file.exists():
            return
        
        try:
            with open(persist_file, 'r') as f:
                data = json.load(f)
            
            now = time.time()
            loaded = 0
            
            for key, item in data.items():
                if now < item.get("expires_at", 0):
                    await self.set(
                        key,
                        item["value"],
                        ttl=int(item["expires_at"] - now)
                    )
                    loaded += 1
            
            logger.info(f"Loaded {loaded} persisted entries")

        except Exception as e:
            logger.error(f"Failed to load persisted cache: {e}")

    async def _warm_cache(self, keys: List[str], loader: Callable) -> None:
        """Warm cache with keys using async loader (Fix 4)."""
        tasks = [loader(key) for key in keys]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for key, value in zip(keys, results):
            if not isinstance(value, Exception):
                await self.set(key, value)


# Global cache instance
_global_cache: Optional[IntelligentCache] = None


async def get_global_cache() -> IntelligentCache:
    """Get global cache instance."""
    global _global_cache
    if _global_cache is None:
        _global_cache = IntelligentCache()
        await _global_cache.initialize()
    return _global_cache



# =============================================================================
# MEMORY OPTIMIZED URL SET (Integrated from hledac/scanners/deep_probe.py)
# =============================================================================

class MemoryOptimizedURLSet:
    """
    Memory-efficient URL set with configurable memory limit.
    
    Optimized for M1 8GB - tracks memory usage and enforces limits.
    Used for tracking discovered URLs during deep web scanning
    without consuming excessive memory.
    
    Example:
        >>> url_set = MemoryOptimizedURLSet(max_memory_mb=50)
        >>> url_set.add("https://example.com/page1")
        >>> url_set.add("https://example.com/page2")
        >>> print(len(url_set))
        2
        >>> print("https://example.com/page1" in url_set)
        True
    """
    
    def __init__(self, max_memory_mb: int = 50):
        """
        Initialize memory-optimized URL set.
        
        Args:
            max_memory_mb: Maximum memory to use in MB
        """
        self.max_memory_mb = max_memory_mb
        self.urls: set = set()
        self._memory_usage = 0
        self._overhead_per_url = 72  # Python set overhead estimate
    
    def add(self, url: str) -> bool:
        """
        Add URL if not already present and within memory limit.
        
        Args:
            url: URL to add
            
        Returns:
            True if added, False if already present or memory limit reached
        """
        if url in self.urls:
            return False
        
        # Estimate memory usage (URL bytes + overhead)
        estimated_size = len(url.encode('utf-8')) + self._overhead_per_url
        max_bytes = self.max_memory_mb * 1024 * 1024
        
        if self._memory_usage + estimated_size > max_bytes:
            logger.warning(
                f"Memory limit reached ({self.max_memory_mb}MB), "
                f"cannot add more URLs (current: {len(self.urls)})"
            )
            return False
        
        self.urls.add(url)
        self._memory_usage += estimated_size
        return True
    
    def update(self, urls: List[str]) -> int:
        """
        Add multiple URLs.
        
        Args:
            urls: List of URLs to add
            
        Returns:
            Number of URLs actually added
        """
        added = 0
        for url in urls:
            if self.add(url):
                added += 1
        return added
    
    def __contains__(self, url: str) -> bool:
        """Check if URL is in set."""
        return url in self.urls
    
    def __len__(self) -> int:
        """Get number of URLs in set."""
        return len(self.urls)
    
    def __iter__(self):
        """Iterate over URLs."""
        return iter(self.urls)
    
    def get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        return self._memory_usage / (1024 * 1024)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get URL set statistics."""
        return {
            'url_count': len(self.urls),
            'memory_usage_mb': self.get_memory_usage_mb(),
            'max_memory_mb': self.max_memory_mb,
            'usage_percent': (self._memory_usage / (self.max_memory_mb * 1024 * 1024)) * 100
        }
    
    def clear(self) -> None:
        """Clear all URLs and reset memory usage."""
        self.urls.clear()
        self._memory_usage = 0


# Update exports
__all__ = [
    'EvictionStrategy',
    'CacheConfig',
    'CacheEntry',
    'CacheStats',
    'IntelligentCache',
    'get_global_cache',
    # NEW from scanners:
    'MemoryOptimizedURLSet',
]
