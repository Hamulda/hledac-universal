"""
runtime/acquisition/report_builder.py

Acquisition report builder and telemetry reconciliation.
Extracted from acquisition_strategy.py (original L1821-2320).

MODERNIZATION (Issue #18):
  - build_acquisition_report() isolated here
  - reconcile_lane_detail_fields() imported from acquisition_telemetry_reconcile
  - complete_source_family_outcomes_from_lane_details() imported from acquisition_telemetry_reconcile
"""


from typing import Any

from hledac.universal.runtime.acquisition.nonfeed_eligibility import terminality_report
from hledac.universal.runtime.acquisition.nonfeed_outcomes import (
    AcquisitionStrategySnapshot,
    MandatoryLaneTerminality,
)





    ACQUISITION_REPORT_SCHEMA_VERSION,
)


def build_acquisition_report(
    query: str = "",
    plan: AcquisitionStrategySnapshot | None = None,

from _core import aclose    terminality: dict | None = None,
    nonfeed_plan_debug: Any = None,
    source_family_outcomes: list[dict] | None = None,
    return_guard: dict | None = None,
    prewindup_barrier: dict | None = None,
    scheduler_exit: dict | None = None,
    windup_guard_observation: dict | None = None,
    # F216B: Nonfeed diagnostic profile telemetry
    acquisition_profile: str | None = None,
    feed_cap_reason: str | None = None,
    nonfeed_priority_enabled: bool = False,
    nonfeed_profile_expected_lanes: list[str] | None = None,
    # F217C: PUBLIC bootstrap telemetry
    public_terminal_stage: str = "",
    public_stage_counters: dict | None = None,
    # F234: PUBLIC discovery empty reason for DISCOVERY_ERROR diagnosis
    public_discovery_empty_reason: str = "",
    # F221G: Preserved diagnostic reason when empty_reason was cleared
    public_discovery_debug_reason: str = "",
    # F214-ACQ: Public provider selection debug
    public_provider_selection_debug: dict | None = None,
    # Sprint F229A: Bootstrap ordering telemetry
    public_bootstrap_order: str = "disabled",
    public_bootstrap_prevented_discovery_timeout: bool = False,
    public_bootstrap_first_fetch_attempted: bool = False,
    # F1-3: keyword_seed_fallback telemetry
    keyword_seed_fallback_triggered: bool = False,
    # F217D: CT provider resilience telemetry
    ct_provider_status: str = "",
    ct_cache_used: bool = False,
    ct_cache_stale: bool = False,
    ct_cache_age_s: float = 0.0,
    ct_quarantine_count: int = 0,
    ct_quarantine_samples: list[str] | None = None,
    # F232: CT loss-stage telemetry
    ct_planned: bool = False,
    ct_scheduled: bool = False,
    ct_provider_selected: str = "",
    ct_request_attempted: bool = False,
    ct_request_timeout: bool = False,
    ct_raw_count: int = 0,
    ct_bridge_invoked: bool = False,
    ct_candidates_built: int = 0,
    ct_storage_attempted: bool = False,
    ct_storage_accepted: bool = False,
    ct_terminal_stage: str = "",
    ct_prelude_missing_but_final_attempted: bool = False,
    # F214: DOH acquisition report fields
    doh_planned: bool = False,
    doh_scheduled: bool = False,
    doh_request_attempted: bool = False,
    doh_domains_attempted: int = 0,
    doh_raw_count: int = 0,
    doh_accepted_findings: int = 0,
    doh_terminal_stage: str = "",
    doh_provider_errors: tuple[str, ...] = (),
    doh_cache_used: bool = False,
    # F234: Critical 33 batch — runtime error/signal fields
    ct_bridge_rejections_count: int = 0,
    ct_storage_rejected: int = 0,
    arrow_last_flush_error: str = "",
    arrow_batch_dropped: int = 0,
    arrow_flush_failure_count: int = 0,
    prewindup_barrier_errors: dict | None = None,
    return_guard_errors: dict | None = None,
    wayback_unchanged_rejected: int = 0,
    nonfeed_provider_failures: list | None = None,
    # F216G: Quality/duplicate/low-info rejection ledgers
    quality_rejection_summary_by_family: dict | None = None,
    duplicate_rejection_summary_by_family: dict | None = None,
    low_information_by_family: dict | None = None,
    # F217E: Nonfeed candidate ledger summary
    nonfeed_candidate_ledger_summary: dict | None = None,
    # F216E: Feed dominance budget telemetry
    feed_dominance_budget: dict | None = None,
    # F228C: Nonfeed surface completeness telemetry
    nonfeed_expected_lanes: list[str] | None = None,
    nonfeed_missing_expected_lanes: list[str] | None = None,
    wayback_terminal_state: str = "",
    passive_dns_terminal_state: str = "",
    nonfeed_surface_complete: bool = False,
    # F222I: Pivot seed telemetry
    pivot_seed_domains: tuple[str, ...] = (),
    pivot_seed_ips: tuple[str, ...] = (),
    pivot_seed_urls: tuple[str, ...] = (),
    pivot_seed_hashes: tuple[str, ...] = (),
    pivot_seed_cves: tuple[str, ...] = (),
    seed_context_available: bool = False,
    seed_context_propagated: bool = False,
    seed_context_skip_reason: str = "",
    seed_context_source: str = "",
    # F227A: lanes_unlocked_by_seed_context
    lanes_unlocked_by_seed_context: list[str] | None = None,
    # Sprint F225A: Acquisition plan build error surface
    acquisition_plan_build_failed: bool = False,
    acquisition_plan_build_error_type: str = "",
    acquisition_plan_build_error: str = "",
    # Sprint F228E: Acquisition plan prelude fields
    acquisition_plan_present_for_prelude: bool = False,
    acquisition_plan_lanes_for_prelude: tuple[str, ...] = (),
    acquisition_plan_enabled_lanes_for_prelude: tuple[str, ...] = (),
    acquisition_plan_profile_for_prelude: str = "",
    acquisition_plan_build_error_for_prelude: str = "",
    # Sprint F228E: Nonfeed prelude telemetry
    nonfeed_prelude_enabled: bool = False,
    nonfeed_prelude_expected_lanes: tuple[str, ...] = (),
    nonfeed_prelude_attempted_lanes: tuple[str, ...] = (),
    nonfeed_prelude_terminal_lanes: tuple[str, ...] = (),
    nonfeed_prelude_missing_lanes: tuple[str, ...] = (),
    nonfeed_prelude_error_by_lane: dict | None = None,
    nonfeed_prelude_accepted_by_lane: dict | None = None,
    nonfeed_prelude_duration_s: float = 0.0,
    nonfeed_prelude_feed_blocked_until_complete: bool = False,
    # F266: Circuit breaker runtime state for acquisition report
    circuit_breakers_state: dict | None = None,
) -> dict[str, Any]:
    """
    Build the canonical acquisition report dict for sprint telemetry.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: always returns a valid dict
    """
    try:
        return {
            "schema_version": ACQUISITION_REPORT_SCHEMA_VERSION,
            "query": query,
            "plan": _plan_to_dict(plan) if plan else None,
            "terminality": terminality or {},
            "nonfeed_plan_debug": _debug_to_dict(nonfeed_plan_debug),
            "source_family_outcomes": source_family_outcomes or [],
            "return_guard": return_guard or {},
            "prewindup_barrier": prewindup_barrier or {},
            "scheduler_exit": scheduler_exit or {},
            "windup_guard_observation": windup_guard_observation or {},
            # F216B
            "acquisition_profile": acquisition_profile or "default",
            "feed_cap_reason": feed_cap_reason or "",
            "nonfeed_priority_enabled": nonfeed_priority_enabled,
            "nonfeed_profile_expected_lanes": nonfeed_profile_expected_lanes or [],
            # F217C
            "public_terminal_stage": public_terminal_stage,
            "public_stage_counters": public_stage_counters or {},
            # F234
            "public_discovery_empty_reason": public_discovery_empty_reason,
            "public_discovery_debug_reason": public_discovery_debug_reason,
            "public_provider_selection_debug": public_provider_selection_debug or {},
            # F229A
            "public_bootstrap_order": public_bootstrap_order,
            "public_bootstrap_prevented_discovery_timeout": public_bootstrap_prevented_discovery_timeout,
            "public_bootstrap_first_fetch_attempted": public_bootstrap_first_fetch_attempted,
            # F1-3
            "keyword_seed_fallback_triggered": keyword_seed_fallback_triggered,
            # F217D
            "ct_provider_status": ct_provider_status,
            "ct_cache_used": ct_cache_used,
            "ct_cache_stale": ct_cache_stale,
            "ct_cache_age_s": ct_cache_age_s,
            "ct_quarantine_count": ct_quarantine_count,
            "ct_quarantine_samples": ct_quarantine_samples or [],
            # F232
            "ct_planned": ct_planned,
            "ct_scheduled": ct_scheduled,
            "ct_provider_selected": ct_provider_selected,
            "ct_request_attempted": ct_request_attempted,
            "ct_request_timeout": ct_request_timeout,
            "ct_raw_count": ct_raw_count,
            "ct_bridge_invoked": ct_bridge_invoked,
            "ct_candidates_built": ct_candidates_built,
            "ct_storage_attempted": ct_storage_attempted,
            "ct_storage_accepted": ct_storage_accepted,
            "ct_terminal_stage": ct_terminal_stage,
            "ct_prelude_missing_but_final_attempted": ct_prelude_missing_but_final_attempted,
            # F214
            "doh_planned": doh_planned,
            "doh_scheduled": doh_scheduled,
            "doh_request_attempted": doh_request_attempted,
            "doh_domains_attempted": doh_domains_attempted,
            "doh_raw_count": doh_raw_count,
            "doh_accepted_findings": doh_accepted_findings,
            "doh_terminal_stage": doh_terminal_stage,
            "doh_provider_errors": doh_provider_errors or (),
            "doh_cache_used": doh_cache_used,
            # F234
            "ct_bridge_rejections_count": ct_bridge_rejections_count,
            "ct_storage_rejected": ct_storage_rejected,
            "arrow_last_flush_error": arrow_last_flush_error,
            "arrow_batch_dropped": arrow_batch_dropped,
            "arrow_flush_failure_count": arrow_flush_failure_count,
            "prewindup_barrier_errors": prewindup_barrier_errors or {},
            "return_guard_errors": return_guard_errors or {},
            "wayback_unchanged_rejected": wayback_unchanged_rejected,
            "nonfeed_provider_failures": nonfeed_provider_failures or [],
            # F216G
            "quality_rejection_summary_by_family": quality_rejection_summary_by_family or {},
            "duplicate_rejection_summary_by_family": duplicate_rejection_summary_by_family or {},
            "low_information_by_family": low_information_by_family or {},
            # F217E
            "nonfeed_candidate_ledger_summary": nonfeed_candidate_ledger_summary or {},
            # F216E
            "feed_dominance_budget": feed_dominance_budget or {},
            # F228C
            "nonfeed_expected_lanes": nonfeed_expected_lanes or [],
            "nonfeed_missing_expected_lanes": nonfeed_missing_expected_lanes or [],
            "wayback_terminal_state": wayback_terminal_state,
            "passive_dns_terminal_state": passive_dns_terminal_state,
            "nonfeed_surface_complete": nonfeed_surface_complete,
            # F222I
            "pivot_seed_domains": pivot_seed_domains or (),
            "pivot_seed_ips": pivot_seed_ips or (),
            "pivot_seed_urls": pivot_seed_urls or (),
            "pivot_seed_hashes": pivot_seed_hashes or (),
            "pivot_seed_cves": pivot_seed_cves or (),
            "seed_context_available": seed_context_available,
            "seed_context_propagated": seed_context_propagated,
            "seed_context_skip_reason": seed_context_skip_reason,
            "seed_context_source": seed_context_source,
            # F227A
            "lanes_unlocked_by_seed_context": lanes_unlocked_by_seed_context or [],
            # F225A
            "acquisition_plan_build_failed": acquisition_plan_build_failed,
            "acquisition_plan_build_error_type": acquisition_plan_build_error_type,
            "acquisition_plan_build_error": acquisition_plan_build_error,
            # F228E
            "acquisition_plan_present_for_prelude": acquisition_plan_present_for_prelude,
            "acquisition_plan_lanes_for_prelude": acquisition_plan_lanes_for_prelude,
            "acquisition_plan_enabled_lanes_for_prelude": acquisition_plan_enabled_lanes_for_prelude,
            "acquisition_plan_profile_for_prelude": acquisition_plan_profile_for_prelude,
            "acquisition_plan_build_error_for_prelude": acquisition_plan_build_error_for_prelude,
            # F228E nonfeed prelude
            "nonfeed_prelude_enabled": nonfeed_prelude_enabled,
            "nonfeed_prelude_expected_lanes": nonfeed_prelude_expected_lanes,
            "nonfeed_prelude_attempted_lanes": nonfeed_prelude_attempted_lanes,
            "nonfeed_prelude_terminal_lanes": nonfeed_prelude_terminal_lanes,
            "nonfeed_prelude_missing_lanes": nonfeed_prelude_missing_lanes,
            "nonfeed_prelude_error_by_lane": nonfeed_prelude_error_by_lane or {},
            "nonfeed_prelude_accepted_by_lane": nonfeed_prelude_accepted_by_lane or {},
            "nonfeed_prelude_duration_s": nonfeed_prelude_duration_s,
            "nonfeed_prelude_feed_blocked_until_complete": nonfeed_prelude_feed_blocked_until_complete,
            # F266
            "circuit_breakers_state": circuit_breakers_state or {},
        }
    except Exception:
        return {"schema_version": ACQUISITION_REPORT_SCHEMA_VERSION, "query": query}


def _plan_to_dict(plan: Any) -> dict:
    """Convert plan snapshot to dict."""
    if plan is None:
        return {}
    if hasattr(plan, "to_dict"):
        return plan.to_dict()
    return {
        "query": getattr(plan, "query", ""),
        "profile": getattr(plan, "profile", ""),
        "duration_s": getattr(plan, "duration_s", 0.0),
        "aggressive_mode": getattr(plan, "aggressive_mode", False),
        "uma_state": getattr(plan, "uma_state", "ok"),
        "swap_detected": getattr(plan, "swap_detected", False),
        "enabled_lanes": list(getattr(plan, "enabled_lanes", [])),
    }


def _debug_to_dict(debug: Any) -> dict:
    """Convert nonfeed plan debug to dict."""
    if debug is None:
        return {}
    if hasattr(debug, "to_dict"):
        return debug.to_dict()
    if isinstance(debug, dict):
        return debug
    return {}


# ── Telemetry reconciliation imports ──────────────────────────────────────────────────
# These are imported from the original module to maintain compatibility


def reconcile_lane_detail_fields(
    family: str,
    outcome: dict,
    lane_details: list[dict] | None,
) -> dict:
    """
    Reconcile lane detail fields into source family outcome.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    # Inline implementation to avoid circular import
    if lane_details is None:
        return outcome
    family_details = [d for d in lane_details if d.get("family") == family]
    if not family_details:
        return outcome
    result = dict(outcome)
    for detail in family_details:
        result["accepted_count"] = result.get("accepted_count", 0) + detail.get("accepted_count", 0)
        result["rejected_count"] = result.get("rejected_count", 0) + detail.get("rejected_count", 0)
    return result


def complete_source_family_outcomes_from_lane_details(
    outcomes: list[dict],
    lane_details: list[dict] | None,
) -> list[dict]:
    """
    Complete source family outcomes by merging lane details.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    if lane_details is None:
        return outcomes
    result = []
    for outcome in outcomes:
        completed = reconcile_lane_detail_fields(
            outcome.get("family", ""), outcome, lane_details
        )
        result.append(completed)
    return result
