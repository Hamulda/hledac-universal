"""Centralized lazy import accessors — single source of truth for canonical import paths.

PEP 810 lazy imports via module-level __getattr__ provide runtime lazy loading.
This module provides explicit accessor functions for IDE autocomplete + refactoring safety.

F350M-R / Issue #25 — canonical import paths for scheduler_v2 lazy imports.

Usage:
    from hledac.universal._lazy_imports import (
        get_DuckDBShadowStore,
        get_M1ResourceGovernor,
        get_Hermes3Engine,
        get_EvidenceLog,
        get_SidecarOrchestrator,
        get_async_helpers,
        get_acquisition_strategy,
        get_SprintSchedulerConfig,
        get_SprintSchedulerResult,
        get_SprintLifecycleManager,
    )

    # At use site:
    DuckDBShadowStore = get_DuckDBShadowStore()
    store = DuckDBShadowStore()

Benefits:
    - Single source of truth for import paths (1 file to change for refactoring)
    - IDE autocomplete support (paths.py is a well-known module)
    - Centralized fail-safe handling (single try/except point)
    - M1 Metal lazy init preserved (imports happen at runtime, not at module load)
"""


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Canonical package paths
# ---------------------------------------------------------------------------
_KNOWLEDGE = "hledac.universal.knowledge"
_BRAIN = "hledac.universal.brain"
_CORE = "hledac.universal.core"
_UTILS = "hledac.universal.utils"
_RUNTIME = "hledac.universal.runtime"


# ---------------------------------------------------------------------------
# Knowledge layer
# ---------------------------------------------------------------------------

def get_DuckDBShadowStore() -> type:
    """Return DuckDBShadowStore class (lazy import).

    Canonical path: hledac.universal.knowledge.duckdb_store.DuckDBShadowStore
    """
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    return DuckDBShadowStore


# ---------------------------------------------------------------------------
# Brain layer
# ---------------------------------------------------------------------------

def get_Hermes3Engine() -> type:
    """Return Hermes3Engine class (lazy import).

    Canonical path: hledac.universal.brain.hermes_engine.Hermes3Engine
    """
    from hledac.universal.brain.hermes_engine import Hermes3Engine

    return Hermes3Engine


# ---------------------------------------------------------------------------
# Core layer
# ---------------------------------------------------------------------------

def get_M1ResourceGovernor() -> type:
    """Return M1ResourceGovernor class (lazy import).

    Canonical path: hledac.universal.core.resource_governor.M1ResourceGovernor
    """
    from hledac.universal.core.resource_governor import M1ResourceGovernor

    return M1ResourceGovernor


# ---------------------------------------------------------------------------
# Utils layer
# ---------------------------------------------------------------------------

def get_EvidenceLog() -> type:
    """Return EvidenceLog class (lazy import).

    Canonical path: hledac.universal.utils.evidence_log.EvidenceLog
    """
    from hledac.universal.utils.evidence_log import EvidenceLog

    return EvidenceLog


def get_async_helpers() -> tuple[type, type]:
    """Return (safe_create_task, safe_gather_return_exceptions) tuple (lazy import).

    Canonical path: hledac.universal.utils.async_helpers
    """
    from hledac.universal.utils.async_helpers import (
        safe_create_task,
        safe_gather_return_exceptions,
    )

    return safe_create_task, safe_gather_return_exceptions


# ---------------------------------------------------------------------------
# Runtime layer
# ---------------------------------------------------------------------------

def get_SidecarOrchestrator() -> type:
    """Return SidecarOrchestrator class (lazy import).

    Canonical path: hledac.universal.runtime.sidecar_orchestrator.SidecarOrchestrator
    """
    from hledac.universal.runtime.sidecar_orchestrator import SidecarOrchestrator

    return SidecarOrchestrator


def get_acquisition_strategy() -> tuple[type, type]:
    """Return (build_acquisition_plan, SprintLifecycleManager) tuple (lazy import).

    Note: build_acquisition_plan is in acquisition_strategy module.
          SprintLifecycleManager is in scheduler_lifecycle_manager module.
    """
    from hledac.universal.runtime.acquisition_strategy import build_acquisition_plan
    from hledac.universal.runtime.scheduler_lifecycle_manager import SprintLifecycleManager

    return build_acquisition_plan, SprintLifecycleManager


# ---------------------------------------------------------------------------
# Scheduler internals (used in __init__ and _initialize_sprint_run)
# ---------------------------------------------------------------------------

def get_SprintSchedulerConfig() -> type:
    """Return SprintSchedulerConfig class (lazy import).

    Canonical path: runtime.scheduler_config.SprintSchedulerConfig
    """
    from runtime.scheduler_config import SprintSchedulerConfig

    return SprintSchedulerConfig


def get_SprintSchedulerResult() -> type:
    """Return SprintSchedulerResult class (lazy import).

    Canonical path: runtime.scheduler_result.SprintSchedulerResult
    """
    from runtime.scheduler_result import SprintSchedulerResult

    return SprintSchedulerResult


def get_SprintLifecycleManager() -> type:
    """Return SprintLifecycleManager class (lazy import).

    Canonical path: runtime.scheduler_lifecycle_manager.SprintLifecycleManager
    """
    from runtime.scheduler_lifecycle_manager import SprintLifecycleManager

    return SprintLifecycleManager
