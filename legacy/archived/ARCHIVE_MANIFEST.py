"""
Archive Manifest — Sprint F205D Dead Code Archive Pass
========================================================

.. Verdict Summary (2026-04-27)

This file records verdicts for candidate legacy surfaces audited in sprint F205D.

================================================================================
VERDICT TABLE
================================================================================

Module                        Size      Canonical Imports?  Verdict
--------------------------------------------------        ----------------------------
behavior_simulator.py         1.8 KiB   NONE               ARCHIVED → legacy/archived/
enhanced_research.py          114.3 KiB YES (tool_registry,  DORMANT/LEGACY — keep, do not delete
                                         legacy/autonomous_orch
                                         _orchestrator comments,
                                         privacy_enhanced_research.py active)
orchestrator/                 65.9 KiB  YES (smoke_runner,   SECONDARY FACADE — keep, document
                                         tests, legacy path)         as non-canonical in REAL_ARCH
federated/                    40.6 KiB  YES (legacy/autonomous  SECONDARY FACADE — keep, document
                                         _orchestrator lazy imports,    as non-canonical in REAL_ARCH
                                         prefetch_oracle.py, tests)

================================================================================
RATIONALE
================================================================================

behavior_simulator.py
  - ZERO canonical call-sites in core/__main__.py, runtime/sprint_scheduler.py,
    pipeline/live_*.py, knowledge/duckdb_store.py
  - Was a ghost feature placeholder re-exporting from layers.stealth_layer
  - Moving to legacy/archived/ to prevent any future accidental import

enhanced_research.py
  - NOT safe to delete — has runtime relationships via:
    * tool_registry.py references DEEP_RESEARCH_ADMISSION (provider-side truth)
    * legacy/autonomous_orchestrator.py comments reference UnifiedResearchEngine
    * project_types.py references DeepResearchRequest/DeepResearchGroundingShim
    * Coordinators has privacy_enhanced_research.py (active, wired)
  - Marked DORMANT/LEGACY in REAL_ARCHITECTURE.md

orchestrator/
  - NOT safe to delete — smoke_runner.py imports FullyAutonomousOrchestrator
  - Also referenced by tests (test_sprint8y, test_sprint61, etc.)
  - Marked SECONDARY FACADE in REAL_ARCHITECTURE.md

federated/
  - NOT safe to delete — active imports from:
    * legacy/autonomous_orchestrator.py (lazy: FederatedEngine, FederatedConfig)
    * prefetch_oracle.py (CountMinSketch, SimHashSketch)
    * tests/test_sprint58b.py, test_sprint61.py, test_sprint65_*.py
  - Marked SECONDARY FACADE in REAL_ARCHITECTURE.md

================================================================================
GHOST_INVARIANTS (Sprint F205D)
================================================================================

- NO canonical write path changes
- NO model loads
- NO blocking calls
- fail-soft import shims preserved as-is
- absolute paths via paths.py only
- GHOST_INVARIANTS not modified

================================================================================
"""

# This module serves as archive manifest only — no runtime behavior.
# Archived modules are moved to legacy/archived/ to prevent accidental import.

ARCHIVED_MODULES = {
    "behavior_simulator": {
        "original_path": "behavior_simulator.py",
        "archive_path": "legacy/archived/behavior_simulator.py",
        "verdict": "ARCHIVED",
        "reason": "Zero canonical call-sites; ghost feature placeholder superseded by layers.stealth_layer",
        "archived_date": "2026-04-27",
        "sprint": "F205D",
    },
}

DORMANT_MODULES = {
    "enhanced_research": {
        "path": "enhanced_research.py",
        "verdict": "DORMANT/LEGACY",
        "reason": "Has runtime relationships via tool_registry, legacy/autonomous_orchestrator comments, project_types; privacy_enhanced_research active in coordinators/",
        "do_not_delete": True,
    },
    "orchestrator": {
        "path": "orchestrator/",
        "verdict": "SECONDARY FACADE",
        "reason": "Referenced by smoke_runner.py and tests; NOT canonical sprint path",
        "do_not_delete": True,
    },
    "federated": {
        "path": "federated/",
        "verdict": "SECONDARY FACADE",
        "reason": "Referenced by legacy/autonomous_orchestrator.py, prefetch_oracle.py, and tests",
        "do_not_delete": True,
    },
}
