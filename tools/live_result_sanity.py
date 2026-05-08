"""F211C — Strict Live Result Bundle Sanity Checker.

Meta-checker that compares benchmark JSON + validation JSON + trace JSON
and reports disagreements between the three surfaces.

Strict mode: stale trace verdicts and wallclock budget overruns are always reported.

ABSOLUTE REPO ROOT: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
Work only inside repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SanityVerdict(Enum):
    SANITY_PASS = "SANITY_PASS"
    SANITY_FAIL_SURFACE_DISAGREEMENT = "SANITY_FAIL_SURFACE_DISAGREEMENT"
    SANITY_FAIL_STALE_TERMINALITY = "SANITY_FAIL_STALE_TERMINALITY"
    SANITY_FAIL_WALLCLOCK_BUDGET = "SANITY_FAIL_WALLCLOCK_BUDGET"
    SANITY_FAIL_BENCHMARK_SHAPE_GAP = "SANITY_FAIL_BENCHMARK_SHAPE_GAP"
    SANITY_FAIL_RESEARCH_QUALITY = "SANITY_FAIL_RESEARCH_QUALITY"


@dataclass
class BenchmarkSurface:
    """Parsed benchmark surface."""

    run_quality_verdict: str | None = None
    live_kpi: dict[str, Any] | None = None
    acquisition_report: dict[str, Any] | None = None
    runtime_truth: dict[str, Any] | None = None
    branch_mix: dict[str, int] | None = None
    actual_duration_s: float | None = None
    planned_duration_s: float | None = None
    public_terminal_state: str | None = None
    ct_terminal_state: str | None = None
    # F215D: swap/comparable from benchmark top-level
    comparable_result: bool | None = None
    swap_gate_triggered: bool | None = None
    # F215A: embedded research_quality from live_kpi (used when no separate quality_json)
    research_quality: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidatorSurface:
    """Parsed validator surface."""

    live_kpi: dict[str, Any] | None = None
    acquisition_report: dict[str, Any] | None = None
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    source_family_outcomes: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceSurface:
    """Parsed trace surface."""

    verdict: str | None = None
    stage: str | None = None
    detail: str | None = None
    extended: dict[str, Any] = field(default_factory=dict)
    terminality_satisfied: bool | None = None
    raw_benchmark: dict[str, Any] | None = None
    raw_internal: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualitySurface:
    """Parsed research quality surface."""

    quality_gate: str | None = None
    grade: str | None = None
    total_quality_score: float | None = None
    research_quality_comparable: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SanityResult:
    verdict: SanityVerdict = SanityVerdict.SANITY_PASS
    checks: dict[str, bool] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    benchmark: BenchmarkSurface = field(default_factory=BenchmarkSurface)
    validator: ValidatorSurface = field(default_factory=ValidatorSurface)
    trace: TraceSurface = field(default_factory=TraceSurface)
    quality_surface: QualitySurface = field(default_factory=QualitySurface)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "checks": self.checks,
            "disagreements": self.disagreements,
            "benchmark": {
                "run_quality_verdict": self.benchmark.run_quality_verdict,
                "branch_mix": self.benchmark.branch_mix,
                "actual_duration_s": self.benchmark.actual_duration_s,
                "planned_duration_s": self.benchmark.planned_duration_s,
                "public_terminal_state": self.benchmark.public_terminal_state,
                "ct_terminal_state": self.benchmark.ct_terminal_state,
            },
            "validator": {
                "acquisition_terminality_checked": self.validator.acquisition_terminality_checked,
                "acquisition_terminality_satisfied": self.validator.acquisition_terminality_satisfied,
                "source_family_outcomes": self.validator.source_family_outcomes,
            },
            "trace": {
                "verdict": self.trace.verdict,
                "stage": self.trace.stage,
                "detail": self.trace.detail,
                "terminality_satisfied": self.trace.terminality_satisfied,
            },
            "quality": {
                "quality_gate": self.quality_surface.quality_gate,
                "grade": self.quality_surface.grade,
                "total_quality_score": self.quality_surface.total_quality_score,
                "research_quality_comparable": self.quality_surface.research_quality_comparable,
            },
        }

    def to_md(self) -> str:
        lines = [
            "# Live Result Bundle Sanity Report",
            "",
            f"**Verdict**: `{self.verdict.value}`",
            "",
            "## Checks",
            "",
        ]
        for name, passed in self.checks.items():
            icon = "PASS" if passed else "FAIL"
            lines.append(f"- [{icon}] `{name}`")
        if self.disagreements:
            lines += ["", "## Disagreements", ""]
            for d in self.disagreements:
                lines.append(f"- {d}")
        lines += ["", "## Benchmark Surface", "", f"- verdict: `{self.benchmark.run_quality_verdict}`"]
        if self.benchmark.branch_mix:
            lines.append(f"- branch_mix: {self.benchmark.branch_mix}")
        lines += ["", "## Validator Surface", "", f"- terminality checked: `{self.validator.acquisition_terminality_checked}`"]
        if self.validator.acquisition_terminality_satisfied is not None:
            lines.append(f"- terminality satisfied: `{self.validator.acquisition_terminality_satisfied}`")
        if self.validator.source_family_outcomes:
            lines.append(f"- source_family_outcomes: {self.validator.source_family_outcomes}")
        lines += ["", "## Trace Surface", "", f"- verdict: `{self.trace.verdict}`"]
        if self.trace.detail:
            lines.append(f"- detail: {self.trace.detail}")
        if self.trace.terminality_satisfied is not None:
            lines.append(f"- terminality_satisfied: `{self.trace.terminality_satisfied}`")
        if self.quality_surface.quality_gate is not None:
            lines += ["", "## Research Quality", "", f"- quality_gate: `{self.quality_surface.quality_gate}`"]
            if self.quality_surface.grade:
                lines.append(f"- grade: `{self.quality_surface.grade}`")
            if self.quality_surface.total_quality_score is not None:
                lines.append(f"- score: {self.quality_surface.total_quality_score:.1f}")
            if self.quality_surface.research_quality_comparable is not None:
                lines.append(f"- comparable: {self.quality_surface.research_quality_comparable}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_TERMINALITY_UNSATISFIED_VERDICTS = frozenset({
    "FAIL_TERMINALITY_UNSATISFIED",
    "FAIL_TERMINALITY_NOT_CHECKED",
    "FAIL_MISSING_SOURCE_OUTCOMES",
    "FAIL_SCHEDULER_EXIT_MISSING",
})

# F224C: NONFEED_EVIDENCE_MISSING is NOT a terminality failure — it's a research quality failure
# Terminality was satisfied but nonfeed evidence was insufficient
_NONFEED_EVIDENCE_MISSING_VERDICTS = frozenset({
    "FAIL_NONFEED_EVIDENCE_MISSING",
})

_TRACE_STALE_VERDICTS = frozenset({
    "TRACE_TERMINALITY_STALE_BEFORE_NONFEED",
    "TRACE_TERMINALITY_STALE_SNAPSHOT",
    "TRACE_DIRECT_PRE_RETURN_BARRIER_MISSING",
    "TRACE_TERMINALITY_UNSATISFIED",
    "TRACE_DROP_BEFORE_EXPORT",
    "TRACE_DROP_AT_BENCHMARK_PARSE",
    "TRACE_DROP_AT_EXPORT",
    "TRACE_VALIDATOR_ALIAS_ONLY",
})


def parse_benchmark(raw: dict[str, Any]) -> BenchmarkSurface:
    surf = BenchmarkSurface(raw=raw)

    # run_quality_verdict
    surf.run_quality_verdict = raw.get("run_quality_verdict") or raw.get("live_run_status")

    # live_kpi
    surf.live_kpi = raw.get("live_kpi") or raw.get("live_kpi_snapshot")

    # acquisition_report (top-level or inside live_kpi)
    surf.acquisition_report = raw.get("acquisition_report")
    if not surf.acquisition_report and surf.live_kpi:
        surf.acquisition_report = surf.live_kpi.get("acquisition_report")

    # runtime_truth / branch_mix
    surf.runtime_truth = raw.get("runtime_truth")
    surf.branch_mix = raw.get("branch_mix") or (surf.runtime_truth or {}).get("branch_mix")

    # timing
    surf.actual_duration_s = raw.get("actual_duration_s")
    surf.planned_duration_s = raw.get("planned_duration_s")

    # terminal states
    surf.public_terminal_state = raw.get("public_terminal_state")
    surf.ct_terminal_state = raw.get("ct_terminal_state")

    # F215D: swap gate and comparable_result from benchmark top-level
    surf.comparable_result = raw.get("comparable_result")
    surf.swap_gate_triggered = raw.get("swap_gate_triggered")

    # F215A: embedded research_quality from live_kpi (used when no separate quality_json)
    _lk = surf.live_kpi
    if isinstance(_lk, dict) and isinstance(_lk.get("research_quality"), dict):
        surf.research_quality = _lk["research_quality"]

    return surf


def parse_validator(raw: dict[str, Any]) -> ValidatorSurface:
    surf = ValidatorSurface(raw=raw)

    surf.live_kpi = raw.get("live_kpi") or raw.get("live_kpi_snapshot")

    # acquisition_report
    surf.acquisition_report = raw.get("acquisition_report")
    if not surf.acquisition_report and surf.live_kpi:
        surf.acquisition_report = surf.live_kpi.get("acquisition_report")

    # terminality
    if surf.live_kpi:
        surf.acquisition_terminality_checked = surf.live_kpi.get("acquisition_terminality_checked")
        surf.acquisition_terminality_satisfied = surf.live_kpi.get("acquisition_terminality_satisfied")
        surf.source_family_outcomes = surf.live_kpi.get("source_family_outcomes")

    return surf


def parse_trace(raw: dict[str, Any]) -> TraceSurface:
    surf = TraceSurface(raw=raw)

    surf.verdict = raw.get("verdict")
    surf.stage = raw.get("stage")
    surf.detail = raw.get("detail")

    extended = raw.get("extended") or {}
    surf.extended = extended
    surf.terminality_satisfied = extended.get("terminality_satisfied")

    surf.raw_benchmark = raw.get("raw_benchmark")
    surf.raw_internal = raw.get("raw_internal")

    return surf


def parse_quality(raw: dict[str, Any]) -> QualitySurface:
    surf = QualitySurface(raw=raw)
    surf.quality_gate = raw.get("quality_gate")
    surf.grade = raw.get("grade")
    surf.total_quality_score = raw.get("total_quality_score")
    surf.research_quality_comparable = raw.get("research_quality_comparable")
    return surf


def parse_quality_with_fallback(raw: dict[str, Any], fallback: dict[str, Any]) -> QualitySurface:
    """
    F215A: Parse quality surface, falling back to embedded research_quality from
    benchmark live_kpi when no explicit quality_json is provided.

    Priority:
    1. raw has quality_gate → use it
    2. raw is empty + fallback has quality_gate → use fallback (embedded in benchmark)
    3. raw is empty + fallback empty → N/A (quality_gate=None, no gate applied)
    4. raw non-empty but quality_gate missing → malformed (quality_gate=None → fail)
    """
    # 1. raw has quality_gate → use it directly
    if raw.get("quality_gate") is not None:
        return parse_quality(raw)

    # 2. raw is empty — try fallback (embedded research_quality from live_kpi)
    if not raw:
        if isinstance(fallback, dict) and fallback.get("quality_gate") is not None:
            return parse_quality(fallback)
        # 3. Nothing available → N/A
        return QualitySurface(raw=raw)

    # 4. Raw non-empty but quality_gate missing → malformed
    return parse_quality(raw)


def _get_sfo_canonical(
    b: BenchmarkSurface, v: ValidatorSurface
) -> list[dict[str, Any]]:
    """
    F221E: Return canonical source_family_outcomes list.

    Priority:
    1. acquisition_report.source_family_outcomes (canonical)
    2. acquisition_report.live_kpi.source_family_outcomes (legacy wrap)
    3. live_kpi.source_family_outcomes (live_kpi direct)

    Returns [] if none available.
    """
    # Benchmark acquisition_report first (canonical)
    ar = b.acquisition_report
    if ar:
        sfo = ar.get("source_family_outcomes")
        if isinstance(sfo, list):
            return sfo
        # acquisition_report may wrap live_kpi
        lk = ar.get("live_kpi")
        if isinstance(lk, dict) and isinstance(lk.get("source_family_outcomes"), list):
            return lk["source_family_outcomes"]

    # Validator acquisition_report
    ar_v = v.acquisition_report
    if ar_v:
        sfo = ar_v.get("source_family_outcomes")
        if isinstance(sfo, list):
            return sfo

    # Fallback: live_kpi source_family_outcomes
    lk = (b.live_kpi or {}) or (v.live_kpi or {})
    if isinstance(lk, dict) and isinstance(lk.get("source_family_outcomes"), list):
        return lk["source_family_outcomes"]

    return []


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def _check_benchmark_fail_validator_pass(b: BenchmarkSurface, v: ValidatorSurface) -> tuple[bool, str | None]:
    bench_fail = b.run_quality_verdict in _TERMINALITY_UNSATISFIED_VERDICTS
    val_pass = (
        v.acquisition_terminality_satisfied is True
        or v.acquisition_terminality_checked is False
    )
    if bench_fail and val_pass:
        return False, (
            f"Benchmark verdict '{b.run_quality_verdict}' but validator terminality_satisfied={v.acquisition_terminality_satisfied}"
        )
    return True, None


def _check_benchmark_missing_source_family_outcomes(
    b: BenchmarkSurface, t: TraceSurface
) -> tuple[bool, str | None]:
    # F221E: acquisition_report.source_family_outcomes is canonical — check it first
    ar = b.acquisition_report
    ar_has = ar is not None and isinstance(ar.get("source_family_outcomes"), list)
    live_kpi = b.live_kpi or {}
    lk_has = isinstance(live_kpi.get("source_family_outcomes"), list)
    bench_missing = not ar_has and not lk_has
    trace_has = t.raw_internal is not None and (
        (t.raw_internal.get("live_kpi") or {}).get("source_family_outcomes")
        or (t.raw_internal.get("live_kpi_snapshot") or {}).get("source_family_outcomes")
        or (t.raw_internal.get("acquisition_report") or {}).get("source_family_outcomes")
    )
    if bench_missing and trace_has:
        return False, "Benchmark missing source_family_outcomes but internal trace has them"
    return True, None


def _check_stale_terminality(
    t: TraceSurface, allow_stale_trace: bool
) -> tuple[bool, str | None]:
    if allow_stale_trace:
        return True, None
    if t.verdict in _TRACE_STALE_VERDICTS:
        return False, f"Stale trace verdict '{t.verdict}' present without --allow-stale-trace"
    return True, None


def _check_wallclock_budget(b: BenchmarkSurface) -> tuple[bool, str | None]:
    if b.actual_duration_s and b.planned_duration_s:
        allowed = max(b.planned_duration_s * 1.10, b.planned_duration_s + 30)
        if b.actual_duration_s > allowed:
            return False, (
                f"Wallclock budget exceeded: actual={b.actual_duration_s:.1f}s vs allowed={allowed:.1f}s (planned={b.planned_duration_s:.1f}s)"
            )
    return True, None


def _check_feed_only_accepted_nonfeed_attempted(
    b: BenchmarkSurface, v: ValidatorSurface
) -> tuple[bool, str | None]:
    """
    F221E: Uses acquisition_report.source_family_outcomes as canonical.
    Falls back to live_kpi.source_family_outcomes.

    CT attempted=True when source_family_outcomes says so OR when ct_provider_status
    or ct_terminal_state indicates a terminal outcome (provider_failure/cooldown/timeout).
    PUBLIC attempted=True when source_family_outcomes says so OR when public_terminal_stage
    is set and not NOT_SCHEDULED.
    """
    outcomes = _get_sfo_canonical(b, v)

    # Check canonical SFO for attempted signals
    ct_attempted = any(
        o.get("family", "").lower() == "ct" and o.get("attempted")
        for o in outcomes
    )
    public_attempted = any(
        o.get("family", "").lower() == "public" and o.get("attempted")
        for o in outcomes
    )

    # F221E: Terminal signals can override an explicit attempted=False in SFO
    # CT terminal signals
    if not ct_attempted:
        ar = b.acquisition_report
        ct_provider_status = ar.get("ct_provider_status") if ar else None
        ct_terminal_state = ar.get("ct_terminal_state") if ar else None
        if ct_provider_status in ("provider_failure", "cooldown", "timeout") or ct_terminal_state in ("provider_failure", "cooldown", "timeout"):
            ct_attempted = True

    # PUBLIC terminal signals
    if not public_attempted:
        ar = b.acquisition_report
        public_terminal_stage = ar.get("public_terminal_stage") if ar else None
        if public_terminal_stage and public_terminal_stage != "NOT_SCHEDULED":
            public_attempted = True

    branch_mix = b.branch_mix or {}
    feed_only = (
        branch_mix.get("feed", 0) > 0
        and branch_mix.get("ct_findings", 0) == 0
        and branch_mix.get("public_findings", 0) == 0
    )
    if feed_only and (ct_attempted or public_attempted):
        reasons = []
        if ct_attempted:
            reasons.append("CT")
        if public_attempted:
            reasons.append("PUBLIC")
        return False, (
            f"Feed-only accepted branch but nonfeed source outcomes were attempted: {', '.join(reasons)}"
        )
    return True, None


# F224C: Check for FAIL_NONFEED_EVIDENCE_MISSING verdict
# This is a research quality failure (insufficient nonfeed evidence), NOT a terminality failure.
# Sanity must fail it with the correct reason — nonfeed evidence missing.
def _check_nonfeed_evidence_missing(
    b: BenchmarkSurface,
) -> tuple[bool, str | None]:
    """
    F224C: FAIL_NONFEED_EVIDENCE_MISSING means terminality was satisfied but nonfeed
    evidence was insufficient. This is a research quality failure, not terminality.

    Fails sanity if:
    - run_quality_verdict is FAIL_NONFEED_EVIDENCE_MISSING
    """
    if b.run_quality_verdict in _NONFEED_EVIDENCE_MISSING_VERDICTS:
        return False, (
            f"Nonfeed evidence missing: run_quality_verdict={b.run_quality_verdict} — "
            "terminality satisfied but nonfeed evidence insufficient"
        )
    return True, None


def _check_ct_loss_stage_present(
    b: BenchmarkSurface,
) -> tuple[bool, str | None]:
    """
    F214R2: Check CT loss telemetry is present when CT lane has raw evidence but zero accepted.

    Fails if:
    - CT raw_count > 0 and accepted_count == 0 but ct_loss_stage is missing from live_kpi.
    """
    live_kpi = b.live_kpi or {}
    lane_execution = live_kpi.get("lane_execution_counts", {}) or live_kpi.get("source_family_outcomes", [])

    # Normalize: could be dict or list
    ct_data = None
    if isinstance(lane_execution, dict):
        ct_data = lane_execution.get("ct") or lane_execution.get("CT")
    elif isinstance(lane_execution, list):
        for entry in lane_execution:
            if isinstance(entry, dict) and entry.get("family", "").lower() == "ct":
                ct_data = entry
                break

    if ct_data is None:
        return True, None  # No CT lane, nothing to check

    ct_raw = ct_data.get("raw_count", 0) if isinstance(ct_data, dict) else 0
    ct_accepted = ct_data.get("accepted_count", 0) if isinstance(ct_data, dict) else 0

    # If CT has raw evidence but zero accepted, ct_loss_stage MUST be present
    if ct_raw > 0 and ct_accepted == 0:
        ct_loss_stage = live_kpi.get("ct_loss_stage")
        if ct_loss_stage is None:
            return False, (
                f"CT raw_count={ct_raw} accepted_count=0 but ct_loss_stage is missing from live_kpi"
            )

    return True, None


def _check_public_surface_present(
    b: BenchmarkSurface,
) -> tuple[bool, str | None]:
    """
    F221E: Check PUBLIC lane surface is present when public was attempted.

    F221E: Canonical surfaces — uses acquisition_report as authoritative:
    1. acquisition_report.public_terminal_state (canonical, set when PUBLIC was scheduled)
    2. acquisition_report.source_family_outcomes PUBLIC entry
    3. live_kpi.public_fetch_attempted / runtime_truth.public_branch_timed_out (legacy fallback)

    Fails if public was attempted (canonical signal) but PUBLIC is absent from
    source_family_outcomes.
    """
    live_kpi = b.live_kpi or {}
    runtime_truth = b.runtime_truth or {}

    # F221E: Primary — public_terminal_state from acquisition_report (canonical)
    ar = b.acquisition_report or {}
    public_terminal_stage = ar.get("public_terminal_stage")
    if not public_terminal_stage:
        public_terminal_stage = b.public_terminal_state or live_kpi.get("public_terminal_stage")

    # F221E: Canonical attempted — public_terminal_stage not NOT_SCHEDULED means attempted
    public_attempted_canonical = (
        public_terminal_stage is not None
        and public_terminal_stage != "NOT_SCHEDULED"
    )

    # Legacy fallback signals
    public_attempted_legacy = (
        live_kpi.get("public_fetch_attempted")
        or runtime_truth.get("public_branch_timed_out")
        or False
    )

    if not public_attempted_canonical and not public_attempted_legacy:
        return True, None

    # F221E: Check acquisition_report.source_family_outcomes (canonical) first
    ar_sfo = ar.get("source_family_outcomes") if ar else None
    if isinstance(ar_sfo, list):
        public_present = any(
            isinstance(o, dict) and o.get("family", "").lower() == "public"
            for o in ar_sfo
        )
        if public_present:
            return True, None
        # PUBLIC attempted (canonical stage) but no outcome entry — fail
        if public_attempted_canonical:
            return False, (
                "public_terminal_stage indicates PUBLIC was attempted but PUBLIC is absent "
                "from acquisition_report.source_family_outcomes"
            )
        # Legacy signal only — check live_kpi as fallback
        if public_attempted_legacy:
            lane_execution = live_kpi.get("lane_execution_counts", {}) or live_kpi.get("source_family_outcomes", [])
            public_present = False
            if isinstance(lane_execution, dict):
                public_present = "public" in lane_execution or "PUBLIC" in lane_execution
            elif isinstance(lane_execution, list):
                public_present = any(
                    isinstance(o, dict) and o.get("family", "").lower() == "public"
                    for o in lane_execution
                )
            if not public_present:
                return False, (
                    "public_fetch_attempted=True but PUBLIC absent from lane_execution_counts"
                )
        return True, None

    # No acquisition_report — use live_kpi as fallback
    lane_execution = live_kpi.get("lane_execution_counts", {}) or live_kpi.get("source_family_outcomes", [])
    public_present = False
    if isinstance(lane_execution, dict):
        public_present = "public" in lane_execution or "PUBLIC" in lane_execution
    elif isinstance(lane_execution, list):
        for entry in lane_execution:
            if isinstance(entry, dict) and entry.get("family", "").lower() == "public":
                public_present = True
                break

    if not public_present and (public_attempted_canonical or public_attempted_legacy):
        return False, (
            "PUBLIC lane was attempted but is absent from source_family_outcomes"
        )

    return True, None


def _check_research_quality(
    q: QualitySurface,
    min_grade: str | None,
    allow_feed_only: bool,
) -> tuple[bool, str | None]:
    """
    Check research quality gate.

    Fails if:
    - quality_gate is missing (None)
    - quality_gate is QUALITY_FAIL_FEED_ONLY and not allow_feed_only
    - quality_gate is any other QUALITY_FAIL_* (always fail)
    - grade is below min_grade threshold (even for warnings)

    Passes (with warning) for QUALITY_WARN_MULTISOURCE_SHALLOW only when above min_grade.
    """
    # F214R2: quality_gate missing is a hard failure
    if q.quality_gate is None:
        return False, "quality_gate is missing from research quality surface"

    # Check minimum grade threshold first — applies to all gates including warnings
    if min_grade is not None and q.grade is not None:
        grade_order = ["FEED_ONLY", "MULTISOURCE_SHALLOW", "MULTISOURCE_USEFUL", "DEEP_RESEARCH_READY"]
        try:
            min_idx = grade_order.index(min_grade)
            actual_idx = grade_order.index(q.grade)
            if actual_idx < min_idx:
                return False, f"Grade {q.grade} is below minimum required grade {min_grade}"
        except ValueError:
            pass  # Unknown grade, skip check

    # Always fail for any QUALITY_FAIL_* unless feed_only is allowed and this is FEED_ONLY
    if q.quality_gate.startswith("QUALITY_FAIL_"):
        if q.quality_gate == "QUALITY_FAIL_FEED_ONLY" and allow_feed_only:
            return True, None
        return False, f"Research quality gate failed: {q.quality_gate}"

    # Warn for QUALITY_WARN_MULTISOURCE_SHALLOW but do not fail (min grade already checked above)
    if q.quality_gate == "QUALITY_WARN_MULTISOURCE_SHALLOW":
        return True, None

    return True, None


def _check_hardware_constrained_comparable(
    b: BenchmarkSurface,
    q: QualitySurface,
) -> tuple[bool, str | None]:
    """
    F214R2: Check hardware_constrained and research_quality_comparable are consistent.

    Fails if:
    - hardware_constrained=True but research_quality_comparable is True or None.
    """
    live_kpi = b.live_kpi or {}
    hardware_constrained = live_kpi.get("hardware_constrained", False)

    if hardware_constrained and q.research_quality_comparable is not False:
        return False, (
            f"hardware_constrained={hardware_constrained} but research_quality_comparable={q.research_quality_comparable} — "
            "hardware-constrained runs must not be marked comparable"
        )

    return True, None


def _check_swap_gate_comparable(b: BenchmarkSurface) -> tuple[bool, str | None]:
    """
    F215D: active300/active600 with swap_gate_triggered=True must have comparable_result=False.

    Fails if:
    - swap_gate_triggered=True but comparable_result is True or None.
    """
    if b.swap_gate_triggered and b.comparable_result is not False:
        return False, (
            f"swap_gate_triggered={b.swap_gate_triggered} but comparable_result={b.comparable_result} — "
            "active300/600 with high swap must not be marked comparable"
        )
    return True, None


# ---------------------------------------------------------------------------
# Main checker
# ---------------------------------------------------------------------------

def sanity_check(
    benchmark_path: str | Path | None = None,
    validation_path: str | Path | None = None,
    trace_path: str | Path | None = None,
    benchmark_raw: dict[str, Any] | None = None,
    validator_raw: dict[str, Any] | None = None,
    trace_raw: dict[str, Any] | None = None,
    allow_stale_trace: bool = False,
    quality_path: str | Path | None = None,
    quality_raw: dict[str, Any] | None = None,
    min_quality_grade: str | None = None,
    allow_feed_only: bool = False,
) -> SanityResult:
    """Load and sanity-check a result bundle.

    Can accept either file paths (for CLI use) or raw dicts (for test use).
    """
    result = SanityResult()

    # Load files if paths provided
    if benchmark_path and not benchmark_raw:
        raw_b = json.loads(Path(benchmark_path).read_text())
    else:
        raw_b = benchmark_raw or {}
    if validation_path and not validator_raw:
        raw_v = json.loads(Path(validation_path).read_text())
    else:
        raw_v = validator_raw or {}
    if trace_path and not trace_raw:
        raw_t = json.loads(Path(trace_path).read_text())
    else:
        raw_t = trace_raw or {}
    if quality_path and not quality_raw:
        raw_q = json.loads(Path(quality_path).read_text())
    else:
        raw_q = quality_raw or {}

    b = parse_benchmark(raw_b)
    v = parse_validator(raw_v)
    t = parse_trace(raw_t)
    # F215A: fall back to embedded research_quality from live_kpi when no quality_json
    q = parse_quality_with_fallback(raw_q, b.research_quality or {})

    result.benchmark = b
    result.validator = v
    result.trace = t
    result.quality_surface = q

    # Run all checks
    checks = {}

    ok, msg = _check_benchmark_fail_validator_pass(b, v)
    checks["benchmark_fail_validator_pass"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_stale_terminality(t, allow_stale_trace)
    checks["stale_terminality"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_benchmark_missing_source_family_outcomes(b, t)
    checks["benchmark_shape_source_family_outcomes"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_wallclock_budget(b)
    checks["wallclock_budget"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_feed_only_accepted_nonfeed_attempted(b, v)
    checks["feed_only_nonfeed_attempted"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    # F224C: FAIL_NONFEED_EVIDENCE_MISSING is a research quality failure (nonfeed evidence insufficient)
    ok, msg = _check_nonfeed_evidence_missing(b)
    checks["nonfeed_evidence_missing"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_research_quality(q, min_quality_grade, allow_feed_only)
    checks["research_quality"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    # F214R2: CT loss stage must be present when CT has raw evidence but zero accepted
    ok, msg = _check_ct_loss_stage_present(b)
    checks["ct_loss_stage_present"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    # F214R2: PUBLIC must be in lane_execution_counts when public was attempted
    ok, msg = _check_public_surface_present(b)
    checks["public_surface_present"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    # F214R2: hardware_constrained and research_quality_comparable must be consistent
    ok, msg = _check_hardware_constrained_comparable(b, q)
    checks["hardware_constrained_comparable"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    # F215D: swap gate — active300/active600 must not be marked comparable when swap is high
    ok, msg = _check_swap_gate_comparable(b)
    checks["swap_gate_comparable"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    result.checks = checks

    # Determine verdict — priority: RESEARCH_QUALITY > EVIDENCE_SURFACE > WALLCLOCK > STALE > SHAPE > SURFACE
    if result.disagreements:
        has_quality = any("Research quality gate" in d or "Grade" in d or "quality_gate" in d for d in result.disagreements)
        has_wallclock = any("actual=" in d for d in result.disagreements)
        has_stale = any("Stale trace verdict" in d for d in result.disagreements)
        has_shape = any("internal trace" in d for d in result.disagreements)
        # F214R2: Evidence surface failures (ct_loss_stage, public_surface, hardware_constrained)
        has_evidence_surface = any(
            "ct_loss_stage" in d or "public_surface" in d or "hardware_constrained" in d
            for d in result.disagreements
        )

        if has_quality:
            result.verdict = SanityVerdict.SANITY_FAIL_RESEARCH_QUALITY
        elif has_evidence_surface:
            result.verdict = SanityVerdict.SANITY_FAIL_SURFACE_DISAGREEMENT
        elif has_wallclock:
            result.verdict = SanityVerdict.SANITY_FAIL_WALLCLOCK_BUDGET
        elif has_stale:
            result.verdict = SanityVerdict.SANITY_FAIL_STALE_TERMINALITY
        elif has_shape:
            result.verdict = SanityVerdict.SANITY_FAIL_BENCHMARK_SHAPE_GAP
        else:
            result.verdict = SanityVerdict.SANITY_FAIL_SURFACE_DISAGREEMENT
    else:
        result.verdict = SanityVerdict.SANITY_PASS

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F211C Live Result Bundle Sanity Checker")
    parser.add_argument("--benchmark-json", type=Path)
    parser.add_argument("--validation-json", type=Path)
    parser.add_argument("--trace-json", type=Path)
    parser.add_argument("--quality-json", type=Path, help="Path to research quality score JSON from research_quality_score.py")
    parser.add_argument("--min-quality-grade", type=str, default=None,
                        help="Minimum acceptable grade (FEED_ONLY, MULTISOURCE_SHALLOW, MULTISOURCE_USEFUL, DEEP_RESEARCH_READY)")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument(
        "--allow-stale-trace",
        action="store_true",
        default=False,
        help="Do not fail when stale trace verdicts are present",
    )
    parser.add_argument(
        "--allow-feed-only",
        action="store_true",
        default=False,
        help="Do not fail when research quality gate is FEED_ONLY (smoke mode only)",
    )
    args = parser.parse_args(argv)

    result = sanity_check(
        benchmark_path=args.benchmark_json,
        validation_path=args.validation_json,
        trace_path=args.trace_json,
        allow_stale_trace=args.allow_stale_trace,
        quality_path=args.quality_json,
        min_quality_grade=args.min_quality_grade,
        allow_feed_only=args.allow_feed_only,
    )

    if args.output_json:
        args.output_json.write_text(json.dumps(result.to_dict(), indent=2))
    if args.output_md:
        args.output_md.write_text(result.to_md())

    print(result.verdict.value)
    for d in result.disagreements:
        print(f"  ! {d}")

    return 0 if result.verdict == SanityVerdict.SANITY_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
