"""Runtime package — lazy-loading re-exports via PEP 562 __getattr__.

Cold-start saving: simple scripts that `from runtime import SprintScheduler`
pay zero cost until the first attribute access. Only the 33k-line
sprint_scheduler loads when actually used.

Pattern (PEP 562):
    from runtime import SprintScheduler  # instant, no module load
    scheduler = SprintScheduler(...)     # triggers import here

Invariant: TYPE_CHECKING imports in callers are unaffected — static
type checkers resolve names at analysis time, not runtime.
"""
from __future__ import annotations

import typing


# Re-exported symbols — add new entries here as the API grows.
# Each entry is (module_path, import_name).
_LAZY_IMPORTS: typing.Final[dict[str, tuple[str, str]]] = {
    "SprintScheduler": ("runtime.sprint_scheduler", "SprintScheduler"),
    "SprintSchedulerConfig": ("runtime.sprint_scheduler", "SprintSchedulerConfig"),
    "SprintSchedulerResult": ("runtime.sprint_scheduler", "SprintSchedulerResult"),
    # Issue 10.2: Observability exports
    "setup_instrumentation": ("runtime.instrumentation_setup", "setup_instrumentation"),
    "instrument_duckdb_connection": ("runtime.instrumentation_setup", "instrument_duckdb_connection"),
    "instrument_lmdb_env": ("runtime.instrumentation_setup", "instrument_lmdb_env"),
    "configure_logfire": ("runtime.logfire_setup", "configure_logfire"),
    "get_logfire_logger": ("runtime.logfire_setup", "get_logfire_logger"),
    "AsyncLogHandler": ("runtime.observability_async_handler", "AsyncLogHandler"),
    "configure_async_logging": ("runtime.observability_async_handler", "configure_async_logging"),
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




