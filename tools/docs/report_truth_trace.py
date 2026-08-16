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
import gc
import json
import sys
from dataclasses import dataclass, field
import msgspec
from _core import aclose
from compat.msgspec_gc_compat import Struct


__all__ = [
    'BoundarySnapshot', 'TraceResult', 'F208_FIELDS', 'TERMINALITY_FIELDS',
    'SHAPE_GAP_FIELDS', 'extract_fields', 'trace_verdict', 'trace_boundaries',
    'load_json', 'main',
]
F208_FIELDS = ['measurement_id', 'status', 'run_quality_verdict', 'report_json_path', 'runtime_truth', 'live_kpi', 'acquisition_report', 'source_family_outcomes', 'scheduler_exit', 'return_guard', 'windup_guard_observation']
TERMINALITY_FIELDS = ['acquisition_terminality_checked', 'acquisition_terminality_satisfied', 'acquisition_terminality_missing_lanes', 'acquisition_terminality_report']
SHAPE_GAP_FIELDS = ['return_guard', 'windup_guard_observation', 'source_family_outcomes']

class BoundarySnapshot(Struct):
    """Field presence at one processing boundary."""
    source: str
    present: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    nulls: list = field(default_factory=list)

class TraceResult(Struct, frozen=True):
    verdict: str
    drop_boundary: str | None
    boundary_snapshots: dict
    acquisition_missing_in: list
    terminality_state: dict
    details: dict
    terminality_satisfied: bool | None = None
    missing_lanes: list | None = None
    benchmark_shape_gaps: list | None = None
    internal_runtime_failures: list | None = None
    terminality_source_outcome_mismatch: list | None = None

def extract_fields(obj: dict, fields: list, source: str) -> BoundarySnapshot:
    """Extract fields from an object, noting presence/absence/null."""
    snap = BoundarySnapshot(source=source)
    for f in fields:
        if f not in obj:
            snap.missing.append(f)
            snap.present[f] = 'MISSING'
        elif obj[f] is None:
            snap.nulls.append(f)
            snap.present[f] = None
        else:
            snap.present[f] = obj[f]
    return snap

def _is_nullish(value: object) -> bool:
    """Check if value is considered null/absent in F208 field tracking."""
    return value in (None, 'MISSING', 'null')


def _resolve_term_field(raw_benchmark: dict, raw_internal: dict, field: str):
    """Resolve terminality field from benchmark, falling back to internal."""
    bm_val = raw_benchmark.get(field) if raw_benchmark else None
    return bm_val if bm_val is not None else (raw_internal.get(field) if raw_internal else None)


def _is_terminality_ok(term_checked, term_satisfied) -> bool:
    """Check if terminality check passed all requirements."""
    return not _is_nullish(term_checked) and term_checked is not False and term_satisfied is True


def _detect_runtime_failures(int_raw: dict, terminality_ok: bool) -> list[str]:
    """Detect runtime guard failures in internal report."""
    failures = []
    return_guard = int_raw.get('return_guard', {})
    if return_guard.get('return_guard_checked') is True and return_guard.get('return_guard_satisfied') is False:
        failures.append('return_guard_unsatisfied')

    windup = int_raw.get('windup_guard_observation', {})
    if not terminality_ok and windup.get('windup_guard_call_count', 0) == 0:
        if not windup.get('windup_guard_not_applicable'):
            failures.append('windup_guard_not_called')
    return failures


def _compute_shape_gaps(bench: BoundarySnapshot, internal: BoundarySnapshot | None) -> list[str]:
    """Compute benchmark shape gaps - fields present in internal but missing from benchmark."""
    gaps = []
    if internal is None:
        return gaps
    for field in SHAPE_GAP_FIELDS:
        bench_has = not _is_nullish(bench.present.get(field))
        int_has = not _is_nullish(internal.present.get(field))
        if int_has and not bench_has:
            gaps.append(field)
    return gaps


def _build_extended_result(
    raw_benchmark: dict,
    raw_internal: dict,
    terminality_ok: bool,
    gaps: list[str],
    stale_lanes: list,
) -> dict:
    """Build extended result dict with all derived fields."""
    term_satisfied = _resolve_term_field(raw_benchmark, raw_internal, 'acquisition_terminality_satisfied')
    term_missing_lanes = _resolve_term_field(raw_benchmark, raw_internal, 'acquisition_terminality_missing_lanes')

    return {
        'terminality_satisfied': term_satisfied if term_satisfied is not None else False,
        'missing_lanes': term_missing_lanes if term_missing_lanes else [],
        'benchmark_shape_gaps': gaps if gaps else [],
        'internal_runtime_failures': _detect_runtime_failures(raw_internal or {}, terminality_ok),
        'terminality_source_outcome_mismatch': stale_lanes if stale_lanes else None,
    }


def trace_verdict(snapshots: dict, raw_benchmark: dict | None=None, raw_internal: dict | None=None) -> tuple[str, str | None, dict]:
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
    bench = snapshots.get('benchmark_json')
    internal = snapshots.get('internal_report_json')
    validation = snapshots.get('validation_json')
    extended_base = {
        'terminality_satisfied': None,
        'missing_lanes': None,
        'benchmark_shape_gaps': None,
        'internal_runtime_failures': None,
        'terminality_source_outcome_mismatch': None,
    }

    # No benchmark readable
    if bench is None:
        return ('TRACE_DROP_AT_BENCHMARK_PARSE', 'benchmark_json (file not readable)', extended_base)

    bench_acq = bench.present.get('acquisition_report')
    bm_raw = raw_benchmark or {}
    int_raw = raw_internal or {}

    # Case 1: Benchmark has acquisition_report
    if bench_acq is not None and not _is_nullish(bench_acq):
        term_checked = _resolve_term_field(bm_raw, int_raw, 'acquisition_terminality_checked')
        term_satisfied = _resolve_term_field(bm_raw, int_raw, 'acquisition_terminality_satisfied')
        terminality_ok = _is_terminality_ok(term_checked, term_satisfied)
        gaps = _compute_shape_gaps(bench, internal)
        stale_lanes = _find_terminality_stale_lanes(int_raw)
        extended = _build_extended_result(bm_raw, int_raw, terminality_ok, gaps, stale_lanes)

        # Terminality verdicts
        if terminality_ok and not gaps:
            return ('TRACE_PASS_ALL_PRESENT', None, extended)
        if not terminality_ok:
            if stale_lanes:
                return ('TRACE_TERMINALITY_STALE_BEFORE_NONFEED',
                        'internal_report_json (terminality snapshot stale before nonfeed predispatch)', extended)
            return ('TRACE_TERMINALITY_UNSATISFIED', 'internal_report_json (terminality not satisfied)', extended)
        if gaps:
            return ('TRACE_BENCHMARK_SHAPE_GAP', 'benchmark_json (missing shape fields from internal)', extended)
        return ('TRACE_PASS_ALL_PRESENT', None, extended)

    # Case 2: Benchmark missing acquisition_report
    if _is_nullish(bench_acq):
        if internal is not None:
            int_acq = internal.present.get('acquisition_report')
            if not _is_nullish(int_acq):
                return ('TRACE_DROP_AT_BENCHMARK_PARSE',
                        "benchmark_json (internal report has it, benchmark doesn't)", extended_base)
            if _is_nullish(int_acq):
                return ('TRACE_DROP_BEFORE_EXPORT',
                        'internal_report_json (scheduler never populated acquisition_report)', extended_base)
        return ('TRACE_DROP_BEFORE_EXPORT',
                'scheduler (acquisition_report never written to benchmark)', extended_base)

    # Case 3: Validation-only failures
    if validation is not None:
        val_failures = validation.present.get('failures') or []
        if val_failures and all('acquisition_report' in f.get('field_path', '') for f in val_failures):
            return ('TRACE_VALIDATOR_ALIAS_ONLY',
                    'validation_json (only validator field paths, no real acquisition data)', extended_base)

    return ('TRACE_DROP_AT_EXPORT', 'export boundary (scorecard fields not persisted)', extended_base)

def _find_terminality_stale_lanes(raw_internal: dict | None) -> list[str]:
    """Detect lanes where source_family_outcomes shows attempted but terminality.missing_lanes still lists them.

    This catches the timing mismatch where terminality snapshot was taken before CT predispatch
    completed: CT appears in source_family_outcomes as attempted=True, but
    acquisition_terminality_missing_lanes still contains CT.

    Returns list of stale lane names (e.g. ["CT", "PUBLIC"]).
    """
    if not raw_internal:
        return []

    # Resolve acquisition_report - try nested paths
    acq = raw_internal.get('acquisition_report')
    if not isinstance(acq, dict):
        acq = raw_internal.get('canonical_run_summary', {}).get('acquisition_report')
    if not isinstance(acq, dict):
        acq = raw_internal

    # Get attempted lanes from source_family_outcomes
    attempted_lanes = _extract_attempted_lanes(acq)
    if not attempted_lanes:
        return []

    # Get missing lanes from terminality
    missing_lanes = _extract_missing_lanes(acq, raw_internal)
    if not missing_lanes:
        return []

    # Return intersection as list
    return list(attempted_lanes & missing_lanes)


def _extract_attempted_lanes(acq: dict) -> set[str]:
    """Extract family names where attempted=True from source_family_outcomes."""
    sf_outcomes = acq.get('source_family_outcomes')
    if not isinstance(sf_outcomes, list):
        return set()
    return {
        outcome.get('family')
        for outcome in sf_outcomes
        if isinstance(outcome, dict)
        and outcome.get('family')
        and outcome.get('attempted') is True
    }


def _extract_missing_lanes(acq: dict, raw_internal: dict) -> set[str]:
    """Extract missing lanes from terminality or raw_internal."""
    terminality = acq.get('terminality')
    if isinstance(terminality, dict):
        missing_list = terminality.get('missing_lanes')
        if isinstance(missing_list, list):
            return set(missing_list)

    missing_raw = raw_internal.get('acquisition_terminality_missing_lanes')
    if isinstance(missing_raw, list):
        return set(missing_raw)
    return set()

def load_json(path: str) -> dict | None:
    """Load JSON file, return None on error."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _build_terminality_state(benchmark: dict, internal_raw: dict | None) -> dict[str, object]:
    """Build terminality state dict from benchmark/internal raw data."""
    result = {}
    for tf in TERMINALITY_FIELDS:
        bm_val = benchmark.get(tf)
        if bm_val is not None:
            result[tf] = bm_val
        elif internal_raw:
            result[tf] = internal_raw.get(tf)
        else:
            result[tf] = None
    return result


def _build_missing_in_list(snapshots: dict) -> list[str]:
    """Build list of boundaries with missing/null fields."""
    missing_in = []
    for name, snap in snapshots.items():
        if snap is None:
            continue
        if snap.nulls:
            missing_in.append(f"{name}: {', '.join(snap.nulls)}")
        elif snap.missing:
            missing_in.append(f"{name}: {', '.join(snap.missing)}")
    return missing_in


def _build_result_details(
    benchmark: dict,
    internal_raw: dict | None,
    validation_path: str | None,
) -> dict[str, object]:
    """Build details dict for TraceResult."""
    validation_verdict = None
    if validation_path:
        val_data = load_json(validation_path)
        if val_data and isinstance(val_data, dict):
            validation_verdict = val_data.get('overall_verdict')

    return {
        'measurement_id': benchmark.get('measurement_id'),
        'status': benchmark.get('status'),
        'run_quality_verdict': benchmark.get('run_quality_verdict'),
        'benchmark_has_acquisition_report': benchmark.get('acquisition_report') is not None,
        'internal_report_has_acquisition_report': (
            bool(internal_raw and internal_raw.get('acquisition_report'))
        ),
        'validation_verdict': validation_verdict,
    }


def _format_field_value(val: object) -> str:
    """Format a single field value for markdown table."""
    if val is None:
        return '`null`'
    if val == 'MISSING':
        return '_MISSING_'
    if isinstance(val, (dict, list)):
        return f'`{json.dumps(val)[:80]}...`'
    return f'`{val}`'


def _build_md_report(result: TraceResult) -> str:
    """Build complete markdown report from TraceResult."""
    parts = [
        f"# F208 Truth Boundary Trace\n\n"
        f"## Verdict: `{result.verdict}`\n\n"
        f"**Drop Boundary:** {result.drop_boundary or 'none — all fields present'}\n\n"
        f"## Measurement\n\n"
        f"| Field | Value |\n|-------|-------|\n"
        f"| measurement_id | {result.details.get('measurement_id', 'N/A')} |\n"
        f"| status | {result.details.get('status', 'N/A')} |\n"
        f"| run_quality_verdict | {result.details.get('run_quality_verdict', 'N/A')} |\n"
        f"| validation_verdict | {result.details.get('validation_verdict', 'N/A')} |\n\n"
        f"## Acquisition Report Presence\n\n"
        f"| Boundary | acquisition_report |\n"
        f"|----------|-------------------|\n"
        f"| benchmark_json | {str(result.details.get('benchmark_has_acquisition_report', 'N/A')).upper()} |\n"
        f"| internal_report_json | {str(result.details.get('internal_report_has_acquisition_report', 'N/A')).upper()} |\n\n"
        f"## Boundary Snapshots\n\n"
    ]

    # Boundary snapshots
    for name, snap_data in result.boundary_snapshots.items():
        parts.append(f'### {name}\n\n')
        if snap_data is None:
            parts.append('_not readable_\n\n')
            continue
        parts.append('| Field | Value |\n|------|-------|\n')
        for field, val in snap_data.items():
            parts.append(f'| {field} | {_format_field_value(val)} |\n')
        parts.append('\n')

    # Acquisition Missing In
    parts.append('## Acquisition Missing In\n\n')
    if result.acquisition_missing_in:
        for item in result.acquisition_missing_in:
            parts.append(f'- {item}\n')
    else:
        parts.append('_none_\n')

    # Classification
    parts.append(
        f"\n## Classification\n\n"
        f"| Field | Value |\n|-------|-------|\n"
        f"| terminality_satisfied | `{result.terminality_satisfied}` |\n"
        f"| missing_lanes | `{result.missing_lanes}` |\n"
        f"| benchmark_shape_gaps | `{result.benchmark_shape_gaps}` |\n"
        f"| internal_runtime_failures | `{result.internal_runtime_failures}` |\n"
        f"| terminality_source_outcome_mismatch | `{result.terminality_source_outcome_mismatch}` |\n\n"
        f"## Terminality State\n\n"
        f"| Field | Value |\n|-------|-------|\n"
    )
    for tf, val in result.terminality_state.items():
        parts.append(f'| {tf} | `{val}` |\n')

    # Timing Diagnosis
    mismatch = result.terminality_source_outcome_mismatch
    if mismatch:
        parts.append(
            f"\n## Timing Diagnosis\n\n"
            f"**Likely Cause:** terminality_computed_before_nonfeed_predispatch\n\n"
            f"| Stale Lane | Explanation |\n"
            f"|------------|-------------|\n"
    )
        for lane in mismatch:
            parts.append(
                f'| {lane} | appears attempted in `source_family_outcomes` '
                f'but still listed in `missing_lanes` — terminality snapshot '
                f'was taken before CT/PUBLIC predispatch completed |\n'
    )

    return ''.join(parts)

def _extract_internal_snapshot(benchmark: dict) -> tuple[BoundarySnapshot | None, dict | None]:
    """Extract internal report snapshot and return raw dict."""
    report_path = benchmark.get('report_json_path')
    if not report_path:
        return None, None

    internal_raw = load_json(report_path)
    if not isinstance(internal_raw, dict):
        return None, None

    int_snap = extract_fields(internal_raw, F208_FIELDS, 'internal_report_json')
    if 'canonical_run_summary' in internal_raw:
        int_snap.present['canonical_run_summary'] = internal_raw['canonical_run_summary']
    for gf in SHAPE_GAP_FIELDS:
        if gf not in F208_FIELDS and gf in internal_raw:
            int_snap.present[gf] = internal_raw[gf]
    return int_snap, internal_raw


def _serialize_result_to_json(result: TraceResult, output_path: str) -> None:
    """Serialize TraceResult to flattened JSON file."""
    output_data = {
        'verdict': result.verdict,
        'drop_boundary': result.drop_boundary,
        'measurement_id': result.details.get('measurement_id'),
        'status': result.details.get('status'),
        'run_quality_verdict': result.details.get('run_quality_verdict'),
        'acquisition_report_in_benchmark': result.details.get('benchmark_has_acquisition_report'),
        'acquisition_report_in_internal': result.details.get('internal_report_has_acquisition_report'),
        'boundary_snapshots': result.boundary_snapshots,
        'acquisition_missing_in': result.acquisition_missing_in,
        'terminality_state': result.terminality_state,
        'validation_verdict': result.details.get('validation_verdict'),
        'terminality_satisfied': result.terminality_satisfied,
        'missing_lanes': result.missing_lanes,
        'benchmark_shape_gaps': result.benchmark_shape_gaps,
        'internal_runtime_failures': result.internal_runtime_failures,
        'terminality_source_outcome_mismatch': result.terminality_source_outcome_mismatch,
    }
    with open(output_path, 'w') as fh:
        json.dump(output_data, fh, indent=2)


def trace_boundaries(
    benchmark_path: str,
    validation_path: str | None,
    output_json_path: str,
    output_md_path: str,
) -> TraceResult:
    """Trace F208 truth across all boundaries."""
    benchmark = load_json(benchmark_path)
    snapshots: dict[str, BoundarySnapshot | None] = {}

    # Case: Benchmark not readable
    if benchmark is None:
        snapshots['benchmark_json'] = None
        verdict, drop, extended = trace_verdict(snapshots)
        return _build_error_result(verdict, drop, extended, benchmark_path, output_json_path, output_md_path)

    # Normal case: Build snapshots from benchmark
    bench_snap = extract_fields(benchmark, F208_FIELDS, 'benchmark_json')
    snapshots['benchmark_json'] = bench_snap

    # Extract internal report if available
    int_snap, internal_raw = _extract_internal_snapshot(benchmark)
    if int_snap:
        snapshots['internal_report_json'] = int_snap

    # Load validation if provided
    if validation_path:
        val_data = load_json(validation_path)
        if val_data:
            val_snap = extract_fields(val_data, ['overall_verdict', 'pass', 'failure_count'], 'validation_json')
            # Store raw failures list if present for detailed analysis
            if 'failures' in val_data:
                val_snap.present['failures'] = val_data['failures']
            snapshots['validation_json'] = val_snap

    # Build terminality state and get verdict
    terminality = _build_terminality_state(benchmark, internal_raw)
    verdict, drop, extended = trace_verdict(snapshots, raw_benchmark=benchmark, raw_internal=internal_raw)
    missing_in = _build_missing_in_list(snapshots)
    details = _build_result_details(benchmark, internal_raw, validation_path)

    result = TraceResult(
        verdict=verdict, drop_boundary=drop,
        boundary_snapshots={k: snap.present if snap else None for k, snap in snapshots.items()},
        acquisition_missing_in=missing_in, terminality_state=terminality,
        details=details, **extended
    )

    # Write outputs
    _serialize_result_to_json(result, output_json_path)
    with open(output_md_path, 'w') as fh:
        fh.write(_build_md_report(result))

    return result


def _build_error_result(
    verdict: str,
    drop: str | None,
    extended: dict,
    benchmark_path: str,
    output_json_path: str,
    output_md_path: str,
) -> TraceResult:
    """Build error result for unreadable benchmark."""
    result = TraceResult(
        verdict=verdict, drop_boundary=drop, boundary_snapshots={},
        acquisition_missing_in=[], terminality_state={},
        details={'error': f'benchmark JSON not readable: {benchmark_path}'}, **extended
    )
    _serialize_result_to_json(result, output_json_path)
    with open(output_md_path, 'w') as fh:
        fh.write(_build_md_report(result))
    return result

def main():
    parser = argparse.ArgumentParser(description='F208 Truth Boundary Diagnostic', suggest_on_error=True, color=True)
    parser.add_argument('--benchmark-json', required=True, help='Path to benchmark JSON')
    parser.add_argument('--validation-json', help='Path to optional validation JSON')
    parser.add_argument('--output-json', required=True, help='Output JSON path')
    parser.add_argument('--output-md', required=True, help='Output markdown path')
    args = parser.parse_args()
    result = trace_boundaries(args.benchmark_json, args.validation_json, args.output_json, args.output_md)
    print(f'TRACE verdict: {result.verdict}')
    print(f'Drop boundary: {result.drop_boundary}')
    print(f'JSON output: {args.output_json}')
    print(f'MD output: {args.output_md}')
    sys.exit(0)
if __name__ == '__main__':
    main()