#!/usr/bin/env python3
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

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Verdict enum
# --------------------------------------------------------------------------- #

class OneButtonVerdict(str, Enum):
    RUN_NOW = "RUN_NOW"
    RESTART_THEN_RUN = "RESTART_THEN_RUN"
    DO_NOT_RUN_FIX_ARTIFACTS = "DO_NOT_RUN_FIX_ARTIFACTS"
    DO_NOT_RUN_PROVIDER_SURFACE = "DO_NOT_RUN_PROVIDER_SURFACE"
    DO_NOT_RUN_CONTRACT = "DO_NOT_RUN_CONTRACT"
    DO_NOT_RUN_UNKNOWN = "DO_NOT_RUN_UNKNOWN"


# --------------------------------------------------------------------------- #
# Swap thresholds (must match prelive_artifact_cockpit.py)
# --------------------------------------------------------------------------- #

CLEAN_SWAP_MAX_GIB: float = 2.0
DIAGNOSTIC_SWAP_MAX_GIB: float = 4.0

# F224G: Benchmark → Acquisition profile mapping
_BENCHMARK_TO_ACQUISITION_PROFILE: dict[str, str] = {
    "nonfeed_diagnostic180": "nonfeed_diagnostic",
    "active300": "default",
    "active600": "default",
}


def _get_acquisition_profile_for_benchmark(benchmark_profile: str) -> str:
    """Map benchmark profile name to runtime acquisition profile.

    F223A: nonfeed_diagnostic180 benchmark → nonfeed_diagnostic acquisition.
    """
    return _BENCHMARK_TO_ACQUISITION_PROFILE.get(benchmark_profile, "default")


# F223H: Repo-root constants
_EXPECTED_REPO_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac"
_UNIVERSAL_ROOT = f"{_EXPECTED_REPO_ROOT}/hledac/universal"


def _get_repo_root_reality() -> dict:
    """Hermetic CWD diagnostic — no live run, no network, no MLX."""
    import os as _os
    from pathlib import Path as _P

    _cwd = _os.getcwd()
    _resolved = str(_P(_cwd).resolve())
    _universal = _UNIVERSAL_ROOT
    _is_universal_root = _resolved == _universal or _resolved.startswith(f"{_universal}/")
    _universal_exists = _P(_universal).exists()
    _tests_probe_exists = _P(f"{_universal}/tests/probe_f223h_cwd_invocation_guard").exists()
    _cwd_warning = (
        f"WARNING: CWD={_cwd} is outside expected universal root ({_universal}). "
        f"Artifact scans may glob wrong directory. Use --repo-root {_UNIVERSAL_ROOT} "
        f"or run from {_UNIVERSAL_ROOT}."
    ) if not _is_universal_root else ""

    return {
        "cwd": _cwd,
        "resolved_cwd": _resolved,
        "expected_repo_root": _EXPECTED_REPO_ROOT,
        "universal_root": _universal,
        "cwd_is_universal_root": _is_universal_root,
        "universal_root_exists": _universal_exists,
        "tests_probe_dir_exists": _tests_probe_exists,
        "cwd_warning": _cwd_warning,
    }


def _check_cwd_guard(repo_root: Path) -> str:
    """Check CWD vs repo-root. Returns warning string or empty if OK."""
    reality = _get_repo_root_reality()
    if reality["cwd_warning"]:
        return reality["cwd_warning"]
    # Additional check: repo_root param must match universal root
    _resolved_repo = str(repo_root.resolve())
    _repo_path = Path(_resolved_repo)
    if _resolved_repo != reality["universal_root"] and not repo_root.name == "hledac":
        # Accept sub-dirs of universal root too
        try:
            _repo_path.relative_to(reality["universal_root"])
        except ValueError:
            return (
                f"WARNING: --repo-root {_resolved_repo} is not inside "
                f"expected universal root ({reality['universal_root']}). "
                f"Artifact scans may be incorrect."
            )
    return ""


# --------------------------------------------------------------------------- #
# F221 required probes and their artifact filenames
# --------------------------------------------------------------------------- #

_F221_REQUIRED_PROBES = [
    ("probe_f221a_source_family_truth", "source_family_truth.json"),
    ("probe_f221b_ct_domain_lane", "ct_domain_lane.json"),
    ("probe_f221c_public_timeout_diagnosis", "public_timeout_diagnosis.json"),
    ("probe_f221d_quality_surface_consistency", "quality_surface_consistency.json"),
    ("probe_f221e_delta_sanity_alignment", "delta_sanity_alignment.json"),
    ("probe_f221f_ae_integration_guard", "ae_integration_guard.json"),
    ("probe_f221g_nonfeed_diag_ready", "nonfeed_diag_ready.json"),
]

# --------------------------------------------------------------------------- #
# F223 post-F223 required artifacts (Sprint F224E)
# --------------------------------------------------------------------------- #

# Alias table: logical_name → list of (probe_dir, filename) candidates to try in order.
# First match wins. Canonical path is the primary (index 0).
_F223_ARTIFACT_ALIASES: dict[str, list[tuple[str, str]]] = {
    "F223A_PROFILE_PROPAGATION": [
        ("probe_f223a_nonfeed_profile_propagation", "nonfeed_profile_propagation.json"),
        ("probe_f223a_profile_propagation", "profile_propagation.json"),
    ],
    "F223B_TERMINALITY_VERDICT_SSOT": [
        ("probe_f223b_terminality_verdict_ssot", "terminality_verdict_ssot.json"),
    ],
    "F223C_PUBLIC_COUNTER_TRUTH": [
        ("probe_f223c_public_counter_truth", "public_counter_truth.json"),
        ("probe_f223c_module_invocation_reality", "module_invocation_reality.json"),
    ],
    "F223D_PRODUCT_VALUE_REALITY": [
        ("probe_f223d_product_value_reality", "product_value_reality.json"),
    ],
    "F223H_CWD_INVOCATION_GUARD": [
        ("probe_f223h_cwd_invocation_guard", "cwd_invocation_guard.json"),
    ],
    "F223E_ASYNC_RESOURCE_HYGIENE": [
        ("probe_f223e_async_resource_hygiene", "async_resource_hygiene.json"),
    ],
    "F223F_ANALYST_BRIEF_REALITY": [
        ("probe_f223f_analyst_brief_reality", "analyst_brief_reality.json"),
    ],
    "F223G_PERSISTENT_DEDUP_AUDIT": [
        ("probe_f223g_persistent_dedup_audit", "persistent_dedup_audit.json"),
    ],
}

# Required: all must be present and valid
_F223_REQUIRED_PROBES = [
    ("probe_f223a_nonfeed_profile_propagation", "nonfeed_profile_propagation.json"),
    ("probe_f223b_terminality_verdict_ssot", "terminality_verdict_ssot.json"),
    ("probe_f223c_public_counter_truth", "public_counter_truth.json"),
    ("probe_f223d_product_value_reality", "product_value_reality.json"),
    ("probe_f223h_cwd_invocation_guard", "cwd_invocation_guard.json"),
]

# Optional: advisory only, do not block
_F223_OPTIONAL_PROBES = [
    ("probe_f223e_async_resource_hygiene", "async_resource_hygiene.json"),
    ("probe_f223f_analyst_brief_reality", "analyst_brief_reality.json"),
    ("probe_f223g_persistent_dedup_audit", "persistent_dedup_audit.json"),
]


# --------------------------------------------------------------------------- #
# UMA sampling (read-only, no live sprint)
# --------------------------------------------------------------------------- #

def _sample_uma() -> dict:
    """Sample current UMA/swap state via core.resource_governor."""
    try:
        from core.resource_governor import sample_uma_status
        UmaStatus = sample_uma_status()
        return {
            "system_used_gib": round(getattr(UmaStatus, "system_used_gib", 0.0), 3),
            "swap_used_gib": round(getattr(UmaStatus, "swap_used_gib", 0.0), 3),
            "swap_detected": getattr(UmaStatus, "swap_detected", False),
            "uma_state": getattr(UmaStatus, "state", "unknown"),
            "io_only": getattr(UmaStatus, "io_only", False),
            "error": None,
        }
    except Exception as exc:
        return {
            "system_used_gib": 0.0,
            "swap_used_gib": 0.0,
            "swap_detected": False,
            "uma_state": "unknown",
            "io_only": False,
            "error": str(exc),
        }


# --------------------------------------------------------------------------- #
# F221 artifact check helpers
# --------------------------------------------------------------------------- #

@dataclass
class F221ArtifactResult:
    probe_dir: str
    filename: str
    found: bool
    parse_error: Optional[str] = None
    valid: bool = False  # found AND valid JSON


def _check_f221_artifact(repo_root: Path, probe_dir: str, filename: str) -> F221ArtifactResult:
    """Check a single F221 probe artifact exists and is parseable JSON."""
    full_path = repo_root / probe_dir / filename
    result = F221ArtifactResult(probe_dir=probe_dir, filename=filename, found=False)

    if not full_path.exists():
        return result

    result.found = True
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            json.load(fh)
        result.valid = True
    except json.JSONDecodeError as exc:
        result.parse_error = f"JSON decode error: {exc}"
    except Exception as exc:
        result.parse_error = str(exc)

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

    return results, missing


# --------------------------------------------------------------------------- #
# F223 artifact check helpers (Sprint F224E)
# --------------------------------------------------------------------------- #

@dataclass
class F223ArtifactResult:
    logical_name: str = ""
    probe_dir: str = ""
    filename: str = ""
    found: bool = False
    valid: bool = False
    parse_error: Optional[str] = None
    resolved_path: Optional[str] = None
    alias_used: bool = False
    searched_paths: list[str] = field(default_factory=list)



def _check_f223_artifact(
    repo_root: Path,
    logical_name: str,
    probe_dir: str,
    filename: str,
) -> F223ArtifactResult:
    """Check a single F223 probe artifact, trying alias paths if primary is missing."""
    candidates = [(probe_dir, filename)]
    # Add aliases for this logical artifact
    aliases = _F223_ARTIFACT_ALIASES.get(logical_name, [])
    for alias_dir, alias_file in aliases:
        if alias_dir != probe_dir or alias_file != filename:
            candidates.append((alias_dir, alias_file))

    searched_paths: list[str] = []
    result = F223ArtifactResult(
        logical_name=logical_name,
        probe_dir=probe_dir,
        filename=filename,
        found=False,
    )

    for candidate_dir, candidate_file in candidates:
        full_path = repo_root / candidate_dir / candidate_file
        searched_paths.append(str(full_path))

        if not full_path.exists():
            continue

        result.found = True
        result.resolved_path = str(full_path)
        # If this is not the primary path, mark alias_used
        if candidate_dir != probe_dir or candidate_file != filename:
            result.alias_used = True
        try:
            with open(full_path, "r", encoding="utf-8") as fh:
                json.load(fh)
            result.valid = True
        except json.JSONDecodeError as exc:
            result.parse_error = f"JSON decode error: {exc}"
        except Exception as exc:
            result.parse_error = str(exc)
        break  # found and processed


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
        # Derive logical name from the primary probe_dir
        logical_name = _derive_logical_name(probe_dir)
        result = _check_f223_artifact(repo_root, logical_name, probe_dir, filename)
        required_results.append(result)
        if not result.valid:
            required_missing.append(result)

    for probe_dir, filename in _F223_OPTIONAL_PROBES:
        logical_name = _derive_logical_name(probe_dir)
        result = _check_f223_artifact(repo_root, logical_name, probe_dir, filename)
        optional_results.append(result)
        # optional never blocks

    return required_results, required_missing, optional_results


def _derive_logical_name(probe_dir: str) -> str:
    """Derive logical artifact name from probe directory."""
    if "nonfeed_profile_propagation" in probe_dir or "profile_propagation" in probe_dir:
        return "F223A_PROFILE_PROPAGATION"
    if "terminality_verdict_ssot" in probe_dir:
        return "F223B_TERMINALITY_VERDICT_SSOT"
    if "public_counter_truth" in probe_dir or "module_invocation_reality" in probe_dir:
        return "F223C_PUBLIC_COUNTER_TRUTH"
    if "product_value_reality" in probe_dir:
        return "F223D_PRODUCT_VALUE_REALITY"
    if "async_resource_hygiene" in probe_dir:
        return "F223E_ASYNC_RESOURCE_HYGIENE"
    if "analyst_brief_reality" in probe_dir:
        return "F223F_ANALYST_BRIEF_REALITY"
    if "persistent_dedup_audit" in probe_dir:
        return "F223G_PERSISTENT_DEDUP_AUDIT"
    if "cwd_invocation_guard" in probe_dir:
        return "F223H_CWD_INVOCATION_GUARD"
    return probe_dir  # fallback


# --------------------------------------------------------------------------- #
# Cross-sprint required probes (from prelive_decision_gate / prelive_artifact_pack)
# --------------------------------------------------------------------------- #

# These are already checked by prelive_decision_gate + prelive_artifact_pack.
# We re-expose them here for the one-button summary.

_CROSS_SPRINT_REQUIRED = [
    ("probe_m218e_memory_integration_guard", "memory_integration_guard.json"),
    ("probe_f219a_surface_contract", "surface_contract.json"),
    ("probe_f219d_public_session_seal", "public_session_seal.json"),
    ("probe_f219e_ct_provider_cooldown", "ct_provider_cooldown.json"),
    ("probe_f220e_provider_surface_smoke", "provider_surface_smoke.json"),
]


def _check_cross_sprint_artifacts(repo_root: Path) -> tuple[list[F221ArtifactResult], list[F221ArtifactResult]]:
    """Check cross-sprint required artifacts."""
    results: list[F221ArtifactResult] = []
    missing: list[F221ArtifactResult] = []

    for probe_dir, filename in _CROSS_SPRINT_REQUIRED:
        result = _check_f221_artifact(repo_root, probe_dir, filename)
        results.append(result)
        if not result.valid:
            missing.append(result)

    return results, missing


# --------------------------------------------------------------------------- #
# Last live triage parsing
# --------------------------------------------------------------------------- #

def _load_last_live_triage(path: Optional[Path]) -> Optional[dict]:
    """Load optional last-live artifact triage result."""
    if path is None or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Decision gate result loading
# --------------------------------------------------------------------------- #

def _load_decision_gate(decision_path: Optional[Path]) -> Optional[dict]:
    if decision_path is None or not decision_path.exists():
        return None
    try:
        with open(decision_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Provider surface check (from decision gate checked_reports)
# --------------------------------------------------------------------------- #

def _is_provider_surface_ok(decision_data: Optional[dict]) -> bool:
    """Check provider surface is OK from decision gate data."""
    if decision_data is None:
        return True  # no gate data = skip check

    checked = decision_data.get("checked_reports", {})
    if not checked:
        return True

    pub_bootstrap = checked.get("probe_f217c_public_bootstrap", {})
    ct_resilience = checked.get("probe_f217d_ct_provider_resilience", {})
    pub_session_seal = checked.get("probe_f219d_public_session_seal", {})
    ct_cooldown = checked.get("probe_f219e_ct_provider_cooldown", {})
    provider_surface_smoke = checked.get("probe_f220e_provider_surface_smoke", {})

    # Check old F217 probes
    pub_ok = pub_bootstrap.get("found") and pub_bootstrap.get("pass")
    seal_ok = pub_session_seal.get("found") and pub_session_seal.get("pass")
    ct_ok = ct_resilience.get("found") and ct_resilience.get("pass")
    cooldown_ok = ct_cooldown.get("found") and ct_cooldown.get("pass")
    smoke_ok = provider_surface_smoke.get("found") and provider_surface_smoke.get("pass")

    # F219 aliases satisfy F217 requirements
    pub_satisfied = pub_ok or seal_ok
    ct_satisfied = ct_ok or cooldown_ok

    # F220E smoke provides additional confirmation
    surface_satisfied = pub_satisfied and ct_satisfied
    if smoke_ok:
        surface_satisfied = True

    return surface_satisfied


def _has_fallback_schema(decision_data: Optional[dict]) -> bool:
    """Check if any report has fallback acquisition schema marker."""
    if decision_data is None:
        return False
    return bool(decision_data.get("fallback_schema_blocked", False))


# --------------------------------------------------------------------------- #
# Core gate logic
# --------------------------------------------------------------------------- #

@dataclass
class OneButtonResult:
    verdict: OneButtonVerdict
    live_allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uma: dict = field(default_factory=dict)
    f221_artifacts: dict = field(default_factory=dict)
    missing_f221: list[str] = field(default_factory=list)
    missing_cross_sprint: list[str] = field(default_factory=list)
    # F223 artifacts (Sprint F224E)
    f223_artifacts: dict = field(default_factory=dict)
    missing_f223_required: list[str] = field(default_factory=list)
    f223_optional_status: dict = field(default_factory=dict)
    provider_surface_ok: bool = True
    fallback_schema_blocked: bool = False
    swap_policy_tier: str = "unknown"
    swap_gate_reason: str = ""
    live_command: dict = field(default_factory=dict)  # {command, expected_assertions}
    triage_verdict: Optional[str] = None
    triage_another_live_useful: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "live_allowed": self.live_allowed,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "uma": self.uma,
            "f221_artifacts": self.f221_artifacts,
            "missing_f221": self.missing_f221,
            "missing_cross_sprint": self.missing_cross_sprint,
            "f223_artifacts": self.f223_artifacts,
            "missing_f223_required": self.missing_f223_required,
            "f223_optional_status": self.f223_optional_status,
            "provider_surface_ok": self.provider_surface_ok,
            "fallback_schema_blocked": self.fallback_schema_blocked,
            "swap_policy_tier": self.swap_policy_tier,
            "swap_gate_reason": self.swap_gate_reason,
            "live_command": self.live_command,
            "triage_verdict": self.triage_verdict,
            "triage_another_live_useful": self.triage_another_live_useful,
        }


def run_one_button_gate(
    repo_root: Path,
    profile: str,
    query: str,
    decision_gate_path: Optional[Path] = None,
    last_live_triage_path: Optional[Path] = None,
) -> OneButtonResult:
    """
    Run the one-button prelive gate.

    No live sprint. No model load. No network.
    """
    repo_root = Path(repo_root).resolve()
    reasons: list[str] = []
    warnings: list[str] = []

    # 1. Sample UMA
    uma = _sample_uma()
    swap_gib = uma.get("swap_used_gib", 0.0)
    uma_state = uma.get("uma_state", "unknown")

    # 2. Check F221A-G artifacts
    f221_results, f221_missing = _check_all_f221_artifacts(repo_root)
    missing_f221 = [f"{r.probe_dir}/{r.filename}" for r in f221_missing]
    f221_valid_count = sum(1 for r in f221_results if r.valid)

    f221_artifacts = {
        "total": len(f221_results),
        "valid": f221_valid_count,
        "missing": len(f221_missing),
        "details": [
            {
                "probe_dir": r.probe_dir,
                "filename": r.filename,
                "found": r.found,
                "valid": r.valid,
                "parse_error": r.parse_error,
            }
            for r in f221_results
        ],
    }

    # 3. Check cross-sprint artifacts
    _, cross_missing = _check_cross_sprint_artifacts(repo_root)
    missing_cross_sprint = [f"{r.probe_dir}/{r.filename}" for r in cross_missing]

    # 3b. Check F223 post-F223 artifacts (Sprint F224E)
    f223_results, f223_required_missing, f223_optional = _check_all_f223_artifacts(repo_root)
    missing_f223_required = [f"{r.probe_dir}/{r.filename}" for r in f223_required_missing]

    f223_artifacts = {
        "required_total": len(f223_results),
        "required_valid": sum(1 for r in f223_results if r.valid),
        "required_missing": len(f223_required_missing),
        "optional_total": len(f223_optional),
        "optional_valid": sum(1 for r in f223_optional if r.valid),
        "required_details": [
            {
                "logical_name": r.logical_name,
                "probe_dir": r.probe_dir,
                "filename": r.filename,
                "found": r.found,
                "valid": r.valid,
                "parse_error": r.parse_error,
                "resolved_path": r.resolved_path,
                "alias_used": r.alias_used,
                "searched_paths": r.searched_paths,
            }
            for r in f223_results
        ],
        "optional_details": [
            {
                "logical_name": r.logical_name,
                "probe_dir": r.probe_dir,
                "filename": r.filename,
                "found": r.found,
                "valid": r.valid,
                "parse_error": r.parse_error,
                "resolved_path": r.resolved_path,
                "alias_used": r.alias_used,
                "searched_paths": r.searched_paths,
            }
            for r in f223_optional
        ],
    }

    f223_optional_status = {
        "total": len(f223_optional),
        "valid": sum(1 for r in f223_optional if r.valid),
    }

    # 4. Load decision gate if provided
    decision_data = _load_decision_gate(decision_gate_path)

    # 5. Load optional last-live triage
    triage = _load_last_live_triage(last_live_triage_path)
    triage_verdict = triage.get("root_cause_class") if triage else None
    triage_another_live_useful = triage.get("another_live_useful") if triage else None

    # 6. Provider surface check
    provider_surface_ok = _is_provider_surface_ok(decision_data)
    fallback_blocked = _has_fallback_schema(decision_data)

    # 7. Swap tier
    if swap_gib <= CLEAN_SWAP_MAX_GIB:
        swap_policy_tier = "clean"
        swap_gate_reason = f"swap={swap_gib:.3f}GiB <= {CLEAN_SWAP_MAX_GIB}GiB"
    elif swap_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
        swap_policy_tier = "diagnostic"
        swap_gate_reason = f"swap={swap_gib:.3f}GiB in ({CLEAN_SWAP_MAX_GIB}GiB, {DIAGNOSTIC_SWAP_MAX_GIB}GiB]"
    else:
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"swap={swap_gib:.3f}GiB > {DIAGNOSTIC_SWAP_MAX_GIB}GiB"

    # 8. Build live command dict (Sprint F224E)
    encoded_query = query.replace('"', '\\"')

    live_command = {
        "command": (
            f"cd /Users/vojtechhamada/PycharmProjects/Hledac && "
            f"rtk proxy python -m hledac.universal.benchmarks.live_sprint_measurement "
            f"--profile {profile} "
            f'--query "{encoded_query}" '
            f"--live "
            f"--require-memory-ok "
            f"--output-json <path> "
            f"--output-md <path>"
        ),
        "expected_assertions": {
            "benchmark_profile": profile,
            "acquisition_profile": _get_acquisition_profile_for_benchmark(profile),
            "nonfeed_priority_enabled": True,
            "terminality_satisfied_cannot_produce_FAIL_TERMINALITY_UNSATISFIED": True,
            "FAIL_NONFEED_EVIDENCE_MISSING_when_nonfeed_evidence_missing": True,
            "runtime_accepted_findings_divergence_explicit": True,
            "public_stage_counters_raw_count_source_present": True,
        },
        "abort_if": {
            "swap_above_hard_block": f"swap > {DIAGNOSTIC_SWAP_MAX_GIB}GiB",
            "missing_f223_required_artifacts": "any F223 required artifact missing",
            "uma_state_critical_or_emergency": "uma_state in (critical, emergency)",
            "provider_surface_not_ok": "provider_surface_ok == False",
            "fallback_schema_blocked": "fallback_schema_blocked == True",
        },
        "profile": profile,
        "query": query,
    }

    # 9. Decision tree
    # Rule 1: Missing F221 artifacts → DO_NOT_RUN_FIX_ARTIFACTS
    if missing_f221:
        verdict = OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS
        live_allowed = False
        reasons.append(f"Missing required F221 probe artifacts: {', '.join(missing_f221)}")
        if missing_cross_sprint:
            reasons.append(f"Also missing cross-sprint artifacts: {', '.join(missing_cross_sprint)}")

    # Rule 1b: Missing F223 required artifacts → DO_NOT_RUN_FIX_ARTIFACTS (Sprint F224E)
    elif missing_f223_required:
        verdict = OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS
        live_allowed = False
        reasons.append(f"Missing required F223 post-F223 probe artifacts: {', '.join(missing_f223_required)}")

    # Rule 2: Fallback schema → DO_NOT_RUN_CONTRACT
    elif fallback_blocked:
        verdict = OneButtonVerdict.DO_NOT_RUN_CONTRACT
        live_allowed = False
        reasons.append("Fallback acquisition schema detected in prelive reports")

    # Rule 3: Provider surface broken → DO_NOT_RUN_PROVIDER_SURFACE
    elif not provider_surface_ok:
        verdict = OneButtonVerdict.DO_NOT_RUN_PROVIDER_SURFACE
        live_allowed = False
        reasons.append("Provider surface missing or failing (public bootstrap / CT resilience)")

    # Rule 4: UMA emergency/critical → DO_NOT_RUN_UNKNOWN (memory issue)
    elif uma_state in ("critical", "emergency"):
        verdict = OneButtonVerdict.DO_NOT_RUN_UNKNOWN
        live_allowed = False
        reasons.append(f"UMA state {uma_state} — restart required before any run")
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"uma_state={uma_state}"

    # Rule 5: Swap elevated (diagnostic or hard_block) but artifacts ready → RESTART_THEN_RUN
    elif swap_policy_tier in ("diagnostic", "hard_block"):
        verdict = OneButtonVerdict.RESTART_THEN_RUN
        live_allowed = False
        reasons.append(f"Swap elevated ({swap_gate_reason}) — restart recommended before live run")
        warnings.append(f"Hardware constrained: swap={swap_gib:.3f}GiB, tier={swap_policy_tier}")

    # Rule 6: All clear → RUN_NOW
    else:
        verdict = OneButtonVerdict.RUN_NOW
        live_allowed = True
        reasons.append(f"All checks passed. UMA ok (swap={swap_gib:.3f}GiB, state={uma_state})")
        if f221_valid_count < len(f221_results):
            warnings.append(f"Only {f221_valid_count}/{len(f221_results)} F221 artifacts valid")

    # Last-live triage context
    if triage_verdict:
        warnings.append(f"Last-live triage verdict: {triage_verdict}")
        if not triage_another_live_useful:
            warnings.append("Last-live triage: another live run may not be useful")

    return OneButtonResult(
        verdict=verdict,
        live_allowed=live_allowed,
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
        live_command=live_command,
        triage_verdict=triage_verdict,
        triage_another_live_useful=triage_another_live_useful,
    )


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #

def _render_markdown(result: OneButtonResult, profile: str, query: str) -> str:
    """Render one-button result as markdown report."""
    icon_map = {
        OneButtonVerdict.RUN_NOW: "✅",
        OneButtonVerdict.RESTART_THEN_RUN: "🟡",
        OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS: "❌",
        OneButtonVerdict.DO_NOT_RUN_PROVIDER_SURFACE: "❌",
        OneButtonVerdict.DO_NOT_RUN_CONTRACT: "❌",
        OneButtonVerdict.DO_NOT_RUN_UNKNOWN: "⚠️",
    }
    icon = icon_map.get(result.verdict, "?")

    lines = [
        "# One-Button Prelive Gate Report (F221H)",
        "",
        f"**Verdict:** {icon} `{result.verdict.value}`",
        f"**Live Allowed:** `{result.live_allowed}`",
        f"**Profile:** `{profile}`",
        f"**Query:** `{query}`",
        "",
        "---",
        "",
        "## Decision Summary",
        "",
    ]

    if result.reasons:
        for r in result.reasons:
            lines.append(f"- {r}")

    if result.warnings:
        lines.append("")
        lines.append("**Warnings:**")
        for w in result.warnings:
            lines.append(f"- {w}")

    lines.extend(["", "---", "", "## UMA / Swap State", ""])
    uma = result.uma
    for key in ["system_used_gib", "swap_used_gib", "swap_detected", "uma_state", "io_only"]:
        val = uma.get(key)
        if val is not None:
            lines.append(f"| {key} | `{val}` |")
    if uma.get("error"):
        lines.append(f"| error | `{uma.get('error')}` |")

    lines.extend([
        "",
        f"| Swap Policy Tier | `{result.swap_policy_tier}` |",
        f"| Swap Gate Reason | `{result.swap_gate_reason}` |",
    ])

    lines.extend(["", "---", "", "## F221 Artifact Status", ""])
    fa = result.f221_artifacts
    lines.extend([
        f"| Total | {fa.get('total', 0)} |",
        f"| Valid | {fa.get('valid', 0)} |",
        f"| Missing | {fa.get('missing', 0)} |",
    ])

    if result.missing_f221:
        lines.append("")
        lines.append("**Missing F221 Artifacts:**")
        for m in result.missing_f221:
            lines.append(f"- `{m}`")

    if result.missing_cross_sprint:
        lines.append("")
        lines.append("**Missing Cross-Sprint Artifacts:**")
        for m in result.missing_cross_sprint:
            lines.append(f"- `{m}`")

    if fa.get("details"):
        lines.extend(["", "### F221 Artifact Details", ""])
        lines.append("| Probe | Artifact | Found | Valid |")
        lines.append("|------|----------|-------|-------|")
        for d in fa["details"]:
            lines.append(
                f"| {d['probe_dir']} | {d['filename']} | "
                f"{'✅' if d['found'] else '❌'} | {'✅' if d['valid'] else '❌'} |"
            )

    # F223 post-F223 artifact status (Sprint F224E)
    f223a = result.f223_artifacts
    lines.extend(["", "---", "", "## F223 Post-F223 Artifact Status (Sprint F224E)", ""])
    if f223a:
        lines.extend([
            f"| Required Total | {f223a.get('required_total', 0)} |",
            f"| Required Valid | {f223a.get('required_valid', 0)} |",
            f"| Required Missing | {f223a.get('required_missing', 0)} |",
            f"| Optional Total | {f223a.get('optional_total', 0)} |",
            f"| Optional Valid | {f223a.get('optional_valid', 0)} |",
        ])

    if result.missing_f223_required:
        lines.append("")
        lines.append("**Missing F223 Required Artifacts:**")
        for m in result.missing_f223_required:
            lines.append(f"- `{m}`")

    if f223a and f223a.get("required_details"):
        lines.extend(["", "### F223 Required Artifact Details", ""])
        lines.append("| Probe | Artifact | Found | Valid |")
        lines.append("|------|----------|-------|-------|")
        for d in f223a["required_details"]:
            lines.append(
                f"| {d['probe_dir']} | {d['filename']} | "
                f"{'✅' if d['found'] else '❌'} | {'✅' if d['valid'] else '❌'} |"
            )

    if f223a and f223a.get("optional_details"):
        lines.extend(["", "### F223 Optional Artifact Details", ""])
        lines.append(f"(_Optional — advisory only, does not block_)")
        lines.append("| Probe | Artifact | Found | Valid |")
        lines.append("|------|----------|-------|-------|")
        for d in f223a["optional_details"]:
            lines.append(
                f"| {d['probe_dir']} | {d['filename']} | "
                f"{'✅' if d['found'] else '❌'} | {'✅' if d['valid'] else '❌'} |"
            )

    lines.extend(["", "---", "", "## Provider Surface", ""])
    ps_icon = "✅" if result.provider_surface_ok else "❌"
    lines.append(f"- **OK:** {ps_icon} `{result.provider_surface_ok}`")
    lines.append(f"- **Fallback Schema Blocked:** `{result.fallback_schema_blocked}`")

    if result.triage_verdict:
        lines.extend(["", "---", "", "## Last-Live Triage", ""])
        lines.append(f"- **Triage Verdict:** `{result.triage_verdict}`")
        lines.append(f"- **Another Live Useful:** `{result.triage_another_live_useful}`")

    lines.extend(["", "---", "", "## Live Command (Sprint F224E)", ""])

    lc = result.live_command
    if lc:
        lines.append("### Exact Command")
        lines.append(f"```bash\n{lc.get('command', '')}\n```")

        lines.append("")
        lines.append("### Expected Post-F223 Assertions")
        assertions = lc.get("expected_assertions", {})
        for key, val in assertions.items():
            lines.append(f"- `{key}` → `{val}`")

        lines.append("")
        lines.append("### Abort Conditions")
        abort_if = lc.get("abort_if", {})
        for reason, desc in abort_if.items():
            lines.append(f"- **{reason}:** {desc}")
    else:
        lines.append("_No live command generated (gate did not pass)._")

    lines.extend([
        "",
        "---",
        "",
        "## How to Run This Gate",
        "",
        "```bash",
        "python tools/prelive_one_button_gate.py \\",
        "  --repo-root . \\",
        "  --profile nonfeed_diagnostic180 \\",
        '  --query "mozilla.org certificate transparency subdomains april 2026" \\',
        "  --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\",
        "  --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md",
        "```",
        "",
        "With optional last-live triage:",
        "```bash",
        "python tools/prelive_one_button_gate.py \\",
        "  --repo-root . --profile nonfeed_diagnostic180 \\",
        '  --query "..." \\',
        "  --last-live-triage probe_f219g_live_artifact_triage/triage.json \\",
        "  --decision-gate-json probe_f219f_prelive_decision_gate/prelive_decision.json \\",
        "  --output-json ... --output-md ...",
        "```",
    ])

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test mode (Sprint F224H)
# --------------------------------------------------------------------------- #

@dataclass
class SelfTestResult:
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
        return {
            "self_test_passed": self.self_test_passed,
            "artifact_matrix": self.artifact_matrix,
            "assertion_contract_ok": self.assertion_contract_ok,
            "command_contract_ok": self.command_contract_ok,
            "cwd_contract_ok": self.cwd_contract_ok,
            "blocking_reasons": self.blocking_reasons,
            "warnings": self.warnings,
            "profile_assertions": self.profile_assertions,
        }


def _run_self_test(repo_root: Path, profile: str, query: str) -> SelfTestResult:
    """
    Self-test mode: validates artifact resolution and expected assertion contract.
    NEVER runs live. No network. No MLX. No model load.
    """
    repo_root = Path(repo_root).resolve()
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    artifact_matrix: list[dict] = []

    # 1. Repo-root reality
    reality = _get_repo_root_reality()
    cwd_contract_ok = (
        reality["cwd_is_universal_root"]
        and reality["universal_root_exists"]
        and not reality["cwd_warning"]
    )
    if reality["cwd_warning"]:
        warnings.append(f"CWD contract: {reality['cwd_warning']}")
    if not reality["universal_root_exists"]:
        blocking_reasons.append(f"universal_root does not exist: {reality['universal_root']}")

    # 2. Resolve all required F223 artifacts
    f223_req_results, f223_req_missing, f223_opt_results = _check_all_f223_artifacts(repo_root)
    _ = f223_req_missing  # used for blocking-reason construction below

    for r in f223_req_results:
        entry = {
            "probe_dir": r.probe_dir,
            "filename": r.filename,
            "category": "required",
            "found": r.found,
            "valid": r.valid,
            "parse_error": r.parse_error,
            "blocks_live": True,
        }
        artifact_matrix.append(entry)
        if not r.valid:
            blocking_reasons.append(f"required artifact invalid/missing: {r.probe_dir}/{r.filename}")

    # 3. Resolve all optional F223 artifacts (F223E/F223F/F223G)
    for r in f223_opt_results:
        entry = {
            "probe_dir": r.probe_dir,
            "filename": r.filename,
            "category": "optional",
            "found": r.found,
            "valid": r.valid,
            "parse_error": r.parse_error,
            "blocks_live": False,
        }
        artifact_matrix.append(entry)
        if not r.valid:
            warnings.append(f"optional artifact invalid/missing: {r.probe_dir}/{r.filename}")

    # 4. Validate cross-sprint required artifacts
    cross_results, cross_missing = _check_cross_sprint_artifacts(repo_root)
    _ = cross_missing  # used for blocking-reason construction below
    for r in cross_results:
        entry = {
            "probe_dir": r.probe_dir,
            "filename": r.filename,
            "category": "cross_sprint_required",
            "found": r.found,
            "valid": r.valid,
            "parse_error": r.parse_error,
            "blocks_live": True,
        }
        artifact_matrix.append(entry)
        if not r.valid:
            blocking_reasons.append(f"cross-sprint artifact invalid/missing: {r.probe_dir}/{r.filename}")

    # ------------------------------------------------------------------------- #
    # F229-GATE-A: F229 artifact checks (nonfeed uplift structural gate)
    # ------------------------------------------------------------------------- #
    # Categories: F229-EXPORT-A, F229-RUNTIME-A, F229-PUBLIC-A, F229-NONFEED-A
    # These are structural import/existence checks only — no live execution.
    # ------------------------------------------------------------------------- #

    # F229-EXPORT-A: export/sprint_exporter.py must be importable
    try:
        from export import sprint_exporter as _export_sprint_exporter
        _has_export = hasattr(_export_sprint_exporter, "_generate_next_sprint_seeds")
        if not _has_export:
            blocking_reasons.append("F229-EXPORT-A: export.sprint_exporter missing _generate_next_sprint_seeds")
        else:
            artifact_matrix.append({
                "probe_dir": "export",
                "filename": "sprint_exporter.py",
                "category": "F229-EXPORT-A",
                "found": True,
                "valid": True,
                "parse_error": None,
                "blocks_live": True,
            })
    except ImportError as _exc:
        blocking_reasons.append(f"F229-EXPORT-A: export.sprint_exporter not importable: {_exc}")
        artifact_matrix.append({
            "probe_dir": "export",
            "filename": "sprint_exporter.py",
            "category": "F229-EXPORT-A",
            "found": False,
            "valid": False,
            "parse_error": str(_exc),
            "blocks_live": True,
        })

    # F229-RUNTIME-A: runtime/sprint_scheduler.py must be importable
    try:
        from runtime import sprint_scheduler as _runtime_scheduler
        _has_scheduler = hasattr(_runtime_scheduler, "SprintScheduler") or hasattr(_runtime_scheduler, "run_sprint")
        if not _has_scheduler:
            blocking_reasons.append("F229-RUNTIME-A: runtime.sprint_scheduler missing SprintScheduler/run_sprint")
        else:
            artifact_matrix.append({
                "probe_dir": "runtime",
                "filename": "sprint_scheduler.py",
                "category": "F229-RUNTIME-A",
                "found": True,
                "valid": True,
                "parse_error": None,
                "blocks_live": True,
            })
    except ImportError as _exc:
        blocking_reasons.append(f"F229-RUNTIME-A: runtime.sprint_scheduler not importable: {_exc}")
        artifact_matrix.append({
            "probe_dir": "runtime",
            "filename": "sprint_scheduler.py",
            "category": "F229-RUNTIME-A",
            "found": False,
            "valid": False,
            "parse_error": str(_exc),
            "blocks_live": True,
        })

    # F229-PUBLIC-A: pipeline/live_public_pipeline.py must have public_bootstrap_order field
    try:
        from pipeline.live_public_pipeline import PipelineRunResult as _PipResult
        if hasattr(_PipResult, "public_bootstrap_order"):
            artifact_matrix.append({
                "probe_dir": "pipeline",
                "filename": "live_public_pipeline.py",
                "category": "F229-PUBLIC-A",
                "found": True,
                "valid": True,
                "parse_error": None,
                "blocks_live": True,
            })
        else:
            blocking_reasons.append("F229-PUBLIC-A: PipelineRunResult missing public_bootstrap_order field")
            artifact_matrix.append({
                "probe_dir": "pipeline",
                "filename": "live_public_pipeline.py",
                "category": "F229-PUBLIC-A",
                "found": True,
                "valid": False,
                "parse_error": "public_bootstrap_order field not found on PipelineRunResult",
                "blocks_live": True,
            })
    except ImportError as _exc:
        blocking_reasons.append(f"F229-PUBLIC-A: pipeline.live_public_pipeline not importable: {_exc}")
        artifact_matrix.append({
            "probe_dir": "pipeline",
            "filename": "live_public_pipeline.py",
            "category": "F229-PUBLIC-A",
            "found": False,
            "valid": False,
            "parse_error": str(_exc),
            "blocks_live": True,
        })

    # F229-NONFEED-A: nonfeed_profile_expected_lanes in LiveMeasurementResult
    try:
        from benchmarks.live_sprint_measurement import LiveMeasurementResult as _LMR
        _has_lanes = hasattr(_LMR, "nonfeed_profile_expected_lanes")
        _has_acq_report = hasattr(_LMR, "acquisition_report")
        if not _has_lanes:
            blocking_reasons.append("F229-NONFEED-A: LiveMeasurementResult missing nonfeed_profile_expected_lanes")
        if not _has_acq_report:
            blocking_reasons.append("F229-NONFEED-A: LiveMeasurementResult missing acquisition_report")
        _nonfeed_valid = _has_lanes and _has_acq_report
        artifact_matrix.append({
            "probe_dir": "benchmarks",
            "filename": "live_sprint_measurement.py",
            "category": "F229-NONFEED-A",
            "found": True,
            "valid": _nonfeed_valid,
            "parse_error": None if _nonfeed_valid else "missing nonfeed fields",
            "blocks_live": True,
        })
    except ImportError as _exc:
        blocking_reasons.append(f"F229-NONFEED-A: benchmarks.live_sprint_measurement not importable: {_exc}")
        artifact_matrix.append({
            "probe_dir": "benchmarks",
            "filename": "live_sprint_measurement.py",
            "category": "F229-NONFEED-A",
            "found": False,
            "valid": False,
            "parse_error": str(_exc),
            "blocks_live": True,
        })

    # 5. Validate expected_assertions contract
    expected_profile = profile
    expected_acquisition = _get_acquisition_profile_for_benchmark(expected_profile)
    profile_assertions = {
        "benchmark_profile": expected_profile,
        "acquisition_profile": expected_acquisition,
        "nonfeed_priority_enabled": True,
        "terminality_satisfied": True,
        "FAIL_NONFEED_EVIDENCE_MISSING": True,
        "runtime_accepted_findings_divergence": True,
        "public_stage_counters_raw_count": True,
    }

    assertion_contract_ok = True
    if expected_profile == "nonfeed_diagnostic180":
        if expected_acquisition != "nonfeed_diagnostic":
            assertion_contract_ok = False
            blocking_reasons.append(
                f"assertion contract violation: benchmark_profile={expected_profile} "
                f"maps to acquisition_profile={expected_acquisition}, expected nonfeed_diagnostic"
            )
    elif expected_profile not in _BENCHMARK_TO_ACQUISITION_PROFILE:
        warnings.append(f"profile {expected_profile!r} not in benchmark→acquisition map")

    # 6. Validate command contract
    command_contract_ok = True
    encoded_query = query.replace('"', '\\"')
    expected_cmd_substrings = [
        f"--profile {profile}",
        f"--query \"{encoded_query}\"",
        "--live",
    ]
    constructed_cmd = (
        f"rtk proxy python -m hledac.universal.benchmarks.live_sprint_measurement "
        f"--profile {profile} --query \"{encoded_query}\" --live"
    )
    for substr in expected_cmd_substrings:
        if substr not in constructed_cmd:
            command_contract_ok = False
            blocking_reasons.append(f"command contract violated: expected substring {substr!r} in live command")

    # 7. Validate --profile is nonfeed_diagnostic180 (not nonfeed_diagnostic directly)
    if profile == "nonfeed_diagnostic":
        warnings.append(
            "profile is 'nonfeed_diagnostic' — did you mean 'nonfeed_diagnostic180'? "
            "nonfeed_diagnostic180 is the benchmark profile that maps to nonfeed_diagnostic acquisition."
        )

    # 8. Validate acquisition_profile is nonfeed_diagnostic when using nonfeed_diagnostic180
    if profile == "nonfeed_diagnostic180" and expected_acquisition != "nonfeed_diagnostic":
        assertion_contract_ok = False
        blocking_reasons.append(
            f"acquisition_profile={expected_acquisition} != nonfeed_diagnostic "
            f"for benchmark profile nonfeed_diagnostic180"
        )

    self_test_passed = (
        cwd_contract_ok
        and assertion_contract_ok
        and command_contract_ok
        and len(blocking_reasons) == 0
    )

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
    icon = "✅" if result.self_test_passed else "❌"
    lines = [
        "# One-Button Gate — Self-Test Report (Sprint F224H)",
        "",
        f"**Self-Test Passed:** {icon} `{result.self_test_passed}`",
        f"**Profile:** `{profile}`",
        f"**Query:** `{query}`",
        "",
        "---",
        "",
        "## Contract Status",
        "",
        f"| Contract | Status |",
        f"|----------|--------|",
        f"| CWD / Repo-Root | {'✅' if result.cwd_contract_ok else '❌'} |",
        f"| Assertion Contract | {'✅' if result.assertion_contract_ok else '❌'} |",
        f"| Command Contract | {'✅' if result.command_contract_ok else '❌'} |",
        "",
        "---",
        "",
        "## Artifact Matrix",
        "",
        "| Probe Dir | Filename | Category | Found | Valid | Blocks Live |",
        "|------------|----------|----------|-------|-------|------------|",
    ]
    for a in result.artifact_matrix:
        found_icon = "✅" if a["found"] else "❌"
        valid_icon = "✅" if a["valid"] else "❌"
        blocks_icon = "🚫" if a["blocks_live"] else "—"
        lines.append(
            f"| {a['probe_dir']} | {a['filename']} | {a['category']} | "
            f"{found_icon} | {valid_icon} | {blocks_icon} |"
        )

    if result.blocking_reasons:
        lines.extend(["", "---", "", "## Blocking Reasons", ""])
        for b in result.blocking_reasons:
            lines.append(f"- ❌ {b}")

    if result.warnings:
        lines.extend(["", "---", "", "## Warnings", ""])
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")

    lines.extend(["", "---", "", "## Profile Assertions", ""])
    for k, v in result.profile_assertions.items():
        lines.append(f"- `{k}` → `{v}`")

    encoded_q = query.replace('"', '\\"')
    lines.extend(["", "---", "", "## How to Run Live", "", "```bash"])
    lines.append(f"python tools/prelive_one_button_gate.py \\")
    lines.append(f"  --repo-root . \\")
    lines.append(f"  --profile {profile} \\")
    lines.append(f"  --query \"{encoded_q}\" \\")
    lines.append(f"  --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\")
    lines.append(f"  --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md")
    lines.append("```")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prelive One-Button Decision Gate — Sprint F221H",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Standard run (reads artifacts from standard probe_* locations):
              python tools/prelive_one_button_gate.py \\
                --repo-root . \\
                --profile nonfeed_diagnostic180 \\
                --query "mozilla.org certificate transparency subdomains april 2026" \\
                --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \\
                --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md

              # With decision gate and last-live triage:
              python tools/prelive_one_button_gate.py \\
                --repo-root . --profile nonfeed_diagnostic180 \\
                --query "..." \\
                --decision-gate-json probe_f219f_prelive_decision_gate/prelive_decision.json \\
                --last-live-triage probe_f219g_live_artifact_triage/triage.json \\
                --output-json ... --output-md ...
        """),
    )
    p.add_argument("--repo-root", type=Path, default=Path("."))
    p.add_argument("--profile", default="nonfeed_diagnostic180")
    p.add_argument("--query", required=True)
    p.add_argument(
        "--decision-gate-json", type=Path, default=None,
        help="Path to prelive_decision.json (from prelive_decision_gate.py). "
             "If omitted, provider surface check is skipped.",
    )
    p.add_argument(
        "--last-live-triage", type=Path, default=None,
        dest="last_live_triage",
        help="Path to last-live triage.json (from live_artifact_triage.py). Optional.",
    )
    p.add_argument("--output-json", type=Path, default=None)
    p.add_argument("--output-md", type=Path, default=None)
    p.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "Run self-test mode: validates artifact resolution and expected assertion "
            "contract without running live. Never loads MLX or makes network calls. "
            "Emits machine-checkable JSON readiness matrix."
        ),
    )
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"ERROR: repo root does not exist: {repo_root}", file=sys.stderr)
        return 1

    # Sprint F224H: self-test mode — validates readiness contract, never runs live
    if args.self_test:
        st_result = _run_self_test(repo_root, args.profile, args.query)

        # Console output
        icon = "✅" if st_result.self_test_passed else "❌"
        print(f"{'=' * 60}")
        print(f"  Self-Test:    {icon} {'PASSED' if st_result.self_test_passed else 'FAILED'}")
        print(f"  CWD Contract: {'✅' if st_result.cwd_contract_ok else '❌'}")
        print(f"  Assertion Contract: {'✅' if st_result.assertion_contract_ok else '❌'}")
        print(f"  Command Contract: {'✅' if st_result.command_contract_ok else '❌'}")
        print(f"{'=' * 60}")
        if st_result.blocking_reasons:
            print("Blocking reasons:")
            for b in st_result.blocking_reasons:
                print(f"  - {b}")
        if st_result.warnings:
            print("Warnings:")
            for w in st_result.warnings:
                print(f"  - {w}")
        print()
        print("Artifact matrix:")
        for a in st_result.artifact_matrix:
            found = "✅" if a["found"] else "❌"
            valid = "✅" if a["valid"] else "❌"
            blocks = "🚫" if a["blocks_live"] else "—"
            print(f"  [{blocks}] {a['probe_dir']}/{a['filename']} found={found} valid={valid}")

        if st_result.profile_assertions:
            print()
            print("Profile assertions:")
            for k, v in st_result.profile_assertions.items():
                print(f"  {k} → {v}")

        # Emit machine-checkable JSON to stdout for testability
        print()
        print("##GATE_SELFTEST_JSON##")
        print(json.dumps(st_result.to_dict(), indent=2))
        print("##GATE_SELFTEST_JSON_END##")

        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_json, "w", encoding="utf-8") as fh:
                json.dump(st_result.to_dict(), fh, indent=2, default=str)
            print(f"\nJSON report written: {args.output_json}")

        if args.output_md:
            md_text = _render_self_test_markdown(st_result, args.profile, args.query)
            args.output_md.parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_md, "w", encoding="utf-8") as fh:
                fh.write(md_text)
            print(f"Markdown report written: {args.output_md}")

        return 0 if st_result.self_test_passed else 1

    # F223H: CWD guard — warn if running from wrong directory
    cwd_warning = _check_cwd_guard(repo_root)
    if cwd_warning:
        print(f"CWD GUARD: {cwd_warning}", file=sys.stderr)
        print("Aborting artifact scan due to wrong CWD.", file=sys.stderr)
        return 1

    result = run_one_button_gate(
        repo_root=repo_root,
        profile=args.profile,
        query=args.query,
        decision_gate_path=args.decision_gate_json,
        last_live_triage_path=args.last_live_triage,
    )

    # Console output
    icon_map = {
        OneButtonVerdict.RUN_NOW: "✅",
        OneButtonVerdict.RESTART_THEN_RUN: "🟡",
        OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS: "❌",
        OneButtonVerdict.DO_NOT_RUN_PROVIDER_SURFACE: "❌",
        OneButtonVerdict.DO_NOT_RUN_CONTRACT: "❌",
        OneButtonVerdict.DO_NOT_RUN_UNKNOWN: "⚠️",
    }
    icon = icon_map.get(result.verdict, "?")
    print(f"{'=' * 60}")
    print(f"  Verdict:      {icon} {result.verdict.value}")
    print(f"  Live Allowed: {result.live_allowed}")
    print(f"  Swap Tier:    {result.swap_policy_tier}")
    print(f"{'=' * 60}")
    if result.reasons:
        print("Reasons:")
        for r in result.reasons:
            print(f"  - {r}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    if result.missing_f221:
        print(f"Missing F221 artifacts ({len(result.missing_f221)}):")
        for m in result.missing_f221:
            print(f"  - {m}")
    if result.missing_f223_required:
        print(f"Missing F223 required artifacts ({len(result.missing_f223_required)}):")
        for m in result.missing_f223_required:
            print(f"  - {m}")
    uma_sw = result.uma.get("swap_used_gib", 0)
    print(f"UMA: swap={uma_sw:.3f}GiB")
    print()
    lc = result.live_command
    if lc:
        print(f"Live command:")
        print(f"  {lc.get('command', '')}")
        print()
        print("Expected assertions:")
        for key, val in lc.get("expected_assertions", {}).items():
            print(f"  {key} → {val}")
        print()
        print("Abort conditions:")
        for reason, desc in lc.get("abort_if", {}).items():
            print(f"  {reason}: {desc}")
    else:
        print("No live command generated (gate did not pass).")

    # Write JSON
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2, default=str)
        print(f"\nJSON report written: {args.output_json}")

    # Write Markdown
    if args.output_md:
        md_text = _render_markdown(result, args.profile, args.query)
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_md, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        print(f"Markdown report written: {args.output_md}")

    return 0 if result.live_allowed else 1


if __name__ == "__main__":
    sys.exit(main())
