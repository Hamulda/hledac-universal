"""
_cache — KV Cache Management Module
===================================

PEP 698: Extracted from DeepHermes3Engine cache-related methods.
Handles prefix cache, session cache, and KV pool management.

Architecture:
- kv_cache_manager.py: Core KV cache abstractions
- warmup.py: Warmup logic for model caches
"""

from hledac.universal.brain._cache.kv_cache_manager import (
    KVCacheManager,
    PrefixCache,
    SessionCache,
    get_kv_cache_manager,
)
from hledac.universal.brain._cache.warmup import WarmupManager
from _core import aclose

__all__ = [
    "KVCacheManager",
    "PrefixCache",
    "SessionCache",
    "get_kv_cache_manager",
    "WarmupManager",
]
