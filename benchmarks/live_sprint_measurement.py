"""
F206BH LIVE SPRINT MEASUREMENT HARNESS

Canonical live sprint measurement: run 180s/300s/600s measured sprints and capture
metrics reproducibly.

Safety invariants:
- Default is --dry-run (no live sprint)
- Live execution requires explicit --live flag
- No stealth default, no aggressive default
- Duration < 180s blocked unless --allow-smoke passed
- No live network during tests
- No MLX model load during tests

Profiles:
  smoke180  → 180s sprint (smoke test)
  active300 → 300s sprint (standard)
  active600 → 600s sprint (extended)

Usage:
  # Dry-run (default, hermetic)
  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"
  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --dry-run

  # Live execution (requires --live)
  python benchmarks/live_sprint_measurement.py --profile active300 --query "LockBit" --live
  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/live_measure.json

  # Preflight check (no sprint execution)
  python benchmarks/live_sprint_measurement.py --print-preflight-only
  python benchmarks/live_sprint_measurement.py --print-preflight-only --output-json /tmp/preflight.json
"""
from __future__ import annotations
import dataclasses
import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from pathlib import Path as _P
_project_root = str(_P(__file__).resolve().parent.parent.parent)
import sys as _sys
_universal = str(_P(__file__).resolve().parent.parent)
if _universal not in _sys.path:
    _sys.path.insert(0, _universal)
import types as _types
_hledac_stub = _types.ModuleType('hledac')
_hledac_stub.__path__ = [_project_root, _universal]
_hledac_stub.__file__ = f'{_project_root}/hledac/__init__.py'
_hledac_stub.__package__ = 'hledac'
_hledac_stub.__spec__ = None
_sys.modules['hledac'] = _hledac_stub
from hledac.universal.core import __main__ as core_main
from hledac.universal.paths import get_sprint_json_report_path
from benchmarks.live_measurement_parser import parse_sprint_report as _parse_sprint_report_impl
import benchmarks.live_measurement_quality as _qm
from benchmarks.live_measurement_quality import _derive_run_quality_verdict as _quality_derive
PROFILE_DURATION: dict[str, int] = {'smoke180': 180, 'nonfeed_diagnostic180': 180, 'active300': 300, 'active600': 600, 'deep_osint_m1_300': 300}
PROFILE_META: dict[str, dict] = {'smoke180': {'planned_duration_s': 180, 'expected_windup_lead_s': 180, 'expected_active_window_s': 0, 'active_runtime_expected': False}, 'nonfeed_diagnostic180': {'planned_duration_s': 180, 'expected_windup_lead_s': 0, 'expected_active_window_s': 180, 'active_runtime_expected': True, 'acquisition_profile': 'nonfeed_diagnostic'}, 'active300': {'planned_duration_s': 300, 'expected_windup_lead_s': 180, 'expected_active_window_s': 120, 'active_runtime_expected': True}, 'active600': {'planned_duration_s': 600, 'expected_windup_lead_s': 180, 'expected_active_window_s': 420, 'active_runtime_expected': True}, 'deep_osint_m1_300': {'planned_duration_s': 300, 'expected_windup_lead_s': 180, 'expected_active_window_s': 120, 'active_runtime_expected': True, 'acquisition_profile': 'deep_osint_m1'}}
_CANONICAL_ACQUISITION_PROFILES = frozenset(['default', 'nonfeed_diagnostic', 'deep_osint_m1'])

def _resolve_acquisition_profile(profile: str) -> str:
    """
    F228A: Resolve benchmark profile → canonical acquisition profile.

    Reads PROFILE_META[profile]["acquisition_profile"] when present,
    falls back to "default". Validates returned value is a canonical
    acquisition profile name — never returns benchmark-specific aliases
    like "nonfeed_diagnostic180" as an acquisition_profile.
    """
    resolved = PROFILE_META.get(profile, {}).get('acquisition_profile', 'default')
    if resolved not in _CANONICAL_ACQUISITION_PROFILES:
        resolved = 'default'
    return resolved
MIN_DURATION_S = 180

def get_invocation_reality() -> dict:
    """
    Return a hermetic diagnostic dict about the invocation namespace.
    Does NOT import heavy MLX/model deps — only stdlib + already-imported locals.
    """
    import hledac as _hledac
    _live_sprint_measurement_file = str(_P(__file__).resolve())
    _core_main_file = str(_P(core_main.__file__).resolve()) if hasattr(core_main, '__file__') else 'N/A'
    _ss_mod = sys.modules.get('hledac.universal.runtime.sprint_scheduler')
    _sprint_scheduler_file = str(_P(_ss_mod.__file__).resolve()) if _ss_mod and hasattr(_ss_mod, '__file__') else 'N/A'
    _as_mod = sys.modules.get('hledac.universal.runtime.acquisition_strategy')
    _acquisition_strategy_file = str(_P(_as_mod.__file__).resolve()) if _as_mod and hasattr(_as_mod, '__file__') else 'N/A'
    _profile_names = list(PROFILE_DURATION.keys())
    _nonfeed_meta = PROFILE_META.get('nonfeed_diagnostic180', {})
    _nonfeed_present = 'nonfeed_diagnostic180' in PROFILE_DURATION
    _nonfeed_duration = PROFILE_DURATION.get('nonfeed_diagnostic180', 0)
    _nonfeed_acquisition_profile = _nonfeed_meta.get('acquisition_profile', 'nonfeed_diagnostic') if _nonfeed_meta else 'N/A'
    _repo_root_reality = get_repo_root_reality()
    return {'live_sprint_measurement_file': _live_sprint_measurement_file, 'core_main_file': _core_main_file, 'sprint_scheduler_file': _sprint_scheduler_file, 'acquisition_strategy_file': _acquisition_strategy_file, 'cwd': os.getcwd(), 'sys_path_head': sys.path[0] if sys.path else '', 'hledac_path_type': type(_hledac.__path__).__name__, 'hledac_path_entries': list(_hledac.__path__), 'profile_names': _profile_names, 'nonfeed_diagnostic180_present': _nonfeed_present, 'nonfeed_diagnostic180_duration': _nonfeed_duration, 'nonfeed_diagnostic180_acquisition_profile': _nonfeed_acquisition_profile, 'cwd_is_repo_parent': _repo_root_reality['cwd_is_repo_parent'], 'cwd_is_universal_root': _repo_root_reality['cwd_is_universal_root'], 'artifact_scan_root': _repo_root_reality['resolved_repo_root'], 'cwd_warning': _repo_root_reality['cwd_warning']}
_EXPECTED_REPO_ROOT = '/Users/vojtechhamada/PycharmProjects/Hledac'
_UNIVERSAL_ROOT = f'{_EXPECTED_REPO_ROOT}/hledac/universal'

def get_repo_root_reality() -> dict:
    """
    Return a hermetic diagnostic dict about the current working directory
    vs. the expected repo-root reality. Detects when operator tools are
    running from a temp/context sandbox CWD instead of actual repo root.

    No live run. No network. No MLX. No model load.
    """
    _cwd = os.getcwd()
    _resolved = str(_P(_cwd).resolve())
    _expected = _EXPECTED_REPO_ROOT
    _universal = _UNIVERSAL_ROOT
    _is_repo_parent = _resolved == _expected or _resolved.startswith(f'{_expected}/')
    _is_universal_root = _resolved == _universal or _resolved.startswith(f'{_universal}/')
    _universal_exists = _P(_universal).exists()
    _tests_probe_exists = _P(f'{_universal}/tests/probe_f223h_cwd_invocation_guard').exists()
    _artifact_scan_root = _universal
    _cwd_warning = ''
    if not _is_repo_parent:
        _cwd_warning = f'WARNING: CWD={_cwd} is outside expected repo root ({_expected}). Artifact scans may glob wrong directory. Use --repo-root {_universal} or run from {_universal}.'
    return {'cwd': _cwd, 'resolved_cwd': _resolved, 'resolved_repo_root': _artifact_scan_root, 'expected_repo_root': _expected, 'universal_root': _universal, 'is_actual_repo_root': _is_universal_root, 'cwd_is_repo_parent': _is_repo_parent, 'cwd_is_universal_root': _is_universal_root, 'universal_root_exists': _universal_exists, 'tests_probe_dir_exists': _tests_probe_exists, 'artifact_scan_root': _artifact_scan_root, 'cwd_warning': _cwd_warning}
from hledac.universal.benchmarks.live_measurement_schema import RunMode, MeasurementStatus, RunQualityVerdict, LiveMeasurementResult
from hledac.universal.benchmarks.live_measurement_quality import _MEMORY_GATE_OPERATOR_ACTION, _SWAP_GATE_THRESHOLD_GIB, _SWAP_GATE_OPERATOR_ACTION, _uma_state_is_critical_or_emergency, _is_active_domain_query, _has_terminal_source_outcomes, _has_scheduler_exit_path, _derive_run_quality_verdict as _quality_derive
_derive_run_quality_verdict = _quality_derive
READINESS_ARTIFACTS = {'stabilization_seal': Path(__file__).parent.parent / 'probe_f206an_stabilization' / 'stabilization_seal.json', 'hermetic_regression_manifest': Path(__file__).parent.parent / 'probe_f206aq_hermetic_regression' / 'hermetic_regression_manifest.json', 'transport_authority_status': Path(__file__).parent.parent / 'probe_transport_authority_f206bc' / 'transport_authority_status_refreshed.json', 'mlx_wired_limit_seal': Path(__file__).parent.parent / 'probe_f206ao_mlx_wired_limit' / 'mlx_wired_limit_seal.json'}

def _check_readiness_artifacts() -> dict[str, bool]:
    """Fail-soft check for readiness artifacts. Returns dict of artifact → present."""
    results = {}
    for name, path in READINESS_ARTIFACTS.items():
        results[name] = path.exists()
    return results

def _make_measurement_id() -> str:
    ts = time.time_ns() // 1000000
    uid = uuid.uuid4().hex[:6]
    return f'lsm_{ts}_{uid}'

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def _capture_uma() -> dict:
    """Capture UMA status. Fail-soft: returns None on error."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        s = sample_uma_status()
        return {'used_gib': round(s.system_used_gib, 3), 'swap_gib': round(s.swap_used_gib, 3), 'state': s.state}
    except Exception:
        return {'used_gib': None, 'swap_gib': None, 'state': None}

def _uma_state_is_critical_or_emergency(state: str | None) -> bool:
    """Alias to benchmarks.live_measurement_quality — delegates to extracted pure module."""
    return _qm._uma_state_is_critical_or_emergency(state)


def _is_active_domain_query(runtime_truth: dict | None, profile_verdict: str | None) -> bool:
    """Alias to benchmarks.live_measurement_quality — delegates to extracted pure module."""
    return _qm._is_active_domain_query(runtime_truth, profile_verdict)


def _has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool:
    """Alias to benchmarks.live_measurement_quality — delegates to extracted pure module."""
    return _qm._has_terminal_source_outcomes(acquisition_strategy)


def _has_scheduler_exit_path(scheduler_exit: dict | None) -> bool:
    """Alias to benchmarks.live_measurement_quality — delegates to extracted pure module."""
    return _qm._has_scheduler_exit_path(scheduler_exit)


def _derive_run_quality_verdict(
    status: MeasurementStatus,
    profile_verdict: str | None,
    uma_pre_state: str | None,
    runtime_truth: dict | None,
    swap_pre_gib: float | None,
    is_memory_gate_abort: bool = False,
    swap_gate_triggered: bool = False,
    acquisition_report: dict | None = None,
    acquisition_terminality_checked: bool | None = None,
    acquisition_terminality_satisfied: bool | None = None,
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None,
    acquisition_strategy: dict | None = None,
    scheduler_exit: dict | None = None,
    planned_duration_s: float | None = None,
    actual_duration_s: float | None = None,
) -> tuple[RunQualityVerdict | None, bool, str | None, bool, str | None, str | None]:
    """Alias to benchmarks.live_measurement_quality — delegates to extracted pure module."""
    return _quality_derive(
        status=status,
        profile_verdict=profile_verdict,
        uma_pre_state=uma_pre_state,
        runtime_truth=runtime_truth,
        swap_pre_gib=swap_pre_gib,
        is_memory_gate_abort=is_memory_gate_abort,
        swap_gate_triggered=swap_gate_triggered,
        acquisition_report=acquisition_report,
        acquisition_terminality_checked=acquisition_terminality_checked,
        acquisition_terminality_satisfied=acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=acquisition_terminality_missing_lanes,
        acquisition_strategy=acquisition_strategy,
        scheduler_exit=scheduler_exit,
        planned_duration_s=planned_duration_s,
        actual_duration_s=actual_duration_s,
    )

def _parse_sprint_report(report_path: str | None) -> dict | None:
    """
    Parse sprint JSON report for measurement metrics.

    F208C: Canonical acquisition report path checked FIRST.
    Legacy fallback paths preserved for backward compatibility.

    Canonical extraction strategy (F208C):
    1. acquisition_report (top-level key from build_acquisition_report()) — canonical
    2. runtime_truth (top-level dict) for cycles, accepted_findings, primary_signal_source
    3. timing_truth (top-level dict) for timing data
    4. canonical_run_summary for checkpoint_zero_category and any missing fields

    Fail-soft: returns partial dict if some fields are missing.
    """
    return _parse_sprint_report_impl(report_path)

def _get_profile_verdict(profile: str) -> tuple[bool, int | None, int | None, str]:
    """Derive profile truthfulness tuple from PROFILE_META. Returns (active_expected, windup, window, verdict)."""
    meta = PROFILE_META.get(profile, {})
    active_expected = meta.get('active_runtime_expected', False)
    windup_lead_s = meta.get('expected_windup_lead_s')
    active_window_s = meta.get('expected_active_window_s')
    verdict = 'ENTRY_SMOKE_ONLY' if not active_expected else 'ACTIVE_SPRINT'
    return (active_expected, windup_lead_s, active_window_s, verdict)

def _stamp_profile_meta(result: LiveMeasurementResult, profile: str) -> None:
    """Stamp profile truthfulness metadata onto result."""
    active_expected, windup_lead_s, active_window_s, verdict = _get_profile_verdict(profile)
    result.active_runtime_expected = active_expected
    result.expected_windup_lead_s = windup_lead_s
    result.expected_active_window_s = active_window_s
    result.profile_verdict = verdict

def _stamp_run_quality_verdict(result: LiveMeasurementResult, is_memory_gate_abort: bool=False) -> None:
    """Derive and stamp run quality verdict onto result."""
    verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next, operator_action = _derive_run_quality_verdict(status=result.status, profile_verdict=result.profile_verdict, uma_pre_state=result.uma_pre_state, runtime_truth=result.runtime_truth, swap_pre_gib=result.uma_pre_swap_gib, is_memory_gate_abort=is_memory_gate_abort, swap_gate_triggered=result.swap_gate_triggered or False, acquisition_report=result.acquisition_report, acquisition_terminality_checked=result.acquisition_terminality_checked, acquisition_terminality_satisfied=result.acquisition_terminality_satisfied, acquisition_terminality_missing_lanes=result.acquisition_terminality_missing_lanes, acquisition_strategy=result.acquisition_strategy, scheduler_exit=result.scheduler_exit, planned_duration_s=result.planned_duration_s, actual_duration_s=result.actual_duration_s)
    result.run_quality_verdict = verdict.value if verdict is not None else None
    result.hardware_constrained = hardware_constrained
    result.memory_state_pre = memory_state_pre
    result.memory_state_post = result.uma_post_state
    result.swap_warning = swap_warning
    result.recommended_next_profile = recommended_next
    result.recommended_operator_action = operator_action

# F230A: KPI derivation extracted to live_measurement_kpi.py
from benchmarks.live_measurement_kpi import (
    LiveKpiInput,
    _derive_live_kpi,
    _derive_live_kpi_from_input,
)

# F229A: next_action logic moved to benchmarks/live_measurement_next_action.py
from benchmarks.live_measurement_next_action import (
    NextActionInput,
    _derive_next_action,
    _was_family_attempted,
    _rule_wallclock_enforcement,
    _rule0b_memory_or_swap_gate,
    _rule0g_prewindup_barrier,
    _rule_profile_propagation,
    _rule_terminality,
    _rule_provider_surface,
    _rule_quality_gate,
    _rule_default,
)

# Alias for backward compatibility
_next_action_input = NextActionInput


def _stamp_live_kpi(result: LiveMeasurementResult) -> None:
    """Compute and stamp live_kpi onto result."""
    inp = LiveKpiInput(status=result.status, is_memory_gate_abort=result.run_quality_verdict == RunQualityVerdict.ABORTED_MEMORY_GATE.value, runtime_truth=result.runtime_truth, actual_duration_s=result.actual_duration_s, primary_signal_source=result.primary_signal_source, run_quality_verdict=result.run_quality_verdict, hardware_constrained=result.hardware_constrained, public_pipeline=result.public_pipeline, timing_truth=result.timing_truth, acquisition_strategy=result.acquisition_strategy, windup_guard_observation=getattr(result, 'windup_guard_observation', None), return_guard_observation=getattr(result, 'return_guard_observation', None), scheduler_exit=getattr(result, 'scheduler_exit', None), acquisition_report=result.acquisition_report, profile_verdict=result.profile_verdict, acquisition_terminality_checked=getattr(result, 'acquisition_terminality_checked', None), acquisition_terminality_satisfied=getattr(result, 'acquisition_terminality_satisfied', None), acquisition_terminality_missing_lanes=getattr(result, 'acquisition_terminality_missing_lanes', None), acquisition_terminality_report=getattr(result, 'acquisition_terminality_report', None), explicit_source_family_outcomes=result.acquisition_report.get('source_family_outcomes') if result.acquisition_report and isinstance(result.acquisition_report, dict) else None, acquisition_prelude_checked=getattr(result, 'acquisition_prelude_checked', None), acquisition_prelude_ran=getattr(result, 'acquisition_prelude_ran', None), acquisition_prelude_required_lanes=getattr(result, 'acquisition_prelude_required_lanes', None), acquisition_prelude_terminal_lanes=getattr(result, 'acquisition_prelude_terminal_lanes', None), acquisition_prelude_missing_lanes=getattr(result, 'acquisition_prelude_missing_lanes', None), acquisition_prelude_skipped_lanes=getattr(result, 'acquisition_prelude_skipped_lanes', None), acquisition_prelude_errors=getattr(result, 'acquisition_prelude_errors', None), acquisition_prelude_duration_s=getattr(result, 'acquisition_prelude_duration_s', None), acquisition_prelude_reason=getattr(result, 'acquisition_prelude_reason', None), planned_duration_s=getattr(result, 'planned_duration_s', None), claims_runtime_status=getattr(result, 'claims_runtime_status', None))
    kpi = _derive_live_kpi_from_input(inp)
    result.live_kpi = kpi
    from tools.research_quality_score import score_research_quality
    _rq_data = {'mode': 'live', 'findings_count': result.findings_count, 'runtime_truth': result.runtime_truth or {}, 'live_kpi': kpi, 'uma_post_swap_gib': result.uma_post_swap_gib, 'swap_warning': result.swap_warning}
    _rq = score_research_quality(_rq_data)
    result.live_kpi['research_quality'] = _rq
    result.live_kpi['quality_gate'] = _rq['quality_gate']
    result.live_kpi['research_quality_comparable'] = _rq['research_quality_comparable']

async def _run_preflight() -> LiveMeasurementResult:
    """
    Sample readiness/memory/profile metadata without calling run_sprint.
    Useful for checking whether Mac is ready after restart.
    """
    readiness = _check_readiness_artifacts()
    uma_pre = await _capture_uma()
    result = LiveMeasurementResult(measurement_id=_make_measurement_id(), sprint_id=None, mode=RunMode.PREFLIGHT, status=MeasurementStatus.PLANNED, start_time_iso=_now_iso(), end_time_iso=None, planned_duration_s=None, actual_duration_s=None, query='(preflight-only)', profile='preflight', uma_pre_used_gib=uma_pre.get('used_gib'), uma_pre_swap_gib=uma_pre.get('swap_gib'), uma_pre_state=uma_pre.get('state'), stabilization_seal_present=readiness.get('stabilization_seal', False), hermetic_regression_manifest_present=readiness.get('hermetic_regression_manifest', False), transport_authority_status_present=readiness.get('transport_authority_status', False), mlx_wired_limit_seal_present=readiness.get('mlx_wired_limit_seal', False), acquisition_profile=None)
    result.runtime_authority_path = 'dry_run_no_runtime'
    result.runtime_authority_module = None
    result.runtime_authority_function = None
    result.runtime_authority_is_canonical = False
    result.runtime_authority_evidence = {'mode': 'preflight'}
    is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
    if is_critical:
        result.status = MeasurementStatus.ABORTED
        result.error = f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — aborting live execution before sprint starts. Resolve memory pressure and retry."
        result.hardware_constrained = True
        result.comparable_result = False
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = result.uma_pre_swap_gib is not None and result.uma_pre_swap_gib > 0
        result.swap_gate_triggered = True
        result.swap_policy_tier = 'hard_block'
        result.swap_gate_reason = f'memory gate abort: uma_state={result.uma_pre_state}, swap={result.uma_pre_swap_gib}GiB'
        result.recommended_next_profile = 'none_until_memory_ok'
        result.recommended_operator_action = _MEMORY_GATE_OPERATOR_ACTION
        result.run_quality_verdict = RunQualityVerdict.ABORTED_MEMORY_GATE.value
        logging.warning('[PREFLIGHT] [MEMORY GATE] Memory critical: %s', result.uma_pre_state)
    else:
        result.hardware_constrained = False
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = uma_pre.get('swap_gib', 0) > 0
        swap_gib = uma_pre.get('swap_gib', 0) or 0
        result.swap_gate_triggered = False
        if swap_gib <= 2.0:
            result.swap_policy_tier = 'clean'
            result.swap_gate_reason = f'swap={swap_gib:.2f}GiB <= 2.0GiB threshold'
        elif swap_gib <= 4.0:
            result.swap_policy_tier = 'diagnostic'
            result.swap_gate_reason = f'swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB] — hardware taint'
        else:
            result.swap_policy_tier = 'hard_block'
            result.swap_gate_reason = f'swap={swap_gib:.2f}GiB > 4.0GiB — restart required'
        if swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
            result.swap_gate_triggered = True
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = 'high_swap'
            result.recommended_next_profile = 'smoke180 or active300_after_restart'
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning('[PREFLIGHT] [SWAP GATE] swap=%.1f GiB >= %.1f GiB threshold — hardware_constrained=True, comparable_result=False', swap_gib, _SWAP_GATE_THRESHOLD_GIB)
        else:
            logging.info('[PREFLIGHT] Memory state=%s — preflight OK', result.uma_pre_state)
    return result

async def _run_dry_run(query: str, profile: str, duration_s: int, aggressive_mode: bool, deep_probe: bool, require_memory_ok: bool=False, allow_high_swap: bool=False) -> LiveMeasurementResult:
    """Validate command construction without running sprint."""
    readiness = _check_readiness_artifacts()
    export_dir = str(Path.home() / '.hledac' / 'reports')
    planned_cmd = [sys.executable, '-m', 'hledac.universal.core', '--sprint', f'--query={query}', f'--duration={duration_s}', f'--export-dir={export_dir}']
    if aggressive_mode:
        planned_cmd.append('--aggressive')
    if deep_probe:
        planned_cmd.append('--deep-probe')
    uma_pre = await _capture_uma()
    result = LiveMeasurementResult(measurement_id=_make_measurement_id(), sprint_id=None, mode=RunMode.DRY_RUN, status=MeasurementStatus.PLANNED, start_time_iso=_now_iso(), end_time_iso=None, planned_duration_s=float(duration_s), actual_duration_s=None, query=query, profile=profile, aggressive_mode=aggressive_mode, deep_probe=deep_probe, uma_pre_used_gib=uma_pre.get('used_gib'), uma_pre_swap_gib=uma_pre.get('swap_gib'), uma_pre_state=uma_pre.get('state'), uma_post_used_gib=None, uma_post_swap_gib=None, uma_post_state=None, findings_count=None, cycles_completed=None, cycles_started=None, accepted_findings=None, runtime_truth=None, timing_truth=None, checkpoint_zero_category=None, primary_signal_source=None, error=None, stabilization_seal_present=readiness.get('stabilization_seal', False), hermetic_regression_manifest_present=readiness.get('hermetic_regression_manifest', False), transport_authority_status_present=readiness.get('transport_authority_status', False), mlx_wired_limit_seal_present=readiness.get('mlx_wired_limit_seal', False), acquisition_profile=os.environ.get('HLEDAC_ACQUISITION_PROFILE'))
    result.runtime_authority_path = 'dry_run_no_runtime'
    result.runtime_authority_module = None
    result.runtime_authority_function = None
    result.runtime_authority_is_canonical = False
    result.runtime_authority_evidence = {'mode': 'dry_run', 'planned_cmd': [sys.executable, '-m', 'hledac.universal.core', '--sprint']}
    _stamp_profile_meta(result, profile)
    if require_memory_ok:
        is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
        if is_critical:
            result.status = MeasurementStatus.ABORTED
            result.error = f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — requires ok/warn state for live execution. Use without --require-memory-ok or address memory pressure first."
            result.swap_gate_triggered = True
            result.swap_policy_tier = 'hard_block'
            result.swap_gate_reason = f'memory gate abort: uma_state={result.uma_pre_state}'
            _stamp_run_quality_verdict(result, is_memory_gate_abort=True)
            logging.error('[DRY-RUN] [MEMORY GATE] Aborted: %s', result.error)
            return result
        else:
            logging.info('[DRY-RUN] [MEMORY GATE] Pre-state=%s — memory OK for live execution', result.uma_pre_state)
    swap_gib = result.uma_pre_swap_gib or 0
    is_active_profile = profile in ('active300', 'active600')
    if swap_gib <= 2.0:
        result.swap_policy_tier = 'clean'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB <= 2.0GiB threshold'
    elif swap_gib <= 4.0:
        result.swap_policy_tier = 'diagnostic'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB]'
    else:
        result.swap_policy_tier = 'hard_block'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB > 4.0GiB — restart required'
    if is_active_profile and swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
        result.swap_gate_triggered = True
        if not allow_high_swap:
            result.status = MeasurementStatus.ABORTED
            result.comparable_result = False
            result.taint_reason = 'high_swap'
            result.error = f"[SWAP GATE] swap={swap_gib:.1f} GiB >= {_SWAP_GATE_THRESHOLD_GIB:.1f} GiB threshold for active profile '{profile}' — aborting dry-run. Restart to clear swap, or use --allow-high-swap to run anyway (results non-comparable)."
            result.hardware_constrained = True
            result.recommended_next_profile = 'smoke180 or active300_after_restart'
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            _stamp_profile_meta(result, profile)
            logging.error('[DRY-RUN] [SWAP GATE] Aborted: %s', result.error)
            return result
        else:
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = 'high_swap'
            result.recommended_next_profile = 'smoke180 or active300_after_restart'
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning('[DRY-RUN] [SWAP GATE] swap=%.1f GiB >= %.1f GiB — proceeding with --allow-high-swap (hardware_constrained=True, comparable_result=False)', swap_gib, _SWAP_GATE_THRESHOLD_GIB)
    logging.info('[DRY-RUN] Planned command: %s', ' '.join(planned_cmd))
    for name, present in readiness.items():
        status_str = 'PRESENT' if present else 'MISSING'
        logging.info('[DRY-RUN] Readiness artifact [%s]: %s', name, status_str)
    active_expected, windup_lead_s, active_window_s, verdict = _get_profile_verdict(profile)
    if not active_expected:
        logging.warning('[DRY-RUN] Profile %s is ENTRY_SMOKE_ONLY — no active runtime window. Use active300 (or active600) for meaningful active sprint measurement.', profile)
    else:
        logging.info('[DRY-RUN] Profile %s verdict=%s windup_lead=%ds active_window=%ds (active_runtime_expected=True)', profile, verdict, windup_lead_s, active_window_s)
    errors: list[str] = []
    if duration_s < MIN_DURATION_S:
        errors.append(f'Duration {duration_s}s < minimum {MIN_DURATION_S}s (use --allow-smoke to override)')
    if errors:
        result.status = MeasurementStatus.ABORTED
        result.error = '; '.join(errors)
    else:
        result.status = MeasurementStatus.PLANNED
        logging.info('[DRY-RUN] Validation PASSED — ready for live execution with --live flag')
    _stamp_run_quality_verdict(result, is_memory_gate_abort=False)
    _stamp_live_kpi(result)
    return result

async def _run_live_sprint(query: str, profile: str, duration_s: int, aggressive_mode: bool, deep_probe: bool, export_dir: str, require_memory_ok: bool=False, allow_high_swap: bool=False) -> LiveMeasurementResult:
    """Run canonical sprint and capture metrics."""
    import uuid
    measurement_id = _make_measurement_id()
    start_time_iso = _now_iso()
    start_ts = time.monotonic()
    ts = time.time_ns() // 1000000
    harness_sprint_id = f'8sa_{ts}_{uuid.uuid4().hex[:6]}'
    uma_pre = await _capture_uma()
    result = LiveMeasurementResult(measurement_id=measurement_id, sprint_id=None, mode=RunMode.LIVE, status=MeasurementStatus.RUNNING, start_time_iso=start_time_iso, end_time_iso=None, planned_duration_s=float(duration_s), actual_duration_s=None, query=query, profile=profile, aggressive_mode=aggressive_mode, deep_probe=deep_probe, uma_pre_used_gib=uma_pre.get('used_gib'), uma_pre_swap_gib=uma_pre.get('swap_gib'), uma_pre_state=uma_pre.get('state'), uma_post_used_gib=None, uma_post_swap_gib=None, uma_post_state=None, findings_count=None, cycles_completed=None, cycles_started=None, accepted_findings=None, runtime_truth=None, timing_truth=None, checkpoint_zero_category=None, primary_signal_source=None, error=None, stabilization_seal_present=READINESS_ARTIFACTS['stabilization_seal'].exists(), hermetic_regression_manifest_present=READINESS_ARTIFACTS['hermetic_regression_manifest'].exists(), transport_authority_status_present=READINESS_ARTIFACTS['transport_authority_status'].exists(), mlx_wired_limit_seal_present=READINESS_ARTIFACTS['mlx_wired_limit_seal'].exists(), acquisition_profile=os.environ.get('HLEDAC_ACQUISITION_PROFILE'))
    if require_memory_ok:
        is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
        if is_critical:
            result.status = MeasurementStatus.ABORTED
            result.end_time_iso = _now_iso()
            result.error = f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — aborting live execution before sprint starts. Resolve memory pressure and retry."
            _stamp_profile_meta(result, profile)
            _stamp_run_quality_verdict(result, is_memory_gate_abort=True)
            result.swap_gate_triggered = True
            result.swap_policy_tier = 'hard_block'
            result.swap_gate_reason = f'memory gate abort: uma_state={result.uma_pre_state}'
            logging.error('[LIVE] [MEMORY GATE] Aborted: %s', result.error)
            result.runtime_authority_path = 'canonical_core_run_sprint'
            result.runtime_authority_module = 'hledac.universal.core.__main__'
            result.runtime_authority_function = 'run_sprint'
            result.runtime_authority_is_canonical = None
            result.runtime_authority_evidence = {'sprint_id': harness_sprint_id, 'measurement_id': measurement_id, 'entry_via': 'benchmarks/live_sprint_measurement._run_live_sprint', 'aborted': True, 'abort_reason': 'memory_gate'}
            return result
    swap_gib = result.uma_pre_swap_gib or 0
    is_active_profile = profile in ('active300', 'active600')
    if swap_gib <= 2.0:
        result.swap_policy_tier = 'clean'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB <= 2.0GiB threshold'
    elif swap_gib <= 4.0:
        result.swap_policy_tier = 'diagnostic'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB]'
    else:
        result.swap_policy_tier = 'hard_block'
        result.swap_gate_reason = f'swap={swap_gib:.2f}GiB > 4.0GiB — restart required'
    if is_active_profile and swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
        result.swap_gate_triggered = True
        if not allow_high_swap:
            result.status = MeasurementStatus.ABORTED
            result.end_time_iso = _now_iso()
            result.comparable_result = False
            result.taint_reason = 'high_swap'
            result.error = f"[SWAP GATE] swap={swap_gib:.1f} GiB >= {_SWAP_GATE_THRESHOLD_GIB:.1f} GiB threshold for active profile '{profile}' — aborting. Restart to clear swap, or use --allow-high-swap to run anyway (results non-comparable)."
            result.hardware_constrained = True
            result.recommended_next_profile = 'smoke180 or active300_after_restart'
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            _stamp_profile_meta(result, profile)
            logging.error('[LIVE] [SWAP GATE] Aborted: %s', result.error)
            result.runtime_authority_path = 'canonical_core_run_sprint'
            result.runtime_authority_module = 'hledac.universal.core.__main__'
            result.runtime_authority_function = 'run_sprint'
            result.runtime_authority_is_canonical = None
            result.runtime_authority_evidence = {'sprint_id': harness_sprint_id, 'measurement_id': measurement_id, 'entry_via': 'benchmarks/live_sprint_measurement._run_live_sprint', 'aborted': True, 'abort_reason': 'swap_gate'}
            return result
        else:
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = 'high_swap'
            result.recommended_next_profile = 'smoke180 or active300_after_restart'
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning('[LIVE] [SWAP GATE] swap=%.1f GiB >= %.1f GiB — proceeding with --allow-high-swap (hardware_constrained=True, comparable_result=False)', swap_gib, _SWAP_GATE_THRESHOLD_GIB)
    result.sprint_id = harness_sprint_id
    _original_make_sprint_id = core_main._make_sprint_id
    _patched_sprint_ids = [harness_sprint_id]

    def _patched_make_sprint_id() -> str:
        return _patched_sprint_ids.pop(0) if _patched_sprint_ids else _original_make_sprint_id()
    core_main._make_sprint_id = _patched_make_sprint_id
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    result.core_run_sprint_module_file = str(_Path(core_main.__file__).resolve()) if hasattr(core_main, '__file__') else None
    result.core_run_sprint_function_qualname = 'run_sprint'
    result.sprint_scheduler_module_file = str(_Path(__file__).resolve().parent.parent / 'runtime' / 'sprint_scheduler.py')
    result.live_sprint_measurement_module_file = str(_Path(__file__).resolve())
    result.python_executable = _sys.executable
    result.runtime_cwd = _os.getcwd()
    result.sys_path_head = _sys.path[0] if _sys.path else None
    _core_main_path = _Path(core_main.__file__) if hasattr(core_main, '__file__') else None
    result.core_main_mtime = _core_main_path.stat().st_mtime if _core_main_path and _core_main_path.exists() else None
    result.sprint_scheduler_mtime = _Path(__file__).resolve().parent.parent.joinpath('runtime', 'sprint_scheduler.py').stat().st_mtime
    try:
        logging.info('[LIVE] Starting sprint measurement_id=%s sprint_id=%s profile=%s duration=%ds', measurement_id, harness_sprint_id, profile, duration_s)
        _windup_lead_s = PROFILE_META.get(profile, {}).get('expected_windup_lead_s')
        # F232: Resolve benchmark alias to canonical acquisition profile BEFORE setting
        # env or passing to run_sprint. The env var is a fallback read by
        # build_acquisition_plan — setting it to the raw alias would corrupt
        # NonfeedPlanDebug.acquisition_profile before run_sprint's own normalization.
        # F232: Also save/restore to prevent profile leakage across runs in the
        # same process (benchmark harness may run multiple measurements).
        _resolved_profile = _resolve_acquisition_profile(profile)
        _prior_env = os.environ.get('HLEDAC_ACQUISITION_PROFILE')
        os.environ['HLEDAC_ACQUISITION_PROFILE'] = _resolved_profile
        try:
            await core_main.run_sprint(query=query, duration_s=float(duration_s), export_dir=export_dir, aggressive_mode=aggressive_mode, deep_probe_enabled=deep_probe, ui_mode=False, windup_lead_s=_windup_lead_s, acquisition_profile=_resolved_profile)
        finally:
            # F232: Restore prior env to prevent profile leakage.
            # Uses finally=always-run so env is cleaned up even when run_sprint raises.
            if _prior_env is not None:
                os.environ['HLEDAC_ACQUISITION_PROFILE'] = _prior_env
            else:
                os.environ.pop('HLEDAC_ACQUISITION_PROFILE', None)
        end_time_iso = _now_iso()
        actual_duration_s = time.monotonic() - start_ts
        uma_post = await _capture_uma()
        result.uma_post_used_gib = uma_post.get('used_gib')
        result.uma_post_swap_gib = uma_post.get('swap_gib')
        result.uma_post_state = uma_post.get('state')
        result.end_time_iso = end_time_iso
        result.actual_duration_s = round(actual_duration_s, 1)
        result.status = MeasurementStatus.COMPLETED
        result.runtime_authority_path = 'canonical_core_run_sprint'
        result.runtime_authority_module = 'hledac.universal.core.__main__'
        result.runtime_authority_function = 'run_sprint'
        result.runtime_authority_is_canonical = True
        result.runtime_authority_evidence = {'sprint_id': harness_sprint_id, 'measurement_id': measurement_id, 'entry_via': 'benchmarks/live_sprint_measurement._run_live_sprint'}
        report_path = get_sprint_json_report_path(harness_sprint_id)
        if report_path.exists():
            result.report_json_path = str(report_path)
            parsed = _parse_sprint_report(str(report_path))
            if parsed:
                result.findings_count = parsed.get('findings_count')
                result.cycles_completed = parsed.get('cycles_completed')
                result.cycles_started = parsed.get('cycles_started')
                result.accepted_findings = parsed.get('accepted_findings')
                result.runtime_truth = parsed.get('runtime_truth')
                result.timing_truth = parsed.get('timing_truth')
                result.checkpoint_zero_category = parsed.get('checkpoint_zero_category')
                result.primary_signal_source = parsed.get('primary_signal_source')
                result.public_pipeline = parsed.get('public_pipeline')
                result.acquisition_strategy = parsed.get('acquisition_strategy')
                result.windup_guard_observation = parsed.get('windup_guard_observation')
                # P3: prewindup_barrier_checked propagates from parsed acquisition_strategy
                _as = result.acquisition_strategy or {}
                result.prewindup_barrier_checked = bool(_as.get('prewindup_barrier_checked', False))
                result.prewindup_barrier_satisfied = bool(_as.get('prewindup_barrier_satisfied', False))
                result.return_guard_observation = parsed.get('return_guard_observation')
                result.scheduler_exit = parsed.get('scheduler_exit')
                result.acquisition_terminality_checked = parsed.get('acquisition_terminality_checked')
                result.acquisition_terminality_satisfied = parsed.get('acquisition_terminality_satisfied')
                result.acquisition_terminality_missing_lanes = parsed.get('acquisition_terminality_missing_lanes')
                result.acquisition_terminality_report = parsed.get('acquisition_terminality_report')
                result.acquisition_prelude_checked = parsed.get('acquisition_prelude_checked')
                result.acquisition_prelude_ran = parsed.get('acquisition_prelude_ran')
                result.acquisition_prelude_required_lanes = parsed.get('acquisition_prelude_required_lanes')
                result.acquisition_prelude_terminal_lanes = parsed.get('acquisition_prelude_terminal_lanes')
                result.acquisition_prelude_missing_lanes = parsed.get('acquisition_prelude_missing_lanes')
                result.acquisition_prelude_skipped_lanes = parsed.get('acquisition_prelude_skipped_lanes')
                result.acquisition_prelude_errors = parsed.get('acquisition_prelude_errors')
                result.acquisition_prelude_duration_s = parsed.get('acquisition_prelude_duration_s')
                result.acquisition_prelude_reason = parsed.get('acquisition_prelude_reason')
                result.nonfeed_mission_active = parsed.get('nonfeed_mission_active')
                result.nonfeed_required_families = parsed.get('nonfeed_required_families')
                result.nonfeed_optional_families = parsed.get('nonfeed_optional_families')
                result.nonfeed_family_status = parsed.get('nonfeed_family_status')
                result.nonfeed_all_required_terminal = parsed.get('nonfeed_all_required_terminal')
                result.nonfeed_any_accepted = parsed.get('nonfeed_any_accepted')
                result.nonfeed_provider_failures = parsed.get('nonfeed_provider_failures')
                result.nonfeed_memory_skips = parsed.get('nonfeed_memory_skips')
                result.nonfeed_mission_exit_reason = parsed.get('nonfeed_mission_exit_reason')
                result.claims_runtime_status = parsed.get('claims_runtime_status')
                # F234: acquisition_report must be copied from parsed canonical report
                result.acquisition_report = parsed.get('acquisition_report')
                if result.acquisition_report and isinstance(result.acquisition_report, dict):
                    _ap_from_report = result.acquisition_report.get('acquisition_profile')
                    if _ap_from_report:
                        result.acquisition_profile = _ap_from_report
                if result.acquisition_report and isinstance(result.acquisition_report, dict):
                    result.nonfeed_priority_enabled = bool(result.acquisition_report.get('nonfeed_priority_enabled', False))
                    _lanes = result.acquisition_report.get('nonfeed_profile_expected_lanes', [])
                    result.nonfeed_profile_expected_lanes = tuple(_lanes) if _lanes else ()
        logging.info('[LIVE] Completed measurement_id=%s findings=%s cycles=%s duration=%.1fs', measurement_id, result.findings_count, result.cycles_completed, actual_duration_s)
        _stamp_profile_meta(result, profile)
    except Exception as exc:
        result.status = MeasurementStatus.FAILED
        result.end_time_iso = _now_iso()
        result.error = f'{type(exc).__name__}: {exc}'
        logging.error('[LIVE] Failed measurement_id=%s: %s', measurement_id, exc, exc_info=True)
        result.runtime_authority_path = 'canonical_core_run_sprint'
        result.runtime_authority_module = 'hledac.universal.core.__main__'
        result.runtime_authority_function = 'run_sprint'
        result.runtime_authority_is_canonical = True
        result.runtime_authority_evidence = {'sprint_id': harness_sprint_id, 'measurement_id': measurement_id, 'entry_via': 'benchmarks/live_sprint_measurement._run_live_sprint', 'failed': True}
        _stamp_profile_meta(result, profile)
    finally:
        core_main._make_sprint_id = _original_make_sprint_id
    _stamp_run_quality_verdict(result, is_memory_gate_abort=False)
    _stamp_live_kpi(result)
    return result

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='F206BH Live Sprint Measurement Harness', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='\nProfiles:\n  smoke180  180s sprint (smoke test)\n  active300 300s sprint (standard)\n  active600 600s sprint (extended)\n\nSafety:\n  Default is --dry-run (no live sprint execution).\n  Live execution requires explicit --live flag.\n  No stealth or aggressive mode by default.\n\nExamples:\n  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"\n  python benchmarks/live_sprint_measurement.py --profile nonfeed_diagnostic180 --query "mozilla.org certificate transparency subdomains" --live\n  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --live\n  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/measure.json\n  python benchmarks/live_sprint_measurement.py --print-preflight-only --output-json /tmp/preflight.json\n        ')
    parser.add_argument('--profile', type=str, choices=list(PROFILE_DURATION.keys()), default='active300', help='Measurement profile (determines duration). Default: active300')
    parser.add_argument('--query', type=str, required=False, help='Sprint query string')
    parser.add_argument('--duration', type=int, default=None, help='Override profile duration (seconds). Use with --allow-smoke for <180s')
    parser.add_argument('--aggressive', action='store_true', help='Enable aggressive mode (8s branch budgets, parallel branches)')
    parser.add_argument('--deep-probe', action='store_true', help='Enable deep probe research post-sprint')
    parser.add_argument('--live', action='store_true', help='Execute live sprint (default is --dry-run)')
    parser.add_argument('--dry-run', action='store_true', default=False, help='Validate command construction without running sprint (default)')
    parser.add_argument('--allow-smoke', action='store_true', help='Allow duration < 180s (smoke profile override)')
    parser.add_argument('--require-memory-ok', action='store_true', help='Abort if UMA pre-state is critical/emergency before live execution. Dry-run reports the gate; live execution aborts before sprint starts.')
    parser.add_argument('--allow-high-swap', action='store_true', help='Allow active300/active600 run even when swap >= 3 GiB. Results will be marked non-comparable (hardware_constrained=True) but will proceed. Use after restart to clear swap.')
    parser.add_argument('--print-preflight-only', action='store_true', help='Sample readiness/memory/profile metadata without running sprint. Never calls run_sprint. Useful for checking readiness after restart.')
    parser.add_argument('--print-invocation-reality', action='store_true', help='F222B: Print namespace/path reality diagnostic and exit. Never runs live sprint or instantiates SprintScheduler.')
    parser.add_argument('--output-json', type=str, default=None, help='Path to write JSON measurement result')
    parser.add_argument('--output-md', type=str, default=None, help='Path to write markdown summary')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('--require-one-button-gate', type=str, default=None,
                        help='F232B: Path to one-button gate JSON. If set, live runs are skipped when investigation_admission.can_run_live_acquisition is false, writing status=skipped/preflight_blocked instead.')
    return parser

def _render_md(result: LiveMeasurementResult) -> str:
    """Render measurement result as markdown. Delegates to extracted module."""
    from hledac.universal.benchmarks.live_measurement_markdown import render_live_measurement_markdown
    return render_live_measurement_markdown(result)

async def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%H:%M:%S')
    if args.print_invocation_reality:
        reality = get_invocation_reality()
        print(json.dumps(reality, indent=2))
        return 0
    if args.print_preflight_only:
        result = await _run_preflight()
        if args.output_json:
            out_path = Path(args.output_json).resolve()
            result.resolved_output_json = str(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'w') as f:
                f.write(result.to_json())
            logging.info('JSON result written to %s', out_path)
        if args.output_md:
            md_path = Path(args.output_md).resolve()
            result.resolved_output_md = str(md_path)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(md_path, 'w') as f:
                f.write(_render_md(result))
            logging.info('Markdown summary written to %s', md_path)
        print(f'[PREFLIGHT] measurement_id={result.measurement_id} status={result.status.value}')
        print(f'  verdict={result.run_quality_verdict}')
        print(f'  uma_pre_state={result.uma_pre_state} uma_pre_used={result.uma_pre_used_gib} GiB')
        print(f'  uma_pre_swap={result.uma_pre_swap_gib} GiB')
        if result.error:
            print(f'  ERROR: {result.error}')
        if result.recommended_operator_action:
            print(f'  OPERATOR ACTION: {result.recommended_operator_action}')
        if result.status == MeasurementStatus.ABORTED:
            return 2
        return 0
    if not args.query:
        logging.error('--query is required (use --print-preflight-only for preflight check without query)')
        return 1
    duration_s = args.duration or PROFILE_DURATION[args.profile]
    if duration_s < MIN_DURATION_S and (not args.allow_smoke):
        logging.error('Duration %ds < minimum %ds. Pass --allow-smoke to override.', duration_s, MIN_DURATION_S)
        return 1
    is_live = args.live and (not args.dry_run)
    mode_str = 'LIVE' if is_live else 'DRY-RUN'
    logging.info('[%s] Profile=%s duration=%ds query=%r aggressive=%s', mode_str, args.profile, duration_s, args.query, args.aggressive)

    # F232B: Check one-button gate before live execution
    gate_blocked = False
    gate_block_reason = ""
    if args.require_one_button_gate and is_live:
        gate_path = Path(args.require_one_button_gate).resolve()
        if gate_path.exists():
            with open(gate_path) as f:
                gate_data = json.load(f)
            inv_adm = gate_data.get("investigation_admission", {})
            if not inv_adm.get("can_run_live_acquisition", True):
                gate_blocked = True
                gate_block_reason = inv_adm.get("reason", "unknown")
                logging.warning('[GATE] Live acquisition blocked by one-button gate: %s', gate_block_reason)
        else:
            logging.warning('[GATE] Gate file not found: %s', gate_path)

    if gate_blocked:
        result = LiveMeasurementResult(
            measurement_id=_make_measurement_id(),
            sprint_id=None,
            mode=RunMode.LIVE,
            status=MeasurementStatus.ABORTED,
            start_time_iso=_now_iso(),
            end_time_iso=_now_iso(),
            planned_duration_s=float(duration_s),
            actual_duration_s=0.0,
            query=args.query,
            profile=args.profile,
            uma_pre_used_gib=0.0,
            uma_pre_swap_gib=0.0,
            uma_pre_state="unknown",
            error=f"preflight_blocked: {gate_block_reason}",
        )
        result.run_quality_verdict = "PREFLIGHT_GATE_BLOCKED"
        if args.output_json:
            out_path = Path(args.output_json).resolve()
            result.resolved_output_json = str(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'w') as f:
                f.write(result.to_json())
            logging.info('JSON result written to %s', out_path)
        if args.output_md:
            md_path = Path(args.output_md).resolve()
            result.resolved_output_md = str(md_path)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(md_path, 'w') as f:
                f.write(_render_md(result))
            logging.info('Markdown summary written to %s', md_path)
        print(f'[LIVE GATE BLOCKED] measurement_id={result.measurement_id} status={result.status.value}')
        print(f'  reason={gate_block_reason}')
        print(f'  gate={gate_path}')
        return 2
    if is_live:
        export_dir = str(Path.home() / '.hledac' / 'reports')
        result = await _run_live_sprint(query=args.query, profile=args.profile, duration_s=duration_s, aggressive_mode=args.aggressive, deep_probe=args.deep_probe, export_dir=export_dir, require_memory_ok=args.require_memory_ok, allow_high_swap=args.allow_high_swap)
    else:
        result = await _run_dry_run(query=args.query, profile=args.profile, duration_s=duration_s, aggressive_mode=args.aggressive, deep_probe=args.deep_probe, require_memory_ok=args.require_memory_ok, allow_high_swap=args.allow_high_swap)
    if args.output_json:
        out_path = Path(args.output_json).resolve()
        result.resolved_output_json = str(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w') as f:
            f.write(result.to_json())
        logging.info('JSON result written to %s', out_path)
    if args.output_md:
        md_path = Path(args.output_md).resolve()
        result.resolved_output_md = str(md_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, 'w') as f:
            f.write(_render_md(result))
        logging.info('Markdown summary written to %s', md_path)
    print(f'[{mode_str}] measurement_id={result.measurement_id} status={result.status.value}')
    print(f'  verdict={result.run_quality_verdict}')
    if result.error:
        print(f'  ERROR: {result.error}')
    elif result.status == MeasurementStatus.PLANNED:
        print(f'  Validated — ready for live execution. Use --live to run sprint.')
    if result.status in (MeasurementStatus.COMPLETED, MeasurementStatus.PLANNED):
        return 0
    elif result.status == MeasurementStatus.ABORTED:
        return 2
    else:
        return 1
if __name__ == '__main__':
    sys.exit(asyncio.run(main()))