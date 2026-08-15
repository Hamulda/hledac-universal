"""
Acquisition Lanes — Modular Architecture (Issue #5).

This package contains the canonical acquisition strategy layer split from
acquisition_strategy.py (5713 lines) into focused submodules.

Modules:
    _core      — AcquisitionLanePlan, AcquisitionContext, RiskLevel, LaneSpec, LaneRule
    _nonfeed   — NonfeedMissionController, NonfeedSeedContext, NonfeedMissionSnapshot
    _planning  — build_acquisition_plan, _build_plan_impl

All imports from acquisition_strategy.py are re-exported here for backward compatibility.
"""





    AcquisitionLane,
    AcquisitionLanePlan,
    AcquisitionLaneOutcome,
    AcquisitionProfile,
    AcquisitionContext,
    AcquisitionStrategySnapshot,
    FeedDominanceBudget,
    LaneSpec,
    LaneRule,
    MandatoryLaneTerminality,
    NonfeedPlanDebug,
    NonfeedSeedContext,
    NonfeedMissionSnapshot,
    NonfeedMissionExitReason,
    NonfeedMissionController,
    MissionIntent,
    MissionTargetKind,
    RiskLevel,
    SourceFamilyOutcome,
    build_acquisition_plan,
    build_acquisition_report,
    get_lane_plan,
    is_lane_enabled,
    lane_skip_reason,
    normalize_acquisition_profile,
    normalize_source_family_name,
    normalize_source_family_outcome,
    normalize_terminal_state,
    terminality_report,
)

from _core import aclose