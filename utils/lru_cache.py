"""
LRU Cache Utilities — DEPRECATED
===============================

.. deprecated::
    This module is deprecated. Import from the new unified package instead:

    Old import                              → New import
    ----------------------------------------------------------------
    from utils.lru_cache import LRUCache    → from utils.cache import LRUCache
    from utils.lru_cache import TTLCache    → from utils.cache import TTLCache
    from utils.lru_cache import SlidingWindowKVCache → from utils.cache import SlidingWindowKVCache
    from utils.lru_cache import lru_cache    → Use functools.lru_cache or async_cached

    The new location provides:
    - Unified cache package (utils/cache/)
    - Modular architecture with shared base classes
    - Backward-compatible re-exports
    - Future improvements and bug fixes

This module is kept for backward compatibility only.
All implementations have been moved to utils/cache/.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import Generic, TypeVar, cast

# Re-export from new location for backward compatibility
from utils.cache import LRUCache
from utils.cache import SlidingWindowKVCache
from utils.cache import TTLCache

__all__ = ["LRUCache", "TTLCache", "SlidingWindowKVCache", "lru_cache"]

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
_F = TypeVar("_F", bound=Callable)


def lru_cache(max_size: int = 128) -> Callable[[_F], _F]:
    """
    LRU cache decorator using dict + list hybrid implementation.

    .. deprecated::
        Use functools.lru_cache for sync functions, or
        utils.cache.async_cached for async functions.

    Example:
        @lru_cache(max_size=256)
        def fib(n):
            return n if n < 2 else fib(n-1) + fib(n-2)
    """
    warnings.warn(
        "lru_cache from utils.lru_cache is deprecated. "
        "Use functools.lru_cache for sync functions or "
        "utils.cache.async_cached for async functions.",
        DeprecationWarning,
        stacklevel=2
    )
    cache: LRUCache[tuple, object] = LRUCache(max_size=max_size, thread_safe=True)

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            try:
                return cache[key]
            except KeyError:
                result = func(*args, **kwargs)
                cache[key] = result
                return result

        # These are functools.lru_cache compatible attributes
        wrapper.cache_info = lambda: cache.stats  # type: ignore[attr-defined]
        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return cast(_F, wrapper)

    return decorator
