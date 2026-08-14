"""
Async-Safe Bounded Cache — DEPRECATED
===================================

.. deprecated::
    This module is deprecated. Import from the new unified package instead:

    Old import                              → New import
    ----------------------------------------------------------------
    from hledac.universal.utils.async_cache import *         → from utils.cache import *
    from hledac.universal.utils.async_cache import AsyncLRUCache → from utils.cache import AsyncLRUCache
    from hledac.universal.utils.async_cache import async_cached  → from utils.cache import async_cached

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
from hledac.universal.utils.cache import AsyncLRUCache
from hledac.universal.utils.cache import AsyncCacheError
from hledac.universal.utils.cache import async_cached
from hledac.universal.utils.cache import cached_awaitable

__all__ = ["AsyncLRUCache", "AsyncCacheError", "async_cached", "cached_awaitable"]
