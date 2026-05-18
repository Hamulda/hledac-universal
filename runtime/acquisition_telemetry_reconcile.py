"""
Sprint F226G: Acquisition Telemetry SSOT Helper.

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

Apply reconcile_lane_detail_fields(report) before final report is
returned/written.
"""

from __future__ import annotations

import logging

__all__ = ["reconcile_lane_detail_fields"]

logger = logging.getLogger(__name__)


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