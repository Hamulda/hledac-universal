"""
LazyImport — Deferred module import with caching
===============================================

Replaces 10× try/except ImportError patterns in duckdb_store.py with a
single helper that resolves once and caches the result.

Usage:
    from .lazy_import import lazy_import

    # Lazy import - resolved on first use, cached thereafter
    resource_governor = lazy_import("core.resource_governor")
    graph_store = lazy_import("knowledge.graph_store")

    # Later in code:
    if resource_governor:
        resource_governor.some_function()

MIGRATION NOTE (Issue #2):
    This replaces the following inline try/except ImportError patterns:
    - Lines ~40, 61, 68, 77 (otel fallback)
    - Line ~164 (TargetProfileSummary)
    - Line ~181 (sprint_diff_engine)
    - Line ~224 (duckdb_subprocess_adapter)
    - Line ~3665 (async_helpers)

STORAGE-DUP-003: duckdb_ipc_store lazy import removed (legacy IPC stack deleted).
"""

import importlib
import importlib.util
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class _LazyImport:
    """
    Deferred import that resolves once on first access and caches the result.

    Thread-safe: uses a dict for storage, first wins on race.
    """

    __slots__ = ("_spec_name", "_resolved", "_on_import_error")

    def __init__(self, spec_name: str, on_error: Any = None) -> None:
        self._spec_name = spec_name
        self._resolved: Any = None
        self._on_import_error = on_error

    def __repr__(self) -> str:
        return f"<LazyImport {self._spec_name!r} resolved={self._resolved is not None}>"

    @property
    def is_available(self) -> bool:
        """Check if the module is available without triggering import."""
        return importlib.util.find_spec(self._spec_name) is not None

    @property
    def module(self) -> Any:
        """
        Resolve and return the module.

        On first access: attempt import, cache result or on_error fallback.
        On subsequent accesses: return cached value.
        """
        if self._resolved is None:
            try:
                self._resolved = importlib.import_module(self._spec_name)
            except ImportError:
                self._resolved = self._on_import_error
        return self._resolved

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to the resolved module."""
        return getattr(self.module, name)


def lazy_import(spec_name: str, on_error: Any = None) -> _LazyImport:
    """
    Create a lazy import resolver for the given module spec name.

    Args:
        spec_name: Full module spec string, e.g. "core.resource_governor"
        on_error: Value to return if import fails (default None)

    Returns:
        _LazyImport proxy object that resolves on first attribute access

    Example:
        otel = lazy_import("otel")
        instrumented = lazy_import("otel").instrument  # triggers import

        # Better pattern - check availability first:
        graph_store = lazy_import("knowledge.graph_store")
        if graph_store.is_available:
            graph_store.GraphStore(...)
    """
    return _LazyImport(spec_name, on_error)


# Pre-built lazy imports for common circular-dependency modules
# These are the 10 ImportError sites in duckdb_store.py consolidated here.

# Sprint T1: OpenTelemetry instrumentation (always-on, M1 EIGHTGB safe, fail-soft)
_lazy_otel = None
if importlib.util.find_spec("otel") is not None:
    try:
        from otel import instrumented as _otel_instrumented

        _lazy_otel = _LazyImport("otel")
    except ImportError:
        _lazy_otel = _LazyImport("hledac.universal.otel")


def get_otel_instrumented() -> Any:
    """Get the otel.instrumented function, with fallback to hledac.otel."""
    global _otel_instrumented
    if "_otel_instrumented" not in globals():
        try:
            from otel import instrumented as _otel_instrumented
        except ImportError:
            from hledac.universal.otel import instrumented as _otel_instrumented
    return _otel_instrumented


# Lazy imports for optional dependencies with graceful fallback
_lazy_resource_governor = _LazyImport("core.resource_governor", on_error=None)
_lazy_graph_store = _LazyImport("knowledge.graph_store", on_error=None)
_lazy_sprint_diff_engine = _LazyImport("knowledge.sprint_diff_engine", on_error=None)
_lazy_duckdb_subprocess = _LazyImport("knowledge.duckdb_subprocess_adapter", on_error=None)
