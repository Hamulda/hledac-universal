"""
F206AH RUNTIME A/B AUTHORITY SEAL
=================================

Static authority manifest — machine-checkable runtime boundary.

This module defines the canonical ownership boundary between Runtime A
(active/production) and Runtime B (legacy/deprecated). It is the
single source of truth for which code paths may produce canonical sprint
truth and which may not.

Usage:
    from runtime_authority_manifest import (
        CANONICAL_SPRINT_OWNER,
        ACTIVE_RUNTIME_FILES,
        LEGACY_RUNTIME_FILES,
        DEPRECATED_FACADE_FILES,
        verify_authority,
    )

NO live imports — this module must be safe to import in any context
(including smoke_runner, probe tests, CI) without triggering model loads,
network, or heavy dependencies.

================================================================
RUNTIME A — ACTIVE (canonical sprint truth owner)
================================================================
"""
from __future__ import annotations

# -------------------------------------------------------------------
# Sole canonical sprint owner — all report truth, timing truth,
# export truth, and SprintSchedulerResult flow from here.
# -------------------------------------------------------------------
CANONICAL_SPRINT_OWNER: str = "hledac.universal.core.__main__.run_sprint"

# -------------------------------------------------------------------
# ACTIVE RUNTIME A files — may contribute to canonical sprint truth
# via the canonical owner (core.__main__.run_sprint).
# These are workers/executors, NOT owners. They may NOT emit
# canonical_sprint_owner or claim canonical sprint truth.
# -------------------------------------------------------------------
ACTIVE_RUNTIME_FILES: list[str] = [
    "runtime/sprint_scheduler.py",          # runtime executor — receives work from canonical owner
    "runtime/sprint_lifecycle.py",          # lifecycle state machine
    "runtime/sprint_lifecycle_runner.py",   # lifecycle orchestration
    "runtime/sprint_advisory_runner.py",    # advisory sidecar runner
    "runtime/sidecar_dispatcher.py",        # sidecar batch dispatcher
    "pipeline/live_public_pipeline.py",     # public discovery feed pipeline
    "pipeline/live_feed_pipeline.py",       # RSS/Atom feed pipeline
    "knowledge/duckdb_store.py",            # canonical findings store (canonical write seam)
    "knowledge/graph_service.py",            # graph accumulation (canonical write seam)
]

# -------------------------------------------------------------------
# LEGACY RUNTIME B files — must NEVER produce canonical sprint truth.
# These paths may be used for backward compatibility or smoke testing
# but must be labeled as "smoke/legacy" or "diagnostic" in any
# runner that touches them.
# -------------------------------------------------------------------
LEGACY_RUNTIME_FILES: list[str] = [
    "legacy/autonomous_orchestrator.py",   # 31k-line God Object — deprecated
]

# -------------------------------------------------------------------
# DEPRECATED FACADE files — re-export facades that look like orchestrators
# but delegate to Runtime B. These are NOT canonical sprint owners.
# Any runner consuming these must label output as "facade/legacy".
# -------------------------------------------------------------------
DEPRECATED_FACADE_FILES: list[str] = [
    "autonomous_orchestrator.py",           # root re-export facade → legacy/
    "orchestrator/__init__.py",             # FullyAutonomousOrchestrator re-export
]

# -------------------------------------------------------------------
# SMOKE RUNNER policy — smoke_runner may call Legacy Runtime B but
# MUST label the run as "smoke/legacy" and must NEVER emit
# canonical_sprint_owner. The smoke_runner docstring already states
# this but the runtime must enforce it.
# -------------------------------------------------------------------
SMOKE_RUNNER_LEGACY_LABEL: str = "smoke/legacy"

# -------------------------------------------------------------------
# Role labels for ENTRYPOINT_ROLE classification
# -------------------------------------------------------------------
ROLE_LABELS: dict[str, str] = {
    "canonical": "sole production sprint owner — all truth flows from here",
    "runtime_worker": "runtime executor — receives work from canonical owner",
    "shell": "CLI dispatcher — never owns sprint state",
    "alternate": "legacy production path — not canonical",
    "facade": "re-export facade — delegates to legacy runtime",
    "diagnostic": "probe/benchmark only — not production",
}


def verify_authority() -> dict[str, bool]:
    """
    Static authority check — verifies this module's own structure.

    Returns dict with keys:
        canonical_owner_defined: bool
        active_runtime_nonempty: bool
        legacy_runtime_nonempty: bool
        facade_nonempty: bool
        no_overlap: bool  (active ∩ legacy == ∅)
    """
    return {
        "canonical_owner_defined": bool(CANONICAL_SPRINT_OWNER),
        "active_runtime_nonempty": bool(ACTIVE_RUNTIME_FILES),
        "legacy_runtime_nonempty": bool(LEGACY_RUNTIME_FILES),
        "facade_nonempty": bool(DEPRECATED_FACADE_FILES),
        "no_overlap": _check_no_overlap(),
    }


def _check_no_overlap() -> bool:
    """Active and legacy runtime file sets must not overlap."""
    active_set = set(ACTIVE_RUNTIME_FILES)
    legacy_set = set(LEGACY_RUNTIME_FILES)
    facade_set = set(DEPRECATED_FACADE_FILES)
    return len(active_set & legacy_set) == 0 and len(active_set & facade_set) == 0


def get_runtime_label(path: str) -> str:
    """
    Return the runtime label for a file path.

    Returns one of: "active_runtime", "legacy_runtime", "deprecated_facade", "unknown"
    """
    if path in ACTIVE_RUNTIME_FILES:
        return "active_runtime"
    if path in LEGACY_RUNTIME_FILES:
        return "legacy_runtime"
    if path in DEPRECATED_FACADE_FILES:
        return "deprecated_facade"
    return "unknown"
