"""
runtime/acquisition/ — Canonical Acquisition Strategy Layer (refactored from acquisition_strategy.py)

SPLIT STRUCTURE (Issue #18):
  threat_dictionary.py  — _THREAT_DICTIONARY + lookup_threat_entity()
  domain_expansion.py  — DOMAIN_EXPANSIONS + _expand_keyword_query + _get_keyword_domain_expansion()
  profile.py           — AcquisitionProfile, normalize_acquisition_profile(), is_academic_profile(), is_mission_profile()
  cid_detection.py     — _CIDV0_RE, _CIDV1_BASE32_RE, _has_explicit_ipfs_cid(), _extract_cids_from_text()
  budget.py            — FeedDominanceBudget, cap_feeding(), feed_budget_to_dict()
  mission.py           — NonfeedMissionController, MissionIntent, MissionTargetKind, infer_mission_intent()
  lane_constants.py    — AcquisitionLane, RiskLevel, TERMINAL_STATES, NON_TERMINAL_STATES
  lane_plan.py         — AcquisitionLanePlan, AcquisitionContext, LaneSpec, LaneRule
  nonfeed_eligibility.py — _build_nonfeed_lane_eligibility, required_terminal_lanes, lane_is_terminal
  nonfeed_outcomes.py — AcquisitionLaneOutcome, SourceFamilyOutcome, NonfeedPlanDebug,
                         NonfeedSeedContext (NonfeedMissionSnapshot is in mission.py)
  plan_builder.py      — build_acquisition_plan(), _build_plan_impl(), lane concurrency helpers
  report_builder.py    — build_acquisition_report(), terminality_report()
  acquisition_lanes.py — run_enabled_acquisition_lanes(), run_enabled_acquisition_lanes_streaming()
                         (shim delegates to acquisition_strategy.py during transition)

BACKWARD COMPATIBILITY:
  acquisition_strategy.py is preserved as-is and re-exports everything from this package.
  All existing importers (runtime/sprint_scheduler.py, runtime/scheduler/lanes/) continue working
  without changes during the transition period.
"""

# ── Threat dictionary ──────────────────────────────────────────────────────
# ── Acquisition lanes (shim during transition) ───────────────────────────────
from hledac.universal.runtime.acquisition.acquisition_lanes import (
    run_enabled_acquisition_lanes,
    run_enabled_acquisition_lanes_streaming,
)

# ── Budget ─────────────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.budget import (
    FeedDominanceBudget,
    _load_feed_budget_from_env,
    feed_budget_to_dict,
)

# ── CID detection ──────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.cid_detection import (
    _CIDV0_RE,
    _CIDV1_BASE32_RE,
    _extract_cids_from_text,
    _has_explicit_ipfs_cid,
)

# ── Domain expansion ────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.domain_expansion import (
    DOMAIN_EXPANSIONS,
    _expand_keyword_query,
)

# ── Lane constants ─────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.lane_constants import (
    NON_TERMINAL_STATES,
    TERMINAL_STATES,
    AcquisitionLane,
    RiskLevel,
)

# ── Lane plan ──────────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.lane_plan import (
    AcquisitionContext,
    LaneRule,
    LaneSpec,
)

# ── Mission ────────────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.mission import (
    MissionIntent,
    MissionTargetKind,
    NonfeedMissionController,
    NonfeedMissionSnapshot,
    infer_mission_intent,
)

# ── Nonfeed eligibility ─────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.nonfeed_eligibility import (
    lane_is_terminal,
    required_terminal_lanes,
    terminality_report,
)

# AcquisitionLanePlan is in nonfeed_outcomes.py
# ── Nonfeed outcomes ────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.nonfeed_outcomes import (
    AcquisitionLaneOutcome,
    AcquisitionLanePlan,
    MandatoryLaneTerminality,
    NonfeedPlanDebug,
    NonfeedSeedContext,
    SourceFamilyOutcome,
)

# ── Plan builder ────────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.plan_builder import (
    ACQUISITION_REPORT_SCHEMA_VERSION,
    build_acquisition_plan,
    build_lane_query,
    canonicalize_source_family_outcomes,
    get_lane_plan,
    is_lane_enabled,
    lane_skip_reason,
    normalize_source_family_name,
    normalize_source_family_outcome,
    normalize_terminal_state,
)

# ── Profile ─────────────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.profile import (
    AcquisitionProfile,
    is_academic_profile,
    is_deep_osint_m1_profile,
    is_mission_profile,
    normalize_acquisition_profile,
)

# ── Report builder ──────────────────────────────────────────────────────────
from hledac.universal.runtime.acquisition.report_builder import (
    build_acquisition_report,
    complete_source_family_outcomes_from_lane_details,
    reconcile_lane_detail_fields,
)
from hledac.universal.runtime.acquisition.threat_dictionary import (
    lookup_threat_entity,
)

__all__ = [
    # threat_dictionary
    "lookup_threat_entity",
    # domain_expansion
    "DOMAIN_EXPANSIONS",
    "_expand_keyword_query",
    # profile
    "AcquisitionProfile",
    "normalize_acquisition_profile",
    "is_academic_profile",
    "is_deep_osint_m1_profile",
    "is_mission_profile",
    # cid_detection
    "_has_explicit_ipfs_cid",
    "_extract_cids_from_text",
    "_CIDV0_RE",
    "_CIDV1_BASE32_RE",
    # budget
    "FeedDominanceBudget",
    "_load_feed_budget_from_env",
    "feed_budget_to_dict",
    # mission
    "NonfeedMissionController",
    "NonfeedMissionSnapshot",
    "MissionIntent",
    "MissionTargetKind",
    "infer_mission_intent",
    # lane_constants
    "AcquisitionLane",
    "RiskLevel",
    "TERMINAL_STATES",
    "NON_TERMINAL_STATES",
    # lane_plan
    "AcquisitionLanePlan",
    "AcquisitionContext",
    "LaneSpec",
    "LaneRule",
    # nonfeed_eligibility
    "required_terminal_lanes",
    "lane_is_terminal",
    "terminality_report",
    # nonfeed_outcomes
    "AcquisitionLaneOutcome",
    "SourceFamilyOutcome",
    "NonfeedPlanDebug",
    "NonfeedSeedContext",
    "MandatoryLaneTerminality",
    # plan_builder
    "build_acquisition_plan",
    "build_lane_query",
    "is_lane_enabled",
    "get_lane_plan",
    "lane_skip_reason",
    "normalize_source_family_outcome",
    "normalize_source_family_name",
    "canonicalize_source_family_outcomes",
    "normalize_terminal_state",
    "ACQUISITION_REPORT_SCHEMA_VERSION",
    # report_builder
    "build_acquisition_report",
    "reconcile_lane_detail_fields",
    "complete_source_family_outcomes_from_lane_details",
    # acquisition_lanes
    "run_enabled_acquisition_lanes",
    "run_enabled_acquisition_lanes_streaming",
]
