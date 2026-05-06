"""F210E — Live Result Bundle Sanity Checker.

Meta-checker that compares benchmark JSON + validation JSON + trace JSON
and reports disagreements between the three surfaces.

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
class SanityResult:
    verdict: SanityVerdict = SanityVerdict.SANITY_PASS
    checks: dict[str, bool] = field(default_factory=dict)
    disagreements: list[str] = field(default_factory=list)
    benchmark: BenchmarkSurface = field(default_factory=BenchmarkSurface)
    validator: ValidatorSurface = field(default_factory=ValidatorSurface)
    trace: TraceSurface = field(default_factory=TraceSurface)

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

_TRACE_STALE_VERDICTS = frozenset({
    "TRACE_TERMINALITY_STALE_BEFORE_NONFEED",
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


def _check_trace_stale_validator_pass(t: TraceSurface, v: ValidatorSurface) -> tuple[bool, str | None]:
    trace_stale = t.verdict in _TRACE_STALE_VERDICTS
    val_pass = v.acquisition_terminality_satisfied is True
    if trace_stale and val_pass:
        return False, (
            f"Trace verdict '{t.verdict}' but validator terminality_satisfied={v.acquisition_terminality_satisfied}"
        )
    return True, None


def _check_benchmark_missing_source_family_outcomes(
    b: BenchmarkSurface, t: TraceSurface
) -> tuple[bool, str | None]:
    bench_missing = b.live_kpi is None or b.live_kpi.get("source_family_outcomes") is None
    trace_has = t.raw_internal is not None and (
        (t.raw_internal.get("live_kpi") or {}).get("source_family_outcomes")
        or (t.raw_internal.get("live_kpi_snapshot") or {}).get("source_family_outcomes")
    )
    if bench_missing and trace_has:
        return False, "Benchmark missing source_family_outcomes but internal trace has them"
    return True, None


def _check_wallclock_budget(b: BenchmarkSurface) -> tuple[bool, str | None]:
    if b.actual_duration_s and b.planned_duration_s:
        if b.actual_duration_s > b.planned_duration_s * 1.5:
            return False, (
                f"Wallclock budget exceeded: actual={b.actual_duration_s:.1f}s vs planned={b.planned_duration_s:.1f}s"
            )
    return True, None


def _check_feed_only_accepted_nonfeed_attempted(
    b: BenchmarkSurface, v: ValidatorSurface
) -> tuple[bool, str | None]:
    outcomes = v.source_family_outcomes or []
    ct_attempted = any(o.get("family") == "ct" and o.get("attempted") for o in outcomes)
    public_attempted = any(o.get("family") == "public" and o.get("attempted") for o in outcomes)

    branch_mix = b.branch_mix or {}
    feed_only = (
        branch_mix.get("feed", 0) > 0
        and branch_mix.get("ct_findings", 0) == 0
        and branch_mix.get("public_findings", 0) == 0
    )
    if feed_only and (ct_attempted or public_attempted):
        return False, (
            "Feed-only accepted branch but nonfeed source outcomes were attempted"
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

    b = parse_benchmark(raw_b)
    v = parse_validator(raw_v)
    t = parse_trace(raw_t)

    result.benchmark = b
    result.validator = v
    result.trace = t

    # Run all checks
    checks = {}

    ok, msg = _check_benchmark_fail_validator_pass(b, v)
    checks["benchmark_fail_validator_pass"] = ok
    if not ok:
        assert msg is not None
        result.disagreements.append(msg)

    ok, msg = _check_trace_stale_validator_pass(t, v)
    checks["trace_stale_validator_pass"] = ok
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

    result.checks = checks

    # Determine verdict
    if result.disagreements:
        # Trace disagreements take priority (they indicate stale data)
        # Only match if the disagreement is specifically about trace staleness,
        # not casual mentions of the word "trace"
        has_trace = any(
            d.startswith("Trace verdict")
            for d in result.disagreements
        )
        has_wallclock = any("actual=" in d for d in result.disagreements)
        has_shape = any("internal trace" in d for d in result.disagreements)

        if has_trace:
            result.verdict = SanityVerdict.SANITY_FAIL_STALE_TERMINALITY
        elif has_wallclock:
            result.verdict = SanityVerdict.SANITY_FAIL_WALLCLOCK_BUDGET
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
    parser = argparse.ArgumentParser(description="F210E Live Result Bundle Sanity Checker")
    parser.add_argument("--benchmark-json", type=Path)
    parser.add_argument("--validation-json", type=Path)
    parser.add_argument("--trace-json", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args(argv)

    result = sanity_check(
        benchmark_path=args.benchmark_json,
        validation_path=args.validation_json,
        trace_path=args.trace_json,
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
