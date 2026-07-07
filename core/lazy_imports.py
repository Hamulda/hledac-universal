"""core/lazy_imports.py — Lazy import registry for heavy modules (F500J §2)

Defer heavy module imports until first attribute access.
Reduces cold-start overhead for --help / diagnostics path.

Usage:
    from core.lazy_imports import LazyImport

    # Define at module level (no import cost yet)
    duckdb = LazyImport("duckdb")
    lancedb = LazyImport("lancedb")

    # First access triggers import
    conn = duckdb.connect(":memory:")   # import happens here
    db = lancedb.connect(...)            # import happens here

Fallback: if module is not installed, returns a stub that raises on use.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType


class LazyImport:
    """
    Deferred import with cached module reference and optional fallback.

    Usage:
        duckdb = LazyImport("duckdb")
        duckdb.connect(...)   # imports on first call
    """

    __slots__ = ("_name", "_module", "_fallback")

    def __init__(self, module_path: str, fallback: Any = None) -> None:
        self._name = module_path
        self._module: ModuleType | None = None
        self._fallback = fallback

    def __call__(self) -> Any:
        """Return the cached module, importing if needed."""
        if self._module is None:
            import importlib

            try:
                self._module = importlib.import_module(self._name)
            except ImportError:
                if self._fallback is not None:
                    return self._fallback
                raise
        return self._module

    def __getattr__(self, attr: str) -> Any:
        """Proxy attribute access to the lazily-loaded module."""
        module = self.__call__()
        try:
            return getattr(module, attr)
        except AttributeError:
            if self._fallback is not None:
                return getattr(self._fallback, attr)
            raise

    @property
    def available(self) -> bool:
        """Check if the module is installed (without triggering import)."""
        import importlib.util

        return importlib.util.find_spec(self._name) is not None


# Pre-configured instances for common heavy modules
duckdb = LazyImport("duckdb")
lancedb = LazyImport("lancedb")
mlx_core = LazyImport("mlx.core")
mlx_embeddings = LazyImport("mlx_embeddings")
torch = LazyImport("torch")
transformers = LazyImport("transformers")
aioquic = LazyImport("aioquic")
selectolax = LazyImport("selectolax")
cryptography = LazyImport("cryptography")
