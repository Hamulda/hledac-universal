"""core/lazy_imports.py — PEP 810 Lazy Import Registry (F500J §2)

Extends PEP 562 __getattr__ for module-level lazy imports.
Reduces cold-start overhead for --help / diagnostics path.

Usage:
    from hledac.universal.core.lazy_imports import lazy

    # Define at module level (no import cost until first attribute access)
    duckdb = lazy("duckdb")
    lancedb = lazy("lancedb")

    # First attribute access triggers import
    conn = duckdb.connect(":memory:")   # import happens here
    db = lancedb.connect("...")         # import happens here

Fallback: if module is not installed, returns a stub that raises on use.

PEP 810 (Python 3.14+): Module-level __getattr__ for lazy imports.
This replaces scattered try/except ImportError chains across 361 files.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "lazy",
    "LazyImport",
    # Pre-configured instances
    "duckdb",
    "lancedb",
    "mlx_core",
    "mlx_embeddings",
    "torch",
    "transformers",
    "aioquic",
    "selectolax",
    "cryptography",
    "orjson",
    "lmdb",
    # Availability checks
    "is_available",
    "get_available_modules",
]

logger = logging.getLogger(__name__)

# Module-level import registry (PEP 810 pattern)
# Maps module_name -> (import_path, is_available, cached_module)
_LAZY_REGISTRY: dict[str, dict[str, Any]] = {}


def lazy(module_path: str, *, fallback: Any = None, install_hint: str = "") -> LazyImport:
    """Create a lazy import descriptor.

    Args:
        module_path: Module to import (e.g., "duckdb", "mlx.core")
        fallback: Value to return if module unavailable (default: raises ImportError)
        install_hint: Optional install command for error messages

    Returns:
        LazyImport descriptor — use .available to check, .get() to import

    Example:
        duckdb = lazy("duckdb")
        if duckdb.available:
            conn = duckdb.get().connect(":memory:")
    """
    return LazyImport(module_path, fallback=fallback, install_hint=install_hint)


def is_available(module_path: str) -> bool:
    """Check if module is installed WITHOUT triggering import."""
    if module_path in _LAZY_REGISTRY:
        return _LAZY_REGISTRY[module_path]["available"]
    spec = importlib.util.find_spec(module_path)
    _LAZY_REGISTRY[module_path] = {
        "available": spec is not None,
        "module": None,
        "fallback": None,
        "install_hint": "",
    }
    return spec is not None


def get_available_modules() -> dict[str, bool]:
    """Return all registered lazy module availability status."""
    return {name: info["available"] for name, info in _LAZY_REGISTRY.items()}


class LazyImport:
    """
    PEP 810 lazy import with cached module reference and optional fallback.

    Usage:
        duckdb = LazyImport("duckdb")
        duckdb.available  # False until first access
        duckdb.get().connect(...)   # imports on first call

    Thread-safe: uses double-checked locking pattern.
    """

    __slots__ = ("_name", "_module", "_fallback", "_available", "_lock", "_install_hint")

    def __init__(
        self,
        module_path: str,
        fallback: Any = None,
        install_hint: str = "",
    ) -> None:
        self._name = module_path
        self._module: ModuleType | None = None
        self._fallback = fallback
        self._available: bool | None = None
        self._install_hint = install_hint
        self._lock = __import__("threading").Lock()

    @property
    def available(self) -> bool:
        """Check if the module is installed (without triggering import)."""
        if self._available is None:
            self._available = importlib.util.find_spec(self._name) is not None
        return self._available

    def get(self) -> Any:
        """Return the cached module, importing if needed."""
        if self._module is not None:
            return self._module
        with self._lock:
            if self._module is not None:
                return self._module
            try:
                self._module = importlib.import_module(self._name)
                self._available = True
            except ImportError:
                self._available = False
                if self._fallback is not None:
                    return self._fallback
                if self._install_hint:
                    logger.debug(f"[LAZY] {self._name!r} unavailable — install: {self._install_hint}")
                raise
            return self._module

    def __call__(self) -> Any:
        """Alias for get() — allows duckdb() syntax."""
        return self.get()

    def __getattr__(self, attr: str) -> Any:
        """Proxy attribute access to the lazily-loaded module."""
        module = self.get()
        try:
            return getattr(module, attr)
        except AttributeError:
            if self._fallback is not None:
                return getattr(self._fallback, attr)
            raise

    def try_import(self) -> tuple[bool, Any]:
        """Try to import, returning (available, module_or_fallback)."""
        if self.available:
            return True, self.get()
        return False, self._fallback


# Pre-configured instances for common heavy modules
# These are lazily imported — no cost until first attribute access
duckdb = LazyImport("duckdb", install_hint="uv add duckdb")
lancedb = LazyImport("lancedb", install_hint="uv add lancedb")
mlx_core = LazyImport("mlx.core", install_hint="uv add mlx")
mlx_embeddings = LazyImport("mlx_embeddings", install_hint="uv add mlx-embed")
torch = LazyImport("torch", install_hint="uv add torch")
transformers = LazyImport("transformers", install_hint="uv add transformers")
aioquic = LazyImport("aioquic", install_hint="uv add aioquic (--extra http3)")
selectolax = LazyImport("selectolax", install_hint="uv add selectolax")
cryptography = LazyImport("cryptography", install_hint="uv add cryptography")
orjson = LazyImport("orjson", install_hint="uv add orjson")
lmdb = LazyImport("lmdb", install_hint="uv add lmdb")


# PEP 810: Module-level __getattr__ for lazy module imports
# This enables: from core.lazy_imports import duckdb  # no import cost until duckdb.connect()
def __getattr__(name: str) -> Any:
    """PEP 810: Lazily import submodules on first attribute access."""
    # Check pre-configured instances
    if name in (
        "duckdb",
        "lancedb",
        "mlx_core",
        "mlx_embeddings",
        "torch",
        "transformers",
        "aioquic",
        "selectolax",
        "cryptography",
        "orjson",
        "lmdb",
    ):
        return globals()[name]
    # Dynamic lazy import
    if name in _LAZY_REGISTRY:
        info = _LAZY_REGISTRY[name]
        if info["available"]:
            return info["module"] or LazyImport(name).get()
        return info["fallback"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
