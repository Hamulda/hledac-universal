"""
_cache — D-SPINE private KV-cache layer (ISSUE #16)
===================================================

D-SPINE private subfolder (see brain/_batch/__init__.py for the layout).
PEP 698 extraction of DeepHermes3Engine cache logic. Handles prefix cache,
session cache, KV pool and warmup.

LIVE STATUS: ``kv_cache_manager.py`` is imported by the live engine as a
convenience adapter over its inline pools; ``warmup.py`` (WarmupManager) is
currently ORPHANED w.r.t. the engine (the engine keeps its own inline warmup).
The engine's real shared prefix/session pools (``_kv_cache_pool`` /
``_session_cache_pool``) persist KV tensors across same-prefix requests — this
is the "shared prompt cache" that must NEVER be destroyed between requests
(model swap is the only destructive path).

Architecture:
- kv_cache_manager.py: Core KV cache abstractions (adapter over engine pools)
- warmup.py: Warmup logic for model caches (currently not wired into engine)
"""

from hledac.universal.brain._cache.kv_cache_manager import (
    KVCacheManager,
    PrefixCache,
    SessionCache,
    get_kv_cache_manager,
)
from hledac.universal.brain._cache.warmup import WarmupManager

__all__ = [
    "KVCacheManager",
    "PrefixCache",
    "SessionCache",
    "get_kv_cache_manager",
    "WarmupManager",
]
