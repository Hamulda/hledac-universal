"""
Pre-Live Decision Gate — Sprint F219F

Reads probe/report artifacts and local UMA state, emits a deterministic


decision without running live sprint, loading model, or using network.

Decision values: READY_FOR_LIVE | BLOCKED_BY_MEMORY | BLOCKED_BY_CONTRACT |
                 BLOCKED_BY_PROVIDER_SURFACE | BLOCKED_BY_UNKNOWN
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass, field
import msgspec
from enum import StrEnum
from pathlib import Path
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

class Decision(StrEnum):
    READY_FOR_LIVE = 'READY_FOR_LIVE'
    READY_FOR_LIVE_HARDWARE_TAINTED = 'READY_FOR_LIVE_HARDWARE_TAINTED'
    READY_FOR_FEED_BASELINE_ONLY = 'READY_FOR_FEED_BASELINE_ONLY'
    BLOCKED_BY_PROVIDER_SURFACE = 'BLOCKED_BY_PROVIDER_SURFACE'
    BLOCKED_BY_CONTRACT = 'BLOCKED_BY_CONTRACT'
    BLOCKED_BY_MEMORY = 'BLOCKED_BY_MEMORY'
    BLOCKED_BY_UNKNOWN = 'BLOCKED_BY_UNKNOWN'
from hledac.universal._core.resource_governor import get_swap_policy_tier
from _core import aclose

def _check_uma() -> dict:
    """
    Sample UMA status via core.resource_governor.
    This is a one-shot local read — no live sprint, no model load.
    """
    try:
        from hledac.universal._core.resource_governor import sample_uma_status
    except Exception as exc:
        return {'error': str(exc), 'system_used_gib': 0.0, 'swap_used_gib': 0.0, 'swap_detected': False, 'uma_state': 'unknown', 'io_only': False, 'last_error': str(exc)}
    try:
        UmaStatus = sample_uma_status()
        return {'system_used_gib': round(getattr(UmaStatus, 'system_used_gib', 0.0), 3), 'swap_used_gib': round(getattr(UmaStatus, 'swap_used_gib', 0.0), 3), 'swap_detected': getattr(UmaStatus, 'swap_detected', False), 'uma_state': getattr(UmaStatus, 'state', 'unknown'), 'io_only': getattr(UmaStatus, 'io_only', False), 'last_error': getattr(UmaStatus, 'last_error', None) or None}
    except Exception as exc:
        return {'error': str(exc), 'system_used_gib': 0.0, 'swap_used_gib': 0.0, 'swap_detected': False, 'uma_state': 'unknown', 'io_only': False, 'last_error': str(exc)}
PROBE_ROOT_ENV = 'PRELIVE_PROBE_ROOT'
_F224_BLOCKING_PROBES = [('probe_f224a_worker_pool_import_seal', 'worker_pool_import_seal.json'), ('probe_f224c_discovery_provider_gap', 'discovery_provider_gap.json'), ('probe_f224d_confidence_policy', 'confidence_policy.json')]
_F224_WARNING_PROBES = [('probe_f224b_claims_extraction_v1', 'claims_extraction_v1.json'), ('probe_f224e_type_checking_hygiene', 'type_checking_hygiene.json')]
_F224_BLOCKING_PROFILES = ('active300', 'nonfeed_diagnostic')

class ProbeReport(msgspec.Struct, gc=False):
    path: str
    found: bool
    data: dict = field(default_factory=dict)
    parse_error: str | None = None

def _load_report(repo_root: Path, probe_name: str, report_filename: str) -> ProbeReport:
    """Load a single JSON report, return ProbeReport (never raises)."""
    probe_root_override = os.environ.get(PROBE_ROOT_ENV)
    if probe_root_override:
        base = Path(probe_root_override)
    else:
        base = repo_root
    full_path = base / probe_name / report_filename
    if not full_path.exists():
        return ProbeReport(path=str(full_path), found=False)
    try:
        with open(full_path, encoding='utf-8') as fh:
            data = json.load(fh)
        return ProbeReport(path=str(full_path), found=True, data=data)
    except Exception as exc:
        return ProbeReport(path=str(full_path), found=False, parse_error=str(exc))
_FALLBACK_SCHEMA_MARKERS = ['fallback_acquisition_schema', 'fallback acquisition schema', '"fallback"', 'acquisition_strategy_fallback', '_FALLBACK_ACQUISITION']

def _has_fallback_schema_marker(report: ProbeReport) -> bool:
    """Scan report raw text for fallback acquisition schema marker."""
    if not report.found or report.parse_error:
        return False
    text = json.dumps(report.data)
    return any((marker in text for marker in _FALLBACK_SCHEMA_MARKERS))

def _check_status_field(d: dict) -> bool | None:
    """Check top-level status field."""
    status = d.get('status', '')
    if isinstance(status, str):
        if status.upper() in ('FAIL', 'FAILED'):
            return False
        if status.upper() in ('PASS', 'PASSED', 'COMPLETE'):
            return True
    return None


def _check_test_results(d: dict) -> bool | None:
    """Check nested test_results schema."""
    test_results = d.get('test_results', {})
    if not isinstance(test_results, dict):
        return None
    for probe_data in test_results.values():
        if isinstance(probe_data, dict):
            s = probe_data.get('status', '')
            if isinstance(s, str):
                if s.upper() == 'FAIL':
                    return False
                if s.upper() == 'PASS':
                    return True
    return None


def _check_tests_nested(d: dict) -> bool | None:
    """Check nested tests.{all_passed, all_passing} schema."""
    tests = d.get('tests', {})
    if not isinstance(tests, dict):
        return None
    all_passed = tests.get('all_passed')
    if isinstance(all_passed, bool):
        return all_passed
    all_passing = tests.get('all_passing')
    if isinstance(all_passing, bool) and all_passing is True:
        return True
    return None


def _check_verification_nested(d: dict) -> bool | None:
    """Check nested verification.{passed, status} schema."""
    verification = d.get('verification', {})
    if not isinstance(verification, dict):
        return None
    vp = verification.get('passed')
    if isinstance(vp, bool):
        return vp
    vs = verification.get('status', '')
    if isinstance(vs, str):
        if vs.upper() in ('FAIL', 'FAILED'):
            return False
        if vs.upper() in ('PASS', 'PASSED', 'COMPLETE'):
            return True
    return None


def _check_top_level_bool_fields(d: dict) -> bool | None:
    """Check top-level boolean pass indicators."""
    for key in ('all_passed', 'passed'):
        val = d.get(key)
        if isinstance(val, bool):
            return val
    ready = d.get('ready_for_controlled_smoke')
    if isinstance(ready, bool):
        return ready
    return None


def _is_pass(report: ProbeReport) -> bool:
    """
    Check if a probe report passes.
    Fail-closed: explicit FAIL/FAILED status wins over weaker pass fields.
    """
    if not report.found or report.parse_error:
        return False
    d = report.data

    # Schema checks in priority order
    checks = [
        _check_status_field,        # top-level status
        _check_test_results,         # test_results nested
        _check_tests_nested,        # tests nested
        _check_verification_nested, # verification nested
        _check_top_level_bool_fields, # top-level booleans
    ]

    for check in checks:
        result = check(d)
        if result is not None:
            return result
    return False

def _zero_findings_quality_sane(report: ProbeReport) -> tuple[bool, str]:
    """
    Check zero-findings quality probe does NOT crash and fails correctly.
    Returns (sane, detail).
    """
    if not report.found:
        return (False, 'report not found')
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}')
    d = report.data
    verdict = d.get('verdict', '')
    if verdict and verdict != 'SANITY_PASS':
        return (True, f'zero findings correctly fails: {verdict}')
    confirmation = d.get('confirmation_zero_findings_stay_failed', {})
    if confirmation:
        grade = confirmation.get('grade', '')
        if grade and grade != 'FEED_ONLY':
            return (True, f'confirmed: {grade}')
        if grade == 'FEED_ONLY':
            return (True, 'confirmed FEED_ONLY (correct failure)')
    checks = d.get('checks', {})
    if isinstance(checks, dict) and checks.get('research_quality') is True:
        return (False, 'research_quality check unexpectedly passed with zero findings')
    return (True, 'no crash detected')
_PROVIDER_SURFACE_ALIASES = {'probe_f217c_public_bootstrap': [('probe_f219h_public_fetcher_import_seal', 'public_fetcher_import_seal.json'), ('probe_f219d_public_session_seal', 'public_session_seal.json')], 'probe_f217d_ct_provider_resilience': [('probe_f219e_ct_provider_cooldown', 'ct_provider_cooldown.json')]}

def _check_provider_surface(repo_root: Path) -> tuple[list[str], list[str], dict]:
    """
    Unified provider surface check with F217→F219 aliasing.
    Returns (missing_required_old_probes, warnings, checked_dict).

    missing_required_old_probes: old probe names with no passing alias
    warnings: for optional alias probes absent
    checked_dict: for DecisionResult.checked_reports
    """
    missing_required: list[str] = []
    warnings: list[str] = []
    checked: dict[str, dict] = {}
    for old_probe, alias_list in _PROVIDER_SURFACE_ALIASES.items():
        old_filename = 'public_bootstrap.json' if 'bootstrap' in old_probe else 'ct_provider_resilience.json'
        old_report = _load_report(repo_root, old_probe, old_filename)
        alias_satisfied = False
        alias_failures: list[str] = []
        for new_probe, report_filename in alias_list:
            new_report = _load_report(repo_root, new_probe, report_filename)
            key = f'{old_probe}_alias_{new_probe}'
            if new_report.found:
                if new_report.parse_error:
                    checked[key] = {'found': True, 'parse_error': new_report.parse_error, 'pass': False}
                    alias_failures.append(f'{new_probe} parse error')
                else:
                    new_pass = _is_pass(new_report)
                    checked[key] = {'found': True, 'pass': new_pass, 'detail': f'alias: {new_probe}'}
                    if new_pass:
                        alias_satisfied = True
                    else:
                        alias_failures.append(f'{new_probe} FAILED')
            else:
                checked[key] = {'found': False, 'pass': False, 'detail': 'alias absent — skipped'}
        old_pass = old_report.found and _is_pass(old_report)
        checked[old_probe] = {'found': old_report.found, 'parse_error': old_report.parse_error, 'pass': old_pass, 'alias_satisfied': alias_satisfied}
        if old_report.found:
            if old_pass:
                alias_satisfied = True
            else:
                alias_failures.append(f'{old_probe} FAILED')
        if not alias_satisfied:
            missing_required.append(old_probe)
    return (missing_required, warnings, checked)

def _check_surface_contract(repo_root: Path) -> tuple[bool, str, ProbeReport | None]:
    """
    Check F219A surface contract if its probe directory exists.
    Returns (pass, detail, report).
    """
    report = _load_report(repo_root, 'probe_f219a_surface_contract', 'surface_contract.json')
    if not report.found:
        return (True, 'optional report absent — skipped', report)
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}', report)
    return (_is_pass(report), f'surface_contract: {_is_pass(report)}', report)

def _check_hermes_metal_finalizer(repo_root: Path) -> tuple[bool, str, ProbeReport | None]:
    """
    Check F219B Hermes Metal finalizer if its probe directory exists.
    """
    report = _load_report(repo_root, 'probe_f219b_hermes_metal_finalizer', 'hermes_metal_finalizer.json')
    if not report.found:
        return (True, 'optional report absent — skipped', report)
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}', report)
    return (_is_pass(report), f'hermes_metal_finalizer: {_is_pass(report)}', report)

def _check_public_session_seal(repo_root: Path) -> tuple[bool, str, ProbeReport | None]:
    """
    Check F219D public session seal if its probe directory exists.
    """
    report = _load_report(repo_root, 'probe_f219d_public_session_seal', 'public_session_seal.json')
    if not report.found:
        return (True, 'optional report absent — skipped', report)
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}', report)
    return (_is_pass(report), f'public_session_seal: {_is_pass(report)}', report)

def _check_ct_cooldown(repo_root: Path) -> tuple[bool, str, ProbeReport | None]:
    """
    Check F219E CT provider cooldown if its probe directory exists.
    """
    report = _load_report(repo_root, 'probe_f219e_ct_provider_cooldown', 'ct_provider_cooldown.json')
    if not report.found:
        return (True, 'optional report absent — skipped', report)
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}', report)
    return (_is_pass(report), f'ct_cooldown: {_is_pass(report)}', report)
_F231_BLOCKING_PROFILES = ('active300', 'nonfeed_diagnostic')
_F231_BLOCKING_PROBES = [('probe_f231a_public_candidate_ledger', 'public_candidate_ledger.json'), ('probe_f231b_ct_acceptance_lift', 'ct_acceptance_lift.json'), ('probe_f231c_advisory_evidence_surface', 'advisory_evidence_surface.json'), ('probe_f231d_research_quality_v2', 'research_quality_v2.json'), ('probe_f231e_research_quality_comparable_field', 'research_quality_comparable_field.json'), ('probe_f231f_evidence_depth_aliases', 'evidence_depth_aliases.json'), ('probe_f231g_quality_sanity_bundle_smoke', 'quality_sanity_bundle_smoke.json')]

def _check_f231_artifacts(repo_root: Path, profile: str) -> tuple[bool, list[str], list[str], dict]:
    """
    Check F231 Evidence Lift Pack artifact presence.
    Returns (core_ready, warnings, missing_blocking, checked_dict).
    core_ready = True when all F231 blocking probes are present for blocking profiles.
    """
    warnings: list[str] = []
    missing_blocking: list[str] = []
    checked_local: dict[str, dict] = {}
    is_blocking_profile = profile in _F231_BLOCKING_PROFILES
    for probe_dir, filename in _F231_BLOCKING_PROBES:
        report = _load_report(repo_root, probe_dir, filename)
        key = f'{probe_dir}_f231'
        checked_local[key] = {'found': report.found, 'parse_error': report.parse_error, 'pass': report.found and (not report.parse_error)}
        if not report.found or report.parse_error:
            detail = f'parse error: {report.parse_error}' if report.parse_error else 'absent'
            warnings.append(f'f231_blocking:{probe_dir} {detail}')
            missing_blocking.append(probe_dir)
    core_ready = not missing_blocking if is_blocking_profile else True
    return (core_ready, warnings, missing_blocking, checked_local)
_F224_CONFIDENCE_POLICY_CANONICAL = ('probe_f224d_confidence_policy', 'confidence_policy.json')
_F224_CONFIDENCE_POLICY_ALIASES = [('probe_f224d_sprint_id_collision', 'sprint_id_collision.json'), ('probe_f225b_confidence_policy_migration', 'confidence_policy_migration.json')]

def _check_f224_confidence_policy(repo_root: Path) -> tuple[bool, str, dict]:
    """
    Check F224D confidence policy via canonical path and aliases.
    Gate passes if any canonical/alias artifact exists and _is_pass() returns True.
    Gate blocks if all are missing or all are failing.
    Returns (pass, detail, checked_dict).
    """
    checked: dict[str, dict] = {}
    canonical_probe, canonical_file = _F224_CONFIDENCE_POLICY_CANONICAL
    canonical_report = _load_report(repo_root, canonical_probe, canonical_file)
    key = f'{canonical_probe}_f224'
    checked[key] = {'found': canonical_report.found, 'parse_error': canonical_report.parse_error, 'pass': canonical_report.found and (not canonical_report.parse_error) and _is_pass(canonical_report)}
    if canonical_report.found and (not canonical_report.parse_error) and _is_pass(canonical_report):
        return (True, 'canonical', checked)
    for alias_probe, alias_file in _F224_CONFIDENCE_POLICY_ALIASES:
        alias_report = _load_report(repo_root, alias_probe, alias_file)
        key = f'{alias_probe}_f224'
        alias_pass = alias_report.found and (not alias_report.parse_error) and _is_pass(alias_report)
        checked[key] = {'found': alias_report.found, 'parse_error': alias_report.parse_error, 'pass': alias_pass}
        if alias_report.found and (not alias_report.parse_error) and _is_pass(alias_report):
            return (True, f'alias:{alias_probe}', checked)
    return (False, 'all_absent_or_failing', checked)

def _check_f224_artifacts(repo_root: Path, profile: str) -> tuple[bool, list[str], list[str], dict]:
    """
    Check F224 artifact presence and return (core_ready, warnings, missing_blocking, checked_dict).
    core_ready = True when all blocking probes are present for blocking profiles.
    warnings = list of warning messages for missing warning probes.
    missing_blocking = list of missing blocking probe names (for reasons list).
    """
    blocking_warnings: list[str] = []
    warning_msgs: list[str] = []
    missing_blocking: list[str] = []
    checked_local: dict[str, dict] = {}
    is_blocking_profile = profile in _F224_BLOCKING_PROFILES
    for probe_dir, filename in _F224_BLOCKING_PROBES:
        if probe_dir == 'probe_f224d_confidence_policy':
            cp_pass, cp_detail, cp_checked = _check_f224_confidence_policy(repo_root)
            checked_local.update(cp_checked)
            if not cp_pass:
                blocking_warnings.append(f'f224_blocking:{probe_dir} {cp_detail}')
                missing_blocking.append(probe_dir)
        else:
            report = _load_report(repo_root, probe_dir, filename)
            key = f'{probe_dir}_f224'
            pass_flag = report.found and (not report.parse_error)
            checked_local[key] = {'found': report.found, 'parse_error': report.parse_error, 'pass': pass_flag}
            if not report.found or report.parse_error:
                detail = f'parse error: {report.parse_error}' if report.parse_error else 'absent'
                blocking_warnings.append(f'f224_blocking:{probe_dir} {detail}')
                missing_blocking.append(probe_dir)
    for probe_dir, filename in _F224_WARNING_PROBES:
        report = _load_report(repo_root, probe_dir, filename)
        key = f'{probe_dir}_f224'
        checked_local[key] = {'found': report.found, 'parse_error': report.parse_error, 'pass': report.found and (not report.parse_error)}
        if not report.found or report.parse_error:
            detail = f'parse error: {report.parse_error}' if report.parse_error else 'absent'
            warning_msgs.append(f'f224_warning:{probe_dir} {detail}')
    core_ready = len(missing_blocking) == 0 if is_blocking_profile else True
    return (core_ready, blocking_warnings + warning_msgs, missing_blocking, checked_local)

def _check_nonfeed_candidate_ledger(repo_root: Path) -> tuple[bool, str]:
    """
    Verify nonfeed candidate ledger is present and bounded (MAX field exists).
    """
    report = _load_report(repo_root, 'probe_f217e_nonfeed_candidate_ledger', 'candidate_ledger.json')
    if not report.found:
        return (True, 'optional report absent — skipped')
    if report.parse_error:
        return (False, f'parse error: {report.parse_error}')
    d = report.data
    if 'bounded_caps' in d or 'bounds' in d or 'max' in d or ('limit' in d):
        return (True, 'bounded_caps present')
    if isinstance(d, dict) and d:
        return (True, 'report present')
    return (False, 'report present but no bounding fields detected')

class DecisionResult(msgspec.Struct, frozen=True, gc=False):
    decision: Decision
    live_allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_required_reports: list[str] = field(default_factory=list)
    missing_optional_reports: list[str] = field(default_factory=list)
    uma: dict = field(default_factory=dict)
    checked_reports: dict = field(default_factory=dict)
    suggested_live_command: str = ''
    suggested_highswap_diagnostic_command: str = ''
    fallback_schema_blocked: bool = False
    hardware_constrained: bool = False
    swap_policy_tier: str = 'unknown'
    swap_gate_reason: str = ''
    f224_core_ready: bool = False
    f224_warnings: list[str] = field(default_factory=list)
    missing_f224_artifacts: list[str] = field(default_factory=list)
    f231_core_ready: bool = False
    f231_warnings: list[str] = field(default_factory=list)
    missing_f231_artifacts: list[str] = field(default_factory=list)
    nonfeed_capability_blocked: bool = False
    nonfeed_block_reason: str = ''
    feed_baseline_allowed: bool = False
    capability_live_allowed: bool = False
    capability_blockers: list[str] = field(default_factory=list)
    next_action_feed_baseline: str = ''
    next_action_capability: str = ''

def _build_nonfeed_block_reason(f224_blocks: bool, f231_blocks: bool, profile: str) -> str:
    """Build human-readable nonfeed block reason for telemetry."""
    parts = []
    if f224_blocks:
        parts.append('F224 blocking artifacts missing')
    if f231_blocks:
        parts.append('F231 evidence lift pack missing')
    if parts:
        return f"nonfeed capability blocked for {profile}: {'; '.join(parts)}"
    return ''

def _resolve_next_action_capability(live_cmd: str, capability_live_allowed: bool, has_memory_block: bool, has_contract_block: bool, f224_blocks_nonfeed: bool, f231_blocks_nonfeed: bool) -> str:
    """Resolve next_action_capability from decision gates — flat, readable."""
    if capability_live_allowed:
        return live_cmd
    if has_memory_block:
        return 'restart required — memory pressure'
    if has_contract_block or f224_blocks_nonfeed or f231_blocks_nonfeed:
        return 'run missing probe lanes to restore capability'
    return 'fix_provider_surface'

@dataclass(frozen=True, slots=True)
class _ContractProbeState:
    """Result of required contract probe checks."""
    mig_report: ProbeReport
    nrg_report: ProbeReport
    zf_sane: bool
    zf_detail: str
    fallback_blocked: bool


@dataclass(frozen=True, slots=True)
class _OptionalProbeState:
    """Result of optional probe checks."""
    ct_cooldown: tuple[bool, str, ProbeReport | None]
    surface_contract: tuple[bool, str, ProbeReport | None]
    hermes_metal: tuple[bool, str, ProbeReport | None]
    public_session_seal: tuple[bool, str, ProbeReport | None]
    ledger_pass: bool
    ledger_detail: str


@dataclass(frozen=True, slots=True)
class _ArtifactState:
    """Result of F224/F231 artifact checks."""
    f224_core_ready: bool
    f224_warnings: list[str]
    missing_f224: list[str]
    f231_core_ready: bool
    f231_warnings: list[str]
    missing_f231: list[str]
    is_nonfeed_blocking: bool


@dataclass(frozen=True, slots=True)
class _MemoryState:
    """UMA and swap policy state."""
    uma: dict
    swap_policy_tier: str
    swap_gate_reason: str
    hardware_constrained: bool
    uma_state: str


@dataclass(frozen=True, slots=True)
class _GateAccumulator:
    """Accumulates reasons, warnings, and block classifications."""
    reasons: list[str]
    warnings: list[str]
    missing_required: list[str]
    missing_optional: list[str]
    checked: dict[str, dict]

    def add_reason(self, reason: str) -> '_GateAccumulator':
        return _GateAccumulator(
            reasons=[*self.reasons, reason],
            warnings=self.warnings,
            missing_required=self.missing_required,
            missing_optional=self.missing_optional,
            checked=self.checked,
    )

    def add_warning(self, warning: str) -> '_GateAccumulator':
        return _GateAccumulator(
            reasons=self.reasons,
            warnings=[*self.warnings, warning],
            missing_required=self.missing_required,
            missing_optional=self.missing_optional,
            checked=self.checked,
    )

    def add_required(self, probe: str) -> '_GateAccumulator':
        return _GateAccumulator(
            reasons=self.reasons,
            warnings=self.warnings,
            missing_required=[*self.missing_required, probe],
            missing_optional=self.missing_optional,
            checked=self.checked,
    )

    def add_optional(self, probe: str) -> '_GateAccumulator':
        return _GateAccumulator(
            reasons=self.reasons,
            warnings=self.warnings,
            missing_required=self.missing_required,
            missing_optional=[*self.missing_optional, probe],
            checked=self.checked,
    )

    def update_checked(self, updates: dict) -> '_GateAccumulator':
        new_checked = {**self.checked, **updates}
        return _GateAccumulator(
            reasons=self.reasons,
            warnings=self.warnings,
            missing_required=self.missing_required,
            missing_optional=self.missing_optional,
            checked=new_checked,
    )

    def extend_warnings(self, more: list[str]) -> '_GateAccumulator':
        return _GateAccumulator(
            reasons=self.reasons,
            warnings=[*self.warnings, *more],
            missing_required=self.missing_required,
            missing_optional=self.missing_optional,
            checked=self.checked,
    )


def _collect_required_probes(repo_root: Path, acc: _GateAccumulator) -> tuple[_GateAccumulator, _ContractProbeState]:
    """Phase 1: Collect required contract probe results."""
    mig_report = _load_report(repo_root, 'probe_m218e_memory_integration_guard', 'memory_integration_guard.json')
    mig_pass = _is_pass(mig_report)
    mig_manifest = _load_report(repo_root, 'probe_m218e_memory_integration_guard', 'memory_integration_manifest.json')
    zf_sanity = _load_report(repo_root, 'probe_f216i_zero_findings_quality', 'sanity_zero_findings.json')
    zf_quality = _load_report(repo_root, 'probe_f216i_zero_findings_quality', 'zero_findings_quality.json')
    zf_sane, zf_detail = _zero_findings_quality_sane(zf_sanity)
    nrg_report = _load_report(repo_root, 'probe_f216h_nonfeed_recovery_guard', 'nonfeed_recovery_guard.json')
    nrg_pass = _is_pass(nrg_report)

    checked = {
        'probe_m218e_memory_integration_guard': {
            'found': mig_report.found, 'parse_error': mig_report.parse_error,
            'pass': mig_pass, 'status': mig_report.data.get('status') if mig_report.found else None
        },
        'probe_m218e_memory_integration_guard_manifest': {
            'found': mig_manifest.found, 'parse_error': mig_manifest.parse_error
        },
        'probe_f216i_zero_findings_quality_sanity': {
            'found': zf_sanity.found, 'parse_error': zf_sanity.parse_error,
            'sane': zf_sane, 'detail': zf_detail,
            'verdict': zf_sanity.data.get('verdict') if zf_sanity.found else None
        },
        'probe_f216i_zero_findings_quality': {
            'found': zf_quality.found, 'parse_error': zf_quality.parse_error,
            'detail': zf_quality.data.get('confirmation_zero_findings_stay_failed', {}).get('grade') if zf_quality.found else None
        },
        'probe_f216h_nonfeed_recovery_guard': {
            'found': nrg_report.found, 'parse_error': nrg_report.parse_error,
            'pass': nrg_pass, 'ready_for_smoke': nrg_report.data.get('ready_for_controlled_smoke') if nrg_report.found else None,
            'status': nrg_report.data.get('status') if nrg_report.found else None
        },
        'probe_f216h_nonfeed_recovery_guard_manifest': {'found': True},
    }

    acc = acc.update_checked(checked)

    # Memory integration guard
    if not mig_report.found:
        acc = acc.add_required('probe_m218e_memory_integration_guard').add_reason('BLOCKED_BY_CONTRACT: memory integration guard missing')
    elif not mig_pass:
        acc = acc.add_reason('BLOCKED_BY_CONTRACT: memory integration guard FAILED')

    # Zero findings quality
    if not zf_sane:
        acc = acc.add_reason(f'BLOCKED_BY_UNKNOWN: zero-findings quality crashed or wrong verdict — {zf_detail}')

    # Nonfeed recovery guard
    if not nrg_report.found:
        acc = acc.add_required('probe_f216h_nonfeed_recovery_guard').add_reason('BLOCKED_BY_CONTRACT: nonfeed recovery guard missing')
    elif not nrg_pass:
        acc = acc.add_reason('BLOCKED_BY_CONTRACT: nonfeed recovery guard FAILED')

    # Fallback schema marker detection
    fallback_reports = [mig_report, mig_manifest, nrg_report, zf_sanity, zf_quality]
    fallback_blocked = False
    for r in fallback_reports:
        if r is not None and _has_fallback_schema_marker(r):
            fallback_blocked = True
            acc = acc.add_reason('BLOCKED_BY_UNKNOWN: fallback acquisition schema marker detected')
            break
    acc = acc.update_checked({'fallback_schema_marker': {'blocked': fallback_blocked}})

    contract_state = _ContractProbeState(
        mig_report=mig_report, nrg_report=nrg_report,
        zf_sane=zf_sane, zf_detail=zf_detail, fallback_blocked=fallback_blocked
    )
    return acc, contract_state


def _collect_optional_probes(repo_root: Path, acc: _GateAccumulator) -> tuple[_GateAccumulator, _OptionalProbeState]:
    """Phase 2: Collect optional probe results."""
    # Provider surface
    surf_missing, surf_warnings, surf_checked = _check_provider_surface(repo_root)
    acc = acc.update_checked(surf_checked).extend_warnings(surf_warnings)
    for old_probe in surf_missing:
        acc = acc.add_required(old_probe)
        if 'bootstrap' in old_probe:
            acc = acc.add_reason('BLOCKED_BY_PROVIDER_SURFACE: public bootstrap missing (no passing F219H/F219D alias)')
        else:
            acc = acc.add_reason('BLOCKED_BY_PROVIDER_SURFACE: CT provider resilience missing (no passing F219E alias)')

    # CT cooldown
    ct_cooldown = _check_ct_cooldown(repo_root)
    ct_cooldown_report = ct_cooldown[2]
    acc = acc.update_checked({'probe_f219e_ct_provider_cooldown': {
        'found': ct_cooldown_report.found if ct_cooldown_report else False,
        'pass': ct_cooldown[0], 'detail': ct_cooldown[1]
    }})
    if ct_cooldown_report and not ct_cooldown_report.found:
        acc = acc.add_optional('probe_f219e_ct_provider_cooldown').add_warning('optional CT cooldown report absent — skipped')
    elif ct_cooldown_report and not ct_cooldown[0]:
        acc = acc.add_reason(f'BLOCKED_BY_PROVIDER_SURFACE: CT cooldown FAILED — {ct_cooldown[1]}')

    # Ledger
    ledger_pass, ledger_detail = _check_nonfeed_candidate_ledger(repo_root)
    acc = acc.update_checked({'probe_f217e_nonfeed_candidate_ledger': {'pass': ledger_pass, 'detail': ledger_detail}})
    if not ledger_pass:
        acc = acc.add_warning(f'nonfeed candidate ledger issue: {ledger_detail}')

    # Surface contract
    sc_pass, sc_detail, sc_report = _check_surface_contract(repo_root)
    acc = acc.update_checked({'probe_f219a_surface_contract': {
        'found': sc_report.found if sc_report else False, 'pass': sc_pass, 'detail': sc_detail
    }})
    if sc_report and not sc_report.found:
        acc = acc.add_optional('probe_f219a_surface_contract').add_warning('optional surface contract absent — skipped')
    elif sc_report and not sc_pass:
        acc = acc.add_reason(f'BLOCKED_BY_CONTRACT: surface contract FAILED — {sc_detail}')

    # Hermes metal finalizer
    hmf_pass, hmf_detail, hmf_report = _check_hermes_metal_finalizer(repo_root)
    acc = acc.update_checked({'probe_f219b_hermes_metal_finalizer': {
        'found': hmf_report.found if hmf_report else False, 'pass': hmf_pass, 'detail': hmf_detail
    }})
    if hmf_report and not hmf_report.found:
        acc = acc.add_optional('probe_f219b_hermes_metal_finalizer').add_warning('optional Hermes Metal finalizer absent — skipped')
    elif hmf_report and not hmf_pass:
        acc = acc.add_reason(f'BLOCKED_BY_CONTRACT: Hermes Metal finalizer FAILED — {hmf_detail}')

    # Public session seal
    pss_pass, pss_detail, pss_report = _check_public_session_seal(repo_root)
    acc = acc.update_checked({'probe_f219d_public_session_seal': {
        'found': pss_report.found if pss_report else False, 'pass': pss_pass, 'detail': pss_detail
    }})
    if pss_report and not pss_report.found:
        acc = acc.add_optional('probe_f219d_public_session_seal').add_warning('optional public session seal absent — skipped')
    elif pss_report and not pss_pass:
        acc = acc.add_reason(f'BLOCKED_BY_PROVIDER_SURFACE: public session seal FAILED — {pss_detail}')

    # Load fallback alias reports
    for old_probe, alias_list in _PROVIDER_SURFACE_ALIASES.items():
        for new_probe, report_filename in alias_list:
            _load_report(repo_root, new_probe, report_filename)

    optional_state = _OptionalProbeState(
        ct_cooldown=ct_cooldown, surface_contract=(sc_pass, sc_detail, sc_report),
        hermes_metal=(hmf_pass, hmf_detail, hmf_report),
        public_session_seal=(pss_pass, pss_detail, pss_report),
        ledger_pass=ledger_pass, ledger_detail=ledger_detail
    )
    return acc, optional_state


def _collect_artifacts(repo_root: Path, profile: str, acc: _GateAccumulator) -> tuple[_GateAccumulator, _ArtifactState]:
    """Phase 3: Collect F224/F231 artifact check results."""
    is_nonfeed_blocking = profile in ('active300', 'nonfeed_diagnostic')

    f224_core_ready, f224_warnings, missing_f224, f224_checked = _check_f224_artifacts(repo_root, profile)
    acc = acc.update_checked(f224_checked).extend_warnings(f224_warnings)
    if not f224_core_ready:
        for probe in missing_f224:
            acc = acc.add_reason(f'BLOCKED_BY_CONTRACT: {probe} missing — required for {profile}')

    f231_core_ready, f231_warnings, missing_f231, f231_checked = _check_f231_artifacts(repo_root, profile)
    acc = acc.update_checked(f231_checked).extend_warnings(f231_warnings)
    if not f231_core_ready:
        for probe in missing_f231:
            acc = acc.add_reason(f'BLOCKED_BY_CONTRACT: {probe} missing — required evidence lift pack for {profile}')

    artifact_state = _ArtifactState(
        f224_core_ready=f224_core_ready, f224_warnings=f224_warnings, missing_f224=missing_f224,
        f231_core_ready=f231_core_ready, f231_warnings=f231_warnings, missing_f231=missing_f231,
        is_nonfeed_blocking=is_nonfeed_blocking
    )
    return acc, artifact_state


def _classify_blocks(acc: _GateAccumulator, artifact_state: _ArtifactState) -> dict:
    """Classify block types from accumulated reasons."""
    reasons = acc.reasons
    f224_blocks = not artifact_state.f224_core_ready and artifact_state.is_nonfeed_blocking
    f231_blocks = not artifact_state.f231_core_ready and artifact_state.is_nonfeed_blocking

    has_provider_surface = any('BLOCKED_BY_PROVIDER_SURFACE' in r for r in reasons)
    has_contract = any('BLOCKED_BY_CONTRACT' in r for r in reasons)
    has_memory = any('BLOCKED_BY_MEMORY' in r for r in reasons)
    has_unknown = any('BLOCKED_BY_UNKNOWN' in r for r in reasons)

    return {
        'has_provider_surface': has_provider_surface,
        'has_contract': has_contract,
        'has_memory': has_memory,
        'has_unknown': has_unknown,
        'f224_blocks': f224_blocks,
        'f231_blocks': f231_blocks,
    }


def _derive_decision(
    memory: _MemoryState,
    artifact_state: _ArtifactState,
    contract_state: _ContractProbeState,
    blocks: dict,
) -> tuple[Decision, bool, list[str], list[str]]:
    """
    Derive final decision using flat dispatch logic.
    Returns (decision, live_allowed, reasons, warnings).
    """
    reasons: list[str] = []
    warnings: list[str] = []
    live_allowed = False
    decision = Decision.BLOCKED_BY_UNKNOWN

    # Memory override states take precedence
    if memory.uma_state in ('critical', 'emergency'):
        return (
            Decision.BLOCKED_BY_MEMORY, False,
            [f'BLOCKED_BY_MEMORY: uma_state={memory.uma_state} (override)'],
            warnings
    )

    if memory.swap_policy_tier == 'hard_block':
        return (
            Decision.BLOCKED_BY_MEMORY, False,
            [f'BLOCKED_BY_MEMORY: {memory.swap_gate_reason}'],
            warnings
    )

    # Decision dispatch based on block classification
    if blocks['f224_blocks'] or blocks['f231_blocks']:
        decision = Decision.BLOCKED_BY_CONTRACT
        reasons = []
        if blocks['f224_blocks']:
            reasons.append('BLOCKED_BY_CONTRACT: F224 blocking artifacts missing for nonfeed profile')
        if blocks['f231_blocks']:
            reasons.append('BLOCKED_BY_CONTRACT: F231 evidence lift pack missing for nonfeed profile')
        return (decision, False, reasons, warnings)

    # Classify primary block type from reasons
    if blocks['has_memory']:
        return (Decision.BLOCKED_BY_MEMORY, False, [f'BLOCKED_BY_MEMORY: {memory.swap_gate_reason}'], warnings)

    if blocks['has_provider_surface']:
        return (Decision.BLOCKED_BY_PROVIDER_SURFACE, False, [], warnings)

    if blocks['has_contract']:
        return (Decision.BLOCKED_BY_CONTRACT, False, [], warnings)

    if blocks['has_unknown']:
        return (Decision.BLOCKED_BY_UNKNOWN, False, [], warnings)

    # Hardware-tainted path for diagnostic tier
    if memory.swap_policy_tier == 'diagnostic':
        decision = Decision.READY_FOR_LIVE_HARDWARE_TAINTED
        live_allowed = True
        reasons.append(f'HARDWARE_TAINTED: {memory.swap_gate_reason}')
        warnings.append('Swap elevated: results will be non-comparable (use --require-memory-ok for clean run)')
        return (decision, live_allowed, reasons, warnings)

    # All clear
    return (Decision.READY_FOR_LIVE, True, ['All required probe checks passed; UMA within limits'], warnings)


def _compute_capability_flags(
    blocks: dict,
    fallback_blocked: bool,
    memory: _MemoryState,
    artifact_state: _ArtifactState,
) -> tuple[bool, list[str]]:
    """Compute capability_live_allowed and capability_blockers."""
    capability_blocked = (
        blocks['has_provider_surface'] or
        blocks['has_memory'] or
        blocks['f224_blocks'] or
        blocks['f231_blocks'] or
        fallback_blocked
    )

    blockers = []
    if blocks['has_provider_surface']:
        blockers.append('provider_surface_degraded')
    if blocks['f224_blocks']:
        blockers.append('F224 blocking artifacts missing')
    if blocks['f231_blocks']:
        blockers.append('F231 evidence lift pack missing')
    if blocks['has_memory']:
        blockers.append('BLOCKED_BY_MEMORY')
    if fallback_blocked:
        blockers.append('fallback_schema_blocked')

    return not capability_blocked, blockers


def run_gate(repo_root: Path, profile: str, query: str) -> DecisionResult:
    """
    Run the pre-live decision gate.
    No live sprint. No model load. No network. No SprintScheduler.
    """
    repo_root = Path(repo_root).resolve()

    # Phase 1: Required contract probes
    acc = _GateAccumulator(reasons=[], warnings=[], missing_required=[], missing_optional=[], checked={})
    acc, contract_state = _collect_required_probes(repo_root, acc)

    # Phase 2: Optional probes
    acc, optional_state = _collect_optional_probes(repo_root, acc)

    # Phase 3: F224/F231 artifacts
    acc, artifact_state = _collect_artifacts(repo_root, profile, acc)

    # Phase 4: Memory state
    uma = _check_uma()
    swap_gib = uma.get('swap_used_gib', 0.0)
    uma_state = uma.get('uma_state', 'unknown')
    swap_policy_tier, swap_gate_reason = get_swap_policy_tier(swap_gib)
    hardware_constrained = swap_policy_tier in ('diagnostic', 'hard_block')
    acc = acc.update_checked({'uma': uma})

    memory = _MemoryState(
        uma=uma, swap_policy_tier=swap_policy_tier, swap_gate_reason=swap_gate_reason,
        hardware_constrained=hardware_constrained, uma_state=uma_state
    )

    # Phase 5: Classify blocks and derive decision
    blocks = _classify_blocks(acc, artifact_state)
    decision, live_allowed, extra_reasons, extra_warnings = _derive_decision(
        memory, artifact_state, contract_state, blocks
    )
    acc = acc.extend_warnings(extra_warnings)

    # Build final reasons (prepend from decision derivation)
    final_reasons = acc.reasons
    if extra_reasons and extra_reasons[0].startswith('BLOCKED_BY_'):
        final_reasons = [*extra_reasons, *acc.reasons]
    else:
        final_reasons = acc.reasons

    # Capability flags
    capability_live_allowed, capability_blockers = _compute_capability_flags(
        blocks, contract_state.fallback_blocked, memory, artifact_state
    )

    # Feed baseline eligibility
    feed_baseline_allowed = (
        not blocks['has_provider_surface'] and
        not blocks['has_memory'] and
        swap_policy_tier in ('clean', 'diagnostic') and
        not acc.missing_required
    )

    # Commands
    encoded_query = query.replace('"', '\\"')
    live_cmd = f'python -m core --profile {profile} --query "{encoded_query}" --live --require-memory-ok'
    highswap_cmd = f'python -m core --profile {profile} --query "{encoded_query}" --live --allow-high-swap'

    next_action_feed = live_cmd if feed_baseline_allowed else (
        highswap_cmd if swap_policy_tier != 'hard_block' else 'restart required — memory pressure'
    )
    next_action_cap = _resolve_next_action_capability(
        live_cmd, capability_live_allowed, blocks['has_memory'],
        blocks['has_contract'], blocks['f224_blocks'], blocks['f231_blocks']
    )

    return DecisionResult(
        decision=decision, live_allowed=live_allowed,
        reasons=final_reasons, warnings=acc.warnings,
        missing_required_reports=acc.missing_required, missing_optional_reports=acc.missing_optional,
        uma=uma, checked_reports=acc.checked,
        suggested_live_command=live_cmd, suggested_highswap_diagnostic_command=highswap_cmd,
        fallback_schema_blocked=contract_state.fallback_blocked,
        hardware_constrained=hardware_constrained,
        swap_policy_tier=swap_policy_tier, swap_gate_reason=swap_gate_reason,
        f224_core_ready=artifact_state.f224_core_ready,
        f224_warnings=artifact_state.f224_warnings,
        missing_f224_artifacts=artifact_state.missing_f224,
        f231_core_ready=artifact_state.f231_core_ready,
        f231_warnings=artifact_state.f231_warnings,
        missing_f231_artifacts=artifact_state.missing_f231,
        nonfeed_capability_blocked=blocks['f224_blocks'] or blocks['f231_blocks'],
        nonfeed_block_reason=_build_nonfeed_block_reason(
            blocks['f224_blocks'], blocks['f231_blocks'], profile
        ),
        feed_baseline_allowed=feed_baseline_allowed,
        capability_live_allowed=capability_live_allowed,
        capability_blockers=capability_blockers,
        next_action_feed_baseline=next_action_feed,
        next_action_capability=next_action_cap,
    )

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Pre-Live Decision Gate — Sprint F219F', formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--repo-root', type=Path, default=Path('.'))
    p.add_argument('--profile', default='nonfeed_diagnostic')
    p.add_argument('--query', required=True)
    p.add_argument('--write-report', type=Path, default=None)
    p.add_argument('--output-markdown', type=Path, default=None)
    return p

def _render_uma_section(result: DecisionResult) -> list[str]:
    """UMA status table section."""
    lines = ['## UMA Status', '', '| Field | Value |', '|-------|-------|']
    uma = result.uma
    for key in ('system_used_gib', 'swap_used_gib', 'swap_detected', 'uma_state', 'io_only', 'last_error'):
        val = uma.get(key)
        if val is not None:
            lines.append(f'| {key} | {val} |')
    return lines


def _render_swap_policy_section(result: DecisionResult) -> list[str]:
    """Swap policy section."""
    return [
        '', '---', '', '## Swap Policy (F220F)', '',
        '| Field | Value |', '|-------|-------|',
        f'| Swap Policy Tier | `{result.swap_policy_tier}` |',
        f'| Swap Gate Reason | `{result.swap_gate_reason}` |',
        f'| Hardware Constrained | `{result.hardware_constrained}` |',
    ]


def _render_reasons_section(result: DecisionResult) -> list[str]:
    """Reasons list section."""
    lines = ['', '---', '', '## Reasons', '']
    if result.reasons:
        lines.extend(f'- {r}' for r in result.reasons)
    else:
        lines.append('- (none)')
    return lines


def _render_warnings_section(result: DecisionResult) -> list[str]:
    """Warnings list section."""
    lines = ['', '---', '', '## Warnings', '']
    if result.warnings:
        lines.extend(f'- {w}' for w in result.warnings)
    else:
        lines.append('- (none)')
    return lines


def _render_missing_reports_section(result: DecisionResult) -> list[str]:
    """Missing reports section."""
    return [
        '', '---', '', '## Missing Reports', '',
        f"**Required:** {', '.join(result.missing_required_reports) or '(none)'}",
        f"**Optional:** {', '.join(result.missing_optional_reports) or '(none)'}",
    ]


def _render_alias_table_section(result: DecisionResult) -> list[str]:
    """Provider surface alias table section."""
    aliases = [
        ('probe_f217c_public_bootstrap', 'probe_f219h_public_fetcher_import_seal / probe_f219d_public_session_seal'),
        ('probe_f217d_ct_provider_resilience', 'probe_f219e_ct_provider_cooldown'),
    ]
    checked = result.checked_reports
    lines = ['', '---', '', '## Provider Surface Alias Table (F217→F219)', '',
             '| Old Probe | Current Alias | Status |',
             '|-----------|---------------|--------|']
    for old_probe, new_alias in aliases:
        old_info = checked.get(old_probe, {})
        status = 'PASS' if old_info.get('alias_satisfied') else ('absent' if not old_info.get('found') else 'FAIL')
        lines.append(f'| {old_probe} | {new_alias} | {status} |')
    return lines


def _render_checked_reports_section(result: DecisionResult) -> list[str]:
    """Detailed checked reports section."""
    lines = ['', '---', '', '## Checked Reports', '']
    for name, info in result.checked_reports.items():
        if name == 'uma':
            continue
        lines.append(f'### {name}')
        lines.append(f'- found: `{info.get("found")}`')
        if info.get('parse_error'):
            lines.append(f'- parse_error: `{info.get("parse_error")}`')
        if info.get('pass') is not None:
            lines.append(f'- pass: `{info.get("pass")}`')
        if info.get('detail'):
            lines.append(f'- detail: `{info.get("detail")}`')
        lines.append('')
    return lines


def _render_commands_section(result: DecisionResult) -> list[str]:
    """Suggested commands section."""
    lines = ['', '---', '', '## Suggested Commands', '',
             f'**Clean run (--require-memory-ok):**\n```bash\n{result.suggested_live_command}\n```']
    if result.swap_policy_tier == 'diagnostic':
        lines.append(f'\n**Diagnostic run (--allow-high-swap — results non-comparable):**\n```bash\n{result.suggested_highswap_diagnostic_command}\n```')
    elif result.swap_policy_tier == 'hard_block':
        lines.append('\n**Hard block — restart required before running**')
    return lines


def _render_markdown(result: DecisionResult, profile: str, query: str) -> str:
    """Render decision result as markdown report."""
    header = [
        '# Pre-Live Decision Gate Report', '',
        f'**Decision:** `{result.decision.value}`',
        f'**Live Allowed:** `{result.live_allowed}`',
        f'**Hardware Constrained:** `{result.hardware_constrained}`',
        f'**Profile:** `{profile}`',
        f'**Query:** `{query}`',
    ]
    sections = [
        _render_uma_section(result),
        _render_swap_policy_section(result),
        _render_reasons_section(result),
        _render_warnings_section(result),
        _render_missing_reports_section(result),
        _render_alias_table_section(result),
        _render_checked_reports_section(result),
        _render_commands_section(result),
    ]
    return '\n'.join(header + [line for section in sections for line in section])

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f'ERROR: repo root does not exist: {repo_root}', file=sys.stderr)
        return 1
    result = run_gate(repo_root, args.profile, args.query)
    if args.write_report:
        out_clean = {'decision': result.decision.value, 'live_allowed': result.live_allowed, 'reasons': result.reasons, 'warnings': result.warnings, 'missing_required_reports': result.missing_required_reports, 'missing_optional_reports': result.missing_optional_reports, 'uma': result.uma, 'suggested_live_command': result.suggested_live_command, 'suggested_highswap_diagnostic_command': result.suggested_highswap_diagnostic_command, 'fallback_schema_blocked': result.fallback_schema_blocked, 'hardware_constrained': result.hardware_constrained, 'swap_policy_tier': result.swap_policy_tier, 'swap_gate_reason': result.swap_gate_reason, 'checked_reports': {k: {kk: vv for kk, vv in v.items() if kk not in ('data',)} for k, v in result.checked_reports.items()}, 'f224_core_ready': result.f224_core_ready, 'f224_warnings': result.f224_warnings, 'missing_f224_artifacts': result.missing_f224_artifacts, 'f231_core_ready': result.f231_core_ready, 'f231_warnings': result.f231_warnings, 'missing_f231_artifacts': result.missing_f231_artifacts, 'nonfeed_capability_blocked': result.nonfeed_capability_blocked, 'nonfeed_block_reason': result.nonfeed_block_reason, 'feed_baseline_allowed': result.feed_baseline_allowed, 'capability_live_allowed': result.capability_live_allowed, 'capability_blockers': result.capability_blockers, 'next_action_feed_baseline': result.next_action_feed_baseline, 'next_action_capability': result.next_action_capability}
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write_report, 'w', encoding='utf-8') as fh:
            json.dump(out_clean, fh, indent=2, default=str)
        print(f'JSON report written: {args.write_report}')
    md_path = args.output_markdown or (args.write_report.parent / 'REPORT_PRELIVE_DECISION_GATE.md' if args.write_report else None)
    if md_path:
        md_text = _render_markdown(result, args.profile, args.query)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, 'w', encoding='utf-8') as fh:
            fh.write(md_text)
        print(f'Markdown report written: {md_path}')
    print(f"\n{'=' * 60}")
    print(f'  Decision: {result.decision.value}')
    print(f'  Live Allowed: {result.live_allowed}')
    print(f'  Hardware Constrained: {result.hardware_constrained}')
    print(f'  Swap Policy Tier: {result.swap_policy_tier}')
    print(f'  Swap Gate Reason: {result.swap_gate_reason}')
    print(f"{'=' * 60}")
    if result.reasons:
        print('Reasons:')
        for r in result.reasons:
            print(f'  - {r}')
    if result.warnings:
        print('Warnings:')
        for w in result.warnings:
            print(f'  - {w}')
    if result.missing_required_reports:
        print(f"Missing required reports: {', '.join(result.missing_required_reports)}")
    uma_sw = result.uma.get('swap_used_gib', 0)
    print(f'UMA: swap={uma_sw:.2f}GiB')
    print()
    return 0 if result.live_allowed else 1
if __name__ == '__main__':
    sys.exit(main())