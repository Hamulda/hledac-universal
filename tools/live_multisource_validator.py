#!/usr/bin/env python3
"""
Live Multisource Validator — F208H + F208K + F209B + F210C

Reads a live_sprint_measurement JSON artifact and emits PASS/FAIL verdict.
Supports both internal sprint report JSON and benchmark live measurement JSON shapes.
Does NOT execute sprints, network calls, or MLX loads.

F208K additions: guard alias fallback for return_guard_checked and
windup_guard_call_count from canonical nested locations.

F209B additions: acquisition_prelude awareness — distinguishes
  - prelude not checked
  - prelude ran but missing mandatory lanes
  - prelude terminal but final terminality stale
  - final terminality genuinely unsatisfied

F210C additions: stale terminality snapshot detection — a lane appears
terminal/attempted in source_family_outcomes but still listed in missing_lanes.
Always-on, fail-closed; dry-run artifacts (run_status=="planned") bypass all live checks.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

# ── Verdict taxonomy ────────────────────────────────────────────────────────

class Verdict(StrEnum):
    PASS_MULTISOURCE_TERMINALITY   = "PASS_MULTISOURCE_TERMINALITY"
    FAIL_TERMINALITY_NOT_CHECKED   = "FAIL_TERMINALITY_NOT_CHECKED"
    FAIL_TERMINALITY_NOT_SATISFIED = "FAIL_TERMINALITY_NOT_SATISFIED"
    FAIL_MISSING_SOURCE_OUTCOMES   = "FAIL_MISSING_SOURCE_OUTCOMES"
    FAIL_PUBLIC_NOT_TERMINAL       = "FAIL_PUBLIC_NOT_TERMINAL"
    FAIL_CT_NOT_TERMINAL          = "FAIL_CT_NOT_TERMINAL"
    FAIL_SCHEDULER_EXIT_MISSING    = "FAIL_SCHEDULER_EXIT_MISSING"
    FAIL_RETURN_GUARD_MISSING      = "FAIL_RETURN_GUARD_MISSING"
    FAIL_HARDWARE_TAINTED         = "FAIL_HARDWARE_TAINTED"
    # F209B: acquisition_prelude awareness
    FAIL_ACQUISITION_PRELUDE_NOT_CHECKED    = "FAIL_ACQUISITION_PRELUDE_NOT_CHECKED"
    FAIL_ACQUISITION_PRELUDE_MISSING_LANES  = "FAIL_ACQUISITION_PRELUDE_MISSING_LANES"
    FAIL_TERMINALITY_STALE_AFTER_PRELUDE    = "FAIL_TERMINALITY_STALE_AFTER_PRELUDE"
    # F210C: stale terminality snapshot — lane terminal/attempted in source_family_outcomes but still in missing_lanes
    FAIL_TERMINALITY_STALE_SNAPSHOT         = "FAIL_TERMINALITY_STALE_SNAPSHOT"
    # F207K: public acceptance KPI — systemic rejection or low yield
    FAIL_PUBLIC_ACCEPTANCE_KPI              = "FAIL_PUBLIC_ACCEPTANCE_KPI"
    WARN_PUBLIC_ACCEPTANCE_KPI              = "WARN_PUBLIC_ACCEPTANCE_KPI"


# ── Module-level aliases for all Verdict members (for hasattr() compatibility) ──
for _member in Verdict:
    globals()[_member.name] = _member
del _member


@dataclass
class ValidationFailure:
    verdict: Verdict
    reason: str
    field_path: str | None = None

VALIDATOR_SCHEMA_VERSION = "f209b.validator.v1"


@dataclass
class ValidationResult:
    overall: Verdict
    failures: list[ValidationFailure] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "validator": "live_multisource_validator",
            "version": VALIDATOR_SCHEMA_VERSION,
            "overall_verdict": self.overall.value,
            "pass": self.overall == Verdict.PASS_MULTISOURCE_TERMINALITY,
            "failure_count": len(self.failures),
            "failures": [
                {
                    "verdict": f.verdict.value,
                    "reason": f.reason,
                    "field_path": f.field_path,
                }
                for f in self.failures
            ],
            "metadata": self.metadata,
        }
# ── Terminal state helpers ──────────────────────────────────────────────────
TERMINAL_STATES = frozenset([
    "COMPLETED",
    "TERMINATED",
    "SATISFIED",
    "EXHAUSTED",
    "NEVER_ATTEMPTED",  # explicit never-attempted is also terminal
])
def is_terminal(state: str | None) -> bool:
    if state is None:
        return False
    return state.upper() in TERMINAL_STATES

# ── F208K: Guard alias helpers ──────────────────────────────────────────────
def _extract_return_guard_checked(data: dict) -> bool | None:
    """Extract return_guard_checked from canonical aliases.
    Supported aliases (checked in order):
      data["return_guard"]["return_guard_checked"]
      data["return_guard"]["checked"]
      data["acquisition_report"]["return_guard"]["return_guard_checked"]
      data["canonical_run_summary"]["return_guard"]["return_guard_checked"]
      data["live_kpi"]["return_guard_checked"]
      data["return_guard_checked"]  (top-level)
    Returns None if no alias resolves to a truthy value.
    """
    # Direct top-level
    val = data.get("return_guard_checked")
    if isinstance(val, bool):
        return val
    # return_guard nested
    rg = data.get("return_guard")
    if isinstance(rg, dict):
        val = rg.get("return_guard_checked")
        if isinstance(val, bool):
            return val
        val = rg.get("checked")
        if isinstance(val, bool):
            return val

    # acquisition_report nested return_guard (canonical — before live_kpi)
    acq = data.get("acquisition_report") or {}
    if isinstance(acq, dict):
        rg_nested = acq.get("return_guard") or {}
        if isinstance(rg_nested, dict):
            val = rg_nested.get("return_guard_checked")
            if isinstance(val, bool):
                return val

    # canonical_run_summary nested return_guard (canonical — before live_kpi)
    crs = data.get("canonical_run_summary") or {}
    if isinstance(crs, dict):
        rg_crs = crs.get("return_guard") or {}
        if isinstance(rg_crs, dict):
            val = rg_crs.get("return_guard_checked")
            if isinstance(val, bool):
                return val

    # live_kpi fallback (checked after canonical sources — live_kpi may be stale)
    live_kpi = data.get("live_kpi") or {}
    if isinstance(live_kpi, dict):
        val = live_kpi.get("return_guard_checked")
        if isinstance(val, bool):
            return val

    return None


def _extract_windup_guard_call_count(data: dict) -> int | None:
    """Extract windup_guard_call_count from canonical aliases.
    Supported aliases (checked in order):
      data["windup_guard_observation"]["windup_guard_call_count"]
      data["windup_guard_observation"]["call_count"]
      data["acquisition_report"]["windup_guard_observation"]["windup_guard_call_count"]
      data["canonical_run_summary"]["windup_guard_observation"]["windup_guard_call_count"]
      data["live_kpi"]["windup_guard_call_count"]
      data["windup_guard_call_count"]  (top-level)
    Returns None if no alias resolves to an int.
    """
    # Direct top-level
    val = data.get("windup_guard_call_count")
    if isinstance(val, (int, float)):
        return int(val)

    # windup_guard_observation nested
    wg = data.get("windup_guard_observation")
    if isinstance(wg, dict):
        val = wg.get("windup_guard_call_count")
        if isinstance(val, (int, float)):
            return int(val)
        val = wg.get("call_count")
        if isinstance(val, (int, float)) and val != 0:
            return int(val)

    # acquisition_report nested windup_guard_observation (canonical — before live_kpi)
    acq = data.get("acquisition_report") or {}
    if isinstance(acq, dict):
        wg_nested = acq.get("windup_guard_observation") or {}
        if isinstance(wg_nested, dict):
            val = wg_nested.get("windup_guard_call_count")
            if isinstance(val, (int, float)):
                return int(val)

    # canonical_run_summary nested windup_guard_observation (canonical — before live_kpi)
    crs = data.get("canonical_run_summary") or {}
    if isinstance(crs, dict):
        wg_crs = crs.get("windup_guard_observation") or {}
        if isinstance(wg_crs, dict):
            val = wg_crs.get("windup_guard_call_count")
            if isinstance(val, (int, float)):
                return int(val)

    # live_kpi fallback (checked after canonical sources — live_kpi may be stale)
    live_kpi = data.get("live_kpi") or {}
    if isinstance(live_kpi, dict):
        val = live_kpi.get("windup_guard_call_count")
        if isinstance(val, (int, float)):
            return int(val)

    return None


def _extract_windup_irrelevant_reason(data: dict) -> tuple[str | None, bool | None]:
    """Extract windup irrelevant reason + windup_not_applicable from canonical aliases.

    Returns (reason_string, windup_not_applicable_bool) or (None, None).
    Supported reason aliases:
      data["windup_guard_observation"]["last_reason"]
      data["windup_guard_reason"]
      data["live_kpi"]["windup_guard_last_reason"]
    Supported not_applicable aliases:
      data["windup_guard_observation"]["windup_guard_not_applicable"]
      data["windup_guard_not_applicable"]
      data["live_kpi"]["windup_guard_not_applicable"]
    """
    reason: str | None = None
    not_applicable: bool | None = None

    # live_kpi first (most common in benchmark shape)
    live_kpi = data.get("live_kpi") or {}
    if isinstance(live_kpi, dict):
        reason = live_kpi.get("windup_guard_last_reason")
        if not isinstance(reason, str):
            reason = None
        nap = live_kpi.get("windup_guard_not_applicable")
        if isinstance(nap, bool):
            not_applicable = nap

    # windup_guard_observation nested (internal report shape)
    wg = data.get("windup_guard_observation")
    if isinstance(wg, dict):
        if reason is None:
            reason = wg.get("last_reason")
            if not isinstance(reason, str):
                reason = None
        nap = wg.get("windup_guard_not_applicable")
        if isinstance(nap, bool) and not_applicable is None:
            not_applicable = nap

    # top-level windup_guard_reason
    if reason is None:
        reason = data.get("windup_guard_reason")
        if not isinstance(reason, str):
            reason = None

    # top-level windup_guard_not_applicable
    nap = data.get("windup_guard_not_applicable")
    if isinstance(nap, bool) and not_applicable is None:
        not_applicable = nap

    # acquisition_report nested windup_guard_observation
    acq = data.get("acquisition_report") or {}
    if isinstance(acq, dict):
        wg_nested = acq.get("windup_guard_observation") or {}
        if isinstance(wg_nested, dict) and reason is None:
            reason = wg_nested.get("windup_guard_last_reason")
            if not isinstance(reason, str):
                reason = None
        nap = wg_nested.get("windup_guard_not_applicable") if isinstance(wg_nested, dict) else None
        if isinstance(nap, bool) and not_applicable is None:
            not_applicable = nap

    # canonical_run_summary nested windup_guard_observation
    crs = data.get("canonical_run_summary") or {}
    if isinstance(crs, dict):
        wg_crs = crs.get("windup_guard_observation") or {}
        if isinstance(wg_crs, dict) and reason is None:
            reason = wg_crs.get("windup_guard_last_reason")
            if not isinstance(reason, str):
                reason = None
        nap = wg_crs.get("windup_guard_not_applicable") if isinstance(wg_crs, dict) else None
        if isinstance(nap, bool) and not_applicable is None:
            not_applicable = nap

    return reason, not_applicable


# ── Shape-aware extraction helpers ─────────────────────────────────────────

def _extract_run_status(data: dict) -> str | None:
    """Extract run status from benchmark or internal report shape."""
    return data.get("status") or data.get("live_run_status") or data.get("run_status")

def _extract_run_id(data: dict) -> str | None:
    """Extract run ID from benchmark or internal report shape."""
    return data.get("measurement_id") or data.get("run_id")

def _extract_branch_mix(data: dict) -> dict:
    """Extract branch_mix — benchmark nests under runtime_truth, internal has it top-level."""
    rt = data.get("runtime_truth") or {}
    if isinstance(rt, dict) and "branch_mix" in rt:
        return rt.get("branch_mix") or {}
    return data.get("branch_mix") or {}

def _extract_live_kpi(data: dict) -> dict:
    """Extract live_kpi from benchmark shape."""
    return data.get("live_kpi") or {}

def _extract_acquisition_report(data: dict) -> dict:
    """Extract acquisition_report — prefer top-level, fall back to nested in live_kpi."""
    top = data.get("acquisition_report") or {}
    if isinstance(top, dict) and top.get("schema_version"):
        return top
    # Benchmark shape: acquisition_report can be directly inside live_kpi
    live_kpi = _extract_live_kpi(data)
    acq_in_live_kpi = live_kpi.get("acquisition_report") or {}
    if isinstance(acq_in_live_kpi, dict) and acq_in_live_kpi.get("schema_version"):
        return acq_in_live_kpi
    # Benchmark shape: acquisition_report nested inside live_kpi.acquisition_strategy
    nested = live_kpi.get("acquisition_strategy") or {}
    if isinstance(nested, dict):
        acq = nested.get("acquisition_report") or {}
        if isinstance(acq, dict) and acq.get("schema_version"):
            return acq
    # Fall back to whatever top-level we found (even if schema_version missing — caller checks)
    if isinstance(top, dict) and top:
        return top
    return {}

def _extract_source_family_outcomes(acq_report: dict, data: dict | None = None) -> dict | None:
    """Extract source_family_outcomes from acquisition_report, with top-level and live_kpi fallback.

    Resolves these locations in priority order:
      1. acq_report["source_family_outcomes"]           (internal shape)
      2. data["source_family_outcomes"]                 (top-level / benchmark shape)
      3. live_kpi["source_family_outcomes"]             (live_kpi fallback)
    """
    sf = acq_report.get("source_family_outcomes") if isinstance(acq_report, dict) else None
    if sf:
        return sf
    # Top-level / benchmark shape: source_family_outcomes at data root
    if data is not None:
        sf = data.get("source_family_outcomes") if isinstance(data, dict) else None
        if sf:
            return sf
        # live_kpi fallback
        live_kpi = (data.get("live_kpi") or {}) if isinstance(data, dict) else {}
        sf = live_kpi.get("source_family_outcomes")
        if sf:
            return sf
    return None


def _extract_terminality_fields(data: dict) -> tuple:
    """Extract terminality fields from top-level, acquisition_report.terminality, or live_kpi fallback."""
    checked = data.get("acquisition_terminality_checked")
    satisfied = data.get("acquisition_terminality_satisfied")
    missing_lanes = data.get("acquisition_terminality_missing_lanes")
    # Derive from acquisition_report.terminality when top-level fields are absent
    if checked is None or satisfied is None or missing_lanes is None:
        acq_report = _extract_acquisition_report(data)
        terminality = (acq_report.get("terminality") or {}) if isinstance(acq_report, dict) else {}
        if checked is None:
            checked = True if isinstance(terminality.get("checked"), list) else None
        if satisfied is None:
            ml = terminality.get("missing_lanes")
            if isinstance(ml, list):
                satisfied = (len(ml) == 0)
        if missing_lanes is None:
            missing_lanes = terminality.get("missing_lanes")
    # Final live_kpi fallback
    if checked is None or satisfied is None or missing_lanes is None:
        live_kpi = _extract_live_kpi(data)
        if checked is None:
            checked = live_kpi.get("acquisition_terminality_checked")
        if satisfied is None:
            satisfied = live_kpi.get("acquisition_terminality_satisfied")
        if missing_lanes is None:
            missing_lanes = live_kpi.get("acquisition_terminality_missing_lanes")

    return checked, satisfied, missing_lanes


# ── F209B: Acquisition Prelude extraction ─────────────────────────────────

def _extract_acquisition_prelude(data: dict) -> dict:
    """Extract acquisition_prelude fields from all canonical locations.

    Returns a dict with keys:
      prelude_checked: bool | None
      prelude_missing_lanes: list | None
      prelude_terminal_lanes: list | None
      terminal_lanes_from_prelude: bool  (whether terminal_lanes came from prelude vs terminality)

    Checks these locations in order (first wins):
      - top-level: data["acquisition_prelude"]
      - live_kpi: data["live_kpi"]["acquisition_prelude"]
      - acquisition_report: data["acquisition_report"]["acquisition_prelude"]
      - canonical_run_summary: data["canonical_run_summary"]["acquisition_prelude"]
    """
    NONE = object()  # sentinel for "not found"

    def _getnested(container: dict, *keys) -> tuple:
        """Walk nested keys, return (value, found_bool)."""
        val = container
        for k in keys:
            if not isinstance(val, dict):
                return NONE, False
            val = val.get(k, NONE)
            if val is NONE:
                return NONE, False
        return val, True

    # Try each canonical location in priority order
    locations = [
        ("top-level", data),
        ("live_kpi", data.get("live_kpi") or {}),
        ("acquisition_report", _extract_acquisition_report(data)),
        ("canonical_run_summary", data.get("canonical_run_summary") or {}),
    ]

    prelude_checked: bool | None = None
    prelude_missing_lanes: list | None = None
    prelude_terminal_lanes: list | None = None

    for _label, container in locations:
        if not isinstance(container, dict) or not container:
            continue
        prelude = container.get("acquisition_prelude")
        if not isinstance(prelude, dict):
            continue

        if prelude_checked is None:
            prelude_checked = prelude.get("prelude_checked")
            if not isinstance(prelude_checked, bool):
                prelude_checked = None

        if prelude_missing_lanes is None:
            ml = prelude.get("prelude_missing_lanes")
            if isinstance(ml, list):
                prelude_missing_lanes = ml

        if prelude_terminal_lanes is None:
            tl = prelude.get("prelude_terminal_lanes")
            if isinstance(tl, list) and len(tl) > 0:
                prelude_terminal_lanes = tl

        # Stop once we have all three fields
        if prelude_checked is not None and prelude_missing_lanes is not None and prelude_terminal_lanes is not None:
            break

    return {
        "prelude_checked": prelude_checked,
        "prelude_missing_lanes": prelude_missing_lanes,
        "prelude_terminal_lanes": prelude_terminal_lanes,
    }


def _resolve_branch_count(branch_mix: dict | None, live_kpi: dict, key: str) -> int:
    """Resolve branch count from branch_mix aliases with live_kpi fallback.
    Resolves these aliases for feed/public/ct keys:
      branch_mix["feed"]           → feed_count
      branch_mix["feed_findings"]  → feed_count  (benchmark shape)
      branch_mix["public_findings"]→ public_count (benchmark shape)
      branch_mix["ct_findings"]    → ct_count     (benchmark shape)
      live_kpi["source_family_counts"]["feed"] → feed_count (live_kpi shape)
    """
    if not isinstance(branch_mix, dict):
        return 0

    # Primary key (internal shape: branch_mix["feed"])
    if key in branch_mix:
        val = branch_mix.get(key)
        if isinstance(val, (int, float)):
            return int(val)

    # _findings suffix (benchmark shape: feed_findings, public_findings, ct_findings)
    findings_key = f"{key}_findings"
    if findings_key in branch_mix:
        val = branch_mix.get(findings_key)
        if isinstance(val, (int, float)):
            return int(val)

    # live_kpi.source_family_counts fallback (live_kpi shape)
    if isinstance(live_kpi, dict):
        sfc = live_kpi.get("source_family_counts") or {}
        if isinstance(sfc, dict) and key in sfc:
            val = sfc.get(key)
            if isinstance(val, (int, float)):
                return int(val)

    return 0


# ── F210C: Stale terminality snapshot detector ───────────────────────────────

TERMINAL_OUTCOME_STATES: frozenset[str] = frozenset([
    "ATTEMPTED", "SKIPPED", "ERROR", "TIMEOUT",
    "TERMINAL", "COMPLETED", "SATISFIED", "EXHAUSTED",
    "NEVER_ATTEMPTED",  # explicit never-attempted is terminal-like for our purposes
])


def _detect_terminality_source_outcome_mismatch(
    sf_outcomes: dict | None,
    missing_lanes: list | None,
) -> list[str]:
    """Detect lanes that appear terminal/attempted in source_family_outcomes
    but are still listed in missing_lanes.

    A mismatch means the acquisition terminality snapshot is stale — the lane
    was resolved at execution time (source_family_outcomes reflects reality)
    but the terminality record still lists it as missing.

    Returns a list of mismatched lane names.
    """
    if not isinstance(sf_outcomes, dict) or not isinstance(missing_lanes, list):
        return []

    mismatched: list[str] = []
    for lane in missing_lanes:
        if not isinstance(lane, str):
            continue
        lane_upper = lane.upper()
        # Check if any family outcome mentions this lane as terminal/attempted
        for family, outcome in sf_outcomes.items():
            if not isinstance(outcome, dict):
                continue
            state = outcome.get("state") or outcome.get("status") or outcome.get("terminal_state") or ""
            family_upper = family.upper()
            if (
                (lane_upper == family_upper or lane_upper in family_upper or family_upper in lane_upper)
                and state.upper() in TERMINAL_OUTCOME_STATES
            ):
                mismatched.append(lane)
                break
    return mismatched


def _extract_guard_fields(data: dict) -> tuple:
    """Extract windup/return guard fields from benchmark or internal shape.

    Uses F208K alias helpers to resolve all canonical nested locations.
    Returns (windup_count, windup_reason, windup_not_applicable, return_guard_checked, scheduler_exit).
    """
    # F208K: use dedicated alias helpers
    windup_count = _extract_windup_guard_call_count(data)
    windup_reason, windup_not_applicable = _extract_windup_irrelevant_reason(data)
    return_guard_checked = _extract_return_guard_checked(data)

    # scheduler_exit still from top-level / live_kpi (no change needed)
    scheduler_exit = data.get("scheduler_exit_path")
    if scheduler_exit is None:
        live_kpi = _extract_live_kpi(data)
        scheduler_exit = live_kpi.get("scheduler_exit_path")
        if scheduler_exit is None:
            # acquisition_report.scheduler_exit path (internal shape)
            acq = _extract_acquisition_report(data)
            sc_exit = acq.get("scheduler_exit") or {}
            if isinstance(sc_exit, dict):
                scheduler_exit = sc_exit.get("exit_path")
    return windup_count, windup_reason, windup_not_applicable, return_guard_checked, scheduler_exit

def _failures_from_dict(data: dict, profile: str, query_type: str, allow_hardware_constrained: bool) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    failures_append = failures.append

    # ── Dry-run / benchmark artifact — no live execution data ──────────────
    run_status = _extract_run_status(data)
    if run_status == "planned":
        return []  # Benchmark artifact; live terminality checks N/A

    # ── Shape-aware extraction ────────────────────────────────────────────────
    acq_report = _extract_acquisition_report(data)
    sf_outcomes = _extract_source_family_outcomes(acq_report, data)
    branch_mix = _extract_branch_mix(data)
    term_checked, term_satisfied, missing_lanes = _extract_terminality_fields(data)
    windup_count, windup_reason, windup_not_applicable, return_guard_checked, scheduler_exit = _extract_guard_fields(data)
    live_kpi = _extract_live_kpi(data)

    # ── F209B: Acquisition Prelude awareness ────────────────────────────────
    # Only applies to active300 domain profile
    prelude = _extract_acquisition_prelude(data)
    prelude_checked = prelude["prelude_checked"]
    prelude_missing_lanes = prelude["prelude_missing_lanes"]
    prelude_terminal_lanes = prelude["prelude_terminal_lanes"]

    if profile == "active300" and query_type == "domain":
        # Rule 0: Skip if acquisition_prelude not present (dry-run / benchmark artifact)
        if prelude_checked is None:
            pass  # No acquisition data — prelude rules N/A for dry-run artifacts
        # Rule 1: active300 domain + prelude_checked false/None → PRELUDE_NOT_CHECKED
        elif prelude_checked is not True:
            failures_append(ValidationFailure(
                Verdict.FAIL_ACQUISITION_PRELUDE_NOT_CHECKED,
                f"acquisition_prelude.prelude_checked is {prelude_checked!r}, expected true for active300 domain",
                "acquisition_prelude.prelude_checked",
            ))

        # Rule 2: prelude_checked true + prelude_missing_lanes non-empty → PRELUDE_MISSING_LANES
        if prelude_checked is True and prelude_missing_lanes and len(prelude_missing_lanes) > 0:
            failures_append(ValidationFailure(
                Verdict.FAIL_ACQUISITION_PRELUDE_MISSING_LANES,
                f"acquisition_prelude.prelude_missing_lanes = {prelude_missing_lanes}, expected []",
                "acquisition_prelude.prelude_missing_lanes",
            ))

        # Rule 3: prelude terminal lanes contains PUBLIC/CT but final terminality missing_lanes
        # contains PUBLIC/CT → TERMINALITY_STALE_AFTER_PRELUDE
        if (prelude_checked is True
                and prelude_missing_lanes is not None and len(prelude_missing_lanes) == 0
                and prelude_terminal_lanes is not None and missing_lanes is not None):
            MUST_TERMINAL = frozenset(["PUBLIC", "CT"])
            prelude_has_mandatory = MUST_TERMINAL.intersection(
                l.upper() for l in prelude_terminal_lanes if isinstance(l, str)
            )
            final_has_mandatory = MUST_TERMINAL.intersection(
                l.upper() for l in missing_lanes if isinstance(l, str)
            )
            if prelude_has_mandatory and final_has_mandatory:
                # Prelude said these lanes terminal, but final says they're still missing
                failures_append(ValidationFailure(
                    Verdict.FAIL_TERMINALITY_STALE_AFTER_PRELUDE,
                    f"prelude terminal_lanes={prelude_terminal_lanes} but final missing_lanes={missing_lanes} — "
                    f"terminality regressed after prelude",
                    "acquisition_prelude.prelude_terminal_lanes",
                ))

    # ── F210C: stale terminality snapshot ────────────────────────────────────
    # A lane appears terminal/attempted in source_family_outcomes but remains
    # in acquisition_report.terminality.missing_lanes — snapshot is stale.
    mismatched_lanes = _detect_terminality_source_outcome_mismatch(
        sf_outcomes, missing_lanes,
    )
    if mismatched_lanes:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_STALE_SNAPSHOT,
            f"source_family_outcomes shows lanes {mismatched_lanes} terminal/attempted "
            f"but missing_lanes still contains them: {missing_lanes}",
            "acquisition_report.terminality.missing_lanes",
        ))

    # ── 1. run_status completed ─────────────────────────────────────────────
    if run_status is not None and run_status not in ("completed", "planned"):
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"run_status is '{run_status}', expected 'completed'",
            "run_status",
        ))
    # ── 2. hardware taint ───────────────────────────────────────────────────
    quality_verdict = (data.get("run_quality_verdict") or live_kpi.get("run_quality_verdict") or "").lower()
    hardware_tainted = (
        "hardware-constrained" in quality_verdict
        or "hardware_constrained" in quality_verdict
    )
    if hardware_tainted and not allow_hardware_constrained:
        failures_append(ValidationFailure(
            Verdict.FAIL_HARDWARE_TAINTED,
            f"run_quality_verdict '{quality_verdict}' indicates hardware constraint",
            "run_quality_verdict",
        ))

    # ── 3. acquisition_report.schema_version present ────────────────────────
    schema_version = acq_report.get("schema_version") if isinstance(acq_report, dict) else None
    if not schema_version:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            "acquisition_report.schema_version is missing",
            "acquisition_report.schema_version",
        ))
    # ── 4. acquisition_terminality_checked == true ───────────────────────────
    if term_checked is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"acquisition_terminality_checked is {term_checked!r}, expected true",
            "acquisition_terminality_checked",
        ))

    # ── 5. acquisition_terminality_satisfied == true ───────────────────────
    if term_satisfied is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_SATISFIED,
            f"acquisition_terminality_satisfied is {term_satisfied!r}, expected true",
            "acquisition_terminality_satisfied",
        ))

    # ── 6. acquisition_terminality_missing_lanes == [] ───────────────────────
    if missing_lanes is None:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            "acquisition_terminality_missing_lanes is null",
            "acquisition_terminality_missing_lanes",
        ))
    elif missing_lanes != []:
        # Terminality was checked but required lanes are missing → NOT_SATISFIED
        if term_checked is True:
            failures_append(ValidationFailure(
                Verdict.FAIL_TERMINALITY_NOT_SATISFIED,
                f"acquisition_terminality_missing_lanes = {missing_lanes}, expected []",
                "acquisition_terminality_missing_lanes",
            ))
        else:
            failures_append(ValidationFailure(
                Verdict.FAIL_TERMINALITY_NOT_CHECKED,
                f"acquisition_terminality_missing_lanes = {missing_lanes}, expected []",
                "acquisition_terminality_missing_lanes",
            ))

    # ── 7. source_family_outcomes present and non-empty ─────────────────────
    if not sf_outcomes:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            f"source_family_outcomes is {sf_outcomes!r}, expected non-empty dict",
            "acquisition_report.source_family_outcomes",
        ))
    elif isinstance(sf_outcomes, dict) and len(sf_outcomes) == 0:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            "source_family_outcomes is empty dict",
            "acquisition_report.source_family_outcomes",
        ))

    # ── 8. feed attempted OR feed count > 0 ─────────────────────────────────
    feed_count = _resolve_branch_count(branch_mix, live_kpi, "feed")
    sf_outcomes_keys = list(sf_outcomes.keys()) if isinstance(sf_outcomes, dict) else []
    feed_in_outcomes = "feed" in sf_outcomes_keys
    attempted_feed = feed_count > 0 or feed_in_outcomes
    if not attempted_feed:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            f"No feed findings attempted. feed_count={feed_count}, feed_in_outcomes={feed_in_outcomes}",
            "branch_mix.feed",
        ))
    elif feed_count == 0 and feed_in_outcomes:
        pass  # feed_in_outcomes proves feed was attempted even when count is 0

    # ── 9. PUBLIC terminal state for domain query ───────────────────────────
    if profile == "active300" and query_type == "domain":
        public_state = data.get("public_terminal_state", "")
        if not public_state:
            public_state = live_kpi.get("public_terminal_state", "")
        public_state = public_state.upper() if public_state else ""
        if public_state == "NEVER_ATTEMPTED":
            failures_append(ValidationFailure(
                Verdict.FAIL_PUBLIC_NOT_TERMINAL,
                "PUBLIC lane never attempted for domain query",
                "public_terminal_state",
            ))
        elif public_state and not is_terminal(public_state):
            failures_append(ValidationFailure(
                Verdict.FAIL_PUBLIC_NOT_TERMINAL,
                f"PUBLIC terminal_state '{public_state}' is not terminal",
                "public_terminal_state",
            ))

    # ── 10. CT terminal state for domain query ──────────────────────────────
    if profile == "active300" and query_type == "domain":
        ct_state = data.get("ct_terminal_state", "")
        if not ct_state:
            ct_state = live_kpi.get("ct_terminal_state", "")
        ct_state = ct_state.upper() if ct_state else ""
        if ct_state == "NEVER_ATTEMPTED":
            failures_append(ValidationFailure(
                Verdict.FAIL_CT_NOT_TERMINAL,
                "CT lane never attempted for domain query",
                "ct_terminal_state",
            ))
        elif ct_state and not is_terminal(ct_state):
            failures_append(ValidationFailure(
                Verdict.FAIL_CT_NOT_TERMINAL,
                f"CT terminal_state '{ct_state}' is not terminal",
                "ct_terminal_state",
            ))

    # ── 11. scheduler_exit_path non-empty ───────────────────────────────────
    if not scheduler_exit or len(str(scheduler_exit).strip()) == 0:
        failures_append(ValidationFailure(
            Verdict.FAIL_SCHEDULER_EXIT_MISSING,
            "scheduler_exit_path is empty",
            "scheduler_exit_path",
        ))

    # ── 12. return_guard_checked == true (F208K: reads all canonical aliases) ──
    if return_guard_checked is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_RETURN_GUARD_MISSING,
            f"return_guard_checked is {return_guard_checked!r}, expected true",
            "return_guard_checked",
        ))

    # ── 13. windup_guard_call_count > 0 OR explicit reason (F208K: reads all canonical aliases) ──
    if windup_count is None:
        windup_count = 0
    windup_irrelevant_reasons = frozenset({"not_applicable", "no_lanes_ran", "disabled", "skipped"})
    has_explicit_reason = (
        str(windup_reason or "").lower() in windup_irrelevant_reasons
        or windup_not_applicable is True
    )
    if not (windup_count > 0 or has_explicit_reason):
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"windup_guard_call_count={windup_count} with no explicit reason why not applicable",
            "windup_guard_call_count",
        ))

    # ── F207K: Public acceptance KPI checks ──────────────────────────────
    verdict_kpi, msg_kpi = _check_public_acceptance_kpi(data, _get_safe)
    if verdict_kpi is not None and verdict_kpi in (Verdict.FAIL_PUBLIC_ACCEPTANCE_KPI, Verdict.WARN_PUBLIC_ACCEPTANCE_KPI):
        failures_append(ValidationFailure(
            verdict_kpi,
            f"public_acceptance_kpi: {msg_kpi}",
            "live_kpi.public_acceptance_*",
        ))

    verdict_fetch, msg_fetch = _check_public_fetch_telemetry(data, _get_safe)
    if verdict_fetch is not None and verdict_fetch == Verdict.WARN_PUBLIC_ACCEPTANCE_KPI:
        failures_append(ValidationFailure(
            verdict_fetch,
            f"public_fetch_telemetry: {msg_fetch}",
            "live_kpi.public_fetch_*",
        ))

    return failures
# ── F207K: Public Acceptance KPI checks ───────────────────────────────────

def _get_safe(data: dict, *keys, default=None):
    """Chained safe dict getter."""
    val = data
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k, default)
        if val is default:
            return default
    return val


def _check_public_acceptance_kpi(data: dict, _get=_get_safe) -> tuple[Verdict | None, str]:
    """
    Validates F207K public acceptance KPI fields.

    FAIL: acceptance_rate < 1% with attempted > 100
          (systemic rejection — likely misconfiguration)
    WARN: acceptance_rate < 10% with attempted > 50
          (low yield — possible quality gate too strict)
    WARN: next_action not in VALID_NEXT_ACTIONS
    INFO: public_acceptance_* absent (non-public report)
    """
    VALID_NEXT_ACTIONS = {
        "PROCEED", "RETRY", "ABORT", "ESCALATE",
        "DEGRADE", "SKIP_PUBLIC", "NONFEED_ONLY",
        "fix_scheduler_return_guard_not_called",
        "post_sleep_windup_break",
        "fix_nonfeed_scheduler_order",
        "run_active600_or_targeted_query",
        "fix_prewindup_barrier_not_called",
    }

    kpi = _get(data, "live_kpi", default=None)
    if kpi is None:
        return None, "live_kpi absent"

    attempted = _get(kpi, "public_acceptance_attempted", default=None)
    accepted  = _get(kpi, "public_acceptance_accepted",  default=None)
    rejected  = _get(kpi, "public_acceptance_rejected",  default=None)
    reasons   = _get(kpi, "public_acceptance_reject_reasons", default={})
    next_act  = _get(kpi, "next_action", default=None)

    if attempted is None and accepted is None:
        return None, "public_acceptance_* absent — non-public report"

    issues = []

    # Acceptance rate check
    if attempted and attempted > 100:
        rate = (accepted or 0) / attempted
        if rate < 0.01:
            issues.append(
                f"CRITICAL: acceptance_rate={rate:.1%} "
                f"(accepted={accepted}/{attempted}) — systemic rejection"
            )
        elif rate < 0.10:
            issues.append(
                f"LOW acceptance_rate={rate:.1%} "
                f"(accepted={accepted}/{attempted})"
            )

    # Consistency check: accepted + rejected should not exceed attempted
    if all(v is not None for v in [attempted, accepted, rejected]):
        if (accepted + rejected) > attempted:
            issues.append(
                f"accepted({accepted})+rejected({rejected}) > attempted({attempted})"
            )

    # Top rejection reason
    if reasons and isinstance(reasons, dict):
        top_reason = max(reasons, key=lambda k: reasons[k])
        top_count  = reasons[top_reason]
        if top_count > (attempted or 0) * 0.5:
            issues.append(
                f"top reject reason={top_reason!r} "
                f"accounts for {top_count}/{attempted} ({top_count/(attempted or 1):.0%})"
            )

    # next_action validation
    if next_act and next_act not in VALID_NEXT_ACTIONS:
        issues.append(f"unknown next_action={next_act!r}")

    # Classify severity
    critical = [i for i in issues if "CRITICAL" in i]
    if critical:
        return Verdict.FAIL_PUBLIC_ACCEPTANCE_KPI, " | ".join(critical)
    if issues:
        return Verdict.WARN_PUBLIC_ACCEPTANCE_KPI, " | ".join(issues)

    return Verdict.PASS_MULTISOURCE_TERMINALITY, (
        f"public_acceptance OK rate={((accepted or 0)/(attempted or 1)):.1%} "
        f"next_action={next_act}"
    )


def _check_public_fetch_telemetry(data: dict, _get=_get_safe) -> tuple[Verdict | None, str]:
    """
    Validates public fetch attempted vs acceptance ratio.
    Catches: fetch_attempted >> acceptance_attempted
             (fetch succeeds but acceptance gate drops everything)
    """
    kpi = _get(data, "live_kpi", default=None)
    if kpi is None:
        return None, "live_kpi absent"

    fetch_attempted  = _get(kpi, "public_fetch_attempted",       default=None)
    accept_attempted = _get(kpi, "public_acceptance_attempted",  default=None)

    if fetch_attempted is None or accept_attempted is None:
        return None, "public fetch telemetry absent"

    # If fetched >> accepted_attempted: pre-acceptance filter is very aggressive
    if fetch_attempted > 0 and accept_attempted is not None:
        pass_rate = accept_attempted / fetch_attempted
        if pass_rate < 0.05:
            return Verdict.WARN_PUBLIC_ACCEPTANCE_KPI, (
                f"pre-acceptance filter drops {1-pass_rate:.0%} of fetched URLs "
                f"(fetch={fetch_attempted}, acceptance_input={accept_attempted})"
            )

    return Verdict.PASS_MULTISOURCE_TERMINALITY, (
        f"fetch telemetry OK fetch={fetch_attempted} "
        f"acceptance_input={accept_attempted}"
    )


# ── Main validation ────────────────────────────────────────────────────────

def validate_live_artifact(
    input_path: str | Path,
    profile: str = "active300",
    query_type: str = "domain",
    allow_hardware_constrained: bool = False,
) -> ValidationResult:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input artifact not found: {path}")
    with path.open() as fh:
        data = json.load(fh)
    failures = _failures_from_dict(data, profile, query_type, allow_hardware_constrained)

    if failures:
        overall = failures[0].verdict
    else:
        overall = Verdict.PASS_MULTISOURCE_TERMINALITY

    metadata = {
        "input_file": str(path),
        "profile": profile,
        "query_type": query_type,
        "run_id": _extract_run_id(data),
        "run_date": data.get("run_date") or data.get("start_time_iso") or "unknown",
        "validated_at": datetime.now(UTC).isoformat(),
    }

    return ValidationResult(overall=overall, failures=failures, metadata=metadata)

def emit_json(result: ValidationResult, output_path: str | Path) -> None:
    with Path(output_path).open("w") as fh:
        json.dump(result.to_dict(), fh, indent=2)

def emit_markdown(result: ValidationResult, output_path: str | Path) -> None:
    lines = [
        "# Live Multisource Validation Report",
        "",
        f"**Overall Verdict:** `{result.overall.value}`",
        f"**Pass:** {'✅ YES' if result.overall == Verdict.PASS_MULTISOURCE_TERMINALITY else '❌ NO'}",
        f"**Validated at:** {result.metadata.get('validated_at', 'unknown')}",
        f"**Input file:** `{result.metadata.get('input_file', 'unknown')}`",
        f"**Profile:** {result.metadata.get('profile', 'unknown')} | **Query type:** {result.metadata.get('query_type', 'unknown')}",
        f"**Run ID:** `{result.metadata.get('run_id', 'unknown')}`",
        "",
        "## Failures",
    ]

    if not result.failures:
        lines.append("_No failures — all checks passed._")
    else:
        for i, f in enumerate(result.failures, 1):
            lines.append(f"{i}. **{f.verdict.value}**")
            lines.append(f"   - Reason: {f.reason}")
            if f.field_path:
                lines.append(f"   - Field: `{f.field_path}`")

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by live_multisource_validator.py {VALIDATOR_SCHEMA_VERSION}*")
    with Path(output_path).open("w") as fh:
        fh.write("\n".join(lines))
# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live Multisource Validator — F209B",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        suggest_on_error=True,
        color=True,
    )
    parser.add_argument("--input-json", required=True, help="Path to live_sprint_measurement JSON artifact")
    parser.add_argument("--output-json", help="Path to write verdict JSON")
    parser.add_argument("--output-md", help="Path to write verdict Markdown report")
    parser.add_argument("--profile", default="active300", help="Profile name (default: active300)")
    parser.add_argument("--query-type", default="domain", help="Query type: domain, identity, leak (default: domain)")
    parser.add_argument("--allow-hardware-constrained", action="store_true", help="Allow hardware-constrained runs to pass")
    args = parser.parse_args(argv)
    try:
        result = validate_live_artifact(
            input_path=args.input_json,
            profile=args.profile,
            query_type=args.query_type,
            allow_hardware_constrained=args.allow_hardware_constrained,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"ERROR: Invalid JSON in {args.input_json}: {exc}\n")
        return 1

    if args.output_json:
        emit_json(result, args.output_json)

    if args.output_md:
        emit_markdown(result, args.output_md)

    # Always print verdict to stdout
    print(result.overall.value)
    return 0 if result.overall == Verdict.PASS_MULTISOURCE_TERMINALITY else 1


if __name__ == "__main__":
    sys.exit(main())
