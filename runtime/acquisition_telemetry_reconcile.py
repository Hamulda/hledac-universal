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



import logging
from _core import aclose

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


# ── Sprint F231B: per-family completers ───────────────────────────────────────

def _complete_doh_outcome(sfo_list: list[dict], result: dict) -> list[dict]:
    """Complete DOH source_family_outcomes entry from lane detail fields."""
    _doh_attempted = result.get("doh_request_attempted", False)
    _doh_stage = result.get("doh_terminal_stage", "") or ""
    _doh_raw = result.get("doh_raw_count", 0)
    _doh_accepted = result.get("doh_accepted_findings", 0)
    _doh_errors = result.get("doh_provider_errors", ())
    _doh_planned = result.get("doh_planned", False)
    _doh_scheduled = result.get("doh_scheduled", False)

    # Case 1: attempted or staged
    if _doh_attempted or _doh_stage:
        if _family_exists(sfo_list, "doh"):
            return sfo_list
        _err = _doh_errors[0] if _doh_errors else None
        if not _err:
            if _doh_stage == "no_candidates":
                _err = "no_candidates"
            elif _doh_stage == "attempted_empty":
                _err = "attempted_empty"
        _skip = not _doh_attempted and not _doh_stage
        return _add_outcome_if_missing(
            sfo_list,
            family="doh",
            attempted=_doh_attempted,
            raw_count=_doh_raw,
            accepted_count=_doh_accepted,
            terminal_state=_doh_stage,
            timeout=_doh_stage == "timeout",
            error=_err,
            skip_reason="doh_not_attempted" if _skip else None,
        )

    # Case 2: planned/scheduled but never attempted
    if _doh_planned or _doh_scheduled:
        if _family_exists(sfo_list, "doh"):
            return sfo_list
        return _add_outcome_if_missing(
            sfo_list,
            family="doh",
            attempted=False,
            raw_count=0,
            accepted_count=0,
            terminal_state="",
            skip_reason="planned_not_attempted",
        )

    return sfo_list


def _complete_wayback_outcome(sfo_list: list[dict], result: dict) -> list[dict]:
    """Complete Wayback source_family_outcomes entry from lane detail fields."""
    _wb_stage = result.get("wayback_terminal_state", "") or ""
    _wb_raw = result.get("wayback_raw_count", 0)
    _wb_accepted = result.get("wayback_accepted_count", 0)
    _wb_planned = result.get("wayback_planned", False)
    _wb_scheduled = result.get("wayback_scheduled", False)

    if _wb_stage:
        if _family_exists(sfo_list, "wayback"):
            return sfo_list
        _err = None
        _attempted = True
        if _wb_stage in ("no_terminal", "terminal_no_results"):
            _err = "no_terminal"
        elif _wb_stage == "skipped":
            _attempted = False
            _err = "skipped"
        elif _wb_stage == "wayback_unchanged_rejected":
            _err = _wb_stage
        return _add_outcome_if_missing(
            sfo_list,
            family="wayback",
            attempted=_attempted,
            raw_count=_wb_raw,
            accepted_count=_wb_accepted,
            terminal_state=_wb_stage,
            error=_err,
        )

    if _wb_planned or _wb_scheduled:
        if _family_exists(sfo_list, "wayback"):
            return sfo_list
        return _add_outcome_if_missing(
            sfo_list,
            family="wayback",
            attempted=False,
            raw_count=0,
            accepted_count=0,
            terminal_state="",
            skip_reason="planned_not_attempted",
        )

    return sfo_list


def _complete_passive_dns_outcome(sfo_list: list[dict], result: dict) -> list[dict]:
    """Complete PassiveDNS source_family_outcomes entry from lane detail fields."""
    _pdns_stage = result.get("passive_dns_terminal_state", "") or ""
    _pdns_raw = result.get("passive_dns_raw_count", 0)
    _pdns_accepted = result.get("passive_dns_accepted_count", 0)
    _pdns_planned = result.get("passive_dns_planned", False)
    _pdns_scheduled = result.get("passive_dns_scheduled", False)

    if _pdns_stage:
        if _family_exists(sfo_list, "passive_dns"):
            return sfo_list
        _err = None
        _attempted = True
        if _pdns_stage in ("no_terminal", "terminal_no_results"):
            _err = "no_terminal"
        elif _pdns_stage == "skipped":
            _attempted = False
            _err = "skipped"
        return _add_outcome_if_missing(
            sfo_list,
            family="passive_dns",
            attempted=_attempted,
            raw_count=_pdns_raw,
            accepted_count=_pdns_accepted,
            terminal_state=_pdns_stage,
            error=_err,
        )

    if _pdns_planned or _pdns_scheduled:
        if _family_exists(sfo_list, "passive_dns"):
            return sfo_list
        return _add_outcome_if_missing(
            sfo_list,
            family="passive_dns",
            attempted=False,
            raw_count=0,
            accepted_count=0,
            terminal_state="",
            skip_reason="planned_not_attempted",
        )

    return sfo_list


def _complete_ct_outcome(sfo_list: list[dict], result: dict) -> list[dict]:
    """Complete CT source_family_outcomes entry from lane detail fields."""
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

    if not (_ct_attempted or _ct_stage):
        return sfo_list
    if _family_exists(sfo_list, "ct"):
        return sfo_list

    _err = None
    if _ct_stage == "request_timeout":
        _err = "timeout"
    elif _ct_stage == "attempted_error":
        _err = "attempted_error"
    elif _ct_stage == "provider_cooldown":
        _err = "cooldown_active"
    elif _ct_stage == "provider_unavailable":
        _err = "provider_unavailable"

    return _add_outcome_if_missing(
        sfo_list,
        family="ct",
        attempted=_ct_attempted,
        raw_count=_ct_raw,
        accepted_count=1 if _ct_accepted else 0,
        terminal_state=_ct_stage,
        timeout=_ct_stage == "request_timeout",
        error=_err,
    )


def _reconcile_public_provider_selection_debug(result: dict, sfo_list: list[dict]) -> None:
    """P2-C: Deduplicate public_provider_selection_debug -- keep richer version."""
    if "public_provider_selection_debug" not in result:
        return
    _psd = result["public_provider_selection_debug"]
    if not isinstance(_psd, dict):
        return
    if _psd.get("candidate_providers") or _psd.get("selected_provider"):
        return  # already rich, keep as-is

    for _sfo in sfo_list or []:
        if _sfo.get("family", "").lower() == "public":
            _pub_psd = _sfo.get("provider_selection_debug")
            if isinstance(_pub_psd, dict) and (
                _pub_psd.get("candidate_providers") or _pub_psd.get("selected_provider")
            ):
                result["public_provider_selection_debug"] = _pub_psd
            break


def complete_source_family_outcomes_from_lane_details(report: dict) -> dict:
    """
    Sprint F231B: Complete source_family_outcomes from lane detail fields.

    The reverse of reconcile_lane_detail_fields: lane detail telemetry exists
    (doh_request_attempted, wayback_terminal_state, etc.) but source_family_outcomes
    may be missing the corresponding family entry.

    RULES:
      - Preserve existing source_family_outcomes entries.
      - Normalize family names to lowercase.
      - If a family already exists, do not duplicate -- merge only missing fields.
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

    sfo_list: list[dict] = result.get("source_family_outcomes") or []
    if not isinstance(sfo_list, list):
        sfo_list = []

    sfo_list = _complete_doh_outcome(sfo_list, result)
    sfo_list = _complete_wayback_outcome(sfo_list, result)
    sfo_list = _complete_passive_dns_outcome(sfo_list, result)
    sfo_list = _complete_ct_outcome(sfo_list, result)

    result["source_family_outcomes"] = sfo_list
    _reconcile_public_provider_selection_debug(result, sfo_list)

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

# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_sfo_by_family(
    sfo_list: list[dict], family: str, aliases: tuple[str, ...] | None = None
) -> dict | None:
    """Find source family outcome by family name (case-insensitive)."""
    variants = [family.lower()]
    if aliases:
        variants.extend(a.lower() for a in aliases)
    for sfo in sfo_list:
        if (sfo.get("family") or "").lower() in variants:
            return sfo
    return None


def _reconcile_attempted_request(result: dict, outcome: dict, prefix: str) -> None:
    """Set {prefix}_request_attempted = True if outcome was attempted."""
    field = f"{prefix}_request_attempted"
    if outcome.get("attempted", False) and not result.get(field):
        result[field] = True


def _reconcile_raw_count(result: dict, outcome: dict, prefix: str) -> None:
    """Reconcile {prefix}_raw_count from outcome if currently zero."""
    field = f"{prefix}_raw_count"
    if result.get(field, 0) == 0:
        raw = outcome.get("raw_count")
        if raw is not None and raw > 0:
            result[field] = raw


def _reconcile_terminal_stage(
    result: dict,
    outcome: dict,
    terminal_field: str,
    timeout_value: str,
    error_normalizer: dict | None = None,
    attempted_fallback: str = "attempted_error",
) -> None:
    """
    Derive and set terminal stage/state from outcome.

    Priority: timeout → error (normalized) → attempted → skipped
    Does NOT overwrite a non-empty terminal field.
    """
    if result.get(terminal_field):
        return

    err = outcome.get("error") or ""
    if outcome.get("timeout"):
        result[terminal_field] = timeout_value
    elif err:
        if error_normalizer and err in error_normalizer:
            result[terminal_field] = error_normalizer[err]
        else:
            result[terminal_field] = err
    elif outcome.get("attempted"):
        result[terminal_field] = attempted_fallback
    else:
        result[terminal_field] = "skipped"


# ── Per-family reconcilers ─────────────────────────────────────────────────────

def _reconcile_ct_outcome(result: dict, outcome: dict) -> None:
    """CT-specific reconciliation: attempted + terminal_stage + raw_count + candidates_built + storage."""
    _reconcile_attempted_request(result, outcome, "ct")
    _reconcile_terminal_stage(
        result,
        outcome,
        terminal_field="ct_terminal_stage",
        timeout_value="request_timeout",
        error_normalizer={
            "cooldown_active": "provider_cooldown",
            "provider_unavailable": "provider_unavailable",
        },
    )
    _reconcile_raw_count(result, outcome, "ct")

    # F266-U5: CT candidates sink
    if result.get("ct_candidates_built", 0) == 0:
        built = outcome.get("built_count")
        if built is not None and built > 0:
            result["ct_candidates_built"] = built

    # Reconcile ct_storage_attempted from accepted_count
    if not result.get("ct_storage_attempted") and outcome.get("accepted_count", 0) > 0:
        result["ct_storage_attempted"] = True


def _reconcile_doh_outcome(result: dict, outcome: dict) -> None:
    """DOH-specific reconciliation: attempted + terminal_stage + raw_count."""
    _reconcile_attempted_request(result, outcome, "doh")
    _reconcile_terminal_stage(
        result,
        outcome,
        terminal_field="doh_terminal_stage",
        timeout_value="timeout",
    )
    _reconcile_raw_count(result, outcome, "doh")


def _reconcile_wayback_outcome(result: dict, outcome: dict) -> None:
    """Wayback reconciliation: terminal_state only."""
    _reconcile_terminal_stage(
        result,
        outcome,
        terminal_field="wayback_terminal_state",
        timeout_value="skipped",
        attempted_fallback="no_terminal",
    )


def _reconcile_passive_dns_outcome(result: dict, outcome: dict) -> None:
    """PassiveDNS reconciliation: terminal_state only."""
    _reconcile_terminal_stage(
        result,
        outcome,
        terminal_field="passive_dns_terminal_state",
        timeout_value="skipped",
        attempted_fallback="no_terminal",
    )


# ── Main reconciler ────────────────────────────────────────────────────────────

def reconcile_lane_detail_fields(report: dict) -> dict:
    """
    Reconcile lane detail fields with source_family_outcomes.

    Mutates a shallow copy of the report and returns it.
    Does not overwrite non-default richer fields.
    """
    result = dict(report)

    sfo_list: list[dict] | None = result.get("source_family_outcomes")
    if not sfo_list:
        return result

    # CT
    ct_outcome = _find_sfo_by_family(sfo_list, "ct")
    if ct_outcome is not None:
        _reconcile_ct_outcome(result, ct_outcome)

    # DOH
    doh_outcome = _find_sfo_by_family(sfo_list, "doh")
    if doh_outcome is not None:
        _reconcile_doh_outcome(result, doh_outcome)

    # Wayback
    wayback_outcome = _find_sfo_by_family(sfo_list, "wayback")
    if wayback_outcome is not None:
        _reconcile_wayback_outcome(result, wayback_outcome)

    # PassiveDNS
    pdns_outcome = _find_sfo_by_family(sfo_list, "passive_dns", aliases=("passivedns",))
    if pdns_outcome is not None:
        _reconcile_passive_dns_outcome(result, pdns_outcome)

    return result
