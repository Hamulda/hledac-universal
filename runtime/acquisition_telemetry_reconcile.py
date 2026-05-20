"""
Sprint F226G: Acquisition Telemetry SSOT Helper.
Sprint F231B: Lane Detail to Source Family Outcome Bridge.
Sprint F250A: Nonfeed Prelude to Source Family Outcome Bridge.

ROLE: Reconcile lane detail fields with source_family_outcomes so reports
never contradict the authoritative outcomes list.

RULES:
  - source_family_outcomes is authoritative when detail fields are missing/default.
  - Normalize family to lowercase before matching.
  - CT attempted/timeout updates ct_request_attempted and ct_terminal_stage.
  - DOH attempted/timeout updates doh_request_attempted and doh_terminal_stage.
  - Wayback/PassiveDNS expected but blank -> explicit skipped/no_terminal state.
  - Do NOT overwrite richer non-default detail fields.
  - Preserve raw_count/accepted_count where possible.
  - No model/network imports.
  - F250A: Nonfeed prelude lane names (WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH, CT)
    are mapped to source_family_outcomes entries even when lane detail fields are blank,
    as long as the lane appears in prelude expected/attempted/terminal/error/accepted sets.

Apply reconcile_lane_detail_fields(report) before final report is
returned/written.

Sprint F231B: complete_source_family_outcomes_from_lane_details applies AFTER
reconcile_lane_detail_fields to ensure lane detail telemetry creates missing
source_family_outcomes entries (the reverse direction).

Sprint F250A: complete_source_family_outcomes_from_prelude applies AFTER
complete_source_family_outcomes_from_lane_details to fill source_family_outcomes
from nonfeed prelude lane sets (expected/attempted/terminal/error/accepted)
for WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR even when no corresponding lane detail
fields exist in the report.
"""

from __future__ import annotations

import logging

__all__ = [
    "reconcile_lane_detail_fields",
    "complete_source_family_outcomes_from_lane_details",
    "complete_source_family_outcomes_from_prelude",
]

logger = logging.getLogger(__name__)


# ── Sprint F231B: helpers ──────────────────────────────────────────────────────

def _normalize_terminal_state(stage: str) -> str:
    """Normalize lane detail terminal stage to source_family_outcomes terminal_state."""
    if not stage:
        return ""
    _l = stage.lower()
    if _l in ("attempted_accepted", "accepted", "storage_accepted"):
        return "ATTEMPTED_ACCEPTED"
    if _l in ("attempted_empty", "no_candidates"):
        return "ATTEMPTED_NO_RESULTS"
    if _l in ("timeout", "request_timeout"):
        return "ATTEMPTED_TIMEOUT"
    if _l in ("provider_error", "dependency_missing", "error"):
        return "ATTEMPTED_ERROR"
    if _l == "skipped":
        return "SKIPPED"
    if _l in ("no_terminal", "terminal_no_results"):
        return "ATTEMPTED_NO_RESULTS"
    if "skipped" in _l:
        return "SKIPPED"
    # [F250B] provider_cooldown/provider_unavailable → ATTEMPTED_ERROR
    if _l in ("provider_cooldown", "provider_unavailable"):
        return "ATTEMPTED_ERROR"
    return stage


def _family_exists(sfo_list: list[dict], family: str) -> bool:
    """Check if a family already exists in source_family_outcomes."""
    for _sfo in sfo_list:
        if (_sfo.get("family") or "").lower() == family.lower():
            return True
    return False


def _add_outcome_if_missing(
    sfo_list: list[dict],
    family: str,
    attempted: bool,
    raw_count: int = 0,
    accepted_count: int = 0,
    terminal_state: str = "",
    timeout: bool = False,
    error: str | None = None,
    skip_reason: str | None = None,
) -> list[dict]:
    """Add family outcome only if it doesn't already exist in the list."""
    if _family_exists(sfo_list, family):
        return sfo_list

    _outcome = {
        "family": family,
        "attempted": attempted,
        "skipped": not attempted,
        "skip_reason": skip_reason,
        "raw_count": raw_count,
        "built_count": 0,
        "accepted_count": accepted_count,
        "error": error,
        "timeout": timeout,
        "duration_s": None,
        "terminal_state": _normalize_terminal_state(terminal_state) if terminal_state else "",
    }
    return sfo_list + [_outcome]


def complete_source_family_outcomes_from_lane_details(report: dict) -> dict:
    """
    Sprint F231B: Complete source_family_outcomes from lane detail fields.

    The reverse of reconcile_lane_detail_fields: lane detail telemetry exists
    (doh_request_attempted, wayback_terminal_state, etc.) but source_family_outcomes
    may be missing the corresponding family entry.

    RULES:
      - Preserve existing source_family_outcomes entries.
      - Normalize family names to lowercase.
      - If a family already exists, do not duplicate — merge only missing fields.
      - If doh_request_attempted or doh_terminal_stage is set and no doh outcome exists,
        add one.
      - If wayback_terminal_state is set and no wayback outcome exists, add one.
      - If passive_dns_terminal_state is set and no passive_dns outcome exists, add one.
      - If ct_terminal_stage is set and no ct outcome exists, add one.
      - If terminal state is blank, do not invent success.
        Use explicit not_attempted_unknown only if the lane was expected/planned/scheduled.
      - Zero accepted findings are valid terminal coverage, not positive corroboration.

    Apply after reconcile_lane_detail_fields in the report pipeline.
    """
    result = dict(report)

    # Ensure source_family_outcomes exists as a list
    sfo_list: list[dict] = result.get("source_family_outcomes") or []
    if not isinstance(sfo_list, list):
        sfo_list = []

    # ── DOH ─────────────────────────────────────────────────────────────────
    _doh_attempted = result.get("doh_request_attempted", False)
    _doh_stage = result.get("doh_terminal_stage", "") or ""
    _doh_raw = result.get("doh_raw_count", 0)
    _doh_accepted = result.get("doh_accepted_findings", 0)
    _doh_errors = result.get("doh_provider_errors", ())
    _doh_planned = result.get("doh_planned", False)
    _doh_scheduled = result.get("doh_scheduled", False)

    if (_doh_attempted or _doh_stage) and not _family_exists(sfo_list, "doh"):
        _err = _doh_errors[0] if _doh_errors else None
        _timeout = _doh_stage == "timeout"
        if not _err:
            if _doh_stage == "no_candidates":
                _err = "no_candidates"
            elif _doh_stage == "attempted_empty":
                _err = "attempted_empty"
            elif _doh_stage == "attempted_accepted":
                _err = None
        _skip = not _doh_attempted and not _doh_stage
        sfo_list = _add_outcome_if_missing(
            sfo_list,
            family="doh",
            attempted=_doh_attempted,
            raw_count=_doh_raw,
            accepted_count=_doh_accepted,
            terminal_state=_doh_stage,
            timeout=_timeout,
            error=_err,
            skip_reason="doh_not_attempted" if _skip else None,
        )
    elif (_doh_planned or _doh_scheduled) and not _doh_attempted and not _doh_stage:
        # Expected/planned but never attempted — explicit skipped coverage
        if not _family_exists(sfo_list, "doh"):
            sfo_list = _add_outcome_if_missing(
                sfo_list,
                family="doh",
                attempted=False,
                raw_count=0,
                accepted_count=0,
                terminal_state="",
                skip_reason="planned_not_attempted",
            )

    # ── Wayback ─────────────────────────────────────────────────────────────
    _wb_stage = result.get("wayback_terminal_state", "") or ""
    _wb_raw = result.get("wayback_raw_count", 0)
    _wb_accepted = result.get("wayback_accepted_count", 0)
    _wb_planned = result.get("wayback_planned", False)
    _wb_scheduled = result.get("wayback_scheduled", False)

    if _wb_stage and not _family_exists(sfo_list, "wayback"):
        _err = None
        _attempted = True
        if _wb_stage in ("no_terminal", "terminal_no_results"):
            _err = "no_terminal"
        elif _wb_stage == "skipped":
            # "skipped" means the lane did not run — attempted=False
            _attempted = False
            _err = "skipped"
        elif _wb_stage == "wayback_unchanged_rejected":
            _err = _wb_stage
        sfo_list = _add_outcome_if_missing(
            sfo_list,
            family="wayback",
            attempted=_attempted,
            raw_count=_wb_raw,
            accepted_count=_wb_accepted,
            terminal_state=_wb_stage,
            error=_err,
        )
    elif (_wb_planned or _wb_scheduled) and not _wb_stage:
        if not _family_exists(sfo_list, "wayback"):
            sfo_list = _add_outcome_if_missing(
                sfo_list,
                family="wayback",
                attempted=False,
                raw_count=0,
                accepted_count=0,
                terminal_state="",
                skip_reason="planned_not_attempted",
            )

    # ── PassiveDNS ──────────────────────────────────────────────────────────
    _pdns_stage = result.get("passive_dns_terminal_state", "") or ""
    _pdns_raw = result.get("passive_dns_raw_count", 0)
    _pdns_accepted = result.get("passive_dns_accepted_count", 0)
    _pdns_planned = result.get("passive_dns_planned", False)
    _pdns_scheduled = result.get("passive_dns_scheduled", False)

    if _pdns_stage and not _family_exists(sfo_list, "passive_dns"):
        _err = None
        _attempted = True
        if _pdns_stage in ("no_terminal", "terminal_no_results"):
            _err = "no_terminal"
        elif _pdns_stage == "skipped":
            # "skipped" means the lane did not run — attempted=False
            _attempted = False
            _err = "skipped"
        sfo_list = _add_outcome_if_missing(
            sfo_list,
            family="passive_dns",
            attempted=_attempted,
            raw_count=_pdns_raw,
            accepted_count=_pdns_accepted,
            terminal_state=_pdns_stage,
            error=_err,
        )
    elif (_pdns_planned or _pdns_scheduled) and not _pdns_stage:
        if not _family_exists(sfo_list, "passive_dns"):
            sfo_list = _add_outcome_if_missing(
                sfo_list,
                family="passive_dns",
                attempted=False,
                raw_count=0,
                accepted_count=0,
                terminal_state="",
                skip_reason="planned_not_attempted",
            )

    # ── CT (if missing but detail fields exist) ─────────────────────────────
    _ct_attempted = result.get("ct_request_attempted", False)
    _ct_stage = result.get("ct_terminal_stage", "") or ""
    _ct_raw = result.get("ct_raw_count", 0)
    _ct_accepted = result.get("ct_storage_accepted", False)
    # [F250B] Derive ct_terminal_stage from ct_provider_status when stage is empty
    if not _ct_stage:
        _ct_provider_status = (result.get("ct_provider_status") or "").lower()
        if "cooldown" in _ct_provider_status:
            _ct_stage = "provider_cooldown"
        elif "unavailable" in _ct_provider_status:
            _ct_stage = "provider_unavailable"

    if (_ct_attempted or _ct_stage) and not _family_exists(sfo_list, "ct"):
        _err = None
        if _ct_stage == "request_timeout":
            _err = "timeout"
        elif _ct_stage == "attempted_error":
            _err = "attempted_error"
        elif _ct_stage == "provider_cooldown":
            _err = "cooldown_active"
        elif _ct_stage == "provider_unavailable":
            _err = "provider_unavailable"
        sfo_list = _add_outcome_if_missing(
            sfo_list,
            family="ct",
            attempted=_ct_attempted,
            raw_count=_ct_raw,
            accepted_count=1 if _ct_accepted else 0,
            terminal_state=_ct_stage,
            timeout=_ct_stage == "request_timeout",
            error=_err,
        )

    result["source_family_outcomes"] = sfo_list
    return result


# ── Sprint F250A: Nonfeed Prelude → Source Family Outcomes ─────────────────────

# Lane name (uppercase) → source family name (lowercase)
_PRELUDE_LANE_TO_FAMILY: dict[str, str] = {
    "CT": "ct",
    "DOH": "doh",
    "WAYBACK": "wayback",
    "PASSIVE_DNS": "passive_dns",
    "PIVOT_EXECUTOR": "pivot_executor",
}


def _prelude_to_sfo(
    sfo_list: list[dict],
    family: str,
    expected_lanes: list[str],
    attempted_lanes: list[str],
    terminal_lanes: list[str],
    error_by_lane: dict[str, str],
    accepted_by_lane: dict[str, int],
) -> list[dict]:
    """Derive an SFO entry for one family from nonfeed prelude sets.

    Rules:
      - If family already exists in sfo_list with a richer entry, do not overwrite.
      - attempted = lane in attempted_lanes or terminal_lanes or error_by_lane
      - accepted_count = accepted_by_lane.get(lane, 0)
      - raw_count/built_count = 0 when unknown
      - error = error_by_lane.get(lane)
      - terminal_state:
          accepted_count > 0         → ATTEMPTED_ACCEPTED
          lane in error_by_lane        → ATTEMPTED_ERROR
          lane in terminal_lanes       → ATTEMPTED_NO_RESULTS
          lane in expected but not in attempted → SKIPPED
      - skip_reason:
          lane in expected but not attempted → eligible_not_attempted
          (only when lane is in expected set)
    """
    # Normalize lane name for lookup
    lane_name = next(
        (ln for ln, fam in _PRELUDE_LANE_TO_FAMILY.items() if fam == family),
        None,
    )
    if lane_name is None:
        return sfo_list

    in_expected = lane_name in expected_lanes
    in_attempted = lane_name in attempted_lanes
    in_terminal = lane_name in terminal_lanes
    has_error = lane_name in error_by_lane
    accepted_count = accepted_by_lane.get(lane_name, 0)

    # Determine if we should add an entry
    if not (in_expected or in_attempted or in_terminal or has_error):
        return sfo_list

    attempted = in_attempted or in_terminal or has_error

    # Compute terminal state
    terminal_state = ""
    skip_reason = None
    error = error_by_lane.get(lane_name)
    timeout = False

    if accepted_count > 0:
        terminal_state = "ATTEMPTED_ACCEPTED"
    elif error is not None:
        terminal_state = "ATTEMPTED_ERROR"
    elif in_terminal:
        terminal_state = "ATTEMPTED_NO_RESULTS"
    elif in_expected and not attempted:
        terminal_state = "SKIPPED"
        skip_reason = "eligible_not_attempted"
    elif attempted:
        # Attempted but no accepted, no error, not in terminal_lanes
        terminal_state = "ATTEMPTED_NO_RESULTS"

    # Determine raw_count (0 when derived from prelude only)
    raw_count = 0
    built_count = 0

    return _add_outcome_if_missing(
        sfo_list,
        family=family,
        attempted=attempted,
        raw_count=raw_count,
        accepted_count=accepted_count,
        terminal_state=terminal_state,
        timeout=timeout,
        error=error,
        skip_reason=skip_reason,
    )


def complete_source_family_outcomes_from_prelude(report: dict) -> dict:
    """
    Sprint F250A: Complete source_family_outcomes from nonfeed prelude lane sets.

    Nonfeed prelude collects lane names in sets:
      - nonfeed_prelude_expected_lanes
      - nonfeed_prelude_attempted_lanes
      - nonfeed_prelude_terminal_lanes
      - nonfeed_prelude_error_by_lane (lane → error str)
      - nonfeed_prelude_accepted_by_lane (lane → accepted int count)

    These sets may contain WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH, CT
    that have no corresponding lane detail fields (wayback_terminal_state,
    passive_dns_terminal_state, etc.) yet still represent real acquisition work.

    This function maps each lane name in those sets to a source_family_outcomes
    entry, following the same rules as complete_source_family_outcomes_from_lane_details:
      - Preserve existing richer entries.
      - Terminal-only lanes (no accepted, no error) → ATTEMPTED_NO_RESULTS.
      - Zero accepted findings are valid terminal coverage.
      - Skipped lanes get eligible_not_attempted skip_reason.

    Apply after complete_source_family_outcomes_from_lane_details in the
    report pipeline so that explicit lane detail fields take precedence.
    """
    result = dict(report)

    sfo_list: list[dict] = result.get("source_family_outcomes") or []
    if not isinstance(sfo_list, list):
        sfo_list = []

    expected = list(result.get("nonfeed_prelude_expected_lanes") or [])
    attempted = list(result.get("nonfeed_prelude_attempted_lanes") or [])
    terminal = list(result.get("nonfeed_prelude_terminal_lanes") or [])
    errors = dict(result.get("nonfeed_prelude_error_by_lane") or {})
    accepted = dict(result.get("nonfeed_prelude_accepted_by_lane") or {})

    # Nothing to do if prelude fields are all empty
    if not (expected or attempted or terminal or errors or accepted):
        return result

    for _family in _PRELUDE_LANE_TO_FAMILY.values():
        sfo_list = _prelude_to_sfo(
            sfo_list,
            family=_family,
            expected_lanes=expected,
            attempted_lanes=attempted,
            terminal_lanes=terminal,
            error_by_lane=errors,
            accepted_by_lane=accepted,
        )

    result["source_family_outcomes"] = sfo_list
    return result


# ── Sprint F226G: original reconcile ────────────────────────────────────────────

def reconcile_lane_detail_fields(report: dict) -> dict:
    """
    Reconcile lane detail fields with source_family_outcomes.

    Mutates a shallow copy of the report and returns it.
    Does not overwrite non-default richer fields.
    """
    # Work on a shallow copy so we don't mutate the caller's dict
    result = dict(report)

    sfo_list: list[dict] | None = result.get("source_family_outcomes")
    if not sfo_list:
        # No outcomes — nothing to reconcile against
        return result

    # ── CT reconciliation ─────────────────────────────────────────────────────
    _ct_outcome = None
    for _sfo in sfo_list:
        _fam = (_sfo.get("family") or "").lower()
        if _fam == "ct":
            _ct_outcome = _sfo
            break

    if _ct_outcome is not None:
        _attempted = _ct_outcome.get("attempted", False)

        # Only fill in if currently missing/default
        if _attempted and not result.get("ct_request_attempted"):
            result["ct_request_attempted"] = True

        if not result.get("ct_terminal_stage"):
            # Derive terminal_stage from outcome error / timeout / skipped
            _err = _ct_outcome.get("error") or ""
            if _ct_outcome.get("timeout"):
                result["ct_terminal_stage"] = "request_timeout"
            elif _err:
                # [F250B] Normalize SFO error → canonical stage
                if _err == "cooldown_active":
                    result["ct_terminal_stage"] = "provider_cooldown"
                elif _err == "provider_unavailable":
                    result["ct_terminal_stage"] = "provider_unavailable"
                else:
                    result["ct_terminal_stage"] = _err
            elif _attempted:
                result["ct_terminal_stage"] = "attempted_error"

        # Reconcile ct_raw_count from outcome if zero
        if result.get("ct_raw_count", 0) == 0:
            _raw = _ct_outcome.get("raw_count")
            if _raw is not None and _raw > 0:
                result["ct_raw_count"] = _raw

        # Reconcile ct_storage_attempted from accepted_count
        if not result.get("ct_storage_attempted") and _ct_outcome.get("accepted_count", 0) > 0:
            result["ct_storage_attempted"] = True

    # ── DOH reconciliation ──────────────────────────────────────────────────
    _doh_outcome = None
    for _sfo in sfo_list:
        _fam = (_sfo.get("family") or "").lower()
        if _fam == "doh":
            _doh_outcome = _sfo
            break

    if _doh_outcome is not None:
        _attempted = _doh_outcome.get("attempted", False)

        if _attempted and not result.get("doh_request_attempted"):
            result["doh_request_attempted"] = True

        if not result.get("doh_terminal_stage"):
            _err = _doh_outcome.get("error") or ""
            if _doh_outcome.get("timeout"):
                result["doh_terminal_stage"] = "timeout"
            elif _err:
                result["doh_terminal_stage"] = _err
            elif _attempted:
                result["doh_terminal_stage"] = "attempted_error"

        # Reconcile doh_raw_count
        if result.get("doh_raw_count", 0) == 0:
            _raw = _doh_outcome.get("raw_count")
            if _raw is not None and _raw > 0:
                result["doh_raw_count"] = _raw

    # ── Wayback reconciliation ─────────────────────────────────────────────
    # If Wayback appears in source_family_outcomes but wayback_terminal_state is blank,
    # derive an explicit terminal state (skipped or no_terminal)
    _wayback_outcome = None
    for _sfo in sfo_list:
        _fam = (_sfo.get("family") or "").lower()
        if _fam == "wayback":
            _wayback_outcome = _sfo
            break

    if _wayback_outcome is not None:
        if not result.get("wayback_terminal_state"):
            _err = _wayback_outcome.get("error") or ""
            if _wayback_outcome.get("skipped"):
                result["wayback_terminal_state"] = "skipped"
            elif _err:
                result["wayback_terminal_state"] = _err
            elif _wayback_outcome.get("attempted"):
                result["wayback_terminal_state"] = "no_terminal"
            else:
                result["wayback_terminal_state"] = "skipped"

    # ── PassiveDNS reconciliation ───────────────────────────────────────────
    _pdns_outcome = None
    for _sfo in sfo_list:
        _fam = (_sfo.get("family") or "").lower()
        if _fam in ("passive_dns", "passivedns"):
            _pdns_outcome = _sfo
            break

    if _pdns_outcome is not None:
        if not result.get("passive_dns_terminal_state"):
            _err = _pdns_outcome.get("error") or ""
            if _pdns_outcome.get("skipped"):
                result["passive_dns_terminal_state"] = "skipped"
            elif _err:
                result["passive_dns_terminal_state"] = _err
            elif _pdns_outcome.get("attempted"):
                result["passive_dns_terminal_state"] = "no_terminal"
            else:
                result["passive_dns_terminal_state"] = "skipped"

    return result