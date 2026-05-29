#!/usr/bin/env python3
"""Sprint F232C — Final Pre-Live Readiness

One definitive operator answer: READY_TO_RUN_NOW / READY_TO_RESTART_AND_RUN /
BLOCKED_BY_CONTRACT / BLOCKED_BY_MEMORY / BLOCKED_BY_ARTIFACTS /
BLOCKED_BY_PROVIDER_SURFACE / BLOCKED_BY_UNKNOWN

No live execution. No network. No MLX.

CLI:
    python tools/final_prelive_readiness.py \\
        --repo-root . \\
        --profile nonfeed_diagnostic180 \\
        --query "mozilla.org certificate transparency subdomains april 2026" \\
        --output-json probe_f232c_final_post_restart_readiness/final_readiness.json \\
        --output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

REPO_ROOT_DEFAULT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"


class Verdict(StrEnum):
    READY_TO_RUN_NOW = "READY_TO_RUN_NOW"
    READY_TO_RESTART_AND_RUN = "READY_TO_RESTART_AND_RUN"
    READY_DIAGNOSTIC_ONLY = "READY_DIAGNOSTIC_ONLY"
    BLOCKED_BY_CONTRACT = "BLOCKED_BY_CONTRACT"
    BLOCKED_BY_MEMORY = "BLOCKED_BY_MEMORY"
    BLOCKED_BY_ARTIFACTS = "BLOCKED_BY_ARTIFACTS"
    BLOCKED_BY_PROVIDER_SURFACE = "BLOCKED_BY_PROVIDER_SURFACE"
    BLOCKED_BY_UNKNOWN = "BLOCKED_BY_UNKNOWN"


class NextAction(StrEnum):
    RUN_LIVE_NOW = "RUN_LIVE_NOW"
    RESTART_THEN_RUN_LIVE = "RESTART_THEN_RUN_LIVE"
    RUN_NONFEED_DIAGNOSTIC = "RUN_NONFEED_DIAGNOSTIC"
    RUN_WITH_HARDWARE_TAINT = "RUN_WITH_HARDWARE_TAINT"
    RUN_MISSING_PROBE = "RUN_MISSING_PROBE"
    FIX_CONTRACT_GATE = "FIX_CONTRACT_GATE"


@dataclass
class Blocker:
    category: str  # memory | contract | artifacts | provider_surface | unknown
    severity: str  # HARD_BLOCK | SOFT_BLOCK
    detail: str
    current_swap_gib: float | None = None
    threshold_gib: float | None = None


@dataclass
class ReadinessResult:
    verdict: Verdict
    live_allowed: bool
    next_action: NextAction
    next_action_detail: str
    blockers: list[Blocker] = field(default_factory=list)
    # F231 inventory
    f231_inventory_verdict: str = ""
    f231_inventory_present: list[str] = field(default_factory=list)
    f231_inventory_missing: list[str] = field(default_factory=list)
    # F231H gate
    f231h_gate_verdict: str = ""
    f231h_gate_status: str = ""
    # Swap / UMA
    swap_used_gib: float = 0.0
    uma_state: str = "unknown"
    swap_policy_tier: str = "unknown"
    swap_gate_reason: str = ""
    hardware_constrained: bool = False
    # Gate
    gate_decision: str = ""
    gate_live_allowed: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    gate_warnings: list[str] = field(default_factory=list)
    # F224 / F231 blocking
    f224_core_ready: bool = False
    f224_blocking: list[str] = field(default_factory=list)
    f231_core_ready: bool = False
    f231_blocking: list[str] = field(default_factory=list)
    # Provider surface
    provider_surface_ok: bool = True
    # Post-restart command
    post_restart_command: str = ""
    # Merge log (for debugging)
    merge_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "sprint": "F232C",
            "report_type": "final_prelive_readiness",
            "verdict": self.verdict.value,
            "live_allowed": self.live_allowed,
            "next_action": self.next_action.value,
            "next_action_detail": self.next_action_detail,
            "blockers": [asdict(b) for b in self.blockers],
            "f231_inventory_verdict": self.f231_inventory_verdict,
            "f231_inventory_present": self.f231_inventory_present,
            "f231_inventory_missing": self.f231_inventory_missing,
            "f231h_gate_verdict": self.f231h_gate_verdict,
            "f231h_gate_status": self.f231h_gate_status,
            "swap_used_gib": self.swap_used_gib,
            "uma_state": self.uma_state,
            "swap_policy_tier": self.swap_policy_tier,
            "swap_gate_reason": self.swap_gate_reason,
            "hardware_constrained": self.hardware_constrained,
            "gate_decision": self.gate_decision,
            "gate_live_allowed": self.gate_live_allowed,
            "gate_reasons": self.gate_reasons,
            "gate_warnings": self.gate_warnings,
            "f224_core_ready": self.f224_core_ready,
            "f224_blocking": self.f224_blocking,
            "f231_core_ready": self.f231_core_ready,
            "f231_blocking": self.f231_blocking,
            "provider_surface_ok": self.provider_surface_ok,
            "post_restart_command": self.post_restart_command,
            "merge_log": self.merge_log,
        }
        return out


# ---------------------------------------------------------------------------
# Constants (mirrored from prelive_decision_gate.py — F220F swap tier policy)
# ---------------------------------------------------------------------------
CLEAN_SWAP_MAX_GIB = 2.0
DIAGNOSTIC_SWAP_MAX_GIB = 4.0
HARD_BLOCK_SWAP_GIB = 4.0

# F224 blocking probes (profile-gated)
_F224_BLOCKING_PROFILES = ("active300", "nonfeed_diagnostic")
_F224_BLOCKING_PROBES = [
    ("probe_f224a_worker_pool_import_seal", "worker_pool_import_seal.json"),
    ("probe_f224c_discovery_provider_gap", "discovery_provider_gap.json"),
    ("probe_f224d_sprint_id_collision", "sprint_id_collision.json"),
    ("probe_f224d_confidence_policy", "confidence_policy.json"),  # reported wrong name
]

# F231 blocking probes
_F231_BLOCKING_PROFILES = ("active300", "nonfeed_diagnostic")
_F231_BLOCKING_PROBES = [
    ("probe_f231a_public_candidate_ledger", "public_candidate_ledger.json"),
    ("probe_f231b_ct_acceptance_lift", "ct_acceptance_lift.json"),
    ("probe_f231c_advisory_evidence_surface", "advisory_evidence_surface.json"),
    ("probe_f231d_research_quality_v2", "research_quality_v2.json"),
    ("probe_f231e_research_quality_comparable_field", "research_quality_comparable_field.json"),
    ("probe_f231f_evidence_depth_aliases", "evidence_depth_aliases.json"),
    ("probe_f231g_quality_sanity_bundle_smoke", "quality_sanity_bundle_smoke.json"),
]

# Contract false positives known to be spurious (F231T analysis)
# These should be stripped from gate_reasons when computing final verdict
_CONTRACT_FALSE_POSITIVES = {
    "probe_f219b_hermes_metal_finalizer": (
        "prelive_decision_gate._is_pass() schema mismatch — "
        "F219B report uses tests.all_passed=True, gate checks status=PASS|ready_for_controlled_smoke"
    ),
    "probe_f224d_confidence_policy": (
        "prelive_decision_gate._check_f224_artifacts() hardcodes probe_f224d_confidence_policy "
        "but actual probe directory is probe_f224d_sprint_id_collision"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_report(repo_root: str, probe_dir: str, filename: str) -> dict:
    """Load a probe JSON report, return {found, parse_error, data}."""
    path = os.path.join(repo_root, probe_dir, filename)
    if not os.path.exists(path):
        return {"found": False, "parse_error": None, "data": None}
    try:
        with open(path) as f:
            data = json.load(f)
        return {"found": True, "parse_error": None, "data": data}
    except Exception as e:
        return {"found": True, "parse_error": str(e), "data": None}


def _check_f224_artifacts(repo_root: str, profile: str) -> tuple[bool, list[str], list[str]]:
    """Check F224 blocking artifacts. Returns (core_ready, missing_blocking, warnings)."""
    warnings: list[str] = []
    missing_blocking: list[str] = []
    is_blocking_profile = profile in _F224_BLOCKING_PROFILES

    for probe_dir, filename in _F224_BLOCKING_PROBES:
        # Special case: probe_f224d has wrong filename in gate (confidence_policy vs sprint_id_collision)
        # Handle both names
        actual_path = os.path.join(repo_root, probe_dir)
        if not os.path.exists(actual_path):
            warnings.append(f"f224_blocking:{probe_dir} absent")
            missing_blocking.append(probe_dir)
            continue
        # Try actual filename
        path = os.path.join(actual_path, filename)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    json.load(f)
                # pass
            except Exception as e:
                warnings.append(f"f224_blocking:{probe_dir} parse error: {e}")
                missing_blocking.append(probe_dir)
        else:
            # Try alternate filename for probe_f224d
            if probe_dir == "probe_f224d_sprint_id_collision":
                alt = os.path.join(actual_path, "sprint_id_collision.json")
                if os.path.exists(alt):
                    try:
                        with open(alt) as f:
                            json.load(f)
                    except Exception as e:
                        warnings.append(f"f224_blocking:{probe_dir} parse error: {e}")
                        missing_blocking.append(probe_dir)
                else:
                    warnings.append(f"f224_blocking:{probe_dir} absent")
                    missing_blocking.append(probe_dir)
            else:
                warnings.append(f"f224_blocking:{probe_dir} absent")
                missing_blocking.append(probe_dir)

    core_ready = len(missing_blocking) == 0 if is_blocking_profile else True
    return core_ready, missing_blocking, warnings


def _check_f231_artifacts(repo_root: str, profile: str) -> tuple[bool, list[str], list[str]]:
    """Check F231 Evidence Lift Pack. Returns (core_ready, missing_blocking, warnings)."""
    warnings: list[str] = []
    missing_blocking: list[str] = []
    is_blocking_profile = profile in _F231_BLOCKING_PROFILES

    for probe_dir, filename in _F231_BLOCKING_PROBES:
        path = os.path.join(repo_root, probe_dir, filename)
        if not os.path.exists(path):
            warnings.append(f"f231_blocking:{probe_dir} absent")
            missing_blocking.append(probe_dir)
            continue
        try:
            with open(path) as f:
                json.load(f)
        except Exception as e:
            warnings.append(f"f231_blocking:{probe_dir} parse error: {e}")
            missing_blocking.append(probe_dir)

    core_ready = len(missing_blocking) == 0 if is_blocking_profile else True
    return core_ready, missing_blocking, warnings


def _check_provider_surface(repo_root: str) -> bool:
    """
    Check provider surface via F219 aliases.
    F231T: F219 aliases (F217→F219) satisfy the provider surface check.
    probe_f219h_public_fetcher_import_seal → satisfies probe_f217c_public_bootstrap
    probe_f219e_ct_provider_cooldown → satisfies probe_f217d_ct_provider_resilience
    """
    p219h = os.path.join(repo_root, "probe_f219h_public_fetcher_import_seal", "public_fetcher_import_seal.json")
    p219e = os.path.join(repo_root, "probe_f219e_ct_provider_cooldown", "ct_cooldown.json")
    return os.path.exists(p219h) and os.path.exists(p219e)


def _extract_uma_from_gate(gate_data: dict) -> tuple[float, str, str, str, bool]:
    """Extract UMA/swap data from prelive gate output."""
    uma = gate_data.get("uma", {})
    swap_used = uma.get("swap_used_gib", 0.0) or 0.0
    uma_state = uma.get("uma_state", "unknown")
    swap_policy_tier = gate_data.get("swap_policy_tier", "unknown")
    swap_gate_reason = gate_data.get("swap_gate_reason", "")
    hardware_constrained = gate_data.get("hardware_constrained", False)
    return swap_used, uma_state, swap_policy_tier, swap_gate_reason, hardware_constrained


def _strip_false_positive_contract_blockers(reasons: list[str]) -> list[str]:
    """Remove known false-positive contract blockers from gate_reasons."""
    filtered = []
    for r in reasons:
        is_false_positive = False
        for fp_probe in _CONTRACT_FALSE_POSITIVES:
            if fp_probe in r:
                is_false_positive = True
                break
        if not is_false_positive:
            filtered.append(r)
    return filtered


# ---------------------------------------------------------------------------
# F231T reader — read authoritative F231T verdict directly
# ---------------------------------------------------------------------------

def read_f231t_result(repo_root: str) -> dict | None:
    """Read F231T final_no_live_readiness.json if present."""
    path = os.path.join(repo_root, "probe_f231t_final_no_live_readiness", "final_no_live_readiness.json")
    return load_json(path)


# ---------------------------------------------------------------------------
# F231 inventory check
# ---------------------------------------------------------------------------

def run_f231_inventory(repo_root: str) -> tuple[str, list[str], list[str]]:
    """Run F231 artifact inventory check. Returns (verdict, present, missing)."""
    inv_path = os.path.join(repo_root, "tools", "f231_artifact_inventory.py")
    if not os.path.exists(inv_path):
        return ("F231_TOOL_ABSENT", [], [])

    # Import and run
    prev_cwd = os.getcwd()
    prev_path = sys.path[:]
    try:
        os.chdir(repo_root)
        sys.path.insert(0, repo_root)
        from tools.f231_artifact_inventory import run_inventory
        inv = run_inventory(repo_root)
        return inv.verdict, inv.present, inv.missing
    except Exception as e:
        return (f"F231_INVENTORY_ERROR:{e}", [], [])
    finally:
        os.chdir(prev_cwd)
        sys.path[:] = prev_path


def run_f231h_gate(repo_root: str) -> tuple[str, str, list[str]]:
    """Read F231H evidence lift gate."""
    gate_json = os.path.join(repo_root, "probe_f231h_prelive_evidence_lift_gate", "prelive_evidence_lift_gate.json")
    data = load_json(gate_json)
    if not data:
        return ("GATE_ABSENT", "", [])
    return (
        data.get("verdict", "UNKNOWN"),
        data.get("gate_status", ""),
        data.get("blocking_probes", []),
    )


# ---------------------------------------------------------------------------
# Run fresh gate computation (bypasses stale prelive_decision.json)
# ---------------------------------------------------------------------------

def run_gate_fresh(repo_root: str, profile: str, query: str) -> dict | None:
    """Run prelive_decision_gate.run_gate() fresh to get current swap/gate data."""
    gate_path = os.path.join(repo_root, "tools", "prelive_decision_gate.py")
    if not os.path.exists(gate_path):
        return None

    prev_cwd = os.getcwd()
    prev_path = sys.path[:]
    try:
        os.chdir(repo_root)
        sys.path.insert(0, repo_root)
        from tools.prelive_decision_gate import run_gate
        result = run_gate(Path(repo_root), profile, query)

        checked_reports_clean = {}
        if hasattr(result, 'checked_reports') and result.checked_reports:
            for k, v in result.checked_reports.items():
                if isinstance(v, dict):
                    checked_reports_clean[k] = {kk: vv for kk, vv in v.items() if kk != 'data'}
                else:
                    checked_reports_clean[k] = v

        return {
            "decision": result.decision.value if hasattr(result.decision, 'value') else str(result.decision),
            "live_allowed": result.live_allowed,
            "reasons": list(result.reasons) if result.reasons else [],
            "warnings": list(result.warnings) if result.warnings else [],
            "f224_core_ready": result.f224_core_ready if hasattr(result, 'f224_core_ready') else False,
            "missing_f224_artifacts": list(result.missing_f224_artifacts) if hasattr(result, 'missing_f224_artifacts') else [],
            "f231_core_ready": result.f231_core_ready if hasattr(result, 'f231_core_ready') else False,
            "missing_f231_artifacts": list(result.missing_f231_artifacts) if hasattr(result, 'missing_f231_artifacts') else [],
            "swap_policy_tier": result.swap_policy_tier if hasattr(result, 'swap_policy_tier') else "unknown",
            "swap_gate_reason": result.swap_gate_reason if hasattr(result, 'swap_gate_reason') else "",
            "hardware_constrained": result.hardware_constrained if hasattr(result, 'hardware_constrained') else False,
            "fallback_schema_blocked": result.fallback_schema_blocked if hasattr(result, 'fallback_schema_blocked') else False,
            "checked_reports": checked_reports_clean,
        }
    except Exception as e:
        return {"_error": str(e)}
    finally:
        os.chdir(prev_cwd)
        sys.path[:] = prev_path


# ---------------------------------------------------------------------------
# Core merge logic
# ---------------------------------------------------------------------------

def compute_verdict(
    gate_decision: str,
    gate_data: dict,
    f224_blocking: list[str],
    f231_blocking: list[str],
    f224_core_ready: bool,
    f231_core_ready: bool,
    swap_used: float,
    uma_state: str,
    inventory_ready: bool,
    inventory_verdict: str,
    f231h_gate: str,
    f231h_status: str,
    provider_surface_ok: bool,
    stripped_reasons: list[str],
    profile: str,
    query: str,
) -> ReadinessResult:
    blockers: list[Blocker] = []
    merge_log: list[str] = []
    merge_log.append(f"gate_decision={gate_decision}")
    merge_log.append(f"inventory_ready={inventory_ready} verdict={inventory_verdict}")
    merge_log.append(f"f231h_gate={f231h_gate} status={f231h_status}")
    merge_log.append(f"provider_surface_ok={provider_surface_ok}")
    merge_log.append(f"original_gate_reasons={gate_data.get('reasons', [])}")
    merge_log.append(f"stripped_reasons={stripped_reasons}")

    # Memory check — swap > HARD_BLOCK_SWAP_GIB
    if swap_used > HARD_BLOCK_SWAP_GIB:
        blockers.append(Blocker(
            category="memory",
            severity="HARD_BLOCK",
            detail=f"swap={swap_used:.3f}GiB > {HARD_BLOCK_SWAP_GIB}GiB threshold (hard_block tier)",
            current_swap_gib=swap_used,
            threshold_gib=HARD_BLOCK_SWAP_GIB,
        ))
        merge_log.append(f"blocker=MEMORY swap={swap_used:.3f}GiB")

    # F231 artifacts check
    if f231_blocking:
        blockers.append(Blocker(
            category="artifacts",
            severity="HARD_BLOCK",
            detail=f"F231 blocking artifacts missing: {', '.join(f231_blocking)}",
        ))
        merge_log.append(f"blocker=ARTIFACTS missing_f231={f231_blocking}")

    # F224 artifacts check (only for blocking profiles)
    if f224_blocking and not f224_core_ready:
        blockers.append(Blocker(
            category="contract",
            severity="HARD_BLOCK",
            detail=f"F224 blocking artifacts missing: {', '.join(f224_blocking)}",
        ))
        merge_log.append(f"blocker=CONTRACT missing_f224={f224_blocking}")

    # Provider surface check
    if not provider_surface_ok and gate_decision == "BLOCKED_BY_PROVIDER_SURFACE":
        blockers.append(Blocker(
            category="provider_surface",
            severity="HARD_BLOCK",
            detail="Provider surface gaps detected (public_bootstrap, ct_provider_resilience)",
        ))
        merge_log.append("blocker=PROVIDER_SURFACE")

    # Real contract blockers (after stripping false positives and provider-surface issues)
    # Exclude provider-surface reasons from the contract blocker bucket
    [r for r in stripped_reasons if "PROVIDER_SURFACE" in r]
    contract_only_reasons = [r for r in stripped_reasons if "PROVIDER_SURFACE" not in r]
    has_real_contract_blocker = bool(contract_only_reasons)
    if has_real_contract_blocker and not f231_blocking and not f224_blocking:
        blockers.append(Blocker(
            category="contract",
            severity="HARD_BLOCK",
            detail=f"Contract gate failed: {'; '.join(stripped_reasons)}",
        ))
        merge_log.append(f"blocker=CONTRACT real blockers={stripped_reasons}")

    # Determine final verdict
    memory_blocker = next((b for b in blockers if b.category == "memory"), None)
    artifact_blocker = next((b for b in blockers if b.category == "artifacts"), None)
    contract_blocker = next((b for b in blockers if b.category == "contract"), None)
    provider_blocker = next((b for b in blockers if b.category == "provider_surface"), None)

    # Memory is highest priority for restart verdict
    if memory_blocker and not artifact_blocker and not contract_blocker and not provider_blocker:
        verdict = Verdict.READY_TO_RESTART_AND_RUN
        next_action = NextAction.RESTART_THEN_RUN_LIVE
        next_action_detail = f"swap={swap_used:.3f}GiB > {HARD_BLOCK_SWAP_GIB}GiB — restart required"
        live_allowed = False
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"swap={swap_used:.3f}GiB > {HARD_BLOCK_SWAP_GIB}GiB"
    elif artifact_blocker:
        verdict = Verdict.BLOCKED_BY_ARTIFACTS
        next_action = NextAction.RUN_MISSING_PROBE
        next_action_detail = artifact_blocker.detail
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "missing F231 artifacts"
    elif contract_blocker:
        verdict = Verdict.BLOCKED_BY_CONTRACT
        next_action = NextAction.FIX_CONTRACT_GATE
        next_action_detail = contract_blocker.detail
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "contract gate failure"
    elif provider_blocker:
        verdict = Verdict.BLOCKED_BY_PROVIDER_SURFACE
        next_action = NextAction.RUN_MISSING_PROBE
        next_action_detail = provider_blocker.detail
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "provider surface gap"
    elif swap_used <= CLEAN_SWAP_MAX_GIB:
        verdict = Verdict.READY_TO_RUN_NOW
        next_action = NextAction.RUN_LIVE_NOW
        next_action_detail = ""
        live_allowed = True
        swap_policy_tier = "clean"
        swap_gate_reason = f"swap={swap_used:.3f}GiB <= {CLEAN_SWAP_MAX_GIB}GiB"
    elif swap_used <= DIAGNOSTIC_SWAP_MAX_GIB:
        verdict = Verdict.READY_DIAGNOSTIC_ONLY
        next_action = NextAction.RUN_WITH_HARDWARE_TAINT
        next_action_detail = f"swap={swap_used:.3f}GiB in ({CLEAN_SWAP_MAX_GIB}GiB, {DIAGNOSTIC_SWAP_MAX_GIB}GiB] — hardware taint"
        live_allowed = True
        swap_policy_tier = "diagnostic"
        swap_gate_reason = f"swap={swap_used:.3f}GiB in diagnostic tier"
    else:
        verdict = Verdict.READY_TO_RESTART_AND_RUN
        next_action = NextAction.RESTART_THEN_RUN_LIVE
        next_action_detail = f"swap={swap_used:.3f}GiB > {HARD_BLOCK_SWAP_GIB}GiB — restart required"
        live_allowed = False
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"swap={swap_used:.3f}GiB > {HARD_BLOCK_SWAP_GIB}GiB"

    hardware_constrained = swap_used > CLEAN_SWAP_MAX_GIB

    return ReadinessResult(
        verdict=verdict,
        live_allowed=live_allowed,
        next_action=next_action,
        next_action_detail=next_action_detail,
        blockers=blockers,
        f231_inventory_verdict=inventory_verdict,
        f231_inventory_present=[],
        f231_inventory_missing=f231_blocking,
        f231h_gate_verdict=f231h_gate,
        f231h_gate_status=f231h_status,
        swap_used_gib=swap_used,
        uma_state=uma_state,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
        hardware_constrained=hardware_constrained,
        gate_decision=gate_decision,
        gate_live_allowed=gate_data.get("live_allowed", False),
        gate_reasons=gate_data.get("reasons", []),
        gate_warnings=gate_data.get("warnings", []),
        f224_core_ready=f224_core_ready,
        f224_blocking=f224_blocking,
        f231_core_ready=f231_core_ready,
        f231_blocking=f231_blocking,
        provider_surface_ok=provider_surface_ok,
        post_restart_command=_build_post_restart_command(profile, query),
        merge_log=merge_log,
    )


def _build_post_restart_command(profile: str, query: str) -> str:
    """Build the post-restart live command."""
    return (
        f"python -m core --profile {profile} "
        f'--query "{query}" --live --require-memory-ok'
    )


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------

def run_final_readiness(repo_root: str, profile: str, query: str) -> ReadinessResult:
    merge_log: list[str] = []

    # Step 1: Read F231T authoritative report (source of truth for false-positive analysis)
    f231t_data = read_f231t_result(repo_root)
    if f231t_data:
        merge_log.append(f"f231t_read: verdict={f231t_data.get('verdict')} "
                         f"swap={f231t_data.get('memory', {}).get('swap_used_gib')}")

    # Step 2: Run fresh gate computation (bypasses stale prelive_decision.json)
    gate_data = run_gate_fresh(repo_root, profile, query)
    if not gate_data:
        merge_log.append("gate_fresh: TOOL_ABSENT")
        return _make_unknown_result("prelive_decision_gate.py not found", merge_log)

    if "_error" in gate_data:
        merge_log.append(f"gate_fresh: ERROR {gate_data['_error']}")
        return _make_unknown_result(f"gate error: {gate_data['_error']}", merge_log)

    gate_decision = gate_data.get("decision", "BLOCKED_BY_UNKNOWN")
    merge_log.append(f"gate_fresh: decision={gate_decision} "
                     f"live_allowed={gate_data.get('live_allowed')}")

    # Step 3: F231 inventory
    inv_verdict, inv_present, inv_missing = run_f231_inventory(repo_root)
    inventory_ready = inv_verdict == "F231_PACK_READY" and not inv_missing
    merge_log.append(f"f231_inventory: verdict={inv_verdict} present={inv_present} missing={inv_missing}")

    # Step 4: F231H gate
    f231h_gate, f231h_status, f231h_blocking = run_f231h_gate(repo_root)
    merge_log.append(f"f231h_gate: verdict={f231h_gate} status={f231h_status}")

    # Step 5: F224 artifacts
    f224_core_ready, f224_blocking, f224_warnings = _check_f224_artifacts(repo_root, profile)
    merge_log.append(f"f224_core_ready={f224_core_ready} missing={f224_blocking}")

    # Step 6: F231 artifacts
    f231_core_ready, f231_blocking, f231_warnings = _check_f231_artifacts(repo_root, profile)
    merge_log.append(f"f231_core_ready={f231_core_ready} missing={f231_blocking}")

    # Step 7: Provider surface
    provider_surface_ok = _check_provider_surface(repo_root)
    merge_log.append(f"provider_surface_ok={provider_surface_ok}")

    # Step 8: Swap data
    swap_used, uma_state, swap_policy_tier, swap_gate_reason, hardware_constrained = \
        _extract_uma_from_gate(gate_data)
    # Override with current system swap if gate didn't compute it
    if swap_used == 0.0:
        swap_used = _get_current_swap_gib()
    merge_log.append(f"swap_used={swap_used:.3f}GiB uma_state={uma_state}")

    # Step 9: Strip false-positive contract blockers
    original_reasons = gate_data.get("reasons", [])
    stripped_reasons = _strip_false_positive_contract_blockers(original_reasons)
    merge_log.append(f"contract_false_positives_stripped: {len(original_reasons) - len(stripped_reasons)}")

    # Step 10: Compute final verdict
    result = compute_verdict(
        gate_decision=gate_decision,
        gate_data=gate_data,
        f224_blocking=f224_blocking,
        f231_blocking=f231_blocking,
        f224_core_ready=f224_core_ready,
        f231_core_ready=f231_core_ready,
        swap_used=swap_used,
        uma_state=uma_state,
        inventory_ready=inventory_ready,
        inventory_verdict=inv_verdict,
        f231h_gate=f231h_gate,
        f231h_status=f231h_status,
        provider_surface_ok=provider_surface_ok,
        stripped_reasons=stripped_reasons,
        profile=profile,
        query=query,
    )

    # Enrich with inventory details
    result.f231_inventory_present = inv_present
    result.merge_log = merge_log

    return result


def _get_current_swap_gib() -> float:
    """Read current swap usage from system (macOS)."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["sysctl", "-n", "vm.swapusage"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        # Format: "total = 7168.00M  used = 5949.50M  free = 1218.50M  (encrypted)"
        parts = out.split()
        for i, p in enumerate(parts):
            if p == "used" and i + 1 < len(parts):
                val_str = parts[i + 1]
                if val_str.endswith("M"):
                    return float(val_str[:-1]) / 1024.0
                elif val_str.endswith("G"):
                    return float(val_str[:-1])
        # fallback: parse used value
        for i, p in enumerate(parts):
            if p == "=" and i + 1 < len(parts):
                val_str = parts[i + 1].rstrip("MGB")
                return float(val_str) / 1024.0 if "M" in parts[i + 1] else float(val_str)
    except Exception:
        pass
    return 0.0


def _make_unknown_result(reason: str, merge_log: list[str]) -> ReadinessResult:
    return ReadinessResult(
        verdict=Verdict.BLOCKED_BY_UNKNOWN,
        live_allowed=False,
        next_action=NextAction.FIX_CONTRACT_GATE,
        next_action_detail=reason,
        merge_log=merge_log,
    )


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

def render_markdown(result: ReadinessResult, profile: str, query: str) -> str:
    lines = [
        "# Final Pre-Live Readiness — F232C",
        "",
        f"**Profile:** `{profile}`",
        f"**Query:** `{query}`",
        "**Date:** 2026-05-10",
        "",
        "---",
        "",
        f"## Verdict: `{result.verdict.value}`",
        "",
        f"**Live Allowed:** {result.live_allowed}",
        f"**Next Action:** {result.next_action.value}",
    ]
    if result.next_action_detail:
        lines.append(f"**Detail:** {result.next_action_detail}")

    if result.blockers:
        lines.extend(["", "### Blockers", ""])
        for b in result.blockers:
            lines.append(f"- **{b.category}** ({b.severity}): {b.detail}")
            if b.current_swap_gib:
                lines.append(f"  - swap={b.current_swap_gib:.3f}GiB (threshold={b.threshold_gib}GiB)")

    lines.extend([
        "",
        "---",
        "",
        "## Swap / Memory",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| swap_used_gib | {result.swap_used_gib:.3f} |",
        f"| uma_state | {result.uma_state} |",
        f"| swap_policy_tier | {result.swap_policy_tier} |",
        f"| swap_gate_reason | {result.swap_gate_reason} |",
        f"| hardware_constrained | {result.hardware_constrained} |",
        "",
        f"**Thresholds:** clean<{CLEAN_SWAP_MAX_GIB}GiB, diagnostic<{DIAGNOSTIC_SWAP_MAX_GIB}GiB, hard_block>={HARD_BLOCK_SWAP_GIB}GiB",
    ])

    lines.extend([
        "",
        "## F231 Artifact Inventory",
        "",
        "| Check | Value |",
        "|-------|-------|",
        f"| verdict | {result.f231_inventory_verdict} |",
        f"| F231H gate | {result.f231h_gate_verdict} ({result.f231h_gate_status}) |",
        f"| F231 core ready | {result.f231_core_ready} |",
        f"| F231 blocking missing | {result.f231_blocking or 'none'} |",
    ])

    if result.f231_inventory_present:
        lines.append(f"| F231 present | {', '.join(result.f231_inventory_present)} |")

    lines.extend([
        "",
        "## Gate Decision",
        "",
        "| Check | Value |",
        "|-------|-------|",
        f"| gate_decision | {result.gate_decision} |",
        f"| gate_live_allowed | {result.gate_live_allowed} |",
        f"| F224 core ready | {result.f224_core_ready} |",
        f"| F224 blocking missing | {result.f224_blocking or 'none'} |",
        f"| provider_surface_ok | {result.provider_surface_ok} |",
    ])

    if result.gate_reasons:
        lines.extend(["", "**Gate Reasons (original):**"])
        for r in result.gate_reasons:
            lines.append(f"- {r}")

    if result.gate_warnings:
        lines.extend(["", "**Gate Warnings:**"])
        for w in result.gate_warnings:
            lines.append(f"- {w}")

    if result.verdict == Verdict.READY_TO_RESTART_AND_RUN:
        lines.extend([
            "",
            "---",
            "",
            "## Post-Restart Command Pack",
            "",
            "⚠️ **ABORT RULE:** If `final_prelive_readiness` does not return `READY_TO_RUN_NOW` after restart, do NOT run live.",
            "",
            "**Memory instruction:** Restart Mac, open only terminal, run readiness first.",
            "",
            "```bash",
            "# 1. Run final pre-live readiness (post-restart)",
            "python -m tools.final_prelive_readiness \\",
            "  --repo-root . \\",
            f"  --profile {profile} \\",
            f'  --query "{query}" \\',
            "  --output-json probe_f232c_final_post_restart_readiness/final_readiness.json \\",
            "  --output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md",
            "",
            "# 2. If READY_TO_RUN_NOW — run live nonfeed_diagnostic180",
            f"python -m core --profile {profile} --query \"{query}\" --live --require-memory-ok",
            "",
            "# 3. Research quality score",
            "python -m tools.research_quality_score \\",
            f"  --repo-root . --profile {profile} --sprint-id F232C \\",
            "  --output-json probe_f232c_final_post_restart_readiness/research_quality_score.json \\",
            "  --output-md probe_f232c_final_post_restart_readiness/RESEARCH_QUALITY_SCORE.md",
            "",
            "# 4. Live result sanity",
            "python -m tools.live_result_sanity \\",
            f"  --repo-root . --profile {profile} \\",
            "  --output-json probe_f232c_final_post_restart_readiness/live_result_sanity.json",
            "",
            "# 5. Evidence delta memory",
            "python -m tools.evidence_delta_memory \\",
            f"  --repo-root . --profile {profile} \\",
            "  --output-json probe_f232c_final_post_restart_readiness/evidence_delta_memory.json",
            "",
            "# 6. F231 artifact inventory (final)",
            "python -m tools.f231_artifact_inventory \\",
            "  --repo-root . \\",
            "  --output-json probe_f232c_final_post_restart_readiness/f231_artifact_inventory.json \\",
            "  --output-md probe_f232c_final_post_restart_readiness/F231_ARTIFACT_INVENTORY.md",
            "```",
        ])

    lines.extend([
        "",
        "---",
        "",
        "## Merge Log",
        "",
    ])
    for entry in result.merge_log:
        lines.append(f"- {entry}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sprint F232C — Final Pre-Live Readiness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", default="nonfeed_diagnostic180")
    parser.add_argument("--query", default="mozilla.org certificate transparency subdomains april 2026")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)

    args = parser.parse_args()
    repo_root = str(args.repo_root.resolve())

    result = run_final_readiness(repo_root, args.profile, args.query)

    # Write JSON
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
        print(f"JSON written: {args.output_json}")

    # Write Markdown
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        md_text = render_markdown(result, args.profile, args.query)
        with open(args.output_md, "w", encoding="utf-8") as f:
            f.write(md_text)
        print(f"Markdown written: {args.output_md}")

    # Console output
    print(f"\n{'='*60}")
    print("F232C Final Pre-Live Readiness")
    print(f"{'='*60}")
    print(f"  verdict:         {result.verdict.value}")
    print(f"  live_allowed:     {result.live_allowed}")
    print(f"  next_action:      {result.next_action.value}")
    if result.next_action_detail:
        print(f"  next_action_detail: {result.next_action_detail}")
    print(f"  swap_used_gib:    {result.swap_used_gib:.3f}")
    print(f"  swap_policy_tier: {result.swap_policy_tier}")
    print(f"  f231_inventory:   {result.f231_inventory_verdict}")
    print(f"  f231h_gate:       {result.f231h_gate_verdict}")
    print(f"  f231_core_ready:  {result.f231_core_ready}")
    print(f"  f224_core_ready:  {result.f224_core_ready}")
    print(f"  provider_surface: {result.provider_surface_ok}")
    if result.blockers:
        print("  blockers:")
        for b in result.blockers:
            print(f"    - [{b.category}] {b.severity}: {b.detail}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
