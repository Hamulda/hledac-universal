"""Runtime package — lazy-loading re-exports via PEP 562 __getattr__.

STEP 4 F350M-R: SprintScheduler is now SprintSchedulerV2 (greenfield rewrite).
The canonical path is runtime.scheduler_v2.SprintSchedulerV2 (~2k LOC, Protocol-based).
The legacy v1 module (33k LOC) is kept for exhaustiveness of type definitions
until all types are migrated.

Pattern (PEP 562):
    from runtime import SprintScheduler  # instant, no module load
    scheduler = SprintScheduler(...)   # triggers V2 import here

Invariant: TYPE_CHECKING imports in callers are unaffected — static
type checkers resolve names at analysis time, not runtime.
"""
from __future__ import annotations

import typing


# Re-exported symbols — add new entries here as the API grows.
# Each entry is (module_path, import_name).
_LAZY_IMPORTS: typing.Final[dict[str, tuple[str, str]]] = {
    # STEP 4 F350M-R: SprintScheduler → V2 (canonical greenfield)
    "SprintScheduler": ("runtime.scheduler_v2", "SprintSchedulerV2"),
    # V1 types (canonical homes after migration)
    "SprintSchedulerConfig": ("runtime.scheduler_config", "SprintSchedulerConfig"),
    "SprintSchedulerResult": ("runtime.scheduler_result", "SprintSchedulerResult"),
    "IntCounterLayoutProto": ("runtime.scheduler_config", "IntCounterLayoutProto"),
    # V2 types
    "SprintSchedulerV2": ("runtime.scheduler_v2", "SprintSchedulerV2"),
    "SprintContext": ("runtime.scheduler_v2", "SprintContext"),
    "PhaseRunner": ("runtime.scheduler_v2", "PhaseRunner"),
    # Issue 10.2: Observability exports
    "setup_instrumentation": ("runtime._telemetry_setup", "configure"),
    "instrument_duckdb_connection": ("runtime._telemetry_setup", "instrument_duckdb_connection"),
    "instrument_lmdb_env": ("runtime._telemetry_setup", "instrument_lmdb_env"),
    "configure_logfire": ("runtime._telemetry_setup", "configure"),
    "get_logfire_logger": ("runtime._telemetry_setup", "is_configured"),
    "AsyncLogHandler": ("runtime.observability_async_handler", "AsyncLogHandler"),
    "configure_async_logging": ("runtime.observability_async_handler", "configure_async_logging"),
    # Issue #22: Health endpoint
    "collect_runtime_health": ("runtime.health", "collect_runtime_health"),
}


def __getattr__(name: str) -> typing.Any:
    """PEP 562: lazily import re-exported symbols on first access."""
    pair = _LAZY_IMPORTS.get(name)
    if pair is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_path, attr_name = pair
    # Inline import keeps the module out of sys.modules until actually used.
    from importlib import import_module
    mod = import_module(mod_path)
    val = getattr(mod, attr_name)
    # Cache in this module so subsequent accesses are O(1).
    globals()[name] = val
    return val


def __dir__() -> list[str]:
    """PEP 562: make lazy imports visible to dir() and tab-completion."""
    return list(_LAZY_IMPORTS) + list(globals().keys())
