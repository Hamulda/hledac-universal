"""
MARLCoordinator Shim
====================

Intentional D in Sprint F196A — zero canonical call-sites, stub experiment.

RL functionality is covered by rl/sprint_policy_manager.py (reward contract,
every-5th-sprint exploration, policy persistence).

This shim exists only to provide a graceful ImportError for legacy callers
(e.g. old tests), rather than a cryptic "module not found".

NO PRODUCTION CALLERS — DO NOT USE IN NEW CODE.
"""

from __future__ import annotations

raise ImportError(
    "hledac.universal.rl.marl_coordinator was removed in Sprint F196A: "
    "zero canonical call-sites, stub experiment. "
    "Use rl.sprint_policy_manager.SprintPolicyManager for RL state/exploration."
)
