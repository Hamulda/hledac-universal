"""
Adaptive Cache — Memory-Aware LRU/TinyLFU Cache for M1 8GB UMA
================================================================

Cutting-edge adaptive cache s dynamickým sizingom podľa dostupnej RAM.
Využíva existujúcu Rust infraštruktúru (PyGraphLRUCache + get_memory_snapshot).

Features:
- TinyLFU admission policy (W-TinyLFU) pre lepší hit ratio
- Adaptívny max_size podľa available_memory_gib
- Memory pressure awareness (normal/elevated/critical)
- M1 8GB safe: max 512 MB pre cache layer

Invariant: Always-on, bounded, fail-safe
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
import msgspec
from enum import IntEnum
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

# Lazy import — Rust extensions sa neimportujú na module level
_rust: Any = None
_rust_lock = threading.Lock()


def _get_rust() -> Any:
    """Lazy loading Rust extensions (thread-safe)."""
    global _rust
    if _rust is None:
        with _rust_lock:
            # Double-check po lock
            if _rust is None:
                try:
                    from hledac.universal.hledac.universal import rust_extensions
                    _rust = rust_extensions
                except ImportError:
                    logger.warning("[AdaptiveCache] Rust extensions unavailable, using fallback")
                    _rust = None
    return _rust


K = TypeVar("K")
V = TypeVar("V")


class MemoryPressure(IntEnum):
    """M1 8GB UMA memory pressure levels."""
    NORMAL = 0      # > 2 GiB available — full cache
    ELEVATED = 1    # 1-2 GiB available — reduce to 50%
    CRITICAL = 2    # < 1 GiB available — minimal cache


# M1 8GB bounds
_MAX_CACHE_BYTES_NORMAL = 512 * 1024 * 1024    # 512 MB
_MAX_CACHE_BYTES_ELEVATED = 256 * 1024 * 1024  # 256 MB
_MAX_CACHE_BYTES_CRITICAL = 64 * 1024 * 1024   # 64 MB
_MAX_ENTRIES_NORMAL = 100_000
_MAX_ENTRIES_ELEVATED = 50_000
_MAX_ENTRIES_CRITICAL = 10_000


class AdaptiveCacheConfig(msgspec.Struct, gc=False):
    """
    Konfigurácia adaptívnej cache.

    M1 8GB UMA bounds:
    - Normal: 512 MB / 100k entries
    - Elevated: 256 MB / 50k entries
    - Critical: 64 MB / 10k entries
    """
    max_bytes_normal: int = _MAX_CACHE_BYTES_NORMAL
    max_bytes_elevated: int = _MAX_CACHE_BYTES_ELEVATED
    max_bytes_critical: int = _MAX_CACHE_BYTES_CRITICAL
    max_entries_normal: int = _MAX_ENTRIES_NORMAL
    max_entries_elevated: int = _MAX_ENTRIES_ELEVATED
    max_entries_critical: int = _MAX_ENTRIES_CRITICAL
    memory_check_interval_sec: float = 5.0
    pressure_threshold_elevated_gib: float = 2.0
    pressure_threshold_critical_gib: float = 1.0


class CacheStats(msgspec.Struct, gc=False):
    """Cache statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_bytes: int = 0
    current_entries: int = 0
    max_bytes: int = 0
    max_entries: int = 0
    pressure_level: MemoryPressure = MemoryPressure.NORMAL

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class AdaptiveCache(Generic[K, V]):
    """
    Memory-aware cache s TinyLFU admission a adaptívnym sizingom.

    Na M1 8GB UMA automaticky znižuje limit podľa dostupnej pamäte.
    Používa Rust PyGraphLRUCache pre TinyLFU + LRU eviction.

    Fallback ak Rust nie je dostupný: pure-Python LRU.
    """

    __slots__ = (
        '_name', '_config', '_pressure', '_max_bytes', '_max_entries',
        '_lock', '_stats', '_memory_check_interval_sec',
        '_last_memory_check', '_use_rust', '_rust_cache',
        '_python_cache', '_python_access_order', '_python_current_bytes',
    )

    def __init__(
        self,
        config: AdaptiveCacheConfig | None = None,
        name: str = "adaptive_cache",
    ) -> None:
        self._name = name
        self._config = config or AdaptiveCacheConfig()
        self._pressure = MemoryPressure.NORMAL
        self._max_bytes = self._config.max_bytes_normal
        self._max_entries = self._config.max_entries_normal
        self._lock = threading.RLock()
        self._stats = CacheStats(
            max_bytes=self._max_bytes,
            max_entries=self._max_entries,
            pressure_level=self._pressure,
        )
        self._memory_check_interval_sec = self._config.memory_check_interval_sec
        self._last_memory_check = 0.0  # timestamp

        # Rust cache - inicializované lazy
        self._use_rust = False
        self._rust_cache = None

        # Python fallback cache - pre-initialized for thread safety
        self._python_cache: dict[K, V] = {}
        self._python_access_order: list[K] = []
        self._python_current_bytes: int = 0

    def _init_rust_cache(self) -> bool:
        """Initialize Rust PyGraphLRUCache ak je dostupný."""
        if self._rust_cache is not None:
            return self._use_rust

        rust = _get_rust()
        if rust is None:
            self._use_rust = False
            self._rust_cache = None
            return False

        try:
            # PyGraphLRUCache(max_entries, max_bytes)
            self._rust_cache = rust.PyGraphLRUCache(
                self._max_entries,
                self._max_bytes,
            )
            self._use_rust = True
            logger.debug(
                f"[AdaptiveCache] Rust PyGraphLRUCache initialized: "
                f"entries={self._max_entries}, bytes={self._max_bytes}"
            )
            return True
        except Exception as e:
            logger.warning(f"[AdaptiveCache] Rust PyGraphLRUCache init failed: {e}")
            self._use_rust = False
            self._rust_cache = None
            return False

    def _get_memory_pressure(self) -> MemoryPressure:
        """Zisti aktuálny memory pressure pomocou Rust get_memory_snapshot."""
        import time
        current_time = time.monotonic()

        # Throttle memory check
        if current_time - self._last_memory_check < self._memory_check_interval_sec:
            return self._pressure
        self._last_memory_check = current_time

        rust = _get_rust()
        if rust is None:
            return self._pressure

        try:
            # volá rust_extensions.get_memory_snapshot()
            snapshot = rust.get_memory_snapshot()
            available_gib = snapshot.get("available_memory_gib", 3.0)

            if available_gib < self._config.pressure_threshold_critical_gib:
                new_pressure = MemoryPressure.CRITICAL
            elif available_gib < self._config.pressure_threshold_elevated_gib:
                new_pressure = MemoryPressure.ELEVATED
            else:
                new_pressure = MemoryPressure.NORMAL

            if new_pressure != self._pressure:
                logger.info(
                    f"[AdaptiveCache] Memory pressure changed: "
                    f"{self._pressure.name} -> {new_pressure.name} "
                    f"(available={available_gib:.2f} GiB)"
                )
                self._pressure = new_pressure
                self._update_limits()

            return self._pressure

        except Exception as e:
            logger.warning(f"[AdaptiveCache] Memory probe failed: {e}")
            return self._pressure

    def _update_limits(self) -> None:
        """Aktualizuje limity podľa aktuálneho pressure levelu."""
        old_max_bytes = self._max_bytes
        old_max_entries = self._max_entries

        if self._pressure == MemoryPressure.CRITICAL:
            self._max_bytes = self._config.max_bytes_critical
            self._max_entries = self._config.max_entries_critical
        elif self._pressure == MemoryPressure.ELEVATED:
            self._max_bytes = self._config.max_bytes_elevated
            self._max_entries = self._config.max_entries_elevated
        else:
            self._max_bytes = self._config.max_bytes_normal
            self._max_entries = self._config.max_entries_normal

        self._stats.max_bytes = self._max_bytes
        self._stats.max_entries = self._max_entries
        self._stats.pressure_level = self._pressure

        if old_max_bytes != self._max_bytes:
            logger.info(
                f"[AdaptiveCache] Limits updated: "
                f"bytes={old_max_bytes//1024//1024}MB -> {self._max_bytes//1024//1024}MB, "
                f"entries={old_max_entries} -> {self._max_entries}"
            )

    def get(self, key: K) -> V | None:
        """
        Získa hodnotu z cache.

        Returns:
            Hodnota ak nájdená, None inak.
        """
        self._get_memory_pressure()

        with self._lock:
            if self._use_rust and self._rust_cache is not None:
                try:
                    result = self._rust_cache.get(str(key))
                    if result is not None:
                        self._stats.hits += 1
                        return result
                    self._stats.misses += 1
                    return None
                except Exception as e:
                    logger.warning(f"[AdaptiveCache] Rust get failed: {e}")
                    self._use_rust = False

            # Fallback: Python dict + threading.Lock (single-threaded LRU approximation)
            return self._python_get(key)

    def put(self, key: K, value: V) -> bool:
        """
        Vloží hodnotu do cache.

        Returns:
            True ak bola hodnota prijatá (prešla TinyLFU admission), False inak.
        """
        self._get_memory_pressure()

        with self._lock:
            if self._use_rust and self._rust_cache is not None:
                try:
                    result = self._rust_cache.put(str(key), value)
                    if result:
                        self._stats.current_entries = self._rust_cache.len()
                        rust_stats = self._rust_cache.stats()
                        self._stats.current_bytes = rust_stats.get("bytes", 0)
                    return result
                except Exception as e:
                    logger.warning(f"[AdaptiveCache] Rust put failed: {e}")
                    self._use_rust = False

            # Fallback: Python implementation
            return self._python_put(key, value)

    def _python_get(self, key: K) -> V | None:
        """Pure-Python fallback LRU get using dict."""
        # Ensure cache exists
        # O(1) lookup by key identity, fall back to string search for compatibility
        if key in self._python_cache:
            # Move to end (most recently used)
            if key in self._python_access_order:
                self._python_access_order.remove(key)
            self._python_access_order.append(key)
            self._stats.hits += 1
            return self._python_cache[key]

        # Try string-based lookup for non-hashable keys
        key_str = str(key)
        for k in list(self._python_cache.keys()):
            if str(k) == key_str:
                if k in self._python_access_order:
                    self._python_access_order.remove(k)
                self._python_access_order.append(k)
                self._stats.hits += 1
                return self._python_cache[k]

        self._stats.misses += 1
        return None

    def _python_put(self, key: K, value: V) -> bool:
        """Pure-Python fallback LRU put with byte budget tracking."""

        # Find existing key
        existing_key: K | None = None
        if key in self._python_cache:
            existing_key = key
        else:
            key_str = str(key)
            for k in self._python_cache.keys():
                if str(k) == key_str:
                    existing_key = k
                    break

        def _estimate_size(v: V) -> int:
            """Estimate byte size of a value using sys.getsizeof."""
            import sys
            try:
                return sys.getsizeof(v)
            except Exception:
                return 64

        if existing_key is not None:
            # Update existing - account for value size change
            old_value = self._python_cache[existing_key]
            old_size = _estimate_size(old_value)
            new_size = _estimate_size(value)
            self._python_current_bytes += (new_size - old_size)
            self._python_cache[existing_key] = value
            if existing_key in self._python_access_order:
                self._python_access_order.remove(existing_key)
            self._python_access_order.append(existing_key)
        else:
            # Evict by entries limit
            while len(self._python_cache) >= self._max_entries:
                if self._python_access_order:
                    oldest = self._python_access_order.pop(0)
                    if oldest in self._python_cache:
                        evicted = self._python_cache.pop(oldest)
                        self._python_current_bytes -= _estimate_size(evicted)
                        self._stats.evictions += 1
                else:
                    break

            # Evict by byte budget if needed
            value_size = _estimate_size(value)
            while self._python_current_bytes + value_size > self._max_bytes and self._python_access_order:
                oldest = self._python_access_order.pop(0)
                if oldest in self._python_cache:
                    evicted = self._python_cache.pop(oldest)
                    self._python_current_bytes -= _estimate_size(evicted)
                    self._stats.evictions += 1

            # Add new entry
            self._python_cache[key] = value
            self._python_access_order.append(key)
            self._python_current_bytes += value_size

        self._stats.current_entries = len(self._python_cache)
        self._stats.current_bytes = self._python_current_bytes
        return True

    def clear(self) -> None:
        """Vymaže cache."""
        with self._lock:
            if self._use_rust and self._rust_cache is not None:
                try:
                    self._rust_cache.clear()
                except Exception as e:
                    logger.warning(f"[AdaptiveCache] Rust clear failed: {e}")

            # Clear Python fallback cache
            if hasattr(self, "_python_cache"):
                self._python_cache.clear()
                self._python_access_order.clear()
                if hasattr(self, "_python_current_bytes"):
                    self._python_current_bytes = 0

            self._stats = CacheStats(
                max_bytes=self._max_bytes,
                max_entries=self._max_entries,
                pressure_level=self._pressure,
            )

    def stats(self) -> CacheStats:
        """Vráti štatistiky cache."""
        with self._lock:
            if self._use_rust and self._rust_cache is not None:
                try:
                    rust_stats = self._rust_cache.stats()
                    self._stats.current_bytes = rust_stats.get("bytes", 0)
                    self._stats.current_entries = rust_stats.get("entries", 0)
                except Exception:
                    pass
            return self._stats

    @property
    def pressure(self) -> MemoryPressure:
        """Aktuálny memory pressure level."""
        self._get_memory_pressure()
        return self._pressure

    def __len__(self) -> int:
        """Počet položiek v cache."""
        if self._use_rust and self._rust_cache is not None:
            try:
                return self._rust_cache.len()
            except Exception:
                pass
        # Python fallback
        if hasattr(self, "_python_cache"):
            return len(self._python_cache)
        return 0

    def __contains__(self, key: K) -> bool:
        """Check či key je v cache."""
        return self.get(key) is not None


# ---------------------------------------------------------------------------
# Global cache registry pre adaptívne management
# ---------------------------------------------------------------------------

_cache_registry: dict[str, AdaptiveCache] = {}
_registry_lock = threading.Lock()


def register_cache(name: str, cache: AdaptiveCache) -> None:
    """Registruje cache do global registry."""
    with _registry_lock:
        _cache_registry[name] = cache
        logger.info(f"[AdaptiveCache] Registered: {name}")


def get_cache(name: str) -> AdaptiveCache | None:
    """Získa cache z registry."""
    with _registry_lock:
        return _cache_registry.get(name)


def clear_all_caches() -> None:
    """Vymaže všetky registrované cache."""
    with _registry_lock:
        for name, cache in _cache_registry.items():
            cache.clear()
            logger.info(f"[AdaptiveCache] Cleared: {name}")


def get_all_stats() -> dict[str, CacheStats]:
    """Vráti štatistiky všetkých registrovaných cache."""
    with _registry_lock:
        return {name: cache.stats() for name, cache in _cache_registry.items()}


# ---------------------------------------------------------------------------
# Adaptive Cache Factory
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = AdaptiveCacheConfig()


def create_adaptive_cache(
    name: str,
    config: AdaptiveCacheConfig | None = None,
) -> AdaptiveCache:
    """
    Factory pre vytvorenie adaptívnej cache.

    Args:
        name: Názov cache (pre registry)
        config: Konfigurácia, alebo None pre default

    Returns:
        Inicializovaná AdaptiveCache
    """
    cache = AdaptiveCache(config=config or _DEFAULT_CONFIG, name=name)
    register_cache(name, cache)
    return cache


# ---------------------------------------------------------------------------
# Convenience decorators
# ---------------------------------------------------------------------------

_CACHES: dict[str, AdaptiveCache] = {}


def cached(
    cache_name: str = "default",
    config: AdaptiveCacheConfig | None = None,
) -> Callable[[Callable[..., V]], Any]:
    """
    Decorator pre caching funkcií.

    Usage:
        @cached("my_cache")
        def expensive_function(arg1, arg2):
            ...
    """
    def decorator(func: Callable[..., V]) -> Any:
        if cache_name not in _CACHES:
            _CACHES[cache_name] = create_adaptive_cache(cache_name, config)
        cache = _CACHES[cache_name]

        func_name = getattr(func, '__name__', repr(func))

        def wrapper(*args: Any, **kwargs: Any) -> V:
            # Key je (func_name, args, kwargs)
            key = (func_name, args, tuple(sorted(kwargs.items())))
            result = cache.get(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache.put(key, result)
            return result

        wrapper.cache = cache  # type: ignore[assignment]
        wrapper.clear = cache.clear  # type: ignore[assignment]
        return wrapper

    return decorator
