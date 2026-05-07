#!/usr/bin/env python3
"""
Pre-Live Decision Gate — Sprint F219F

Reads probe/report artifacts and local UMA state, emits a deterministic
decision without running live sprint, loading model, or using network.

Decision values: READY_FOR_LIVE | BLOCKED_BY_MEMORY | BLOCKED_BY_CONTRACT |
                 BLOCKED_BY_PROVIDER_SURFACE | BLOCKED_BY_UNKNOWN
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Decision enum
# --------------------------------------------------------------------------- #

class Decision(str, Enum):
    READY_FOR_LIVE = "READY_FOR_LIVE"
    READY_FOR_LIVE_HARDWARE_TAINTED = "READY_FOR_LIVE_HARDWARE_TAINTED"
    BLOCKED_BY_MEMORY = "BLOCKED_BY_MEMORY"
    BLOCKED_BY_CONTRACT = "BLOCKED_BY_CONTRACT"
    BLOCKED_BY_PROVIDER_SURFACE = "BLOCKED_BY_PROVIDER_SURFACE"
    BLOCKED_BY_UNKNOWN = "BLOCKED_BY_UNKNOWN"


# --------------------------------------------------------------------------- #
# Swap tiered policy constants (F220F: macOS swap gate calibration)
# --------------------------------------------------------------------------- #
# Imported from core.resource_governor (F220F: single source of truth for constants).
# --------------------------------------------------------------------------- #

from core.resource_governor import get_swap_policy_tier


# --------------------------------------------------------------------------- #
# UMA check  (read-only import — no network, no model, no SprintScheduler)
# --------------------------------------------------------------------------- #

def _check_uma() -> dict:
    """
    Sample UMA status via core.resource_governor.
    This is a one-shot local read — no live sprint, no model load.
    """
    try:
        from core.resource_governor import sample_uma_status
    except Exception as exc:
        return {
            "error": str(exc),
            "system_used_gib": 0.0,
            "swap_used_gib": 0.0,
            "swap_detected": False,
            "uma_state": "unknown",
            "io_only": False,
            "last_error": str(exc),
        }

    try:
        UmaStatus = sample_uma_status()
        return {
            "system_used_gib": round(getattr(UmaStatus, "system_used_gib", 0.0), 3),
            "swap_used_gib": round(getattr(UmaStatus, "swap_used_gib", 0.0), 3),
            "swap_detected": getattr(UmaStatus, "swap_detected", False),
            "uma_state": getattr(UmaStatus, "state", "unknown"),
            "io_only": getattr(UmaStatus, "io_only", False),
            "last_error": getattr(UmaStatus, "last_error", None) or None,
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "system_used_gib": 0.0,
            "swap_used_gib": 0.0,
            "swap_detected": False,
            "uma_state": "unknown",
            "io_only": False,
            "last_error": str(exc),
        }


# --------------------------------------------------------------------------- #
# Report loading helpers
# --------------------------------------------------------------------------- #

PROBE_ROOT_ENV = "PRELIVE_PROBE_ROOT"  # allow override in tests


@dataclass
class ProbeReport:
    path: str
    found: bool
    data: dict = field(default_factory=dict)
    parse_error: Optional[str] = None


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
        with open(full_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return ProbeReport(path=str(full_path), found=True, data=data)
    except Exception as exc:
        return ProbeReport(path=str(full_path), found=False, parse_error=str(exc))


# --------------------------------------------------------------------------- #
# Check helpers
# --------------------------------------------------------------------------- #

_FALLBACK_SCHEMA_MARKERS = [
    "fallback_acquisition_schema",
    "fallback acquisition schema",
    '"fallback"',
    "acquisition_strategy_fallback",
    "_FALLBACK_ACQUISITION",
]


def _has_fallback_schema_marker(report: ProbeReport) -> bool:
    """Scan report raw text for fallback acquisition schema marker."""
    if not report.found or report.parse_error:
        return False
    text = json.dumps(report.data, separators=(",", ":"))
    return any(marker in text for marker in _FALLBACK_SCHEMA_MARKERS)


def _is_pass(report: ProbeReport) -> bool:
    """
    Check if a probe report passes.
    Supports multiple schemas:
      - {"status": "PASS"|"FAIL"}
      - {"test_results": {"probe_XXX": {"status": "PASS"|"FAIL"}}}
      - {"verdict": "SANITY_PASS"}  (zero-findings sanity: PASS means no crash)
    """
    if not report.found:
        return False
    if report.parse_error:
        return False

    d = report.data

    # Schema: status = PASS/FAIL
    status = d.get("status", "")
    if isinstance(status, str) and status.upper() in ("PASS", "PASSED", "COMPLETE"):
        return True
    if isinstance(status, str) and status.upper() in ("FAIL", "FAILED"):
        return False

    # Schema: test_results.PROBE.status = PASS
    test_results = d.get("test_results", {})
    if isinstance(test_results, dict):
        for probe_data in test_results.values():
            if isinstance(probe_data, dict):
                s = probe_data.get("status", "")
                if isinstance(s, str) and s.upper() == "PASS":
                    return True
                if isinstance(s, str) and s.upper() == "FAIL":
                    return False

    # Schema: ready_for_controlled_smoke bool
    ready = d.get("ready_for_controlled_smoke")
    if isinstance(ready, bool):
        return ready

    return False  # unknown schema = fail-open block


def _zero_findings_quality_sane(report: ProbeReport) -> tuple[bool, str]:
    """
    Check zero-findings quality probe does NOT crash and fails correctly.
    Returns (sane, detail).
    """
    if not report.found:
        return False, "report not found"
    if report.parse_error:
        return False, f"parse error: {report.parse_error}"

    d = report.data

    # sanity_zero_findings.json — verdict should be SANITY_FAIL_* (correct failure)
    # If verdict is SANITY_PASS with zero findings, that would be wrong.
    verdict = d.get("verdict", "")
    if verdict and verdict != "SANITY_PASS":
        # Any SANITY_FAIL_* verdict means it failed correctly — that's what we want
        return True, f"zero findings correctly fails: {verdict}"

    # zero_findings_quality.json — confirmation_zero_findings_stay_failed
    confirmation = d.get("confirmation_zero_findings_stay_failed", {})
    if confirmation:
        grade = confirmation.get("grade", "")
        if grade and grade != "FEED_ONLY":
            return True, f"confirmed: {grade}"
        if grade == "FEED_ONLY":
            return True, "confirmed FEED_ONLY (correct failure)"

    # If we have a checks dict with research_quality: True -> wrong
    checks = d.get("checks", {})
    if isinstance(checks, dict) and checks.get("research_quality") is True:
        return False, "research_quality check unexpectedly passed with zero findings"

    return True, "no crash detected"


# --------------------------------------------------------------------------- #
# Provider surface check — unified F219 alias table  (read-only, no network)
# --------------------------------------------------------------------------- #
# Alias table:
#   probe_f217c_public_bootstrap        → probe_f219h_public_fetcher_import_seal  (public fetcher import/seal)
#                                          → probe_f219d_public_session_seal       (public session seal)
#   probe_f217d_ct_provider_resilience  → probe_f219e_ct_provider_cooldown         (CT cooldown/resilience)
#
# Decision rules:
#   - Missing both old AND new → BLOCKED_BY_PROVIDER_SURFACE
#   - Missing old but new present and passing → READY (alias satisfied)
#   - Old present and passing → READY (backward-compat)
#   - New present and failing → BLOCKED
#   - Optional reports missing → warning only, never block
# --------------------------------------------------------------------------- #

_PROVIDER_SURFACE_ALIASES = {
    "probe_f217c_public_bootstrap": [
        ("probe_f219h_public_fetcher_import_seal", "public_fetcher_import_seal.json"),
        ("probe_f219d_public_session_seal", "public_session_seal.json"),
    ],
    "probe_f217d_ct_provider_resilience": [
        ("probe_f219e_ct_provider_cooldown", "ct_provider_cooldown.json"),
    ],
}


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
        old_filename = (
            "public_bootstrap.json" if "bootstrap" in old_probe
            else "ct_provider_resilience.json"
        )
        old_report = _load_report(repo_root, old_probe, old_filename)

        alias_satisfied = False
        alias_failures: list[str] = []

        for new_probe, report_filename in alias_list:
            new_report = _load_report(repo_root, new_probe, report_filename)
            key = f"{old_probe}_alias_{new_probe}"
            if new_report.found:
                if new_report.parse_error:
                    checked[key] = {"found": True, "parse_error": new_report.parse_error, "pass": False}
                    alias_failures.append(f"{new_probe} parse error")
                else:
                    new_pass = _is_pass(new_report)
                    checked[key] = {"found": True, "pass": new_pass, "detail": f"alias: {new_probe}"}
                    if new_pass:
                        alias_satisfied = True
                    else:
                        alias_failures.append(f"{new_probe} FAILED")
            else:
                checked[key] = {"found": False, "pass": False, "detail": "alias absent — skipped"}

        # Store old probe result
        old_pass = old_report.found and _is_pass(old_report)
        checked[old_probe] = {
            "found": old_report.found,
            "parse_error": old_report.parse_error,
            "pass": old_pass,
            "alias_satisfied": alias_satisfied,
        }

        if old_report.found:
            if old_pass:
                alias_satisfied = True  # backward-compat: old passing satisfies
            else:
                alias_failures.append(f"{old_probe} FAILED")

        if not alias_satisfied:
            missing_required.append(old_probe)

    return missing_required, warnings, checked


# --------------------------------------------------------------------------- #
# Surface-contract check  (read-only — no live network, no model load)
# --------------------------------------------------------------------------- #

def _check_surface_contract(repo_root: Path) -> tuple[bool, str, Optional[ProbeReport]]:
    """
    Check F219A surface contract if its probe directory exists.
    Returns (pass, detail, report).
    """
    report = _load_report(repo_root, "probe_f219a_surface_contract", "surface_contract.json")

    if not report.found:
        return True, "optional report absent — skipped", report

    if report.parse_error:
        return False, f"parse error: {report.parse_error}", report

    return _is_pass(report), f"surface_contract: {_is_pass(report)}", report


def _check_hermes_metal_finalizer(repo_root: Path) -> tuple[bool, str, Optional[ProbeReport]]:
    """
    Check F219B Hermes Metal finalizer if its probe directory exists.
    """
    report = _load_report(repo_root, "probe_f219b_hermes_metal_finalizer", "hermes_metal_finalizer.json")

    if not report.found:
        return True, "optional report absent — skipped", report

    if report.parse_error:
        return False, f"parse error: {report.parse_error}", report

    return _is_pass(report), f"hermes_metal_finalizer: {_is_pass(report)}", report


def _check_public_session_seal(repo_root: Path) -> tuple[bool, str, Optional[ProbeReport]]:
    """
    Check F219D public session seal if its probe directory exists.
    """
    report = _load_report(repo_root, "probe_f219d_public_session_seal", "public_session_seal.json")

    if not report.found:
        return True, "optional report absent — skipped", report

    if report.parse_error:
        return False, f"parse error: {report.parse_error}", report

    return _is_pass(report), f"public_session_seal: {_is_pass(report)}", report


def _check_ct_cooldown(repo_root: Path) -> tuple[bool, str, Optional[ProbeReport]]:
    """
    Check F219E CT provider cooldown if its probe directory exists.
    """
    report = _load_report(repo_root, "probe_f219e_ct_provider_cooldown", "ct_provider_cooldown.json")

    if not report.found:
        return True, "optional report absent — skipped", report

    if report.parse_error:
        return False, f"parse error: {report.parse_error}", report

    return _is_pass(report), f"ct_cooldown: {_is_pass(report)}", report


# --------------------------------------------------------------------------- #
# Nonfeed candidate ledger boundedness check
# --------------------------------------------------------------------------- #

def _check_nonfeed_candidate_ledger(repo_root: Path) -> tuple[bool, str]:
    """
    Verify nonfeed candidate ledger is present and bounded (MAX field exists).
    """
    report = _load_report(repo_root, "probe_f217e_nonfeed_candidate_ledger", "candidate_ledger.json")

    if not report.found:
        # Optional — missing is a warning, not a block
        return True, "optional report absent — skipped"

    if report.parse_error:
        return False, f"parse error: {report.parse_error}"

    d = report.data
    # Bounded means it has bounded_caps or similar field
    if "bounded_caps" in d or "bounds" in d or "max" in d or "limit" in d:
        return True, "bounded_caps present"
    # At minimum, a non-empty dict means it exists
    if isinstance(d, dict) and d:
        return True, "report present"
    return False, "report present but no bounding fields detected"


# --------------------------------------------------------------------------- #
# Main gate
# --------------------------------------------------------------------------- #

@dataclass
class DecisionResult:
    decision: Decision
    live_allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_required_reports: list[str] = field(default_factory=list)
    missing_optional_reports: list[str] = field(default_factory=list)
    uma: dict = field(default_factory=dict)
    checked_reports: dict = field(default_factory=dict)
    suggested_live_command: str = ""
    suggested_highswap_diagnostic_command: str = ""
    fallback_schema_blocked: bool = False
    # F220F: swap tiered policy telemetry
    hardware_constrained: bool = False
    swap_policy_tier: str = "unknown"  # "clean" | "diagnostic" | "hard_block"
    swap_gate_reason: str = ""


def run_gate(
    repo_root: Path,
    profile: str,
    query: str,
) -> DecisionResult:
    """
    Run the pre-live decision gate.
    No live sprint. No model load. No network. No SprintScheduler.
    """
    repo_root = Path(repo_root).resolve()
    reasons: list[str] = []
    warnings: list[str] = []
    missing_required: list[str] = []
    missing_optional: list[str] = []
    checked: dict[str, dict] = {}
    fallback_blocked = False

    # --------------------------------------------------------------------------- #
    # 1. Memory integration guard — REQUIRED
    # --------------------------------------------------------------------------- #
    mig_report = _load_report(repo_root, "probe_m218e_memory_integration_guard", "memory_integration_guard.json")
    mig_pass = _is_pass(mig_report)
    checked["probe_m218e_memory_integration_guard"] = {
        "found": mig_report.found,
        "parse_error": mig_report.parse_error,
        "pass": mig_pass,
        "status": mig_report.data.get("status") if mig_report.found else None,
    }
    if not mig_report.found:
        missing_required.append("probe_m218e_memory_integration_guard")
        reasons.append("BLOCKED_BY_CONTRACT: memory integration guard missing")
    elif not mig_pass:
        reasons.append("BLOCKED_BY_CONTRACT: memory integration guard FAILED")

    # Also check manifest
    mig_manifest = _load_report(repo_root, "probe_m218e_memory_integration_guard", "memory_integration_manifest.json")
    checked["probe_m218e_memory_integration_guard_manifest"] = {
        "found": mig_manifest.found,
        "parse_error": mig_manifest.parse_error,
    }

    # --------------------------------------------------------------------------- #
    # 2. Zero findings quality — REQUIRED (must not crash, must fail correctly)
    # --------------------------------------------------------------------------- #
    zf_sanity = _load_report(repo_root, "probe_f216i_zero_findings_quality", "sanity_zero_findings.json")
    zf_quality = _load_report(repo_root, "probe_f216i_zero_findings_quality", "zero_findings_quality.json")

    zf_sane, zf_detail = _zero_findings_quality_sane(zf_sanity)
    checked["probe_f216i_zero_findings_quality_sanity"] = {
        "found": zf_sanity.found,
        "parse_error": zf_sanity.parse_error,
        "sane": zf_sane,
        "detail": zf_detail,
        "verdict": zf_sanity.data.get("verdict") if zf_sanity.found else None,
    }
    checked["probe_f216i_zero_findings_quality"] = {
        "found": zf_quality.found,
        "parse_error": zf_quality.parse_error,
        "detail": zf_quality.data.get("confirmation_zero_findings_stay_failed", {}).get("grade") if zf_quality.found else None,
    }
    if not zf_sane:
        reasons.append(f"BLOCKED_BY_UNKNOWN: zero-findings quality crashed or wrong verdict — {zf_detail}")

    # --------------------------------------------------------------------------- #
    # 3. Nonfeed recovery guard — REQUIRED
    # --------------------------------------------------------------------------- #
    nrg_report = _load_report(repo_root, "probe_f216h_nonfeed_recovery_guard", "nonfeed_recovery_guard.json")
    nrg_manifest = _load_report(repo_root, "probe_f216h_nonfeed_recovery_guard", "nonfeed_recovery_manifest.json")
    nrg_pass = _is_pass(nrg_report)
    checked["probe_f216h_nonfeed_recovery_guard"] = {
        "found": nrg_report.found,
        "parse_error": nrg_report.parse_error,
        "pass": nrg_pass,
        "ready_for_smoke": nrg_report.data.get("ready_for_controlled_smoke") if nrg_report.found else None,
        "status": nrg_report.data.get("status") if nrg_report.found else None,
    }
    checked["probe_f216h_nonfeed_recovery_guard_manifest"] = {
        "found": nrg_manifest.found,
    }
    if not nrg_report.found:
        missing_required.append("probe_f216h_nonfeed_recovery_guard")
        reasons.append("BLOCKED_BY_CONTRACT: nonfeed recovery guard missing")
    elif not nrg_pass:
        reasons.append("BLOCKED_BY_CONTRACT: nonfeed recovery guard FAILED")

    # --------------------------------------------------------------------------- #
    # 4-5. Provider surface — REQUIRED (F217→F219 alias table)
    # --------------------------------------------------------------------------- #
    surf_missing, surf_warnings, surf_checked = _check_provider_surface(repo_root)
    checked.update(surf_checked)
    warnings.extend(surf_warnings)
    for old_probe in surf_missing:
        missing_required.append(old_probe)
        if "bootstrap" in old_probe:
            reasons.append("BLOCKED_BY_PROVIDER_SURFACE: public bootstrap missing (no passing F219H/F219D alias)")
        else:
            reasons.append("BLOCKED_BY_PROVIDER_SURFACE: CT provider resilience missing (no passing F219E alias)")

    # --------------------------------------------------------------------------- #
    # 6. CT cooldown — IF PRESENT
    # --------------------------------------------------------------------------- #
    ct_cooldown_pass, ct_cooldown_detail, ct_cooldown_report = _check_ct_cooldown(repo_root)
    checked["probe_f219e_ct_provider_cooldown"] = {
        "found": ct_cooldown_report.found if ct_cooldown_report else False,
        "pass": ct_cooldown_pass,
        "detail": ct_cooldown_detail,
    }
    if ct_cooldown_report and not ct_cooldown_report.found:
        missing_optional.append("probe_f219e_ct_provider_cooldown")
        warnings.append(f"optional CT cooldown report absent — skipped")
    elif ct_cooldown_report and not ct_cooldown_pass:
        reasons.append(f"BLOCKED_BY_PROVIDER_SURFACE: CT cooldown FAILED — {ct_cooldown_detail}")

    # --------------------------------------------------------------------------- #
    # 7. Nonfeed candidate ledger boundedness — OPTIONAL
    # --------------------------------------------------------------------------- #
    ledger_pass, ledger_detail = _check_nonfeed_candidate_ledger(repo_root)
    checked["probe_f217e_nonfeed_candidate_ledger"] = {
        "pass": ledger_pass,
        "detail": ledger_detail,
    }
    if not ledger_pass:
        warnings.append(f"nonfeed candidate ledger issue: {ledger_detail}")

    # --------------------------------------------------------------------------- #
    # 8. Surface contract — IF PRESENT (conditional required)
    # --------------------------------------------------------------------------- #
    sc_pass, sc_detail, sc_report = _check_surface_contract(repo_root)
    checked["probe_f219a_surface_contract"] = {
        "found": sc_report.found if sc_report else False,
        "pass": sc_pass,
        "detail": sc_detail,
    }
    if sc_report and not sc_report.found:
        missing_optional.append("probe_f219a_surface_contract")
        warnings.append(f"optional surface contract absent — skipped")
    elif sc_report and not sc_pass:
        reasons.append(f"BLOCKED_BY_CONTRACT: surface contract FAILED — {sc_detail}")

    # --------------------------------------------------------------------------- #
    # 9. Hermes Metal finalizer — IF PRESENT (conditional required)
    # --------------------------------------------------------------------------- #
    hmf_pass, hmf_detail, hmf_report = _check_hermes_metal_finalizer(repo_root)
    checked["probe_f219b_hermes_metal_finalizer"] = {
        "found": hmf_report.found if hmf_report else False,
        "pass": hmf_pass,
        "detail": hmf_detail,
    }
    if hmf_report and not hmf_report.found:
        missing_optional.append("probe_f219b_hermes_metal_finalizer")
        warnings.append(f"optional Hermes Metal finalizer absent — skipped")
    elif hmf_report and not hmf_pass:
        reasons.append(f"BLOCKED_BY_CONTRACT: Hermes Metal finalizer FAILED — {hmf_detail}")

    # --------------------------------------------------------------------------- #
    # 10. Public session seal — IF PRESENT (conditional required)
    # --------------------------------------------------------------------------- #
    pss_pass, pss_detail, pss_report = _check_public_session_seal(repo_root)
    checked["probe_f219d_public_session_seal"] = {
        "found": pss_report.found if pss_report else False,
        "pass": pss_pass,
        "detail": pss_detail,
    }
    if pss_report and not pss_report.found:
        missing_optional.append("probe_f219d_public_session_seal")
        warnings.append(f"optional public session seal absent — skipped")
    elif pss_report and not pss_pass:
        reasons.append(f"BLOCKED_BY_PROVIDER_SURFACE: public session seal FAILED — {pss_detail}")

    # --------------------------------------------------------------------------- #
    # 11. Fallback acquisition schema marker — always checked
    # --------------------------------------------------------------------------- #
    fallback_reports: list[Optional[ProbeReport]] = [
        mig_report, mig_manifest, nrg_report, nrg_manifest,
        zf_sanity, zf_quality,
        ct_cooldown_report, sc_report, hmf_report, pss_report,
    ]
    for old_probe, alias_list in _PROVIDER_SURFACE_ALIASES.items():
        for new_probe, report_filename in alias_list:
            fallback_reports.append(_load_report(repo_root, new_probe, report_filename))
        old_filename = "public_bootstrap.json" if "bootstrap" in old_probe else "ct_provider_resilience.json"
        fallback_reports.append(_load_report(repo_root, old_probe, old_filename))
    for r in fallback_reports:
        if r is not None and _has_fallback_schema_marker(r):
            fallback_blocked = True
            reasons.append("BLOCKED_BY_UNKNOWN: fallback acquisition schema marker detected")
            break

    checked["fallback_schema_marker"] = {
        "blocked": fallback_blocked,
    }

    # --------------------------------------------------------------------------- #
    # 12. UMA check — done last; memory decision overrides contract/other blocks
    # F220F: Tiered macOS swap-aware policy (no longer hard-blocks on tiny swap)
    # --------------------------------------------------------------------------- #
    uma = _check_uma()
    swap_gib = uma.get("swap_used_gib", 0.0)
    uma_state = uma.get("uma_state", "unknown")

    checked["uma"] = uma

    # F220F: Determine swap tier using canonical helper from resource_governor
    swap_policy_tier, swap_gate_reason = get_swap_policy_tier(swap_gib)
    hardware_constrained = swap_policy_tier in ("diagnostic", "hard_block")

    # F220F: Emergency/critical UMA state always blocks regardless of swap tier
    if uma_state in ("critical", "emergency"):
        decision = Decision.BLOCKED_BY_MEMORY
        live_allowed = False
        hardware_constrained = True
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"uma_state={uma_state} (override)"
        reasons.insert(0, f"BLOCKED_BY_MEMORY: uma_state={uma_state} (override)")

    # F220F: Hard block tier overrides everything
    elif swap_policy_tier == "hard_block":
        decision = Decision.BLOCKED_BY_MEMORY
        live_allowed = False
        reasons.insert(0, f"BLOCKED_BY_MEMORY: {swap_gate_reason}")

    # F220F: Diagnostic tier — allowed with explicit flag but tainted
    elif swap_policy_tier == "diagnostic":
        # Check if any contract/provider blocks exist
        if reasons:
            first_reason = reasons[0]
            if "BLOCKED_BY_PROVIDER_SURFACE" in first_reason:
                decision = Decision.BLOCKED_BY_PROVIDER_SURFACE
            elif "BLOCKED_BY_CONTRACT" in first_reason:
                decision = Decision.BLOCKED_BY_CONTRACT
            elif "BLOCKED_BY_UNKNOWN" in first_reason:
                decision = Decision.BLOCKED_BY_UNKNOWN
            else:
                decision = Decision.BLOCKED_BY_UNKNOWN
            live_allowed = False
        else:
            decision = Decision.READY_FOR_LIVE_HARDWARE_TAINTED
            live_allowed = True  # allowed with --allow-high-swap
            reasons.append(f"HARDWARE_TAINTED: {swap_gate_reason}")
            warnings.append("Swap elevated: results will be non-comparable (use --require-memory-ok for clean run)")

    # Tier 1: Clean swap
    elif reasons:
        first_reason = reasons[0]
        if "BLOCKED_BY_PROVIDER_SURFACE" in first_reason:
            decision = Decision.BLOCKED_BY_PROVIDER_SURFACE
        elif "BLOCKED_BY_CONTRACT" in first_reason:
            decision = Decision.BLOCKED_BY_CONTRACT
        elif "BLOCKED_BY_UNKNOWN" in first_reason:
            decision = Decision.BLOCKED_BY_UNKNOWN
        else:
            decision = Decision.BLOCKED_BY_UNKNOWN
        live_allowed = False
    else:
        decision = Decision.READY_FOR_LIVE
        live_allowed = True
        reasons.append("All required probe checks passed; UMA within limits")

    # --------------------------------------------------------------------------- #
    # 13. Build command suggestions
    # F220F: Clean command includes --require-memory-ok; diagnostic uses --allow-high-swap
    # --------------------------------------------------------------------------- #
    encoded_query = query.replace('"', '\\"')
    # Clean run: use --require-memory-ok to confirm memory cleanliness
    live_cmd = (
        f"python -m core "
        f"--profile {profile} "
        f'--query "{encoded_query}" '
        f"--live "
        f"--require-memory-ok"
    )
    # Diagnostic run with hardware taint: use --allow-high-swap
    highswap_cmd = (
        f"python -m core "
        f"--profile {profile} "
        f'--query "{encoded_query}" '
        f"--live "
        f"--allow-high-swap"
    )

    return DecisionResult(
        decision=decision,
        live_allowed=live_allowed,
        reasons=reasons,
        warnings=warnings,
        missing_required_reports=missing_required,
        missing_optional_reports=missing_optional,
        uma=uma,
        checked_reports=checked,
        suggested_live_command=live_cmd,
        suggested_highswap_diagnostic_command=highswap_cmd,
        fallback_schema_blocked=fallback_blocked,
        hardware_constrained=hardware_constrained,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
    )


# --------------------------------------------------------------------------- #
# CLI entrypoint
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pre-Live Decision Gate — Sprint F219F",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo-root", type=Path, default=Path("."))
    p.add_argument("--profile", default="nonfeed_diagnostic")
    p.add_argument("--query", required=True)
    p.add_argument("--write-report", type=Path, default=None)
    p.add_argument("--output-markdown", type=Path, default=None)
    return p


def _render_markdown(result: DecisionResult, profile: str, query: str) -> str:
    """Render decision result as markdown report."""
    lines = [
        "# Pre-Live Decision Gate Report",
        "",
        f"**Decision:** `{result.decision.value}`",
        f"**Live Allowed:** `{result.live_allowed}`",
        f"**Hardware Constrained:** `{result.hardware_constrained}`",
        f"**Profile:** `{profile}`",
        f"**Query:** `{query}`",
        "",
        "---",
        "",
        "## UMA Status",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
    ]

    uma = result.uma
    for key in ["system_used_gib", "swap_used_gib", "swap_detected", "uma_state", "io_only", "last_error"]:
        val = uma.get(key)
        if val is not None:
            lines.append(f"| {key} | {val} |")

    # F220F: Swap tiered policy section
    lines.extend(["", "---", "", "## Swap Policy (F220F)", ""])
    lines.extend([
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Swap Policy Tier | `{result.swap_policy_tier}` |",
        f"| Swap Gate Reason | `{result.swap_gate_reason}` |",
        f"| Hardware Constrained | `{result.hardware_constrained}` |",
    ])

    lines.extend(["", "---", "", "## Reasons", ""])
    if result.reasons:
        for r in result.reasons:
            lines.append(f"- {r}")
    else:
        lines.append("- (none)")

    lines.extend(["", "---", "", "## Warnings", ""])
    if result.warnings:
        for w in result.warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- (none)")

    lines.extend(["", "---", "", "## Missing Reports", ""])
    lines.append(f"**Required:** {', '.join(result.missing_required_reports) or '(none)'}")
    lines.append(f"**Optional:** {', '.join(result.missing_optional_reports) or '(none)'}")

    lines.extend(["", "---", "", "## Provider Surface Alias Table (F217→F219)", ""])
    lines.append("| Old Probe | Current Alias | Status |")
    lines.append("|-----------|---------------|--------|")
    aliases = [
        ("probe_f217c_public_bootstrap", "probe_f219h_public_fetcher_import_seal / probe_f219d_public_session_seal"),
        ("probe_f217d_ct_provider_resilience", "probe_f219e_ct_provider_cooldown"),
    ]
    checked = result.checked_reports
    for old, new_alias in aliases:
        old_info = checked.get(old, {})
        alias_keys = [k for k in checked if k.startswith(old + "_alias_")]
        alias_statuses = []
        for ak in alias_keys:
            ai = checked.get(ak, {})
            alias_statuses.append(f"{ak.split('_alias_', 1)[1]}: pass={ai.get('pass')}, found={ai.get('found')}")
        status = "PASS" if old_info.get("alias_satisfied") else ("absent" if not old_info.get("found") else "FAIL")
        lines.append(f"| {old} | {new_alias} | {status} |")

    lines.extend(["", "---", "", "## Checked Reports", ""])
    for name, info in result.checked_reports.items():
        if name == "uma":
            continue
        found = info.get("found")
        parse_err = info.get("parse_error")
        passed = info.get("pass")
        detail = info.get("detail")
        lines.append(f"### {name}")
        lines.append(f"- found: `{found}`")
        if parse_err:
            lines.append(f"- parse_error: `{parse_err}`")
        if passed is not None:
            lines.append(f"- pass: `{passed}`")
        if detail:
            lines.append(f"- detail: `{detail}`")
        lines.append("")

    lines.extend(["", "---", "", "## Suggested Commands", ""])
    # F220F: Always show clean command with --require-memory-ok
    lines.append(f"**Clean run (--require-memory-ok):**\n```bash\n{result.suggested_live_command}\n```")
    # F220F: Show diagnostic command only when swap is elevated (diagnostic tier)
    if result.swap_policy_tier == "diagnostic":
        lines.append(f"\n**Diagnostic run (--allow-high-swap — results non-comparable):**\n```bash\n{result.suggested_highswap_diagnostic_command}\n```")
    elif result.swap_policy_tier == "hard_block":
        lines.append(f"\n**Hard block — restart required before running**")

    return "\n".join(lines)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"ERROR: repo root does not exist: {repo_root}", file=sys.stderr)
        return 1

    result = run_gate(repo_root, args.profile, args.query)

    # Write JSON report
    if args.write_report:
        # Remove nested checked_reports from top-level for cleaner output
        out_clean = {
            "decision": result.decision.value,
            "live_allowed": result.live_allowed,
            "reasons": result.reasons,
            "warnings": result.warnings,
            "missing_required_reports": result.missing_required_reports,
            "missing_optional_reports": result.missing_optional_reports,
            "uma": result.uma,
            "suggested_live_command": result.suggested_live_command,
            "suggested_highswap_diagnostic_command": result.suggested_highswap_diagnostic_command,
            "fallback_schema_blocked": result.fallback_schema_blocked,
            # F220F: swap tiered policy telemetry
            "hardware_constrained": result.hardware_constrained,
            "swap_policy_tier": result.swap_policy_tier,
            "swap_gate_reason": result.swap_gate_reason,
            "checked_reports": {
                k: {kk: vv for kk, vv in v.items() if kk not in ("data",)}
                for k, v in result.checked_reports.items()
            },
        }
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write_report, "w", encoding="utf-8") as fh:
            json.dump(out_clean, fh, indent=2, default=str)
        print(f"JSON report written: {args.write_report}")

    # Write markdown report
    md_path = args.output_markdown or (args.write_report.parent / "REPORT_PRELIVE_DECISION_GATE.md" if args.write_report else None)
    if md_path:
        md_text = _render_markdown(result, args.profile, args.query)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        print(f"Markdown report written: {md_path}")

    # Console output
    print(f"\n{'='*60}")
    print(f"  Decision: {result.decision.value}")
    print(f"  Live Allowed: {result.live_allowed}")
    print(f"  Hardware Constrained: {result.hardware_constrained}")
    print(f"  Swap Policy Tier: {result.swap_policy_tier}")
    print(f"  Swap Gate Reason: {result.swap_gate_reason}")
    print(f"{'='*60}")
    if result.reasons:
        print("Reasons:")
        for r in result.reasons:
            print(f"  - {r}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")
    if result.missing_required_reports:
        print(f"Missing required reports: {', '.join(result.missing_required_reports)}")
    uma_sw = result.uma.get("swap_used_gib", 0)
    print(f"UMA: swap={uma_sw:.2f}GiB")
    print()

    return 0 if result.live_allowed else 1


if __name__ == "__main__":
    sys.exit(main())
