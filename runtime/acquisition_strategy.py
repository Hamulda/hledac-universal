"""
Sprint F350M-R — Acquisition Strategy Layer (Re-export Shim).

This module is a backward-compatibility shim. It re-exports all public symbols
from the split modules:

  - Pure planner: runtime.acquisition_strategy_planner
  - Async runner: runtime.acquisition_strategy_runner

Single responsibility violations that existed in the pre-split module
(dual-role PLANNER + RUNNER in one 2472-line file) are now resolved:
  - Planner: pure functions, no network I/O
  - Runner: async functions, all network I/O

Existing callers importing from this module see zero API change.
New code should import directly from the appropriate submodule.
"""
from __future__ import annotations

import logging

# Re-export all pure planner symbols (PLANNER section)
from hledac.universal.runtime.acquisition_strategy_planner import (
    # Enums
    AcquisitionLane,
    AcquisitionProfile,
    AcquisitionContext,
    # Structs / dataclasses
    AcquisitionLanePlan,
    AcquisitionStrategySnapshot,
    AcquisitionLaneOutcome,
    SourceFamilyOutcome,
    NonfeedPlanDebug,
    MandatoryLaneTerminality,
    FeedDominanceBudget,
    LANE_RULES,
    # Functions (planner)
    build_acquisition_plan,
    _disabled_reason,
    build_acquisition_report,
    build_lane_query,
    is_lane_enabled,
    get_lane_plan,
    lane_skip_reason,
    normalize_source_family_outcome,
    normalize_source_family_name,
    canonicalize_source_family_outcomes,
    normalize_terminal_state,
    required_terminal_lanes,
    lane_is_terminal,
    terminality_report,
    # Utilities
    logger,
    _load_feed_budget_from_env,
    _has_explicit_ipfs_cid,
    _extract_cids_from_text,
    lookup_threat_entity,
    DOMAIN_EXPANSIONS,
    _THREAT_DICTIONARY,
    # Re-exported for convenience
    _expand_keyword_query,
    _get_keyword_domain_expansion,
    _wallet_to_findings,
    _extract_crypto_from_query,
    select_ct_domains_for_passivedns_pivot,
    _extract_domain_from_ct_finding,
    normalize_passive_dns_query,
    _extract_ips_from_query,
    _looks_like_ip,
    _looks_like_domain,
    # Constants
    ACQUISITION_REPORT_SCHEMA_VERSION,
    TERMINAL_STATES,
    NON_TERMINAL_STATES,
    # Legacy / telemetry
    NonfeedSeedContext,
    NonfeedMissionController,
    NonfeedMissionSnapshot,
    MissionIntent,
    MissionTargetKind,
    infer_mission_intent,
    normalize_acquisition_profile,
    is_academic_profile,
    is_deep_osint_m1_profile,
    LaneSpecFeedNFD,
    reconcile_lane_detail_fields,
    complete_source_family_outcomes_from_lane_details,
    # Bridge helpers (used by runner)
    ct_results_to_findings,
    passive_dns_results_to_findings,
    wayback_results_to_findings,
    MAX_SAMPLE_REJECTIONS,
    # Internal helpers (used by lanes/__init__.py)
    _feed_budget_to_dict,
    _has_threat_indicator,
    _has_crypto_indicator,
)

# Re-export async runner symbols (RUNNER section)





    run_enabled_acquisition_lanes,
    _get_ct_adapter,
)

# Keep legacy module-level constants for backward compatibility
__all__ = [
    # From planner

from _core import aclose    'AcquisitionLane',
    'AcquisitionProfile',
    'AcquisitionContext',
    'AcquisitionLanePlan',
    'AcquisitionStrategySnapshot',
    'AcquisitionLaneOutcome',
    'SourceFamilyOutcome',
    'NonfeedPlanDebug',
    'MandatoryLaneTerminality',
    'FeedDominanceBudget',
    'LANE_RULES',
    'build_acquisition_plan',
    '_disabled_reason',
    'build_acquisition_report',
    'build_lane_query',
    'is_lane_enabled',
    'get_lane_plan',
    'lane_skip_reason',
    'normalize_source_family_outcome',
    'normalize_source_family_name',
    'canonicalize_source_family_outcomes',
    'normalize_terminal_state',
    'required_terminal_lanes',
    'lane_is_terminal',
    'terminality_report',
    '_load_feed_budget_from_env',
    '_has_explicit_ipfs_cid',
    '_extract_cids_from_text',
    'lookup_threat_entity',
    'DOMAIN_EXPANSIONS',
    '_expand_keyword_query',
    '_get_keyword_domain_expansion',
    '_wallet_to_findings',
    '_extract_crypto_from_query',
    'select_ct_domains_for_passivedns_pivot',
    '_extract_domain_from_ct_finding',
    'normalize_passive_dns_query',
    '_extract_ips_from_query',
    '_looks_like_ip',
    '_looks_like_domain',
    'ACQUISITION_REPORT_SCHEMA_VERSION',
    'NonfeedSeedContext',
    'NonfeedMissionController',
    'NonfeedMissionSnapshot',
    'MissionIntent',
    'MissionTargetKind',
    'infer_mission_intent',
    'normalize_acquisition_profile',
    'is_academic_profile',
    'is_deep_osint_m1_profile',
    'LaneSpecFeedNFD',
    'reconcile_lane_detail_fields',
    'complete_source_family_outcomes_from_lane_details',
    'TERMINAL_STATES',
    'NON_TERMINAL_STATES',
    # From runner
    'run_enabled_acquisition_lanes',
]
