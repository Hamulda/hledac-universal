"""
Capability Prober - Runtime dependency detection without boolean flags.

This module provides lazy capability probing without persistent boolean flags.
"""

import asyncio
import importlib
import importlib.util
import logging
from collections import OrderedDict, deque
from functools import cached_property
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bounded cache for probed capabilities (max 128 entries)
_MAX_CACHE_SIZE = 128
_MAX_STATS_MISSED = 100


class _LazyModule:
    """
    Lazy module loader - returns module object on first access.

    Unlike CapabilityProber which just checks existence, this actually imports
    the module when accessed. Useful for heavy dependencies that should be
    loaded only when actually needed.

    Sprint 79c: Enhanced with async ensure_loaded() for parallel loading,
    and fail-fast sync access (never auto-imports).

    Usage:
        mlx_lm = _LazyModule("mlx_lm")
        # Module not loaded yet - use async for loading
        await mlx_lm.ensure_loaded()
        mlx_lm.generate(...)  # Use the module after loading

    For parallel loading:
        light_modules = [_LazyModule("os"), _LazyModule("json")]
        heavy_modules = [_LazyModule("mlx_lm")]
        await asyncio.gather(*(m.ensure_loaded() for m in light_modules), return_exceptions=True)
        for m in heavy_modules:
            await m.ensure_loaded()
    """

    _cache: Dict[str, Any] = {}

    def __init__(self, name: str):
        self._name = name
        self._module: Optional[Any] = None
        self._load_error: Optional[Exception] = None

    def __bool__(self) -> bool:
        """Check if module is available - FAIL-FAST, no auto-load."""
        return self._module is not None

    def __getattr__(self, name: str) -> Any:
        """Access module attributes - FAIL-FAST, never auto-imports."""
        if self._module is None:
            raise RuntimeError(
                f"Module '{self._name}' not loaded. "
                f"Call await ensure_loaded() first"
            )
        return getattr(self._module, name)

    async def ensure_loaded(self) -> Any:
        """Asynchronně načte modul - preferovaný způsob."""
        if self._module is not None:
            return self._module
        if self._load_error is not None:
            raise self._load_error

        # Check global cache first
        if self._name in _LazyModule._cache:
            cached = _LazyModule._cache[self._name]
            if cached is not None:
                self._module = cached
                return self._module
            else:
                raise self._load_error

        # Load in thread to avoid blocking event loop
        try:
            self._module = await asyncio.to_thread(importlib.import_module, self._name)
            _LazyModule._cache[self._name] = self._module
            return self._module
        except Exception as e:
            self._load_error = e
            _LazyModule._cache[self._name] = None
            raise

    def _ensure_loaded(self) -> None:
        """Legacy sync load - for backwards compatibility only."""
        if self._module is None and self._load_error is None:
            try:
                self._module = importlib.import_module(self._name)
                _LazyModule._cache[self._name] = self._module
            except ImportError as e:
                self._module = None
                self._load_error = e
                _LazyModule._cache[self._name] = None


class CapabilityProber:
    """Enhanced capability prober with sync/async methods and hardware detection."""

    def __init__(self):
        self._cache: OrderedDict[str, bool] = OrderedDict()
        self._cache_max = _MAX_CACHE_SIZE
        self._stats = {"hits": 0, "misses": 0, "missed_modules": deque()}

    def has_module(self, name: str) -> bool:
        """
        Synchronous – just find_spec + cache. Never imports.
        name must be importable module path (stdlib or hledac.universal.*).
        For internal modules always use full prefix hledac.universal.
        """
        if name in self._cache:
            self._stats["hits"] += 1
            self._cache.move_to_end(name)
            return self._cache[name]

        try:
            spec = importlib.util.find_spec(name)
        except (ModuleNotFoundError, ValueError):
            spec = None
        exists = spec is not None
        self._cache[name] = exists

        if not exists:
            self._stats["misses"] += 1
            self._stats["missed_modules"].append(name)
            if len(self._stats["missed_modules"]) > _MAX_STATS_MISSED:
                self._stats["missed_modules"].popleft()
        else:
            self._stats["hits"] += 1

        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return exists

    async def aget_module(self, name: str, timeout: float = 2.0):
        """
        Asynchronous – import in executor with timeout.
        name must be fully qualified.
        """
        loop = asyncio.get_running_loop()
        try:
            module = await asyncio.wait_for(
                loop.run_in_executor(None, importlib.import_module, name),
                timeout=timeout
            )
            return module
        except (asyncio.TimeoutError, ImportError):
            self._stats["misses"] += 1
            self._stats["missed_modules"].append(name)
            if len(self._stats["missed_modules"]) > _MAX_STATS_MISSED:
                self._stats["missed_modules"].popleft()
            return None

    async def aget_class(self, module_name: str, class_name: str, timeout: float = 2.0):
        """Asynchronously loads class from module."""
        module = await self.aget_module(module_name, timeout=timeout)
        if module is None:
            return None
        return getattr(module, class_name, None)

    def get_class(self, module_name: str, class_name: str):
        """
        Synchronous – loads class (imports!). Call only during initialization,
        not in hot path. Use aget_class otherwise.
        """
        if not self.has_module(module_name):
            return None
        try:
            module = importlib.import_module(module_name)
            return getattr(module, class_name, None)
        except ImportError:
            return None

    def require(self, name: str, reason: str):
        """Raises ImportError if module is not available."""
        if not self.has_module(name):
            raise ImportError(f"{reason}: missing {name}")

    @cached_property
    def has_ane(self) -> bool:
        """Detects ANE (Apple Neural Engine) availability - lazy, cached."""
        try:
            import mlx.core as mx
            return hasattr(mx.metal, "get_ane_utilization")
        except ImportError:
            return False

    @cached_property
    def has_metal(self) -> bool:
        """Detects Metal (GPU) availability via MLX - lazy, cached."""
        try:
            import mlx.core as mx
            return mx.metal.is_available()
        except ImportError:
            return False

    def stats(self) -> dict:
        """
        Returns copy of statistics (no modification, no blocking operations).
        """
        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "missed_modules": self._stats["missed_modules"][:],
            "cache_size": len(self._cache),
            "has_ane": self.has_ane,
            "has_metal": self.has_metal,
        }

    def clear_cache(self) -> None:
        """Clear the capability cache."""
        self._cache.clear()


# Global singleton for production
_PROBER: Optional[CapabilityProber] = None


def get_prober() -> CapabilityProber:
    """Returns global CapabilityProber singleton."""
    global _PROBER
    if _PROBER is None:
        _PROBER = CapabilityProber()
    return _PROBER


# Legacy compatibility functions
def probe_import(module: str, attr: Optional[str] = None) -> Any:
    """
    Probe if a module/attribute is importable.
    DEPRECATED: Use CapabilityProber.has_module() instead.
    """
    prober = get_prober()
    if not prober.has_module(module):
        return None

    try:
        mod = importlib.import_module(module)
        if attr:
            return getattr(mod, attr, None)
        return mod
    except ImportError:
        return None


def probe_call(fn: Callable, *args, **kwargs) -> Any:
    """Probe if a callable succeeds. DEPRECATED: Use try/except directly."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.debug(f"Probe call failed: {fn.__name__}: {e}")
        return None


def clear_cache() -> None:
    """Clear the capability cache. DEPRECATED: Use prober.clear_cache()."""
    if _PROBER:
        _PROBER.clear_cache()


def get_cache_stats() -> Dict[str, int]:
    """Get cache statistics. DEPRECATED: Use prober.stats()."""
    if _PROBER:
        s = _PROBER.stats()
        return {"size": s["cache_size"], "max_size": _MAX_CACHE_SIZE}
    return {"size": 0, "max_size": _MAX_CACHE_SIZE}
