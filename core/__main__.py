"""
F186A CANONICAL SPRINT TRUTH CLOSURE — CLI Entry Point: python -m hledac.universal.core

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
  → core.__main__.run_sprint() [sole canonical sprint owner]

  Note: `python -m hledac.universal.core --sprint` is an ALTERNATE entrypoint
  that also calls run_sprint() directly, but the canonical operator path
  is through root __main__.py (python -m hledac.universal).

Canonical sprint owner: run_sprint()
All report truth (canonical_run_summary, runtime_truth, timing_truth,
checkpoint_zero_category, observed_run_tuple) flows from run_sprint().

Usage:
    python -m hledac.universal.core --sprint --query "LockBit ransomware" --duration 1800
    python -m hledac.universal.core --ct-pivot example.com
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import orjson

from hledac.universal.core.resource_governor import sample_uma_status
from hledac.universal.utils import mlx_cache
from hledac.universal.intelligence.ct_log_client import CTLogClient
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
from hledac.universal.knowledge.semantic_store import SemanticStore
from hledac.universal.paths import TOR_ROOT, get_sprint_json_report_path
from hledac.universal.runtime.sprint_scheduler import (
    SprintScheduler,
    SprintSchedulerConfig,
)
from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager
from hledac.universal.transport.tor_transport import TorTransport
from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager, _PHASE_ORDER
from hledac.universal.runtime.acquisition_strategy import (
    build_acquisition_report,
    normalize_source_family_outcome,
    canonicalize_source_family_outcomes,
    ACQUISITION_REPORT_SCHEMA_VERSION,
)
from hledac.universal.export.sprint_exporter import export_sprint

logger = logging.getLogger(__name__)


def _make_sprint_id() -> str:
    """Generate collision-resistant sprint ID using ns timestamp + short uuid suffix."""
    ts = time.time_ns() // 1_000_000  # millisecond precision
    uid = uuid.uuid4().hex[:6]  # 6-char hex suffix
    return f"8sa_{ts}_{uid}"


def _is_meaningful_run(
    actual_duration_s: float,
    cycles_completed: int,
    cycles_started: int,
    accepted_findings: int,
    total_pattern_hits: int,
    swap_detected: bool = False,
    uma_state: str = "ok",
) -> tuple[bool, str]:
    """
    Distinguish smoke from meaningful active evidence.

    Returns (is_meaningful, evidence_note).
    Smoke: too short, too few cycles, no signal whatsoever.
    Meaningful: enough runtime or evidence of real work.

    F176A: Hardware-limited smoke detection — swap/memory pressure + zero cycles
    is a distinct hardware-limited classification, NOT depleted query.
    """
    # Hard smoke: no cycles ran at all
    if cycles_started == 0:
        # F176A: Explicit hardware-limited distinction
        if swap_detected or uma_state in ("critical", "emergency"):
            return False, "hardware_limited_smoke: zero cycles, memory pressure detected"
        return False, "zero cycles started — entry only, no active work"

    # Short but found something: counts as minimal meaningful
    if accepted_findings > 0:
        return True, f"found {accepted_findings} findings despite short runtime"

    # Short but pattern activity: minimal signal
    if total_pattern_hits > 0 and actual_duration_s >= 15:
        return True, f"pattern activity ({total_pattern_hits} hits) despite short run"

    # Hard smoke thresholds
    if actual_duration_s < 30 and cycles_completed < 3:
        return False, f"runtime {actual_duration_s:.0f}s and {cycles_completed} cycles below minimum"

    if actual_duration_s < 10:
        return False, f"runtime {actual_duration_s:.1f}s — entry/import only"

    # E0-T4: <180s without findings is meaningful_empty, not meaningful.
    # authoritative early-returns above (findings > 0, hits >= 15) are exempt.
    if actual_duration_s < 180 and accepted_findings == 0 and total_pattern_hits == 0:
        return False, (
            f"runtime {actual_duration_s:.0f}s < 180s floor, "
            f"no findings, no pattern hits — below meaningful threshold"
        )

    # Normal meaningful run
    return True, (
        f"{actual_duration_s:.0f}s runtime, "
        f"{cycles_completed}/{cycles_started} cycles completed, "
        f"no findings but within normal parameters"
    )


def _scheduler_result_acquisition_payload(
    result: "SprintSchedulerResult",
    scheduler: "SprintScheduler",
    query: str,
    duration_s: float,
) -> dict:
    """
    [F208I-A] Extract acquisition terminality and report fields from SprintSchedulerResult.

    Fails soft — missing fields produce None/empty defaults, never crash.
    Returns a flat dict with top-level keys for all acquisition terminality fields
    so run_sprint() can spread them into the final report JSON.

    Return keys:
        acquisition_report          -- canonical acquisition report dict (build_acquisition_report or fallback)
        acquisition_terminality_checked   -- bool
        acquisition_terminality_satisfied -- bool
        acquisition_terminality_missing_lanes -- list
        acquisition_terminality_report    -- dict
        source_family_outcomes      -- list of SourceFamilyOutcome.to_dict() dicts
        scheduler_exit             -- dict with exit path/reason/phase/cycle
        return_guard               -- dict with return guard observation
        windup_guard_observation   -- dict with windup guard call counts
        prewindup_barrier          -- dict with prewindup barrier state
        # Sprint F209B: Acquisition prelude pass-through
        acquisition_prelude_checked       -- bool
        acquisition_prelude_ran           -- bool
        acquisition_prelude_required_lanes -- list
        acquisition_prelude_terminal_lanes -- list
        acquisition_prelude_missing_lanes  -- list
        acquisition_prelude_skipped_lanes  -- dict
        acquisition_prelude_errors        -- dict
        acquisition_prelude_duration_s    -- float
        acquisition_prelude_reason         -- str
    """
    # ── 1. Source family outcomes ────────────────────────────────────────────
    # Synthesize from acquisition_lane_outcomes and result counters
    _sfo_list: list[dict] = []

    # Feed family — always present if scheduler ran
    # [F223D] Use full result.accepted_findings (all lanes + ct_log_stored accumulated
    # by windup time) as runtime_accepted_findings so PVS uses the true runtime total.
    # The normalized accepted_count for FEED scorecard remains result.accepted_findings
    # at this windup entry point (nonfeed lanes in separate counters, ct_log_stored
    # added after this function returns at line 908).
    _feed_raw = result.accepted_findings
    if _feed_raw > 0 or result.total_pattern_hits > 0:
        _sfo_list.append(
            normalize_source_family_outcome(
                "FEED",
                {
                    "family": "FEED",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": result.total_pattern_hits,
                    "built_count": 0,
                    "accepted_count": _feed_raw,
                    "error": None,
                    "timeout": False,
                    "duration_s": None,
                },
            )
        )

    # Public family — DISCOVERY_TIMEOUT/ERROR/ZERO_RESULTS also emit an outcome
    # even when raw_count=0 and accepted_count=0 (F234B)
    _pub_has_outcome = (
        getattr(result, "public_discovered", 0) > 0
        or getattr(result, "public_accepted_findings", 0) > 0
        or bool(getattr(result, "public_terminal_stage", ""))
        or bool(getattr(result, "public_error", ""))
        or (getattr(result, "public_stage_counters", None) is not None
            and getattr(result, "public_stage_counters", {}) != {}
            and getattr(result, "public_stage_counters", {}).get("fetch_attempted", 0) > 0)
    )
    if _pub_has_outcome:
        _pub_raw = {
            "family": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "skip_reason": None,
            "raw_count": getattr(result, "public_discovered", 0),
            "built_count": 0,
            "accepted_count": getattr(result, "public_accepted_findings", 0),
            "error": getattr(result, "public_error", None) or getattr(result, "public_terminal_stage", "") or None,
            "timeout": getattr(result, "public_terminal_stage", "") == "DISCOVERY_TIMEOUT",
            "duration_s": None,
        }
        _sfo_list.append(normalize_source_family_outcome("PUBLIC", _pub_raw))

    # CT log family — planned/scheduled/error/timeout also emit outcome even when 0 (F234B)
    _ct_has_outcome = (
        getattr(result, "ct_log_discovered", 0) > 0
        or getattr(result, "ct_log_accepted_findings", 0) > 0
        or bool(getattr(result, "ct_terminal_stage", ""))
        or bool(getattr(result, "ct_log_error", ""))
        or getattr(result, "ct_planned", False)
        or getattr(result, "ct_scheduled", False)
        or getattr(result, "ct_request_attempted", False)
        or bool(getattr(result, "ct_provider_status", ""))
    )
    if _ct_has_outcome:
        _ct_raw = {
            "family": "CT",
            "attempted": getattr(result, "ct_request_attempted", False) or getattr(result, "ct_scheduled", False) or getattr(result, "ct_planned", False),
            "skipped": False,
            "skip_reason": None,
            "raw_count": getattr(result, "ct_log_discovered", 0),
            "built_count": 0,
            "accepted_count": getattr(result, "ct_log_accepted_findings", 0),
            "error": getattr(result, "ct_log_error", None) or getattr(result, "ct_terminal_stage", "") or None,
            "timeout": getattr(result, "ct_terminal_stage", "") == "request_timeout",
            "duration_s": None,
        }
        _sfo_list.append(normalize_source_family_outcome("CT", _ct_raw))

    # Map acquisition_lane_outcomes (AcquisitionLaneOutcome tuples) to SourceFamilyOutcome
    # Each AcquisitionLaneOutcome has .lane, .source_family, .accepted_findings, etc.
    _lanes_seen: set[str] = set()
    for _o in getattr(result, "acquisition_lane_outcomes", None) or ():
        if not hasattr(_o, "lane"):
            continue
        _lane = _o.lane
        if _lane in _lanes_seen:
            continue
        _lanes_seen.add(_lane)
        _raw_dict = {
            "family": getattr(_o, "source_family", _lane.upper()),
            "attempted": getattr(_o, "attempted", False),
            "skipped": not getattr(_o, "attempted", False),
            "skip_reason": None if getattr(_o, "attempted", False) else "lane_not_attempted",
            "raw_count": getattr(_o, "ct_results_raw", 0),
            "built_count": getattr(_o, "produced_items", 0),
            "accepted_count": getattr(_o, "accepted_findings", 0),
            "error": getattr(_o, "error", None),
            "timeout": getattr(_o, "timeout", False),
            "duration_s": getattr(_o, "duration_s", None),
        }
        _sfo_list.append(normalize_source_family_outcome(_raw_dict["family"], _raw_dict))

    # F235D: Canonicalize source family outcomes — dedup and merge same-family entries
    # so no report contains both "CT" and "ct" as separate contradictory outcomes.
    _sfo_list = canonicalize_source_family_outcomes(_sfo_list)

    # ── 2. Scheduler exit ─────────────────────────────────────────────────
    _se_dict: dict = {
        "exit_path": getattr(result, "scheduler_exit_path", None),
        "exit_reason": getattr(result, "scheduler_exit_reason", None),
        "exit_phase": getattr(result, "scheduler_exit_phase", None),
        "exit_cycle": getattr(result, "scheduler_exit_cycle", None),
        "exit_elapsed_s": getattr(result, "scheduler_exit_elapsed_s", None),
        "exit_guard_checked": getattr(result, "scheduler_exit_guard_checked", None),
        "exit_guard_satisfied": getattr(result, "scheduler_exit_guard_satisfied", None),
    }

    # ── 3. Return guard ────────────────────────────────────────────────────
    _rg_dict: dict = {
        "return_guard_checked": getattr(result, "return_guard_checked", False),
        "return_guard_satisfied": getattr(result, "return_guard_satisfied", False),
        "return_guard_block_reason": getattr(result, "return_guard_block_reason", ""),
        "return_guard_attempted_lanes": list(getattr(result, "return_guard_attempted_lanes", ()) or ()),
        "return_guard_skipped_lanes": dict(getattr(result, "return_guard_skipped_lanes", {}) or {}),
        "return_guard_errors": dict(getattr(result, "return_guard_errors", {}) or {}),
        "return_guard_delayed_for_nonfeed": getattr(result, "return_guard_delayed_for_nonfeed", False),
    }

    # ── 4. Windup guard observation ───────────────────────────────────────
    _wg_last_reason = getattr(result, "windup_guard_last_reason", None)
    _wg_last_allowed = getattr(result, "windup_guard_last_allowed", None)
    _wg_dict: dict = {
        "windup_guard_call_count": getattr(result, "windup_guard_call_count", 0),
        "windup_guard_callback_supplied_count": getattr(
            result, "windup_guard_callback_supplied_count", 0
        ),
        "windup_guard_callback_executed_count": getattr(
            result, "windup_guard_callback_executed_count", 0
        ),
        "windup_guard_required_lanes": list(
            getattr(result, "windup_guard_required_lanes", ()) or ()
        ),
        "windup_guard_not_applicable": getattr(result, "windup_guard_not_applicable", False),
        "windup_guard_last_reason": _wg_last_reason,
        "windup_guard_last_allowed": _wg_last_allowed,
        "windup_guard_callback_not_executed_reason": getattr(
            result, "windup_guard_last_callback_not_executed_reason", ""
        ),
    }

    # ── 5. Pre-windup barrier ───────────────────────────────────────────────
    _pwb: dict = {
        "prewindup_barrier_checked": getattr(result, "prewindup_barrier_checked", False),
        "prewindup_barrier_required_lanes": list(
            getattr(result, "prewindup_barrier_required_lanes", ()) or ()
        ),
        "prewindup_barrier_satisfied": getattr(result, "prewindup_barrier_satisfied", False),
        "prewindup_barrier_attempted_lanes": list(
            getattr(result, "prewindup_barrier_attempted_lanes", ()) or ()
        ),
        "prewindup_barrier_skipped_lanes": dict(
            getattr(result, "prewindup_barrier_skipped_lanes", {}) or {}
        ),
        "prewindup_barrier_errors": dict(getattr(result, "prewindup_barrier_errors", {}) or {}),
        "prewindup_barrier_duration_s": getattr(result, "prewindup_barrier_duration_s", 0.0),
    }

    # ── 6. Acquisition terminality ───────────────────────────────────────────
    _term_rep: dict = getattr(result, "acquisition_terminality_report", {}) or {}

    # ── 7. Try build_acquisition_report from acquisition_strategy ────────────
    # F234: _acq_input is assigned at line 864 (after this block).
    # Initialize sentinel here so any reference in this try block is safe.
    _acq_input: str | None = None
    _acq_effective: str | None = None
    _acq_normalized: bool = False
    _acq_report: dict = {}
    try:
        _plan = getattr(scheduler, "_acquisition_plan", None)
        _nd_raw = getattr(_plan, "nonfeed_plan_debug", None) if _plan is not None else None
        _nd: dict | None = None
        if _nd_raw is not None:
            _nd = {
                "domain_detected": getattr(_nd_raw, "domain_detected", False),
                "wallet_detected": getattr(_nd_raw, "wallet_detected", False),
                "enabled_nonfeed_lanes": list(
                    getattr(_nd_raw, "enabled_nonfeed_lanes", ()) or ()
                ),
                "disabled_nonfeed_lanes": list(
                    getattr(_nd_raw, "disabled_nonfeed_lanes", ()) or ()
                ),
                "disabled_reasons": list(
                    getattr(_nd_raw, "disabled_reasons", ()) or ()
                ),
                "scheduled_nonfeed_lanes": list(
                    getattr(_nd_raw, "scheduled_nonfeed_lanes", ()) or ()
                ),
                "hardware_skipped_lanes": list(
                    getattr(_nd_raw, "hardware_skipped_lanes", ()) or ()
                ),
                "nonfeed_execution_scheduled": getattr(
                    _nd_raw, "nonfeed_execution_scheduled", False
                ),
                "nonfeed_execution_skip_reason": getattr(
                    _nd_raw, "nonfeed_execution_skip_reason", None
                ),
                # F216B: nonfeed_diagnostic profile telemetry
                "acquisition_profile": getattr(
                    _nd_raw, "acquisition_profile", "default"
                ),
                "feed_cap_reason": getattr(
                    _nd_raw, "feed_cap_reason", None
                ),
                "nonfeed_priority_enabled": getattr(
                    _nd_raw, "nonfeed_priority_enabled", False
                ),
                "nonfeed_profile_expected_lanes": list(
                    getattr(_nd_raw, "nonfeed_profile_expected_lanes", ()) or ()
                ),
            }
        _acq_report = build_acquisition_report(
            plan=_plan,
            terminality=_term_rep,
            nonfeed_plan_debug=_nd,
            source_family_outcomes=_sfo_list,
            return_guard=_rg_dict,
            prewindup_barrier=_pwb,
            scheduler_exit=_se_dict,
            windup_guard_observation=_wg_dict,
            # F216B: Nonfeed diagnostic profile telemetry (from _nd already built above)
            acquisition_profile=_nd.get("acquisition_profile", "default") if _nd else "default",
            feed_cap_reason=_nd.get("feed_cap_reason") if _nd else None,
            nonfeed_priority_enabled=_nd.get("nonfeed_priority_enabled", False) if _nd else False,
            nonfeed_profile_expected_lanes=_nd.get("nonfeed_profile_expected_lanes", []) if _nd else [],
            # F217C: PUBLIC bootstrap telemetry
            public_terminal_stage=getattr(result, "public_terminal_stage", ""),
            public_stage_counters=getattr(result, "public_stage_counters", None),
            # F234: PUBLIC discovery empty reason for DISCOVERY_ERROR diagnosis
            public_discovery_empty_reason=getattr(result, "public_discovery_empty_reason", ""),
            # F214-ACQ: Public provider selection debug — why provider was/wasn't selected
            public_provider_selection_debug=getattr(result, "public_provider_selection_debug", None) or {},
            # F217D: CT provider resilience telemetry
            ct_provider_status=getattr(result, "ct_provider_status", ""),
            ct_cache_used=getattr(result, "ct_cache_used", False),
            ct_cache_stale=getattr(result, "ct_cache_stale", False),
            ct_cache_age_s=getattr(result, "ct_cache_age_s", 0.0),
            ct_quarantine_count=getattr(result, "ct_quarantine_count", 0),
            ct_quarantine_samples=list(getattr(result, "ct_quarantine_samples", ()) or ()),
            # F232: CT loss-stage telemetry
            ct_planned=getattr(result, "ct_planned", False),
            ct_scheduled=getattr(result, "ct_scheduled", False),
            ct_provider_selected=getattr(result, "ct_provider_selected", ""),
            ct_request_attempted=getattr(result, "ct_request_attempted", False),
            ct_request_timeout=getattr(result, "ct_request_timeout", False),
            ct_raw_count=getattr(result, "ct_raw_count", 0),
            ct_bridge_invoked=getattr(result, "ct_bridge_invoked", False),
            ct_candidates_built=getattr(result, "ct_candidates_built", 0),
            ct_storage_attempted=getattr(result, "ct_storage_attempted", False),
            ct_storage_accepted=getattr(result, "ct_storage_accepted", False),
            ct_terminal_stage=getattr(result, "ct_terminal_stage", ""),
            ct_prelude_missing_but_final_attempted=getattr(
                result, "ct_prelude_missing_but_final_attempted", False
            ),
            # F216G: Quality/duplicate/low-info rejection ledgers (from result if available)
            quality_rejection_summary_by_family=getattr(result, "quality_rejection_summary_by_family", None),
            duplicate_rejection_summary_by_family=getattr(result, "duplicate_rejection_summary_by_family", None),
            low_information_by_family=getattr(result, "low_information_by_family", None),
            # F217E: Nonfeed candidate ledger summary
            nonfeed_candidate_ledger_summary=getattr(result, "nonfeed_candidate_ledger_summary", None),
            # F216E: Feed dominance budget telemetry (from _plan if available)
            feed_dominance_budget=getattr(_plan, "feed_dominance_budget", None) if _plan else None,
        )
        # F224: Ensure acquisition_prelude is surfaced in acquisition_report
        # build_acquisition_report does not yet have acquisition_prelude params,
        # so we inject from result attributes after the call.
        _acq_report["acquisition_prelude_checked"] = getattr(result, "acquisition_prelude_checked", False)
        _acq_report["acquisition_prelude_ran"] = getattr(result, "acquisition_prelude_ran", False)
        _acq_report["acquisition_prelude_required_lanes"] = list(
            getattr(result, "acquisition_prelude_required_lanes", ()) or ()
        )
        _acq_report["acquisition_prelude_terminal_lanes"] = list(
            getattr(result, "acquisition_prelude_terminal_lanes", ()) or ()
        )
        _acq_report["acquisition_prelude_missing_lanes"] = list(
            getattr(result, "acquisition_prelude_missing_lanes", ()) or ()
        )
        _acq_report["acquisition_prelude_skipped_lanes"] = dict(
            getattr(result, "acquisition_prelude_skipped_lanes", {}) or {}
        )
        _acq_report["acquisition_prelude_errors"] = dict(
            getattr(result, "acquisition_prelude_errors", {}) or {}
        )
        _acq_report["acquisition_prelude_duration_s"] = getattr(result, "acquisition_prelude_duration_s", 0.0)
        _acq_report["acquisition_prelude_reason"] = getattr(result, "acquisition_prelude_reason", "")
        # F228A: Normalization telemetry — surfaces the three-phase normalization chain
        _acq_report["acquisition_profile_input"] = _acq_input
        _acq_report["acquisition_profile_effective"] = _acq_effective
        _acq_report["acquisition_profile_normalized"] = _acq_normalized
        # NOTE R1: surfaced from scheduler runtime — previously unread downstream.
        # budget_violations > 0 indicates sprint exceeded resource budget.
        # return_guard_block_reason non-empty indicates why sprint return was blocked.
        _acq_report["budget_violations"] = getattr(result, "budget_violations", 0)
        _acq_report["return_guard_block_reason"] = getattr(result, "return_guard_block_reason", "") or ""
        # NOTE R2: ct_quarantine_count and ct_quarantine_samples surfaced from scheduler runtime.
        # ct_quarantine_count > 0 indicates CT findings were quarantined before bridge.
        # tuple -> list for JSON serialization.
        _acq_report["ct_quarantine_count"] = getattr(result, "ct_quarantine_count", 0)
        _acq_report["ct_quarantine_samples"] = list(
            getattr(result, "ct_quarantine_samples", ()) or ()
        )
    except Exception as _exc:
        logger.exception(
            "[F234-FALLBACK] build_acquisition_report raised — "
            "falling back to default profile. "
            "acquisition_profile_input=%r acquisition_profile_effective=%r",
            _acq_input,  # sentinel defined at line 327 (F234)
            _acq_effective,  # sentinel defined at line 327 (F234)
        )
        try:
            _fallback_profile = (
                _nd.get("acquisition_profile", "default") if _nd else "default"
            )
            _acq_report = {
                "schema_version": f"{ACQUISITION_REPORT_SCHEMA_VERSION}-fallback",
                "terminality": _term_rep,
                "source_family_outcomes": _sfo_list,
                "return_guard": _rg_dict,
                "prewindup_barrier": _pwb,
                "scheduler_exit": _se_dict,
                "windup_guard_observation": _wg_dict,
                "fallback_reason": f"canonical_build_failed: {_exc}",
                "acquisition_report_fallback_used": True,  # F232F: fail-loud marker
                "plan": getattr(_plan, "plans", None) if _plan else None,
                "nonfeed_plan_debug": _nd,  # F232F: preserve _nd when available
                # F216B: Nonfeed diagnostic profile telemetry — preserve profile from _nd
                "acquisition_profile": _fallback_profile,
                "feed_cap_reason": _nd.get("feed_cap_reason") if _nd else None,
                "nonfeed_priority_enabled": (
                    _nd.get("nonfeed_priority_enabled", False) if _nd else False
                ),
                "nonfeed_profile_expected_lanes": _nd.get("nonfeed_profile_expected_lanes", []) if _nd else [],
                # F217C: PUBLIC bootstrap telemetry
                "public_terminal_stage": getattr(result, "public_terminal_stage", ""),
                "public_stage_counters": getattr(result, "public_stage_counters", None),
                # F234: PUBLIC discovery empty reason for DISCOVERY_ERROR diagnosis
                "public_discovery_empty_reason": getattr(result, "public_discovery_empty_reason", ""),
                # F214-ACQ: Public provider selection debug
                "public_provider_selection_debug": getattr(result, "public_provider_selection_debug", None) or {},
                # F217D: CT provider resilience telemetry
                "ct_provider_status": getattr(result, "ct_provider_status", ""),
                "ct_cache_used": getattr(result, "ct_cache_used", False),
                "ct_cache_stale": getattr(result, "ct_cache_stale", False),
                "ct_cache_age_s": getattr(result, "ct_cache_age_s", 0.0),
                "ct_quarantine_count": getattr(result, "ct_quarantine_count", 0),
                "ct_quarantine_samples": list(getattr(result, "ct_quarantine_samples", ()) or ()),
                # F232: CT loss-stage telemetry
                "ct_planned": getattr(result, "ct_planned", False),
                "ct_scheduled": getattr(result, "ct_scheduled", False),
                "ct_provider_selected": getattr(result, "ct_provider_selected", ""),
                "ct_request_attempted": getattr(result, "ct_request_attempted", False),
                "ct_request_timeout": getattr(result, "ct_request_timeout", False),
                "ct_raw_count": getattr(result, "ct_raw_count", 0),
                "ct_bridge_invoked": getattr(result, "ct_bridge_invoked", False),
                "ct_candidates_built": getattr(result, "ct_candidates_built", 0),
                "ct_storage_attempted": getattr(result, "ct_storage_attempted", False),
                "ct_storage_accepted": getattr(result, "ct_storage_accepted", False),
                "ct_terminal_stage": getattr(result, "ct_terminal_stage", ""),
                "ct_prelude_missing_but_final_attempted": getattr(
                    result, "ct_prelude_missing_but_final_attempted", False
                ),
                # F216G: Quality/duplicate/low-info rejection ledgers
                "quality_rejection_summary_by_family": None,
                "duplicate_rejection_summary_by_family": None,
                "low_information_by_family": None,
                # F217E: Nonfeed candidate ledger summary
                "nonfeed_candidate_ledger_summary": getattr(result, "nonfeed_candidate_ledger_summary", None),
                # F216E: Feed dominance budget telemetry
                "feed_dominance_budget": None,
                # NOTE R1: surfaced from scheduler runtime
                "budget_violations": getattr(result, "budget_violations", 0),
                "return_guard_block_reason": getattr(result, "return_guard_block_reason", "") or "",
            }
        except Exception as _fallback_exc:
            logger.critical(
                "FALLBACK ALSO FAILED — acquisition report unavailable: %s: %s",
                type(_fallback_exc).__name__, _fallback_exc,
            )
            # NOTE S3: double-fallback failure — return minimal sentinel
            # so downstream gate receives something rather than crashing.
            _acq_report = {
                "schema_version": f"{ACQUISITION_REPORT_SCHEMA_VERSION}-fallback",
                "fallback_reason": f"double_fallback_failure: {_exc} -> {_fallback_exc}",
                "acquisition_report_fallback_used": True,
                "acquisition_report_double_fallback": True,
                "acquisition_profile": "unavailable",
                "error": str(_fallback_exc),
            }

    return {
        "acquisition_report": _acq_report,
        "acquisition_terminality_checked": getattr(result, "acquisition_terminality_checked", False),
        "acquisition_terminality_satisfied": getattr(
            result, "acquisition_terminality_satisfied", False
        ),
        "acquisition_terminality_missing_lanes": list(
            getattr(result, "acquisition_terminality_missing_lanes", ()) or ()
        ),
        "acquisition_terminality_report": _term_rep,
        "source_family_outcomes": _sfo_list,
        "scheduler_exit": _se_dict,
        "return_guard": _rg_dict,
        "windup_guard_observation": _wg_dict,
        "prewindup_barrier": _pwb,
        # Sprint F209B: Acquisition prelude pass-through
        "acquisition_prelude_checked": getattr(result, "acquisition_prelude_checked", False),
        "acquisition_prelude_ran": getattr(result, "acquisition_prelude_ran", False),
        "acquisition_prelude_required_lanes": list(
            getattr(result, "acquisition_prelude_required_lanes", ()) or ()
        ),
        "acquisition_prelude_terminal_lanes": list(
            getattr(result, "acquisition_prelude_terminal_lanes", ()) or ()
        ),
        "acquisition_prelude_missing_lanes": list(
            getattr(result, "acquisition_prelude_missing_lanes", ()) or ()
        ),
        "acquisition_prelude_skipped_lanes": dict(
            getattr(result, "acquisition_prelude_skipped_lanes", {}) or {}
        ),
        "acquisition_prelude_errors": dict(
            getattr(result, "acquisition_prelude_errors", {}) or {}
        ),
        "acquisition_prelude_duration_s": getattr(result, "acquisition_prelude_duration_s", 0.0),
        "acquisition_prelude_reason": getattr(result, "acquisition_prelude_reason", ""),
        # Sprint F215D: Early exit semantics — canonical classification of WHY run ended early
        "early_exit_class": getattr(result, "early_exit_class", ""),
        "early_exit_reason": getattr(result, "early_exit_reason", ""),
        "requested_duration_s": getattr(result, "requested_duration_s", 0.0),
        "actual_duration_s": getattr(result, "actual_duration_s", 0.0),
        "elapsed_pct": getattr(result, "elapsed_pct", 0.0),
        "active_window_budget_s": getattr(result, "active_window_budget_s", 0.0),
        "active_window_elapsed_s": getattr(result, "active_window_elapsed_s", 0.0),
    }


def _runtime_truth(
    actual_duration_s: float,
    query: str,
    duration_s: float,
    cycles_completed: int,
    cycles_started: int,
    accepted_findings: int,
    total_pattern_hits: int,
    public_accepted_findings: int,
    feed_findings: int,
    # Sprint F194A: CT findings are additive to feed/public in canonical truth
    ct_findings: int = 0,
    # F176A: Hardware pressure surfaces for smoke classification
    swap_detected: bool = False,
    uma_state: str = "ok",
    # Sprint F195B: Branch timeout telemetry
    branch_timeout_count: int = 0,
    public_branch_timed_out: bool = False,
    ct_branch_timed_out: bool = False,
) -> dict:
    """Build canonical runtime-truth record from scheduler result data."""
    is_meaningful, evidence_note = _is_meaningful_run(
        actual_duration_s, cycles_completed, cycles_started,
        accepted_findings, total_pattern_hits,
        swap_detected=swap_detected,
        uma_state=uma_state,
    )

    # Branch mix — dominant signal source
    # Sprint F194A: CT findings tracked as distinct branch in branch_mix
    branch_mix = {
        "feed_findings": feed_findings,
        "public_findings": public_accepted_findings,
        "ct_findings": ct_findings,
    }

    # Primary signal source label — Sprint F194A: CT findings can dominate
    if ct_findings > 0 and feed_findings == 0 and public_accepted_findings == 0:
        primary = "ct"
    elif feed_findings > 0 and public_accepted_findings == 0 and ct_findings == 0:
        primary = "feed"
    elif public_accepted_findings > 0 and feed_findings == 0 and ct_findings == 0:
        primary = "public"
    elif feed_findings > 0 and public_accepted_findings > 0 and ct_findings == 0:
        # F214-ACQ: When feed dominates (>95%) and non-feed is minimal, label as feed
        # not mixed — the signal is overwhelmingly from the feed lane.
        total_nonfeed = public_accepted_findings + ct_findings
        feed_dominance_ratio = feed_findings / (feed_findings + total_nonfeed) if (feed_findings + total_nonfeed) > 0 else 1.0
        if feed_dominance_ratio > 0.95:
            primary = "feed"
        else:
            primary = "mixed"
    elif ct_findings > 0 and (feed_findings > 0 or public_accepted_findings > 0):
        primary = "mixed_ct"
    else:
        primary = "none"

    return {
        "is_meaningful": is_meaningful,
        "evidence_note": evidence_note,
        "command_params": {
            "query": query,
            "requested_duration_s": duration_s,
        },
        "actual_duration_s": round(actual_duration_s, 2),
        "cycles_completed": cycles_completed,
        "cycles_started": cycles_started,
        "branch_mix": branch_mix,
        "primary_signal_source": primary,
        "total_pattern_hits": total_pattern_hits,
        "accepted_findings": accepted_findings,
        # F176A: Hardware pressure surfaces for smoke classification
        "pre_sprint_swap_detected": swap_detected,
        "pre_sprint_uma_state": uma_state,
        # Sprint F195B: Branch timeout telemetry
        "branch_timeout_count": branch_timeout_count,
        "public_branch_timed_out": public_branch_timed_out,
        "ct_branch_timed_out": ct_branch_timed_out,
    }

def _get_live_feed_urls() -> list[str]:
    """
    Return canonical runtime feed URLs for live sprint path.

    Uses get_runtime_feed_seeds() from rss_atom_adapter — the single source
    of truth for the runtime RSS/Atom feed surface. Returns only ``curated_seed``
    entries sorted by priority descending. This is the accessor the canonical
    sprint owner path should use; topology_candidates are excluded by design.
    """
    from hledac.universal.discovery.rss_atom_adapter import get_runtime_feed_seeds
    return [seed.feed_url for seed in get_runtime_feed_seeds()]


# =============================================================================
# Pre-sprint checks
# =============================================================================

# Sprint M218A: GC startup tuning for M1 UMA stability.
# gc.freeze() reduces GC pause variance during long sprints.
# gc.set_threshold(1000,50,50) reduces collection frequency.
# Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.
_gc_configured: bool = False


def _configure_gc_for_sprint() -> dict:
    """
    Configure Python GC for sprint workload.

    Called once at sprint boot. Freezes GC to reduce pause variance on M1.
    Sets threshold to (1000, 50, 50) to reduce collection frequency.
    Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.

    Returns a dict with telemetry fields.
    """
    global _gc_configured
    result = {
        "gc_freeze_attempted": False,
        "gc_freeze_applied": False,
        "gc_thresholds": None,
        "gc_freeze_error": None,
    }
    if _gc_configured:
        return result

    import gc as _gc

    result["gc_freeze_attempted"] = True
    if os.environ.get("HLEDAC_DISABLE_GC_FREEZE") == "1":
        logger.info("[GC] HLEDAC_DISABLE_GC_FREEZE=1 — skipping gc.freeze()")
    else:
        try:
            if hasattr(_gc, "freeze"):
                _gc.freeze()
                result["gc_freeze_applied"] = True
                logger.info("[GC] gc.freeze() applied — reduces GC pause variance")
            else:
                logger.debug("[GC] gc.freeze() not available on this Python build")
        except Exception as exc:
            result["gc_freeze_error"] = str(exc)
            logger.debug(f"[GC] gc.freeze() failed (non-fatal): {exc}")

    try:
        _gc.set_threshold(1000, 50, 50)
        result["gc_thresholds"] = (1000, 50, 50)
        logger.debug("[GC] gc.set_threshold(1000, 50, 50)")
    except Exception as exc:
        result["gc_thresholds"] = None
        logger.debug(f"[GC] set_threshold failed (non-fatal): {exc}")

    _gc_configured = True
    return result


def run_pre_sprint_checks() -> bool:
    """
    Run mandatory pre-sprint checks.

    Returns True if safe to proceed, False to abort.
    """
    checks_passed = True

    # MLX wired limit — fail-soft (Sprint F207D)
    # MLX is optional. Skip Metal limit config when unavailable.
    if not mlx_cache.MLX_AVAILABLE:
        logger.info("[BOOT] MLX unavailable — skipping Metal wired limit")
    else:
        try:
            mlx_cache.init_mlx_buffers()
            status = mlx_cache.get_metal_limits_status()
            _fmt = lambda v: f"{v // (1024 * 1024):.0f}MiB" if v else "N/A"
            logger.info(
                f"[BOOT] MLX buffers: cache={_fmt(status['cache_limit_bytes'])} wired={_fmt(status['wired_limit_bytes'])} configured={status['configured']}"
            )
        except Exception as exc:
            logger.warning(f"[BOOT] MLX buffer init failed: {exc}")

    # Swap check — WARNING only, non-blocking
    s = sample_uma_status()
    if s.swap_used_gib > 2.0:
        logger.warning(
            f"[BOOT] SWAP {s.swap_used_gib:.1f}GB > 2GB — "
            f"doporučuji restart před long run"
        )

    logger.info(
        f"[BOOT] Pre-sprint checks OK | "
        f"UMA: {s.system_used_gib:.2f}GiB used | swap: {s.swap_used_gib:.2f}GiB"
    )
    return checks_passed


# =============================================================================
# Sprint delta writer (uses existing DuckDB schema)
# =============================================================================


def _derive_top_source(hits_per_source: dict[str, int]) -> str:
    """Return source with most hits, or empty string if no data."""
    if not hits_per_source:
        return ""
    return max(hits_per_source, key=lambda k: hits_per_source[k])


async def write_sprint_delta(
    store: DuckDBShadowStore,
    sprint_id: str,
    query: str,
    new_findings: int,
    dedup_hits: int,
    ioc_nodes: int,
    uma_baseline_gib: float,
    uma_peak_gib: float,
    synthesis_success: bool,
    duration_s: float,
    hits_per_source: dict[str, int],
) -> None:
    """Write sprint_delta record to DuckDB at TEARDOWN."""
    try:
        findings_per_min = (new_findings / (duration_s / 60.0)) if duration_s > 0 else 0.0
        top_source = _derive_top_source(hits_per_source)
        row = {
            "sprint_id": sprint_id,
            "ts": time.time(),
            "query": query,
            "duration_s": duration_s,
            "new_findings": new_findings,
            "dedup_hits": dedup_hits,
            "ioc_nodes": ioc_nodes,
            "ioc_new_this_sprint": new_findings,
            "uma_peak_gib": uma_peak_gib - uma_baseline_gib,
            "synthesis_success": synthesis_success,
            "findings_per_min": findings_per_min,
            "top_source_type": top_source,
            "synthesis_confidence": 1.0 if synthesis_success else 0.0,
        }
        # Wait for store to be healthy
        for _ in range(40):
            if await store.async_healthcheck():
                break
            await asyncio.sleep(0.05)
        await store.async_record_sprint_delta(row)
        logger.info(
            f"[TEARDOWN] sprint_delta written: {new_findings} findings, "
            f"{dedup_hits} dedup hits, "
            f"UMA delta: {uma_peak_gib - uma_baseline_gib:+.2f}GiB, "
            f"top_source: {top_source!r}, "
            f"findings_per_min: {findings_per_min:.2f}"
        )
    except Exception as exc:
        logger.warning(f"[TEARDOWN] sprint_delta write failed: {exc}")


# =============================================================================
# Main sprint runner
# =============================================================================


async def run_sprint(
    query: str,
    duration_s: float = 1800.0,
    export_dir: str = str(Path.home() / ".hledac" / "reports"),
    aggressive_mode: bool = False,
    deep_probe_enabled: bool = False,
    ui_mode: bool = False,
    windup_lead_s: float | None = None,
    acquisition_profile: str | None = None,  # F223A: explicit profile override
) -> None:
    """
    Run a full sprint lifecycle with UMA monitoring and delta reporting.
    Uses SprintScheduler.run() directly to enable compute_sprint_intelligence() access.

    ROLE: CANONICAL SPRINT OWNER — SOLE production sprint authority.
    All report truth surfaces (canonical_run_summary, runtime_truth, timing_truth,
    checkpoint_zero_category, observed_run_tuple) are derived here.
    No alternate or residual path may claim canonical_sprint_owner = "core.__main__.run_sprint".
    """
    # Sprint 8SA: Phase timing instrumentation
    _phase_times: dict[str, float] = {}
    _phase_times["BOOT"] = time.monotonic()

    # M218A: GC tuning for M1 UMA stability — runs once per process
    _gc_telemetry = _configure_gc_for_sprint()

    # Pre-sprint checks
    run_pre_sprint_checks()

    # F214Q: Remote debug OPSEC guard — strict exit if HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1
    # and PYTHON_DISABLE_REMOTE_DEBUG is not set. Python 3.14 activates safe-external-debugger by default.
    if os.environ.get("HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED") == "1":
        if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") != "1":
            sys.exit(
                "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — "
                "OSINT runtime requires external debugger disabled"
            )

    # Sprint F174A: Canonical bootstrap guarantee — ensure non-empty matcher registry
    # before any pipeline run. Matches root __main__._run_sprint_mode() guarantee.
    from hledac.universal.patterns.pattern_matcher import configure_default_bootstrap_patterns_if_empty
    configure_default_bootstrap_patterns_if_empty()

    # F176A: Pre-sprint UMA state capture — hardware pressure before scheduler runs.
    # This is used to classify hardware-limited smoke vs depleted query.
    _uma_pre_sprint = sample_uma_status()
    _swap_detected_pre = _uma_pre_sprint.swap_detected
    _uma_state_pre = _uma_pre_sprint.state

    # UMA baseline
    uma_baseline_gib = _uma_pre_sprint.system_used_gib

    # Sprint ID
    sprint_id = _make_sprint_id()
    _phase_times["WARMUP"] = time.monotonic()

    # Initialize stores
    store = DuckDBShadowStore()
    await store.async_initialize()

    # Scheduler config
    # F221: windup_lead_s param (default 180s) + active-budget guard for 'default' profile
    _windup_lead_s = windup_lead_s if windup_lead_s is not None else 180.0
    # F228A: Defensive normalization — benchmark profile aliases must not reach
    # acquisition_strategy as raw values. Record all three phases for telemetry.
    _acq_input = acquisition_profile
    _acq_effective = acquisition_profile
    _acq_normalized = False
    if _acq_effective == "nonfeed_diagnostic180":
        _acq_effective = "nonfeed_diagnostic"
        _acq_normalized = True
    # Check _acq_effective (not _acq_input) since _acq_effective may have been
    # normalized by the alias check above — we only want to flag truly unknown values
    if _acq_effective not in ("default", "nonfeed_diagnostic"):
        if _acq_input is not None and _acq_input not in ("default", "nonfeed_diagnostic"):
            logger.warning(
                "[F228A] Unknown acquisition_profile=%r normalized to 'default'",
                _acq_input,
            )
        _acq_effective = "default"
        _acq_normalized = True
    # Guard: smoke180 profile uses windup_lead=180s — warn when active_budget <= 0
    # _acq_effective is "default" when input is None (normalized at line above)
    # nonfeed_diagnostic has windup_lead=0 so it always has an active window
    _active_budget = duration_s - _windup_lead_s
    if _active_budget <= 0 and _acq_effective == "default":
        logger.warning(
            "[F221] Sprint duration %.0fs <= windup_lead %.0fs → zero active budget. "
            "Use --duration %.0f+ or --profile nonfeed_diagnostic for an active window.",
            duration_s, _windup_lead_s, _windup_lead_s + 60,
        )
    # Propagate normalized value to scheduler and env for downstream seams
    if "HLEDAC_ACQUISITION_PROFILE" not in os.environ:
        os.environ["HLEDAC_ACQUISITION_PROFILE"] = _acq_effective or "default"
    acquisition_profile = _acq_effective or "default"
    config = SprintSchedulerConfig(
        sprint_duration_s=duration_s,
        windup_lead_s=_windup_lead_s,
        export_enabled=True,
        export_dir=export_dir,
        aggressive_mode=aggressive_mode,
        # Sprint F195B: 8s branch budget in aggressive mode
        branch_timeout_budget_s=8.0 if aggressive_mode else 0.0,
        # F223A: Explicit acquisition profile override
        acquisition_profile=acquisition_profile,
    )

    scheduler = SprintScheduler(config)

    # Sprint F153: Lifecycle receives explicit runtime params — duration authority propagated
    lifecycle = SprintLifecycleManager(
        sprint_duration_s=duration_s,
        windup_lead_s=config.windup_lead_s,
    )
    # Sprint F223K: Opt-in RL feedback loop — enables quality-weighted source selection
    policy_manager = SprintPolicyManager(
        enabled=os.environ.get("ENABLE_RL_FEEDBACK", "false").lower() == "true"
    )
    scheduler.inject_policy_manager(policy_manager)

    # Sprint F153: Canonical source inventory — real URLs from typed seed surface
    live_feed_urls = _get_live_feed_urls()

    # Sprint F193A: Instantiate CT log client for canonical pipeline
    _ct_log_client = None
    try:
        from pathlib import Path
        _ct_cache = Path(os.path.expanduser("~/.hledac/ct_cache"))
        _ct_cache.mkdir(parents=True, exist_ok=True)
        _ct_log_client = CTLogClient(cache_dir=_ct_cache)
    except Exception as e:
        logger.debug(f"CT log client initialization failed: {e}")

    try:
        # Sprint F195C: Sprint dashboard — created when ui_mode=True
        _dashboard: Any = None
        if ui_mode:
            try:
                from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
                _dashboard = SprintDashboard(sprint_id, query, duration_s)
                _dashboard.start()
            except Exception as e:
                logger.warning(f"Dashboard creation failed: {e}")  # fail-safe: dashboard must never block sprint

        # Sprint F195C: Progress callback for dashboard updates
        def _on_cycle(result: Any, phase: str, elapsed_s: float) -> None:
            if _dashboard is not None:
                try:
                    _dashboard.update(result, phase, elapsed_s)
                except Exception as e:
                    logger.debug(f"Dashboard update failed: {e}")

        # Run sprint via scheduler directly (enables compute_sprint_intelligence access)
        # now_monotonic=None: scheduler uses live time internally via adapter.tick()
        result = await scheduler.run(
            lifecycle=lifecycle,
            sources=live_feed_urls,
            now_monotonic=None,
            query=query,
            duckdb_store=store,
            ct_log_client=_ct_log_client,
            progress_callback=_on_cycle,
        )

        # Sprint F150H: Pull scheduler intelligence (fail-soft, additive)
        # correlation, hypothesis_pack, signal_path, feed_verdict,
        # public_verdict, branch_value, sprint_verdict
        try:
            intel = scheduler.compute_sprint_intelligence()
        except Exception as e:
            logger.debug(f"compute_sprint_intelligence failed: {e}")

        _phase_times["WINDUP"] = time.monotonic()

        # BOOT → WINDUP: when scheduler's should_enter_windup() fires.
        # This is the active window used (NOT full scheduler runtime —
        # scheduler runs duration_s internally but windup_lead_s offsets entry).
        # e.g. requested=300s, windup_lead_s=180 → time_to_windup_s ≈ 120s (correct).
        time_to_windup_s = _phase_times["WINDUP"] - _phase_times["BOOT"]

        # F166C: actual_duration is FULL BOOT→TEARDOWN wall-clock (not time_to_windup_s).
        # time_to_windup_s was a misleading alias — it conflated pre-scheduler boot cost
        # with active window. Actual runtime for metrics/thresholds must be full wall-clock.
        # F167B fix: _phase_times["TEARDOWN"] is a timestamp; use it directly as timestamp.
        # When TEARDOWN not yet recorded (early exit), fall back to BOOT→WINDUP which IS
        # a duration stored in time_to_windup_s (not a timestamp). Guard with _phase_times["BOOT"]
        # so the arithmetic is always timestamp - timestamp = duration.
        _teardown_ts = _phase_times.get("TEARDOWN")
        actual_duration = (_teardown_ts - _phase_times["BOOT"]) if _teardown_ts is not None else time_to_windup_s

        # F166C: Pre-scheduler boot time (BOOT→WARMUP).
        # Captures import, store init, lifecycle creation overhead.
        pre_scheduler_boot_s = _phase_times.get("WARMUP", 0) - _phase_times["BOOT"]

        # F166C: Scheduler wall time (WARMUP→WINDUP).
        # Full scheduler elapsed from instantiation to windup entry.
        # If ACTIVE was reached, ACTIVE→WINDUP is part of this window (scheduled cycles ran).
        _windup_mark = _phase_times.get("WINDUP", _phase_times.get("TEARDOWN", _phase_times["BOOT"]))
        scheduler_wall_s = _windup_mark - _phase_times.get("WARMUP", _phase_times["BOOT"])

        # F166C: Pre-ACTIVE starvation — scheduler already computes this;
        # __main__ re-derives for timing_truth only (not stored back to result).
        # Uses result.entered_active_at_monotonic (set by scheduler at loop guard)
        # and result.first_cycle_started_at_monotonic (set at first cycles_started += 1).

        # UMA peak
        uma_peak_gib = sample_uma_status().system_used_gib

        # Sprint F193A+F194A: CT log canonical discovery — runs once after main cycle loop.
        # In aggressive mode, CT runs in-cycle via _run_one_cycle_aggressive, so skip post-loop.
        # Sprint F194A: Persisted CT findings are additive to feed/public accepted_findings
        # in canonical sprint truth. They flow into write_sprint_delta, runtime_truth,
        # report_dict, canonical_run_summary, and export handoff.
        if not scheduler._config.aggressive_mode:
            await scheduler._run_ct_log_discovery_in_cycle(query=query, store=store)
            result.accepted_findings += result.ct_log_stored

        # Write sprint delta
        await write_sprint_delta(
            store=store,
            sprint_id=sprint_id,
            query=query,
            new_findings=result.accepted_findings,
            dedup_hits=result.duplicate_entry_hashes_skipped,
            ioc_nodes=result.unique_entry_hashes_seen,
            uma_baseline_gib=uma_baseline_gib,
            uma_peak_gib=uma_peak_gib,
            synthesis_success=result.accepted_findings > 0,
            duration_s=actual_duration,
            hits_per_source=result.hits_per_source,
        )

        _phase_times["TEARDOWN"] = time.monotonic()

        # Sprint 8SA: Phase timing profile — uses _PHASE_ORDER from sprint_lifecycle
        phases = _PHASE_ORDER
        for i, ph in enumerate(phases):
            if ph in _phase_times:
                next_ph = phases[i + 1] if i + 1 < len(phases) else "END"
                if next_ph in _phase_times:
                    elapsed = _phase_times[next_ph] - _phase_times[ph]
                    logger.info(f"[{sprint_id}] {ph}→{next_ph}: {elapsed:.1f}s")

        # --- Timing truth (Sprint F160E) -------------------------------------------
        # Canonical surfaces that distinguish:
        #   requested_duration  — what operator asked for
        #   windup_lead_s       — T-minus offset that triggers wind-down
        #   time_to_windup_s    — BOOT→WINDUP, the active window actually used
        #   time_to_teardown_s  — BOOT→TEARDOWN, full wall-clock of this run
        #   active_window_budget_s — theoretical active window (requested - windup_lead)
        #   windup_lead_observed_s — actual time between WINDUP entry and TEARDOWN
        _teardown_time = _phase_times.get("TEARDOWN", _phase_times.get("WINDUP", 0))
        windup_lead_observed_s = _teardown_time - _phase_times.get("WINDUP", 0)
        timing_truth = {
            "requested_duration_s": duration_s,
            "windup_lead_s": config.windup_lead_s,
            "time_to_windup_s": round(time_to_windup_s, 2),
            "time_to_teardown_s": round(_teardown_time - _phase_times["BOOT"], 2),
            "active_window_budget_s": round(duration_s - config.windup_lead_s, 2),
            "windup_lead_observed_s": round(windup_lead_observed_s, 2),
            # F166C: Pre-scheduler boot cost (import, store init, lifecycle creation)
            "pre_scheduler_boot_s": round(pre_scheduler_boot_s, 2),
            # F166C: Scheduler wall time (WARMUP→WINDUP, full scheduler elapsed)
            "scheduler_wall_s": round(scheduler_wall_s, 2),
            # F169F: scheduler_returned_phase — derive from result state, not dict inspection
            # F167B fix: use result.entered_active_at_monotonic, NOT _phase_times["ACTIVE"]
            # (which is never set — only BOOT/WARMUP/WINDUP/TEARDOWN are written)
            "scheduler_returned_phase": (
                "ACTIVE"
                if result.entered_active_at_monotonic is not None
                else "entry_only"
            ),
            # F167B fix: use result fields (first cycle STARTED not cycles_completed)
            "entered_active_truth": result.entered_active_at_monotonic is not None,
            "first_cycle_truth": result.first_cycle_started_at_monotonic is not None,
            # F166C: Pre-ACTIVE starvation — scheduler computes pre_active_starved and
            # pre_loop_blocker_reason; use directly from result (not re-derived locally).
            "pre_active_starvation": result.pre_active_starved,
            "pre_active_blocker": result.pre_loop_blocker_reason or None,
            # F166C: Full budget view for canonical runtime consumption
            "canonical_runtime_budget_view": {
                "pre_boot_s": round(pre_scheduler_boot_s, 2),
                "scheduler_elapsed_s": round(scheduler_wall_s, 2),
                "total_wallclock_s": round(actual_duration, 2),
                "budget_consumed_pct": round((actual_duration / duration_s) * 100, 1) if duration_s > 0 else 0.0,
            },
        }

        # --- Derived metrics --------------------------------------------------------
        findings_per_min = (result.accepted_findings / (actual_duration / 60.0)) if actual_duration > 0 else 0.0
        total_seen = result.unique_entry_hashes_seen + result.duplicate_entry_hashes_skipped
        dup_rate = (result.duplicate_entry_hashes_skipped / total_seen * 100) if total_seen > 0 else 0.0
        feed_fnd = result.accepted_findings - result.public_accepted_findings
        public_pct = (result.public_accepted_findings / result.accepted_findings * 100) if result.accepted_findings > 0 else 0.0

        # F169F: Use scheduler result fields directly — no local duplication.
        # Scheduler SprintSchedulerResult.public_backend_degraded is pre-computed.
        # DF-1: _public_backend_degraded, _feed_zero, _cross_branch_fail now
        # computed ONCE in _ckpt_category section below; verdict uses inline checks.
        _public_backend_degraded = result.public_backend_degraded

        # Source mix
        src_mix: list[str] = []
        for src, cnt in sorted(result.hits_per_source.items(), key=lambda x: x[1], reverse=True):
            src_mix.append(f"{src}={cnt}")
        src_mix_str = ", ".join(src_mix) if src_mix else "none"

        # Verdict heuristics — F176A+F169F: hardware-limited smoke is distinct from depleted query.
        # _is_hardware_limited computed once below; verdict uses same condition inline.
        _inline_hardware_limited = (
            result.accepted_findings == 0
            and result.total_pattern_hits == 0
            and result.cycles_started == 0
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency"))
        )
        if result.aborted:
            # F178B: Aborted without findings = hard abort. Aborted WITH findings = partial signal.
            # Both share the abort modifier but the base verdict reflects signal state.
            if result.accepted_findings > 0:
                _base_verdict = (
                    "📦  NOISE-HEAVY: duplicated heavily"
                    if dup_rate > 85
                    else "🌐  PUBLIC-LED: public discovery dominated"
                    if public_pct > 60
                    else "⚖️  MIXED: public contributed meaningfully"
                    if public_pct > 25
                    else "✅  FEED-LED: feed sources strong"
                    if feed_fnd > 0
                    else "✅  SIGNAL: good feed performance"
                )
                verdict = f"⚠️  ABORTED (partial) — {_base_verdict}"
            else:
                verdict = "⚠️  ABORTED: hard stop, no signal collected"
        elif _inline_hardware_limited:
            verdict = "💾  HARDWARE-LIMITED: swap/memory pressure blocked entry"
        elif _public_backend_degraded:
            verdict = "🌐  DEGRADED: public backend/network error — check TOR/proxy/config"
        elif result.accepted_findings == 0:
            if result.public_discovered > 0:
                verdict = "🔍  NOVELTY: public found hits, feed accepted nothing"
            elif result.total_pattern_hits == 0:
                verdict = "🗿  DEPLETED: no pattern hits anywhere"
            else:
                verdict = "🤷  SILENT: pattern hits but no accepted findings"
        elif dup_rate > 85:
            verdict = "📦  NOISE-HEAVY: duplicated heavily"
        elif public_pct > 60:
            verdict = "🌐  PUBLIC-LED: public discovery dominated"
        elif public_pct > 25:
            verdict = "⚖️  MIXED: public contributed meaningfully"
        elif feed_fnd > 0:
            verdict = "✅  FEED-LED: feed sources strong"
        else:
            verdict = "✅  SIGNAL: good feed performance"

        # Next-step hint (heuristic, no new planner)
        next_hint: str
        if _inline_hardware_limited:
            next_hint = "hardware memory pressure — free RAM or restart before next run"
        elif result.accepted_findings == 0 and result.total_pattern_hits == 0:
            next_hint = "query may be too narrow — broaden terms or switch seed"
        elif dup_rate > 80:
            next_hint = "high dup rate — consider narrowing query scope"
        elif public_pct > 60:
            next_hint = "public discovery effective — let it run longer next time"
        elif public_pct < 10 and feed_fnd == 0:
            next_hint = "feed yield low — check if sources still alive (urlhaus, threatfox)"
        elif public_pct < 10 and feed_fnd > 0:
            next_hint = "feed performing — rely on feed-first, use public as supplemental"
        elif result.public_discovered > 0 and result.public_fetched == 0:
            next_hint = "public discovered but not fetched — check network/TOR"
        elif result.stop_requested:
            next_hint = "early stop triggered — lower threshold or widen query"
        else:
            next_hint = "current query and source mix working — continue as-is"

        # --- Runtime truth (smoke vs meaningful) ---------------------------------
        # [F207L] Compute CT findings: legacy ct_log_stored + new acquisition lane CT findings
        # The new acquisition lane (crtsh_adapter) is the canonical nonfeed CT path.
        # lane_ct_accepted_findings tracks new-lane CT; ct_log_stored tracks legacy CT pipeline.
        # Both paths can run in the same sprint — sum them for total CT signal.
        _lane_ct = getattr(result, "lane_ct_accepted_findings", 0) or 0
        _legacy_ct = getattr(result, "ct_log_stored", 0) or 0
        _total_ct = _lane_ct + _legacy_ct

        runtime_truth = _runtime_truth(
            actual_duration_s=actual_duration,
            query=query,
            duration_s=duration_s,
            cycles_completed=result.cycles_completed,
            cycles_started=result.cycles_started,
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_accepted_findings=result.public_accepted_findings,
            feed_findings=feed_fnd,
            # Sprint F194A: CT findings additive to canonical truth accounting
            # [F207L] Sum legacy ct_log_stored + lane_ct_accepted_findings from new acquisition lanes
            ct_findings=_total_ct,
            # F176A: Hardware pressure surfaces for smoke classification
            swap_detected=_swap_detected_pre,
            uma_state=_uma_state_pre,
            # Sprint F195B: Branch timeout telemetry
            branch_timeout_count=result.branch_timeout_count,
            public_branch_timed_out=result.public_branch_timed_out,
            ct_branch_timed_out=result.ct_branch_timed_out,
        )
        is_meaningful = runtime_truth["is_meaningful"]
        evidence_note = runtime_truth["evidence_note"]

        # F164D: explicit active-runtime occurred flag — guards against
        # "windup only, no active window" drift in report layer.
        # time_to_windup_s > 0 alone is insufficient (windupLead fires immediately
        # on entry-only runs); requires is_meaningful too.
        timing_truth["active_runtime_occurred"] = is_meaningful and time_to_windup_s > 0

        # Clear separation: [SMOKE] vs [ACTIVE]
        if is_meaningful:
            logger.info(
                f"[RUNTIME TRUTH] ✅ MEANINGFUL ACTIVE RUN | {evidence_note} | "
                f"primary: {runtime_truth['primary_signal_source']} | "
                f"cycles: {result.cycles_completed}/{result.cycles_started} | "
                f"windup: {time_to_windup_s:.0f}s (budget={timing_truth['active_window_budget_s']:.0f}s)"
            )
        else:
            logger.warning(
                f"[RUNTIME TRUTH] 🚨 SMOKE ONLY | {evidence_note} | "
                f"cycles: {result.cycles_completed}/{result.cycles_started} | "
                f"windup: {time_to_windup_s:.0f}s (budget={timing_truth['active_window_budget_s']:.0f}s)"
            )

        logger.info(
            f"[SPRINT DONE] {sprint_id} | "
            f"findings: {result.accepted_findings} | "
            f"cycles: {result.cycles_completed}/{result.cycles_started} | "
            f"duplicates: {result.duplicate_entry_hashes_skipped} | "
            f"phase: {result.final_phase}"
        )
        logger.info(
            f"[SUMMARY] {verdict} | "
            f"feed={feed_fnd} public={result.public_accepted_findings}({public_pct:.0f}%) | "
            f"f/min={findings_per_min:.2f} | dup={dup_rate:.1f}% | "
            f"public: disc={result.public_discovered} fetch={result.public_fetched} "
            f"match={result.public_matched_patterns} stored={result.public_stored_findings}"
        )
        logger.info(f"[NEXT] {next_hint}")
        logger.info(f"[SOURCES] {src_mix_str}")

        # Sprint F150H: Log scheduler intelligence (visible operator signal)
        sv = intel.get("sprint_verdict") or {}
        sp = intel.get("signal_path") or {}
        corr = intel.get("correlation") or {}
        hyp = intel.get("hypothesis_pack") or {}
        if sv:
            logger.info(
                f"[INTEL] posture={sv.get('posture','?')} | "
                f"dominant={sv.get('dominant_signal','?')} | "
                f"corroborated={sp.get('is_corroborated',False)} | "
                f"noisy={sp.get('is_noisy',False)} | "
                f"risk={corr.get('risk_score',0):.3f} | "
                f"hypotheses={hyp.get('hypothesis_count',0)} | "
                f"next={sv.get('first_action','?')[:60]}"
            )

        # Sprint F500I: Use canonical path helper (no more ad-hoc /tmp)
        report_path = get_sprint_json_report_path(sprint_id)

        # CHECKPOINT-0 additive derived fields (computed before report_dict)
        active_iterations = result.cycles_completed

        # F176A: Hardware-limited smoke detection (MUST be before runtime_truth_level)
        _is_hardware_limited = (
            not is_meaningful
            and result.cycles_started == 0
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency"))
        )
        # F176A: Pre-active memory starvation
        _is_pre_active_mem_starved = (
            not is_meaningful
            and result.cycles_started == 0
            and result.entered_active_at_monotonic is not None
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency", "warn"))
        )

        # F176A+E0-T4: runtime truth level taxonomy
        # F176A adds: hardware_limited_smoke, pre_active_memory_starvation, survival_active_minimal
        # E0-T4: short_signal — <180s with pattern hits but no findings.
        # 180s floor in _is_meaningful_run is exempt for hits/findings early-returns.
        # F178B: Priority order — more specific conditions must come BEFORE less specific.
        # pre_active_memory_starvation (entered ACTIVE but zero cycles with memory pressure)
        # MUST be checked before survival_active_minimal (bounded work with memory pressure).
        # hardware_limited_smoke (never entered active, zero cycles, memory pressure) comes after
        # pre_active_memory_starvation since the latter requires entered_active_at_monotonic.
        runtime_truth_level = (
            "active"
            if is_meaningful and result.accepted_findings > 0
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            else "survival_active_minimal"
            if is_meaningful and _uma_state_pre in ("warn", "critical", "emergency")
            else "hardware_limited_smoke"
            if _is_hardware_limited
            else "short_signal"
            if is_meaningful and result.total_pattern_hits > 0
            else "meaningful_empty"
            if is_meaningful
            else "smoke"
        )

        # Sprint F162D: observed_run_tuple must be deterministic — no verdict string
        # (verdict is heuristic and non-reproducible across identical runs).
        # Canonical components: query-truncated, duration, iterations, source-mix, truth-level.
        observed_run_tuple = (
            query[:40] if len(query) > 40 else query,
            round(actual_duration, 1),
            active_iterations,
            src_mix_str,
            runtime_truth_level,
        )

        # CHECKPOINT-0 taxonomy (Sprint F155 + E0-T4 + F163C + F164D + F169F + F189A)
        # Disjoint machine-readable buckets — report layer must not conflate these.
        # Bucket set:
        #   signal_reaches_findings           — findings accepted
        #   pre_active_memory_starvation      — F176A: entered ACTIVE but zero cycles with memory pressure
        #   survival_active_minimal           — F176A: bounded ACTIVE work under memory pressure
        #   hardware_limited_smoke           — F176A: zero cycles + swap/pressure (hardware, not query failure)
        #   public_backend_degraded           — F169F: public branch backend error (NetworkProxyError, HTTP errors)
        #   degraded_public_blocker           — public branch error (legacy, non-backend errors)
        #   meaningful_empty_run              — F169F+F189A: meaningful query, zero pattern hits, no findings
        #   feed_ingress_blocker              — F169F: feed zero AND public discovered some signal
        #   feed_source_inaccessible          — F169F: feed failed AND total hits=0 AND no infra error
        #   true_depleted_query               — F169F: query vocabulary matched but nothing accepted
        #   short_signal                      — F189A: meaningful query with hits but no accepted findings
        #   cross_branch_source_inaccessible  — F169F: cross-branch sources failed, feed/public accessible
        #   windup_export_fail_soft           — windup fired on zero-findings run
        # Priority: findings > survival > hardware_limited > pre_active_mem > public_backend >
        #   degraded > meaningful_empty > feed_ingress > feed_source_inaccessible >
        #   true_depleted > short_signal > cross_branch > windup > depleted
        # NOTE (F189A): meaningful_empty_run moved BEFORE _feed_zero guards because it requires
        #   is_meaningful=True (query had runtime/hits evidence) while feed_source_inaccessible
        #   describes a feed infrastructure failure. short_signal moved BEFORE true_depleted_query
        #   because it requires is_meaningful=True (distinct from true_depleted_query's zero-findings
        #   verdict which doesn't distinguish meaningful vs non-meaningful query execution).
        # F192A DF-1/2/3: Use scheduler result fields directly — eliminated duplicate
        # _public_backend_degraded, _feed_zero, _cross_branch_fail local computations.
        _public_backend = result.public_backend_degraded
        _feed_zero_check = result.accepted_findings == 0 and feed_fnd == 0
        _cross_branch_fail_check = (
            result.accepted_findings == 0
            and result.total_pattern_hits > 0
            and not _public_backend
            and not result.public_error
        )
        _ckpt_category = (
            "signal_reaches_findings"
            if result.accepted_findings > 0
            # F176A: Pre-active memory starvation — entered ACTIVE but zero cycles started
            # under memory pressure. MUST come before survival_active_minimal.
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            # F176A: Survival minimal active — bounded work under memory pressure
            else "survival_active_minimal"
            if is_meaningful and _uma_state_pre in ("warn", "critical", "emergency")
            # F176A: Hardware-limited smoke — zero cycles, hardware pressure
            else "hardware_limited_smoke"
            if _is_hardware_limited
            # F169F: explicit backend degraded first (httpx/network errors)
            else "public_backend_degraded"
            if _public_backend
            # F169F: degraded_public_blocker (non-backend public errors)
            else "degraded_public_blocker"
            if result.public_error
            # F189A: meaningful_empty_run BEFORE _feed_zero_check guards — meaningful query with zero hits
            # is a distinct bucket from feed_source_inaccessible (feed infrastructure failure).
            else "meaningful_empty_run"
            if is_meaningful and result.total_pattern_hits == 0 and result.accepted_findings == 0
            # F169F: feed_ingress_blocker — feed zero but public found signal
            else "feed_ingress_blocker"
            if _feed_zero_check and result.public_discovered > 0
            # F169F: feed source inaccessible — feed failed AND total hits=0 AND no infra error
            else "feed_source_inaccessible"
            if _feed_zero_check and result.total_pattern_hits == 0 and not result.public_error
            # F189A: short_signal BEFORE true_depleted_query — short_signal requires is_meaningful=True
            # (query had real runtime/hits evidence) while true_depleted_query is broader.
            else "short_signal"
            if is_meaningful and result.total_pattern_hits > 0 and result.accepted_findings == 0
            # F169F: true depleted query — hits seen but pattern matched nothing accepted
            else "true_depleted_query"
            if result.accepted_findings == 0 and result.total_pattern_hits > 0 and not _public_backend
            # F169F: cross-branch source inaccessible — hits seen but blocked by source-level failure
            else "cross_branch_source_inaccessible"
            if _cross_branch_fail_check
            else "windup_export_fail_soft"
            if result.accepted_findings == 0 and _phase_times.get("WINDUP", 0) > 0 and is_meaningful
            else "depleted"
        )
        # F176A+F169F+F190A reason chain — machine-readable, mutually exclusive.
        # F190A: chain order aligned with _ckpt_category (F189A fixes propagated to reason chain):
        #   1. meaningful_empty_run BEFORE feed_ingress_blocker/feed_source_inaccessible
        #   2. short_signal_no_findings BEFORE true_depleted_query:hits_without_acceptance
        _checkpoint_zero_reason = (
            # F176A: Hardware-limited smoke — evidence_note already has hardware_limited_smoke text
            evidence_note
            if _is_hardware_limited
            # F176A: Pre-active memory starvation
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            else evidence_note
            if not is_meaningful
            else "signal_reaches_findings"
            if result.accepted_findings > 0
            # F169F: backend degraded — httpx/network errors
            else f"public_backend_degraded:{result.public_error}"
            if _public_backend
            else f"degraded_public_branch_blocked:{result.public_error}"
            if result.public_error
            # F190A: meaningful_empty_run BEFORE feed guards (aligns with _ckpt_category F189A order)
            else "meaningful_empty_run"
            if is_meaningful and result.total_pattern_hits == 0 and result.accepted_findings == 0
            # F169F: feed_ingress_blocker (meaningful=False, public found signal)
            else f"feed_ingress_blocker:{result.public_discovered}"
            if result.accepted_findings == 0 and feed_fnd == 0 and result.public_discovered > 0
            # F169F: feed source inaccessible
            else "feed_source_inaccessible"
            if result.accepted_findings == 0 and result.total_pattern_hits == 0 and not result.public_error
            # F190A: short_signal_no_findings BEFORE true_depleted_query (aligns with _ckpt_category F189A order)
            else "short_signal_no_findings"
            if is_meaningful and result.total_pattern_hits > 0
            # F169F: true depleted query — hits seen but nothing accepted, no infra error
            else "true_depleted_query:hits_without_acceptance"
            if result.accepted_findings == 0 and result.total_pattern_hits > 0 and not _public_backend
            else "cross_branch_source_inaccessible"
            if _cross_branch_fail_check
            else "depleted_no_pattern_hits"
        )
        _export_finish_status = (
            "finished"
            if result.final_phase in ("EXPORT", "TEARDOWN") and result.accepted_findings > 0 and not result.aborted
            else "aborted" if result.aborted
            else "empty_run" if result.accepted_findings == 0
            else "unknown"
        )

        report_dict = {
            "sprint_id": sprint_id,
            "query": query,
            "duration_s": duration_s,
            "actual_duration_s": actual_duration,
            "accepted_findings": result.accepted_findings,
            "feed_findings": feed_fnd,
            "public_accepted_findings": result.public_accepted_findings,
            "public_discovered": result.public_discovered,
            "public_fetched": result.public_fetched,
            "public_matched_patterns": result.public_matched_patterns,
            "public_stored_findings": result.public_stored_findings,
            "public_error": result.public_error,
            # Sprint F193A+F194A: CT log canonical discovery — additive to sprint truth
            "ct_log_discovered": result.ct_log_discovered,
            "ct_log_stored": result.ct_log_stored,
            "ct_log_accepted_findings": result.ct_log_accepted_findings,
            "ct_log_error": result.ct_log_error,
            "cycles_completed": result.cycles_completed,
            "cycles_started": result.cycles_started,
            "unique_entry_hashes_seen": result.unique_entry_hashes_seen,
            "duplicate_entry_hashes_skipped": result.duplicate_entry_hashes_skipped,
            "total_pattern_hits": result.total_pattern_hits,
            "dup_rate_pct": round(dup_rate, 2),
            "findings_per_min": round(findings_per_min, 2),
            "final_phase": result.final_phase,
            "aborted": result.aborted,
            "abort_reason": result.abort_reason,
            "stop_requested": result.stop_requested,
            "entries_per_source": result.entries_per_source,
            "hits_per_source": result.hits_per_source,
            "export_paths": result.export_paths,
            "uma_peak_gib": uma_peak_gib - uma_baseline_gib,
            "synthesis_success": result.accepted_findings > 0,
            "verdict": verdict,
            "next_hint": next_hint,
            "phase_timing": {
                ph: round(_phase_times.get(ph, 0) - _phase_times.get("BOOT", 0), 2)
                for ph in phases if ph in _phase_times
            },
            "runtime_truth": runtime_truth,
            # Sprint F150H: Scheduler intelligence propagated fail-soft (additive)
            "correlation_summary": intel.get("correlation"),
            "hypothesis_pack_summary": intel.get("hypothesis_pack"),
            "signal_path": intel.get("signal_path"),
            "feed_verdict": intel.get("feed_verdict"),
            "public_verdict": intel.get("public_verdict"),
            "branch_value": intel.get("branch_value"),
            "sprint_verdict": intel.get("sprint_verdict"),
            # Sprint F500I: Empirical run boundary — reproducible tuple
            "execution_context": {
                "query": query,
                "requested_duration_s": duration_s,
                "actual_duration_s": round(actual_duration, 2),
                "source_count": len(live_feed_urls),
                "sources": live_feed_urls,
                "platform": {
                    "python_version": __import__("sys").version.split()[0],
                    "macos_version": __import__("platform").mac_ver()[0] or "unknown",
                },
                "report_path": str(report_path),
                "git_snapshot": "unknown",
                "export_dir": export_dir,
            },
            # Sprint F150H+F206S: Canonical operator summary — built ONCE, used in both
            # report_dict and handoff. Acquisition payload spread additively on top so
            # any canonical_run_summary fields also in _acq_payload are overwritten
            # with acquisition truth (correct: acquisition fields should take precedence).
            "canonical_run_summary": {

                "meaningful": runtime_truth["is_meaningful"],
                "primary_signal": runtime_truth["primary_signal_source"],
                "posture": (intel.get("sprint_verdict") or {}).get("posture", "unknown"),
                "dominant_signal_path": (intel.get("signal_path") or {}).get("dominant_signal_path", "unknown"),
                "corroborated": (intel.get("signal_path") or {}).get("is_corroborated", False),
                "is_noisy": (intel.get("signal_path") or {}).get("is_noisy", False),
                "next_pivot": (intel.get("signal_path") or {}).get("next_pivot_recommendation", "unknown"),
                "branch_verdict": (intel.get("branch_value") or {}).get("branch_verdict", "unknown"),
                "risk_score": (intel.get("correlation") or {}).get("risk_score", 0.0),
                "hypothesis_count": (intel.get("hypothesis_pack") or {}).get("hypothesis_count", 0),
                "first_action": (intel.get("sprint_verdict") or {}).get("first_action", ""),
                "confidence": (intel.get("sprint_verdict") or {}).get("confidence", ""),
                "runtime_truth_level": runtime_truth_level,
                "checkpoint_zero_category": _ckpt_category,
                "checkpoint_zero_reason": _checkpoint_zero_reason,
                "observed_run_tuple": observed_run_tuple,
                "canonical_sprint_owner": "core.__main__.run_sprint",
                "canonical_path_used": "run_sprint",
                "effective_source_mix": src_mix_str,
                "effective_parallelism": len(live_feed_urls),
                "effective_timeouts": {},
                "active_iteration_count": active_iterations,
                "pre_loop_elapsed_s": result.pre_loop_elapsed_s,
                "pre_loop_blocker_reason": result.pre_loop_blocker_reason,
                "pre_active_starvation": result.pre_active_starved,
                "export_finish_layer_status": _export_finish_status,
                "public_error": result.public_error,
                "ct_log_discovered": result.ct_log_discovered,
                "ct_log_stored": result.ct_log_stored,
                "ct_log_accepted_findings": result.ct_log_accepted_findings,
                "cc_archive_injected": result.cc_archive_injected,
                "academic_findings_count": result.academic_findings_count,
                "timing_truth": timing_truth,
                # Sprint F215D: Early exit semantics
                "early_exit_class": getattr(result, "early_exit_class", ""),
                "early_exit_reason": getattr(result, "early_exit_reason", ""),
                "requested_duration_s": getattr(result, "requested_duration_s", 0.0),
                "actual_duration_s": getattr(result, "actual_duration_s", 0.0),
                "elapsed_pct": getattr(result, "elapsed_pct", 0.0),
                "active_window_budget_s": getattr(result, "active_window_budget_s", 0.0),
                "active_window_elapsed_s": getattr(result, "active_window_elapsed_s", 0.0),
            },
            # [F208I-A] Acquisition terminality and report truth — pure, fail-soft.
            # Spread on top of canonical_run_summary so acquisition fields take precedence.
            **_scheduler_result_acquisition_payload(result, scheduler, query, duration_s),
            "runtime_truth": runtime_truth,
            "timing_truth": timing_truth,
            # Sprint M218A: GC startup tuning telemetry
            "gc_telemetry": _gc_telemetry,
            # Sprint F217B: Nonfeed mission controller telemetry
            "nonfeed_mission_active": getattr(result, "nonfeed_mission_active", False),
            "nonfeed_required_families": getattr(result, "nonfeed_required_families", ()),
            "nonfeed_optional_families": getattr(result, "nonfeed_optional_families", ()),
            "nonfeed_family_status": getattr(result, "nonfeed_family_status", {}),
            "nonfeed_all_required_terminal": getattr(result, "nonfeed_all_required_terminal", False),
            "nonfeed_any_accepted": getattr(result, "nonfeed_any_accepted", False),
            "nonfeed_provider_failures": getattr(result, "nonfeed_provider_failures", ()),
            "nonfeed_memory_skips": getattr(result, "nonfeed_memory_skips", ()),
            "nonfeed_mission_exit_reason": getattr(result, "nonfeed_mission_exit_reason", ""),
        }
        report_path.write_bytes(orjson.dumps(report_dict, option=orjson.OPT_INDENT_2))
        logger.info(f"[REPORT] {report_path}")

        # Sprint F151D: Wire existing exporter seam over already-computed truth surfaces.
        # Reuse: ExportHandoff, ensure_export_handoff, store.get_top_seed_nodes(),
        # intel (correlation/hypothesis_pack/signal_path/feed_verdict/
        # public_verdict/branch_value/sprint_verdict), runtime_truth, canonical_run_summary.
        # Additive + fail-soft only — exporter failure does not crash sprint.
        try:
            from hledac.universal.project_types import ExportHandoff

            top_seed_nodes: list = []
            try:
                top_seed_nodes = store.get_top_seed_nodes(n=5) if store else []
            except Exception as e:
                logger.debug(f"get_top_seed_nodes failed: {e}")

            # Sprint F155: Determine handoff enrichment level (canonical_run_summary built inline)
            _handoff_enriched = bool(runtime_truth and intel)

            # [F208J-B] Compute acquisition payload BEFORE ExportHandoff construction
            # so it can be spread into both scorecard and canonical_run_summary.
            # This ensures acquisition truth enters the actual ExportHandoff passed to export_sprint(),
            # not just the local report_dict that was written to disk.
            _acq_payload = _scheduler_result_acquisition_payload(result, scheduler, query, duration_s)

            handoff = ExportHandoff(
                sprint_id=sprint_id,
                scorecard={
                    "synthesis_engine_used": "hermes3",
                    "gnn_predicted_links": 0,
                    "top_graph_nodes": top_seed_nodes,
                    "phase_duration_seconds": {
                        ph: round(_phase_times.get(ph, 0) - _phase_times.get("BOOT", 0), 2)
                        for ph in phases if ph in _phase_times
                    },
                    # [F223D] runtime_accepted_findings — full truth from all lanes at windup time.
                    # This is the authoritative runtime total and is used by product_value_summary
                    # to populate runtime_accepted_findings. The normalized accepted_findings
                    # in source_family_outcomes reflects per-lane breakdown; this field provides
                    # the ground-truth total so PVS is never contradictory with runtime_truth.
                    "runtime_accepted_findings": result.accepted_findings,
                    # F220F: findings_per_minute — computed from all-lanes total / active window.
                    # PVS uses scorecard.findings_per_minute directly; adding here ensures PVS
                    # never shows 0.0 for a productive sprint where phase_timings.WINDUP is 0.0.
                    "findings_per_minute": round(result.accepted_findings / (actual_duration / 60.0), 2)
                    if actual_duration > 0 else 0.0,
                    # Sprint F202B: Identity stitching sidecar counters
                    "identity_candidates_found": result.identity_candidates_found,
                    "identity_findings_produced": result.identity_findings_produced,
                    # [F208J-B] Canonical acquisition terminality and report truth
                    **_acq_payload,
                },
                top_nodes=top_seed_nodes,
                phase_durations={
                    ph: round(_phase_times.get(ph, 0) - _phase_times.get("BOOT", 0), 2)
                    for ph in phases if ph in _phase_times
                },
                # Sprint F155: Canonical truth enrichment — additive, derived-only
                runtime_truth=runtime_truth,
                execution_context={
                    "query": query,
                    "requested_duration_s": duration_s,
                    "actual_duration_s": round(actual_duration, 2),
                    "source_count": len(live_feed_urls),
                    "sources": live_feed_urls,
                    "platform": {
                        "python_version": __import__("sys").version.split()[0],
                        "macos_version": __import__("platform").mac_ver()[0] or "unknown",
                    },
                    "report_path": str(report_path),
                    "git_snapshot": "unknown",
                    "export_dir": export_dir,
                },
                # Sprint F155: canonical_run_summary inline (already computed in report_dict)
                canonical_run_summary={
                    "meaningful": runtime_truth["is_meaningful"],
                    "primary_signal": runtime_truth["primary_signal_source"],
                    "posture": (intel.get("sprint_verdict") or {}).get("posture", "unknown"),
                    "dominant_signal_path": (intel.get("signal_path") or {}).get("dominant_signal_path", "unknown"),
                    "corroborated": (intel.get("signal_path") or {}).get("is_corroborated", False),
                    "is_noisy": (intel.get("signal_path") or {}).get("is_noisy", False),
                    "next_pivot": (intel.get("signal_path") or {}).get("next_pivot_recommendation", "unknown"),
                    "branch_verdict": (intel.get("branch_value") or {}).get("branch_verdict", "unknown"),
                    "risk_score": (intel.get("correlation") or {}).get("risk_score", 0.0),
                    "hypothesis_count": (intel.get("hypothesis_pack") or {}).get("hypothesis_count", 0),
                    "first_action": (intel.get("sprint_verdict") or {}).get("first_action", ""),
                    "confidence": (intel.get("sprint_verdict") or {}).get("confidence", ""),
                    "runtime_truth_level": runtime_truth_level,
                    "checkpoint_zero_category": _ckpt_category,
                    "checkpoint_zero_reason": _checkpoint_zero_reason,
                    "observed_run_tuple": observed_run_tuple,
                    "canonical_sprint_owner": "core.__main__.run_sprint",
                    "canonical_path_used": "run_sprint",
                    "effective_source_mix": src_mix_str,
                    "effective_parallelism": len(live_feed_urls),
                    "effective_timeouts": {},
                    "active_iteration_count": active_iterations,
                    # F166B+F178B: Pre-loop and pre-active starvation surfaces
                    "pre_loop_elapsed_s": result.pre_loop_elapsed_s,
                    "pre_loop_blocker_reason": result.pre_loop_blocker_reason,
                    "pre_active_starvation": result.pre_active_starved,
                    "export_finish_layer_status": _export_finish_status,
                    # Sprint F163C: public_error must surface at canonical boundary
                    "public_error": result.public_error,
                    # Sprint F194A: CT log canonical findings — additive to sprint truth
                    "ct_log_discovered": result.ct_log_discovered,
                    "ct_log_stored": result.ct_log_stored,
                    "ct_log_accepted_findings": result.ct_log_accepted_findings,
                    # Sprint F160E: Canonical timing truth — separates active window from full run
                    "timing_truth": timing_truth,
                    # [F208J-B] Canonical acquisition terminality and report truth — same payload
                    # as what entered scorecard above, ensuring export_sprint() receives consistent
                    # acquisition truth via both the scorecard and canonical_run_summary seams.
                    **_acq_payload,
                },
                synthesis_outcome_payload=None,  # synthesis_runner not exposed on lifecycle/scheduler
                # Sprint F153: Top-level sprint verdict propagated to export
                sprint_verdict=intel.get("sprint_verdict"),
                # Sprint F204E: Analyst brief from sprint teardown
                analyst_brief=scheduler.get_analyst_brief(),
            )

            # Sprint F155: Log enrichment level
            logger.info(
                f"[EXPORT] {'fully_enriched' if _handoff_enriched else 'degraded'} → sprint_id={sprint_id}"
            )

            export_result = await export_sprint(store=store, handoff=handoff, sprint_id=sprint_id)
            logger.info(f"[EXPORT] finish layer → seeds={export_result.get('seeds_json','')}")

            # Deep probe runs AFTER export completes — post-sprint, non-blocking
            if deep_probe_enabled:
                try:
                    from hledac.universal.deep_research.probe_runner import run_deep_probe_if_enabled
                    probe_result = await run_deep_probe_if_enabled(
                        query=query,
                        store=store,
                        deep_probe_enabled=True,
                    )
                    if probe_result:
                        logger.info(f"[DEEP_PROBE] completed: {probe_result}")
                except Exception as probe_err:
                    logger.warning(f"[DEEP_PROBE] probe runner failed (non-fatal): {probe_err}")
        except Exception as ex:
            logger.warning(f"[EXPORT] sprint_exporter seam failed (non-fatal): {ex}")

    finally:
        # Sprint F195C: Finalize dashboard display
        if _dashboard is not None:
            try:
                elapsed_s = time.monotonic() - _phase_times["BOOT"]
                _dashboard.finish(result, elapsed_s)
            except Exception as e:
                logger.warning(f"Dashboard finish failed: {e}")  # fail-safe
        await store.aclose()
        # Sprint F206K: Close HTTPX client if it was lazily instantiated
        try:
            from hledac.universal.transport.httpx_client import close_httpx_client_async
            await close_httpx_client_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] HTTPX client close failed: {e}")  # fail-soft
        # Sprint F206L: Close curl_cffi sessions if they were lazily instantiated
        try:
            from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async
            await close_curl_cffi_sessions_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] curl_cffi sessions close failed: {e}")  # fail-soft
        # Sprint F219K: Close public_fetcher local Tor/I2P sessions
        try:
            from hledac.universal.fetching.public_fetcher import close_public_fetcher_sessions_async
            await close_public_fetcher_sessions_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] public_fetcher sessions close failed: {e}")  # fail-soft
        # Sprint F216A: Close aiohttp session used by public_fetcher
        try:
            from hledac.universal.network.session_runtime import close_aiohttp_session_async
            await close_aiohttp_session_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] aiohttp session close failed: {e}")  # fail-soft


# =============================================================================
# CLI entry point
# =============================================================================


async def run_ct_pivot(domain: str) -> None:
    """Run CT log pivot for a single domain."""
    ct_client = CTLogClient(TOR_ROOT.parent / "cache" / "crt")
    tor_transport = TorTransport()

    tor_started = await tor_transport.start()
    if tor_started:
        logger.info("Tor ready for .onion fetches")
    else:
        logger.warning("Tor unavailable — .onion sources disabled")

    try:
        async with aiohttp.ClientSession() as sess:
            result = await ct_client.pivot_domain(domain, sess)
        print(f"\nCT LOG PIVOT: {result['domain']}")
        print(f"  Cert count:  {result['cert_count']}")
        print(f"  First cert: {result['first_cert']}")
        print(f"  Last cert:  {result['last_cert']}")
        print(f"  SAN domains: {len(result['san_names'])}")
        for san in result["san_names"][:10]:
            print(f"    {san}")
        if result["san_names"] and len(result["san_names"]) > 10:
            print(f"    ... (+{len(result['san_names']) - 10} more)")
        print(f"  Issuers: {result['issuers']}")
    finally:
        await tor_transport.stop()
        logger.info("CT pivot done, Tor stopped")


async def run_semantic_pivot(query: str, top_k: int = 10) -> None:
    """
    Sprint 8SB: Semantic pivot — ANN search for similar findings.

    Loads SemanticStore, runs semantic_pivot, prints results.
    """
    from hledac.universal.paths import RAMDISK_ROOT

    lancedb_path = RAMDISK_ROOT / "lancedb"
    store = SemanticStore(db_path=lancedb_path)
    await store.initialize()

    try:
        results = await store.semantic_pivot(query, top_k=top_k)
        print(f"\n[SEMANTIC PIVOT] query: {query!r}  top_k={top_k}")
        if not results:
            print("  No results found.")
        for r in results:
            score = r.get("score", 0.0)
            src = r.get("source_type", "?")
            text = r.get("text", "")[:120]
            ts = r.get("ts", 0)
            print(f"  [{score:.3f}] {src:15} | {text}")
            if ts:
                import datetime
                print(f"               ts: {datetime.datetime.fromtimestamp(ts):.0f}")
        print(f"\nTotal results: {len(results)}")
    finally:
        await store.close()


def _install_signal_handler_for_loop(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    """
    Install SIGINT/SIGTERM handlers bound to a specific loop and event.

    Returns a cleanup function that restores previous signal handlers.
    Handler is idempotent, fail-soft, never calls loop.stop().
    """
    _prev_int: Callable[[int, Any], Any] | None = None
    _prev_term: Callable[[int, Any], Any] | None = None

    def _handler(signum: int, frame: Any) -> None:
        sig_name = getattr(signal.Signals, 'SIGINT', None) and signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        logging.info(f"[SIGNAL] Received {sig_name} — cooperative shutdown")
        try:
            if loop.is_running() and not loop.is_closed():
                loop.call_soon_threadsafe(shutdown_event.set)
            else:
                # Loop not running — set event directly
                shutdown_event.set()
        except Exception:
            pass

    try:
        _prev_int = signal.signal(signal.SIGINT, _handler)
        _prev_term = signal.signal(signal.SIGTERM, _handler)
        logging.info("[SIGNAL] SIGINT/SIGTERM handlers installed")
    except (ImportError, AttributeError, OSError, TypeError) as e:
        logging.warning(f"[SIGNAL] Signal handlers not available: {e}")

    def _restore() -> None:
        try:
            if _prev_int is not None:
                signal.signal(signal.SIGINT, _prev_int)
            if _prev_term is not None:
                signal.signal(signal.SIGTERM, _prev_term)
        except Exception:
            pass

    return _restore


def main() -> None:
    parser = argparse.ArgumentParser(description="Hledac Sprint 8RA Runner")
    parser.add_argument("--sprint", action="store_true", help="Run in sprint mode")
    parser.add_argument("--query", type=str, default="OSINT default query")
    parser.add_argument(
        "--duration",
        type=int,
        default=1800,
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=str(Path.home() / ".hledac" / "reports"),
    )
    parser.add_argument(
        "--ct-pivot",
        type=str,
        default=None,
        help="Run CT log pivot for a domain via crt.sh",
    )
    parser.add_argument(
        "--pivot",
        type=str,
        default=None,
        help="Sprint 8SB: semantic pivot — find similar findings via ANN search",
    )
    parser.add_argument(
        "--pivot-k",
        type=int,
        default=10,
        help="Number of results for --pivot (default: 10)",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Sprint F195B: Enable aggressive mode with 8s branch budgets",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint (deep web, S3 buckets, IPFS)",
    )
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=["default", "nonfeed_diagnostic"],
        help="F216B: Acquisition runtime profile (default | nonfeed_diagnostic)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # P1E-A: Set acquisition profile env var so build_acquisition_plan picks it up
    os.environ["HLEDAC_ACQUISITION_PROFILE"] = args.acquisition_profile

    if args.ct_pivot:
        asyncio.run(run_ct_pivot(args.ct_pivot))
    elif args.sprint:
        import contextlib
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        shutdown_event = asyncio.Event()
        restore_signals = _install_signal_handler_for_loop(loop, shutdown_event)
        try:
            sprint_task = loop.create_task(
                run_sprint(args.query, float(args.duration), args.export_dir, args.aggressive, args.deep_probe, acquisition_profile=args.acquisition_profile)
            )
            sig_task = loop.create_task(shutdown_event.wait())
            done, pending = loop.run_until_complete(
                asyncio.wait(
                    [sprint_task, sig_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            )
            if sprint_task not in done:
                sprint_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    loop.run_until_complete(sprint_task)
        finally:
            restore_signals()
            for task in pending:
                task.cancel()
            loop.close()
    elif args.pivot:
        asyncio.run(run_semantic_pivot(args.pivot, top_k=args.pivot_k))
    else:
        print("Hledac Sprint 8RA Runner")
        print("  python -m hledac.universal.core --sprint --query '...' --duration 1800")
        print("  python -m hledac.universal.core --ct-pivot example.com")
        print("  python -m hledac.universal.core --pivot 'ransomware CVE' --pivot-k 10")


if __name__ == "__main__":
    main()
