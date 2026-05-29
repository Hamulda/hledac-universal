"""
Lazy Imports - On-Demand Module Loading
=======================================

Integrated from hledac/utils/lazy_imports.py

Lazy module loader that only imports when accessed.
Delays expensive imports until actually needed, improving startup time.
Tracks usage statistics for performance monitoring.

Example:
    >>> manager = LazyImportManager()
    >>> torch = manager.register('torch')
    >>> # torch is not loaded yet
    >>> torch.tensor([1, 2, 3])  # Now it's loaded
    >>> print(manager.get_stats())
"""

from __future__ import annotations

import functools
import importlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LazyLoadStats:
    """Statistics for lazy loading performance."""
    total_loads: int = 0
    total_time: float = 0.0
    loaded_modules: set[str] = field(default_factory=set)
    failed_modules: set[str] = field(default_factory=set)
    cache_hits: int = 0


class LazyLoader:
    """
    Lazy module loader that only imports when accessed.

    Delays expensive imports until actually needed, improving startup time.
    Tracks usage statistics for performance monitoring.
    """

    def __init__(self, module_name: str, manager: LazyImportManager):
        """
        Initialize lazy loader.

        Args:
            module_name: Name of module to load lazily
            manager: Parent import manager for tracking
        """
        self._module_name = module_name
        self._manager = manager
        self._module: Any | None = None
        self._loaded = False
        self._load_time = 0.0

    def _load(self) -> Any:
        """Load the module if not already loaded."""
        if self._loaded:
            self._manager.stats.cache_hits += 1
            return self._module

        start_time = time.perf_counter()

        try:
            self._module = importlib.import_module(self._module_name)
            self._loaded = True
            self._load_time = time.perf_counter() - start_time

            # Update statistics
            self._manager.stats.total_loads += 1
            self._manager.stats.total_time += self._load_time
            self._manager.stats.loaded_modules.add(self._module_name)

            logger.debug(f"Lazy loaded module: {self._module_name} in {self._load_time:.4f}s")

        except ImportError as e:
            self._manager.stats.failed_modules.add(self._module_name)
            logger.error(f"Failed to lazy load module {self._module_name}: {e}")
            raise

        return self._module

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the loaded module."""
        module = self._load()
        return getattr(module, name)

    def __call__(self, *args, **kwargs):
        """Make the loader callable if the module is callable."""
        module = self._load()
        return module(*args, **kwargs)

    def is_loaded(self) -> bool:
        """Check if module has been loaded."""
        return self._loaded

    def get_load_time(self) -> float:
        """Get time taken to load module."""
        return self._load_time


class LazyImportManager:
    """
    Manager for lazy imports.

    Central registry for lazy-loaded modules with statistics tracking.
    """

    def __init__(self):
        """Initialize lazy import manager."""
        self._loaders: dict[str, LazyLoader] = {}
        self.stats = LazyLoadStats()

    def register(self, module_name: str) -> LazyLoader:
        """
        Register a module for lazy loading.

        Args:
            module_name: Name of module to lazy load

        Returns:
            LazyLoader instance for the module
        """
        if module_name not in self._loaders:
            self._loaders[module_name] = LazyLoader(module_name, self)
        return self._loaders[module_name]

    def get_stats(self) -> dict[str, Any]:
        """Get lazy loading statistics."""
        return {
            'total_loads': self.stats.total_loads,
            'total_time': self.stats.total_time,
            'avg_load_time': self.stats.total_time / max(self.stats.total_loads, 1),
            'loaded_modules': list(self.stats.loaded_modules),
            'failed_modules': list(self.stats.failed_modules),
            'cache_hits': self.stats.cache_hits,
            'registered_modules': list(self._loaders.keys()),
        }

    def reset_stats(self) -> None:
        """Reset statistics."""
        self.stats = LazyLoadStats()

    def preload(self, module_name: str) -> bool:
        """
        Force preload a specific module.

        Args:
            module_name: Name of module to preload

        Returns:
            True if successful, False otherwise
        """
        if module_name in self._loaders:
            try:
                self._loaders[module_name]._load()
                return True
            except ImportError:
                return False
        return False

    def preload_all(self) -> dict[str, bool]:
        """Preload all registered modules."""
        results = {}
        for name in self._loaders:
            results[name] = self.preload(name)
        return results


# Global manager instance
_global_manager: LazyImportManager | None = None


def get_lazy_import_manager() -> LazyImportManager:
    """Get global lazy import manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = LazyImportManager()
    return _global_manager


def lazy_import(module_name: str) -> LazyLoader:
    """
    Convenience function for lazy importing.

    Args:
        module_name: Name of module to lazy load

    Returns:
        LazyLoader for the module

    Example:
        >>> torch = lazy_import('torch')
        >>> np = lazy_import('numpy')
        >>> # Modules not loaded yet
        >>> x = torch.tensor([1.0])  # torch loaded here
    """
    manager = get_lazy_import_manager()
    return manager.register(module_name)


def lazy_import_decorator(module_name: str):
    """
    Decorator for lazy importing in functions.

    Args:
        module_name: Name of module to lazy load

    Returns:
        Decorator function

    Example:
        >>> @lazy_import_decorator('torch')
        ... def process_with_torch(tensor_data, torch=None):
        ...     return torch.tensor(tensor_data)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            module = lazy_import(module_name)
            kwargs[module_name.split('.')[-1]] = module
            return func(*args, **kwargs)
        return wrapper
    return decorator


__all__ = [
    'LazyLoadStats',
    'LazyLoader',
    'LazyImportManager',
    'get_lazy_import_manager',
    'lazy_import',
    'lazy_import_decorator',
]
