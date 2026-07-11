"""
Capability Prober - Runtime dependency detection without boolean flags.

This module provides lazy capability probing without persistent boolean flags.
"""


import asyncio
from utils.async_helpers import safe_gather_ok
import importlib
import importlib.util
import logging
from collections import OrderedDict, deque
from collections.abc import Callable
from functools import cached_property
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.system_detector import HardwareCapabilities

logger = logging.getLogger(__name__)

# Bounded cache for probed capabilities (max 128 entries)
_MAX_CACHE_SIZE = 128
_MAX_STATS_MISSED = 100

# Whitelist of allowed module names for importlib.import_module()
# Defense-in-depth: prevents arbitrary code execution if untrusted input reaches these functions
_ALLOWED_LAZY_MODULES: frozenset[str] = frozenset({
    # Stdlib
    "os", "json", "time", "re", "sys", "logging", "asyncio", "threading",
    "pathlib", "functools", "collections", "copy", "inspect", "traceback",
    "warnings", "weakref", "types", "gc", "io", "abc", "contextlib",
    # MLX ecosystem
    "mlx", "mlx.core", "mlx_lm",
    # Scientific computing
    "numpy", "pandas", "scipy",
    # HTTP/networking
    "aiohttp", "httpx", "curl_cffi", "requests",
    # Database
    "duckdb", "lancedb", "lmdb", "sqlite3",
    # Graph analytics
    "igraph",
    # Parsing
    "orjson", "msgspec", "pydantic",
    # Cryptography
    "cryptography", "hashlib", "hmac", "secrets",
    # Browser automation
    "nodriver", "playwright",
    # Academic/scientific APIs
    "arxiv", "openalex", "unpaywall", "core",
    # Optional ML
    "torch", "transformers", "sentence_transformers",
    # Compression
    "zstandard", "zlib", "gzip", "bz2", "lz4",
    # OSINT utilities
    "psutil", "yara_python",
    # Project modules
    "hledac.universal", "hledac.universal.brain",
    "hledac.universal.knowledge", "hledac.universal.fetching",
    "hledac.universal.discovery", "hledac.universal.coordinators",
})


def _validate_lazy_module(name: str) -> None:
    """Validate module name against whitelist. Raises ImportError if not allowed."""
    if name not in _ALLOWED_LAZY_MODULES:
        raise ImportError(
            f"Module '{name}' not in whitelist. "
            f"Dynamic module loading requires explicit allowlisting. "
            f"Allowed modules: {sorted(_ALLOWED_LAZY_MODULES) if len(_ALLOWED_LAZY_MODULES) <= 20 else 'see _ALLOWED_LAZY_MODULES'}"
        )


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
        # F314: migrated asyncio.gather -> safe_gather_ok
        await safe_gather_ok(*(m.ensure_loaded() for m in light_modules), label="capability_prober:load_light")
        for m in heavy_modules:
            await m.ensure_loaded()
    """

    _cache: dict[str, Any] = {}

    def __init__(self, name: str):
        self._name = name
        self._module: Any | None = None
        self._load_error: Exception | None = None

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
                # ty: `_load_error` is `Exception | None`. In practice a
                # cached `None` is only written after `_ensure_loaded` /
                # `ensure_loaded` set `_load_error` — but the static type
                # can't see that. Guard with an explicit None check.
                if self._load_error is not None:
                    raise self._load_error
                raise RuntimeError(
                    f"Module '{self._name}' cached as None without load error"
                )

        # Load in thread to avoid blocking event loop
        try:
            _validate_lazy_module(self._name)
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
                # Whitelist validated at construction time; double-check for defense-in-depth
                _validate_lazy_module(self._name)  # noqa: S608 — validated by whitelist
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
        # ty: explicit annotation — dict literal is inferred as deque[Unknown]
        # in the absence of a declared type. `Any` covers both int counters
        # and the bounded deque of missed module names.
        self._stats: dict[str, Any] = {"hits": 0, "misses": 0, "missed_modules": deque()}

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
        try:
            # Whitelist validation before import — defense-in-depth
            _validate_lazy_module(name)  # noqa: S608 — validated by whitelist
            async with asyncio.timeout(timeout):
                module = await asyncio.to_thread(importlib.import_module, name)
            return module
        except (TimeoutError, ImportError):
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
            # Whitelist validated above in has_module(); double-check here for defense-in-depth
            _validate_lazy_module(module_name)  # noqa: S608 — validated by whitelist
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

    @cached_property
    def hardware_capabilities(self) -> "HardwareCapabilities":
        """
        Get hardware capabilities from SystemDetector.

        Lazy import to avoid circular dependencies.
        """
        from core.system_detector import get_hardware_capabilities
        return get_hardware_capabilities()

    @cached_property
    def ram_tier(self) -> str:
        """RAM tier: '8gb', '16gb', '32gb', '64gb', 'other'."""
        return self.hardware_capabilities.ram_tier

    @cached_property
    def is_m1_8gb(self) -> bool:
        """True only on M1 MacBook Air 8GB."""
        return self.hardware_capabilities.is_m1_8gb

    @cached_property
    def max_memory_mb(self) -> int:
        """Adaptive max_memory_mb based on RAM tier."""
        return self.hardware_capabilities.max_memory_mb

    @cached_property
    def max_concurrent_tools(self) -> int:
        """Adaptive max_concurrent_tools based on RAM tier."""
        return self.hardware_capabilities.max_concurrent_tools

    def stats(self) -> dict:
        """
        Returns copy of statistics (no modification, no blocking operations).
        """
        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "missed_modules": list(self._stats["missed_modules"]),
            "cache_size": len(self._cache),
            "has_ane": self.has_ane,
            "has_metal": self.has_metal,
        }

    def clear_cache(self) -> None:
        """Clear the capability cache."""
        self._cache.clear()


# Global singleton for production
_PROBER: CapabilityProber | None = None


def get_prober() -> CapabilityProber:
    """Returns global CapabilityProber singleton."""
    global _PROBER
    if _PROBER is None:
        _PROBER = CapabilityProber()
    return _PROBER


# Legacy compatibility functions
def probe_import(module: str, attr: str | None = None) -> Any:
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
        # ty: not all `Callable` instances carry `__name__` (e.g. functools.partial
        # bound methods, instances with `__call__` only). Use getattr + fallback
        # to `repr` for safe logging.
        fn_name = getattr(fn, "__name__", None) or repr(fn)
        logger.debug(f"Probe call failed: {fn_name}: {e}")
        return None


def clear_cache() -> None:
    """Clear the capability cache. DEPRECATED: Use prober.clear_cache()."""
    if _PROBER:
        _PROBER.clear_cache()


def get_cache_stats() -> dict[str, int]:
    """Get cache statistics. DEPRECATED: Use prober.stats()."""
    if _PROBER:
        s = _PROBER.stats()
        return {"size": s["cache_size"], "max_size": _MAX_CACHE_SIZE}
    return {"size": 0, "max_size": _MAX_CACHE_SIZE}

