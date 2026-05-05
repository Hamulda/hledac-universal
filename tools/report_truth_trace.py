#!/usr/bin/env python3
"""
report_truth_trace.py — F208 Truth Boundary Diagnostic

Traces where F208 acquisition fields disappear across benchmark → internal report → validation boundaries.

Verdicts:
  TRACE_PASS_ALL_PRESENT
  TRACE_DROP_BEFORE_EXPORT   — fields absent in internal report
  TRACE_DROP_AT_BENCHMARK_PARSE — fields present in internal report but missing benchmark
  TRACE_DROP_AT_EXPORT       — fields in scorecard but absent after export
  TRACE_VALIDATOR_ALIAS_ONLY — only validator fields present, no real acquisition data
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional


# F208 fields to trace at each boundary
F208_FIELDS = [
    "measurement_id",
    "status",
    "run_quality_verdict",
    "report_json_path",
    "runtime_truth",
    "live_kpi",
    "acquisition_report",
    "source_family_outcomes",
    "scheduler_exit",
    "return_guard",
    "windup_guard_observation",
]

# Terminality sub-fields
TERMINALITY_FIELDS = [
    "acquisition_terminality_checked",
    "acquisition_terminality_satisfied",
    "acquisition_terminality_missing_lanes",
    "acquisition_terminality_report",
]


@dataclass
class BoundarySnapshot:
    """Field presence at one processing boundary."""
    source: str  # e.g. "benchmark_json", "internal_report_json", "validation_json"
    present: dict = field(default_factory=dict)  # field -> value or "null"/"missing"
    missing: list = field(default_factory=list)
    nulls: list = field(default_factory=list)


@dataclass
class TraceResult:
    verdict: str
    drop_boundary: Optional[str]
    boundary_snapshots: dict
    acquisition_missing_in: list  # which boundaries have acquisition_report null
    terminality_state: dict
    details: dict


def extract_fields(obj: dict, fields: list, source: str) -> BoundarySnapshot:
    """Extract fields from an object, noting presence/absence/null."""
    snap = BoundarySnapshot(source=source)
    for f in fields:
        if f not in obj:
            snap.missing.append(f)
            snap.present[f] = "MISSING"
        elif obj[f] is None:
            snap.nulls.append(f)
            snap.present[f] = None
        else:
            snap.present[f] = obj[f]
    return snap


def trace_verdict(snapshots: dict, raw_benchmark: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """Determine TRACE verdict from boundary snapshots.

    Args:
        snapshots: boundary -> BoundarySnapshot
        raw_benchmark: optional raw benchmark dict for terminality fields not in F208_FIELDS
    """
    bench = snapshots.get("benchmark_json")
    internal = snapshots.get("internal_report_json")
    validation = snapshots.get("validation_json")

    if bench is None:
        return "TRACE_DROP_AT_BENCHMARK_PARSE", "benchmark_json (file not readable)"

    bench_acq = bench.present.get("acquisition_report")

    # Case: acquisition_report is a populated dict in benchmark → check terminality via raw_benchmark
    if bench_acq is not None and bench_acq != "MISSING" and bench_acq != "null":
        # Terminality fields may not be in F208_FIELDS — use raw_benchmark if available
        bm = raw_benchmark if raw_benchmark is not None else {}
        terminality_ok = all(
            bm.get(tf) not in (None, "MISSING", "null", False)
            for tf in TERMINALITY_FIELDS
        )
        if terminality_ok:
            return "TRACE_PASS_ALL_PRESENT", None
        # Terminality not all set → treat as drop (terminality not satisfied)
        return "TRACE_DROP_BEFORE_EXPORT", "internal_report_json (terminality not satisfied)"

    # Case: acquisition_report is null/missing in benchmark
    if bench_acq in (None, "null", "MISSING"):
        if internal is not None:
            int_acq = internal.present.get("acquisition_report")
            if int_acq not in (None, "null", "MISSING"):
                return "TRACE_DROP_AT_BENCHMARK_PARSE", "benchmark_json (internal report has it, benchmark doesn't)"
            if int_acq in (None, "null", "MISSING"):
                return "TRACE_DROP_BEFORE_EXPORT", "internal_report_json (scheduler never populated acquisition_report)"
        return "TRACE_DROP_BEFORE_EXPORT", "scheduler (acquisition_report never written to benchmark)"

    # Check for validator-only alias pattern
    if validation is not None:
        val_failures = validation.get("failures") or []
        if val_failures and all("acquisition_report" in f.get("field_path", "") for f in val_failures):
            return "TRACE_VALIDATOR_ALIAS_ONLY", "validation_json (only validator field paths, no real acquisition data)"

    return "TRACE_DROP_AT_EXPORT", "export boundary (scorecard fields not persisted)"


def load_json(path: str) -> Optional[dict]:
    """Load JSON file, return None on error."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def trace_boundaries(
    benchmark_path: str,
    validation_path: Optional[str],
    output_json_path: str,
    output_md_path: str,
) -> TraceResult:
    """Trace F208 truth across all boundaries."""

    # Load benchmark
    benchmark = load_json(benchmark_path)
    snapshots = {}

    if benchmark is None:
        snapshots["benchmark_json"] = None
        verdict, drop = trace_verdict(snapshots, raw_benchmark=benchmark)
        result = TraceResult(
            verdict=verdict,
            drop_boundary=drop,
            boundary_snapshots={},
            acquisition_missing_in=[],
            terminality_state={},
            details={"error": f"benchmark JSON not readable: {benchmark_path}"},
        )
    else:
        # Extract benchmark fields
        bench_snap = extract_fields(benchmark, F208_FIELDS, "benchmark_json")
        snapshots["benchmark_json"] = bench_snap

        # Try to load internal report
        internal = None
        if benchmark.get("report_json_path"):
            internal = load_json(benchmark["report_json_path"])

        if internal:
            int_snap = extract_fields(internal, F208_FIELDS, "internal_report_json")
            # Internal report stores runtime truth differently (canonical_run_summary, not runtime_truth)
            if "canonical_run_summary" in internal:
                int_snap.present["canonical_run_summary"] = internal["canonical_run_summary"]
            snapshots["internal_report_json"] = int_snap

        # Load validation if provided
        if validation_path:
            val_data = load_json(validation_path)
            if val_data:
                val_snap = extract_fields(val_data, ["overall_verdict", "pass", "failure_count"], "validation_json")
                snapshots["validation_json"] = val_snap

        # Determine terminality state
        terminality = {}
        for tf in TERMINALITY_FIELDS:
            if benchmark.get(tf) is not None:
                terminality[tf] = benchmark[tf]
            else:
                terminality[tf] = None

        verdict, drop = trace_verdict(snapshots, raw_benchmark=benchmark)

        # Build missing-in list
        missing_in = []
        for name, snap in snapshots.items():
            if snap and snap.nulls:
                missing_in.append(f"{name}: {', '.join(snap.nulls)}")
            elif snap and snap.missing:
                missing_in.append(f"{name}: {', '.join(snap.missing)}")

        result = TraceResult(
            verdict=verdict,
            drop_boundary=drop,
            boundary_snapshots={k: snap.present if snap else None for k, snap in snapshots.items()},
            acquisition_missing_in=missing_in,
            terminality_state=terminality,
            details={
                "measurement_id": benchmark.get("measurement_id"),
                "status": benchmark.get("status"),
                "run_quality_verdict": benchmark.get("run_quality_verdict"),
                "benchmark_has_acquisition_report": benchmark.get("acquisition_report") is not None,
                "internal_report_has_acquisition_report": internal.get("acquisition_report") is not None if internal else False,
                "validation_verdict": validation_path and (load_json(validation_path) or {}).get("overall_verdict"),
            },
        )

    # Write outputs
    with open(output_json_path, "w") as fh:
        json.dump(
            {
                "verdict": result.verdict,
                "drop_boundary": result.drop_boundary,
                "measurement_id": result.details.get("measurement_id"),
                "status": result.details.get("status"),
                "run_quality_verdict": result.details.get("run_quality_verdict"),
                "acquisition_report_in_benchmark": result.details.get("benchmark_has_acquisition_report"),
                "acquisition_report_in_internal": result.details.get("internal_report_has_acquisition_report"),
                "boundary_snapshots": result.boundary_snapshots,
                "acquisition_missing_in": result.acquisition_missing_in,
                "terminality_state": result.terminality_state,
                "validation_verdict": result.details.get("validation_verdict"),
            },
            fh,
            indent=2,
        )

    # Write markdown
    md = f"""# F208 Truth Boundary Trace

## Verdict: `{result.verdict}`

**Drop Boundary:** {result.drop_boundary or "none — all fields present"}

## Measurement

| Field | Value |
|-------|-------|
| measurement_id | {result.details.get("measurement_id", "N/A")} |
| status | {result.details.get("status", "N/A")} |
| run_quality_verdict | {result.details.get("run_quality_verdict", "N/A")} |
| validation_verdict | {result.details.get("validation_verdict", "N/A")} |

## Acquisition Report Presence

| Boundary | acquisition_report |
|----------|-------------------|
| benchmark_json | {str(result.details.get("benchmark_has_acquisition_report", "N/A")).upper()} |
| internal_report_json | {str(result.details.get("internal_report_has_acquisition_report", "N/A")).upper()} |

## Boundary Snapshots

"""

    for name, snap_data in result.boundary_snapshots.items():
        md += f"### {name}\n\n"
        if snap_data is None:
            md += "_not readable_\n\n"
            continue
        md += "| Field | Value |\n|------|-------|\n"
        for field, val in snap_data.items():
            if val is None:
                md += f"| {field} | `null` |\n"
            elif val == "MISSING":
                md += f"| {field} | _MISSING_ |\n"
            elif isinstance(val, dict):
                md += f"| {field} | `{json.dumps(val)[:80]}...` |\n"
            elif isinstance(val, list):
                md += f"| {field} | `{json.dumps(val)[:80]}...` |\n"
            else:
                md += f"| {field} | `{val}` |\n"
        md += "\n"

    md += "## Acquisition Missing In\n\n"
    for item in result.acquisition_missing_in:
        md += f"- {item}\n"

    md += f"""
## Terminality State

| Field | Value |
|-------|-------|
"""
    for tf, val in result.terminality_state.items():
        md += f"| {tf} | `{val}` |\n"

    with open(output_md_path, "w") as fh:
        fh.write(md)

    return result


def main():
    parser = argparse.ArgumentParser(description="F208 Truth Boundary Diagnostic")
    parser.add_argument("--benchmark-json", required=True, help="Path to benchmark JSON")
    parser.add_argument("--validation-json", help="Path to optional validation JSON")
    parser.add_argument("--output-json", required=True, help="Output JSON path")
    parser.add_argument("--output-md", required=True, help="Output markdown path")
    args = parser.parse_args()

    result = trace_boundaries(
        args.benchmark_json,
        args.validation_json,
        args.output_json,
        args.output_md,
    )

    print(f"TRACE verdict: {result.verdict}")
    print(f"Drop boundary: {result.drop_boundary}")
    print(f"JSON output: {args.output_json}")
    print(f"MD output: {args.output_md}")
    sys.exit(0)


if __name__ == "__main__":
    main()