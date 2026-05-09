"""
Sprint F228D — Live Measurement Terminality Predicates

Pure module: no live sprint, no scheduler import, no MLX, no network.

Canonical single-owner for benchmark-side terminality predicates.
Shared by live_measurement_parser and live_sprint_measurement.

PRODUCT GOAL:
  Create benchmarks/live_measurement_terminality.py as pure single-owner
  for benchmark-side terminality predicates.

FUNCTIONS:
  has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool
  has_scheduler_exit_path(scheduler_exit: dict | None) -> bool
  extract_terminality_summary(data: dict) -> dict
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Public predicate API — canonical names
# ---------------------------------------------------------------------------

def has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool:
    """
    Return True if acquisition_strategy has non-empty source_family_outcomes.

    source_family_outcomes is the canonical record of which lanes were
    dispatched and their terminal state. An empty/missing dict means the
    acquisition never reached the point of recording lane outcomes.
    """
    if not acquisition_strategy or not isinstance(acquisition_strategy, dict):
        return False
    sf_outcomes = acquisition_strategy.get("source_family_outcomes")
    if isinstance(sf_outcomes, dict):
        return bool(sf_outcomes)
    if isinstance(sf_outcomes, list):
        return bool(sf_outcomes)
    return False


def has_scheduler_exit_path(scheduler_exit: dict | None) -> bool:
    """
    Return True if scheduler_exit contains a non-empty path string.

    scheduler_exit_path is the canonical record of how the scheduler exited
    (which guard/condition triggered windup). An empty/missing path means
    the scheduler exit was never recorded.
    """
    if not scheduler_exit or not isinstance(scheduler_exit, dict):
        return False
    path = (
        scheduler_exit.get("path")
        or scheduler_exit.get("exit_path")  # canonical field in scheduler_exit dict
        or scheduler_exit.get("scheduler_exit_path")  # legacy live_kpi alias
        or ""
    )
    return bool(str(path).strip())


# ---------------------------------------------------------------------------
# Backward-compatibility aliases — tests expect underscore names
# ---------------------------------------------------------------------------

def _has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool:
    return has_terminal_source_outcomes(acquisition_strategy)


def _has_scheduler_exit_path(scheduler_exit: dict | None) -> bool:
    return has_scheduler_exit_path(scheduler_exit)


# ---------------------------------------------------------------------------
# Terminality summary extraction
# ---------------------------------------------------------------------------

def extract_terminality_summary(data: dict) -> dict:
    """
    Extract terminality summary from a sprint report dict.

    Canonical priority:
      1. acquisition_report.source_family_outcomes + scheduler_exit.path
         (acquisition_report.terminality is advisory — scheduler_exit is authoritative)
      2. Top-level acquisition_terminality_* fields (legacy live_kpi fallback)
      3. live_kpi.acquisition_terminality_* (deeper fallback)

    Returns a dict with keys:
      - has_source_outcomes: bool
      - has_scheduler_exit: bool
      - acquisition_terminality_checked: bool | None
      - acquisition_terminality_satisfied: bool | None
      - acquisition_terminality_missing_lanes: list | None
      - acquisition_terminality_report: dict | None
    """
    result: dict = {
        "has_source_outcomes": False,
        "has_scheduler_exit": False,
        "acquisition_terminality_checked": None,
        "acquisition_terminality_satisfied": None,
        "acquisition_terminality_missing_lanes": None,
        "acquisition_terminality_report": None,
    }

    # Priority 1: canonical acquisition_report
    acq_report = data.get("acquisition_report")
    if isinstance(acq_report, dict):
        acq_strategy = {
            "source_family_outcomes": acq_report.get("source_family_outcomes", []),
        }
        result["has_source_outcomes"] = has_terminal_source_outcomes(acq_strategy)

        se = acq_report.get("scheduler_exit")
        result["has_scheduler_exit"] = has_scheduler_exit_path(se) if isinstance(se, dict) else False

    # Priority 2: top-level acquisition_terminality_* (canonical report keys)
    if result["acquisition_terminality_checked"] is None:
        result["acquisition_terminality_checked"] = data.get("acquisition_terminality_checked")
    if result["acquisition_terminality_satisfied"] is None:
        result["acquisition_terminality_satisfied"] = data.get("acquisition_terminality_satisfied")
    if result["acquisition_terminality_missing_lanes"] is None:
        result["acquisition_terminality_missing_lanes"] = data.get("acquisition_terminality_missing_lanes")
    if result["acquisition_terminality_report"] is None:
        result["acquisition_terminality_report"] = data.get("acquisition_terminality_report")

    # Priority 3: live_kpi fallback
    live_kpi = data.get("live_kpi") or {}
    if isinstance(live_kpi, dict):
        if result["acquisition_terminality_checked"] is None:
            result["acquisition_terminality_checked"] = live_kpi.get("acquisition_terminality_checked")
        if result["acquisition_terminality_satisfied"] is None:
            result["acquisition_terminality_satisfied"] = live_kpi.get("acquisition_terminality_satisfied")
        if result["acquisition_terminality_missing_lanes"] is None:
            result["acquisition_terminality_missing_lanes"] = live_kpi.get("acquisition_terminality_missing_lanes")
        if result["acquisition_terminality_report"] is None:
            result["acquisition_terminality_report"] = live_kpi.get("acquisition_terminality_report")

    return result
