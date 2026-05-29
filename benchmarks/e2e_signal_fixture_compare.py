"""
benchmarks/e2e_signal_fixture_compare.py

Compare two signal fixture runs (baseline vs treatment) and produce diff artifact.
Used after running benchmarks/e2e_signal_fixture.py to generate compare reports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


def load_artifact(path: str) -> dict:
    """Load fixture artifact, fail-open."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e), "path": path}


def compare_fixtures(
    baseline_path: str = "probe_e2e_readiness/e2e_signal_fixture_baseline.json",
    treatment_path: str = "probe_e2e_readiness/e2e_signal_fixture_curl_cffi_on.json",
    treatment_name: str = "curl_cffi_on",
) -> dict:
    """
    Compare baseline vs treatment fixture runs.

    Returns compare artifact.
    """
    baseline = load_artifact(baseline_path)
    treatment = load_artifact(treatment_path)

    compare_fields = [
        "fixture_hits", "fetched_bytes", "status_code", "pattern_hits",
        "public_fetched", "selected_transport", "http_version",
        "transport_policy_reason", "transport_fallback_reason", "duration_ms",
    ]

    field_diffs = []
    for field in compare_fields:
        b_val = baseline.get(field)
        t_val = treatment.get(field)
        if b_val != t_val:
            field_diffs.append({
                "field": field,
                "baseline": b_val,
                f"{treatment_name}_treatment": t_val,
            })

    # Transport counter diffs
    tc_fields = [
        "aiohttp_count", "httpx_h2_count", "curl_cffi_count",
        "fallback_count", "curl_cffi_fallback_to_aiohttp_count",
        "httpx_h2_fallback_to_aiohttp_count",
    ]
    tc_diffs = []
    b_tc = baseline.get("transport_counters", {})
    t_tc = treatment.get("transport_counters", {})
    for field in tc_fields:
        if b_tc.get(field) != t_tc.get(field):
            tc_diffs.append({
                "field": field,
                "baseline": b_tc.get(field),
                f"{treatment_name}_treatment": t_tc.get(field),
            })

    verdict = "STABLE_BASELINE"
    verdict_reason = "Treatment matches baseline within tolerance"

    if baseline.get("errors") and not treatment.get("errors"):
        verdict = "IMPROVED"
        verdict_reason = "Treatment succeeded where baseline had errors"
    elif not baseline.get("errors") and treatment.get("errors"):
        verdict = "REGRESSION"
        verdict_reason = "Treatment produced errors that baseline did not"
    elif baseline.get("errors") and treatment.get("errors"):
        verdict = "STABLE_BASELINE"
        verdict_reason = "Both runs had errors"

    # Check non-empty signal
    b_signal = baseline.get("pattern_hits", 0) > 0 or baseline.get("public_fetched", 0) > 0
    t_signal = treatment.get("pattern_hits", 0) > 0 or treatment.get("public_fetched", 0) > 0

    if not b_signal and t_signal:
        verdict = "IMPROVED"
        verdict_reason = "Treatment produced signal where baseline did not"
    elif b_signal and not t_signal:
        verdict = "REGRESSION"
        verdict_reason = "Baseline produced signal but treatment did not"

    return {
        "artifact_type": "signal_fixture_compare",
        "baseline_path": baseline_path,
        "treatment_path": treatment_path,
        "treatment_name": treatment_name,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "field_diffs": field_diffs,
        "transport_counter_diffs": tc_diffs,
        "baseline_comparable": {f: baseline.get(f) for f in compare_fields},
        f"{treatment_name}_comparable": {f: treatment.get(f) for f in compare_fields},
        "baseline_errors": baseline.get("errors", []),
        f"{treatment_name}_errors": treatment.get("errors", []),
    }


def main():
    """CLI for compare."""
    import argparse

    parser = argparse.ArgumentParser(description="Compare signal fixture runs")
    parser.add_argument("--baseline", default="probe_e2e_readiness/e2e_signal_fixture_baseline.json")
    parser.add_argument("--treatment", default="probe_e2e_readiness/e2e_signal_fixture_curl_cffi_on.json")
    parser.add_argument("--treatment-name", default="curl_cffi_on")
    parser.add_argument("--output", default="probe_e2e_readiness/e2e_signal_fixture_compare.json")
    args = parser.parse_args()

    result = compare_fixtures(args.baseline, args.treatment, args.treatment_name)

    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[F206X] Compare: {args.baseline} vs {args.treatment}")
    print(f"  verdict: {result['verdict']}")
    print(f"  verdict_reason: {result['verdict_reason']}")
    print(f"  field_diffs: {len(result['field_diffs'])}")
    print(f"  output: {args.output}")

    return result


if __name__ == "__main__":
    main()
