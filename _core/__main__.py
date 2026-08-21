"""
core.__main__ — Canonical sprint entry point (F350M-R legacy re-export).

ROLE: CANONICAL SPRINT OWNER (per sprint_entrypoint.py:2056).

This module exists for backward compatibility and test infrastructure that
patches `hledac.universal._core.__main__.run_sprint`. The actual implementation
lives in `runtime.sprint_entrypoint.run_sprint`; this module re-exports it.

Canonical path referenced by:
- tests/test_r0_nonfeed_reality_lock.py
- runtime/sprint_entrypoint.py (canonical_sprint_owner documentation)
- Various probe/audit reports
"""

from __future__ import annotations

# F350M-R: Lazy imports to break core ↔ runtime cycle

# Lazy runtime access
_runtime_run_sprint_impl = None
_SprintFlags_impl = None


def _get_runtime_run_sprint():
    """Lazy getter for run_sprint from runtime.sprint_entrypoint."""
    global _runtime_run_sprint_impl
    if _runtime_run_sprint_impl is None:
        from hledac.universal.runtime.sprint_entrypoint import run_sprint as _impl

        _runtime_run_sprint_impl = _impl
    return _runtime_run_sprint_impl


def _get_SprintFlags():
    """Lazy getter for SprintFlags from runtime.sprint_entrypoint."""
    global _SprintFlags_impl
    if _SprintFlags_impl is None:
        from hledac.universal.runtime.sprint_entrypoint import SprintFlags as _impl

        _SprintFlags_impl = _impl
    return _SprintFlags_impl


# Re-export for backward compatibility (canonical path)
run_sprint = _get_runtime_run_sprint()

__all__ = ["run_sprint", "_get_SprintFlags"]
