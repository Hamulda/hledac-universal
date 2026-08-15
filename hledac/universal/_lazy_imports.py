"""
hledac/universal/_lazy_imports.py — Core Service Factory Module

PEP 810 Lazy Import Pattern for 5 Critical Core Services
=========================================================

Provides fail-soft factory functions that lazy-load the actual class
implementations only when first accessed. Designed for M1 8GB + Python 3.14+.

SERVICES:
  - DuckDBShadowStore   (knowledge/duckdb_store.py)
  - M1ResourceGovernor  (runtime/resource_governor.py)
  - Hermes3Engine      (brain/hermes3_engine.py)
  - EvidenceLog        (evidence_log.py)
  - SidecarOrchestrator (runtime/sidecar_orchestrator.py)

PATTERN: Each factory returns the CLASS (not instance), allowing
the caller to invoke it with their own parameters (e.g., SidecarOrchestrator
needs result_sink, governor, scheduler).

USAGE:
    from hledac.universal._lazy_imports import get_DuckDBShadowStore

    DuckDBShadowStore = get_DuckDBShadowStore()
    store = DuckDBShadowStore()

If the underlying module fails to import (e.g., missing optional dependency),
the factory raises a clear ImportError with installation hint.

F350M-R: Centralized lazy import registry for scheduler_v2.
FACT: Module was referenced but never existed → all 5 services silently None.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "get_DuckDBShadowStore",
    "get_M1ResourceGovernor",
    "get_Hermes3Engine",
    "get_EvidenceLog",
    "get_SidecarOrchestrator",
    "LazyServiceInfo",
    "get_all_service_status",
]

logger = logging.getLogger(__name__)

# Module-level cache to avoid repeated import attempts
_CACHED_CLASSES: dict[str, type | None] = {}
_FAILED_IMPORTS: dict[str, str] = {}  # name -> error message


class LazyServiceInfo:
    """Result type for service availability check."""

    __slots__ = ("name", "available", "class_path", "error")

    def __init__(
        self,
        name: str,
        available: bool,
        class_path: str,
        error: str | None = None,
    ) -> None:
        self.name = name
        self.available = available
        self.class_path = class_path
        self.error = error

    def __repr__(self) -> str:
        status = "✓" if self.available else "✗"
        extra = f" — {self.error}" if self.error else ""
        return f"<LazyServiceInfo {self.name}: {status}{extra}>"


def _lazy_import_class(
    module_path: str,
    class_name: str,
    *,
    install_hint: str = "",
) -> type:
    """
    Generic lazy class importer with caching and clear error messages.

    Args:
        module_path: Dotted module path (e.g., "hledac.universal.knowledge.duckdb_store")
        class_name: Name of the class to import from the module
        install_hint: Optional pip/uv install command hint

    Returns:
        The imported class (not an instance)

    Raises:
        ImportError: If the module or class cannot be imported
    """
    cache_key = f"{module_path}:{class_name}"

    # Fast path: return cached class
    if cache_key in _CACHED_CLASSES:
        cls = _CACHED_CLASSES[cache_key]
        if cls is None:
            raise ImportError(_FAILED_IMPORTS.get(cache_key, f"Cannot import {class_name}"))
        return cls

    # Attempt lazy import
    try:
        from importlib import import_module

        module: ModuleType = import_module(module_path)
        cls: type = getattr(module, class_name)

        _CACHED_CLASSES[cache_key] = cls
        logger.debug(f"[_lazy_imports] Loaded {class_name} from {module_path}")
        return cls

    except ImportError as exc:
        _CACHED_CLASSES[cache_key] = None
        error_msg = str(exc)
        _FAILED_IMPORTS[cache_key] = error_msg

        hint = ""
        if install_hint:
            hint = f"\n  Install with: {install_hint}"

        raise ImportError(
            f"Failed to lazy-load {class_name} from {module_path}. "
            f"Original error: {error_msg}.{hint}"
        ) from exc


# ─── Service Factory Functions ────────────────────────────────────────────────


def get_DuckDBShadowStore() -> type:
    """
    Factory for DuckDBShadowStore class.

    The canonical DuckDB store for sprint-level facts and shadow findings.
    Handles async initialization via async_init() method.

    RAISES:
        ImportError: If duckdb package or knowledge.duckdb_store unavailable

    EXAMPLE:
        DuckDBShadowStore = get_DuckDBShadowStore()
        store = DuckDBShadowStore()
        await store.async_init()
    """
    return _lazy_import_class(
        "knowledge.duckdb_store",
        "DuckDBShadowStore",
        install_hint="uv add duckdb",
    )


def get_M1ResourceGovernor() -> type:
    """
    Factory for M1ResourceGovernor class.

    Advisory safety layer for M1 8GB RAM management.
    Reads from canonical sources (UMA budget, model lifecycle).

    RAISES:
        ImportError: If runtime.resource_governor unavailable

    EXAMPLE:
        M1ResourceGovernor = get_M1ResourceGovernor()
        governor = M1ResourceGovernor()
    """
    return _lazy_import_class(
        "runtime.resource_governor",
        "M1ResourceGovernor",
    )


def get_Hermes3Engine() -> type:
    """
    Factory for Hermes3Engine class.

    LLM inference engine wrapper (alias for DeepHermes3Engine).
    Provides structured inference with thinking output parsing.

    RAISES:
        ImportError: If brain.hermes3_engine or mlx unavailable

    NOTE:
        Hermes3Engine is an alias for DeepHermes3Engine in brain.deephermes3_engine.
        The hermes3_engine module provides backward-compatible re-exports.

    EXAMPLE:
        Hermes3Engine = get_Hermes3Engine()
        engine = Hermes3Engine()
    """
    return _lazy_import_class(
        "brain.hermes3_engine",
        "Hermes3Engine",
        install_hint="uv add mlx",
    )


def get_EvidenceLog() -> type:
    """
    Factory for EvidenceLog class.

    Append-only evidence ledger for research provenance.
    M1 8GB optimized with ring buffer and async write queue.

    RAISES:
        ImportError: If evidence_log module unavailable

    NOTE:
        EvidenceLog.__init__ signature: (run_id, persist_path=None,
        enable_persist=True, encrypt_at_rest=False, ...)

    EXAMPLE:
        EvidenceLog = get_EvidenceLog()
        elog = EvidenceLog(run_id="sprint-123")
    """
    return _lazy_import_class(
        "evidence_log",
        "EvidenceLog",
    )


def get_SidecarOrchestrator() -> type:
    """
    Factory for SidecarOrchestrator class.

    Thin facade for sprint sidecar execution with result sink,
    governor, and scheduler wiring.

    RAISES:
        ImportError: If runtime.sidecar_orchestrator unavailable

    NOTE:
        SidecarOrchestrator.__init__ signature: (result_sink, governor=None,
        scheduler=None)

    EXAMPLE:
        SidecarOrchestrator = get_SidecarOrchestrator()
        orch = SidecarOrchestrator(
            result_sink=result,
            governor=governor,
            scheduler=scheduler,
        )
    """
    return _lazy_import_class(
        "runtime.sidecar_orchestrator",
        "SidecarOrchestrator",
    )


# ─── Diagnostic Helpers ──────────────────────────────────────────────────────


def get_all_service_status() -> dict[str, LazyServiceInfo]:
    """
    Check availability of all 5 core services WITHOUT triggering full import.

    Useful for diagnostics and startup validation.

    RETURNS:
        Dict mapping service name -> LazyServiceInfo with availability status

    EXAMPLE:
        status = get_all_service_status()
        for name, info in status.items():
            print(f"{name}: {'OK' if info.available else f'FAIL — {info.error}'}")
    """
    services = [
        ("DuckDBShadowStore", "knowledge.duckdb_store", "DuckDBShadowStore"),
        ("M1ResourceGovernor", "runtime.resource_governor", "M1ResourceGovernor"),
        ("Hermes3Engine", "brain.hermes3_engine", "Hermes3Engine"),
        ("EvidenceLog", "evidence_log", "EvidenceLog"),
        ("SidecarOrchestrator", "runtime.sidecar_orchestrator", "SidecarOrchestrator"),
    ]

    from importlib import util

    results: dict[str, LazyServiceInfo] = {}

    for name, module_path, class_name in services:
        cache_key = f"{module_path}:{class_name}"

        # Check cache first
        if cache_key in _CACHED_CLASSES:
            cls = _CACHED_CLASSES[cache_key]
            results[name] = LazyServiceInfo(
                name=name,
                available=cls is not None,
                class_path=f"{module_path}.{class_name}",
                error=_FAILED_IMPORTS.get(cache_key),
            )
            continue

        # Check if module spec exists (fast, no import)
        try:
            spec = util.find_spec(module_path)
        except Exception:
            spec = None
        if spec is None:
            results[name] = LazyServiceInfo(
                name=name,
                available=False,
                class_path=f"{module_path}.{class_name}",
                error=f"Module {module_path!r} not found in sys.path",
            )
            _CACHED_CLASSES[cache_key] = None
            _FAILED_IMPORTS[cache_key] = f"Module {module_path!r} not found"
            continue

        # Try actual import to verify class exists
        try:
            cls = _lazy_import_class(module_path, class_name)
            results[name] = LazyServiceInfo(
                name=name,
                available=True,
                class_path=f"{module_path}.{class_name}",
            )
        except ImportError as exc:
            results[name] = LazyServiceInfo(
                name=name,
                available=False,
                class_path=f"{module_path}.{class_name}",
                error=str(exc),
            )

    return results


# ─── PEP 810 Module-Level __getattr__ ────────────────────────────────────────
# Enables: from hledac.universal._lazy_imports import get_DuckDBShadowStore
# without triggering import until the function is actually called.


def __getattr__(name: str) -> Any:
    """PEP 810: Lazy module attribute access for factory functions."""
    factory_map = {
        "get_DuckDBShadowStore": get_DuckDBShadowStore,
        "get_M1ResourceGovernor": get_M1ResourceGovernor,
        "get_Hermes3Engine": get_Hermes3Engine,
        "get_EvidenceLog": get_EvidenceLog,
        "get_SidecarOrchestrator": get_SidecarOrchestrator,
        "get_all_service_status": get_all_service_status,
        "LazyServiceInfo": LazyServiceInfo,
    }

    if name in factory_map:
        return factory_map[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
