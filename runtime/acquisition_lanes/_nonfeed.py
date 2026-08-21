"""
Acquisition Lanes — Nonfeed Mission (_nonfeed.py).

Nonfeed mission controller: NonfeedMissionController, NonfeedSeedContext,
NonfeedMissionSnapshot, and related types.

This module is extracted from acquisition_strategy.py (5713 lines) as part of
Issue #5 god-object split. The canonical source remains acquisition_strategy.py
until full migration is complete.
"""

# Re-export from acquisition_strategy.py for now (until full extraction)
from hledac.universal.runtime.acquisition_strategy import (
    MissionIntent,
    MissionTargetKind,
    NonfeedMissionController,
    NonfeedMissionExitReason,
    NonfeedMissionSnapshot,
    NonfeedPlanDebug,
    NonfeedSeedContext,
)

__all__ = [
    "NonfeedPlanDebug",
    "NonfeedSeedContext",
    "NonfeedMissionSnapshot",
    "NonfeedMissionExitReason",
    "NonfeedMissionController",
    "MissionIntent",
    "MissionTargetKind",
]
