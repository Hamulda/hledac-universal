"""Thin re-export stub for backward compatibility.

F350M-R: The canonical v1 module was moved to:
    archive/scheduler_archives/sprint_scheduler_v1_archived.py

This stub re-exports all public symbols from the archived module.
The stub itself is lazy-loading — the archived module is only imported
when at least one symbol is accessed from this module.

This maintains backward compatibility for:
- runtime/__init__.py lazy imports
- runtime/sprint_scheduler.py __getattr__ re-exports (SourceWork, SprintRunContext, HealthReport, ...)
- runtime/context/__init__.py re-exports

No active production code should import from this module. PivotTask has been
extracted to runtime/pivot_types.py.
Canonical paths (use these):
    from runtime.scheduler_v2 import SprintSchedulerV2
    from runtime.sprint_scheduler import SprintScheduler  # → SprintSchedulerV2
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from importlib import import_module
from typing import TYPE_CHECKING

# Absolute path to the archived module
_ARCHIVE_DIR = os.path.join(
    os.path.dirname(__file__),  # runtime/
    "..",
    "archive",
    "scheduler_archives",
)
_ARCHIVE_MODULE_PATH = os.path.join(_ARCHIVE_DIR, "sprint_scheduler_v1_archived.py")

# Cached reference to the loaded archive module
_archived_module: object | None = None


def __getattr__(name: str):
    """PEP 562: lazily import from the archived module on first access."""
    global _archived_module

    if _archived_module is None:
        # Use importlib to load from file path (archive/ is not a package)
        spec = importlib.util.spec_from_file_location(
            "sprint_scheduler_v1_archived", _ARCHIVE_MODULE_PATH
        )
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Cannot load archived module from {_ARCHIVE_MODULE_PATH}"
            )
        _archived_module = importlib.util.module_from_spec(spec)
        # Add to sys.modules so nested imports resolve correctly
        sys.modules["sprint_scheduler_v1_archived"] = _archived_module
        spec.loader.exec_module(_archived_module)

    val = getattr(_archived_module, name)
    # Cache in this module so subsequent accesses are O(1)
    globals()[name] = val
    return val


def __dir__() -> list[str]:
    """PEP 562: delegate to archived module's dir()."""
    if _archived_module is None:
        # Trigger lazy load to get the module
        __getattr__("__dir__")
    if _archived_module is not None:
        return dir(_archived_module)
    return []
