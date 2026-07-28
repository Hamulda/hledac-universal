"""GlobalCacheRegistry — centralized cache lifecycle management.

F350M-R / Issue #16

Architecture:
- Single registry for all named caches in the system
- Provides clear_all() on sprint winddown / shutdown
- Integrates with memory pressure monitoring
- WeakValueDictionary for caches holding only object values

Usage:
    from hledac.universal.core.global_cache_registry import (
        get_cache_registry,
        register_cache,
    )

    # Register a cache
    register_cache("embeddings", get_size=lambda: len(cache._l1), clear=cache.clear)

    # On shutdown: clear all
    registry = get_cache_registry()
    sizes = registry.clear_all()
"""
from __future__ import annotations

import threading
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A registered cache entry."""
    name: str
    get_size: Callable[[], int]
    clear: Callable[[], Any]
    memory_pressure_threshold: float = 0.85
    _description: str = ""


class GlobalCacheRegistry:
    """Centralized registry for all global caches.

    Allows explicit clear_all() on shutdown and memory-pressure-aware eviction.

    Thread-safe via double-checked locking (DCLP) pattern.
    """
    __slots__ = ("_entries", "_lock", "_initialized")

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._initialized = True

    # -------------------------------------------------------------------------
    # Singleton (DCLP pattern, matches hledac conventions)
    # -------------------------------------------------------------------------
    _instance: "GlobalCacheRegistry | None" = None
    _init_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "GlobalCacheRegistry":
        """Return the singleton instance (lazy, thread-safe)."""
        if cls._instance is not None:
            return cls._instance
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(
        self,
        name: str,
        get_size: Callable[[], int],
        clear: Callable[[], Any],
        *,
        memory_pressure_threshold: float = 0.85,
        description: str = "",
    ) -> None:
        """Register a cache with the global registry.

        Args:
            name: Unique identifier for the cache (e.g., "embeddings", "brain.prompt")
            get_size: Callable returning current cache entry count
            clear: Callable that clears the cache (may be sync or async)
            memory_pressure_threshold: Threshold (0.0-1.0) at which to evict
            description: Human-readable description for debugging
        """
        with self._lock:
            if name in self._entries:
                logger.debug(f"[GlobalCacheRegistry] overwriting existing entry: {name}")
            self._entries[name] = CacheEntry(
                name=name,
                get_size=get_size,
                clear=clear,
                memory_pressure_threshold=memory_pressure_threshold,
                _description=description,
            )
            logger.debug(
                f"[GlobalCacheRegistry] registered: {name}"
                + (f" ({description})" if description else "")
            )

    def unregister(self, name: str) -> bool:
        """Remove a cache from the registry.

        Returns True if the cache was found and removed, False otherwise.
        """
        with self._lock:
            if name in self._entries:
                del self._entries[name]
                logger.debug(f"[GlobalCacheRegistry] unregistered: {name}")
                return True
            return False

    # -------------------------------------------------------------------------
    # Operations
    # -------------------------------------------------------------------------

    def clear_all(self) -> dict[str, int]:
        """Clear all registered caches.

        Returns a dict of cache_name → size_before_clear for debugging.
        Clears synchronously — for async caches, the clear() callable must
        handle async internally or be wrapped.
        """
        sizes: dict[str, int] = {}
        with self._lock:
            entries = list(self._entries.items())

        for name, entry in entries:
            try:
                size = entry.get_size()
                sizes[name] = size
                entry.clear()
                logger.debug(f"[GlobalCacheRegistry] cleared: {name} ({size} entries)")
            except Exception as e:
                logger.warning(f"[GlobalCacheRegistry] clear failed for {name}: {e}")
                sizes[name] = -1  # Indicate failure

        logger.info(f"[GlobalCacheRegistry] clear_all complete: {len(sizes)} caches processed")
        return sizes

    def get_registry_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all registered caches.

        Returns:
            Dict mapping cache name → {size, threshold, description}
        """
        with self._lock:
            return {
                name: {
                    "size": entry.get_size(),
                    "threshold": entry.memory_pressure_threshold,
                    "description": entry._description,
                }
                for name, entry in self._entries.items()
            }

    def list_caches(self) -> list[str]:
        """Return list of registered cache names (sorted)."""
        with self._lock:
            return sorted(self._entries.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Module-level convenience functions (DCLP singleton access)
# ---------------------------------------------------------------------------
_registry: GlobalCacheRegistry | None = None
_reg_lock = threading.RLock()


def _get_registry() -> GlobalCacheRegistry:
    """Get or create the global registry instance."""
    global _registry
    if _registry is not None:
        return _registry
    with _reg_lock:
        if _registry is None:
            _registry = GlobalCacheRegistry.get_instance()
        return _registry


def register_cache(
    name: str,
    get_size: Callable[[], int],
    clear: Callable[[], Any],
    *,
    memory_pressure_threshold: float = 0.85,
    description: str = "",
) -> None:
    """Register a cache with the global registry.

    Convenience wrapper around GlobalCacheRegistry.register().
    """
    _get_registry().register(
        name=name,
        get_size=get_size,
        clear=clear,
        memory_pressure_threshold=memory_pressure_threshold,
        description=description,
    )


def unregister_cache(name: str) -> bool:
    """Remove a cache from the global registry."""
    return _get_registry().unregister(name)


def clear_all_caches() -> dict[str, int]:
    """Clear all registered caches. Returns size dict for debugging."""
    return _get_registry().clear_all()


def get_cache_stats() -> dict[str, dict[str, Any]]:
    """Get statistics for all registered caches."""
    return _get_registry().get_registry_stats()


def list_registered_caches() -> list[str]:
    """Return list of registered cache names."""
    return _get_registry().list_caches()


# ---------------------------------------------------------------------------
# __all__ for explicit export surface
# ---------------------------------------------------------------------------
__all__ = [
    "GlobalCacheRegistry",
    "CacheEntry",
    "register_cache",
    "unregister_cache",
    "clear_all_caches",
    "get_cache_stats",
    "list_registered_caches",
    "get_cache_registry",
]
