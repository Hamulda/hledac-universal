#!/usr/bin/env python3
"""
report_truth_trace.py — F208 Truth Boundary Diagnostic

Traces where F208 acquisition fields disappear across benchmark → internal report → validation boundaries.

Verdicts:
  TRACE_PASS_ALL_PRESENT              — all fields present, terminality satisfied
  TRACE_TERMINALITY_UNSATISFIED      — acquisition_report exists in both, terminality.satisfied=false
  TRACE_TERMINALITY_STALE_BEFORE_NONFEED — terminality snapshot computed before CT predispatch completed
  TRACE_BENCHMARK_SHAPE_GAP          — internal has return_guard/windup/source that benchmark is missing
  TRACE_DROP_BEFORE_EXPORT           — acquisition_report missing in internal
  TRACE_DROP_AT_EXPORT               — acquisition_report present in internal, missing in exported report
  TRACE_DROP_AT_BENCHMARK_PARSE      — acquisition_report present in internal, missing in benchmark
  TRACE_VALIDATOR_ALIAS_ONLY          — only validator fields present, no real acquisition data
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

# Fields whose absence in benchmark but presence in internal indicates a shape propagation gap
SHAPE_GAP_FIELDS = [
    "return_guard",
    "windup_guard_observation",
    "source_family_outcomes",
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
    acquisition_missing_in: list
    terminality_state: dict
    details: dict
    # Extended classification fields
    terminality_satisfied: Optional[bool] = None
    missing_lanes: Optional[list] = None
    benchmark_shape_gaps: Optional[list] = None
    internal_runtime_failures: Optional[list] = None
    terminality_source_outcome_mismatch: Optional[list] = None  # lanes that appear attempted but stale in missing_lanes


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


def trace_verdict(
    snapshots: dict,
    raw_benchmark: Optional[dict] = None,
    raw_internal: Optional[dict] = None,
) -> tuple[str, Optional[str], dict]:
    """Determine TRACE verdict from boundary snapshots.

    Args:
        snapshots: boundary -> BoundarySnapshot
        raw_benchmark: optional raw benchmark dict for terminality fields not in F208_FIELDS
        raw_internal: optional raw internal report dict for terminality and shape gap fields

    Returns:
        (verdict, drop_boundary, extended_fields)
        extended_fields: dict with terminality_satisfied, missing_lanes,
                        benchmark_shape_gaps, internal_runtime_failures
    """
    bench = snapshots.get("benchmark_json")
    internal = snapshots.get("internal_report_json")
    validation = snapshots.get("validation_json")

    # Default extended fields
    extended = {
        "terminality_satisfied": None,
        "missing_lanes": None,
        "benchmark_shape_gaps": None,
        "internal_runtime_failures": None,
        "terminality_source_outcome_mismatch": None,
    }

    if bench is None:
        return "TRACE_DROP_AT_BENCHMARK_PARSE", "benchmark_json (file not readable)", extended

    bench_acq = bench.present.get("acquisition_report")

    # Case: acquisition_report is a populated dict in benchmark
    if bench_acq is not None and bench_acq != "MISSING" and bench_acq != "null":
        # Determine terminality state — prefer internal report's terminality when available
        bm = raw_benchmark if raw_benchmark is not None else {}
        int_raw = raw_internal if raw_internal is not None else {}

        # Use internal's terminality if benchmark's is None/missing but internal's is populated
        term_checked = bm.get("acquisition_terminality_checked")
        if term_checked is None and int_raw.get("acquisition_terminality_checked") is not None:
            term_checked = int_raw["acquisition_terminality_checked"]
        term_satisfied = bm.get("acquisition_terminality_satisfied")
        if term_satisfied is None and int_raw.get("acquisition_terminality_satisfied") is not None:
            term_satisfied = int_raw["acquisition_terminality_satisfied"]
        term_missing_lanes = bm.get("acquisition_terminality_missing_lanes")
        if term_missing_lanes is None and int_raw.get("acquisition_terminality_missing_lanes") is not None:
            term_missing_lanes = int_raw["acquisition_terminality_missing_lanes"]

        terminality_ok = term_checked not in (None, "MISSING", "null", False) and \
                         term_satisfied not in (None, "MISSING", "null", False) and \
                         term_satisfied is True

        # Check for benchmark shape gaps: internal has fields benchmark doesn't
        gaps = []
        if internal is not None:
            for gf in SHAPE_GAP_FIELDS:
                bench_has = bench.present.get(gf) not in (None, "MISSING", "null")
                int_has = internal.present.get(gf) not in (None, "MISSING", "null")
                if int_has and not bench_has:
                    gaps.append(gf)

        # Check for internal runtime failures (return_guard/windup outcomes)
        # Only flag windup failures when terminality is not satisfied
        runtime_failures = []
        if int_raw.get("return_guard") and int_raw["return_guard"].get("return_guard_checked") is True:
            if int_raw["return_guard"].get("return_guard_satisfied") is False:
                runtime_failures.append("return_guard_unsatisfied")
        if not terminality_ok and int_raw.get("windup_guard_observation", {}).get("windup_guard_call_count", 0) == 0:
            if not int_raw.get("windup_guard_observation", {}).get("windup_guard_not_applicable"):
                runtime_failures.append("windup_guard_not_called")

        extended["terminality_satisfied"] = term_satisfied if term_satisfied is not None else False
        extended["missing_lanes"] = term_missing_lanes if term_missing_lanes else []
        extended["benchmark_shape_gaps"] = gaps if gaps else []
        extended["internal_runtime_failures"] = runtime_failures if runtime_failures else []

        # Detect terminality stale lanes: source_family_outcomes shows attempted=True
        # but lane still appears in missing_lanes (terminality snapshot was taken too early)
        stale_lanes = _find_terminality_stale_lanes(int_raw)
        extended["terminality_source_outcome_mismatch"] = stale_lanes if stale_lanes else None

        if terminality_ok and not gaps:
            return "TRACE_PASS_ALL_PRESENT", None, extended

        if not terminality_ok:
            # Check if this is a stale terminality case before marking as unsatisfied
            if stale_lanes:
                return "TRACE_TERMINALITY_STALE_BEFORE_NONFEED", "internal_report_json (terminality snapshot stale before nonfeed predispatch)", extended
            # acquisition_report exists in both, but terminality not satisfied → runtime failure
            return "TRACE_TERMINALITY_UNSATISFIED", "internal_report_json (terminality not satisfied)", extended

        if gaps:
            # Shape propagation gap — fields present in internal but not in benchmark
            return "TRACE_BENCHMARK_SHAPE_GAP", "benchmark_json (missing shape fields from internal)", extended

        return "TRACE_PASS_ALL_PRESENT", None, extended

    # Case: acquisition_report is null/missing in benchmark
    if bench_acq in (None, "null", "MISSING"):
        if internal is not None:
            int_acq = internal.present.get("acquisition_report")
            if int_acq not in (None, "null", "MISSING"):
                return "TRACE_DROP_AT_BENCHMARK_PARSE", "benchmark_json (internal report has it, benchmark doesn't)", extended
            if int_acq in (None, "null", "MISSING"):
                return "TRACE_DROP_BEFORE_EXPORT", "internal_report_json (scheduler never populated acquisition_report)", extended
        return "TRACE_DROP_BEFORE_EXPORT", "scheduler (acquisition_report never written to benchmark)", extended

    # Check for validator-only alias pattern
    if validation is not None:
        val_failures = validation.get("failures") or []
        if val_failures and all("acquisition_report" in f.get("field_path", "") for f in val_failures):
            return "TRACE_VALIDATOR_ALIAS_ONLY", "validation_json (only validator field paths, no real acquisition data)", extended

    return "TRACE_DROP_AT_EXPORT", "export boundary (scorecard fields not persisted)", extended


def _find_terminality_stale_lanes(
    raw_internal: Optional[dict],
) -> list:
    """Detect lanes where source_family_outcomes shows attempted but terminality.missing_lanes still lists them.

    This catches the timing mismatch where terminality snapshot was taken before CT predispatch
    completed: CT appears in source_family_outcomes as attempted=True, but
    acquisition_terminality_missing_lanes still contains CT.

    Returns list of stale lane names (e.g. ["CT", "PUBLIC"]).
    """
    if not raw_internal:
        return []

    # Walk acquisition_report from two possible locations:
    # 1. raw_internal["acquisition_report"]  (standard path)
    # 2. raw_internal["canonical_run_summary"]["acquisition_report"]  (nested path)
    acq = raw_internal.get("acquisition_report")
    if not isinstance(acq, dict):
        acq = raw_internal.get("canonical_run_summary", {}).get("acquisition_report")
    if not isinstance(acq, dict):
        # Fallback: treat raw_internal itself as the acquisition dict (top-level terminality fields)
        acq = raw_internal

    sf_outcomes = acq.get("source_family_outcomes") if isinstance(acq, dict) else None
    if not isinstance(sf_outcomes, list):
        return []

    # Collect attempted non-feed lanes from source_family_outcomes
    attempted_lanes = set()
    for outcome in sf_outcomes:
        if isinstance(outcome, dict):
            family = outcome.get("family")
            attempted = outcome.get("attempted")
            if family and attempted is True:
                attempted_lanes.add(family)

    # Check terminality.missing_lanes for overlap with attempted lanes
    terminality = acq.get("terminality") if isinstance(acq, dict) else None
    if not isinstance(terminality, dict):
        # Fallback: top-level terminality fields
        missing_raw = raw_internal.get("acquisition_terminality_missing_lanes")
        if not isinstance(missing_raw, list):
            return []
        missing_lanes_set = set(missing_raw)
    else:
        missing_lanes_list = terminality.get("missing_lanes")
        if not isinstance(missing_lanes_list, list):
            return []
        missing_lanes_set = set(missing_lanes_list)

    stale = list(attempted_lanes & missing_lanes_set)
    return stale


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
        verdict, drop, extended = trace_verdict(snapshots, raw_benchmark=None)
        result = TraceResult(
            verdict=verdict,
            drop_boundary=drop,
            boundary_snapshots={},
            acquisition_missing_in=[],
            terminality_state={},
            details={"error": f"benchmark JSON not readable: {benchmark_path}"},
            **extended,
        )
    else:
        # Extract benchmark fields
        bench_snap = extract_fields(benchmark, F208_FIELDS, "benchmark_json")
        snapshots["benchmark_json"] = bench_snap

        # Try to load internal report
        internal_raw = None
        if benchmark.get("report_json_path"):
            internal_raw = load_json(benchmark["report_json_path"])

        if internal_raw:
            int_snap = extract_fields(internal_raw, F208_FIELDS, "internal_report_json")
            # Internal report stores runtime truth differently (canonical_run_summary, not runtime_truth)
            if "canonical_run_summary" in internal_raw:
                int_snap.present["canonical_run_summary"] = internal_raw["canonical_run_summary"]
            # Handle shape gap fields that might not be in F208_FIELDS
            for gf in SHAPE_GAP_FIELDS:
                if gf not in F208_FIELDS and gf in internal_raw:
                    int_snap.present[gf] = internal_raw[gf]
            snapshots["internal_report_json"] = int_snap

        # Load validation if provided
        if validation_path:
            val_data = load_json(validation_path)
            if val_data:
                val_snap = extract_fields(val_data, ["overall_verdict", "pass", "failure_count"], "validation_json")
                snapshots["validation_json"] = val_snap

        # Determine terminality state — prefer internal's values when benchmark's is missing
        terminality = {}
        for tf in TERMINALITY_FIELDS:
            if benchmark.get(tf) is not None:
                terminality[tf] = benchmark[tf]
            elif internal_raw and internal_raw.get(tf) is not None:
                terminality[tf] = internal_raw[tf]
            else:
                terminality[tf] = None

        verdict, drop, extended = trace_verdict(snapshots, raw_benchmark=benchmark, raw_internal=internal_raw)

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
                "internal_report_has_acquisition_report": internal_raw.get("acquisition_report") is not None if internal_raw else False,
                "validation_verdict": validation_path and (load_json(validation_path) or {}).get("overall_verdict"),
            },
            **extended,
        )

    # Write outputs
    output_data = {
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
        "terminality_satisfied": result.terminality_satisfied,
        "missing_lanes": result.missing_lanes,
        "benchmark_shape_gaps": result.benchmark_shape_gaps,
        "internal_runtime_failures": result.internal_runtime_failures,
        "terminality_source_outcome_mismatch": result.terminality_source_outcome_mismatch,
    }

    with open(output_json_path, "w") as fh:
        json.dump(output_data, fh, indent=2)

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
    if result.acquisition_missing_in:
        for item in result.acquisition_missing_in:
            md += f"- {item}\n"
    else:
        md += "_none_\n"

    md += f"""
## Classification

| Field | Value |
|-------|-------|
| terminality_satisfied | `{result.terminality_satisfied}` |
| missing_lanes | `{result.missing_lanes}` |
| benchmark_shape_gaps | `{result.benchmark_shape_gaps}` |
| internal_runtime_failures | `{result.internal_runtime_failures}` |
| terminality_source_outcome_mismatch | `{result.terminality_source_outcome_mismatch}` |

## Terminality State

| Field | Value |
|-------|-------|
"""
    for tf, val in result.terminality_state.items():
        md += f"| {tf} | `{val}` |\n"

    # Add timing mismatch diagnosis when applicable
    mismatch = result.terminality_source_outcome_mismatch
    if mismatch:
        md += f"""
## Timing Diagnosis

**Likely Cause:** terminality_computed_before_nonfeed_predispatch

| Stale Lane | Explanation |
|------------|-------------|
"""
        for lane in mismatch:
            md += f"| {lane} | appears attempted in `source_family_outcomes` but still listed in `missing_lanes` — terminality snapshot was taken before CT/PUBLIC predispatch completed | `source_family_outcomes` vs `acquisition_terminality_missing_lanes` timing gap |\n"

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