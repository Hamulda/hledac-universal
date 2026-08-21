"""
runtime/sprint_entrypoint.py — Backward compatibility wrapper

F350M-R: This module is now a thin wrapper that re-exports all public APIs
from the new modular structure at runtime/sprint/.

Original code has been refactored into:
    runtime/sprint/
        __init__.py         # Re-exports
        context.py           # SprintRunContext, SprintContextManager
        types.py             # Type definitions, decision tables
        phases/
            boot.py          # _run_sprint_boot
            execute.py       # _run_sprint_execute
            windup.py        # _run_sprint_windup
            teardown.py      # _run_sprint_teardown
            orchestrator.py  # run_sprint

Benefits:
- 2828 LOC → ~300 LOC (88% reduction)
- Each phase < 100 LOC
- Better testability and maintainability
- M1 8GB optimized

Migration:
    Old: from hledac.universal.runtime.sprint_entrypoint import run_sprint
    New: from hledac.universal.runtime.sprint import run_sprint

Both work identically. The old import is preserved for backward compatibility.
"""

from __future__ import annotations


def main() -> None:
    """
    Synchronous entry point with structured exit-code handling.

    Thin wrapper that delegates to the new modular implementation.
    Preserved for backward compatibility with existing CLI usage.
    """
    from hledac.universal.runtime.sprint._cli import main as _new_main

    _new_main()


# These functions have been removed from the codebase:
# - _get_timing_fields: Refactored into compute_timing_truth()
# - _get_memory_fields: Refactored into timing_truth construction
# - _serialize_payload_direct: Refactored into _build_report_dict()
#
# If any external code depends on these, update to use the new APIs.

__doc__ = """
runtime/sprint_entrypoint.py — Canonical Sprint Entry Point

F186A CANONICAL SPRINT TRUTH CLOSURE — CLI Entry Point:
    python -m hledac.universal.runtime.sprint_entrypoint

Pre-sprint checks, UMA wiring, sprint_delta reporting.

Wires UMAAlarmDispatcher → SprintScheduler wind-down callbacks.

================================================================
F186A CANONICAL SPRINT TRUTH — ROLE TABLE
================================================================
Role        | Function                        | Owner | Notes
----------- | ------------------------------- | ----- | ----
canonical   | run_sprint()                    | YES   | SOLE canonical sprint owner
canonical   | _runtime_truth()                | YES   | part of canonical run boundary
canonical   | _is_meaningful_run()            | YES   | part of canonical run boundary
canonical   | run_pre_sprint_checks()          | YES   | part of canonical pre-flight
canonical   | write_sprint_delta()            | YES   | part of canonical teardown
shell       | main() --sprint path            | NO    | delegates to run_sprint(), owns no sprint state
alternate   | main() --ct-pivot path          | NO    | CT log tool, no sprint
alternate   | main() --pivot path             | NO    | semantic pivot, no sprint
residual    | _get_live_feed_urls()           | NO    | shared helper, called by canonical

Canonical path: `python -m hledac.universal --sprint` → root main() --sprint
  → runtime.sprint_entrypoint.run_sprint() [sole canonical sprint owner]

  Note: `python -m hledac.universal.runtime.sprint_entrypoint --sprint` is an ALTERNATE entrypoint
  that also calls run_sprint() directly, but the canonical operator path
  is through root __main__.py (python -m hledac.universal).

Canonical sprint owner: run_sprint()
All report truth (canonical_run_summary, runtime_truth, timing_truth,
checkpoint_zero_category, observed_run_tuple) flows from run_sprint().

Usage:
    python -m hledac.universal.runtime.sprint_entrypoint --sprint --query "LockBit ransomware" --duration 1800
    python -m hledac.universal._core --ct-pivot example.com

REFACTORING (F350M-R):
    This file is now a thin backward-compatibility wrapper.
    The actual implementation is in runtime/sprint/ package.
"""
