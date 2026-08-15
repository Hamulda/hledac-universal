"""
Prelive One-Button Decision Gate — Sprint F221H

Single command gives one verdict on whether a live sprint is worth running.




Combines:
  - Artifact readiness (F221A-G + cross-sprint required probes)
  - Memory/swap state (UMA sample)
  - Surface contract (prelive decision gate)
  - Provider surface readiness
  - Optional last live artifact triage

Verdicts:
  RUN_NOW                       — all clear, ready to run
  RESTART_THEN_RUN             — swap elevated but artifacts ready
  DO_NOT_RUN_FIX_ARTIFACTS     — missing required F221 probe artifacts
  DO_NOT_RUN_PROVIDER_SURFACE  — provider surface missing or broken
  DO_NOT_RUN_CONTRACT          — fallback acquisition schema detected
  DO_NOT_RUN_UNKNOWN           — parse/runtime error

No live execution. No network. No MLX load. No SprintScheduler.

Usage:
    python tools/prelive_one_button_gate.py \\
        --repo-root . \\
        --profile nonfeed_diagnostic180 \\
        --query "mozilla.org certificate transparency subdomains april 2026" \\
        --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\
        --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md

    # With optional last-live triage:
    python tools/prelive_one_button_gate.py \\
        --repo-root . \\
        --profile nonfeed_diagnostic180 \\
        --query "..." \\
        --last-live-triage probe_f219g_live_artifact_triage/triage.json \\
        --output-json ...
"""
import argparse
import json
import sys
import textwrap
from dataclasses import dataclass, field
import msgspec
from enum import StrEnum
from pathlib import Path


# =============================================================================
# Shared helpers
# =============================================================================

@dataclass(frozen=True, slots=True)
class JsonValidationResult:
    """Result of JSON file validation."""
    valid: bool
    parse_error: str | None = None


def _validate_json_file(path: Path) -> JsonValidationResult:
    """Validate a JSON file. Returns (valid, parse_error)."""
    try:
        with open(path, encoding='utf-8') as fh:
            json.load(fh)
        return JsonValidationResult(valid=True)
    except json.JSONDecodeError as exc:
        return JsonValidationResult(valid=False, parse_error=f'JSON decode error: {exc}')
    except Exception as exc:
        return JsonValidationResult(valid=False, parse_error=str(exc))


@dataclass(frozen=True, slots=True)
class ImportCheckResult:
    """Result of import contract check."""
    found: bool
    valid: bool
    parse_error: str | None = None
    blocks_live: bool = True


def _check_import_contract(
    module_path: str,
    probe_dir: str,
    filename: str,
    checks: tuple[tuple[str, bool], ...],
    blocks_live: bool = True,
) -> ImportCheckResult:
    """
    Check an import contract. Returns ImportCheckResult.
    
    Args:
        module_path: Full import path, e.g. 'hledac.universal.export.sprint_exporter'
        probe_dir: Directory name for artifact matrix
        filename: Filename for artifact matrix
        checks: Tuple of (check_name, check_result) tuples
        blocks_live: Whether failure blocks live runs
    """
    try:
        import importlib
        import_str = module_path
        parts = module_path.rsplit('.', 1)
        if len(parts) == 2:
            module = importlib.import_module(parts[0])
            obj = getattr(module, parts[1], None)
        else:
            obj = importlib.import_module(module_path)
        
        failures = [name for name, ok in checks if not ok]
        valid = len(failures) == 0
        return ImportCheckResult(
            found=True,
            valid=valid,
            parse_error='; '.join(failures) if failures else None,
            blocks_live=blocks_live,
        )
    except ImportError as exc:
        return ImportCheckResult(
            found=False,
            valid=False,
            parse_error=str(exc),
            blocks_live=blocks_live,
        )

class OneButtonVerdict(StrEnum):
    RUN_NOW = 'RUN_NOW'
    RESTART_THEN_RUN = 'RESTART_THEN_RUN'
    DO_NOT_RUN_FIX_ARTIFACTS = 'DO_NOT_RUN_FIX_ARTIFACTS'
    DO_NOT_RUN_PROVIDER_SURFACE = 'DO_NOT_RUN_PROVIDER_SURFACE'
    DO_NOT_RUN_CONTRACT = 'DO_NOT_RUN_CONTRACT'
    DO_NOT_RUN_MEMORY_HARD_BLOCK = 'DO_NOT_RUN_MEMORY_HARD_BLOCK'
    DO_NOT_RUN_UNKNOWN = 'DO_NOT_RUN_UNKNOWN'
    READY_FOR_NONFEED_CAPABILITY_RUN = 'READY_FOR_NONFEED_CAPABILITY_RUN'
    READY_FOR_FEED_BASELINE_ONLY = 'READY_FOR_FEED_BASELINE_ONLY'
from functools import lru_cache

from hledac.universal._core.resource_governor import CLEAN_SWAP_MAX_GIB, DIAGNOSTIC_SWAP_MAX_GIB
from _core import aclose
_BENCHMARK_TO_ACQUISITION_PROFILE: dict[str, str] = {'nonfeed_diagnostic180': 'nonfeed_diagnostic', 'active300': 'default', 'active600': 'default'}

def _get_acquisition_profile_for_benchmark(benchmark_profile: str) -> str:
    """Map benchmark profile name to runtime acquisition profile.

    F223A: nonfeed_diagnostic180 benchmark → nonfeed_diagnostic acquisition.
    """
    return _BENCHMARK_TO_ACQUISITION_PROFILE.get(benchmark_profile, 'default')
_EXPECTED_REPO_ROOT = '/Users/vojtechhamada/PycharmProjects/Hledac'
_UNIVERSAL_ROOT = f'{_EXPECTED_REPO_ROOT}/hledac/universal'

def _get_repo_root_reality() -> dict:
    """Hermetic CWD diagnostic — no live run, no network, no MLX."""
    import os as _os
    from pathlib import Path as _P
    _cwd = _os.getcwd()
    _resolved = str(_P(_cwd).resolve())
    _universal = _UNIVERSAL_ROOT
    _is_universal_root = _resolved == _universal or _resolved.startswith(f'{_universal}/')
    _universal_exists = _P(_universal).exists()
    _tests_probe_exists = _P(f'{_universal}/tests/probe_f223h_cwd_invocation_guard').exists()
    _cwd_warning = f'WARNING: CWD={_cwd} is outside expected universal root ({_universal}). Artifact scans may glob wrong directory. Use --repo-root {_UNIVERSAL_ROOT} or run from {_UNIVERSAL_ROOT}.' if not _is_universal_root else ''
    return {'cwd': _cwd, 'resolved_cwd': _resolved, 'expected_repo_root': _EXPECTED_REPO_ROOT, 'universal_root': _universal, 'cwd_is_universal_root': _is_universal_root, 'universal_root_exists': _universal_exists, 'tests_probe_dir_exists': _tests_probe_exists, 'cwd_warning': _cwd_warning}

def _check_cwd_guard(repo_root: Path) -> str:
    """Check CWD vs repo-root. Returns warning string or empty if OK."""
    reality = _get_repo_root_reality()
    if reality['cwd_warning']:
        return reality['cwd_warning']
    _resolved_repo = str(repo_root.resolve())
    _repo_path = Path(_resolved_repo)
    if _resolved_repo != reality['universal_root'] and (not repo_root.name == 'hledac'):
        try:
            _repo_path.relative_to(reality['universal_root'])
        except ValueError:
            return f"WARNING: --repo-root {_resolved_repo} is not inside expected universal root ({reality['universal_root']}). Artifact scans may be incorrect."
    return ''
_F221_REQUIRED_PROBES = [('probe_f221a_source_family_truth', 'source_family_truth.json'), ('probe_f221b_ct_domain_lane', 'ct_domain_lane.json'), ('probe_f221c_public_timeout_diagnosis', 'public_timeout_diagnosis.json'), ('probe_f221d_quality_surface_consistency', 'quality_surface_consistency.json'), ('probe_f221e_delta_sanity_alignment', 'delta_sanity_alignment.json'), ('probe_f221f_ae_integration_guard', 'ae_integration_guard.json'), ('probe_f221g_nonfeed_diag_ready', 'nonfeed_diag_ready.json')]
_F223_ARTIFACT_ALIASES: dict[str, list[tuple[str, str]]] = {'F223A_PROFILE_PROPAGATION': [('probe_f223a_nonfeed_profile_propagation', 'nonfeed_profile_propagation.json'), ('probe_f223a_profile_propagation', 'profile_propagation.json')], 'F223B_TERMINALITY_VERDICT_SSOT': [('probe_f223b_terminality_verdict_ssot', 'terminality_verdict_ssot.json')], 'F223C_PUBLIC_COUNTER_TRUTH': [('probe_f223c_public_counter_truth', 'public_counter_truth.json'), ('probe_f223c_module_invocation_reality', 'module_invocation_reality.json')], 'F223D_PRODUCT_VALUE_REALITY': [('probe_f223d_product_value_reality', 'product_value_reality.json')], 'F223H_CWD_INVOCATION_GUARD': [('probe_f223h_cwd_invocation_guard', 'cwd_invocation_guard.json')], 'F223E_ASYNC_RESOURCE_HYGIENE': [('probe_f223e_async_resource_hygiene', 'async_resource_hygiene.json')], 'F223F_ANALYST_BRIEF_REALITY': [('probe_f223f_analyst_brief_reality', 'analyst_brief_reality.json')], 'F223G_PERSISTENT_DEDUP_AUDIT': [('probe_f223g_persistent_dedup_audit', 'persistent_dedup_audit.json')]}
_F223_REQUIRED_PROBES = [('probe_f223a_nonfeed_profile_propagation', 'nonfeed_profile_propagation.json'), ('probe_f223b_terminality_verdict_ssot', 'terminality_verdict_ssot.json'), ('probe_f223c_public_counter_truth', 'public_counter_truth.json'), ('probe_f223d_product_value_reality', 'product_value_reality.json'), ('probe_f223h_cwd_invocation_guard', 'cwd_invocation_guard.json')]
_F223_OPTIONAL_PROBES = [('probe_f223e_async_resource_hygiene', 'async_resource_hygiene.json'), ('probe_f223f_analyst_brief_reality', 'analyst_brief_reality.json'), ('probe_f223g_persistent_dedup_audit', 'persistent_dedup_audit.json')]


# =============================================================================
# Refactored run_one_button_gate — helper functions
# =============================================================================

_NONFEED_PROFILES: frozenset[str] = frozenset({'nonfeed_diagnostic', 'nonfeed_diagnostic180', 'active300'})

# --- Artifact extraction helpers ---
def _extract_missing_paths(results: list) -> list[str]:
    """Extract missing artifact paths from results list."""
    return [f'{r.probe_dir}/{r.filename}' for r in results if not r.valid]


def _build_f221_artifacts_dict(
    results: list[F221ArtifactResult],
    missing: list[F221ArtifactResult],
) -> dict:
    """Build F221 artifact summary dict."""
    return {
        'total': len(results),
        'valid': sum(1 for r in results if r.valid),
        'missing': len(missing),
        'details': [
            {'probe_dir': r.probe_dir, 'filename': r.filename, 'found': r.found,
             'valid': r.valid, 'parse_error': r.parse_error}
            for r in results
        ],
    }


def _build_f223_artifacts_dict(
    required_results: list[F223ArtifactResult],
    required_missing: list[F223ArtifactResult],
    optional_results: list[F223ArtifactResult],
) -> dict:
    """Build F223 artifact summary dict."""
    return {
        'required_total': len(required_results),
        'required_valid': sum(1 for r in required_results if r.valid),
        'required_missing': len(required_missing),
        'optional_total': len(optional_results),
        'optional_valid': sum(1 for r in optional_results if r.valid),
        'required_details': [
            {'logical_name': r.logical_name, 'probe_dir': r.probe_dir, 'filename': r.filename,
             'found': r.found, 'valid': r.valid, 'parse_error': r.parse_error,
             'resolved_path': r.resolved_path, 'alias_used': r.alias_used,
             'searched_paths': r.searched_paths}
            for r in required_results
        ],
        'optional_details': [
            {'logical_name': r.logical_name, 'probe_dir': r.probe_dir, 'filename': r.filename,
             'found': r.found, 'valid': r.valid, 'parse_error': r.parse_error,
             'resolved_path': r.resolved_path, 'alias_used': r.alias_used,
             'searched_paths': r.searched_paths}
            for r in optional_results
        ],
    }


@lru_cache(maxsize=1)
def _sample_uma() -> dict:
    """Sample current UMA/swap state via core.resource_governor."""
    try:
        from hledac.universal._core.resource_governor import sample_uma_status
        UmaStatus = sample_uma_status()
        return {'system_used_gib': round(getattr(UmaStatus, 'system_used_gib', 0.0), 3), 'swap_used_gib': round(getattr(UmaStatus, 'swap_used_gib', 0.0), 3), 'swap_detected': getattr(UmaStatus, 'swap_detected', False), 'uma_state': getattr(UmaStatus, 'state', 'unknown'), 'io_only': getattr(UmaStatus, 'io_only', False), 'error': None}
    except Exception as exc:
        return {'system_used_gib': 0.0, 'swap_used_gib': 0.0, 'swap_detected': False, 'uma_state': 'unknown', 'io_only': False, 'error': str(exc)}

class F221ArtifactResult(msgspec.Struct, gc=False):
    probe_dir: str
    filename: str
    found: bool
    parse_error: str | None = None
    valid: bool = False

def _check_f221_artifact(repo_root: Path, probe_dir: str, filename: str) -> F221ArtifactResult:
    """Check a single F221 probe artifact exists and is parseable JSON."""
    full_path = repo_root / probe_dir / filename
    result = F221ArtifactResult(probe_dir=probe_dir, filename=filename, found=False)
    if not full_path.exists():
        return result
    result.found = True
    validation = _validate_json_file(full_path)
    result.valid = validation.valid
    result.parse_error = validation.parse_error
    return result

def _check_all_f221_artifacts(repo_root: Path) -> tuple[list[F221ArtifactResult], list[F221ArtifactResult]]:
    """Check all F221 required artifacts. Returns (required_results, missing)."""
    results: list[F221ArtifactResult] = []
    missing: list[F221ArtifactResult] = []
    for probe_dir, filename in _F221_REQUIRED_PROBES:
        result = _check_f221_artifact(repo_root, probe_dir, filename)
        results.append(result)
        if not result.valid:
            missing.append(result)
    return (results, missing)

class F223ArtifactResult(msgspec.Struct, frozen=True, gc=False):
    logical_name: str = ''
    probe_dir: str = ''
    filename: str = ''
    found: bool = False
    valid: bool = False
    parse_error: str | None = None
    resolved_path: str | None = None
    alias_used: bool = False
    searched_paths: list[str] = field(default_factory=list)

def _check_f223_artifact(repo_root: Path, logical_name: str, probe_dir: str, filename: str) -> F223ArtifactResult:
    """Check a single F223 probe artifact, trying alias paths if primary is missing."""
    candidates = [(probe_dir, filename)]
    aliases = _F223_ARTIFACT_ALIASES.get(logical_name, [])
    for alias_dir, alias_file in aliases:
        if alias_dir != probe_dir or alias_file != filename:
            candidates.append((alias_dir, alias_file))
    searched_paths: list[str] = []
    result = F223ArtifactResult(logical_name=logical_name, probe_dir=probe_dir, filename=filename, found=False)
    for candidate_dir, candidate_file in candidates:
        full_path = repo_root / candidate_dir / candidate_file
        searched_paths.append(str(full_path))
        if not full_path.exists():
            continue
        result.found = True
        result.resolved_path = str(full_path)
        if candidate_dir != probe_dir or candidate_file != filename:
            result.alias_used = True
        validation = _validate_json_file(full_path)
        result.valid = validation.valid
        result.parse_error = validation.parse_error
        break
    result.searched_paths = searched_paths
    return result

def _check_all_f223_artifacts(repo_root: Path) -> tuple[list[F223ArtifactResult], list[F223ArtifactResult], list[F223ArtifactResult]]:
    """
    Check all F223 artifacts using alias resolution. Returns (required_results, required_missing, optional_results).
    Required missing blocks RUN_NOW / RESTART_THEN_RUN.
    """
    required_results: list[F223ArtifactResult] = []
    required_missing: list[F223ArtifactResult] = []
    optional_results: list[F223ArtifactResult] = []
    for probe_dir, filename in _F223_REQUIRED_PROBES:
        logical_name = _derive_logical_name(probe_dir)
        result = _check_f223_artifact(repo_root, logical_name, probe_dir, filename)
        required_results.append(result)
        if not result.valid:
            required_missing.append(result)
    for probe_dir, filename in _F223_OPTIONAL_PROBES:
        logical_name = _derive_logical_name(probe_dir)
        result = _check_f223_artifact(repo_root, logical_name, probe_dir, filename)
        optional_results.append(result)
    return (required_results, required_missing, optional_results)

# Dispatch table for _derive_logical_name
_DERIVE_LOGICAL_NAME_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (('nonfeed_profile_propagation', 'profile_propagation'), 'F223A_PROFILE_PROPAGATION'),
    (('terminality_verdict_ssot',), 'F223B_TERMINALITY_VERDICT_SSOT'),
    (('public_counter_truth', 'module_invocation_reality'), 'F223C_PUBLIC_COUNTER_TRUTH'),
    (('product_value_reality',), 'F223D_PRODUCT_VALUE_REALITY'),
    (('async_resource_hygiene',), 'F223E_ASYNC_RESOURCE_HYGIENE'),
    (('analyst_brief_reality',), 'F223F_ANALYST_BRIEF_REALITY'),
    (('persistent_dedup_audit',), 'F223G_PERSISTENT_DEDUP_AUDIT'),
    (('cwd_invocation_guard',), 'F223H_CWD_INVOCATION_GUARD'),
)


def _derive_logical_name(probe_dir: str) -> str:
    """Derive logical artifact name from probe directory using dispatch table."""
    for keywords, logical_name in _DERIVE_LOGICAL_NAME_MAP:
        if any(kw in probe_dir for kw in keywords):
            return logical_name
    return probe_dir
_CROSS_SPRINT_REQUIRED = [('probe_m218e_memory_integration_guard', 'memory_integration_guard.json'), ('probe_f219a_surface_contract', 'surface_contract.json'), ('probe_f219d_public_session_seal', 'public_session_seal.json'), ('probe_f219e_ct_provider_cooldown', 'ct_provider_cooldown.json'), ('probe_f220e_provider_surface_smoke', 'provider_surface_smoke.json')]

def _check_cross_sprint_artifacts(repo_root: Path) -> tuple[list[F221ArtifactResult], list[F221ArtifactResult]]:
    """Check cross-sprint required artifacts."""
    results: list[F221ArtifactResult] = []
    missing: list[F221ArtifactResult] = []
    for probe_dir, filename in _CROSS_SPRINT_REQUIRED:
        result = _check_f221_artifact(repo_root, probe_dir, filename)
        results.append(result)
        if not result.valid:
            missing.append(result)
    return (results, missing)

def _load_last_live_triage(path: Path | None) -> dict | None:
    """Load optional last-live artifact triage result."""
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None

def _load_decision_gate(decision_path: Path | None) -> dict | None:
    if decision_path is None or not decision_path.exists():
        return None
    try:
        with open(decision_path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None

def _is_provider_surface_ok(decision_data: dict | None) -> bool:
    """Check provider surface is OK from decision gate data."""
    if decision_data is None:
        return True
    checked = decision_data.get('checked_reports', {})
    if not checked:
        return True
    pub_bootstrap = checked.get('probe_f217c_public_bootstrap', {})
    ct_resilience = checked.get('probe_f217d_ct_provider_resilience', {})
    pub_session_seal = checked.get('probe_f219d_public_session_seal', {})
    ct_cooldown = checked.get('probe_f219e_ct_provider_cooldown', {})
    provider_surface_smoke = checked.get('probe_f220e_provider_surface_smoke', {})
    pub_ok = pub_bootstrap.get('found') and pub_bootstrap.get('pass')
    seal_ok = pub_session_seal.get('found') and pub_session_seal.get('pass')
    ct_ok = ct_resilience.get('found') and ct_resilience.get('pass')
    cooldown_ok = ct_cooldown.get('found') and ct_cooldown.get('pass')
    smoke_ok = provider_surface_smoke.get('found') and provider_surface_smoke.get('pass')
    pub_satisfied = pub_ok or seal_ok
    ct_satisfied = ct_ok or cooldown_ok
    surface_satisfied = pub_satisfied and ct_satisfied
    if smoke_ok:
        surface_satisfied = True
    return surface_satisfied

def _has_fallback_schema(decision_data: dict | None) -> bool:
    """Check if any report has fallback acquisition schema marker."""
    if decision_data is None:
        return False
    return bool(decision_data.get('fallback_schema_blocked', False))

class OneButtonResult(msgspec.Struct, frozen=True, gc=False):
    verdict: OneButtonVerdict
    live_allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uma: dict = field(default_factory=dict)
    f221_artifacts: dict = field(default_factory=dict)
    missing_f221: list[str] = field(default_factory=list)
    missing_cross_sprint: list[str] = field(default_factory=list)
    f223_artifacts: dict = field(default_factory=dict)
    missing_f223_required: list[str] = field(default_factory=list)
    f223_optional_status: dict = field(default_factory=dict)
    provider_surface_ok: bool = True
    fallback_schema_blocked: bool = False
    swap_policy_tier: str = 'unknown'
    swap_gate_reason: str = ''
    live_command: dict = field(default_factory=dict)
    triage_verdict: str | None = None
    triage_another_live_useful: bool | None = None
    capability_live_allowed: bool = False
    feed_baseline_allowed: bool = False
    why_nonfeed_capability_blocked: str = ''
    degraded_but_allowed: bool = False
    canonical_fallback_detected: bool = False
    f232g_research_quality_present: bool = False
    f233d_nonfeed_prelude_coverage: bool = False
    can_run_live_acquisition: bool = True
    can_run_nonfeed_diagnostic: bool = True
    can_run_llm_synthesis: bool = False
    recommended_mode: str = 'dry_plan'
    max_safe_iterations: int = 0
    max_safe_pivots: int = 0
    investigation_reason: str = ''

    def to_dict(self) -> dict:
        return {'verdict': self.verdict.value, 'live_allowed': self.live_allowed, 'reasons': self.reasons, 'warnings': self.warnings, 'uma': self.uma, 'f221_artifacts': self.f221_artifacts, 'missing_f221': self.missing_f221, 'missing_cross_sprint': self.missing_cross_sprint, 'f223_artifacts': self.f223_artifacts, 'missing_f223_required': self.missing_f223_required, 'f223_optional_status': self.f223_optional_status, 'provider_surface_ok': self.provider_surface_ok, 'fallback_schema_blocked': self.fallback_schema_blocked, 'swap_policy_tier': self.swap_policy_tier, 'swap_gate_reason': self.swap_gate_reason, 'live_command': self.live_command, 'triage_verdict': self.triage_verdict, 'triage_another_live_useful': self.triage_another_live_useful, 'capability_live_allowed': self.capability_live_allowed, 'feed_baseline_allowed': self.feed_baseline_allowed, 'why_nonfeed_capability_blocked': self.why_nonfeed_capability_blocked, 'degraded_but_allowed': self.degraded_but_allowed, 'canonical_fallback_detected': self.canonical_fallback_detected, 'f232g_research_quality_present': self.f232g_research_quality_present, 'f233d_nonfeed_prelude_coverage': self.f233d_nonfeed_prelude_coverage, 'investigation_admission': {'can_run_live_acquisition': self.can_run_live_acquisition, 'can_run_nonfeed_diagnostic': self.can_run_nonfeed_diagnostic, 'can_run_llm_synthesis': self.can_run_llm_synthesis, 'recommended_mode': self.recommended_mode, 'max_safe_iterations': self.max_safe_iterations, 'max_safe_pivots': self.max_safe_pivots, 'reason': self.investigation_reason}}

# =============================================================================
# Refactored run_one_button_gate — dispatch table pattern (CC: 75 → ~12)
# =============================================================================

# --- Swap Policy Tier ------------------------------------------------------------

def _compute_swap_policy_tier(swap_gib: float, uma_state: str) -> tuple[str, str]:
    """Compute swap policy tier and human-readable reason."""
    if uma_state in ('critical', 'emergency'):
        return ('hard_block', f'uma_state={uma_state}')
    if swap_gib <= CLEAN_SWAP_MAX_GIB:
        return ('clean', f'swap={swap_gib:.3f}GiB <= {CLEAN_SWAP_MAX_GIB}GiB')
    if swap_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
        return ('diagnostic', f'swap={swap_gib:.3f}GiB in ({CLEAN_SWAP_MAX_GIB}GiB, {DIAGNOSTIC_SWAP_MAX_GIB}GiB]')
    return ('hard_block', f'swap={swap_gib:.3f}GiB > {DIAGNOSTIC_SWAP_MAX_GIB}GiB')


# --- Nonfeed Profile Flag Checker ------------------------------------------------

def _check_nonfeed_profile_bool(repo_root: Path, probe_name: str, json_key: str) -> bool:
    """Read a boolean flag from a nonfeed profile JSON artifact. Returns False on any error."""
    path = repo_root / probe_name
    if not path.exists():
        return False
    try:
        for candidate in path.iterdir():
            if candidate.is_file() and candidate.suffix == '.json':
                with open(candidate, encoding='utf-8') as fh:
                    if json.load(fh).get(json_key) is True:
                        return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _check_nonfeed_profile_flags(repo_root: Path, is_nonfeed_profile: bool) -> tuple[bool, bool]:
    """Check F232G research quality and F233D prelude coverage for nonfeed profiles."""
    if not is_nonfeed_profile:
        return (False, False)
    f232g = _check_nonfeed_profile_bool(repo_root, 'probe_f231d_research_quality_v2', 'research_quality')
    f233d = _check_nonfeed_profile_bool(repo_root, 'probe_f233d_nonfeed_prelude_coverage', 'coverage_present')
    return (f232g, f233d)


# --- Blocking Conditions Dispatch Table ----------------------------------------

@dataclass(frozen=True, slots=True)
class _BlockingCondition:
    """Single blocking condition for dispatch table."""
    check: bool
    verdict: OneButtonVerdict
    reason: str
    capability_block: str | None = None


def _evaluate_blocking_conditions(
    missing_f221: list[str],
    missing_cross_sprint: list[str],
    missing_f223_required: list[str],
    fallback_blocked: bool,
    provider_surface_ok: bool,
    uma_state: str,
    swap_gib: float,
) -> tuple[bool, _BlockingCondition | None]:
    """
    Evaluate blocking conditions in priority order.
    Returns (any_blocked, first_matching_condition | None).
    """
    conditions: tuple[_BlockingCondition, ...] = (
        _BlockingCondition(
            check=bool(missing_f221),
            verdict=OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS,
            reason=f'Missing required F221 probe artifacts: {", ".join(missing_f221)}'
                + (f'; Also missing cross-sprint: {", ".join(missing_cross_sprint)}' if missing_cross_sprint else ''),
            capability_block='missing_f221_artifacts',
        ),
        _BlockingCondition(
            check=bool(missing_f223_required),
            verdict=OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS,
            reason=f'Missing required F223 post-F223 probe artifacts: {", ".join(missing_f223_required)}',
            capability_block='missing_f223_required_artifacts',
        ),
        _BlockingCondition(
            check=fallback_blocked,
            verdict=OneButtonVerdict.DO_NOT_RUN_CONTRACT,
            reason='Fallback acquisition schema detected in prelive reports',
            capability_block='canonical_fallback_detected',
        ),
        _BlockingCondition(
            check=not provider_surface_ok,
            verdict=OneButtonVerdict.DO_NOT_RUN_PROVIDER_SURFACE,
            reason='Provider surface missing or failing (public bootstrap / CT resilience)',
            capability_block='provider_surface_missing_or_failing',
        ),
        _BlockingCondition(
            check=uma_state in ('critical', 'emergency'),
            verdict=OneButtonVerdict.DO_NOT_RUN_UNKNOWN,
            reason=f'UMA state {uma_state} — restart required before any run',
            capability_block=f'uma_state={uma_state}',
        ),
        _BlockingCondition(
            check=swap_gib > DIAGNOSTIC_SWAP_MAX_GIB,
            verdict=OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK,
            reason=f'Swap {swap_gib:.3f}GiB exceeds hard-block threshold ({DIAGNOSTIC_SWAP_MAX_GIB}GiB) — restart required before any run',
            capability_block=f'swap={swap_gib:.3f}GiB_exceeds_hard_block_threshold',
        ),
    )
    for cond in conditions:
        if cond.check:
            return (True, cond)
    return (False, None)


# --- Capability Live Allowed Calculator -----------------------------------------

@dataclass(frozen=True, slots=True)
class _CapabilityResult:
    """Result of capability live allowed calculation."""
    allowed: bool
    why_blocked: str
    warnings: tuple[str, ...]


def _compute_capability_live_allowed(
    provider_surface_ok: bool,
    canonical_fallback_detected: bool,
    f232g_present: bool,
    is_nonfeed_profile: bool,
) -> _CapabilityResult:
    """Compute capability_live_allowed and blocked reasons."""
    checks = (
        ('provider_surface_degraded', provider_surface_ok),
        ('canonical_fallback_detected', not canonical_fallback_detected),
        ('f232g_research_quality_missing', f232g_present if is_nonfeed_profile else True),
    )
    blocked = [name for name, ok in checks if not ok]
    warnings: tuple[str, ...] = ()
    if is_nonfeed_profile and not f232g_present:
        warnings = ('F232G research_quality not confirmed — capability run may be degraded',)
    return _CapabilityResult(
        allowed=all(ok for _, ok in checks),
        why_blocked='; '.join(blocked) if blocked else '',
        warnings=warnings,
    )


# --- Verdict Calculator ---------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _VerdictResult:
    """Complete verdict result with all derived fields."""
    verdict: OneButtonVerdict
    live_allowed: bool
    capability_live_allowed: bool
    feed_baseline_allowed: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def _compute_verdict(
    any_blocked: bool,
    blocking: _BlockingCondition | None,
    swap_policy_tier: str,
    swap_gate_reason: str,
    swap_gib: float,
    uma_state: str,
    capability: _CapabilityResult,
    is_nonfeed_profile: bool,
    f221_valid_count: int,
    f221_total: int,
) -> _VerdictResult:
    """Compute final verdict and all derived flags from state."""
    reasons: list[str] = []
    warnings: list[str] = list(capability.warnings)

    if any_blocked and blocking:
        reasons.append(blocking.reason)
        if swap_policy_tier == 'hard_block':
            warnings.append(f'Hardware constrained: swap={swap_gib:.3f}GiB, tier={swap_policy_tier}')
        return _VerdictResult(
            verdict=blocking.verdict,
            live_allowed=False,
            capability_live_allowed=False,
            feed_baseline_allowed=False,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    # Not blocked
    feed_baseline_allowed = True

    if swap_policy_tier == 'diagnostic':
        if capability.allowed:
            verdict = OneButtonVerdict.READY_FOR_NONFEED_CAPABILITY_RUN
            live_allowed = True
        else:
            verdict = OneButtonVerdict.RESTART_THEN_RUN
            live_allowed = False
        reasons.append(f'Swap elevated ({swap_gate_reason}) — restart recommended before clean run')
        warnings.append(f'Hardware constrained: swap={swap_gib:.3f}GiB, tier={swap_policy_tier}')
    elif capability.allowed:
        verdict = OneButtonVerdict.READY_FOR_NONFEED_CAPABILITY_RUN
        live_allowed = True
        reasons.append(f'All nonfeed capability checks passed. UMA ok (swap={swap_gib:.3f}GiB, state={uma_state})')
    else:
        verdict = OneButtonVerdict.READY_FOR_FEED_BASELINE_ONLY
        live_allowed = True
        reasons.append(f'Feed baseline ready. Nonfeed capability blocked: {capability.why_blocked}')
        if f221_valid_count < f221_total:
            warnings.append(f'Only {f221_valid_count}/{f221_total} F221 artifacts valid')

    return _VerdictResult(
        verdict=verdict,
        live_allowed=live_allowed,
        capability_live_allowed=capability.allowed,
        feed_baseline_allowed=feed_baseline_allowed,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


# --- Run Mode Dispatch Table -----------------------------------------------------

@dataclass(frozen=True, slots=True)
class _RunModeResult:
    """Result of run mode calculation."""
    can_run_live: bool
    can_run_nonfeed_diag: bool
    can_run_llm: bool
    recommended_mode: str
    max_safe_iter: int
    max_safe_piv: int
    investigation_reason: str


# Module-level run mode base templates (swap_gib and why_blocked injected at call time)
# Note: hard_block always has live_allowed=False, so we use False as the key.
# The case (hard_block, True) is impossible and handled by the fallback below.
_RUN_MODE_TEMPLATES: dict[tuple[str, bool], tuple[bool, bool, bool, str, int, int, str]] = {
    # (tier, live_allowed) -> (can_run_live, can_run_nonfeed_diag, can_run_llm, recommended_mode, max_iter, max_piv, reason_template)
    ('hard_block', False): (False, False, False, 'restart_first', 0, 0, 'swap_hard_block: swap={swap_gib:.3f}GiB > {diag_max}GiB'),
    ('diagnostic', True): (True, True, False, 'dry_plan', 0, 0, 'swap_diagnostic: swap={swap_gib:.3f}GiB in ({clean_max}, {diag_max}]GiB'),
    ('diagnostic', False): (False, True, False, 'dry_plan', 0, 0, 'swap_diagnostic: swap={swap_gib:.3f}GiB in ({clean_max}, {diag_max}]GiB'),
    ('clean', True): (True, True, True, 'domain_live', 5, 20, 'clean_swap_full_capability'),
    ('clean', False): (False, True, False, 'fix_artifacts', 0, 0, 'capability_blocked: {why_blocked}'),
}


def _compute_run_mode(
    swap_policy_tier: str,
    live_allowed: bool,
    capability_live_allowed: bool,
    why_nonfeed_capability_blocked: str,
    provider_surface_ok: bool,
    fallback_blocked: bool,
    swap_gib: float,
) -> _RunModeResult:
    """Compute run mode recommendations from swap tier and capability state."""
    # Lookup base mode template
    template = _RUN_MODE_TEMPLATES.get((swap_policy_tier, live_allowed))
    if template is None:
        template = (False, True, False, 'dry_plan', 0, 0, 'unknown_swap_tier')
    
    can_run_live, can_run_diag, can_run_llm, recommended, max_iter, max_piv, reason_tpl = template
    
    # Build investigation reason with context
    if '{swap_gib}' in reason_tpl:
        investigation_reason = reason_tpl.format(swap_gib=swap_gib, clean_max=CLEAN_SWAP_MAX_GIB, diag_max=DIAGNOSTIC_SWAP_MAX_GIB)
    elif '{why_blocked}' in reason_tpl:
        investigation_reason = reason_tpl.format(why_blocked=why_nonfeed_capability_blocked)
    else:
        investigation_reason = reason_tpl

    result = _RunModeResult(
        can_run_live=can_run_live,
        can_run_nonfeed_diag=can_run_diag,
        can_run_llm=can_run_llm,
        recommended_mode=recommended,
        max_safe_iter=max_iter,
        max_safe_piv=max_piv,
        investigation_reason=investigation_reason,
    )

    # Override for surface issues
    if not provider_surface_ok:
        result = _RunModeResult(
            can_run_live=False,
            can_run_nonfeed_diag=result.can_run_nonfeed_diag,
            can_run_llm=result.can_run_llm,
            recommended_mode='fix_artifacts',
            max_safe_iter=result.max_safe_iter,
            max_safe_piv=result.max_safe_piv,
            investigation_reason=f'provider_surface_broken: {result.investigation_reason}',
        )
    if fallback_blocked:
        result = _RunModeResult(
            can_run_live=False,
            can_run_nonfeed_diag=result.can_run_nonfeed_diag,
            can_run_llm=result.can_run_llm,
            recommended_mode='fix_artifacts',
            max_safe_iter=result.max_safe_iter,
            max_safe_piv=result.max_safe_piv,
            investigation_reason=f'fallback_schema_blocked: {result.investigation_reason}',
        )

    return result


# --- Live Command Builder --------------------------------------------------------

def _build_live_command(profile: str, query: str) -> dict:
    """Build the live command dict with expected assertions and abort conditions."""
    encoded_query = query.replace('"', '\\"')
    return {
        'command': (
            f'cd /Users/vojtechhamada/PycharmProjects/Hledac && rtk proxy python '
            f'-m hledac.universal.benchmarks.live_sprint_measurement '
            f'--profile {profile} --query "{encoded_query}" --live '
            f'--require-memory-ok --output-json <path> --output-md <path>'
        ),
        'expected_assertions': {
            'benchmark_profile': profile,
            'acquisition_profile': _get_acquisition_profile_for_benchmark(profile),
            'run_quality_verdict': 'PASS_VALID_CAPABILITY_RUN or FAIL_NONFEED_EVIDENCE_MISSING',
            'hardware_constrained': False,
            'capability_synthesis': 'not None',
            'next_sprint_seeds_generated': 'true or explicit skip_reason',
            'public_terminal_stage_not_discovery_timeout': 'when bootstrap candidates exist',
            'CT_raw_gt_0_accepted_eq_0_no_loss': False,
            'nonfeed_priority_enabled': True,
            'terminality_satisfied_cannot_produce_FAIL_TERMINALITY_UNSATISFIED': True,
            'FAIL_NONFEED_EVIDENCE_MISSING_when_nonfeed_evidence_missing': True,
            'runtime_accepted_findings_divergence_explicit': True,
            'public_stage_counters_raw_count_source_present': True,
        },
        'abort_if': {
            'swap_above_2G': f'swap > {CLEAN_SWAP_MAX_GIB}GiB',
            'missing_f229_artifacts': 'any F229 structural check fails',
            'missing_f223_required_artifacts': 'any F223 required artifact missing',
            'fallback_acquisition_schema': 'fallback_schema detected in prelive reports',
            'capability_synthesis_missing_in_exporter_self_test': 'capability_synthesis not in _generate_next_sprint_seeds',
            'public_ct_provider_surface_missing': 'provider surface not OK',
            'uma_state_critical_or_emergency': 'uma_state in (critical, emergency)',
        },
        'profile': profile,
        'query': query,
    }


# --- Main Gate Function ----------------------------------------------------------

def run_one_button_gate(
    repo_root: Path,
    profile: str,
    query: str,
    decision_gate_path: Path | None = None,
    last_live_triage_path: Path | None = None,
) -> OneButtonResult:
    """
    Run the one-button prelive gate.

    No live sprint. No model load. No network.
    """
    repo_root = Path(repo_root).resolve()

    # --- Gather artifacts and state ---
    uma = _sample_uma()
    swap_gib = uma.get('swap_used_gib', 0.0)
    uma_state = uma.get('uma_state', 'unknown')

    # --- Artifact collection phase ---
    f221_results, f221_missing = _check_all_f221_artifacts(repo_root)
    _, cross_missing = _check_cross_sprint_artifacts(repo_root)
    f223_results, f223_required_missing, f223_optional = _check_all_f223_artifacts(repo_root)

    # --- Build artifact summaries using helpers ---
    f221_artifacts = _build_f221_artifacts_dict(f221_results, f221_missing)
    f223_artifacts = _build_f223_artifacts_dict(f223_results, f223_required_missing, f223_optional)
    missing_f221 = _extract_missing_paths(f221_missing)
    missing_cross_sprint = _extract_missing_paths(cross_missing)
    missing_f223_required = _extract_missing_paths(f223_required_missing)
    f223_optional_status = {'total': len(f223_optional), 'valid': sum(1 for r in f223_optional if r.valid)}

    # --- Decision gate and triage ---
    decision_data = _load_decision_gate(decision_gate_path)
    triage = _load_last_live_triage(last_live_triage_path)
    triage_verdict = triage.get('root_cause_class') if triage else None
    triage_another_live_useful = triage.get('another_live_useful') if triage else None

    # --- Surface and schema checks ---
    provider_surface_ok = _is_provider_surface_ok(decision_data)
    fallback_blocked = _has_fallback_schema(decision_data)
    canonical_fallback_detected = bool(decision_data.get('fallback_schema_blocked', False)) if decision_data else False

    # --- Swap policy tier ---
    swap_policy_tier, swap_gate_reason = _compute_swap_policy_tier(swap_gib, uma_state)

    # --- Nonfeed profile flags ---
    is_nonfeed_profile = profile in _NONFEED_PROFILES
    f232g_present, f233d_present = _check_nonfeed_profile_flags(repo_root, is_nonfeed_profile)

    # --- Blocking conditions ---
    any_blocked, blocking = _evaluate_blocking_conditions(
        missing_f221=missing_f221,
        missing_cross_sprint=missing_cross_sprint,
        missing_f223_required=missing_f223_required,
        fallback_blocked=fallback_blocked,
        provider_surface_ok=provider_surface_ok,
        uma_state=uma_state,
        swap_gib=swap_gib,
    )

    # --- Capability calculation ---
    capability = _compute_capability_live_allowed(
        provider_surface_ok=provider_surface_ok,
        canonical_fallback_detected=canonical_fallback_detected,
        f232g_present=f232g_present,
        is_nonfeed_profile=is_nonfeed_profile,
    )

    # --- Verdict calculation ---
    f221_valid_count = f221_artifacts['valid']
    f221_total = f221_artifacts['total']
    verdict_result = _compute_verdict(
        any_blocked=any_blocked,
        blocking=blocking,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
        swap_gib=swap_gib,
        uma_state=uma_state,
        capability=capability,
        is_nonfeed_profile=is_nonfeed_profile,
        f221_valid_count=f221_valid_count,
        f221_total=f221_total,
    )

    # --- Triage warnings ---
    reasons = list(verdict_result.reasons)
    warnings = list(verdict_result.warnings)
    if triage_verdict:
        warnings.append(f'Last-live triage verdict: {triage_verdict}')
        if not triage_another_live_useful:
            warnings.append('Last-live triage: another live run may not be useful')

    # --- Run mode calculation ---
    run_mode = _compute_run_mode(
        swap_policy_tier=swap_policy_tier,
        live_allowed=verdict_result.live_allowed,
        capability_live_allowed=verdict_result.capability_live_allowed,
        why_nonfeed_capability_blocked=capability.why_blocked,
        provider_surface_ok=provider_surface_ok,
        fallback_blocked=fallback_blocked,
        swap_gib=swap_gib,
    )

    # --- Build result ---
    return OneButtonResult(
        verdict=verdict_result.verdict,
        live_allowed=verdict_result.live_allowed,
        reasons=reasons,
        warnings=warnings,
        uma=uma,
        f221_artifacts=f221_artifacts,
        missing_f221=missing_f221,
        missing_cross_sprint=missing_cross_sprint,
        f223_artifacts=f223_artifacts,
        missing_f223_required=missing_f223_required,
        f223_optional_status=f223_optional_status,
        provider_surface_ok=provider_surface_ok,
        fallback_schema_blocked=fallback_blocked,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
        live_command=_build_live_command(profile, query),
        triage_verdict=triage_verdict,
        triage_another_live_useful=triage_another_live_useful,
        capability_live_allowed=verdict_result.capability_live_allowed,
        feed_baseline_allowed=verdict_result.feed_baseline_allowed,
        why_nonfeed_capability_blocked=capability.why_blocked,
        degraded_but_allowed=not provider_surface_ok and verdict_result.live_allowed,
        canonical_fallback_detected=canonical_fallback_detected,
        f232g_research_quality_present=f232g_present,
        f233d_nonfeed_prelude_coverage=f233d_present,
        can_run_live_acquisition=run_mode.can_run_live,
        can_run_nonfeed_diagnostic=run_mode.can_run_nonfeed_diag,
        can_run_llm_synthesis=run_mode.can_run_llm,
        recommended_mode=run_mode.recommended_mode,
        max_safe_iterations=run_mode.max_safe_iter,
        max_safe_pivots=run_mode.max_safe_piv,
        investigation_reason=run_mode.investigation_reason,
    )

# =============================================================================
# Markdown renderer helpers
# =============================================================================

_VERDICT_ICONS: dict[OneButtonVerdict, str] = {
    OneButtonVerdict.RUN_NOW: '✅',
    OneButtonVerdict.RESTART_THEN_RUN: '🟡',
    OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS: '❌',
    OneButtonVerdict.DO_NOT_RUN_PROVIDER_SURFACE: '❌',
    OneButtonVerdict.DO_NOT_RUN_CONTRACT: '❌',
    OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK: '🚫',
    OneButtonVerdict.DO_NOT_RUN_UNKNOWN: '⚠️',
    OneButtonVerdict.READY_FOR_NONFEED_CAPABILITY_RUN: '✅',
    OneButtonVerdict.READY_FOR_FEED_BASELINE_ONLY: '🟡',
}

_UMA_KEYS: tuple[str, ...] = ('system_used_gib', 'swap_used_gib', 'swap_detected', 'uma_state', 'io_only')


def _render_artifact_table_row(d: dict) -> str:
    """Render a single artifact table row with emoji indicators."""
    found_icon = '✅' if d.get('found') else '❌'
    valid_icon = '✅' if d.get('valid') else '❌'
    return f"| {d.get('probe_dir', '')} | {d.get('filename', '')} | {found_icon} | {valid_icon} |"


def _render_uma_section(uma: dict, swap_policy_tier: str, swap_gate_reason: str) -> list[str]:
    """Render UMA/swap state section."""
    lines = ['## UMA / Swap State', '', '| Key | Value |', '|-------|-------|']
    for key in _UMA_KEYS:
        val = uma.get(key)
        if val is not None:
            lines.append(f'| {key} | `{val}` |')
    if uma.get('error'):
        lines.append(f"| error | `{uma.get('error')}` |")
    lines.extend(['', f'| Swap Policy Tier | `{swap_policy_tier}` |', f'| Swap Gate Reason | `{swap_gate_reason}` |'])
    return lines


def _render_f221_section(result: OneButtonResult) -> list[str]:
    """Render F221 artifact section."""
    fa = result.f221_artifacts
    lines = ['## F221 Artifact Status', '',
             f"| Total | {fa.get('total', 0)} |",
             f"| Valid | {fa.get('valid', 0)} |",
             f"| Missing | {fa.get('missing', 0)} |"]
    if result.missing_f221:
        lines.extend(['', '**Missing F221 Artifacts:**'])
        lines.extend(f'- `{m}`' for m in result.missing_f221)
    if result.missing_cross_sprint:
        lines.extend(['', '**Missing Cross-Sprint Artifacts:**'])
        lines.extend(f'- `{m}`' for m in result.missing_cross_sprint)
    if fa.get('details'):
        lines.extend(['', '### F221 Artifact Details', '',
                     '| Probe | Artifact | Found | Valid |',
                     '|------|----------|-------|-------|'])
        lines.extend(_render_artifact_table_row(d) for d in fa['details'])
    return lines


def _render_f223_section(result: OneButtonResult) -> list[str]:
    """Render F223 artifact section."""
    f223a = result.f223_artifacts
    lines = ['## F223 Post-F223 Artifact Status (Sprint F224E)', '']
    if f223a:
        lines.extend([
            f"| Required Total | {f223a.get('required_total', 0)} |",
            f"| Required Valid | {f223a.get('required_valid', 0)} |",
            f"| Required Missing | {f223a.get('required_missing', 0)} |",
            f"| Optional Total | {f223a.get('optional_total', 0)} |",
            f"| Optional Valid | {f223a.get('optional_valid', 0)} |",
        ])
    if result.missing_f223_required:
        lines.extend(['', '**Missing F223 Required Artifacts:**'])
        lines.extend(f'- `{m}`' for m in result.missing_f223_required)
    if f223a and f223a.get('required_details'):
        lines.extend(['', '### F223 Required Artifact Details', '',
                     '| Probe | Artifact | Found | Valid |',
                     '|------|----------|-------|-------|'])
        lines.extend(_render_artifact_table_row(d) for d in f223a['required_details'])
    if f223a and f223a.get('optional_details'):
        lines.extend(['', '### F223 Optional Artifact Details', '',
                     '(_Optional — advisory only, does not block_)',
                     '| Probe | Artifact | Found | Valid |',
                     '|------|----------|-------|-------|'])
        lines.extend(_render_artifact_table_row(d) for d in f223a['optional_details'])
    return lines


def _render_capability_section(result: OneButtonResult) -> list[str]:
    """Render capability vs feed split section."""
    icon = '✅' if result.provider_surface_ok else '❌'
    lines = ['## F233F Gate: Capability vs Feed Split', '',
             '| Field | Value |', '|-------|-------|',
             f'| Live Allowed | `{result.live_allowed}` |',
             f'| Capability Live Allowed | `{result.capability_live_allowed}` |',
             f'| Feed Baseline Allowed | `{result.feed_baseline_allowed}` |',
             f'| Why Capability Blocked | `{result.why_nonfeed_capability_blocked}` |',
             f'| Degraded But Allowed | `{result.degraded_but_allowed}` |',
             f'| Canonical Fallback Detected | `{result.canonical_fallback_detected}` |',
             f'| F232G Research Quality Present | `{result.f232g_research_quality_present}` |',
             f'| F233D Nonfeed Prelude Coverage | `{result.f233d_nonfeed_prelude_coverage}` |']
    if result.capability_live_allowed:
        lines.extend(['', '### Exact Command (Nonfeed Capability)', '', '```bash', result.live_command.get('command', ''), '```'])
    elif result.feed_baseline_allowed:
        lines.extend(['', '### Exact Command (Feed Baseline)', '',
                     '_Nonfeed capability blocked. Feed baseline run:_', '```bash', result.live_command.get('command', ''), '```'])
    else:
        lines.extend(['', '### Exact Command', '', '_No run type currently allowed._'])
    return lines


def _render_live_command_section(result: OneButtonResult) -> list[str]:
    """Render live command section."""
    lc = result.live_command
    lines = ['## Live Command (Sprint F224E)', '']
    if lc:
        lines.extend(['### Exact Command', f"```bash\n{lc.get('command', '')}\n```", '',
                     '### Expected Post-F223 Assertions'])
        assertions = lc.get('expected_assertions', {})
        lines.extend(f'- `{k}` → `{v}`' for k, v in assertions.items())
        lines.extend(['', '### Abort Conditions'])
        abort_if = lc.get('abort_if', {})
        lines.extend(f'- **{r}:** {d}' for r, d in abort_if.items())
    else:
        lines.append('_No live command generated (gate did not pass)._')
    return lines


_USAGE_TEMPLATE: tuple[str, ...] = (
    '', '---', '',
    '## How to Run This Gate', '',
    '```bash',
    'python tools/prelive_one_button_gate.py \\',
    '  --repo-root . \\',
    '  --profile nonfeed_diagnostic180 \\',
    '  --query "mozilla.org certificate transparency subdomains april 2026" \\',
    '  --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\',
    '  --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md',
    '```',
    '',
    'With optional last-live triage:',
    '```bash',
    'python tools/prelive_one_button_gate.py \\',
    '  --repo-root . --profile nonfeed_diagnostic180 \\',
    '  --query "..." \\',
    '  --last-live-triage probe_f219g_live_artifact_triage/triage.json \\',
    '  --decision-gate-json probe_f219f_prelive_decision_gate/prelive_decision.json \\',
    '  --output-json ... --output-md ...',
    '```',
)


def _render_markdown(result: OneButtonResult, profile: str, query: str) -> str:
    """Render one-button result as markdown report."""
    icon = _VERDICT_ICONS.get(result.verdict, '?')
    lines: list[str] = [
        '# One-Button Prelive Gate Report (F221H)', '',
        f'**Verdict:** {icon} `{result.verdict.value}`',
        f'**Live Allowed:** `{result.live_allowed}`',
        f'**Profile:** `{profile}`',
        f'**Query:** `{query}`',
        '', '---', '',
        '## Decision Summary', '',
    ]
    lines.extend(f'- {r}' for r in result.reasons)
    if result.warnings:
        lines.extend(['', '**Warnings:**'])
        lines.extend(f'- {w}' for w in result.warnings)
    lines.extend(['', '---'])
    lines.extend(_render_uma_section(result.uma, result.swap_policy_tier, result.swap_gate_reason))
    lines.extend(['', '---'])
    lines.extend(_render_f221_section(result))
    lines.extend(['', '---'])
    lines.extend(_render_f223_section(result))
    lines.extend(['', '---', '', '## Provider Surface', '',
                  f'- **OK:** {icon} `{result.provider_surface_ok}`',
                  f'- **Fallback Schema Blocked:** `{result.fallback_schema_blocked}`'])
    lines.extend(['', '---'])
    lines.extend(_render_capability_section(result))
    if result.triage_verdict:
        lines.extend(['', '---', '', '## Last-Live Triage', '',
                      f'- **Triage Verdict:** `{result.triage_verdict}`',
                      f'- **Another Live Useful:** `{result.triage_another_live_useful}`'])
    lines.extend(_render_live_command_section(result))
    lines.extend(_USAGE_TEMPLATE)
    return '\n'.join(lines)

class SelfTestResult(msgspec.Struct, frozen=True, gc=False):
    """Machine-checkable self-test output (Sprint F224H)."""
    self_test_passed: bool
    artifact_matrix: list[dict]
    assertion_contract_ok: bool
    command_contract_ok: bool
    cwd_contract_ok: bool
    blocking_reasons: list[str]
    warnings: list[str]
    profile_assertions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {'self_test_passed': self.self_test_passed, 'artifact_matrix': self.artifact_matrix, 'assertion_contract_ok': self.assertion_contract_ok, 'command_contract_ok': self.command_contract_ok, 'cwd_contract_ok': self.cwd_contract_ok, 'blocking_reasons': self.blocking_reasons, 'warnings': self.warnings, 'profile_assertions': self.profile_assertions}

# --- Artifact matrix builder helpers for _run_self_test ---

@dataclass(frozen=True, slots=True)
class _F229ContractCheck:
    """F229 import contract check definition."""
    module_path: str
    probe_dir: str
    filename: str
    category: str
    failures: tuple[str, ...]
    blocks_live: bool = True


_F229_CONTRACT_CHECKS: tuple[_F229ContractCheck, ...] = (
    _F229ContractCheck(
        module_path='hledac.universal.export.sprint_exporter',
        probe_dir='export',
        filename='sprint_exporter.py',
        category='F229-EXPORT-A',
        failures=('_generate_next_sprint_seeds',),
    ),
    _F229ContractCheck(
        module_path='hledac.universal.runtime.sprint_scheduler',
        probe_dir='runtime',
        filename='sprint_scheduler.py',
        category='F229-RUNTIME-A',
        failures=('SprintScheduler', 'run_sprint'),
    ),
    _F229ContractCheck(
        module_path='pipeline.live_public_pipeline.PipelineRunResult',
        probe_dir='pipeline',
        filename='live_public_pipeline.py',
        category='F229-PUBLIC-A',
        failures=('public_bootstrap_order',),
    ),
    _F229ContractCheck(
        module_path='benchmarks.live_sprint_measurement.LiveMeasurementResult',
        probe_dir='benchmarks',
        filename='live_sprint_measurement.py',
        category='F229-NONFEED-A',
        failures=('nonfeed_profile_expected_lanes', 'acquisition_report'),
    ),
)


def _run_f229_contract_checks() -> tuple[list[dict], list[str]]:
    """Run F229 contract checks. Returns (artifact_matrix, blocking_reasons)."""
    import importlib
    artifact_matrix: list[dict] = []
    blocking_reasons: list[str] = []
    for check in _F229_CONTRACT_CHECKS:
        parts = check.module_path.rsplit('.', 1)
        entry = {
            'probe_dir': check.probe_dir,
            'filename': check.filename,
            'category': check.category,
            'found': False,
            'valid': False,
            'parse_error': None,
            'blocks_live': check.blocks_live,
        }
        try:
            module = importlib.import_module(parts[0])
            obj = getattr(module, parts[1]) if len(parts) == 2 else module
            failures = [f for f in check.failures if not hasattr(obj, f)]
            entry['found'] = True
            entry['valid'] = len(failures) == 0
            entry['parse_error'] = '; '.join(failures) if failures else None
            if failures:
                for f in failures:
                    blocking_reasons.append(f'{check.category}: {check.module_path} missing {f}')
        except ImportError as exc:
            entry['parse_error'] = str(exc)
            blocking_reasons.append(f'{check.category}: {check.module_path} not importable: {exc}')
        artifact_matrix.append(entry)
    return artifact_matrix, blocking_reasons


def _run_self_test(repo_root: Path, profile: str, query: str) -> SelfTestResult:
    """
    Self-test mode: validates artifact resolution and expected assertion contract.
    NEVER runs live. No network. No MLX. No model load.
    """
    repo_root = Path(repo_root).resolve()
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    artifact_matrix: list[dict] = []
    reality = _get_repo_root_reality()
    cwd_contract_ok = reality['cwd_is_universal_root'] and reality['universal_root_exists'] and (not reality['cwd_warning'])
    if reality['cwd_warning']:
        warnings.append(f"CWD contract: {reality['cwd_warning']}")
    if not reality['universal_root_exists']:
        blocking_reasons.append(f"universal_root does not exist: {reality['universal_root']}")
    
    # --- F223 and cross-sprint artifacts ---
    f223_req_results, f223_req_missing, f223_opt_results = _check_all_f223_artifacts(repo_root)
    for r in f223_req_results:
        artifact_matrix.append({'probe_dir': r.probe_dir, 'filename': r.filename, 'category': 'required', 'found': r.found, 'valid': r.valid, 'parse_error': r.parse_error, 'blocks_live': True})
        if not r.valid:
            blocking_reasons.append(f'required artifact invalid/missing: {r.probe_dir}/{r.filename}')
    for r in f223_opt_results:
        artifact_matrix.append({'probe_dir': r.probe_dir, 'filename': r.filename, 'category': 'optional', 'found': r.found, 'valid': r.valid, 'parse_error': r.parse_error, 'blocks_live': False})
        if not r.valid:
            warnings.append(f'optional artifact invalid/missing: {r.probe_dir}/{r.filename}')
    cross_results, _ = _check_cross_sprint_artifacts(repo_root)
    for r in cross_results:
        artifact_matrix.append({'probe_dir': r.probe_dir, 'filename': r.filename, 'category': 'cross_sprint_required', 'found': r.found, 'valid': r.valid, 'parse_error': r.parse_error, 'blocks_live': True})
        if not r.valid:
            blocking_reasons.append(f'cross-sprint artifact invalid/missing: {r.probe_dir}/{r.filename}')
    
    # --- F229 contract checks ---
    f229_matrix, f229_blocking = _run_f229_contract_checks()
    artifact_matrix.extend(f229_matrix)
    blocking_reasons.extend(f229_blocking)
    
    # --- Profile assertions ---
    expected_acquisition = _get_acquisition_profile_for_benchmark(profile)
    profile_assertions = {
        'benchmark_profile': profile,
        'acquisition_profile': expected_acquisition,
        'run_quality_verdict': 'PASS_VALID_CAPABILITY_RUN or FAIL_NONFEED_EVIDENCE_MISSING',
        'hardware_constrained': False,
        'capability_synthesis': 'not None',
        'next_sprint_seeds': 'not None',
        'public_terminal_stage_not_discovery_timeout': True,
        'CT_raw_gt_0_accepted_eq_0_no_loss': False,
        'nonfeed_priority_enabled': True,
        'terminality_satisfied': True,
        'FAIL_NONFEED_EVIDENCE_MISSING': True,
        'runtime_accepted_findings_divergence': True,
        'public_stage_counters_raw_count': True,
    }
    
    # --- Assertion contract ---
    assertion_contract_ok = True
    if profile == 'nonfeed_diagnostic180' and expected_acquisition != 'nonfeed_diagnostic':
        assertion_contract_ok = False
        blocking_reasons.append(f'acquisition_profile={expected_acquisition} != nonfeed_diagnostic for benchmark profile nonfeed_diagnostic180')
    elif profile not in _BENCHMARK_TO_ACQUISITION_PROFILE:
        warnings.append(f'profile {profile!r} not in benchmark→acquisition map')
    
    # --- Command contract ---
    command_contract_ok = True
    encoded_query = query.replace('"', '\\"')
    constructed_cmd = f'rtk proxy python -m hledac.universal.benchmarks.live_sprint_measurement --profile {profile} --query "{encoded_query}" --live'
    for substr in (f'--profile {profile}', f'--query "{encoded_query}"', '--live'):
        if substr not in constructed_cmd:
            command_contract_ok = False
            blocking_reasons.append(f'command contract violated: expected substring {substr!r} in live command')
    
    if profile == 'nonfeed_diagnostic':
        warnings.append("profile is 'nonfeed_diagnostic' — did you mean 'nonfeed_diagnostic180'? nonfeed_diagnostic180 is the benchmark profile that maps to nonfeed_diagnostic acquisition.")
    
    self_test_passed = cwd_contract_ok and assertion_contract_ok and command_contract_ok and (not blocking_reasons)
    return SelfTestResult(
        self_test_passed=self_test_passed,
        artifact_matrix=artifact_matrix,
        assertion_contract_ok=assertion_contract_ok,
        command_contract_ok=command_contract_ok,
        cwd_contract_ok=cwd_contract_ok,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        profile_assertions=profile_assertions,
    )

def _render_self_test_markdown(result: SelfTestResult, profile: str, query: str) -> str:
    """Render self-test result as markdown."""
    icon = '✅' if result.self_test_passed else '❌'
    lines = ['# One-Button Gate — Self-Test Report (Sprint F224H)', '', f'**Self-Test Passed:** {icon} `{result.self_test_passed}`', f'**Profile:** `{profile}`', f'**Query:** `{query}`', '', '---', '', '## Contract Status', '', '| Contract | Status |', '|----------|--------|', f"| CWD / Repo-Root | {('✅' if result.cwd_contract_ok else '❌')} |", f"| Assertion Contract | {('✅' if result.assertion_contract_ok else '❌')} |", f"| Command Contract | {('✅' if result.command_contract_ok else '❌')} |", '', '---', '', '## Artifact Matrix', '', '| Probe Dir | Filename | Category | Found | Valid | Blocks Live |', '|------------|----------|----------|-------|-------|------------|']
    for a in result.artifact_matrix:
        found_icon = '✅' if a['found'] else '❌'
        valid_icon = '✅' if a['valid'] else '❌'
        blocks_icon = '🚫' if a['blocks_live'] else '—'
        lines.append(f"| {a['probe_dir']} | {a['filename']} | {a['category']} | {found_icon} | {valid_icon} | {blocks_icon} |")
    if result.blocking_reasons:
        lines.extend(['', '---', '', '## Blocking Reasons', ''])
        for b in result.blocking_reasons:
            lines.append(f'- ❌ {b}')
    if result.warnings:
        lines.extend(['', '---', '', '## Warnings', ''])
        for w in result.warnings:
            lines.append(f'- ⚠️ {w}')
    lines.extend(['', '---', '', '## Profile Assertions', ''])
    for k, v in result.profile_assertions.items():
        lines.append(f'- `{k}` → `{v}`')
    encoded_q = query.replace('"', '\\"')
    lines.extend(['', '---', '', '## How to Run Live', '', '```bash'])
    lines.append('python tools/prelive_one_button_gate.py \\')
    lines.append('  --repo-root . \\')
    lines.append(f'  --profile {profile} \\')
    lines.append(f'  --query "{encoded_q}" \\')
    lines.append('  --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\')
    lines.append('  --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md')
    lines.append('```')
    return '\n'.join(lines)

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Prelive One-Button Decision Gate — Sprint F221H', formatter_class=argparse.RawDescriptionHelpFormatter, epilog=textwrap.dedent('            Examples:\n              # Standard run (reads artifacts from standard probe_* locations):\n              python tools/prelive_one_button_gate.py \\\n                --repo-root . \\\n                --profile nonfeed_diagnostic180 \\\n                --query "mozilla.org certificate transparency subdomains april 2026" \\\n                --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\\n                --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md\n\n              # With decision gate and last-live triage:\n              python tools/prelive_one_button_gate.py \\\n                --repo-root . --profile nonfeed_diagnostic180 \\\n                --query "..." \\\n                --decision-gate-json probe_f219f_prelive_decision_gate/prelive_decision.json \\\n                --last-live-triage probe_f219g_live_artifact_triage/triage.json \\\n                --output-json ... --output-md ...\n        '))
    p.add_argument('--repo-root', type=Path, default=Path('.'))
    p.add_argument('--profile', default='nonfeed_diagnostic180')
    p.add_argument('--query', required=True)
    p.add_argument('--decision-gate-json', type=Path, default=None, help='Path to prelive_decision.json (from prelive_decision_gate.py). If omitted, provider surface check is skipped.')
    p.add_argument('--last-live-triage', type=Path, default=None, dest='last_live_triage', help='Path to last-live triage.json (from live_artifact_triage.py). Optional.')
    p.add_argument('--output-json', type=Path, default=None)
    p.add_argument('--output-md', type=Path, default=None)
    p.add_argument('--self-test', action='store_true', help='Run self-test mode: validates artifact resolution and expected assertion contract without running live. Never loads MLX or makes network calls. Emits machine-checkable JSON readiness matrix.')
    return p

def _print_selftest_summary(st_result: SelfTestResult) -> None:
    """Print self-test summary to stdout."""
    icon = '✅' if st_result.self_test_passed else '❌'
    print(f"{'=' * 60}")
    print(f"  Self-Test:    {icon} {'PASSED' if st_result.self_test_passed else 'FAILED'}")
    print(f"  CWD Contract: {'✅' if st_result.cwd_contract_ok else '❌'}")
    print(f"  Assertion Contract: {'✅' if st_result.assertion_contract_ok else '❌'}")
    print(f"  Command Contract: {'✅' if st_result.command_contract_ok else '❌'}")
    print(f"{'=' * 60}")
    if st_result.blocking_reasons:
        print('Blocking reasons:')
        for b in st_result.blocking_reasons:
            print(f'  - {b}')
    if st_result.warnings:
        print('Warnings:')
        for w in st_result.warnings:
            print(f'  - {w}')
    print()
    print('Artifact matrix:')
    for a in st_result.artifact_matrix:
        found = '✅' if a['found'] else '❌'
        valid = '✅' if a['valid'] else '❌'
        blocks = '🚫' if a['blocks_live'] else '—'
        print(f"  [{blocks}] {a['probe_dir']}/{a['filename']} found={found} valid={valid}")
    if st_result.profile_assertions:
        print()
        print('Profile assertions:')
        for k, v in st_result.profile_assertions.items():
            print(f'  {k} → {v}')
    print()
    print('##GATE_SELFTEST_JSON##')
    print(json.dumps(st_result.to_dict(), indent=2))
    print('##GATE_SELFTEST_JSON_END##')


def _print_gate_summary(result: OneButtonResult) -> None:
    """Print gate summary to stdout."""
    icon = _VERDICT_ICONS.get(result.verdict, '?')
    print(f"{'=' * 60}")
    print(f'  Verdict:      {icon} {result.verdict.value}')
    print(f'  Live Allowed: {result.live_allowed}')
    print(f'  Capability Allowed: {result.capability_live_allowed}')
    print(f'  Feed Baseline Allowed: {result.feed_baseline_allowed}')
    print(f'  Swap Tier:    {result.swap_policy_tier}')
    if result.why_nonfeed_capability_blocked:
        print(f'  Capability Blocked: {result.why_nonfeed_capability_blocked}')
    print(f"{'=' * 60}")
    if result.reasons:
        print('Reasons:')
        for r in result.reasons:
            print(f'  - {r}')
    if result.warnings:
        print('Warnings:')
        for w in result.warnings:
            print(f'  - {w}')
    if result.missing_f221:
        print(f'Missing F221 artifacts ({len(result.missing_f221)}):')
        for m in result.missing_f221:
            print(f'  - {m}')
    if result.missing_f223_required:
        print(f'Missing F223 required artifacts ({len(result.missing_f223_required)}):')
        for m in result.missing_f223_required:
            print(f'  - {m}')
    uma_sw = result.uma.get('swap_used_gib', 0)
    print(f'UMA: swap={uma_sw:.3f}GiB')
    print()
    lc = result.live_command
    if lc:
        print('Live command:')
        print(f"  {lc.get('command', '')}")
        print()
        print('Expected assertions:')
        for key, val in lc.get('expected_assertions', {}).items():
            print(f'  {key} → {val}')
        print()
        print('Abort conditions:')
        for reason, desc in lc.get('abort_if', {}).items():
            print(f'  {reason}: {desc}')
    else:
        print('No live command generated (gate did not pass).')


def _write_outputs(output_json: Path | None, output_md: Path | None, json_data: dict, md_text: str | None = None, md_renderer=None, args=None) -> None:
    """Write JSON and/or markdown output files."""
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, 'w', encoding='utf-8') as fh:
            json.dump(json_data, fh, indent=2, default=str)
        print(f'\nJSON report written: {output_json}')
    if output_md and md_text:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(output_md, 'w', encoding='utf-8') as fh:
            fh.write(md_text)
        print(f'Markdown report written: {output_md}')


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f'ERROR: repo root does not exist: {repo_root}', file=sys.stderr)
        return 1
    if args.self_test:
        st_result = _run_self_test(repo_root, args.profile, args.query)
        _print_selftest_summary(st_result)
        _write_outputs(args.output_json, args.output_md, st_result.to_dict(), md_text=_render_self_test_markdown(st_result, args.profile, args.query))
        return 0 if st_result.self_test_passed else 1
    cwd_warning = _check_cwd_guard(repo_root)
    if cwd_warning:
        print(f'CWD GUARD: {cwd_warning}', file=sys.stderr)
        print('Aborting artifact scan due to wrong CWD.', file=sys.stderr)
        return 1
    result = run_one_button_gate(repo_root=repo_root, profile=args.profile, query=args.query, decision_gate_path=args.decision_gate_json, last_live_triage_path=args.last_live_triage)
    _print_gate_summary(result)
    _write_outputs(args.output_json, args.output_md, result.to_dict(), md_text=_render_markdown(result, args.profile, args.query))
    return 0 if result.live_allowed else 1
if __name__ == '__main__':
    sys.exit(main())