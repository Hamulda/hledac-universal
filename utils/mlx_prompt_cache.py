"""
MLX Prompt Cache - Explicit size tracking for KV cache.

Provides:
- LRU cache for prompt cache states
- Explicit size_bytes tracking for memory management
- Bounded entries and total size
- Async-safe with asyncio.Lock
"""

import asyncio
import logging
from collections import OrderedDict
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class MLXPromptCache:
    """LRU cache for MLX prompt cache states with explicit size tracking."""

    def __init__(self, max_entries: int = 10, max_size_gb: Optional[float] = None):
        self._cache: OrderedDict[str, Tuple[list, int]] = OrderedDict()  # (cache_state, size_bytes)
        self._max_entries = max_entries
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._cache_evictions = 0

        if max_size_gb is None:
            try:
                import mlx.core as mx
                active = mx.metal.get_active_memory()
                free = 4 * 1024**3 - active
                self._max_size_bytes = int(free * 0.5)
            except Exception:
                self._max_size_bytes = int(1.5 * 1024**3)
        else:
            self._max_size_bytes = int(max_size_gb * 1024**3)

        self._current_size = 0

    async def get(self, prompt_hash: str) -> Optional[list]:
        """Get cached prompt state by hash."""
        async with self._lock:
            if prompt_hash in self._cache:
                self._cache.move_to_end(prompt_hash)
                self._hits += 1
                return self._cache[prompt_hash][0]
            self._misses += 1
            return None

    async def put(self, prompt_hash: str, cache_state: list, size_bytes: int):
        """Store prompt cache state with size tracking."""
        if size_bytes > self._max_size_bytes:
            logger.debug(f"Item too large ({size_bytes} > {self._max_size_bytes}) – skipping cache")
            return

        async with self._lock:
            # Evict by size or entry count
            while (self._current_size + size_bytes > self._max_size_bytes
                   or len(self._cache) >= self._max_entries):
                if not self._cache:
                    break
                key, (_, old_size) = self._cache.popitem(last=False)
                self._current_size -= old_size
                self._cache_evictions += 1

            self._cache[prompt_hash] = (cache_state, size_bytes)
            self._current_size += size_bytes
            self._cache.move_to_end(prompt_hash)

    async def clear(self):
        """Clear all cached entries."""
        async with self._lock:
            self._cache.clear()
            self._current_size = 0

    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size_bytes": self._current_size,
            "max_bytes": self._max_size_bytes,
            "items": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._cache_evictions,
            "hit_rate": self._hits / total if total > 0 else 0
        }
