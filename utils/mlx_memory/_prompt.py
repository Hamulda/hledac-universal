"""
utils/mlx_memory/_prompt.py — MLX Prompt KV Cache (F330-MLX-DUP-007)

LRU cache for MLX prompt cache states with explicit size tracking.

Bounded entries and total size. Async-safe with asyncio.Lock.
"""
import asyncio
import logging
from collections import OrderedDict
from typing import Any
logger = logging.getLogger(__name__)

class MLXPromptCache:
    """
    LRU cache for MLX prompt cache states with explicit size tracking.

    Usage:
        cache = MLXPromptCache(max_entries=10, max_size_gb=0.5)
        cache.set("prompt_key", (cache_state, size_bytes))
        state = cache.get("prompt_key")
        if state is not None:
            cache_key, (cache_state, size_bytes) = state
    """
    __slots__ = tuple(('_cache', '_lock', '_max_entries', '_max_size_bytes'))

    def __init__(self, max_entries: int=10, max_size_gb: float | None=None):
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._max_entries = max_entries
        self._max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024) if max_size_gb else None
        self._lock = asyncio.Lock()

    async def set_async(self, key: str, value: tuple[Any, int]) -> None:
        """Store a (cache_state, size_bytes) tuple (async-safe)."""
        total_size = value[1]
        if self._max_size_bytes and total_size > self._max_size_bytes:
            logger.debug(f'MLXPromptCache: entry {key} too large ({total_size} bytes), skipped')
            return
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._max_entries:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
                logger.debug(f'MLXPromptCache: evicted {oldest}')

    def set(self, key: str, value: tuple[Any, int]) -> None:
        """Store a (cache_state, size_bytes) tuple (sync, non-blocking)."""
        total_size = value[1]
        if self._max_size_bytes and total_size > self._max_size_bytes:
            logger.debug(f'MLXPromptCache: entry {key} too large ({total_size} bytes), skipped')
            return
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_entries:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
            logger.debug(f'MLXPromptCache: evicted {oldest}')

    def get(self, key: str) -> tuple[str, tuple[Any, int]] | None:
        """Get a (key, (cache_state, size_bytes)) tuple or None."""
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return (key, self._cache[key])

    def clear(self) -> None:
        """Clear all entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def total_size(self) -> int:
        """Sum of all entry sizes in bytes."""
        return sum((size for _, size in self._cache.values()))

    def keys(self):
        return self._cache.keys()