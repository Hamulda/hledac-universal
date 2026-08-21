"""
runtime/sprint/truth_logger.py — Runtime truth, timing truth, and observed run tuple

F350M-R: Computes canonical truth records from sprint results.

Exports:
- _runtime_truth(): Build canonical runtime-truth record
- timing_truth construction: Done in windup phase
- observed_run_tuple: Tuple of (query, duration, cycles, source_mix, truth_level)

Usage:
    runtime_truth = _runtime_truth(inp=RuntimeTruthInput(...))
    timing_truth = {...}  # Built in windup phase
    observed_run_tuple = (query[:40], round(actual_duration, 1), cycles_completed, src_mix_str, runtime_truth_level)
"""

from __future__ import annotations

import logging
from typing import Any

from .types import RuntimeTruthInput

logger = logging.getLogger(__name__)


def _runtime_truth(inp: RuntimeTruthInput) -> dict:
    """
    Build canonical runtime-truth record from scheduler result data.

    Computes:
    - is_meaningful: Boolean indicating if run had meaningful evidence
    - evidence_note: Human-readable description
    - command_params: Requested duration and query
    - actual_duration_s: Actual wall time
    - cycles_completed / cycles_started: Cycle statistics
    - branch_mix: Breakdown of finding sources
    - primary_signal_source: Dominant source type
    - total_pattern_hits, accepted_findings: Signal statistics
    - pre_sprint_swap_detected / pre_sprint_uma_state: Hardware context
    - branch_timeout_count: Timeout statistics

    Args:
        inp: RuntimeTruthInput bundle with all required fields

    Returns:
        dict with runtime truth record
    """
    from .types import _is_meaningful_run

    is_meaningful, evidence_note = _is_meaningful_run(
        inp.actual_duration_s,
        inp.cycles_completed,
        inp.cycles_started,
        inp.accepted_findings,
        inp.total_pattern_hits,
        swap_detected=inp.swap_detected,
        uma_state=inp.uma_state,
    )

    branch_mix = {
        "feed_findings": inp.feed_findings,
        "public_findings": inp.public_accepted_findings,
        "ct_findings": inp.ct_findings,
    }

    # Compute primary signal source
    if inp.ct_findings > 0 and inp.feed_findings == 0 and inp.public_accepted_findings == 0:
        primary = "ct"
    elif inp.feed_findings > 0 and inp.public_accepted_findings == 0 and inp.ct_findings == 0:
        primary = "feed"
    elif inp.public_accepted_findings > 0 and inp.feed_findings == 0 and inp.ct_findings == 0:
        primary = "public"
    elif inp.feed_findings > 0 and inp.public_accepted_findings > 0 and inp.ct_findings == 0:
        total_nonfeed = inp.public_accepted_findings
        feed_dominance_ratio = (
            inp.feed_findings / (inp.feed_findings + total_nonfeed) if inp.feed_findings + total_nonfeed > 0 else 1.0
        )
        primary = "feed" if feed_dominance_ratio > 0.95 else "mixed"
    elif inp.ct_findings > 0 and (inp.feed_findings > 0 or inp.public_accepted_findings > 0):
        primary = "mixed_ct"
    else:
        primary = "none"

    return {
        "is_meaningful": is_meaningful,
        "evidence_note": evidence_note,
        "command_params": {
            "query": inp.query,
            "requested_duration_s": inp.duration_s,
        },
        "actual_duration_s": round(inp.actual_duration_s, 2),
        "cycles_completed": inp.cycles_completed,
        "cycles_started": inp.cycles_started,
        "branch_mix": branch_mix,
        "primary_signal_source": primary,
        "total_pattern_hits": inp.total_pattern_hits,
        "accepted_findings": inp.accepted_findings,
        "pre_sprint_swap_detected": inp.swap_detected,
        "pre_sprint_uma_state": inp.uma_state,
        "branch_timeout_count": inp.branch_timeout_count,
        "public_branch_timed_out": inp.public_branch_timed_out,
        "ct_branch_timed_out": inp.ct_branch_timed_out,
    }


def compute_timing_truth(
    duration_s: float,
    windup_lead_s: float,
    time_to_windup_s: float,
    time_to_teardown_s: float,
    active_window_budget_s: float,
    windup_lead_observed_s: float,
    pre_scheduler_boot_s: float,
    scheduler_wall_s: float,
    entered_active_at_monotonic: float | None,
    first_cycle_started_at_monotonic: float | None,
    pre_active_starved: bool,
    pre_active_blocker: str | None,
) -> dict[str, Any]:
    """
    Build timing truth record from phase timings.

    Args:
        duration_s: Requested sprint duration
        windup_lead_s: Configured windup lead time
        time_to_windup_s: Actual time until windup
        time_to_teardown_s: Actual time until teardown
        active_window_budget_s: Available time for active acquisition
        windup_lead_observed_s: Observed windup duration
        pre_scheduler_boot_s: Pre-scheduler boot time
        scheduler_wall_s: Scheduler wall time
        entered_active_at_monotonic: Monotonic time when active started
        first_cycle_started_at_monotonic: Monotonic time of first cycle
        pre_active_starved: Whether pre-active starvation occurred
        pre_active_blocker: Blocker reason if any

    Returns:
        dict with timing truth record
    """
    return {
        "requested_duration_s": duration_s,
        "windup_lead_s": windup_lead_s,
        "time_to_windup_s": round(time_to_windup_s, 2),
        "time_to_teardown_s": round(time_to_teardown_s, 2),
        "active_window_budget_s": round(active_window_budget_s, 2),
        "windup_lead_observed_s": round(windup_lead_observed_s, 2),
        "pre_scheduler_boot_s": round(pre_scheduler_boot_s, 2),
        "scheduler_wall_s": round(scheduler_wall_s, 2),
        "scheduler_returned_phase": "ACTIVE" if entered_active_at_monotonic else "entry_only",
        "entered_active_truth": entered_active_at_monotonic is not None,
        "first_cycle_truth": first_cycle_started_at_monotonic is not None,
        "pre_active_starvation": pre_active_starved,
        "pre_active_blocker": pre_active_blocker,
        "active_runtime_occurred": False,  # Set by caller after is_meaningful check
    }


def build_observed_run_tuple(
    query: str,
    actual_duration: float,
    cycles_completed: int,
    src_mix_str: str,
    runtime_truth_level: str,
) -> tuple[str, float, int, str, str]:
    """
    Build observed run tuple for telemetry.

    Args:
        query: Sprint query (truncated to 40 chars)
        actual_duration: Actual sprint duration
        cycles_completed: Number of completed cycles
        src_mix_str: Source mix string
        runtime_truth_level: Runtime truth level string

    Returns:
        tuple of (query, duration, cycles, source_mix, truth_level)
    """
    return (
        query[:40] if len(query) > 40 else query,
        round(actual_duration, 1),
        cycles_completed,
        src_mix_str,
        runtime_truth_level,
    )
