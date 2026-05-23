# Sprint 8VX §A: Export plane finish-up
# - ExportHandoff confirmed as primary handoff surface (wired in __main__.py:2343)
# - compat fallback documented with explicit removal conditions
# - No new framework, no new store API
"""
Sprint 8VI §A: EXPORT fáze — export_sprint() + _generate_next_sprint_seeds()
Sprint 8VJ §C: ExportHandoff | dict → typed handoff spotřeba
Sprint 8VX §A: Finish-up — removal conditions tightened, comments aligned with reality
Sprint F150I: product_value_summary — přenáší do exportu to, co runtime už ví:
  - accepted/stored reality z dedup status
  - reject breakdown (low-info / duplicate / fail-open)
  - circuit breaker state pokud je k dispozici
  - gnn_predictions signal
  - phase_durations timing truth
  - robustnější seed derivation (divný input → skip, ne pád)
Sprint F150J: Enhanced next-seed derivation driven by product_value_summary:
  - 4 seed categories: ioc_followup, query_suggestion, source_revisit, low_signal_recommendation
  - signal_quality → query direction (refine/broaden/narrow/new_approach)
  - reject_breakdown → query strategy (low_info_ratio → narrow scope)
  - cb_open_domains → source_revisit with backoff
  - depleted signal → retry_known_sources or new_approach
  - Bounded output: max 12 seeds total, sorted by priority
Sprint F150K: Next-action package — praktický follow-up balíček:
  - hypothesis_engine.suggest_next_queries() jako bounded seam (fail-soft, lazy load)
  - human-readable sprint_summary block (co found / co nevyšlo / co dělat dál)
  - priority-based next actions (max 10, deduped, signal-derived)
  - focus/expand recommendations derived from signal_quality
  - NO new persistence, NO new planner, NO new write-back path
Sprint F150L: Operator finish layer — derived seams integrated:
  - branch_value z scorecard (feed vs public branch analysis)
  - sprint_trend z store (poslední sprinty, fail-soft)
  - source_leaderboard z store (top zdroje, fail-soft)
  - eh.correlation (RunCorrelation) pokud přítomen
  - Praktický operator brief: co sprint našel, která branch nesla signál,
    co bylo slabé, jaký je nejbližší další krok, 2-5 zajímavých follow-upů
  - Rozhraní mezi feed/public branch recommendation
  - enriched next seeds z branch_value + sprint_trend
  - VŠECHNO derived only, žádný new business engine
Sprint F150P: Finish-layer truth fields — canonical surfaces from scheduler/core:
  - runtime_truth, feed_verdict, public_verdict, signal_path, hypothesis_pack
    z ExportHandoff.scorecard (compute_sprint_intelligence output)
  - run_truth_note: operator-facing sprint characterization (meaningful vs smoke)
  - branch_truth: definitive feed/public balance summary
  - best_first_move: immediate next action (single sentence)
  - why_this_run_matters: one-liner significance statement
  - No new store reads, no write-back, additive only
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sprint F232A: Component imports — narrative, scorecard, pivot, signal, hypothesis
# ---------------------------------------------------------------------------
from hledac.universal.export.components.narrative_builder import (
    _build_operator_brief,
    _build_sprint_summary,
    _derive_confidence_band,
    _derive_follow_ups,
    _derive_high_value_findings,
    _derive_next_step,
    _derive_priority_stack,
    _derive_trust_note,
    _derive_what_not_to_do,
    _derive_why_this_run_matters,
    _enrich_follow_ups,
    _get_branch_value,
)
from hledac.universal.export.components.pivot_builder import (
    _derive_branch_seeds,
    _derive_focus_expand,
    _derive_trend_seeds,
    _get_correlation_from_handoff,
    _get_runtime_truth,
)
from hledac.universal.export.components.signal_builder import (
    _compute_runtime_diagnosis,
    _extract_runtime_timing,
)
from hledac.universal.export.components.hypothesis_builder import (
    _derive_hypothesis_queries,
)

# ---------------------------------------------------------------------------
# Sprint F232A: Investigation Packet builder
# Connects existing reconciliation + planner into report enrichment.
# No new storage, no new model deps, no live network calls.
# ---------------------------------------------------------------------------

from hledac.universal.runtime.acquisition_telemetry_reconcile import (
    complete_source_family_outcomes_from_lane_details,
    complete_source_family_outcomes_from_prelude,
    reconcile_lane_detail_fields,
)
from hledac.universal.runtime.investigation_planner import (
    build_planner_state_from_report,
    plan_next_investigation_actions,
    summarize_planner_actions,
)


def _build_investigation_packet(report: dict) -> dict:
    """
    Sprint F232A: Build investigation_packet from a live/export report dict.

    Calls existing reconciliation + planner through their public APIs:
      1. reconcile_lane_detail_fields (from acquisition_telemetry_reconcile)
      2. complete_source_family_outcomes_from_lane_details
      3. build_planner_state_from_report
      4. plan_next_investigation_actions
      5. summarize_planner_actions

    investigation_packet shape:
      {
        "query": str,
        "seed_context": {available, source, domains, ips, urls, hashes, cves},
        "source_family_summary": [...],
        "terminal_coverage": {...},
        "corroboration": {...},
        "capability": {...},
        "gaps": [...],
        "planner_actions": [...],
        "next_pivots": [...]
      }

    Bounds:
      max planner_actions: 10
      max next_pivots: 10
      max source families: 20
      no raw HTML
      no raw huge evidence bodies

    Fail soft throughout. Returns partial packet on any error.
    """
    try:
        # 1. Reconcile lane detail fields first
        reconciled = reconcile_lane_detail_fields(dict(report))
        reconciled = complete_source_family_outcomes_from_lane_details(reconciled)
        # F250A: Complete source_family_outcomes from nonfeed prelude lane sets
        reconciled = complete_source_family_outcomes_from_prelude(reconciled)

        # 2. Build planner state
        planner_state = build_planner_state_from_report(reconciled)

        # 3. Run planner
        raw_actions = plan_next_investigation_actions(planner_state, max_actions=10)
        planner_actions = summarize_planner_actions(raw_actions)

        # ── Seed context ───────────────────────────────────────────────────────
        sc = planner_state.get("seed_context") or {}
        seed_context_out = {
            "available": bool(sc.get("available", False)),
            "source": str(sc.get("source", "") or ""),
            "domains": list(sc.get("domains", []))[:50],
            "ips": list(sc.get("ips", []))[:50],
            "urls": list(sc.get("urls", []))[:20],
            "hashes": list(sc.get("hashes", []))[:20],
            "cves": list(sc.get("cves", []))[:20],
        }

        # ── Source family summary ──────────────────────────────────────────────
        sfo_dict = planner_state.get("source_family_outcomes") or {}
        source_family_summary: list[dict] = []
        for family, outcome in sfo_dict.items():
            if len(source_family_summary) >= 20:
                break
            if not isinstance(outcome, dict):
                continue
            accepted = outcome.get("accepted", 0)
            rejected = outcome.get("rejected", 0)
            pending = outcome.get("pending", 0)
            # Terminal-only lanes (zero accepted, attempted) count as coverage
            attempted = outcome.get("attempted", False)
            terminal_state = outcome.get("terminal_state", "")
            source_family_summary.append({
                "family": family,
                "accepted": accepted,
                "rejected": rejected,
                "pending": pending,
                "attempted": attempted,
                "terminal_state": terminal_state,
                "has_findings": accepted > 0,
                "terminal_only": (attempted or bool(terminal_state)) and accepted == 0,
            })

        # ── Terminal coverage ───────────────────────────────────────────────────
        terminal_coverage: dict[str, str] = {}
        for entry in source_family_summary:
            fam = entry.get("family", "")
            if fam and entry.get("terminal_only") or entry.get("attempted"):
                terminal_coverage[fam] = entry.get("terminal_state", "") or "attempted_no_results"

        # ── Corroboration ──────────────────────────────────────────────────────
        corr_scores = planner_state.get("corroboration_scores") or {}
        corroboration: dict[str, float] = {}
        for k, v in corr_scores.items():
            if len(corroboration) >= 50:
                break
            corroboration[str(k)] = round(float(v), 4) if v is not None else 0.0

        # ── Capability ─────────────────────────────────────────────────────────
        cap_synth = report.get("capability_synthesis") or {}
        if isinstance(cap_synth, dict):
            capability = {
                "synthesis_available": True,
                "product_value_summary": report.get("product_value_summary") or {},
            }
        else:
            capability = {"synthesis_available": False}

        # ── Gaps ───────────────────────────────────────────────────────────────
        gaps: list[str] = []
        missing = planner_state.get("missing_lanes") or []
        for lane in missing:
            if len(gaps) >= 20:
                break
            gaps.append(f"lane_missing={lane}")

        # No accepted findings at all
        total_accepted = sum(v.get("accepted", 0) for v in sfo_dict.values())
        if total_accepted == 0 and not gaps:
            gaps.append("no_accepted_findings")

        # Feed dominance without nonfeed corroboration
        from hledac.universal.runtime.investigation_planner import _is_feed_dominant
        if _is_feed_dominant(sfo_dict) and not corroboration:
            gaps.append("feed_dominant_no_nonfeed_corroboration")

        # ── Next pivots from planner actions ───────────────────────────────────
        next_pivots: list[dict] = []
        for action in planner_actions:
            if len(next_pivots) >= 10:
                break
            act_type = action.get("action", "")
            target = action.get("target", "")
            if act_type in ("run_doh_on_domain", "run_ct_on_domain", "run_wayback_on_url",
                           "run_passivedns_on_domain_or_ip"):
                next_pivots.append({
                    "pivot_type": act_type.replace("run_", "").replace("_on_", "_"),
                    "target": target,
                    "priority": action.get("priority", 0.0),
                    "lane": action.get("lane", ""),
                })

        return {
            "query": str(planner_state.get("current_query", "") or ""),
            "seed_context": seed_context_out,
            "source_family_summary": source_family_summary,
            "terminal_coverage": terminal_coverage,
            "corroboration": corroboration,
            "capability": capability,
            "gaps": gaps,
            "planner_actions": planner_actions,
            "next_pivots": next_pivots,
        }
    except Exception as e:
        logger.warning(f"[EXPORT] investigation_packet build failed (fail-soft): {e}")
        return {
            "query": report.get("query", "") or "",
            "seed_context": {"available": False, "source": "", "domains": [], "ips": [], "urls": [], "hashes": [], "cves": []},
            "source_family_summary": [],
            "terminal_coverage": {},
            "corroboration": {},
            "capability": {"synthesis_available": False},
            "gaps": ["investigation_packet_build_failed"],
            "planner_actions": [],
            "next_pivots": [],
        }


# ---------------------------------------------------------------------------
# Sprint F250C: Provider Yield Diagnosis Surface
# Canonical reports show zero nonfeed yield with fragmented diagnostic signals.
# This helper synthesizes a unified provider_yield_diagnosis dict from existing
# report fields: source_family_outcomes, public_discovery_empty_reason,
# ct_provider_status, doh_terminal_stage, wayback_terminal_state,
# passive_dns_terminal_state, capability_synthesis.
#
# RULES:
#   - source_family_outcomes is primary when available.
#   - Detail fields (public_discovery_empty_reason, etc.) are fallback.
#   - No raw HTML, no raw provider responses.
#   - No network/model imports.
#   - Fail-soft: returns partial diagnosis when fields are missing.
#   - accepted nonfeed evidence changes overall status to partial_yield.
# ---------------------------------------------------------------------------

def _build_provider_yield_diagnosis(report: dict) -> dict:
    """
    Sprint F250C: Build provider_yield_diagnosis from a report dict.

    Returns a compact diagnosis dict with keys:
      - overall: no_positive_nonfeed_yield | partial_yield | nonfeed_successful
      - families: {public: {status, reason, action}, ct: {...}, doh: {...}, wayback: {...}, passive_dns: {...}}
      - recommended_next_engineering_action: str
      - recommended_next_investigation_action: str

    Bounds:
      - status values: error_or_zero | cooldown | attempted_empty | successful | skipped | unknown
      - action values: check_provider_selection_or_bootstrap | retry_later_or_use_cache |
                       try_passive_dns_or_wayback | retry_with_fresh_seeds | none
      - recommended_*_action: one of a bounded set of engineering/investigation directives
    """
    try:
        # ── Source family outcomes lookup ─────────────────────────────────────
        sfo_list: list[dict] = report.get("source_family_outcomes") or []
        sfo_by_family: dict[str, dict] = {entry.get("family", "").lower(): entry for entry in sfo_list}

        # ── Compute overall nonfeed yield ─────────────────────────────────────
        nonfeed_accepted = 0
        for fam, entry in sfo_by_family.items():
            if fam in ("ct", "doh", "wayback", "passive_dns", "pivot_executor"):
                nonfeed_accepted += entry.get("accepted_count", 0) or 0

        if nonfeed_accepted > 0:
            overall = "nonfeed_successful"
        else:
            overall = "no_positive_nonfeed_yield"

        # ── PUBLIC diagnosis ───────────────────────────────────────────────────
        # Primary: source_family_outcomes entry
        public_outcome = sfo_by_family.get("public", {})
        public_terminal = public_outcome.get("terminal_state", "") or ""
        public_error = public_outcome.get("error", "") or ""
        public_attempted = public_outcome.get("attempted", False)

        # Fallback to detail fields
        if not public_terminal:
            public_terminal = report.get("public_terminal_stage", "") or ""
        if not public_error:
            public_error = report.get("public_discovery_empty_reason", "") or ""

        # Determine public status and action
        if public_terminal in ("DISCOVERY_ERROR", "FETCH_ERROR", "FETCH_TIMEOUT"):
            if public_error in ("provider_returned_zero", "no_provider_selected",
                                "provider_unavailable", "provider_timeout"):
                pub_status = "error_or_zero"
                pub_reason = public_error or "provider_returned_zero"
                pub_action = "check_provider_selection_or_bootstrap"
            else:
                pub_status = "error_or_zero"
                pub_reason = "DISCOVERY_ERROR"
                pub_action = "check_provider_selection_or_bootstrap"
        elif public_terminal in ("ATTEMPTED_NO_RESULTS", "TERMINAL_NO_RESULTS", "NO_CANDIDATES"):
            pub_status = "attempted_empty"
            pub_reason = public_error or "no_candidates"
            pub_action = "retry_with_fresh_seeds"
        elif not public_attempted and public_terminal in ("SKIPPED", "", None):
            pub_status = "skipped"
            pub_reason = "not_attempted"
            pub_action = "none"
        else:
            pub_status = "unknown"
            pub_reason = public_terminal or "unknown"
            pub_action = "check_provider_selection_or_bootstrap"

        public_diag = {
            "status": pub_status,
            "reason": pub_reason,
            "action": pub_action,
        }

        # ── CT diagnosis ───────────────────────────────────────────────────────
        ct_outcome = sfo_by_family.get("ct", {})
        ct_terminal = ct_outcome.get("terminal_state", "") or ""
        ct_error = ct_outcome.get("error", "") or ""
        ct_attempted = ct_outcome.get("attempted", False)

        if not ct_terminal:
            ct_terminal = report.get("ct_terminal_stage", "") or ""
        if not ct_error:
            ct_error = report.get("ct_provider_status", "") or ""
            # Strip prefix: "CTProviderStatus.COOLDOWN_ACTIVE" → "cooldown_active"
            if ct_error.startswith("CTProviderStatus."):
                ct_error = ct_error[len("CTProviderStatus."):].lower()

        if ct_error in ("cooldown_active", "cooldown"):
            ct_status = "cooldown"
            ct_reason = "cooldown_active"
            ct_action = "retry_later_or_use_cache"
        elif ct_terminal in ("ATTEMPTED_NO_RESULTS", "no_candidates", "attempted_empty"):
            ct_status = "attempted_empty"
            ct_reason = ct_error or "no_candidates"
            ct_action = "retry_later_or_use_cache"
        elif ct_terminal in ("ATTEMPTED_ACCEPTED",) or (ct_outcome.get("accepted_count", 0) or 0) > 0:
            ct_status = "successful"
            ct_reason = ct_error or "accepted"
            ct_action = "none"
        elif not ct_attempted:
            ct_status = "skipped"
            ct_reason = "not_attempted"
            ct_action = "none"
        else:
            ct_status = "error_or_zero"
            ct_reason = ct_error or "unknown"
            ct_action = "retry_later_or_use_cache"

        ct_diag = {
            "status": ct_status,
            "reason": ct_reason,
            "action": ct_action,
        }

        # ── DOH diagnosis ──────────────────────────────────────────────────────
        doh_outcome = sfo_by_family.get("doh", {})
        doh_terminal = doh_outcome.get("terminal_state", "") or ""
        doh_error = doh_outcome.get("error", "") or ""
        doh_attempted = doh_outcome.get("attempted", False)

        if not doh_terminal:
            doh_terminal = report.get("doh_terminal_stage", "") or ""
        if not doh_error:
            doh_error = report.get("doh_provider_errors", "")
            if isinstance(doh_error, (list, tuple)) and doh_error:
                doh_error = str(doh_error[0])
            elif not isinstance(doh_error, str):
                doh_error = ""

        if doh_terminal in ("attempted_empty", "no_candidates"):
            doh_status = "attempted_empty"
            doh_reason = doh_error or "attempted_empty"
            doh_action = "try_passive_dns_or_wayback"
        elif doh_terminal in ("ATTEMPTED_ACCEPTED", "attempted_accepted") or (doh_outcome.get("accepted_count", 0) or 0) > 0:
            doh_status = "successful"
            doh_reason = doh_error or "accepted"
            doh_action = "none"
        elif not doh_attempted:
            doh_status = "skipped"
            doh_reason = "not_attempted"
            doh_action = "none"
        elif doh_terminal in ("timeout", "provider_error", "dependency_missing"):
            doh_status = "error_or_zero"
            doh_reason = doh_error or doh_terminal
            doh_action = "try_passive_dns_or_wayback"
        else:
            doh_status = "attempted_empty"
            doh_reason = doh_terminal or "unknown"
            doh_action = "try_passive_dns_or_wayback"

        doh_diag = {
            "status": doh_status,
            "reason": doh_reason,
            "action": doh_action,
        }

        # ── Wayback diagnosis ─────────────────────────────────────────────────
        wb_outcome = sfo_by_family.get("wayback", {})
        wb_terminal = wb_outcome.get("terminal_state", "") or ""
        wb_error = wb_outcome.get("error", "") or ""
        wb_attempted = wb_outcome.get("attempted", False)

        if not wb_terminal:
            wb_terminal = report.get("wayback_terminal_state", "") or ""

        if wb_terminal in ("no_terminal", "terminal_no_results", "wayback_unchanged_rejected"):
            wb_status = "attempted_empty"
            wb_reason = wb_error or wb_terminal or "no_terminal"
            wb_action = "none"
        elif not wb_attempted:
            wb_status = "skipped"
            wb_reason = "not_attempted"
            wb_action = "none"
        elif wb_outcome.get("accepted_count", 0) or 0 > 0:
            wb_status = "successful"
            wb_reason = wb_error or "accepted"
            wb_action = "none"
        else:
            wb_status = "attempted_empty"
            wb_reason = wb_terminal or "unknown"
            wb_action = "none"

        wayback_diag = {
            "status": wb_status,
            "reason": wb_reason,
            "action": wb_action,
        }

        # ── PassiveDNS diagnosis ────────────────────────────────────────────────
        pdns_outcome = sfo_by_family.get("passive_dns", {})
        pdns_terminal = pdns_outcome.get("terminal_state", "") or ""
        pdns_error = pdns_outcome.get("error", "") or ""
        pdns_attempted = pdns_outcome.get("attempted", False)

        if not pdns_terminal:
            pdns_terminal = report.get("passive_dns_terminal_state", "") or ""

        if pdns_terminal in ("no_terminal", "terminal_no_results"):
            pdns_status = "attempted_empty"
            pdns_reason = pdns_error or "no_terminal"
            pdns_action = "none"
        elif not pdns_attempted:
            pdns_status = "skipped"
            pdns_reason = "not_attempted"
            pdns_action = "none"
        elif pdns_outcome.get("accepted_count", 0) or 0 > 0:
            pdns_status = "successful"
            pdns_reason = pdns_error or "accepted"
            pdns_action = "none"
        else:
            pdns_status = "attempted_empty"
            pdns_reason = pdns_terminal or "unknown"
            pdns_action = "none"

        pdns_diag = {
            "status": pdns_status,
            "reason": pdns_reason,
            "action": pdns_action,
        }

        # ── Engineering and investigation recommendations ────────────────────────
        cap_synth = report.get("capability_synthesis") or {}
        next_eng = cap_synth.get("next_engineering_action", "") or ""
        next_inv = cap_synth.get("next_investigation_action", "") or ""

        # Fallback recommendations based on overall status
        if overall == "no_positive_nonfeed_yield":
            if next_eng:
                eng_action = next_eng
            else:
                eng_action = "improve_nonfeed_provider_yield"
            if next_inv:
                inv_action = next_inv
            else:
                inv_action = "use_planner_next_seeds"
        else:
            eng_action = next_eng or "none"
            inv_action = next_inv or "none"

        families: dict[str, dict] = {
            "public": public_diag,
            "ct": ct_diag,
            "doh": doh_diag,
            "wayback": wayback_diag,
            "passive_dns": pdns_diag,
        }

        return {
            "overall": overall,
            "families": families,
            "recommended_next_engineering_action": eng_action,
            "recommended_next_investigation_action": inv_action,
        }
    except Exception as e:
        logger.warning(f"[EXPORT] provider_yield_diagnosis build failed (fail-soft): {e}")
        return {
            "overall": "unknown",
            "families": {},
            "recommended_next_engineering_action": "check_provider_selection_or_bootstrap",
            "recommended_next_investigation_action": "use_planner_next_seeds",
        }


# ---------------------------------------------------------------------------
# Sprint F229A: Terminal truth reconciliation helper
# Resolves contradictions between product_value_summary, runtime_truth,
# partial_export finding_count, and capability_synthesis surfaces.
#
# Truth precedence (first non-zero wins):
#   1. explicit runtime_truth.accepted_findings if > 0
#   2. scorecard.runtime_truth.accepted_findings if > 0
#   3. scorecard.accepted_findings if > 0
#   4. pvs.accepted / pvs.runtime_accepted_findings if > 0
#   5. 0 fallback (all-zero = low-signal / smoke run)
#
# Rules:
#   - If accepted_findings > 0 and meaningful=true, do NOT emit invalid_capability
#     solely because product_value_summary is zero.
#   - If pvs is zero but runtime_truth has accepted, backfill pvs.
#   - Preserve explicit low-signal verdicts when ALL sources are zero.
#   - Expose reconciliation via truth_reconciliation_applied + reason.
# ---------------------------------------------------------------------------

def reconcile_terminal_truth(
    pvs: dict[str, Any] | None,
    scorecard: dict[str, Any] | None,
    runtime_truth: dict[str, Any] | None,
    partial_finding_count: int | None = None,
) -> tuple[dict[str, Any], int, bool, str]:
    """
    Sprint F229A: reconcile terminal truth across surfaces.

    Returns (reconciled_pvs, accepted_count, truth_reconciliation_applied, reason).
    Side-effect: reconciled_pvs may have accepted/runtime_accepted_findings backfilled.

    Applied BEFORE: final terminal report write, partial export write, next_seeds,
    and capability_synthesis generation.
    """
    scorecard = scorecard or {}
    runtime_truth = runtime_truth or {}
    pvs = dict(pvs) if pvs else {}

    # Resolve accepted_findings using truth precedence
    rt_accepted = 0
    if runtime_truth and isinstance(runtime_truth, dict):
        rt_accepted = runtime_truth.get("accepted_findings", 0) or 0

    sc_rt_accepted = 0
    _sc_rt = scorecard.get("runtime_truth")
    if isinstance(_sc_rt, dict):
        sc_rt_accepted = _sc_rt.get("accepted_findings", 0) or 0

    sc_accepted = _pvs_n(scorecard, "accepted_findings", 0)
    pvs_accepted = pvs.get("accepted", 0) if pvs else 0
    pvs_runtime_accepted = pvs.get("runtime_accepted_findings", 0) if pvs else 0
    partial_count = partial_finding_count or 0

    # Truth precedence order
    candidates = [
        ("runtime_truth.accepted_findings", rt_accepted),
        ("scorecard.runtime_truth.accepted_findings", sc_rt_accepted),
        ("scorecard.accepted_findings", sc_accepted),
        ("pvs.accepted", pvs_accepted),
        ("pvs.runtime_accepted_findings", pvs_runtime_accepted),
        ("partial_finding_count", partial_count),
    ]

    accepted_count = 0
    truth_applied = False
    reason = ""

    for name, val in candidates:
        if val > 0:
            accepted_count = val
            truth_applied = True
            reason = f"reconciled_from={name}"
            break

    # Backfill pvs if runtime_truth had the truth
    if accepted_count > 0:
        if pvs.get("accepted", 0) == 0:
            pvs["accepted"] = accepted_count
        if pvs.get("runtime_accepted_findings", 0) == 0:
            pvs["runtime_accepted_findings"] = accepted_count
        if pvs.get("findings_per_minute", 0.0) == 0.0 and pvs.get("runtime_findings_per_minute", 0.0) > 0:
            pvs["findings_per_minute"] = pvs["runtime_findings_per_minute"]

    return pvs, accepted_count, truth_applied, reason


# ---------------------------------------------------------------------------
# Sprint F192F §1: PVS helper — type-safe numeric coercion for scorecard reads
# Consolidates isinstance guard pattern used throughout _build_product_value_summary.
# Guards against MagicMock / non-numeric values in test or degraded scenarios.
# ---------------------------------------------------------------------------

def _pvs_num(val: Any, default: float | int) -> float | int:
    """Type-safe numeric coercion — returns default for non-numeric values."""
    return val if isinstance(val, (int, float)) else default


def _pvs_n(scorecard: dict, key: str, default: float | int) -> float | int:
    """Type-safe scorecard numeric read with key-level default."""
    return _pvs_num(scorecard.get(key, default), default)


async def export_partial_sprint(
    store: Any,
    handoff: "ExportHandoff | dict",  # type: ignore[name-defined]
    sprint_id: str | None = None,
    finding_count: int = 0,
) -> dict:
    """
    PARTIAL EXPORT — recovery-grade JSON artifact written during aggressive-mode runs.

    Triggered every N findings (default 10) in aggressive mode, and on early
    windup / immediate abort so the latest partial artifact remains available.

    Writes to the same directory as the final report:
      {sprint_id}_partial.json

    Derived from the SAME canonical truth surfaces used by export_sprint():
    runtime_truth, scorecard, branch_mix.  Final export (export_sprint) is the
    canonical terminal artifact — it does NOT read or delete the partial file;
    the partial is purely a recovery surface.

    Never raises. Fail-soft: write errors are logged but do not crash the sprint.
    """
    from hledac.universal.paths import get_sprint_json_report_path
    from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

    _sprint_id = sprint_id or "unknown"
    try:
        eh = ensure_export_handoff(handoff, default_sprint_id=_sprint_id)
        _sprint_id = eh.sprint_id if eh.sprint_id != "unknown" else _sprint_id
    except Exception:
        eh = handoff if not isinstance(handoff, dict) else None

    report_path = get_sprint_json_report_path(_sprint_id)
    partial_path = report_path.parent / f"{_sprint_id}_partial.json"

    runtime_truth: dict = {}
    scorecard: dict = {}
    if eh and hasattr(eh, "runtime_truth"):
        runtime_truth = eh.runtime_truth or {}
    elif isinstance(handoff, dict):
        runtime_truth = handoff.get("runtime_truth", {})

    if eh and hasattr(eh, "scorecard"):
        scorecard = eh.scorecard or {}
    elif isinstance(handoff, dict):
        scorecard = handoff.get("scorecard", {})

    # Fallback: if runtime_truth is empty but scorecard has runtime_truth, use it
    # F230B: ensures partial export top-level runtime_truth mirrors scorecard.runtime_truth
    _sc_rt = scorecard.get("runtime_truth")
    if _sc_rt is None:
        _sc_rt = scorecard.get("run_truth")
    if not runtime_truth and _sc_rt is not None and isinstance(_sc_rt, dict):
        # F230B: filter raw_evidence — evidence lives only in LMDB, not JSON surfaces
        _filtered_rt = {k: v for k, v in _sc_rt.items() if k != "raw_evidence"}
        runtime_truth = _filtered_rt

    partial_artifact = {
        "sprint_id": _sprint_id,
        "is_partial": True,
        "finding_count": finding_count,
        "runtime_truth": runtime_truth,
        "scorecard": scorecard,
        "partial_export": True,
    }

    try:
        # F214OPT314: compress transient artifact with zstd (10-18% size reduction, 1.3-1.5x faster)
        # Written as NEW sidecar (.json.zst) — existing .json path untouched for backward compat
        _text_data = json.dumps(partial_artifact, indent=2, default=str)
        try:
            import compression.zstd
            compressed = compression.zstd.compress(_text_data.encode('utf-8'))
            partial_path_zst = partial_path.with_suffix('.json.zst')
            partial_path_zst.write_bytes(compressed)
            logger.info(f"[PARTIAL-EXPORT] {partial_path_zst} — findings={finding_count} (zstd sidecar)")
        except ImportError:
            # zstd unavailable — only write .json (already done below)
            logger.warning(f"[PARTIAL-EXPORT] zstd unavailable, plain JSON only")
        # Always write .json for backward compatibility with existing readers
        partial_path.write_text(_text_data)
    except Exception as ex:
        logger.warning(f"[PARTIAL-EXPORT] write failed (non-fatal): {ex}")

    return {"partial_json": str(partial_path), "finding_count": finding_count}


async def export_sprint(
    store: Any,
    handoff: "ExportHandoff",  # type: ignore[name-defined]
    sprint_id: str | None = None,
    enable_security_enrichment: bool = False,
    export_mode: str = "slim",
) -> dict:
    """
    EXPORT fáze — JSON report, seed tasky pro příští sprint.

    Voláno z _print_scorecard_report() v __main__.py EXPORT fázi.
    Nikdy nevyhodí výjimku.

    Canonical input: typed ExportHandoff from __main__._print_scorecard_report().
    The handoff parameter is declared as ExportHandoff — the canonical producer
    always passes typed ExportHandoff. The dict/None compat paths in
    ensure_export_handoff() are preserved for backward compat but are NOT
    exercised by the canonical producer path.

    export_mode (default "slim"):
      slim — M1-safe minimal export. JSON report + next seeds + canonical runtime
             truth. No security enrichment, no stealth engine, no evidence chains,
             no hypothesis engine, no background monitoring.
      full — Full export with all enrichment layers. Enables evidence_chain
             (igraph) and hypothesis_engine (numpy/mlx) when explicitly requested.
             Security enrichment (enable_security_enrichment=True) also requires
             full mode.

    enable_security_enrichment (default False):
      Když False (implicitní): export_sprint nepouští SecurityCoordinator/StealthEngine.
      Když True (explicitní stealth mód): volá sanitize_outbound pro PII audit.
      Vždy produkuje platný JSON report.

    Součásti:
      1. JSON report do ~/.hledac/reports/{sprint_id}_report.json
         Canonical path owner: paths.get_sprint_json_report_path() (post-F500B)
      2. Seed tasky pro příští sprint z top IOC graph nodes
      3. Sprint F150I: product_value_summary — decisions有用的 pro další sprinty

    PRIMARY HANDOFF SURFACE (Sprint 8VX):
      - ExportHandoff.top_nodes — kanonický zdroj pro seed generation
      - ExportHandoff.scorecard — kanonický zdroj pro JSON report

    ACCEPTED COMPAT SEAM — store-facing fallback:
      - Pokud top_nodes prázdné (non-main caller passoval prázdný seznam),
        zkusí store.get_top_seed_nodes(n=5) — store-facing seam.
      - REMOVAL CONDITION: žádný — oba kanoničtí producenti (__main__._print_scorecard_report
        i core.__main__.run_sprint) vždy plní top_nodes z store.get_top_seed_nodes() PŘED
        konstrukcí ExportHandoff. Tento fallback je legacy defense pro ne-kanonické volající.
      - OBRAZ: __main__ řádek 2576: _top_nodes = store.get_top_seed_nodes(n=10)
        core.__main__ řádek 969: top_seed_nodes = store.get_top_seed_nodes(n=5)
        → oba canonical producers už volaj store PŘED export_sprint(), fallback se nikdy netrefí.

    Sparrow Principle: export_sprint is a thin dispatcher. The actual logic lives in
    JSONFormatter.format() (formatters.py). This module still contains all 44 private
    helpers — they are NOT moved, just organized under the formatter class hierarchy.
    """
    from .formatters import JSONFormatter
    formatter = JSONFormatter()
    return await formatter.format(
        store=store,
        handoff=handoff,
        sprint_id=sprint_id,
        enable_security_enrichment=enable_security_enrichment,
        export_mode=export_mode,
    )


def _action_to_seed(action: dict) -> dict | None:
    """
    Sprint F238A: Map a single planner_action dict to a seed dict.

    action keys: action, target, priority, reason, lane
    seed keys: seed_type, action, lane, target, ioc_type, priority, reason, source

    Bounds:
      max 12 total seeds (enforced by caller)
      stable ordering: priority desc, then action order
      dedupe by (action, lane, target) — caller dedupes via _dedup_seeds
      no raw HTML, no raw evidence, no model imports, no network

    Returns None when action has no meaningful seed (e.g. stop_enough_evidence
    or synthesize_with_llm or plain query text that is not an IOC target).
    """
    act = action.get("action", "") or ""
    target = action.get("target", "") or ""
    priority = float(action.get("priority", 0.5))
    reason = action.get("reason", "") or ""

    match act:
        case "run_doh_on_domain":
            return {
                "seed_type": "lane_action",
                "action": "run_doh_on_domain",
                "lane": "DOH",
                "target": target,
                "ioc_type": "domain",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "run_ct_on_domain":
            return {
                "seed_type": "lane_action",
                "action": "run_ct_on_domain",
                "lane": "CT",
                "target": target,
                "ioc_type": "domain",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "run_wayback_on_url":
            # target may be "https://example.com" — pass as-is, lane knows it's URL
            return {
                "seed_type": "lane_action",
                "action": "run_wayback_on_url",
                "lane": "WAYBACK",
                "target": target,
                "ioc_type": "url",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "run_passivedns_on_domain_or_ip":
            # Determine ioc_type from target shape; pass target as-is
            ioc_type = "domain"
            if target and target[0].isdigit() and target.count(".") == 3:
                ioc_type = "ip"
            return {
                "seed_type": "lane_action",
                "action": "run_passivedns_on_domain_or_ip",
                "lane": "PASSIVE_DNS",
                "target": target,
                "ioc_type": ioc_type,
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "public_bootstrap_from_seed":
            # target is plain query text — NOT an IOC. Do not fabricate ioc_type.
            return {
                "seed_type": "public_bootstrap_seed",
                "action": "public_bootstrap_from_seed",
                "lane": "PUBLIC",
                "target": target,
                "ioc_type": "",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "extract_more_seeds_from_duckdb":
            return {
                "seed_type": "diagnostic_action",
                "action": "extract_more_seeds_from_duckdb",
                "lane": "public",
                "target": target,
                "ioc_type": "",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "synthesize_with_llm":
            return {
                "seed_type": "synthesis_action",
                "action": "synthesize_with_llm",
                "lane": "synthesis",
                "target": target,
                "ioc_type": "",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case "stop_enough_evidence":
            return {
                "seed_type": "stop_action",
                "action": "stop_enough_evidence",
                "lane": "stop",
                "target": target,
                "ioc_type": "",
                "priority": priority,
                "reason": reason,
                "source": "investigation_packet.planner_actions",
            }
        case _:
            return None


def _planner_actions_to_seeds(planner_actions: list[dict]) -> tuple[list[dict], str]:
    """
    Sprint F238A: Convert planner_actions to seeds using _action_to_seed.

    Returns (seeds, next_seeds_source):
      - "investigation_packet.planner_actions" when planner_actions is non-empty
      - "legacy_fallback" when planner_actions is empty or None

    Bounds: max 12 seeds, stable priority-desc sort, dedupe by (action, lane, target).
    No model imports, no network calls, no raw HTML/evidence.
    """
    if not planner_actions:
        return [], "legacy_fallback"

    seeds: list[dict] = []
    for idx, action in enumerate(planner_actions):
        seed = _action_to_seed(action)
        if seed is not None:
            seed["_orig_idx"] = idx
            seeds.append(seed)

    if not seeds:
        return [], "legacy_fallback"

    # Stable sort: priority desc, then insertion order (action order from planner)
    seeds.sort(key=lambda s: (-s["priority"], s["_orig_idx"]))

    # Dedup by (action, lane, target)
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for s in seeds:
        key = (s["action"], s["lane"], s["target"])
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    # Enforce max 12
    MAX_NEXT_SEEDS = 12
    if len(deduped) > MAX_NEXT_SEEDS:
        deduped = deduped[:MAX_NEXT_SEEDS]

    return deduped, "investigation_packet.planner_actions"


def _generate_next_sprint_seeds(
    top_nodes: list,
    sprint_id: str,
    report_path: pathlib.Path | None,
    pvs: dict[str, Any] | None = None,
    branch_value: dict[str, Any] | None = None,
    sprint_trend: list[dict] | None = None,
    export_mode: str = "slim",
    capability_synthesis: dict[str, Any] | None = None,
    analyst_brief: dict[str, Any] | None = None,
    investigation_packet: dict[str, Any] | None = None,
) -> pathlib.Path:
    """
    Sprint F150J: Enhanced seed derivation driven by product_value_summary.
    Sprint F226D: Enhanced with capability_synthesis + analyst_brief for active seed shaping.
    Sprint F238A: investigation_packet.planner_actions is canonical next-sprint-seeds source.

    Seed source priority (Sprint F238A):
      1. investigation_packet.planner_actions → canonical, high-fidelity
      2. Legacy heuristics (top_nodes, pvs, branch_value, ...) → fallback only

    4 legacy seed categories derived from pvs:
      1. ioc_followup — top graph nodes (existing _type_aware_seeds logic)
      2. query_suggestion — based on signal_quality + reject_breakdown
      3. source_revisit — circuit-breaker open domains + depleted signal
      4. low_signal_recommendation — when sprint found almost nothing

    Bounded: max ~12 seeds total. No combinatorial explosion.

    Canonical next-seeds path (Sprint F500D):
      - Primary: get_sprint_next_seeds_path(sprint_id) z paths.py
      - Fallback: SPRINT_STORE_ROOT.parent/"reports" if report_path is None

    export_mode (Sprint F207H):
      slim (default) — skip hypothesis_engine (numpy/mlx heavy import)
      full — enables hypothesis_engine for query suggestions
    """
    from hledac.universal.paths import SPRINT_STORE_ROOT, get_sprint_next_seeds_path
    # Sprint F229 §1: Use get_sprint_next_seeds_path() — same canonical pattern as
    # get_sprint_json_report_path() used for report_path. This ensures the test's
    # patch on get_sprint_next_seeds_path is respected in _generate_next_sprint_seeds.
    if report_path is not None:
        seeds_path = get_sprint_next_seeds_path(sprint_id)
    else:
        seeds_path = SPRINT_STORE_ROOT.parent / "reports" / f"{sprint_id}_next_seeds.json"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)
    seeds: list[dict[str, Any]] = []
    next_seeds_source = "legacy_fallback"

    try:
        # Sprint F238A: Canonical path — use planner_actions from investigation_packet
        planner_actions = None
        if investigation_packet and isinstance(investigation_packet, dict):
            pa = investigation_packet.get("planner_actions")
            if pa and isinstance(pa, list) and len(pa) > 0:
                planner_actions = pa

        if planner_actions:
            # Canonical: planner_actions are the primary next-sprint-seeds source
            planner_seeds, next_seeds_source = _planner_actions_to_seeds(planner_actions)
            seeds.extend(planner_seeds)
        else:
            # Legacy fallback: gather from top_nodes and pvs heuristics
            next_seeds_source = "legacy_fallback"

            # 1. IOC follow-up seeds from top_nodes (existing logic)
            for node in top_nodes:
                try:
                    if isinstance(node, dict):
                        ioc_value = str(node.get("value", "")) if node else ""
                        ioc_type = str(node.get("ioc_type", "unknown")) if node else "unknown"
                    elif isinstance(node, (list, tuple)) and len(node) >= 2:
                        ioc_value = str(node[0]) if node[0] else ""
                        ioc_type = str(node[1]) if node[1] else "unknown"
                    elif isinstance(node, (list, tuple)) and len(node) == 1:
                        ioc_value = str(node[0]) if node[0] else ""
                        ioc_type = "unknown"
                    elif isinstance(node, str):
                        ioc_value = node
                        ioc_type = "unknown"
                    elif isinstance(node, (int, float)):
                        ioc_value = str(node)
                        ioc_type = "unknown"
                    else:
                        continue
                except Exception:
                    continue

                if not ioc_value or len(ioc_value) < 3:
                    continue

                node_seeds = _type_aware_seeds(ioc_value, ioc_type, reason="ioc_followup")
                seeds.extend(node_seeds)

            # 2. Sprint F150J: query_suggestion — derive next queries from sprint signal
            if pvs:
                query_seeds = _derive_query_seeds(pvs)
                seeds.extend(query_seeds)

            # 3. Sprint F150J: source_revisit — circuit breaker + depleted signal
            if pvs:
                revisit_seeds = _derive_source_revisit_seeds(pvs)
                seeds.extend(revisit_seeds)

            # 4. Sprint F150J: low_signal_recommendation — when sprint was nearly empty
            if pvs:
                low_signal_seeds = _derive_low_signal_seeds(pvs)
                seeds.extend(low_signal_seeds)

            # 5. Sprint F207H: hypothesis_engine.suggest_next_queries() seam
            if pvs and export_mode == "full":
                hyp_queries = _derive_hypothesis_queries(pvs, max_queries=2)
                seeds.extend(hyp_queries)

            # 6. Sprint F150K: focus/expand recommendations
            if pvs:
                focus_expand = _derive_focus_expand(pvs)
                seeds.extend(focus_expand)

            # 7. Sprint F150L: branch_value-driven seeds
            if branch_value:
                branch_seeds = _derive_branch_seeds(branch_value)
                seeds.extend(branch_seeds)

            # 8. Sprint F150L: sprint_trend-driven seeds
            if sprint_trend:
                trend_seeds = _derive_trend_seeds(sprint_trend)
                seeds.extend(trend_seeds)

            # 9. Sprint F226D: capability_synthesis-driven seeds
            if capability_synthesis:
                cap_seeds = _derive_capability_seeds(capability_synthesis)
                seeds.extend(cap_seeds)

            # 10. Sprint F226D: analyst_brief-driven seeds
            if analyst_brief:
                brief_seeds = _derive_analyst_brief_seeds(analyst_brief)
                seeds.extend(brief_seeds)

            # Sprint F226D: dedup before cap
            seeds = _dedup_seeds(seeds)

            # Sprint F214Q: Merge quantum pathfinder seeds with degree-centrality seeds
            from hledac.universal.knowledge.sprint_seeds_store import sync_load_sprint_seeds
            quantum_seeds = sync_load_sprint_seeds(sprint_id)
            if quantum_seeds:
                q_seed_dicts = [
                    {"seed_type": "quantum_path", "query": q, "priority": 0.8, "source": "graph_quantum_pathfinder"}
                    for q in quantum_seeds[:50]
                ]
                # Merge: quantum first (higher fidelity path-informed), then dedup
                seeds = _dedup_seeds(q_seed_dicts + seeds)
                next_seeds_source = "quantum_pathfinder"

            # Bounded output — keep total seed count manageable
            MAX_SEEDS = 15
            if len(seeds) > MAX_SEEDS:
                seeds.sort(key=lambda s: s.get("priority", 0.5), reverse=True)
                seeds = seeds[:MAX_SEEDS]

        # Surface next_seeds_source in the wrapper
        _seeds_wrapper = {
            "seeds": seeds,
            "next_seeds_source": next_seeds_source,
            "capability_synthesis": capability_synthesis,
        }
        _seeds_text = json.dumps(_seeds_wrapper, indent=2, default=str)
        _seeds_bytes = _seeds_text.encode("utf-8")
        # F214ZSTD2: write optional zstd sidecar
        try:
            import compression.zstd
            seeds_zst = seeds_path.with_suffix(".json.zst")
            seeds_zst.write_bytes(compression.zstd.compress(_seeds_bytes, level=3))
            logger.info(f"[EXPORT] {len(seeds)} seeds ({next_seeds_source}) → {seeds_zst} (zstd sidecar)")
        except ImportError:
            logger.warning("[EXPORT] zstd unavailable, plain JSON only")
        seeds_path.write_text(_seeds_text, encoding="utf-8")
        logger.info(f"[EXPORT] {len(seeds)} seeds ({next_seeds_source}) ({', '.join(_seed_type_counts(seeds))}) → {seeds_path}")
    except Exception as e:
        logger.warning(f"[EXPORT] Enhanced seed generation failed: {e}")
        _empty_wrapper = {
            "seeds": [],
            "next_seeds_source": next_seeds_source,
            "capability_synthesis": capability_synthesis,
        }
        _empty_text = json.dumps(_empty_wrapper, indent=2)
        try:
            import compression.zstd
            seeds_zst = seeds_path.with_suffix(".json.zst")
            seeds_zst.write_bytes(compression.zstd.compress(_empty_text.encode("utf-8"), level=3))
        except ImportError:
            logger.warning("[EXPORT] zstd unavailable, plain JSON only")
        seeds_path.write_text(_empty_text, encoding="utf-8")

    return seeds_path


def _seed_type_counts(seeds: list[dict[str, Any]]) -> dict[str, int]:
    """Count seeds by their seed_type."""
    counts: dict[str, int] = {}
    for s in seeds:
        t = s.get("task_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _derive_query_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: query_suggestion — derive next query seeds from sprint signal.

    Reads: signal_quality, reject_breakdown, accepted, ioc_density.

    Logic:
      - high_density + accepted > 0 → suggest more of the same queries (query refinement)
      - medium_density → suggest broadening scope
      - low_density / slow_novelty → suggest different query strategy
      - depleted → no query seeds (already tried hard, switch approach)
    """
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    ioc_density = pvs.get("ioc_density", 0.0)
    findings_per_minute = pvs.get("findings_per_minute", 0.0)
    reject_breakdown = pvs.get("reject_breakdown") or {}

    seeds: list[dict[str, Any]] = []

    # Low-information rejection ratio — if most rejects were low-info, queries may be too broad
    total_rejected = pvs.get("total_rejected", 0)
    low_info_rejected = reject_breakdown.get("low_information", 0)
    low_info_ratio = low_info_rejected / total_rejected if total_rejected > 0 else 0.0

    match signal:
        case "high_density":
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "refine",
                "priority": 0.75,
                "reason": f"signal=high_density/accepted={accepted}/ioc_density={ioc_density:.2f}",
            })
        case "medium_density":
            if low_info_ratio > 0.5:
                seeds.append({
                    "task_type": "query_suggestion",
                    "suggested_action": "narrow_scope",
                    "priority": 0.70,
                    "reason": f"low_info_ratio={low_info_ratio:.2f}/broad_queries",
                })
            else:
                seeds.append({
                    "task_type": "query_suggestion",
                    "suggested_action": "broaden",
                    "priority": 0.65,
                    "reason": f"signal=medium_density/ioc_density={ioc_density:.2f}",
                })
        case "slow_novelty":
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "accelerate",
                "priority": 0.60,
                "reason": f"signal=slow_novelty/fpm={findings_per_minute:.2f}",
            })
        case "depleted":
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "new_approach",
                "priority": 0.80,
                "reason": "signal=depleted/exhausted_query_space",
            })

    return seeds[:3]  # Hard cap: max 3 query suggestions


def _derive_source_revisit_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: source_revisit — domains/hosts that need re-visiting.

    Reads: cb_open_domains (circuit breaker open domains), signal_quality.

    Logic:
      - cb_open_domains → retry with longer backoff
      - depleted signal → revisit domains that previously timed out
    """
    seeds: list[dict[str, Any]] = []
    cb_open: list[str] = pvs.get("cb_open_domains") or []
    signal = pvs.get("_signal_quality_classification", "unknown")

    if cb_open:
        for domain in cb_open[:3]:  # Max 3 domains from circuit breaker
            seeds.append({
                "task_type": "source_revisit",
                "value": domain,
                "priority": 0.55,
                "reason": "circuit_breaker_open",
                "backoff_seconds": 3600,  # 1h backoff recommendation
            })
    elif signal == "depleted":
        # No cb state but depleted — suggest retrying known sources with backoff
        seeds.append({
            "task_type": "source_revisit",
            "suggested_action": "retry_known_sources",
            "priority": 0.50,
            "reason": "signal=depleted/retry_after_backoff",
        })

    return seeds[:3]


def _derive_low_signal_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: low_signal_recommendation — when sprint found almost nothing.

    Reads: accepted, total_rejected, findings_per_minute.

    Trigger: accepted <= 2 AND findings_per_minute < 0.5.

    Generates practical starting points for next sprint instead of
    continuing with same approach that yielded near-zero results.
    """
    accepted = pvs.get("accepted", 0)
    findings_per_minute = pvs.get("findings_per_minute", 0.0)
    total_rejected = pvs.get("total_rejected", 0)

    seeds: list[dict[str, Any]] = []

    if accepted <= 2 and findings_per_minute < 0.5 and total_rejected > 0:
        # Sprint was nearly empty — offer practical restart suggestions
        seeds.append({
            "task_type": "low_signal_recommendation",
            "suggested_action": "start_fresh",
            "priority": 0.70,
            "reason": f"accepted={accepted}/fpm={findings_per_minute:.2f}/near_empty_sprint",
        })
        # If dedup was effective but we still found nothing, sources may be exhausted
        if pvs.get("dedup_effective"):
            seeds.append({
                "task_type": "low_signal_recommendation",
                "suggested_action": "new_seed_sources",
                "priority": 0.65,
                "reason": "dedup_effective_but_depleted/switch_sources",
            })

    return seeds[:2]  # Hard cap: max 2 low-signal recommendations


# ---------------------------------------------------------------------------
# Sprint F226D §1: capability_synthesis-driven seeds
# ---------------------------------------------------------------------------

def _derive_capability_seeds(capability_synthesis: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    Sprint F226D: Derive seeds from capability_synthesis signals.

    Reads:
      - feed_noise_summary → nonfeed seed if feed_noisy_no_nonfeed_signal
      - source_diversity_summary → source diversity seed if weak
      - corroboration_summary → corroboration seed if weak/none
      - useful_evidence_present → quality seed if False
      - next_investigation_action → investigation seed

    All seeds have seed_source="capability_synthesis" for traceability.
    Bounded: max 4 capability-derived seeds.
    Fail-soft: returns [] for None input.
    """
    if not capability_synthesis:
        return []

    seeds: list[dict[str, Any]] = []

    feed_noise = capability_synthesis.get("feed_noise_summary", "unknown")
    source_diversity = capability_synthesis.get("source_diversity_summary", "unknown_source")
    corroboration = capability_synthesis.get("corroboration_summary", "none")
    evidence_present = capability_synthesis.get("useful_evidence_present", False)
    next_action = capability_synthesis.get("next_investigation_action", "")
    next_engineering = capability_synthesis.get("next_engineering_action", "")

    # 1. Feed noise → nonfeed evidence seed
    if feed_noise in ("feed_noisy_no_nonfeed_signal", "feed_dominant"):
        seeds.append({
            "task_type": "source_revisit",
            "suggested_action": "boost_nonfeed_lanes",
            "priority": 0.82,
            "reason": f"feed_noise={feed_noise}",
            "seed_source": "capability_synthesis",
            "expected_value": "nonfeed_signal_balance",
        })

    # 2. Weak source diversity → PUBLIC/CT/Wayback seed
    if source_diversity in ("single_source_feed_only", "single_source_niche", "unknown_source"):
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "expand_public_sources",
            "priority": 0.75,
            "reason": f"source_diversity={source_diversity}",
            "seed_source": "capability_synthesis",
            "expected_value": "multi_source_diversity",
        })

    # 3. Weak corroboration → corroboration seed
    if corroboration in ("none", "noisy"):
        seeds.append({
            "task_type": "corroboration_seed",
            "suggested_action": "seek_corroboration",
            "priority": 0.72,
            "reason": f"corroboration={corroboration}",
            "seed_source": "capability_synthesis",
            "expected_value": "cross_source_confirmation",
        })

    # 4. Weak evidence quality → quality improvement seed
    if not evidence_present:
        seeds.append({
            "task_type": "quality_seed",
            "suggested_action": "improve_evidence_quality",
            "priority": 0.70,
            "reason": "useful_evidence_present=false",
            "seed_source": "capability_synthesis",
            "expected_value": "actionable_findings",
        })

    # 5. Next investigation action → top-priority investigation seed
    if next_action and isinstance(next_action, str) and len(next_action) > 3:
        seeds.append({
            "task_type": "investigation_seed",
            "suggested_action": next_action,
            "priority": 0.88,
            "reason": f"next_investigation_action={next_action[:60]}",
            "seed_source": "capability_synthesis",
            "expected_value": "targeted_discovery",
        })

    # 6. Engineering action as lower-priority engineering seed
    if next_engineering and isinstance(next_engineering, str) and len(next_engineering) > 3:
        seeds.append({
            "task_type": "engineering_seed",
            "suggested_action": next_engineering,
            "priority": 0.55,
            "reason": f"next_engineering_action={next_engineering[:60]}",
            "seed_source": "capability_synthesis",
            "expected_value": "system_improvement",
        })

    return seeds[:4]  # Hard cap: max 4 capability-derived seeds


# ---------------------------------------------------------------------------
# Sprint F226D §2: analyst_brief-driven seeds
# ---------------------------------------------------------------------------

def _get_brief_field(obj: Any, field: str, default: Any = None) -> Any:
    """Get field from dict or dataclass/attr object (fallback: getattr)."""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _derive_analyst_brief_seeds(analyst_brief: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    Sprint F226D: Derive seeds from analyst_brief fields.

    Reads:
      - pivot_recommendations → pivot seeds (max 2)
      - evidence_gaps → gap-filling seeds (max 1)
      - risk_hypotheses → risk-investigation seeds (max 1)

    All seeds have seed_source="analyst_brief".
    Bounded: max 4 analyst-brief-derived seeds total.
    Fail-soft: returns [] for None input.
    """
    if not analyst_brief:
        return []
    seeds: list[dict[str, Any]] = []

    pivots = _get_brief_field(analyst_brief, "pivot_recommendations") or []
    if isinstance(pivots, (list, tuple)):
        for p in pivots[:2]:
            if isinstance(p, str) and len(p) > 3:
                seeds.append({
                    "task_type": "pivot_seed",
                    "suggested_action": p,
                    "priority": 0.78,
                    "reason": f"pivot_recommendation={p[:60]}",
                    "seed_source": "analyst_brief",
                    "expected_value": "pivot_discovery",
                })

    gaps = _get_brief_field(analyst_brief, "evidence_gaps") or []
    if isinstance(gaps, (list, tuple)) and gaps:
        gap = gaps[0]
        if isinstance(gap, str) and len(gap) > 3:
            seeds.append({
                "task_type": "gap_fill_seed",
                "suggested_action": f"address_gap: {gap[:80]}",
                "priority": 0.68,
                "reason": f"evidence_gap={gap[:60]}",
                "seed_source": "analyst_brief",
                "expected_value": "evidence_completeness",
            })

    risks = _get_brief_field(analyst_brief, "risk_hypotheses") or []
    if isinstance(risks, (list, tuple)) and risks:
        risk = risks[0]
        if isinstance(risk, str) and len(risk) > 3:
            seeds.append({
                "task_type": "risk_investigation_seed",
                "suggested_action": f"investigate_risk: {risk[:80]}",
                "priority": 0.65,
                "reason": f"risk_hypothesis={risk[:60]}",
                "seed_source": "analyst_brief",
                "expected_value": "risk_mitigation",
            })

    return seeds[:4]  # Hard cap: max 4 analyst-brief seeds


# ---------------------------------------------------------------------------
# Sprint F226D §3: seed deduplication
# ---------------------------------------------------------------------------

def _dedup_seeds(seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sprint F226D: Deduplicate seeds by (seed_source, task_type, suggested_action).

    seed_source is included in the key so capability_synthesis and analyst_brief
    seeds cannot dedup each other even with identical task_type+action.
    Preserves first-seen order (insertion order) among duplicates.
    Returns a new list; does not mutate input.
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for s in seeds:
        key = (
            s.get("seed_source", ""),
            s.get("task_type", ""),
            s.get("suggested_action", ""),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return deduped


def _type_aware_seeds(value: str, ioc_type: str, reason: str = "top_graph_node") -> list[dict[str, Any]]:
    """
    Sprint F500G §H2: Type-aware seed generation.

    Truthful mapping — generates seeds JEN kde typu odpovídá task_type.

    | ioc_type  | rdap_lookup | domain_to_ct | dht_infohash_lookup |
    |-----------|-------------|--------------|---------------------|
    | domain    | YES         | YES          | NO                  |
    | ip        | YES         | NO           | NO                  |
    | url       | YES         | NO           | NO                  |
    | infohash  | NO          | NO           | YES                 |
    | onion     | NO          | NO           | CONDITIONAL         |
    | cve       | NO          | NO           | NO                  |
    | md5/sha*  | NO          | NO           | NO                  |
    | unknown   | NO          | NO           | NO                  |

    Truthful skip: CVE, hash, unknown — žádné seed tasky, není co generovat.
    Willing to SKIP: není false-positive seed generation.
    """
    # Normalize ioc_type lowercase for matching
    match ioc_type.lower():
        case "domain":
            return [
                {
                    "task_type": "rdap_lookup",
                    "value": value,
                    "priority": 0.85,
                    "reason": f"{reason}/{ioc_type}",
                },
                {
                    "task_type": "domain_to_ct",
                    "value": value,
                    "priority": 0.80,
                    "reason": f"{reason}/{ioc_type}",
                },
            ]
        case "ip" | "ipv4" | "ipv6":
            return [
                {
                    "task_type": "rdap_lookup",
                    "value": value,
                    "priority": 0.85,
                    "reason": f"{reason}/{ioc_type}",
                },
            ]
        case "url":
            # URL has host component — RDAP lookup makes sense
            # domain_to_ct makes NO sense (URL is not a domain)
            return [
                {
                    "task_type": "rdap_lookup",
                    "value": value,
                    "priority": 0.80,
                    "reason": f"{reason}/{ioc_type}",
                },
            ]
        case "infohash":
            return [
                {
                    "task_type": "dht_infohash_lookup",
                    "value": value,
                    "priority": 0.90,
                    "reason": f"{reason}/{ioc_type}",
                },
            ]
        case "onion":
            # Onion is not a DNS domain — no domain_to_ct
            # DHT lookup is marginally relevant (some Tor research uses DHT)
            # but skip entirely to be safe — no strong signal
            return []
        case ("cve" | "md5" | "sha1" | "sha256" | "sha512" | "sha384"
              | "md6" | "ripemd160" | "unknown" | "email" | "phone"
              | "ipv4_addr" | "ipv6_addr" | "mac_addr" | "btc" | "eth"
              | "xmpp" | "jabber"):
            # Truthful skip — these types have no meaningful follow-up seed
            # CVE: vuln ID not a network observable
            # Hashes: not domains, not infohashes, not IPs
            # Unknown: no valid seeds possible
            return []
        case _:
            # Catch-all for any other type not explicitly handled:
            # generate NO seeds — better to skip than to generate falsy task
            return []


def _build_product_value_summary(
    store: Any,
    eh: "ExportHandoff",  # type: ignore[name-defined]
    sprint_id: str,
) -> dict[str, Any]:
    """
    Sprint F150I §1: product_value_summary — agreguje truth surfaces do jednoho
    rozhodovacího balíčku pro další sprinty.

    ZDROJE (existující surfaces, žádné nové):
      1. eh.scorecard — windup output (findings_per_minute, ioc_density,
         semantic_novelty, accepted_findings, peak_rss_mb, phase_timings)
      2. store.get_dedup_runtime_status() — accepted vs rejected by reason
         (Sprint 8AV extended: low-info / in-memory-dup / persistent-dup / fail-open)
      3. eh.scorecard["cb_open_domains"] — circuit breaker state
      4. eh.gnn_predictions — ML model signal (0 pokud nepoužit)
      5. eh.phase_durations — timing truth

    DEGRADED MODE: pokud store není dostupný, pole jsou None — není to chyba,
    je to expected degraded state pro standalone/test scénáře.

    JE TO DERIVED OUTPUT, NE NOVÝ TRUTH STORE:
      - Žádné nové write API
      - Žádné nové history mechanismy
      - Pouze čte z existujících surfaces a skládá je dohromady
    """
    scorecard = eh.scorecard if eh.scorecard else {}

    # 1. Základní scorecard facts
    # Sprint F192F §1: use module-level _pvs_num / _pvs_n helpers
    # (previously local _num / _n closures — now consolidated at module scope)
    # [F230A] runtime_truth.accepted_findings is the authoritative canonical truth
    # from __main__._runtime_truth() — use it as the PRIMARY source. Scorecard
    # runtime_accepted_findings is a duplicate that can be absent or stale (0) even
    # when canonical runtime_truth.accepted_findings > 0 (F229B residual bug).
    # Priority: runtime_truth.accepted_findings > scorecard.runtime_accepted_findings
    # > scorecard.accepted_findings (legacy fallback for scorecard-only builds).
    runtime_truth: dict[str, Any] = eh.runtime_truth or {}
    _rt_accepted = _pvs_n(runtime_truth, "accepted_findings", 0)
    runtime_accepted_findings = _pvs_n(scorecard, "runtime_accepted_findings", 0)
    if _rt_accepted > 0:
        runtime_accepted_findings = _rt_accepted
    accepted = runtime_accepted_findings
    if accepted == 0:
        accepted = _pvs_n(scorecard, "accepted_findings", 0)
        if accepted > 0:
            runtime_accepted_findings = accepted
    findings_per_minute = _pvs_n(scorecard, "findings_per_minute", 0.0)
    ioc_density = _pvs_n(scorecard, "ioc_density", 0.0)
    peak_rss_mb = scorecard.get("peak_rss_mb", None)
    if peak_rss_mb is not None and not isinstance(peak_rss_mb, (int, float)):
        peak_rss_mb = None
    phase_timings = scorecard.get("phase_duration_seconds", {}) or {}
    actual_duration = phase_timings.get("WINDUP", 0.0) or phase_timings.get("TEARDOWN", 0.0)

    # [F223D] runtime_findings_per_minute: computed from all-lanes runtime total.
    # Only meaningful when runtime_accepted_findings > 0. For scorecard-only / legacy
    # test builds (accepted=0 from runtime field), leave as 0.0 — original
    # scorecard findings_per_minute is preserved as-is for those builds.
    if runtime_accepted_findings > 0 and actual_duration > 0:
        runtime_findings_per_minute = round(runtime_accepted_findings / (actual_duration / 60.0), 2)
    else:
        runtime_findings_per_minute = 0.0
    # [F241] Fallback: if scorecard findings_per_minute is 0.0 but we have valid runtime
    # data, compute from runtime values so PVS doesn't show 0.0 for a productive sprint.
    if findings_per_minute == 0.0 and runtime_findings_per_minute > 0:
        findings_per_minute = runtime_findings_per_minute
    # [F221C] Fallback: if runtime_findings_per_minute is 0.0 because actual_duration
    # was unavailable (empty phase_timings) but findings_per_minute is already valid,
    # propagate it so runtime_findings_per_minute doesn't show 0.0 for a productive sprint.
    if runtime_findings_per_minute == 0.0 and findings_per_minute > 0:
        runtime_findings_per_minute = findings_per_minute

    # 2. Dedup status — Sprint 8AV extended ingest outcome counters
    dedup_status: dict[str, Any] | None = None
    if store is not None:
        try:
            if hasattr(store, "get_dedup_runtime_status"):
                raw = store.get_dedup_runtime_status()
                # Sprint F150I §6: guard against MagicMock / non-dict returns
                if isinstance(raw, dict):
                    dedup_status = raw
        except Exception:
            pass

    if dedup_status:
        # Sprint F192F §1: accepted_count from dedup_status is RUNTIME STATE (in-memory
        # counter, not persisted fact). Scorecard accepted_findings is the authoritative
        # persisted fact. Only use dedup_status accepted_count as secondary when
        # scorecard has no accepted_findings (e.g., scorecard-only builds).
        # Previously: dedup_status.accepted_count OVERRODE scorecard fact — DF-1 drift.
        _dedup_accepted = dedup_status.get("accepted_count", 0)
        accepted = accepted if accepted > 0 else _dedup_accepted
        reject_breakdown = {
            "low_information": dedup_status.get("low_information_rejected_count", 0),
            "in_memory_duplicate": dedup_status.get("in_memory_duplicate_rejected_count", 0),
            "persistent_duplicate": dedup_status.get("persistent_duplicate_rejected_count", 0),
            "fail_open": dedup_status.get("other_rejected_count", 0),
            # Sprint F250E: Security gate
            "security_rejected": scorecard.get("security_rejected_count", 0) or 0,
            "pii_redacted": scorecard.get("pii_redacted_count", 0) or 0,
        }
        total_rejected = sum(reject_breakdown.values())
        dedup_effective = dedup_status.get("persistent_dedup_enabled", False)
        dedup_lmdb_path = dedup_status.get("dedup_lmdb_path", "")
        hot_cache = {
            "size": dedup_status.get("hot_cache_size", 0),
            "capacity": dedup_status.get("hot_cache_capacity", 0),
        }
    else:
        # Sprint F250E: Even when dedup_status is absent, preserve security gate counters
        reject_breakdown = {
            "low_information": 0,
            "in_memory_duplicate": 0,
            "persistent_duplicate": 0,
            "fail_open": 0,
            "security_rejected": scorecard.get("security_rejected_count", 0) or 0,
            "pii_redacted": scorecard.get("pii_redacted_count", 0) or 0,
        }
        total_rejected = reject_breakdown["security_rejected"]  # security is the only real count here
        dedup_effective = None
        dedup_lmdb_path = None
        hot_cache = None

    # 3. Circuit breaker state
    cb_open_domains = scorecard.get("cb_open_domains", []) or []

    # 4. GNN predictions
    gnn_predictions = eh.gnn_predictions if eh.gnn_predictions else 0

    # 5. Synthesis engine
    synthesis_engine = eh.synthesis_engine if eh.synthesis_engine else (
        scorecard.get("synthesis_engine_used", "unknown") or "unknown"
    )

    # F214-ACQ: Feed dominance ratio and nonfeed diagnostic recommendation
    # Computed from source_family_outcomes for full fidelity (all lanes, not just branch_mix).
    # source_family_outcomes lives in eh.scorecard (spread there via _scheduler_result_acquisition_payload
    # in __main__.py run_sprint).
    _sfo_list = scorecard.get("source_family_outcomes", []) if isinstance(scorecard, dict) else []
    _feed_entry = next((e for e in _sfo_list if isinstance(e, dict) and e.get("family") == "feed"), None)
    _nonfeed_entries = [e for e in _sfo_list if isinstance(e, dict) and e.get("family") != "feed" and e.get("attempted")]
    _feed_accepted = (_feed_entry.get("accepted_count") or 0) if _feed_entry else 0
    _nonfeed_accepted = sum((e.get("accepted_count") or 0) for e in _nonfeed_entries)
    _total_accepted = _feed_accepted + _nonfeed_accepted
    feed_dominance_ratio = (_feed_accepted / _total_accepted) if _total_accepted > 0 else None
    should_recommend_nonfeed_diagnostic = (
        feed_dominance_ratio is not None
        and feed_dominance_ratio > 0.95
        and _nonfeed_accepted < 5
    )

    # Sprint F178C: signal_quality renamed to _signal_quality_classification
    # PRECISE SEPARATION of FACTS vs DERIVED:
    # - FACTS (raw data from scorecard/store): accepted, reject_breakdown, total_rejected,
    #   findings_per_minute, ioc_density, peak_rss_mb, phase_durations, cb_open_domains,
    #   gnn_predictions, synthesis_engine, dedup_effective, dedup_lmdb_path, hot_cache
    # - DERIVED (computed from facts): _signal_quality_classification
    #   NOTE: _prefix means "derived classification, not raw fact"
    if accepted > 0 and findings_per_minute > 0:
        if ioc_density >= 0.5:
            _signal_quality = "high_density"
        elif ioc_density >= 0.2:
            _signal_quality = "medium_density"
        else:
            _signal_quality = "low_density"
    elif accepted > 0 and findings_per_minute > 0 and ioc_density < 0.2:
        _signal_quality = "slow_novelty"
    elif accepted == 0 and dedup_status:
        _signal_quality = "depleted"
    else:
        _signal_quality = "unknown"

    summary: dict[str, Any] = {
        "sprint_id": sprint_id,
        # FACTS — raw data from scorecard/store
        # [F223D] Renamed+aliased: accepted was ambiguous (FEED-lane-only in scorecard).
        # runtime_accepted_findings is the authoritative full-truth total at windup
        # (all lanes + ct_log_stored). accepted is kept as alias for backward compat.
        "runtime_accepted_findings": runtime_accepted_findings,
        "accepted": runtime_accepted_findings,
        "reject_breakdown": reject_breakdown,
        "total_rejected": total_rejected,
        # [F223D] runtime_findings_per_minute is computed from runtime_accepted_findings
        # and actual WINDUP/TEARDOWN phase duration — matches runtime truth rate.
        # findings_per_minute reflects the scorecard field which may be 0.0 when scorecard
        # only captured FEED-lane findings; runtime_findings_per_minute is the trustworthy
        # per-minute rate based on all lanes.
        "runtime_findings_per_minute": runtime_findings_per_minute,
        "findings_per_minute": findings_per_minute,
        "ioc_density": ioc_density,
        "peak_rss_mb": peak_rss_mb,
        "phase_durations": phase_timings if phase_timings else None,
        "cb_open_domains": cb_open_domains,
        "gnn_predictions": gnn_predictions,
        "synthesis_engine": synthesis_engine,
        "dedup_effective": dedup_effective,
        "dedup_lmdb_path": dedup_lmdb_path,
        "hot_cache": hot_cache,
        # F193B: Archive + academic discovery contribution surfaces
        "commoncrawl_archive_augmented": (eh.canonical_run_summary.get("cc_archive_injected", 0) if eh.canonical_run_summary else None) or scorecard.get("cc_archive_injected", 0),
        "academic_discovery_contribution": (eh.canonical_run_summary.get("academic_findings_count", 0) if eh.canonical_run_summary else None) or scorecard.get("academic_findings_count", 0),
        # Sprint F204F: Production CTI scorecard enrichment fields
        "attribution": eh.canonical_run_summary.get("attribution") if eh.canonical_run_summary else None,
        "wayback_diff": eh.canonical_run_summary.get("wayback_diff") if eh.canonical_run_summary else None,
        "embedding": eh.canonical_run_summary.get("embedding") if eh.canonical_run_summary else None,
        "hypothesis_feedback": eh.canonical_run_summary.get("hypothesis_feedback") if eh.canonical_run_summary else None,
        "circuit_state": eh.canonical_run_summary.get("circuit_state") if eh.canonical_run_summary else scorecard.get("circuit_state"),
        # F214-ACQ: Feed dominance and nonfeed diagnostic signals
        "feed_dominance_ratio": round(feed_dominance_ratio, 4) if feed_dominance_ratio is not None else None,
        "should_recommend_nonfeed_diagnostic": should_recommend_nonfeed_diagnostic,
        # DERIVED — computed from facts (prefix _ = classification, not raw fact)
        "_signal_quality_classification": _signal_quality,
        # F229B: Lane corroboration score from src_family_outcomes
        "corroboration_score": _corroboration_score_value(scorecard),
        "corroborating_families": _corroborating_families(scorecard),
        "corroboration_reason": _corroboration_reason_str(scorecard),
        "corroboration_penalties": _corroboration_penalties_list(scorecard),
        # F231A: Terminal coverage (distinct from positive corroboration)
        "lane_terminal_coverage_score": _terminal_coverage_score(scorecard),
        "terminal_families": _terminal_families(scorecard),
        "terminal_coverage_reason": _terminal_coverage_reason_str(scorecard),
        # F232C: Provider yield signals from existing provider debug surfaces
        **_compute_provider_yield_signals(scorecard),
        # F251E: Enrichment value delta — sidecar IOC yield measurement
        **_build_enrichment_value_delta(scorecard, accepted),
    }

    # Remove None fields for cleaner output (keep 0 as valid)
    return {k: v for k, v in summary.items() if v is not None}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# F251E: Enrichment Value Delta
# Measures how much new IOC value sidecars added vs. raw input findings.
# Canonical read-only seam — no network, no model, no new store API.
# ---------------------------------------------------------------------------

def _build_enrichment_value_delta(scorecard: dict, input_accepted: int) -> dict:
    """
    Sprint F251E: Build enrichment_value_delta from scorecard surfaces.

    Measures sidecar IOC yield: unique new domains/IPs extracted by sidecars
    vs. the raw accepted findings fed into the sidecar bus.

    Sources (all read-only, no network/model):
      - scorecard.source_family_outcomes (sidecar family outcomes)
      - scorecard.graph_signal (nodes/edges delta from graph_accumulator)
      - scorecard.next_seeds_from_enrichment (enrichment-seeded next seeds count)

    Verdict rules:
      - no_input: input_accepted == 0
      - no_enrichment_yield: input > 0 but zero sidecar stored findings
      - low_enrichment_yield: sidecars ran but yielded no unique domains/IPs
      - useful_enrichment_yield: sidecars produced unique domains or IPs

    Graph delta: surfaced as null with reason when scorecard.graph_signal absent.

    Bounds: sample lists capped at 20 items to prevent report bloat.
    """
    evd: dict[str, Any] = {
        "input_accepted_findings_count": input_accepted,
        "sidecar_stored_findings_count": 0,
        "sidecar_source_families": [],
        "new_unique_iocs_from_sidecars": 0,
        "new_unique_domains_from_sidecars": 0,
        "new_unique_ips_from_sidecars": 0,
        "graph_nodes_added": None,
        "graph_edges_added": None,
        "next_seeds_from_enrichment_count": 0,
        "enrichment_value_ratio": 0.0,
        "verdict": "no_input",
    }

    if input_accepted == 0:
        evd["verdict"] = "no_input"
        return evd

    # ── Sidecar outcomes from source_family_outcomes ─────────────────────────
    sfo_list: list[dict] = scorecard.get("source_family_outcomes", []) if isinstance(scorecard, dict) else []

    # Collect sidecar families (non-feed families that attempted and stored findings)
    sidecar_families: list[str] = []
    total_stored = 0
    sample_domains: list[str] = []
    sample_ips: list[str] = []

    for entry in sfo_list:
        if not isinstance(entry, dict):
            continue
        fam = entry.get("family", "")
        # Sidecar families are non-feed families that represent enrichment runners
        if fam in ("feed", ""):
            continue
        attempted = entry.get("attempted", False)
        if not attempted:
            continue
        stored = entry.get("stored_count", 0) or 0
        total_stored += stored
        if stored <= 0:
            continue
        if fam not in sidecar_families:
            sidecar_families.append(fam)
        # Collect sample IOCs from family entries (bound at 20 per type)
        for dom in (entry.get("sample_domains", []) or [])[:20]:
            if dom and dom not in sample_domains:
                sample_domains.append(dom)
        for ip in (entry.get("sample_ips", []) or [])[:20]:
            if ip and ip not in sample_ips:
                sample_ips.append(ip)

    evd["sidecar_stored_findings_count"] = total_stored
    evd["sidecar_source_families"] = sidecar_families
    evd["new_unique_domains_from_sidecars"] = len(sample_domains)
    evd["new_unique_ips_from_sidecars"] = len(sample_ips)
    evd["new_unique_iocs_from_sidecars"] = len(sample_domains) + len(sample_ips)

    if total_stored == 0:
        evd["verdict"] = "no_enrichment_yield"
        evd["enrichment_value_ratio"] = 0.0
        return evd

    # ── Graph delta from scorecard.graph_signal ─────────────────────────────
    gs: dict | None = scorecard.get("graph_signal") if isinstance(scorecard, dict) else None
    if isinstance(gs, dict):
        evd["graph_nodes_added"] = gs.get("nodes") or gs.get("graph_nodes") or 0
        evd["graph_edges_added"] = gs.get("edges") or gs.get("graph_edges") or 0
    else:
        evd["graph_nodes_added"] = None
        evd["graph_edges_added"] = None

    # ── Next seeds from enrichment ───────────────────────────────────────────
    nse = scorecard.get("next_seeds_from_enrichment", 0) if isinstance(scorecard, dict) else 0
    evd["next_seeds_from_enrichment_count"] = max(0, int(nse))

    # ── Verdict based on unique IOC yield ─────────────────────────────────────
    unique_iocs = evd["new_unique_iocs_from_sidecars"]
    evd["enrichment_value_ratio"] = round(unique_iocs / max(input_accepted, 1), 4)

    if unique_iocs == 0:
        evd["verdict"] = "low_enrichment_yield"
    else:
        evd["verdict"] = "useful_enrichment_yield"

    return evd


# ---------------------------------------------------------------------------
# F254C: Engineering Action Map
# Maps provider_yield_diagnosis + enrichment_value_delta to a deterministic
# engineering action recommendation for the sprint report.
# Canonical read-only seam — no network, no model, no new store API.
# ---------------------------------------------------------------------------

def _build_engineering_action_map(
    pyd: dict[str, Any] | None,
    evd: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Sprint F254C: Build engineering_action_map from pyd + evd.

    Returns a compact recommendation dict with keys:
      - primary_action: str  (bounded action name)
      - reason: str          (one-line explanation)
      - target_area: str     (provider_selection|seed_quality|provider_yield|
                               enrichment|storage|none)
      - confidence: float    (0.0-1.0)

    Rules (in priority order):
      1. pyd.overall == "no_positive_nonfeed_yield" and evd.verdict == "no_input"
         → improve_nonfeed_provider_yield / provider_yield
      2. pyd.public.status == "error_or_zero" and "no_provider_selected" in pyd.public.reason
         → fix_public_provider_selection / provider_selection
      3. provider returned zero but was selected (non-skipped)
         → add_or_use_provider_replay_fixture / provider_yield
      4. evd.verdict == "useful_enrichment_yield"
         → continue_pivot_expansion / enrichment
      5. pyd.overall == "nonfeed_successful" and evd.verdict in ("no_enrichment_yield", "low_enrichment_yield")
         → improve_sidecar_input_or_mapping / enrichment
      6. evd.verdict == "no_enrichment_yield" and input_accepted > 0
         → improve_sidecar_input_or_mapping / enrichment
      7. Otherwise → none / none

    Bounds:
      - action names: improve_nonfeed_provider_yield | fix_public_provider_selection |
                     add_or_use_provider_replay_fixture | continue_pivot_expansion |
                     improve_sidecar_input_or_mapping | none
      - target_area: provider_selection | seed_quality | provider_yield | enrichment | storage | none
      - confidence: 0.0-1.0, rounded to 2 decimal places
    """
    if pyd is None and evd is None:
        return {"primary_action": "none", "reason": "no diagnosis data available", "target_area": "none", "confidence": 0.0}

    pyd = pyd if isinstance(pyd, dict) else {}
    evd = evd if isinstance(evd, dict) else {}

    pyd_overall = pyd.get("overall", "")
    evd_verdict = evd.get("verdict", "")
    evd_input = evd.get("input_accepted_findings_count", 0)

    public_diag = pyd.get("families", {}).get("public", {}) if isinstance(pyd.get("families"), dict) else {}
    public_status = public_diag.get("status", "") if isinstance(public_diag, dict) else ""
    public_reason = public_diag.get("reason", "") if isinstance(public_diag, dict) else ""

    # Rule 1: no_positive_nonfeed_yield + no_input verdict
    # Only fires when public has no specific provider error (status != error_or_zero).
    # Rules 2/3 take priority for specific provider errors.
    if pyd_overall == "no_positive_nonfeed_yield" and evd_verdict == "no_input":
        if public_status != "error_or_zero":
            return {
                "primary_action": "improve_nonfeed_provider_yield",
                "reason": "zero nonfeed yield with no input accepted — provider yield is the bottleneck",
                "target_area": "provider_yield",
                "confidence": 0.85,
            }
        # public has error_or_zero — let Rules 2/3 handle it

    # Rule 2: public no_provider_selected
    if public_status == "error_or_zero" and "no_provider_selected" in str(public_reason):
        return {
            "primary_action": "fix_public_provider_selection",
            "reason": "public provider was selected but returned zero — check provider bootstrap",
            "target_area": "provider_selection",
            "confidence": 0.90,
        }

    # Rule 3: provider returned zero but was attempted (not skipped)
    if public_status == "error_or_zero" and ("provider_returned_zero" in str(public_reason) or "provider_unavailable" in str(public_reason)):
        return {
            "primary_action": "add_or_use_provider_replay_fixture",
            "reason": "provider returned zero or unavailable — replay fixture needed for diagnostics",
            "target_area": "provider_yield",
            "confidence": 0.80,
        }

    # Rule 4: useful enrichment yield
    if evd_verdict == "useful_enrichment_yield":
        return {
            "primary_action": "continue_pivot_expansion",
            "reason": "enrichment sidecars produced unique IOCs — pivot expansion is working",
            "target_area": "enrichment",
            "confidence": 0.80,
        }

    # Rule 5: nonfeed successful but no enrichment yield
    if pyd_overall == "nonfeed_successful" and evd_verdict in ("no_enrichment_yield", "low_enrichment_yield"):
        return {
            "primary_action": "improve_sidecar_input_or_mapping",
            "reason": "nonfeed lanes succeeded but sidecars yielded no unique IOCs — improve input mapping",
            "target_area": "enrichment",
            "confidence": 0.75,
        }

    # Rule 6: no enrichment yield with input accepted
    if evd_verdict == "no_enrichment_yield" and evd_input > 0:
        return {
            "primary_action": "improve_sidecar_input_or_mapping",
            "reason": "input accepted but sidecars stored zero — improve sidecar input or IOC mapping",
            "target_area": "enrichment",
            "confidence": 0.75,
        }

    # Rule 7: default — no action needed
    return {
        "primary_action": "none",
        "reason": "no actionable engineering signal detected",
        "target_area": "none",
        "confidence": 0.50,
    }


# ---------------------------------------------------------------------------
# F260A: Expected Evidence Contract
# Compares provider_yield_diagnosis against what was expected for the
# mission intent + seed classes. Read-only seam — no network, no model.
# ---------------------------------------------------------------------------

def _build_expected_evidence(
    intent: str,
    pyd: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Sprint F260A: Build expected_evidence contract from intent + pyd.

    Returns a compact contract dict with keys:
      - intent: str                          (canonical intent name)
      - expected_families: list[str]         (source families expected to yield)
      - minimum_success: str                 (pass/fail/minimal/partial)
      - missing_critical: list[str]           (families that failed critically)
      - unexpected_skipped: list[str]         (families skipped unexpectedly)
      - contract_status: str                  (met | partial | unmet | no_strict_expectation)

    Contract rules per intent:

      domain_infrastructure:
        expected_families = ["public", "ct", "passive_dns", "wayback"]
        minimum: any(["ct", "passive_dns", "rdap"]) or next_seeds > 0
        missing_critical if none of ct/pdns succeed and next_seeds == 0

      malware_family:
        expected_families = ["public"]
        minimum: public yields candidate domains OR next_seeds > 0

      vulnerability:
        expected_families = ["public", "ct", "wayback"]
        minimum: public or ct yields CVEs/IDs

      cve_recon:
        expected_families = ["public", "ct", "wayback"]
        minimum: any public or ct yields CVE indicators

      wallet_recon:
        expected_families = ["public", "ct"]
        minimum: any yields blockchain-adjacent indicators

      unknown / other:
        no_strict_expectation — pass through

    Bounds:
      - expected_families: bounded set per intent (see above)
      - minimum_success: pass | fail | minimal | partial | no_strict_expectation
      - contract_status: met | partial | unmet | no_strict_expectation
      - All lists bounded; empty lists when nothing missing.
    """
    # Guard: fail-soft for None/non-dict pyd (empty families dict is valid — processes as unmet)
    if pyd is None or not isinstance(pyd, dict):
        return {
            "intent": intent,
            "expected_families": [],
            "minimum_success": "no_strict_expectation",
            "missing_critical": [],
            "unexpected_skipped": [],
            "contract_status": "no_strict_expectation",
        }
    families = pyd.get("families", {}) if isinstance(pyd.get("families"), dict) else {}

    # Canonical family status helper
    def _family_status(name: str) -> str:
        entry = families.get(name, {}) if isinstance(families, dict) else {}
        return entry.get("status", "unknown") if isinstance(entry, dict) else "unknown"

    def _family_accepted(name: str) -> int:
        entry = families.get(name, {}) if isinstance(families, dict) else {}
        return entry.get("accepted_count", 0) or 0 if isinstance(entry, dict) else 0

    def _family_attempted(name: str) -> bool:
        entry = families.get(name, {}) if isinstance(families, dict) else {}
        return entry.get("attempted", False) if isinstance(entry, dict) else False

    # ----- domain_infrastructure -----
    if intent == "domain_recon":
        expected = ["public", "ct", "passive_dns", "wayback"]
        success_families = [f for f in expected if _family_status(f) == "successful"]
        attempted_families = [f for f in expected if _family_attempted(f)]

        # minimum_success: any of ct/pdns succeed OR next_seeds > 0
        # We only know seed_classes here; next_seeds is not available in this helper
        # so we check for at least ct or pdns success
        minimum_met = len([f for f in ("ct", "passive_dns") if _family_status(f) == "successful"]) > 0

        missing = [f for f in expected if _family_status(f) in ("error_or_zero", "attempted_empty")]
        unexpected_skipped = [f for f in expected if _family_status(f) == "skipped" and f in attempted_families]

        if minimum_met:
            contract_status = "met"
        elif success_families:
            contract_status = "partial"
        else:
            contract_status = "unmet"

        return {
            "intent": intent,
            "expected_families": expected,
            "minimum_success": "partial" if success_families else "fail",
            "missing_critical": missing,
            "unexpected_skipped": unexpected_skipped,
            "contract_status": contract_status,
        }

    # ----- malware_family / wallet_recon -----
    if intent in ("malware_family", "wallet_recon"):
        expected = ["public"] + (["ct"] if intent == "wallet_recon" else [])
        public_status = _family_status("public")

        # minimum: public yields candidate domains (accepted > 0)
        public_accepted = _family_accepted("public")

        missing = [f for f in expected if _family_status(f) in ("error_or_zero", "attempted_empty")]
        unexpected_skipped = [f for f in expected if _family_status(f) == "skipped" and _family_attempted(f)]

        if public_accepted > 0:
            contract_status = "met"
            minimum = "pass"
        else:
            contract_status = "unmet"
            minimum = "fail"

        return {
            "intent": intent,
            "expected_families": expected,
            "minimum_success": minimum,
            "missing_critical": missing,
            "unexpected_skipped": unexpected_skipped,
            "contract_status": contract_status,
        }

    # ----- vulnerability / cve_recon -----
    if intent in ("cve_recon", "vulnerability"):
        expected = ["public", "ct", "wayback"]
        success_families = [f for f in expected if _family_status(f) == "successful"]

        missing = [f for f in expected if _family_status(f) in ("error_or_zero", "attempted_empty")]
        unexpected_skipped = [f for f in expected if _family_status(f) == "skipped" and _family_attempted(f)]

        if success_families:
            contract_status = "met"
        else:
            contract_status = "unmet"

        return {
            "intent": intent,
            "expected_families": expected,
            "minimum_success": "pass" if success_families else "fail",
            "missing_critical": missing,
            "unexpected_skipped": unexpected_skipped,
            "contract_status": contract_status,
        }

    # ----- org_recon / person_recon / unknown -----
    # No strict expectation — defer to other signals
    return {
        "intent": intent,
        "expected_families": [],
        "minimum_success": "no_strict_expectation",
        "missing_critical": [],
        "unexpected_skipped": [],
        "contract_status": "no_strict_expectation",
    }


# ---------------------------------------------------------------------------
# F229B/F230E: Lane corroboration score helpers
# ---------------------------------------------------------------------------

def _get_corrob_outcomes(scorecard: dict) -> dict:
    """Normalize lane outcomes from either src_family_outcomes or source_family_outcomes.

    Live sprint reports write ``source_family_outcomes`` as a list of dicts with
    ``family`` and lane data keys (terminal_state, accepted_count, raw_count, etc.).
    F229B helpers historically read only ``src_family_outcomes`` as a flat dict keyed
    by family name.

    This function accepts both shapes and returns a flat dict keyed by family name,
    matching what ``runtime.corroboration_score.score_from_result`` expects.

    Normalisation rules
    -------------------
    1. If ``src_family_outcomes`` is a non-empty dict, return it directly (F229B compat).
    2. Otherwise, read ``source_family_outcomes`` as a list and index by ``family``.
    3. On any failure, return ``{}`` (fail-soft).
    """
    # 1. Prefer src_family_outcomes dict (F229B shape)
    sfo = scorecard.get("src_family_outcomes")
    if isinstance(sfo, dict) and sfo:
        return sfo

    # 2. Normalise source_family_outcomes list (live shape)
    try:
        sfo_list = scorecard.get("source_family_outcomes", [])
        if not isinstance(sfo_list, list):
            return {}
        out = {}
        for entry in sfo_list:
            if isinstance(entry, dict):
                fam = entry.get("family")
                if fam and isinstance(fam, str):
                    out[fam.lower()] = entry
        return out
    except Exception:
        return {}

def _corroboration_score_value(scorecard: dict) -> float:
    """Compute corroboration score (0.0-1.0) from src_family_outcomes or source_family_outcomes."""
    from hledac.universal.runtime.corroboration_score import score_from_result
    outcomes = _get_corrob_outcomes(scorecard)

    class _Result:
        __slots__ = ("src_family_outcomes", "seed_context_available")
        def __init__(self, outcomes):
            self.src_family_outcomes = outcomes
            self.seed_context_available = False

    result = _Result(outcomes)
    try:
        sc = score_from_result(result)
        return sc.corroboration_score
    except Exception:
        return 0.0


def _corroborating_families(scorecard: dict) -> tuple:
    """Return tuple of families that contributed to corroboration."""
    from hledac.universal.runtime.corroboration_score import score_from_result
    outcomes = _get_corrob_outcomes(scorecard)

    class _Result:
        __slots__ = ("src_family_outcomes", "seed_context_available")
        def __init__(self, outcomes):
            self.src_family_outcomes = outcomes
            self.seed_context_available = False

    result = _Result(outcomes)
    try:
        sc = score_from_result(result)
        return sc.corroborating_families
    except Exception:
        return ()


def _corroboration_reason_str(scorecard: dict) -> str:
    """Return human-readable corroboration reason."""
    from hledac.universal.runtime.corroboration_score import score_from_result
    outcomes = _get_corrob_outcomes(scorecard)

    class _Result:
        __slots__ = ("src_family_outcomes", "seed_context_available")
        def __init__(self, outcomes):
            self.src_family_outcomes = outcomes
            self.seed_context_available = False

    result = _Result(outcomes)
    try:
        sc = score_from_result(result)
        return sc.corroboration_reason
    except Exception:
        return "corroboration unavailable"


def _corroboration_penalties_list(scorecard: dict) -> list:
    """Return list of active penalties."""
    from hledac.universal.runtime.corroboration_score import _NONFEED_FAMILIES, _TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS
    outcomes = _get_corrob_outcomes(scorecard)
    penalties = []

    feed_present = bool(outcomes.get("feed", {}).get("accepted_count", 0) > 0)
    nonfeed_terminals = sum(
        1 for f in _NONFEED_FAMILIES
        if outcomes.get(f, {}).get("terminal_state") in (_TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS)
    )
    nonfeed_missed = all(
        outcomes.get(f, {}).get("terminal_state") not in (_TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS)
        for f in _NONFEED_FAMILIES
    )
    if not feed_present and nonfeed_missed and nonfeed_terminals == 0:
        penalties.append("nonfeed_expected_missing")

    if feed_present and nonfeed_terminals == 0:
        ct_t = outcomes.get("ct", {}).get("terminal_state") not in (_TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS)
        doh_t = outcomes.get("doh", {}).get("terminal_state") not in (_TERMINAL_COMPLETED, _TERMINAL_NO_RESULTS)
        if ct_t and doh_t:
            penalties.append("feed_only_no_nonfeed")

    if outcomes.get("public", {}).get("terminal_state") == _TERMINAL_NO_RESULTS:
        penalties.append("public_zero_results")

    return penalties


# F231A: Terminal coverage helpers (distinct from positive corroboration)
def _terminal_coverage_score(scorecard: dict) -> float:
    """Compute terminal coverage score (0.0–1.0) from lane outcomes.

    Terminal coverage counts ATTEMPTED_ERROR / ATTEMPTED_TIMEOUT as "covered"
    because the lane was planned and attempted — it is not absent/silent.
    This is separate from corroboration_score which only rewards positive outcomes.
    """
    from hledac.universal.runtime.corroboration_score import compute_terminal_coverage
    outcomes = _get_corrob_outcomes(scorecard)
    try:
        tc = compute_terminal_coverage(outcomes)
        return tc.terminal_coverage_score
    except Exception:
        return 0.0


def _terminal_families(scorecard: dict) -> tuple:
    """Return families that reached terminal/attempted state."""
    from hledac.universal.runtime.corroboration_score import compute_terminal_coverage
    outcomes = _get_corrob_outcomes(scorecard)
    try:
        tc = compute_terminal_coverage(outcomes)
        return tc.terminal_families
    except Exception:
        return ()


def _terminal_coverage_reason_str(scorecard: dict) -> str:
    """Return human-readable terminal coverage reason."""
    from hledac.universal.runtime.corroboration_score import compute_terminal_coverage
    outcomes = _get_corrob_outcomes(scorecard)
    try:
        tc = compute_terminal_coverage(outcomes)
        return tc.terminal_coverage_reason
    except Exception:
        return "terminal coverage unavailable"


def _compute_provider_yield_signals(
    scorecard: dict,
    doh_provider_errors: tuple[str, ...] | None = None,
    public_provider_errors: list[dict] | None = None,
    nonfeed_missing_expected_lanes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sprint F232C: Provider yield signals from existing provider debug surfaces.

    Derives provider yield diagnostics from the union of:
      - scorecard["source_family_outcomes"]
      - doh_provider_errors (tuple of provider error strings)
      - public_provider_errors (list of {family, error, error_type} dicts)
      - nonfeed_missing_expected_lanes (list of family names)

    NO network. NO model. NO new dependencies. NO HTML in output strings.

    Returns
    -------
    dict with keys:
      provider_yield_summary : dict with keys:
        dependency_gaps    : list[str]  families with dependency_missing errors
        timeout_families   : list[str]  families with timeout errors
        low_yield_families  : list[str]  families with zero/minimal accepted results
        coverage_gaps       : list[str]  families expected but not attempted
      low_yield_families        : tuple[str, ...]
      dependency_gap_families    : tuple[str, ...]
      timeout_families          : tuple[str, ...]
      recommended_provider_actions : tuple[str, ...]
    """
    errors = doh_provider_errors or ()
    pub_errors = public_provider_errors or []
    missing = nonfeed_missing_expected_lanes or []
    sfo_list = scorecard.get("source_family_outcomes", []) if isinstance(scorecard, dict) else []
    nonfeed_expected = scorecard.get("nonfeed_expected_lanes", []) or []

    # Detect feed-only: only feed family has accepted findings, no nonfeed attempted
    nonfeed_families = {"ct", "doh", "wayback", "passive_dns", "shodan", "hunter"}
    _feed_only = False
    if sfo_list:
        feed_entry = next((e for e in sfo_list if isinstance(e, dict) and e.get("family") == "feed"), None)
        nonfeed_attempted = [e for e in sfo_list if isinstance(e, dict) and e.get("family") in nonfeed_families and e.get("attempted")]
        _feed_only = (feed_entry is not None and (feed_entry.get("accepted_count") or 0) > 0) and len(nonfeed_attempted) == 0

    # 1. dependency_gap_families — from doh_provider_errors
    _dep_gaps: list[str] = []
    for e in errors:
        if isinstance(e, str) and "dependency_missing" in e:
            _dep_gaps.append("doh")
    # Also check public_provider_errors for dependency signals
    for pe in pub_errors:
        if isinstance(pe, dict):
            err = str(pe.get("error", "")).lower()
            if "dependency_missing" in err or "dependency" in err:
                fam = str(pe.get("family", "")).lower()
                if fam and fam not in _dep_gaps:
                    _dep_gaps.append(fam)

    # 2. timeout_families — from terminal_state containing "timeout" or public_provider_errors
    _timeout_fams: list[str] = []
    for pe in pub_errors:
        if isinstance(pe, dict):
            err_type = str(pe.get("error_type", "")).lower()
            if err_type == "timeout":
                fam = str(pe.get("family", ""))
                if fam and fam not in _timeout_fams:
                    _timeout_fams.append(fam)
    # Also scan source_family_outcomes terminal_state
    for entry in sfo_list:
        if isinstance(entry, dict):
            ts = str(entry.get("terminal_state", "")).lower()
            if "timeout" in ts:
                fam = str(entry.get("family", ""))
                if fam and fam not in _timeout_fams:
                    _timeout_fams.append(fam)

    # 3. low_yield_families — families that attempted but produced minimal/no findings
    _low_yield: list[str] = []
    for entry in sfo_list:
        if isinstance(entry, dict) and entry.get("attempted"):
            fam = entry.get("family", "")
            accepted = entry.get("accepted_count", 0)
            ts = str(entry.get("terminal_state", "")).lower()
            # not_scheduled while expected = low yield
            if ts == "not_scheduled" and fam in missing:
                if fam not in _low_yield:
                    _low_yield.append(fam)
            # zero accepted + attempted = low yield (and not already flagged as dep/timeout gap)
            elif accepted == 0 and fam not in (_dep_gaps + _timeout_fams):
                if fam not in _low_yield:
                    _low_yield.append(fam)
    # Missing expected nonfeed lanes that were never attempted
    for fam in missing:
        if fam not in _low_yield and fam not in _dep_gaps:
            _low_yield.append(fam)

    # 4. recommended_provider_actions
    _actions: list[str] = []
    outcomes = _get_corrob_outcomes(scorecard)
    try:
        from hledac.universal.runtime.corroboration_score import compute_terminal_coverage
        tc = compute_terminal_coverage(outcomes)
        terminal_score = tc.terminal_coverage_score
    except Exception:
        terminal_score = 0.0

    corrob_score = _corroboration_score_value(scorecard)

    # High terminal coverage (all families reached terminal) + low corroboration
    # → provider quality improvement recommended
    if terminal_score >= 0.75 and corrob_score < 0.3 and not _feed_only:
        _actions.append("improve_provider_quality")

    # Feed-only with missing nonfeed lanes → scheduling recommendation, not provider quality
    if _feed_only and missing:
        _actions.append("expand_scheduling_coverage")

    # Dependency gaps detected → fix dependencies
    if _dep_gaps:
        _actions.append("resolve_provider_dependencies")

    # Timeouts detected → improve provider reliability
    if _timeout_fams:
        _actions.append("improve_provider_reliability")

    return {
        "provider_yield_summary": {
            "dependency_gaps": _dep_gaps,
            "timeout_families": _timeout_fams,
            "low_yield_families": _low_yield,
            "coverage_gaps": [f for f in missing if f not in _dep_gaps and f not in _timeout_fams],
        },
        "low_yield_families": tuple(_low_yield),
        "dependency_gap_families": tuple(_dep_gaps),
        "timeout_families": tuple(_timeout_fams),
        "recommended_provider_actions": tuple(_actions),
    }


    # [IMPORTED from components] def placeholder at L2537


    # [IMPORTED from components] def placeholder at L2615


    # [IMPORTED from components] def placeholder at L2687


    # [IMPORTED from components] def placeholder at L2745


async def _get_sprint_trend(store: Any, last_n: int = 5) -> list[dict]:
    """
    Sprint F183D §2: FAIL-SOFT async read seam — sprint trend.

    READ SEAM PRIORITY (truth order):
      1. async_query_sprint_trend(last_n) — PRIMARY: async DuckDB read
         REMOVAL CONDITION: store migration to fully-typed async pipeline
      2. get_sprint_trend(last_n) — COMPAT: sync wrapper fallback (deprecated)
         REMOVAL CONDITION: all callers use async path

    Both paths return same shape: list[dict] with sprint_id, new_findings, ioc_nodes.

    The sync wrapper is retained as COMPAT fallback for callers that have not
    yet migrated to async context (e.g., sync report printing paths).

    NOTE: This function is intentionally FAIL-SOFT — returns [] on any error.
    Sprint trend is a nice-to-have in export output, never a blocker.
    """
    if store is None:
        return []
    try:
        if hasattr(store, "async_query_sprint_trend"):
            # PRIMARY: async read seam — preferred for async export context
            return await store.async_query_sprint_trend(last_n=last_n) or []
    except Exception:
        pass
    # COMPAT FALLBACK: sync wrapper (deprecated — use async path above)
    try:
        if hasattr(store, "get_sprint_trend"):
            # Run sync wrapper in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: store.get_sprint_trend(last_n=last_n) or []
            )
    except Exception:
        pass
    return []


async def _get_source_leaderboard(store: Any, days: int = 7) -> list[dict]:
    """
    Sprint F183D §2: FAIL-SOFT async read seam — source leaderboard.

    READ SEAM PRIORITY (truth order):
      1. async_query_source_leaderboard(days) — PRIMARY: async DuckDB read
         REMOVAL CONDITION: store migration to fully-typed async pipeline
      2. get_source_leaderboard(days) — COMPAT: sync wrapper fallback (deprecated)
         REMOVAL CONDITION: all callers use async path

    Both paths return same shape: list[dict] with source_type, total_findings.

    The sync wrapper is retained as COMPAT fallback for callers that have not
    yet migrated to async context (e.g., sync report printing paths).

    NOTE: This function is intentionally FAIL-SOFT — returns [] on any error.
    Source leaderboard is a nice-to-have in export output, never a blocker.
    """
    if store is None:
        return []
    try:
        if hasattr(store, "async_query_source_leaderboard"):
            # PRIMARY: async read seam — preferred for async export context
            return await store.async_query_source_leaderboard(days=days) or []
    except Exception:
        pass
    # COMPAT FALLBACK: sync wrapper (deprecated — use async path above)
    try:
        if hasattr(store, "get_source_leaderboard"):
            # Run sync wrapper in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: store.get_source_leaderboard(days=days) or []
            )
    except Exception:
        pass
    return []


    # [IMPORTED from components] def placeholder at L2887


    # [IMPORTED from components] def placeholder at L2956


def _get_acquisition_truth(eh: "ExportHandoff") -> dict[str, Any]:
    """
    Sprint F208J-C: Acquisition truth pass-through — handoff-first truth order.

    Priority for each field (fail-soft, no store access):
      acquisition_report:
        1. eh.scorecard["acquisition_report"]
        2. eh.canonical_run_summary["acquisition_report"]
        3. eh.runtime_truth["acquisition_report"]

      acquisition_terminality_checked / _satisfied / _missing_lanes:
        1. eh.scorecard (top-level keys)
        2. eh.canonical_run_summary
        3. eh.runtime_truth

      source_family_outcomes / scheduler_exit / return_guard /
      windup_guard_observation / prewindup_barrier:
        1. eh.scorecard (top-level keys)
        2. eh.canonical_run_summary
        3. eh.runtime_truth

      # Sprint F209B: acquisition_prelude_* fields follow same priority order:
        1. eh.scorecard (top-level keys)
        2. eh.canonical_run_summary
        3. eh.runtime_truth

    NO scheduler import. NO store read. NO network. NO MLX load.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    crs = eh.canonical_run_summary if eh.canonical_run_summary else {}
    rt = eh.runtime_truth if eh.runtime_truth else {}

    result: dict[str, Any] = {}

    # acquisition_report — scorecard first, then canonical_run_summary, then runtime_truth
    ar = scorecard.get("acquisition_report")
    if not ar and isinstance(crs, dict):
        ar = crs.get("acquisition_report")
    if not ar and isinstance(rt, dict):
        ar = rt.get("acquisition_report")
    if ar and isinstance(ar, dict):
        result["acquisition_report"] = _make_serializable(ar)

    # acquisition_terminality_checked
    atc = scorecard.get("acquisition_terminality_checked")
    if atc is None:
        atc = crs.get("acquisition_terminality_checked") if isinstance(crs, dict) else None
    if atc is None:
        atc = rt.get("acquisition_terminality_checked") if isinstance(rt, dict) else None
    if atc is not None:
        result["acquisition_terminality_checked"] = atc

    # acquisition_terminality_satisfied
    ats = scorecard.get("acquisition_terminality_satisfied")
    if ats is None:
        ats = crs.get("acquisition_terminality_satisfied") if isinstance(crs, dict) else None
    if ats is None:
        ats = rt.get("acquisition_terminality_satisfied") if isinstance(rt, dict) else None
    if ats is not None:
        result["acquisition_terminality_satisfied"] = ats

    # acquisition_terminality_missing_lanes
    atm = scorecard.get("acquisition_terminality_missing_lanes")
    if atm is None:
        atm = crs.get("acquisition_terminality_missing_lanes") if isinstance(crs, dict) else None
    if atm is None:
        atm = rt.get("acquisition_terminality_missing_lanes") if isinstance(rt, dict) else None
    if atm is not None:
        result["acquisition_terminality_missing_lanes"] = atm

    # source_family_outcomes
    sfo = scorecard.get("source_family_outcomes")
    if not sfo and isinstance(crs, dict):
        sfo = crs.get("source_family_outcomes")
    if not sfo and isinstance(rt, dict):
        sfo = rt.get("source_family_outcomes")
    if sfo:
        result["source_family_outcomes"] = _make_serializable(sfo)

    # scheduler_exit
    se = scorecard.get("scheduler_exit")
    if not se and isinstance(crs, dict):
        se = crs.get("scheduler_exit")
    if not se and isinstance(rt, dict):
        se = rt.get("scheduler_exit")
    if se:
        result["scheduler_exit"] = _make_serializable(se)

    # return_guard
    rg = scorecard.get("return_guard")
    if not rg and isinstance(crs, dict):
        rg = crs.get("return_guard")
    if not rg and isinstance(rt, dict):
        rg = rt.get("return_guard")
    if rg:
        result["return_guard"] = _make_serializable(rg)

    # windup_guard_observation
    wg = scorecard.get("windup_guard_observation")
    if not wg and isinstance(crs, dict):
        wg = crs.get("windup_guard_observation")
    if not wg and isinstance(rt, dict):
        wg = rt.get("windup_guard_observation")
    if wg:
        result["windup_guard_observation"] = _make_serializable(wg)

    # prewindup_barrier
    pwb = scorecard.get("prewindup_barrier")
    if not pwb and isinstance(crs, dict):
        pwb = crs.get("prewindup_barrier")
    if not pwb and isinstance(rt, dict):
        pwb = rt.get("prewindup_barrier")
    if pwb:
        result["prewindup_barrier"] = _make_serializable(pwb)

    # Sprint F209B: Acquisition prelude pass-through
    # acquisition_prelude_checked / _ran / _required_lanes / _terminal_lanes /
    # _missing_lanes / _skipped_lanes / _errors / _duration_s / _reason:
    #   1. eh.scorecard (top-level keys)
    #   2. eh.canonical_run_summary
    #   3. eh.runtime_truth
    _prelude_fields = [
        "acquisition_prelude_checked",
        "acquisition_prelude_ran",
        "acquisition_prelude_required_lanes",
        "acquisition_prelude_terminal_lanes",
        "acquisition_prelude_missing_lanes",
        "acquisition_prelude_skipped_lanes",
        "acquisition_prelude_errors",
        "acquisition_prelude_duration_s",
        "acquisition_prelude_reason",
    ]
    for _field in _prelude_fields:
        _val = scorecard.get(_field)
        if _val is None and isinstance(crs, dict):
            _val = crs.get(_field)
        if _val is None and isinstance(rt, dict):
            _val = rt.get(_field)
        if _val is not None:
            if isinstance(_val, (dict, list)):
                result[_field] = _make_serializable(_val)
            else:
                result[_field] = _val

    return result


def _reconcile_acquisition_terminality_from_source_outcomes(report_dict: dict) -> dict:
    """
    Sprint F211A: Final terminality reconciliation from source_family_outcomes.

    The acquisition_report.terminality may be snapshotted before all
    source_family_outcomes are finalized. This function reconciles the
    terminality missing_lanes by checking the authoritative source_family_outcomes.

    Rules for terminal from source_family_outcomes:
      - attempted=True  => terminal
      - skipped=True    => terminal
      - timeout=True    => terminal
      - error non-empty  => terminal

    accepted_count=0 does NOT make a lane missing.

    NO scheduler execution. NO store read. NO network. NO MLX load.
    """
    # Gather source_family_outcomes from all known locations
    sfo_list: list[dict] = []

    # 1. Top-level
    sfo = report_dict.get("source_family_outcomes")
    if sfo and isinstance(sfo, list):
        sfo_list = sfo

    # 2. acquisition_report.source_family_outcomes
    ar = report_dict.get("acquisition_report")
    if isinstance(ar, dict):
        sfo = ar.get("source_family_outcomes")
        if sfo and isinstance(sfo, list) and not sfo_list:
            sfo_list = sfo

    # 3. canonical_run_summary.source_family_outcomes
    crs = report_dict.get("canonical_run_summary")
    if isinstance(crs, dict):
        sfo = crs.get("source_family_outcomes")
        if sfo and isinstance(sfo, list) and not sfo_list:
            sfo_list = sfo

    # Index source_family_outcomes by family name for fast lookup
    outcomes_by_family: dict[str, dict] = {}
    for outcome in sfo_list:
        fam = outcome.get("family") if isinstance(outcome, dict) else None
        if fam:
            outcomes_by_family[fam] = outcome

    # Get existing terminality from acquisition_report
    term = None
    if isinstance(ar, dict):
        term = ar.get("terminality")

    if not term or not isinstance(term, dict):
        # Nothing to reconcile — no terminality present
        return report_dict

    original_missing: list[str] = term.get("missing_lanes") or []
    # required_lanes preserved for future diagnostics if needed
    # term.get("required_lanes") or []

    if not original_missing:
        # Nothing to reconcile — no missing lanes
        return report_dict

    # Determine which required lanes are terminal from source_family_outcomes
    mismatch_before: list[str] = []

    for lane in list(original_missing):
        outcome = outcomes_by_family.get(lane)
        if outcome is None:
            # No outcome recorded for this lane — keep as missing
            continue

        # Check if this outcome is terminal
        attempted = outcome.get("attempted") if isinstance(outcome, dict) else False
        skipped = outcome.get("skipped") if isinstance(outcome, dict) else False
        timeout = outcome.get("timeout") if isinstance(outcome, dict) else False
        error = outcome.get("error") if isinstance(outcome, dict) else None

        is_terminal = attempted or skipped or timeout or (error is not None)

        if is_terminal:
            mismatch_before.append(lane)

    if not mismatch_before:
        # No reconciliation needed
        return report_dict

    # Build reconciled terminality
    new_missing = [lane for lane in original_missing if lane not in mismatch_before]

    # Preserve original terminality
    term_reconciled = dict(term)
    term_reconciled["missing_lanes"] = new_missing
    term_reconciled["satisfied"] = list(
        set((term.get("satisfied") or []) + mismatch_before)
    )
    term_reconciled["terminal_lanes"] = list(
        set((term.get("terminal_lanes") or []) + mismatch_before)
    )

    # Write back to acquisition_report
    ar_final = dict(ar) if ar else {}
    ar_final["terminality"] = term_reconciled
    report_dict["acquisition_report"] = ar_final

    # Add reconciliation markers
    report_dict["terminality_reconciled"] = True
    report_dict["terminality_reconciliation_reason"] = "source_family_outcomes_final_authority"
    report_dict["terminality_before_reconciliation"] = dict(term)
    report_dict["terminality_source_outcome_mismatch_before"] = mismatch_before

    # Update top-level acquisition_terminality_missing_lanes if present
    if "acquisition_terminality_missing_lanes" in report_dict:
        report_dict["acquisition_terminality_missing_lanes"] = new_missing

    # Update acquisition_terminality_satisfied
    if "acquisition_terminality_satisfied" in report_dict:
        report_dict["acquisition_terminality_satisfied"] = len(new_missing) == 0

    return report_dict


def _get_feed_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: feed_verdict z ExportHandoff.scorecard.

    Aggregated feed economics verdict across sprint cycles.
    Produced by compute_sprint_intelligence() → scorecard["feed_verdict"].

    Seam: scorecard["feed_verdict"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    fv = scorecard.get("feed_verdict")
    if fv and isinstance(fv, dict):
        return fv
    return None


def _get_public_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: public_verdict z ExportHandoff.scorecard.

    Aggregated public branch verdict across sprint cycles.
    Produced by compute_sprint_intelligence() → scorecard["public_verdict"].

    Seam: scorecard["public_verdict"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    pv = scorecard.get("public_verdict")
    if pv and isinstance(pv, dict):
        return pv
    return None


def _get_signal_path(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: signal_path z ExportHandoff.scorecard.

    Dominant signal path, next pivot recommendation, corroboration health.
    Produced by compute_sprint_intelligence() → scorecard["signal_path"].

    Seam: scorecard["signal_path"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    sp = scorecard.get("signal_path")
    if sp and isinstance(sp, dict):
        return sp
    return None


def _get_hypothesis_pack(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: hypothesis_pack z ExportHandoff.scorecard.

    Operator shortlist + actionability summary z hypothesis_engine.
    Produced by compute_sprint_intelligence() → scorecard["hypothesis_pack"].

    Seam: scorecard["hypothesis_pack"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    hp = scorecard.get("hypothesis_pack")
    if hp and isinstance(hp, dict):
        return hp
    return None


def _get_canonical_run_summary(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P §2 + F157: canonical_run_summary — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["canonical_run_summary"] — primary canonical surface
      2. eh.scorecard["canonical_run_summary"] — fallback pro scorecard-only builds

    High-level sprint characterization produced by compute_sprint_intelligence().
    Contains: sprint_id, total_cycles, accepted_findings, signal_verdict,
    feed_public_balance, key_highlight, primary_theme.

    Fail-soft: returns None when not present (older sprints).
    """
    # Priority 1: top-level canonical surface
    crs = eh.canonical_run_summary if eh.canonical_run_summary else None
    if crs and isinstance(crs, dict):
        return crs
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    crs = scorecard.get("canonical_run_summary")
    if crs and isinstance(crs, dict):
        return crs
    return None


def _get_sprint_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P §2 + F157: sprint_verdict — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["sprint_verdict"] — primary canonical surface
      2. eh.scorecard["sprint_verdict"] — fallback pro scorecard-only builds

    Aggregated sprint quality verdict: success / partial / failed / degraded.
    Produced by compute_sprint_intelligence() → scorecard["sprint_verdict"].

    Fail-soft: returns None when not present.
    """
    # Priority 1: top-level canonical surface
    sv = eh.sprint_verdict if eh.sprint_verdict else None
    if sv and isinstance(sv, dict):
        return sv
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    sv = scorecard.get("sprint_verdict")
    if sv and isinstance(sv, dict):
        return sv
    return None


def _get_synthesis_outcome_payload(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F157: synthesis_outcome_payload — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["synthesis_outcome_payload"] — primary canonical surface
      2. eh.scorecard["synthesis_outcome_payload"] — fallback pro scorecard-only builds

    Serialized SynthesisOutcome seam from synthesis_runner.
    Fail-soft: returns None when not present (synthesis not run, or older builds).
    """
    # Priority 1: top-level canonical surface
    sop = eh.synthesis_outcome_payload if eh.synthesis_outcome_payload else None
    if sop and isinstance(sop, dict):
        return sop
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    sop = scorecard.get("synthesis_outcome_payload")
    if sop and isinstance(sop, dict):
        return sop
    return None


# Sprint F192H: Research Depth Metric
# Source type depth tiers — higher tier = harder to reach = deeper research
#
# Taxonomy normalization (Sprint F192H):
#   - ct_log (ct_log_client.py:273) and ct_log_pipeline share tier 1
#   - onion_discovery (live_public_pipeline.py:1785) → tier 2 (dark/deep web)
#   - ipfs (ti_feed_adapter.py:1367) → tier 1 (structured TI)
#   - shodan_search (shodan_wrapper.py:204) → tier 1 (structured TI)
#   - bgp_monitor (ti_feed_adapter.py:1742) → tier 1 (structured TI)
#   - academic_discovery, pastebin_monitor, github_secret_scanner already present
_SOURCE_TIER: dict[str, int] = {
    # Tier 0: indexed/surface (high availability, low depth)
    "rss_atom_pipeline": 0,
    "live_public_pipeline": 0,
    "rss": 0,
    "api": 0,
    "planner_bridge": 0,
    # Tier 1: structured TI (moderate depth)
    "ct_log": 1,
    "ct_log_pipeline": 1,
    "circl_pdns": 1,
    "academic_discovery": 1,
    "pastebin_monitor": 1,
    "github_secret_scanner": 1,
    "ipfs": 1,
    "shodan_search": 1,
    "bgp_monitor": 1,
    # Tier 2: deep/dark web (hard to reach, high depth)
    "onion_discovery": 2,
    "rl_research": 2,
    "tot_synthesis": 2,
    "report": 2,
}


def _compute_research_depth(
    eh: "ExportHandoff",  # type: ignore[name-defined]
    pvs: dict[str, Any] | None,
    signal_path: dict[str, Any] | None,
    hypothesis_pack: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Sprint F192H: research_depth_metric — derived from canonical surfaces only.

    Computes a 0-100 research depth score differentiating:
      - Surface research (indexed web only, single source type)
      - Shallow research (multiple indexed sources)
      - Moderate research (some CT logs, archive, PDNS)
      - Deep research (significant deep sources + corroboration)
      - Comprehensive research (all dimensions strong)

    Components (0-100 total):
      - source_diversity (0-25): unique source types + Shannon entropy
      - non_indexed_ratio (0-20): tier1+tier2 sources / total
      - corroboration (0-25): is_corroborated + campaign_hints + noisy signal
      - branch_diversity (0-15): feed vs public vs CT active branches
      - pivot_depth (0-15): hypothesis_count + pivot recommendations

    DERIVED ONLY — reads from ExportHandoff canonical surfaces:
      - eh.scorecard["entries_per_source"] / ["hits_per_source"]
      - eh.scorecard["branch_mix"] via runtime_truth
      - signal_path["is_corroborated"], ["is_noisy"], ["next_pivot_recommendation"]
      - correlation["campaign_hints"], ["high_risk_branch"]
      - hypothesis_pack["hypothesis_count"]

    NO new persistence. NO new store reads.
    """
    import math

    scorecard = eh.scorecard if eh.scorecard else {}
    runtime_truth = _get_runtime_truth(eh)

    # ── 1. Source diversity (0-25) ─────────────────────────────────────
    # Extract source hit counts from scorecard surfaces
    entries_per_source: dict[str, int] = {}
    hits_per_source: dict[str, int] = {}
    if isinstance(scorecard.get("entries_per_source"), dict):
        entries_per_source = scorecard["entries_per_source"]
    if isinstance(scorecard.get("hits_per_source"), dict):
        hits_per_source = scorecard["hits_per_source"]

    # Union of both — deduplicated by source name
    source_counts: dict[str, int] = {}
    for d in (entries_per_source, hits_per_source):
        for src, cnt in d.items():
            if isinstance(cnt, (int, float)):
                source_counts[str(src)] = source_counts.get(src, 0) + int(cnt)

    unique_types = len(source_counts)
    total_hits = sum(source_counts.values()) if source_counts else 0

    # Shannon entropy of source distribution (0-1 normalized)
    entropy_score = 0.0
    if unique_types >= 2 and total_hits > 0:
        probs = [cnt / total_hits for cnt in source_counts.values() if cnt > 0]
        h = -sum(p * math.log2(p) for p in probs if p > 0)
        max_entropy = math.log2(len(probs))
        entropy_score = (h / max_entropy) if max_entropy > 0 else 0.0

    # Diversity score: entropy contribution (15pts) + unique type bonus (10pts)
    source_diversity_score = min(25.0, entropy_score * 15 + min(10.0, unique_types * 2.5))

    # ── 2. Non-indexed ratio (0-20) ────────────────────────────────────
    # Count hits from tier1 (structured) and tier2 (deep) sources
    deep_hits = 0
    for src, cnt in source_counts.items():
        tier = _SOURCE_TIER.get(src, 0)
        if tier > 0:
            deep_hits += cnt

    non_indexed_ratio = deep_hits / total_hits if total_hits > 0 else 0.0
    non_indexed_ratio_score = non_indexed_ratio * 20.0

    # ── 3. Corroboration (0-25) ────────────────────────────────────────
    corroboration_score = 0.0
    campaign_count = 0
    is_corroborated = False
    is_noisy = True  # default assumption

    if signal_path:
        if signal_path.get("is_corroborated") is True:
            corroboration_score += 15
            is_corroborated = True
        if signal_path.get("is_noisy") is False:
            corroboration_score += 5
            is_noisy = False

    if correlation and not correlation.get("_no_correlation_data"):
        raw_hints = correlation.get("campaign_hints") or []
        if isinstance(raw_hints, list):
            campaign_count = len(raw_hints)
        if campaign_count >= 3:
            corroboration_score += 5
        elif campaign_count >= 1:
            corroboration_score += 3

    corroboration_score = min(25.0, corroboration_score)

    # ── 4. Branch diversity (0-15) ─────────────────────────────────────
    branch_score = 0.0
    active_branches = 0
    if runtime_truth:
        branch_mix = runtime_truth.get("branch_mix") or {}
        if isinstance(branch_mix, dict):
            active_branches = sum(1 for v in branch_mix.values() if isinstance(v, (int, float)) and v > 0)
    branch_score = min(15.0, active_branches * 5.0)

    # ── 5. Pivot depth (0-15) ───────────────────────────────────────────
    pivot_score = 0.0
    pivot_recommended = False
    if hypothesis_pack:
        hyp_count = hypothesis_pack.get("hypothesis_count") or 0
        if isinstance(hyp_count, (int, float)) and hyp_count > 0:
            pivot_score += 5
    if signal_path:
        pivot_rec = signal_path.get("next_pivot_recommendation") or ""
        if pivot_rec and pivot_rec not in ("continue", "unknown", ""):
            pivot_score += 10
            pivot_recommended = True

    pivot_score = min(15.0, pivot_score)

    # ── Total ─────────────────────────────────────────────────────────
    total = source_diversity_score + non_indexed_ratio_score + corroboration_score + branch_score + pivot_score
    total = min(100.0, round(total, 1))

    # ── Classification ────────────────────────────────────────────────
    if total >= 81:
        level = "comprehensive"
    elif total >= 61:
        level = "deep"
    elif total >= 41:
        level = "moderate"
    elif total >= 21:
        level = "shallow"
    else:
        level = "surface"

    return {
        "score": total,
        "level": level,
        "breakdown": {
            "source_diversity": round(source_diversity_score, 1),
            "non_indexed_ratio": round(non_indexed_ratio_score, 1),
            "corroboration": round(corroboration_score, 1),
            "branch_diversity": round(branch_score, 1),
            "pivot_depth": round(pivot_score, 1),
        },
        "depth_signals": {
            "unique_source_types": list(source_counts.keys()),
            "deep_sources_found": deep_hits,
            "total_source_hits": total_hits,
            "corroborated": is_corroborated,
            "noisy_signal": is_noisy,
            "campaign_hints": campaign_count,
            "active_branches": active_branches,
            "pivot_recommended": pivot_recommended,
        },
    }


def _build_capability_synthesis(
    pvs: dict[str, Any] | None,
    analyst_brief: dict[str, Any] | None,
    runtime_truth: dict[str, Any] | None,
    acquisition_report: dict[str, Any] | None,
    research_depth: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Sprint F225F: capability_synthesis — did this run improve actual OSINT capability?

    Answers:
      - Did this run produce useful OSINT capability?
      - What improved?
      - What is still weak?
      - What is the next high-value engineering action?

    DERIVED ONLY — reads from existing canonical surfaces, NO new store reads,
    NO network, NO MLX load. Fail-soft: returns "unknown" verdicts when data
    is missing (smoke runs, legacy sprints).

    Inputs:
      pvs: product_value_summary from export_sprint
      analyst_brief: eh.analyst_brief (target brief with memory/sprint context)
      runtime_truth: canonical runtime truth (is_meaningful, primary_signal_source)
      acquisition_report: from acq_truth["acquisition_report"]
      research_depth: from _compute_research_depth()

    Sprint F229A: Truth precedence applied before verdict determination.
    If accepted_findings > 0 and runtime_truth.is_meaningful=true, do NOT emit
    invalid_capability solely because pvs.accepted is zero.
    """
    # Sprint F229A: Reconcile accepted count BEFORE verdict logic
    # Uses reconcile_terminal_truth to resolve pvs=0 vs runtime_truth > 0 contradiction.
    _scorecard = {}
    reconciled_pvs, runtime_accepted, truth_recon_applied, truth_recon_reason = reconcile_terminal_truth(
        pvs, _scorecard, runtime_truth
    )
    # Use reconciled pvs for accepted counts in verdict logic
    _pvs_accepted = reconciled_pvs.get("accepted", 0) if reconciled_pvs else 0

    # ── 1. Baseline capability verdict ─────────────────────────────────────
    # Terminality is prerequisite for any capability claim
    terminality_satisfied = False
    if acquisition_report and isinstance(acquisition_report, dict):
        term = acquisition_report.get("terminality")
        if isinstance(term, dict):
            terminality_satisfied = bool(term.get("satisfied", False))

    is_meaningful = runtime_truth.get("is_meaningful", None) if runtime_truth else None

    # F229B: Read corroboration score from pvs for verdict influence
    _corrob_score = pvs.get("corroboration_score", 0.0) if pvs else 0.0
    _corrob_penalties = pvs.get("corroboration_penalties", []) if pvs else []
    _corrob_families = pvs.get("corroborating_families", ()) if pvs else ()

    # Sprint F229A: Verdict precedence order (tightened to fix truth contradiction):
    #   1. Smoke: is_meaningful=False → smoke_capability (regardless of accepted count)
    #   2. F229A Fix: runtime has accepted findings from a meaningful run → useful_capability
    #      (This overrides terminality check when runtime shows real findings but
    #       acquisition_report was not captured / is None — common in partial exports)
    #   3. F230C: Low corroboration score → weak_capability/invalid_capability
    #   4. Invalid: terminality explicitly unsatisfied → invalid_capability
    #   5. Default: terminality satisfied + meaningful → useful_capability
    # Rules:
    #   - Do NOT emit invalid_capability solely because pvs.accepted is zeroed
    #     when runtime_truth had meaningful accepted findings
    #   - Preserve invalid_capability when terminality actually failed AND no runtime findings
    #   - F230C: Low corroboration score (< 0.3) with feed-only penalty → weak_capability
    # Note: The F229A fix (step 2) requires runtime_accepted > 0 from a reconciled source,
    # not just pvs.accepted. When pvs.accepted > 0 but _pvs_accepted (reconciled) is 0,
    # the F229A path should NOT override the F230C low-corroboration check.
    # Verdict precedence order (finalized F229A+F230C+F230F):
    #   1. F230C: Low corroboration score (< 0.3) with feed-only penalty → weak_capability
    #   2. Smoke: is_meaningful=False → smoke_capability (F229A precedence: checked before runtime override)
    #   3. F229A runtime override: runtime has accepted findings (from runtime_truth source)
    #      → useful_capability (requires terminality satisfied to preserve smoke precedence)
    #   4. Invalid: terminality unsatisfied → invalid_capability
    #   5. Default: terminality satisfied + meaningful → useful_capability
    if _corrob_score < 0.3 and "feed_only_no_nonfeed" in _corrob_penalties:
        verdict = "weak_capability"
        confidence = 0.70
    elif is_meaningful is False:
        verdict = "smoke_capability"
        confidence = 0.85
    elif runtime_accepted > 0 and truth_recon_applied and terminality_satisfied:
        verdict = "useful_capability"
        confidence = 0.80
    elif not terminality_satisfied:
        verdict = "invalid_capability"
        confidence = 0.95
    else:
        verdict = "useful_capability"
        confidence = 0.75

    # ── 2. Evidence quality signals ─────────────────────────────────────────
    accepted = _pvs_accepted if _pvs_accepted > 0 else (pvs.get("accepted", 0) if pvs else 0)
    nonfeed_accepted = 0
    if pvs:
        # nonfeed signals in pvs — look for ct_findings/public_findings in branch_mix
        branch_mix = (runtime_truth.get("branch_mix", {}) if runtime_truth else {})
        ct_findings = branch_mix.get("ct_findings", 0) if isinstance(branch_mix, dict) else 0
        public_findings = branch_mix.get("public_findings", 0) if isinstance(branch_mix, dict) else 0
        nonfeed_accepted = ct_findings + public_findings

    useful_evidence = accepted > 0 and nonfeed_accepted > 0
    feed_heavy = (pvs.get("feed_share", 1.0) >= 0.9) if pvs else False
    hardware_constrained = bool(pvs.get("peak_rss_mb", 0) > 7000) if pvs else False

    if hardware_constrained and accepted > 0:
        verdict = "incomparable_capability"
        confidence = 0.60

    # ── 3. Source diversity summary ─────────────────────────────────────────
    source_types: list[str] = []
    if research_depth and isinstance(research_depth, dict):
        ds = research_depth.get("depth_signals", {})
        if isinstance(ds, dict):
            source_types = ds.get("unique_source_types", [])

    if len(source_types) >= 3:
        source_diversity_summary = "multi_source_diverse"
    elif len(source_types) == 2:
        source_diversity_summary = "dual_source_mixed"
    elif len(source_types) == 1:
        source_diversity_summary = "single_source_feed_only" if source_types[0] in ("ct", "feed") else "single_source_niche"
    else:
        source_diversity_summary = "unknown_source"

    # ── 4. Corroboration summary (F230C: use F229B corroboration_score) ───────
    # F230C: Derive corroboration_summary from pvs.lane_corroboration_score
    # rather than research_depth.depth_signals (which is a different signal).
    # F231A: Terminal coverage is distinct from positive corroboration.
    #   - corroboration_score < 0.3 with terminal coverage but no positive families
    #     → "nonfeed_attempted_no_positive_evidence" (not "feed_only")
    _terminal_coverage_score = pvs.get("lane_terminal_coverage_score", 0.0) if pvs else 0.0
    _terminal_families = pvs.get("terminal_families", ()) if pvs else ()
    _has_terminal_nonfeed = bool({"ct", "doh", "wayback", "passive_dns"} & set(_terminal_families))

    corroboration = "none"
    if _corrob_score >= 0.75:
        corroboration = "corroborated"
    elif _corrob_score < 0.3 and _terminal_coverage_score > 0 and _has_terminal_nonfeed:
        # F231A: Nonfeed lanes were planned/attempted (terminal coverage exists)
        # but no positive corroboration — distinct from pure feed_only
        corroboration = "nonfeed_attempted_no_positive_evidence"
    elif _corrob_score < 0.3:
        corroboration = "noisy" if _corrob_score > 0 else "feed_only"
    elif "campaign_hint" in _corrob_families:
        corroboration = "campaign_hint"

    # ── 5. Feed noise summary ─────────────────────────────────────────────────
    if pvs:
        sig_class = pvs.get("_signal_quality_classification", "unknown")
        dedup_eff = pvs.get("dedup_effective", False)
        if sig_class == "depleted":
            feed_noise_summary = "depleted_feed_exhausted"
        elif feed_heavy and not useful_evidence:
            feed_noise_summary = "feed_noisy_no_nonfeed_signal"
        elif dedup_eff and sig_class in ("high_density", "medium_density"):
            feed_noise_summary = "feed_clean_dedup_effective"
        elif feed_heavy:
            feed_noise_summary = "feed_dominant"
        else:
            feed_noise_summary = "balanced_or_nonfeed"
    else:
        feed_noise_summary = "unknown"

    # ── 6. Non-feed value present ────────────────────────────────────────────
    # F230C: Also consider corroborating_families for nonfeed presence
    _nonfeed_families_set = {"ct", "doh", "wayback", "passive_dns"}
    _has_corrob_nonfeed = bool(_nonfeed_families_set & set(_corrob_families))
    nonfeed_value = nonfeed_accepted > 0 or _has_corrob_nonfeed or (len(source_types) > 1 and len(source_types) <= 3)
    nonfeed_value_present = bool(nonfeed_value)

    # ── 7. Next engineering action (deterministic, no ML) ───────────────────
    # F230C: Influence engineering action from F229B corroboration score
    # F231A: Handle nonfeed_attempted_no_positive_evidence before feed_heavy check
    # because lanes were already planned/attempted — yield/quality fix not lane boost.
    if verdict == "invalid_capability":
        next_engineering_action = "fix_terminality_before_capacity_expansion"
    elif hardware_constrained:
        next_engineering_action = "address_m1_memory_pressure_before_scale"
    elif not terminality_satisfied:
        next_engineering_action = "resolve_terminality_gaps_first"
    elif corroboration == "nonfeed_attempted_no_positive_evidence":
        # F231A: Lanes were planned and reached terminal state but yielded no
        # positive evidence — improve yield/provider quality, not lane scheduling
        next_engineering_action = "improve_nonfeed_yield_or_provider_quality"
    elif feed_heavy and not useful_evidence:
        next_engineering_action = "boost_nonfeed_lanes_to_achieve_balance"
    elif _corrob_score < 0.3 and "feed_only_no_nonfeed" in _corrob_penalties:
        # F230C: Low corroboration + feed-only penalty → terminality fix
        next_engineering_action = "fix_terminality_before_capacity_expansion"
    elif _corrob_score >= 0.7 and _has_corrob_nonfeed:
        # F230C: High corroboration with nonfeed families → expand
        next_engineering_action = "expand_or_deepen_pivots"
    elif "nonfeed_missed" in _corrob_penalties or _corrob_score < 0.2:
        # F230C: Nonfeed missing → fix nonfeed terminality
        next_engineering_action = "fix_nonfeed_terminality"
    elif corroboration == "noisy":
        next_engineering_action = "investigate_signal_noise_source"
    elif research_depth and isinstance(research_depth, dict):
        rd_score = research_depth.get("score", 0) if isinstance(research_depth, dict) else 0
        if rd_score < 30:
            next_engineering_action = "pivot_deeper_sources_or_new_query_strategy"
        elif rd_score < 60:
            next_engineering_action = "improve_source_diversity_and_corrobortion"
        else:
            next_engineering_action = "maintain_current_capability_and_increment"
    else:
        next_engineering_action = "maintain_current_capability_and_increment"

    # ── 7b. F226E: Target memory feedback — feed dominance correction ────────
    if analyst_brief and isinstance(analyst_brief, dict):
        tmf = _get_brief_field(analyst_brief, "target_memory_feedback", {}) or {}
        if tmf.get("repeated_feed_dominance"):
            next_engineering_action = "boost_nonfeed_lanes_to_achieve_balance"
        elif tmf.get("prior_nonfeed_weakness"):
            nea = tmf.get("suggested_next_profile", "")
            if nea == "PUBLIC":
                next_engineering_action = "bootstrap_public_bridge_before_scale"
            elif nea == "CT":
                next_engineering_action = "bootstrap_ct_provider_before_scale"

    # ── 8. Next investigation action (for analyst) ─────────────────────────
    # F230C: Influence from F229B corroboration score
    if analyst_brief and isinstance(analyst_brief, dict):
        target_memory = _get_brief_field(analyst_brief, "target_memory_summary", "") or ""
        if isinstance(target_memory, str) and target_memory:
            next_investigation_action = f"follow_up_on_target_memory_{target_memory[:40]}"
        else:
            next_investigation_action = "review_fresh_findings_and_query_adjustments"
    elif _corrob_score < 0.2:
        # F230C: Very low corroboration → diagnostic action
        next_investigation_action = "diagnose_acquisition_or_query_effectiveness"
    elif accepted > 10:
        next_investigation_action = "analyze_top_findings_for_operational_actionability"
    elif accepted > 0:
        next_investigation_action = "assess_accepted_findings_for_false_positive_rate"
    else:
        next_investigation_action = "diagnose_acquisition_or_query_effectiveness"

    return {
        "capability_verdict": verdict,
        "useful_evidence_present": useful_evidence,
        "nonfeed_value_present": nonfeed_value_present,
        "source_diversity_summary": source_diversity_summary,
        "corroboration_summary": corroboration,
        "feed_noise_summary": feed_noise_summary,
        "next_engineering_action": next_engineering_action,
        "next_investigation_action": next_investigation_action,
        "confidence": confidence,
    }


def _derive_run_truth_note(
    runtime_truth: dict[str, Any] | None,
    canonical_run_summary: dict[str, Any] | None,
    sprint_verdict: dict[str, Any] | None,
    pvs: dict[str, Any] | None,
) -> str:
    """
    Sprint F176D: run_truth_note — operator-facing sprint characterization.

    Tightened for fast operator triage: meaningful vs smoke vs degraded.

    Priority order:
      1. runtime_truth["is_meaningful"] — primary empirical signal
      2. sprint_verdict["verdict" / "sprint_status"] — synthesized verdict
      3. pvs.signal_quality — last resort fallback

    Labels are short for operator glance:
      - meaningful_run, smoke_run, slow_signal_run, mixed_run, degraded_run, unknown_run
    """
    # Priority 1: runtime_truth is PRIMARY
    if runtime_truth:
        is_meaningful = runtime_truth.get("is_meaningful")
        evidence_note = runtime_truth.get("evidence_note") or ""
        if is_meaningful is True:
            return f"meaningful_run" + (f": {evidence_note}" if evidence_note else "")
        elif is_meaningful is False:
            return f"smoke_run" + (f": {evidence_note}" if evidence_note else "")
        elif isinstance(is_meaningful, str):
            return f"{is_meaningful}" + (f": {evidence_note}" if evidence_note else "")

    # Priority 2: sprint_verdict — degraded/failed checked before general verdict
    if sprint_verdict:
        status = sprint_verdict.get("sprint_status") or sprint_verdict.get("verdict") or ""
        if status in ("degraded", "failed"):
            return f"degraded_run: {status}"
        if status and len(status) > 2:
            return f"run: {status}"

    # Priority 3: canonical_run_summary signal_verdict
    if canonical_run_summary:
        sig_verdict = canonical_run_summary.get("signal_verdict") or ""
        if sig_verdict and len(sig_verdict) > 2:
            return f"signal={sig_verdict}"

    # Priority 4: pvs-based fallback
    if pvs is None:
        return "unknown_run: insufficient data"
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)

    match signal:
        case "high_density":
            return "meaningful_run: high density signal"
        case "medium_density":
            return "mixed_run: signal present but noisy"
        case "depleted":
            return "smoke_run: depleted, no actionable signal"
        case "slow_novelty":
            return "slow_signal_run: signal exists, low rate"
        case "unknown" if accepted > 0:
            return "findings_run: signal unclear, findings exist"
        case "unknown":
            return "unknown_run: no signal characterization"
        case _:
            return "unknown_run"


def _derive_branch_truth(
    feed_verdict: dict[str, Any] | None,
    public_verdict: dict[str, Any] | None,
    branch_value: dict[str, Any] | None,
) -> str:
    """
    Sprint F150P §1: branch_truth — definitive feed/public balance summary.

    Combines feed_verdict + public_verdict + branch_value into single sentence.

    Reads:
    - feed_verdict["dominant_tag"], feed_verdict["avg_quality"]
    - public_verdict["avg_value_ratio"], public_verdict["dominant_next_action"]
    - branch_value["branch_verdict"], feed/public percentages

    Fail-soft: falls back to branch_value alone if verdicts unavailable.
    """
    parts: list[str] = []

    if feed_verdict:
        tag = feed_verdict.get("dominant_tag", "")
        quality = feed_verdict.get("avg_quality", 0)
        if tag:
            parts.append(f"feed={tag}(q={quality})")

    if public_verdict:
        vr = public_verdict.get("avg_value_ratio", 0)
        action = public_verdict.get("dominant_next_action", "")
        if vr > 0:
            parts.append(f"public=val_ratio({vr:.2f})")
        if action:
            parts.append(f"public_action={action[:20]}")

    if branch_value:
        verdict = branch_value.get("branch_verdict", "")
        feed_pct = branch_value.get("feed_pct", 0)
        pub_pct = branch_value.get("public_pct", 0)
        if verdict:
            parts.append(f"verdict={verdict}(feed={feed_pct:.0f}%/pub={pub_pct:.0f}%)")

    if not parts:
        return "branch_truth: insufficient data"
    return " | ".join(parts)


def _derive_best_first_move(
    runtime_truth: dict[str, Any] | None,
    signal_path: dict[str, Any] | None,
    canonical_run_summary: dict[str, Any] | None,
    sprint_verdict: dict[str, Any] | None,
    pvs: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> str:
    """
    Sprint F176D: best_first_move — immediate next action (single sentence).

    Priority order (tightened for operator speed):
    1. DEGRADED indicator FIRST — operator needs to know if sprint was bad
       - runtime_truth.is_meaningful=False → smoke signal
       - sprint_verdict.degraded → degraded state
    2. High-risk findings — critical findings override all other guidance
    3. sprint_verdict["recommended_action"] — most synthesized verdict
    4. signal_path next_pivot_recommendation
    5. canonical_run_summary["next_action"]
    6. pvs signal quality guidance
    7. correlation operator_shortlist first item

    Single sentence, max 80 chars.
    """
    # 0. DEGRADED FIRST — operator needs instant awareness of bad sprints
    if runtime_truth:
        is_meaningful = runtime_truth.get("is_meaningful")
        if is_meaningful is False:
            return "pivot: smoke run, change approach immediately"

    if sprint_verdict:
        status = sprint_verdict.get("sprint_status") or sprint_verdict.get("verdict") or ""
        if status in ("degraded", "failed"):
            return f"degraded sprint ({status}) — investigate root cause"

    # 1. High-risk first (critical findings take operational priority)
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            return "investigate high-risk branch — critical findings present"

    # 2. sprint_verdict recommended action
    if sprint_verdict:
        rec_action = sprint_verdict.get("recommended_action") or sprint_verdict.get("next_action") or ""
        if rec_action and len(rec_action) > 2:
            return f"action: {rec_action[:80]}"

    # 3. Signal path next pivot
    if signal_path:
        next_pivot = signal_path.get("next_pivot_recommendation", "")
        if next_pivot == "pivot_immediately":
            return "pivot: signal path recommends immediate pivot"

    # 4. canonical_run_summary next_action
    if canonical_run_summary:
        na = canonical_run_summary.get("next_action") or canonical_run_summary.get("recommended_action") or ""
        if na and len(na) > 2:
            return f"action: {na[:80]}"

    # 5. pvs signal guidance
    signal = pvs.get("_signal_quality_classification", "unknown") if pvs else "unknown"
    match signal:
        case "depleted":
            return "new approach: current query space exhausted"
        case "high_density":
            return "expand: broaden successful query approach"
        case "medium_density":
            return "narrow: reduce query scope to reduce low-info noise"
        case "slow_novelty":
            return "accelerate: real signal exists, speed up sources"

    # 6. Correlation operator shortlist
    if correlation:
        shortlist = correlation.get("operator_shortlist") or []
        if shortlist and isinstance(shortlist, list) and len(shortlist) > 0:
            first = shortlist[0]
            if isinstance(first, dict):
                action = first.get("action", "")
                target = first.get("target", "")
                if action:
                    return f"{action}: {target[:40]}" if target else action[:80]

    return "assess: gather more data before committing to approach"


    # [IMPORTED from components] def placeholder at L3699


    # [IMPORTED from components] def placeholder at L3780


    # [IMPORTED from components] def placeholder at L3789


    # [IMPORTED from components] def placeholder at L3975


    # [IMPORTED from components] def placeholder at L4010


    # [IMPORTED from components] def placeholder at L4079


    # [IMPORTED from components] def placeholder at L4125


    # [IMPORTED from components] def placeholder at L4173


    # [IMPORTED from components] def placeholder at L4231


    # [IMPORTED from components] def placeholder at L4283


    # [IMPORTED from components] def placeholder at L4333


    # [IMPORTED from components] def placeholder at L4389


# Sprint F238E Phase C: Optional runtime_timing section in JSON export
_MAX_RUNTIME_TIMING_EVENTS = 500  # mirror of _MAX_TELEMETRY_EVENTS in sprint_timer.py

from hledac.universal.runtime.sprint_timer import compute_runtime_loop_telemetry


    # [IMPORTED from components] def placeholder at L4489


# Sprint F240B: Runtime Telemetry Drives Operator Diagnosis
    # [IMPORTED from components] def placeholder at L4528


def _make_serializable(obj: Any) -> Any:
    """Rekurzivně převede objekt na JSON-serializovatelný dict."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return _make_serializable(obj.__dict__)
    return str(obj)
