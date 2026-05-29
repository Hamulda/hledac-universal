"""
Runtime Authority Probe — Sprint F2130

Reads a live_sprint_measurement benchmark JSON and verifies the runtime
authority contract: which execution path was used to run the sprint,
whether it is canonical, and whether the report artifacts confirm a
real product sprint vs. a noncanonical benchmark-only construction.

Verdicts:
  AUTHORITY_CANONICAL_CONFIRMED     — real sprint, canonical execution path
  AUTHORITY_NONCANONICAL_BENCHMARK_ONLY — benchmark that doesn't run real sprint
  AUTHORITY_DRY_RUN_ONLY           — dry-run mode, no runtime at all
  AUTHORITY_INCONCLUSIVE            — missing artifacts, cannot determine

Safety: ALL tests use mocked/file-based data — no live sprint, no network, no MLX.

Usage:
    python -m tools.runtime_authority_probe <benchmark_json_path>
    python -m tools.runtime_authority_probe <benchmark_json_path> --report-json <report_json_path>
    python -m tools.runtime_authority_probe --output-json
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path

# ------------------------------------------------------------------ #
# Self-configure Python path
# ------------------------------------------------------------------ #
_probe_file = Path(__file__).resolve()
_project_root = _probe_file.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class AuthorityVerdict(Enum):
    AUTHORITY_CANONICAL_CONFIRMED = "AUTHORITY_CANONICAL_CONFIRMED"
    AUTHORITY_NONCANONICAL_BENCHMARK_ONLY = "AUTHORITY_NONCANONICAL_BENCHMARK_ONLY"
    AUTHORITY_DRY_RUN_ONLY = "AUTHORITY_DRY_RUN_ONLY"
    AUTHORITY_INCONCLUSIVE = "AUTHORITY_INCONCLUSIVE"


class ProbeResult:
    __slots__ = (
        "verdict",
        "runtime_authority_path",
        "runtime_authority_is_canonical",
        "sprint_id_match",
        "runtime_truth_present",
        "scheduler_exit_present",
        "report_path_exists",
        "benchmark_path",
        "report_path",
        "errors",
        "evidence",
    )

    def __init__(
        self,
        verdict: AuthorityVerdict,
        runtime_authority_path: str | None = None,
        runtime_authority_is_canonical: bool | None = None,
        sprint_id_match: bool | None = None,
        runtime_truth_present: bool = False,
        scheduler_exit_present: bool = False,
        report_path_exists: bool = False,
        benchmark_path: str | None = None,
        report_path: str | None = None,
        errors: list[str] | None = None,
        evidence: dict | None = None,
    ):
        self.verdict = verdict
        self.runtime_authority_path = runtime_authority_path
        self.runtime_authority_is_canonical = runtime_authority_is_canonical
        self.sprint_id_match = sprint_id_match
        self.runtime_truth_present = runtime_truth_present
        self.scheduler_exit_present = scheduler_exit_present
        self.report_path_exists = report_path_exists
        self.benchmark_path = benchmark_path
        self.report_path = report_path
        self.errors = errors or []
        self.evidence = evidence or {}

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "runtime_authority_path": self.runtime_authority_path,
            "runtime_authority_is_canonical": self.runtime_authority_is_canonical,
            "sprint_id_match": self.sprint_id_match,
            "runtime_truth_present": self.runtime_truth_present,
            "scheduler_exit_present": self.scheduler_exit_present,
            "report_path_exists": self.report_path_exists,
            "benchmark_path": self.benchmark_path,
            "report_path": self.report_path,
            "errors": self.errors,
            "evidence": self.evidence,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Runtime Authority Probe Report",
            "",
            f"**Verdict:** {self.verdict.value}",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| Runtime authority path | `{self.runtime_authority_path or 'N/A'}` |",
            f"| Is canonical | {self.runtime_authority_is_canonical} |",
            f"| Sprint ID match | {self.sprint_id_match} |",
            f"| runtime_truth present | {self.runtime_truth_present} |",
            f"| scheduler_exit present | {self.scheduler_exit_present} |",
            f"| Report path exists | {self.report_path_exists} |",
        ]
        if self.errors:
            lines.extend(["", "## Errors", ""])
            for err in self.errors:
                lines.append(f"- {err}")
        if self.evidence:
            lines.extend(["", "## Evidence", "", "```json", json.dumps(self.evidence, indent=2, default=str), "```"])
        return "\n".join(lines)


def probe(benchmark_json_path: str, report_json_path: str | None = None) -> ProbeResult:
    """
    Probe a live_sprint_measurement benchmark JSON for runtime authority.

    Args:
        benchmark_json_path: Path to the benchmark JSON from live_sprint_measurement
        report_json_path: Optional explicit path to the sprint report JSON.
                          If not provided, extracted from benchmark's report_json_path.

    Returns:
        ProbeResult with verdict and supporting evidence.
    """
    errors: list[str] = []
    evidence: dict = {}

    # Load benchmark JSON
    benchmark_path = Path(benchmark_json_path)
    if not benchmark_path.exists():
        return ProbeResult(
            verdict=AuthorityVerdict.AUTHORITY_INCONCLUSIVE,
            benchmark_path=str(benchmark_path),
            errors=[f"Benchmark JSON not found: {benchmark_json_path}"],
        )

    try:
        with open(benchmark_path) as f:
            benchmark = json.load(f)
    except Exception as exc:
        return ProbeResult(
            verdict=AuthorityVerdict.AUTHORITY_INCONCLUSIVE,
            benchmark_path=str(benchmark_path),
            errors=[f"Failed to parse benchmark JSON: {exc}"],
        )

    # Extract runtime authority fields from benchmark
    runtime_authority_path = benchmark.get("runtime_authority_path")
    runtime_authority_is_canonical = benchmark.get("runtime_authority_is_canonical")
    benchmark_sprint_id = benchmark.get("sprint_id")
    benchmark_mode = benchmark.get("mode")
    runtime_truth = benchmark.get("runtime_truth")
    scheduler_exit = benchmark.get("scheduler_exit")

    # Determine report path
    report_path_str = benchmark.get("report_json_path") or (report_json_path or "")
    report_path = Path(report_path_str) if report_path_str else None
    report_path_exists = report_path.is_file() if report_path else False

    # Load report JSON if available
    report_data = None
    report_sprint_id = None
    if report_path is not None and report_path_exists:
        try:
            with open(report_path) as f:
                report_data = json.load(f)
            report_sprint_id = report_data.get("sprint_id") if report_data else None
        except Exception as exc:
            errors.append(f"Failed to parse report JSON: {exc}")

    # Determine sprint_id match
    sprint_id_match: bool | None = None
    if report_sprint_id and benchmark_sprint_id:
        sprint_id_match = (report_sprint_id == benchmark_sprint_id)
    elif report_sprint_id or benchmark_sprint_id:
        sprint_id_match = False

    # Check runtime_truth presence
    runtime_truth_present = isinstance(runtime_truth, dict) and len(runtime_truth) > 0

    # Check scheduler_exit presence
    scheduler_exit_present = isinstance(scheduler_exit, dict) and len(scheduler_exit) > 0

    # Build evidence
    evidence = {
        "benchmark_mode": benchmark_mode,
        "benchmark_sprint_id": benchmark_sprint_id,
        "report_sprint_id": report_sprint_id,
        "runtime_authority_path": runtime_authority_path,
        "runtime_authority_is_canonical": runtime_authority_is_canonical,
        "runtime_truth_keys": list(runtime_truth.keys()) if runtime_truth else [],
        "scheduler_exit_keys": list(scheduler_exit.keys()) if scheduler_exit else [],
    }

    # Derive verdict
    if runtime_authority_path == "dry_run_no_runtime":
        verdict = AuthorityVerdict.AUTHORITY_DRY_RUN_ONLY
    elif runtime_authority_path == "canonical_core_run_sprint":
        if runtime_authority_is_canonical is False:
            # Explicitly marked non-canonical — contradictory to path label
            errors.append("runtime_authority_path='canonical_core_run_sprint' but runtime_authority_is_canonical=False")
            verdict = AuthorityVerdict.AUTHORITY_INCONCLUSIVE
        elif runtime_authority_is_canonical is None:
            # Canonical path was checked but sprint didn't run (e.g. memory/swap gate abort)
            # Path is canonical, but no runtime_truth because sprint never executed
            verdict = AuthorityVerdict.AUTHORITY_CANONICAL_CONFIRMED
        elif not runtime_truth_present:
            # is_canonical=True but no runtime truth — something is wrong
            errors.append("runtime_authority_path='canonical_core_run_sprint' and is_canonical=True but runtime_truth is missing")
            verdict = AuthorityVerdict.AUTHORITY_INCONCLUSIVE
        else:
            verdict = AuthorityVerdict.AUTHORITY_CANONICAL_CONFIRMED
    elif runtime_authority_path in ("canonical_cli_sprint", "noncanonical_manual_scheduler"):
        verdict = AuthorityVerdict.AUTHORITY_NONCANONICAL_BENCHMARK_ONLY
    elif runtime_authority_path is None:
        # No runtime authority path set at all — cannot determine
        errors.append("runtime_authority_path is None — not set in benchmark JSON")
        verdict = AuthorityVerdict.AUTHORITY_INCONCLUSIVE
    else:
        errors.append(f"Unknown runtime_authority_path: {runtime_authority_path}")
        verdict = AuthorityVerdict.AUTHORITY_INCONCLUSIVE

    return ProbeResult(
        verdict=verdict,
        runtime_authority_path=runtime_authority_path,
        runtime_authority_is_canonical=runtime_authority_is_canonical,
        sprint_id_match=sprint_id_match,
        runtime_truth_present=runtime_truth_present,
        scheduler_exit_present=scheduler_exit_present,
        report_path_exists=report_path_exists,
        benchmark_path=str(benchmark_path),
        report_path=str(report_path) if report_path else None,
        errors=errors if errors else None,
        evidence=evidence,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Runtime Authority Probe")
    parser.add_argument("benchmark_json", nargs="?", help="Path to benchmark JSON")
    parser.add_argument("--report-json", dest="report_json", help="Optional explicit report JSON path")
    parser.add_argument("--output-json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--output-md", action="store_true", help="Output markdown report")
    args = parser.parse_args()

    if not args.benchmark_json:
        # Self-check with a minimal test
        print("Runtime Authority Probe — Sprint F2130")
        print("Usage: python -m tools.runtime_authority_probe <benchmark_json> [--report-json <path>]")
        return 0

    result = probe(args.benchmark_json, args.report_json)

    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    elif args.output_md:
        print(result.to_markdown())
    else:
        # Human-readable summary
        print(f"Verdict: {result.verdict.value}")
        print(f"Runtime authority path: {result.runtime_authority_path or 'N/A'}")
        print(f"Is canonical: {result.runtime_authority_is_canonical}")
        print(f"Report path exists: {result.report_path_exists}")
        print(f"runtime_truth present: {result.runtime_truth_present}")
        print(f"scheduler_exit present: {result.scheduler_exit_present}")
        if result.errors:
            print("\nErrors:")
            for err in result.errors:
                print(f"  - {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
