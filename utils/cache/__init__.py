"""
Cache Package — Unified modular cache implementations
=====================================================

Modular architecture consolidating previously scattered cache implementations:

    utils/cache/
    ├── __init__.py      # Facade & backward-compatible re-exports
    ├── _base.py         # Shared eviction interfaces & node logic
    ├── _sync.py         # Synchronous LRUCache & TTLCache
    ├── _async.py        # AsyncLRUCache wrapper & async_cached decorator
    └── _adaptive.py     # IntelligentCache & ARC eviction engine

Design Principles
-----------------
1. Single responsibility: each module has one clear purpose
2. DRY eviction logic: base classes handle core patterns
3. M1 8GB safe: bounded structures, no unbounded growth
4. Python 3.14+ compatible: no OrderedDict, modern typing, __slots__

When to use which
-----------------
- LRUCache: Simple LRU eviction, O(1) operations, optional thread-safety
- TTLCache: LRU + per-entry time-based expiration
- AsyncLRUCache: Async-safe with per-key single-flight locks
- IntelligentCache: ML-enhanced adaptive eviction (ARC algorithm)

Migration from legacy modules
-----------------------------
Old import                                → New import
-------------------------------------------------------------------------------
from utils.lru_cache import LRUCache      → from utils.cache import LRUCache
from utils.async_cache import AsyncLRUCache → from utils.cache import AsyncLRUCache
from utils.intelligent_cache import IntelligentCache → from utils.cache import IntelligentCache

Note: PyCacheDict, AsyncPyCacheDict, BoundedLoRACache, GenerationalCache 
remain in utils/cache.py (the parent module) for backward compatibility.
They are accessible via `from utils.cache import PyCacheDict` (imports from utils/cache.py).
"""

from __future__ import annotations

# ── Base interfaces & shared logic ────────────────────────────────────────────
from ._base import (
    CacheMetrics,
    EvictionPolicy,
    CacheStats,
)

# ── Synchronous caches ────────────────────────────────────────────────────────
from ._sync import (
    LRUCache,
    TTLCache,
    SlidingWindowKVCache,
)

# ── Async caches ───────────────────────────────────────────────────────────────
from ._async import (
    AsyncLRUCache,
    AsyncCacheError,
    async_cached,
    cached_awaitable,
)

# ── Adaptive/ML caches ─────────────────────────────────────────────────────────
from ._adaptive import (
    IntelligentCache,
    MemoryOptimizedURLSet,
    CacheConfig,
    CacheEntry,
    EvictionStrategy,
    get_global_cache,
)

# ── Legacy classes from utils/cache.py ─────────────────────────────────────────
# Import directly from the file to avoid circular imports
# The module is now utils/cache/ (package), so we need to import from utils.cache file
# Using importlib.util to import from the file directly
import importlib.util
import sys

_legacy_cache_classes = frozenset({
    "PyCacheDict",
    "AsyncPyCacheDict",
    "BoundedLoRACache",
    "GenerationalCache",
})

# Lazy load legacy classes from utils/cache.py (the file)
_legacy_cache_module_loaded = False
_legacy_cache_module = None


def _get_legacy_module():
    """Load the legacy utils.cache module (the file, not the package)."""
    global _legacy_cache_module_loaded, _legacy_cache_module
    if not _legacy_cache_module_loaded:
        # Import the file utils/cache.py directly
        spec = importlib.util.spec_from_file_location(
            "utils._cache_legacy", 
            "utils/cache.py"
        )
        if spec and spec.loader:
            _legacy_cache_module = importlib.util.module_from_spec(spec)
            sys.modules["utils._cache_legacy"] = _legacy_cache_module
            spec.loader.exec_module(_legacy_cache_module)
        _legacy_cache_module_loaded = True
    return _legacy_cache_module


def __getattr__(name: str):
    """Lazily import legacy cache classes from utils/cache.py file."""
    if name in _legacy_cache_classes:
        mod = _get_legacy_module()
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Return list of all available attributes including lazy imports."""
    return list(globals().keys()) + list(_legacy_cache_classes)


__all__ = [
    # Base interfaces
    "CacheMetrics",
    "EvictionPolicy",
    "CacheStats",
    # Sync caches
    "LRUCache",
    "TTLCache",
    "SlidingWindowKVCache",
    # Async caches
    "AsyncLRUCache",
    "AsyncCacheError",
    "async_cached",
    "cached_awaitable",
    # Adaptive caches
    "IntelligentCache",
    "MemoryOptimizedURLSet",
    "CacheConfig",
    "CacheEntry",
    "EvictionStrategy",
    "get_global_cache",
    # Legacy classes (lazy loaded from utils/cache.py)
    "PyCacheDict",
    "AsyncPyCacheDict",
    "BoundedLoRACache",
    "GenerationalCache",
]
