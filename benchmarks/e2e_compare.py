#!/usr/bin/env python3
"""
benchmarks/e2e_compare.py — E2E artifact comparison tool for benchmark truth surface validation.

Usage:
    python benchmarks/e2e_compare.py <baseline_json> <new_json> [--out <output_json>]

Exit codes:
    0  — STABLE_BASELINE or NOISY_BUT_VALID
    1  — BROKEN
    2  — file not found or invalid JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_artifact(path: str) -> tuple[dict[str, Any] | None, str]:
    p = Path(path)
    if not p.exists():
        return None, f"FILE_NOT_FOUND: {path}"
    try:
        with open(p) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, f"NOT_A_DICT: {path} — got {type(data).__name__}"
        return data, ""
    except json.JSONDecodeError as e:
        return None, f"INVALID_JSON: {path} — {e}"
    except Exception as e:
        return None, f"ERROR: {path} — {e}"


TRUTH_SURFACES = [
    "runtime_truth",
    "timing_truth",
    "canonical_run_summary",
    "_signal_quality_classification",
    "temporal_signal_summary",
    "temporal_priority_hints",
    "transport_counters",
    "memory_truth",
]

# Artifact type detection
PROBE_AGGREGATE_FIELDS = {"runtime_truth_present", "timing_truth_present",
                            "canonical_run_summary_present", "report_paths_found"}
CANONICAL_REPORT_FIELDS = {"runtime_truth", "timing_truth", "canonical_run_summary"}


def detect_artifact_type(data: dict) -> str:
    """Probe aggregate has _present flags; canonical report has actual objects."""
    if PROBE_AGGREGATE_FIELDS.intersection(data.keys()):
        return "probe_aggregate"
    if CANONICAL_REPORT_FIELDS.intersection(data.keys()):
        return "canonical_report"
    return "unknown"


COMPARABLE_FIELDS = [
    "exit_code",
    "duration_actual_s",
    "actual_duration_s",
    "cycles_started",
    "cycles_completed",
    "accepted_findings",
    "public_accepted_findings",
    "feed_findings",
    "ct_findings",
    "errors",
]


def extract_count(data: dict, key: str) -> Any:
    return data.get(key)


def _present_flags(data: dict) -> dict[str, bool]:
    """For probe_aggregate: extract _present booleans as truth surface map."""
    return {
        "runtime_truth": data.get("runtime_truth_present", False),
        "timing_truth": data.get("timing_truth_present", False),
        "canonical_run_summary": data.get("canonical_run_summary_present", False),
        "temporal_signal_summary": data.get("temporal_summary_present", False),
        "temporal_priority_hints": data.get("temporal_priority_hints_present", False),
        "transport_counters": data.get("transport_counters_present", False),
        "memory_truth": data.get("memory_truth_present", False),
    }


def compare_artifacts(baseline: dict, new: dict) -> dict[str, Any]:
    result: dict[str, Any] = {
        "baseline_path": "",
        "new_path": "",
        "verdict": "UNKNOWN",
        "verdict_reason": "",
        "baseline_truth_surfaces": {},
        "new_truth_surfaces": {},
        "baseline_comparable": {},
        "new_comparable": {},
        "field_diffs": [],
        "new_added_surfaces": [],
        "new_missing_surfaces": [],
        "schema_mismatch_info": None,
    }

    new_type = detect_artifact_type(new)
    baseline_type = detect_artifact_type(baseline)
    result["new_artifact_type"] = new_type
    result["baseline_artifact_type"] = baseline_type

    # Schema mismatch informational (not a verdict driver)
    if new_type != baseline_type:
        result["schema_mismatch_info"] = (
            f"baseline={baseline_type}, new={new_type} — comparing truth surface availability only"
        )

    # For probe_aggregate: use _present flags as truth surface map
    if new_type == "probe_aggregate":
        for surf in TRUTH_SURFACES:
            if surf == "_signal_quality_classification":
                result["new_truth_surfaces"][surf] = surf in new
                result["baseline_truth_surfaces"][surf] = surf in baseline
            else:
                result["new_truth_surfaces"][surf] = new.get(f"{surf}_present", False)
                result["baseline_truth_surfaces"][surf] = baseline.get(f"{surf}_present", False)
    else:
        # canonical_report: actual objects
        for surf in TRUTH_SURFACES:
            result["baseline_truth_surfaces"][surf] = surf in baseline
            result["new_truth_surfaces"][surf] = surf in new

    # Comparable fields (only where both artifacts have the field)
    for field in COMPARABLE_FIELDS:
        b_val = extract_count(baseline, field)
        n_val = extract_count(new, field)
        result["baseline_comparable"][field] = b_val
        result["new_comparable"][field] = n_val
        if b_val is not None and n_val is not None and b_val != n_val:
            result["field_diffs"].append({
                "field": field,
                "baseline": b_val,
                "new": n_val,
            })

    # Surfaces in new but not baseline (additive)
    for surf in TRUTH_SURFACES:
        if result["new_truth_surfaces"].get(surf) and not result["baseline_truth_surfaces"].get(surf):
            result["new_added_surfaces"].append(surf)

    # Surfaces missing in new but were in baseline
    for surf in TRUTH_SURFACES:
        if result["baseline_truth_surfaces"].get(surf) and not result["new_truth_surfaces"].get(surf):
            result["new_missing_surfaces"].append(surf)

    # === VERDICT LOGIC ===
    # BROKEN only for: non-zero exit, invalid artifact, or incomplete run
    new_exit = new.get("exit_code", None)

    if new_exit is not None and new_exit != 0:
        result["verdict"] = "BROKEN"
        result["verdict_reason"] = f"new exit_code={new_exit} (expected 0)"
        return result

    if new.get("status") == "FAILED":
        result["verdict"] = "BROKEN"
        result["verdict_reason"] = "new status=FAILED"
        return result

    # For canonical_report: require actual truth objects
    if new_type == "canonical_report":
        required = ["runtime_truth", "timing_truth", "canonical_run_summary"]
        missing = [s for s in required if s not in new]
        if missing:
            result["verdict"] = "BROKEN"
            result["verdict_reason"] = f"canonical_report missing required truth surfaces: {missing}"
            return result

    # probe_aggregate: run completed successfully if exit_code=0 and status=COMPLETED
    # canonical_report: run completed if it has the required truth surfaces above
    # Both pass the completion check

    # Check count diffs - only for fields where BOTH artifacts have non-null values
    comparable_diffs = [
        d for d in result["field_diffs"]
        if d["field"] in new and d["field"] in baseline
    ]

    # Count how many baseline comparable fields are actually populated
    baseline_has_comparable = sum(
        1 for v in result["baseline_comparable"].values() if v is not None
    )
    sum(
        1 for v in result["new_comparable"].values() if v is not None
    )

    if new_type != baseline_type and baseline_has_comparable == 0:
        # Cross-schema comparison where baseline has no comparable metrics
        result["verdict"] = "NOISY_BUT_VALID"
        result["verdict_reason"] = (
            f"schema_mismatch={new_type} vs {baseline_type}, "
            f"baseline has no comparable fields, "
            f"new run completed exit=0 in {new.get('duration_actual_s', 'N/A')}s"
        )
    elif comparable_diffs:
        result["verdict"] = "NOISY_BUT_VALID"
        diff_names = [d["field"] for d in comparable_diffs]
        result["verdict_reason"] = (
            f"both artifacts valid and complete, counts differ: {diff_names}"
        )
    else:
        result["verdict"] = "STABLE_BASELINE"
        result["verdict_reason"] = "both artifacts valid and complete, no count differences"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two E2E benchmark artifacts for truth surface parity."
    )
    parser.add_argument("baseline", help="Baseline JSON artifact path")
    parser.add_argument("new", help="New JSON artifact path")
    parser.add_argument(
        "--out",
        default="probe_e2e_readiness/e2e_compare_report_truth.json",
        help="Output comparison JSON path (default: probe_e2e_readiness/e2e_compare_report_truth.json)",
    )
    args = parser.parse_args()

    baseline, err = load_artifact(args.baseline)
    if err:
        print(f"ERROR loading baseline: {err}", file=sys.stderr)
        return 2
    new, err = load_artifact(args.new)
    if err:
        print(f"ERROR loading new: {err}", file=sys.stderr)
        return 2

    if baseline is None or new is None:
        print("ERROR: artifact is None after validation", file=sys.stderr)
        return 2

    result = compare_artifacts(baseline, new)
    result["baseline_path"] = str(args.baseline)
    result["new_path"] = str(args.new)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Comparison saved to: {out_path}")
    print(f"Verdict: {result['verdict']}")
    print(f"Reason: {result['verdict_reason']}")

    if result["field_diffs"]:
        print(f"\nField differences ({len(result['field_diffs'])}):")
        for d in result["field_diffs"]:
            print(f"  {d['field']}: baseline={d['baseline']!r} new={d['new']!r}")

    if result["new_added_surfaces"]:
        print("\nNew additive surfaces in new artifact:")
        for s in result["new_added_surfaces"]:
            print(f"  + {s}")

    if result["new_missing_surfaces"]:
        print("\nWARNING — surfaces missing in new artifact:")
        for s in result["new_missing_surfaces"]:
            print(f"  - {s}")

    return 0 if result["verdict"] in ("STABLE_BASELINE", "NOISY_BUT_VALID") else 1


if __name__ == "__main__":
    sys.exit(main())
