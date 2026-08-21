"""
runtime/sprint/__init__.py — Sprint module re-exports

F350M-R: Modular sprint entrypoint with phase separation.

Original: runtime/sprint_entrypoint.py (2828 LOC, monolithic)
New structure:
    runtime/sprint/
        __init__.py         # Re-exports for backward compatibility
        context.py           # SprintRunContext + SprintContextManager
        types.py             # Type definitions, dataclass bundles, decision tables
        phases/
            boot.py          # _run_sprint_boot (BOOT phase)
            execute.py       # _run_sprint_execute (EXECUTE phase)
            windup.py        # _run_sprint_windup (WINDUP phase)
            teardown.py      # _run_sprint_teardown (TEARDOWN phase)
            orchestrator.py  # run_sprint (main orchestrator)
        cleanup.py           # _fail_safe context managers
        delta_writer.py      # sprint_delta serialization
        truth_logger.py       # runtime_truth, timing_truth computation

Benefits:
- Reduced cognitive load: Each phase is < 100 LOC
- Better testability: Phases can be tested in isolation
- Improved maintainability: Changes are localized
- M1 8GB optimized: slots=True dataclasses, minimal GC pressure

Usage:
    from hledac.universal.runtime.sprint import run_sprint
    await run_sprint(query="LockBit ransomware", duration_s=1800)

Backward compatibility:
    from hledac.universal.runtime.sprint_entrypoint import run_sprint
    # Still works via re-exports
"""

from __future__ import annotations

from .cleanup import (
    _cleanup_stale_locks,
    _fail_safe,
    _fail_safe_async,
)

# Context and types
from .context import (
    SprintContextManager,
    get_current_sprint_context,
    get_sprint_seed_state,
    set_current_sprint_context,
    set_sprint_seed_state,
)

# Delta writer
from .delta_writer import write_sprint_delta

# Phase functions
from .phases.boot import _run_sprint_boot
from .phases.execute import _run_sprint_execute

# Main entry point
from .phases.orchestrator import run_sprint
from .phases.teardown import _run_sprint_teardown
from .phases.windup import _run_sprint_windup

# Truth logging
from .truth_logger import (
    _runtime_truth,
    build_observed_run_tuple,
    compute_timing_truth,
)
from .types import (
    CheckpointInput,
    ExportHandoffInput,
    ReportBuildInput,
    RuntimeTruthInput,
    SprintFlags,
    SprintRunContext,
    VerdictHintInput,
)

__all__ = [
    # Main entry point
    "run_sprint",
    # Phase functions
    "_run_sprint_boot",
    "_run_sprint_execute",
    "_run_sprint_windup",
    "_run_sprint_teardown",
    # Context
    "SprintRunContext",
    "SprintContextManager",
    "get_current_sprint_context",
    "set_current_sprint_context",
    "get_sprint_seed_state",
    "set_sprint_seed_state",
    # Types
    "SprintFlags",
    "VerdictHintInput",
    "CheckpointInput",
    "RuntimeTruthInput",
    "ReportBuildInput",
    "ExportHandoffInput",
    "_fail_safe",
    "_fail_safe_async",
    "_cleanup_stale_locks",
    # Truth logging
    "_runtime_truth",
    "compute_timing_truth",
    "build_observed_run_tuple",
    # Delta writer
    "write_sprint_delta",
]
