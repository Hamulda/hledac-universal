"""
_core.module_state — PEP 773-compatible module state management.

Modern replacement for global state anti-pattern. Provides:
- Thread-safe, clearable module state via dataclass
- Backward compatibility via __getattr__ lazy loading
- Memory-efficient with slots=True
- M1 8GB friendly (minimal footprint)

Architecture:
    Per-module singleton state class + module-level accessor.
    For Python 3.14+, use PEP 773 module state directly.

Usage:
    from _core.module_state import _state, get_module_state

    # Access lazy-loaded values
    value = _state.get_or_create("key", factory_fn)

    # Clear for sprint winddown
    _state.clear_between_sprints()

Anti-pattern it replaces:
    _cache: dict = {}
    def get_something():
        global _cache
        if "key" not in _cache:
            _cache["key"] = compute()
        return _cache["key"]
"""
from __future__ import annotations

import gc
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


# ── Thread-safe singleton registry ────────────────────────────────────────────


@dataclass(slots=True)
class ModuleState:
    """
    Thread-safe, clearable module state container.

    Benefits over raw module globals:
    1. Centralized state = easier debugging
    2. Clearable = prevents memory leaks between sprints
    3. Thread-safe = no race conditions
    4. slots=True = memory efficient on M1 8GB

    PEP 773 note: Python 3.14 will have native module state via
    __get_state__/__set_state__. Until then, this provides the
    same benefits in a compatible way.
    """

    # Lazy-loaded caches (lazy_caches: dict[str, Any] = field(default_factory=dict))
    lazy_caches: dict[str, Any] = field(default_factory=dict)

    # Attribute index for __getattr__ fallback
    attribute_index: dict[str, str] | None = None

    # Loaded engines/instances (heavy resources)
    loaded_engines: dict[str, object] = field(default_factory=dict)

    # Memory pressure tracking
    memory_pressure: str = "normal"

    # Initialization flag
    initialized: bool = False

    # Thread lock for concurrent access
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def get_or_create(
        self,
        key: str,
        factory: Callable[[], T],
    ) -> T:
        """
        Get cached value or create via factory (thread-safe).

        This replaces:
            global _cache
            if key not in _cache:
                _cache[key] = factory()
            return _cache[key]
        """
        with self._lock:
            if key in self.lazy_caches:
                return self.lazy_caches[key]

            value = factory()
            self.lazy_caches[key] = value
            return value

    def set_if_none(self, key: str, factory: Callable[[], T]) -> T:
        """
        Set value only if not already set (idempotent, thread-safe).

        Unlike get_or_create, this doesn't call factory if already set.
        Useful for singleton patterns where multiple calls may race.
        """
        with self._lock:
            if key in self.lazy_caches:
                return self.lazy_caches[key]

            value = factory()
            self.lazy_caches[key] = value
            return value

    def get(self, key: str, default: T | None = None) -> T | None:
        """Get cached value without creation."""
        return self.lazy_caches.get(key, default)

    def set(self, key: str, value: T) -> None:
        """Set cached value."""
        with self._lock:
            self.lazy_caches[key] = value

    def clear(self) -> None:
        """
        Light clear — keeps structure, clears data.

        Use between related operations to free memory while
        maintaining the cache structure.
        """
        with self._lock:
            self.lazy_caches.clear()
            self.attribute_index = None

    def clear_between_sprints(self) -> None:
        """
        Deep clear for sprint boundaries — prevents memory leaks.

        Clears:
        - All lazy caches
        - Loaded engines (with unload if supported)
        - Attribute index
        - Triggers garbage collection

        MUST be called at sprint winddown.
        """
        with self._lock:
            # Clear lazy caches
            self.lazy_caches.clear()

            # Clear engines with unload if supported
            for name in list(self.loaded_engines.keys()):
                engine = self.loaded_engines.pop(name, None)
                if engine is not None and hasattr(engine, "unload"):
                    try:
                        engine.unload()
                    except Exception:
                        pass  # Best effort unload
                del engine

            self.loaded_engines.clear()
            self.attribute_index = None
            self.initialized = False

        # Force GC after clearing heavy resources
        gc.collect()

    def clear_engine(self, name: str) -> bool:
        """
        Clear specific engine to free memory.

        Returns True if engine was found and cleared.
        """
        with self._lock:
            if name not in self.loaded_engines:
                return False

            engine = self.loaded_engines.pop(name)
            if hasattr(engine, "unload"):
                try:
                    engine.unload()
                except Exception:
                    pass
            del engine
            return True

    def register_engine(self, name: str, engine: object) -> None:
        """Register a heavy resource (engine) for managed lifecycle."""
        with self._lock:
            self.loaded_engines[name] = engine

    @property
    def cache_size(self) -> int:
        """Current cache entry count (for monitoring)."""
        return len(self.lazy_caches)

    @property
    def engine_count(self) -> int:
        """Current loaded engine count (for monitoring)."""
        return len(self.loaded_engines)

    def __repr__(self) -> str:
        return (
            f"ModuleState("
            f"caches={len(self.lazy_caches)}, "
            f"engines={len(self.loaded_engines)}, "
            f"pressure={self.memory_pressure})"
        )


# ── Module-level singleton (backward compatibility) ────────────────────────────
#
# ISSUE-002: Add atexit handler for automatic cleanup at process exit.
# This ensures all module state is properly released when the process terminates.


import atexit as _atexit

# NOTE: This is the module-level state instance that replaces
# scattered global _cache, _pool, etc. variables.
#
# Migration path:
#   BEFORE: global _cache; _cache[key] = value
#   AFTER:  from _core.module_state import _state; _state.set(key, value)
#
# For backward compatibility with existing global patterns, we also
# provide __getattr__ at the module level.

_state: ModuleState = ModuleState()

# Register atexit handler for automatic cleanup
_atexit.register(_state.clear_between_sprints)


def get_module_state() -> ModuleState:
    """
    Get the global module state instance.

    Use this in tests or when you need direct access to the state
    for inspection or advanced operations.
    """
    return _state


def clear_module_state() -> None:
    """
    Clear all module state (light clear).

    Alias for _state.clear(). Use for mid-operation cleanup.
    """
    _state.clear()


def clear_between_sprints() -> None:
    """
    Clear module state between sprints.

    Alias for _state.clear_between_sprints().
    MUST be called at sprint winddown to prevent memory leaks.
    """
    _state.clear_between_sprints()


# ── Module-level __getattr__ for backward compatibility ───────────────────────
#
# This allows existing code that uses `from _core.module_state import _xxx`
# to continue working, while routing through the centralized state.
#
# IMPORTANT: Only intended for gradual migration. New code should use
# _state.get_or_create() or _state.set()/get() directly.


def __getattr__(name: str) -> Any:
    """
    Lazy module-level attribute access via centralized state.

    Supports:
    - _state._cache[key] → lazy_caches[key]
    - _state._engines[name] → loaded_engines[name]

    For backward compatibility with code like:
        from _core.module_state import _cache
        _cache[key] = value

    Modern code should use:
        from _core.module_state import _state
        _state.set(key, value)
    """
    if name.startswith("_"):
        # Handle backward-compat aliases
        if name == "_cache" or name == "_lazy_caches":
            return _state.lazy_caches
        if name == "_engines" or name == "_loaded_engines":
            return _state.loaded_engines
        if name == "_attribute_index":
            return _state.attribute_index
        if name == "_initialized":
            return _state.initialized
        if name == "_memory_pressure":
            return _state.memory_pressure

    raise AttributeError(f"module '_core.module_state' has no attribute '{name}'")


def __dir__() -> list[str]:
    """Support for dir() on this module."""
    return [
        "_state",
        "ModuleState",
        "get_module_state",
        "clear_module_state",
        "clear_between_sprints",
    ]
