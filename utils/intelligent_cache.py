"""
Intelligent Cache with ML-Powered Eviction — DEPRECATED
====================================================

.. deprecated::
    This module is deprecated. Import from the new unified package instead:

    Old import                              → New import
    ----------------------------------------------------------------
    from hledac.universal.utils.intelligent_cache import *   → from utils.cache import *
    from hledac.universal.utils.intelligent_cache import IntelligentCache → from utils.cache import IntelligentCache
    from hledac.universal.utils.intelligent_cache import MemoryOptimizedURLSet → from utils.cache import MemoryOptimizedURLSet

    The new location provides:
    - Unified cache package (utils/cache/)
    - Modular architecture with shared base classes
    - Backward-compatible re-exports
    - Future improvements and bug fixes

This module is kept for backward compatibility only.
All implementations have been moved to utils/cache/.
"""

from __future__ import annotations

# Re-export from new location for backward compatibility
from hledac.universal.utils.cache import (
    CacheConfig,
    CacheEntry,
    CacheStats,
    EvictionStrategy,
    IntelligentCache,
    MemoryOptimizedURLSet,
    get_global_cache,
)

__all__ = [
    "CacheConfig",
    "CacheEntry",
    "CacheStats",
    "EvictionStrategy",
    "IntelligentCache",
    "MemoryOptimizedURLSet",
    "get_global_cache",
]
