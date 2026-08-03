"""GlobalCacheRegistry — centralized cache lifecycle management.

F350M-R / Issue #16 / R8

Architecture:
- Single registry for all named caches in the system
- Provides clear_all() on sprint winddown / shutdown
- R8: Integrates with MemoryPressureBroadcaster for active pressure-driven eviction
  via _GlobalCacheRegistryListener bridge
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

# R8: deferred import for MemoryPressureBroadcaster (avoid circular imports)
_broadcaster: Any = None
_broadcaster_lock = threading.RLock()


def _get_broadcaster():
    """Lazy accessor for MemoryPressureBroadcaster singleton."""
    global _broadcaster
    if _broadcaster is not None:
        return _broadcaster
    with _broadcaster_lock:
        if _broadcaster is None:
            try:
                from hledac.universal.core.memory_pressure import MemoryPressureBroadcaster
                _broadcaster = MemoryPressureBroadcaster.get_instance()
            except Exception:
                _broadcaster = False  # cache negative
    return _broadcaster if _broadcaster is not False else None


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

    # -------------------------------------------------------------------------
    # R8: Pressure-driven partial eviction (delegates to threshold-based clear)
    # -------------------------------------------------------------------------

    def evict_by_pressure(self, threshold: float) -> dict[str, int]:
        """
        R8: Evict caches that meet or exceed the given pressure threshold.

        Unlike clear_all(), this only clears caches whose
        memory_pressure_threshold >= the given threshold. This allows
        tiered eviction:
          - threshold=1.0 → only critical-level caches (clear none)
          - threshold=0.85 → HIGH pressure caches
          - threshold=0.7 → ELEVATED pressure caches
          - threshold=0.0 → all caches (equivalent to clear_all)

        Args:
            threshold: Pressure ratio threshold (0.0-1.0). Caches with
                memory_pressure_threshold >= threshold are evicted.

        Returns:
            Dict of cache_name → entries_before_clear.
        """
        sizes: dict[str, int] = {}
        with self._lock:
            matching = [
                (name, entry)
                for name, entry in self._entries.items()
                if entry.memory_pressure_threshold >= threshold
            ]

        for name, entry in matching:
            try:
                size = entry.get_size()
                sizes[name] = size
                entry.clear()
                logger.info(
                    f"[GlobalCacheRegistry] pressure-evicted: {name} "
                    f"({size} entries, threshold={entry.memory_pressure_threshold})"
                )
            except Exception as e:
                logger.warning(f"[GlobalCacheRegistry] pressure-evict failed for {name}: {e}")
                sizes[name] = -1

        if sizes:
            logger.info(
                f"[GlobalCacheRegistry] pressure eviction complete: "
                f"{len(sizes)} caches (threshold >= {threshold})"
            )
        return sizes

    # -------------------------------------------------------------------------
    # R8: Bridge to MemoryPressureBroadcaster
    # -------------------------------------------------------------------------

    def _ensure_broadcaster_registered(self) -> None:
        """
        R8: Register the registry as a listener with the MemoryPressureBroadcaster.

        This creates a _RegistryPressureBridge that converts broadcaster
        callbacks into threshold-based eviction on registered caches.

        Idempotent — no-op if already registered.
        """
        bc = _get_broadcaster()
        if bc is None:
            return  # Broadcaster not available (e.g., during import)
        # Check if already registered by name
        if "global_cache_registry" in bc.list_registered():
            return
        bridge = _RegistryPressureBridge(self)
        bc.register(bridge)

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

    R8: Also ensures the registry is registered as a listener with the
    MemoryPressureBroadcaster (idempotent).
    """
    registry = _get_registry()
    registry.register(
        name=name,
        get_size=get_size,
        clear=clear,
        memory_pressure_threshold=memory_pressure_threshold,
        description=description,
    )
    # R8: ensure the bridge to MemoryPressureBroadcaster is active
    registry._ensure_broadcaster_registered()


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


def get_cache_registry() -> GlobalCacheRegistry:
    """Return the GlobalCacheRegistry singleton instance."""
    return _get_registry()


# ---------------------------------------------------------------------------
# R8: Bridge listener — converts broadcaster events to threshold-based eviction
# ---------------------------------------------------------------------------


class _RegistryPressureBridge:
    """
    R8: Bridge between MemoryPressureBroadcaster and GlobalCacheRegistry.

    Implements MemoryPressureListener protocol. On each pressure event,
    delegates to GlobalCacheRegistry.evict_by_pressure() with an appropriate
    threshold:
      - on_soft_warn (ELEVATED): evict caches with threshold >= 0.8
      - on_warn (HIGH):          evict caches with threshold >= 0.85
      - on_critical (CRITICAL):  evict ALL caches (threshold >= 0.0)
    """

    __slots__ = ("_registry",)

    def __init__(self, registry: GlobalCacheRegistry) -> None:
        self._registry = registry

    @property
    def listener_priority(self) -> int:
        """Priority 2 = MEDIUM — clears after critical caches are done."""
        return 2

    @property
    def listener_name(self) -> str:
        return "global_cache_registry"

    def on_soft_warn(self) -> None:
        """ELEVATED: evict caches with threshold >= 0.8."""
        self._registry.evict_by_pressure(0.8)

    def on_warn(self) -> None:
        """HIGH: evict caches with threshold >= 0.85."""
        self._registry.evict_by_pressure(0.85)

    def on_critical(self) -> None:
        """CRITICAL: evict ALL registered caches."""
        self._registry.evict_by_pressure(0.0)

    def on_normal(self) -> None:
        """NORMAL: no action — caches refill naturally."""
        pass


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
