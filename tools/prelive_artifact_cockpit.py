#!/usr/bin/env python3
"""
Pre-Live Artifact Cockpit — Sprint F220D + F220F (swap gate calibration)

Merges:
  - prelive decision gate verdict + UMA
  - artifact pack status
  - clean live readiness (optional)
  - provider surface status

Produces a single verdict: READY_TO_RUN_NOW | READY_TO_RESTART_AND_RUN |
  BLOCKED_BY_ARTIFACTS | BLOCKED_BY_MEMORY | BLOCKED_BY_PROVIDER_SURFACE |
  BLOCKED_BY_UNKNOWN

Plus exact next action for the operator.

No live execution. No network. No MLX load. No scheduler.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Sprint ID Collision Detection — F224D
# --------------------------------------------------------------------------- #

# Pattern: F + 3+ digits + optional letter suffix
_SPRINT_ID_RE = re.compile(r"^F(\d{3,})[A-Z]?(?:_[A-Z_]+)?$")


@dataclass
class SprintIdCollision:
    sprint_id: str
    aliases: list[str] = field(default_factory=list)
    probe_dirs: list[str] = field(default_factory=list)
    report_paths: list[str] = field(default_factory=list)
    json_paths: list[str] = field(default_factory=list)


@dataclass
class SprintCollisionReport:
    has_collisions: bool = False
    collisions: list[SprintIdCollision] = field(default_factory=list)
    total_probes_scanned: int = 0
    warnings: list[str] = field(default_factory=list)


def _canonical_base(sprint_id: str) -> tuple[str, str]:
    """Return (base, qualifier) for disambiguation.

    base=F223D, qualifier='' for plain F223D
    base=F223D, qualifier='_PRODUCT_VALUE' for F223D_PRODUCT_VALUE
    """
    m = _SPRINT_ID_RE.match(sprint_id)
    if not m:
        return sprint_id, ""
    digits = m.group(1)
    suffix = sprint_id[len(f"F{digits}"):]
    base = f"F{digits}"
    qualifier = suffix if suffix else ""
    return base, qualifier


def scan_probe_artifacts(repo_root: Path) -> SprintCollisionReport:
    """Scan probe_f* directories for sprint ID collisions.

    Detects:
    - Multiple probe dirs with the same sprint ID (e.g. F223D product_value + F223D prewindup)
    - Ambiguous aliases that could confuse operator reports

    Returns SprintCollisionReport with collision list and warnings.
    """
    universal_root = repo_root / "hledac" / "universal"
    probe_root = universal_root if universal_root.exists() else repo_root

    by_base: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    probe_dirs_found = []

    try:
        for item in sorted(probe_root.iterdir()):
            if not item.is_dir():
                continue
            name = item.name
            if not name.startswith("probe_f"):
                continue
            probe_dirs_found.append(name)

            report_path = None
            json_path = None
            sprint_id = name

            json_files = list(item.glob("*.json"))

            if json_files:
                json_path = json_files[0]
                try:
                    with open(json_path, encoding="utf-8") as f:
                        data = json.load(f)
                        sprint_id = data.get("sprint_id", name)
                except Exception:
                    pass

            md_files = list(item.glob("REPORT_*.md"))
            if md_files:
                report_path = md_files[0]

            base, qualifier = _canonical_base(sprint_id)
            by_base[base][qualifier if qualifier else ""].append({
                "probe_dir": name,
                "sprint_id": sprint_id,
                "report_path": str(report_path) if report_path else "",
                "json_path": str(json_path) if json_path else "",
            })

    except Exception as exc:
        return SprintCollisionReport(warnings=[f"scan_probe_artifacts failed: {exc}"])

    collisions = []
    for base, qualifiers in sorted(by_base.items()):
        entries_by_qual = {q: v for q, v in qualifiers.items() if q}
        if len(entries_by_qual) > 1:
            all_entries = []
            all_aliases = []
            for q, entries in entries_by_qual.items():
                for e in entries:
                    all_aliases.append(f"{base}{q}")
                    all_entries.append(e)

            collisions.append(SprintIdCollision(
                sprint_id=base,
                aliases=list(dict.fromkeys(all_aliases)),
                probe_dirs=[e["probe_dir"] for e in all_entries],
                report_paths=[e["report_path"] for e in all_entries],
                json_paths=[e["json_path"] for e in all_entries],
            ))

    return SprintCollisionReport(
        has_collisions=len(collisions) > 0,
        collisions=collisions,
        total_probes_scanned=len(probe_dirs_found),
    )


def render_collision_warning(report: SprintCollisionReport) -> list[str]:
    """Render collision warnings as markdown lines."""
    if not report.has_collisions:
        return []

    lines = ["", "## Sprint ID Collision Warning", ""]
    lines.append(f"⚠️ **Detected {len(report.collisions)} sprint ID collision(s)** across {report.total_probes_scanned} probes scanned.")
    lines.append("")

    for coll in report.collisions:
        lines.append(f"### Collision: `{coll.sprint_id}`")
        lines.append(f"**Aliases:** {', '.join(f'`{a}`' for a in coll.aliases)}")
        lines.append("")
        lines.append("| Probe Directory | Report | JSON |")
        lines.append("|----------------|--------|-----|")
        for probe_dir, report_p, json_p in zip(coll.probe_dirs, coll.report_paths, coll.json_paths):
            lines.append(f"| `{probe_dir}` | {report_p or 'N/A'} | {json_p or 'N/A'} |")
        lines.append("")
        lines.append(f"**Action:** Operator reports may show ambiguous labels. Use full alias (e.g. `{coll.aliases[0]}`) to disambiguate. **Live is NOT blocked** — required artifact paths are explicit.")

    return lines


@dataclass
class SprintIdCollisionWarning:
    has_collisions: bool = False
    collision_count: int = 0
    total_probes_scanned: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "has_collisions": self.has_collisions,
            "collision_count": self.collision_count,
            "total_probes_scanned": self.total_probes_scanned,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Verdict & Action enums
# --------------------------------------------------------------------------- #

class Verdict(str, Enum):
    READY_TO_RUN_NOW = "READY_TO_RUN_NOW"
    READY_DIAGNOSTIC_ONLY = "READY_DIAGNOSTIC_ONLY"
    READY_TO_RESTART_AND_RUN = "READY_TO_RESTART_AND_RUN"
    # F232H: Feed baseline only — nonfeed capability blocked, feed can proceed
    READY_FOR_FEED_BASELINE_ONLY = "READY_FOR_FEED_BASELINE_ONLY"
    BLOCKED_BY_ARTIFACTS = "BLOCKED_BY_ARTIFACTS"
    BLOCKED_BY_MEMORY = "BLOCKED_BY_MEMORY"
    BLOCKED_BY_PROVIDER_SURFACE = "BLOCKED_BY_PROVIDER_SURFACE"
    BLOCKED_BY_UNKNOWN = "BLOCKED_BY_UNKNOWN"


class NextAction(str, Enum):
    RUN_LIVE_NOW = "run_live_now"
    RUN_WITH_HARDWARE_TAINT = "run_with_hardware_taint"
    RESTART_THEN_RUN_LIVE = "restart_then_run_live"
    RUN_NONFEED_DIAGNOSTIC = "run_nonfeed_diagnostic"
    RUN_MISSING_PROBE = "run_missing_probe"
    FIX_PROVIDER_SURFACE = "fix_provider_surface"
    FIX_CONTRACT_GATE = "fix_contract_gate"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class UmaState:
    system_used_gib: float = 0.0
    swap_used_gib: float = 0.0
    swap_detected: bool = False
    uma_state: str = "unknown"
    io_only: bool = False
    error: Optional[str] = None
    # F220F: swap tiered policy telemetry
    hardware_constrained: bool = False
    swap_policy_tier: str = "unknown"
    swap_gate_reason: str = ""


@dataclass
class CockpitResult:
    verdict: Verdict
    live_allowed: bool
    next_action: NextAction
    next_action_detail: str = ""

    # Component verdicts
    gate_decision: str = ""
    gate_live_allowed: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    gate_warnings: list[str] = field(default_factory=list)

    artifact_count: int = 0
    artifact_ready: int = 0
    artifact_missing: int = 0
    artifact_stale: int = 0

    uma: UmaState = field(default_factory=UmaState)
    provider_surface_ok: bool = True
    missing_required_probes: list[str] = field(default_factory=list)
    fallback_schema_blocked: bool = False

    # F220F: swap tiered policy telemetry
    hardware_constrained: bool = False
    swap_policy_tier: str = "unknown"
    swap_gate_reason: str = ""

    # Raw merge log for traceability
    merge_log: list[str] = field(default_factory=list)

    # F224D: sprint ID collision telemetry
    sprint_collision: Optional[SprintIdCollisionWarning] = None

    # F225E: F224 artifact gate telemetry
    f224_core_ready: bool = False
    f224_warnings: list[str] = field(default_factory=list)
    missing_f224_artifacts: list[str] = field(default_factory=list)

    # F231H: F231 Evidence Lift Pack gate telemetry
    f231_core_ready: bool = False
    f231_warnings: list[str] = field(default_factory=list)
    missing_f231_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "live_allowed": self.live_allowed,
            "next_action": self.next_action.value,
            "next_action_detail": self.next_action_detail,
            "gate": {
                "decision": self.gate_decision,
                "live_allowed": self.gate_live_allowed,
                "reasons": self.gate_reasons,
                "warnings": self.gate_warnings,
            },
            "artifacts": {
                "total": self.artifact_count,
                "ready": self.artifact_ready,
                "missing": self.artifact_missing,
                "stale": self.artifact_stale,
            },
            "uma": {
                "system_used_gib": self.uma.system_used_gib,
                "swap_used_gib": self.uma.swap_used_gib,
                "swap_detected": self.uma.swap_detected,
                "uma_state": self.uma.uma_state,
                "io_only": self.uma.io_only,
                "error": self.uma.error,
                "hardware_constrained": self.uma.hardware_constrained,
                "swap_policy_tier": self.uma.swap_policy_tier,
                "swap_gate_reason": self.uma.swap_gate_reason,
            },
            "provider_surface_ok": self.provider_surface_ok,
            "missing_required_probes": self.missing_required_probes,
            "fallback_schema_blocked": self.fallback_schema_blocked,
            "hardware_constrained": self.hardware_constrained,
            "swap_policy_tier": self.swap_policy_tier,
            "swap_gate_reason": self.swap_gate_reason,
            "merge_log": self.merge_log,
            "sprint_collision": self.sprint_collision.to_dict() if self.sprint_collision else None,
            # F225E: F224 artifact gate
            "f224_core_ready": self.f224_core_ready,
            "f224_warnings": self.f224_warnings,
            "missing_f224_artifacts": self.missing_f224_artifacts,
            # F231H: F231 Evidence Lift Pack gate
            "f231_core_ready": self.f231_core_ready,
            "f231_warnings": self.f231_warnings,
            "missing_f231_artifacts": self.missing_f231_artifacts,
        }


# --------------------------------------------------------------------------- #
# Memory thresholds (M1 8GB UMA safe) — F220F tiered macOS swap policy
# --------------------------------------------------------------------------- #
# macOS uses swap/compression opportunistically even when RAM is not fully
# exhausted. On M1 8GB, tiny swap values (0.05 GiB) are normal and should
# NOT block clean runs. Tiered policy:
#   - CLEAN_SWAP_MAX_GIB (2.0): swap <= 2.0 GiB → READY_TO_RUN_NOW
#   - DIAGNOSTIC_SWAP_MAX_GIB (4.0): 2.0 < swap <= 4.0 GiB → READY_DIAGNOSTIC_ONLY
#   - HARD_BLOCK_SWAP_GIB (4.0): swap > 4.0 GiB → READY_TO_RESTART_AND_RUN
# --------------------------------------------------------------------------- #

CLEAN_SWAP_MAX_GIB: float = 2.0    # below this = clean swap
DIAGNOSTIC_SWAP_MAX_GIB: float = 4.0  # above this = hard block
HARD_BLOCK_SWAP_GIB: float = 4.0

# F223H: Repo-root constants
_EXPECTED_REPO_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac"
_UNIVERSAL_ROOT = f"{_EXPECTED_REPO_ROOT}/hledac/universal"


def _get_cwd_guard_state() -> dict:
    """Hermetic CWD diagnostic — no live run, no network, no MLX."""
    import os as _os
    from pathlib import Path as _P

    _cwd = _os.getcwd()
    _resolved = str(_P(_cwd).resolve())
    _universal = _UNIVERSAL_ROOT
    _is_universal_root = _resolved == _universal or _resolved.startswith(f"{_universal}/")
    _cwd_warning = (
        f"WARNING: CWD={_cwd} is outside expected universal root ({_universal}). "
        f"Artifact scans may glob wrong directory."
    ) if not _is_universal_root else ""

    return {
        "cwd": _cwd,
        "resolved_cwd": _resolved,
        "universal_root": _universal,
        "cwd_is_universal_root": _is_universal_root,
        "cwd_warning": _cwd_warning,
    }


# --------------------------------------------------------------------------- #
# JSON load helpers
# --------------------------------------------------------------------------- #

def load_decision_gate(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_artifact_pack(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_readiness(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# UMA extraction
# --------------------------------------------------------------------------- #

def extract_uma(decision_data: dict) -> UmaState:
    uma_raw = decision_data.get("uma", {})
    if isinstance(uma_raw, str):
        return UmaState(error=uma_raw)
    return UmaState(
        system_used_gib=uma_raw.get("system_used_gib", 0.0),
        swap_used_gib=uma_raw.get("swap_used_gib", 0.0),
        swap_detected=uma_raw.get("swap_detected", False),
        uma_state=uma_raw.get("uma_state", "unknown"),
        io_only=uma_raw.get("io_only", False),
        error=uma_raw.get("error"),
    )


# --------------------------------------------------------------------------- #
# Artifact pack analysis
# --------------------------------------------------------------------------- #

def analyze_artifact_pack(data: dict) -> tuple[int, int, int, int, list[str]]:
    """
    Returns (total, ready, missing, stale, missing_probes).
    """
    required = data.get("required_artifacts", data.get("required", []))
    total = len(required)
    ready = sum(
        1 for a in required
        if a.get("status") == "READY_FOR_PRELIVE_GATE"
    )
    missing = sum(
        1 for a in required
        if a.get("status") == "MISSING_REQUIRED"
    )
    stale = sum(
        1 for a in required
        if a.get("status") == "STALE_OR_CORRUPT"
    )
    missing_probes = [
        a["probe_dir"]
        for a in required
        if a.get("status") in ("MISSING_REQUIRED", "STALE_OR_CORRUPT")
    ]
    return total, ready, missing, stale, missing_probes


# --------------------------------------------------------------------------- #
# Provider surface check (from decision gate checked_reports)
# --------------------------------------------------------------------------- #

def check_provider_surface(decision_data: dict) -> bool:
    checked = decision_data.get("checked_reports", {})

    # Empty → no checks performed yet → trust gate decision, don't block
    if not checked:
        return True

    pub_bootstrap = checked.get("probe_f217c_public_bootstrap", {})
    ct_resilience = checked.get("probe_f217d_ct_provider_resilience", {})
    pub_session_seal = checked.get("probe_f219d_public_session_seal", {})
    ct_cooldown = checked.get("probe_f219e_ct_provider_cooldown", {})

    pub_ok = bool(pub_bootstrap.get("found", False) and pub_bootstrap.get("pass", False))
    seal_ok = bool(pub_session_seal.get("found", False) and pub_session_seal.get("pass", False))
    ct_ok = bool(ct_resilience.get("found", False) and ct_resilience.get("pass", False))
    cooldown_ok = bool(ct_cooldown.get("found", False) and ct_cooldown.get("pass", False))

    pub_satisfied = pub_ok or seal_ok
    ct_satisfied = ct_ok or cooldown_ok

    return pub_satisfied and ct_satisfied


# --------------------------------------------------------------------------- #
# Core merge logic
# --------------------------------------------------------------------------- #

def merge_cockpit(
    decision_data: dict,
    artifact_data: dict,
    readiness_data: Optional[dict],
) -> CockpitResult:
    """
    Merge decision gate + artifact pack + readiness into a single CockpitResult.
    """
    log: list[str] = []

    # ---- 1. Gate decision ----
    gate_decision = decision_data.get("decision", "BLOCKED_BY_UNKNOWN")
    gate_live_allowed = decision_data.get("live_allowed", False)
    gate_reasons = decision_data.get("reasons", [])
    gate_warnings = decision_data.get("warnings", [])
    fallback_schema_blocked = decision_data.get("fallback_schema_blocked", False)

    log.append(f"gate_decision={gate_decision}")

    # ---- 2. UMA ----
    uma = extract_uma(decision_data)
    log.append(f"uma=swap={uma.swap_used_gib:.2f}GiB uma_state={uma.uma_state}")

    # ---- 3. Artifact pack ----
    total, ready, missing, stale, missing_probes = analyze_artifact_pack(artifact_data)
    log.append(f"artifacts=ready:{ready}/{total} missing:{missing} stale:{stale}")

    # ---- 4. Provider surface ----
    provider_surface_ok = check_provider_surface(decision_data)
    log.append(f"provider_surface_ok={provider_surface_ok}")

    # ---- 5. Readiness (optional) ----
    readiness_ok = True
    if readiness_data:
        readiness_ok = readiness_data.get("ready_for_live", True)
        log.append(f"readiness={readiness_ok}")
    else:
        log.append("readiness=not provided (OK)")

    # ---- 6. F224 artifact gate (F225E) ----
    f224_core_ready = decision_data.get("f224_core_ready", False)
    f224_warnings = decision_data.get("f224_warnings", [])
    missing_f224_artifacts = decision_data.get("missing_f224_artifacts", [])
    log.append(f"f224_core_ready={f224_core_ready} missing_f224={len(missing_f224_artifacts)}")

    # ---- 7. F231 Evidence Lift Pack gate (F231H) ----
    f231_core_ready = decision_data.get("f231_core_ready", False)
    f231_warnings = decision_data.get("f231_warnings", [])
    missing_f231_artifacts = decision_data.get("missing_f231_artifacts", [])
    log.append(f"f231_core_ready={f231_core_ready} missing_f231={len(missing_f231_artifacts)}")

    # ------------------------------------------------------------------------- #
    # Verdicts
    # ------------------------------------------------------------------------- #

    # A: Gate blocked by unknown / fallback schema
    # F220F: Initialize swap tier telemetry defaults
    hardware_constrained = False
    swap_policy_tier = "unknown"
    swap_gate_reason = ""

    if fallback_schema_blocked or gate_decision == "BLOCKED_BY_UNKNOWN":
        verdict = Verdict.BLOCKED_BY_UNKNOWN
        next_action = NextAction.FIX_CONTRACT_GATE
        next_action_detail = "fallback schema detected in prelive reports"
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "fallback schema or unknown"
        log.append("verdict=BLOCKED_BY_UNKNOWN (fallback schema)")

    # B: Gate blocked by provider surface
    elif gate_decision == "BLOCKED_BY_PROVIDER_SURFACE" or not provider_surface_ok:
        verdict = Verdict.BLOCKED_BY_PROVIDER_SURFACE
        next_action = NextAction.FIX_PROVIDER_SURFACE
        next_action_detail = ""
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "provider surface issue"
        log.append("verdict=BLOCKED_BY_PROVIDER_SURFACE")

    # C: Gate blocked by memory
    elif gate_decision == "BLOCKED_BY_MEMORY":
        verdict = Verdict.BLOCKED_BY_MEMORY
        next_action = NextAction.RESTART_THEN_RUN_LIVE
        next_action_detail = "memory pressure requires restart before live"
        live_allowed = False
        hardware_constrained = True
        swap_policy_tier = "hard_block"
        swap_gate_reason = f"blocked by memory gate: swap={uma.swap_used_gib:.2f}GiB"
        log.append("verdict=BLOCKED_BY_MEMORY")

    # D: Gate blocked by contract (non-provider-surface)
    # F225E: also blocks if F224 blocking artifacts missing for blocking profiles
    # F231H: also blocks if F231 Evidence Lift Pack missing for blocking profiles
    elif gate_decision == "BLOCKED_BY_CONTRACT" or (missing_f224_artifacts and not f224_core_ready) or (missing_f231_artifacts and not f231_core_ready):
        verdict = Verdict.BLOCKED_BY_ARTIFACTS
        next_action = NextAction.RUN_MISSING_PROBE
        # Combine missing F224 and F231 artifacts
        all_missing = list(set(missing_f224_artifacts + missing_f231_artifacts))
        next_action_detail = ",".join(all_missing) if all_missing else ""
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "contract gate failure"
        log.append("verdict=BLOCKED_BY_CONTRACT → BLOCKED_BY_ARTIFACTS")

    # E: Gate READY but missing/stale artifacts
    elif missing > 0 or stale > 0:
        verdict = Verdict.BLOCKED_BY_ARTIFACTS
        next_action = NextAction.RUN_MISSING_PROBE
        next_action_detail = ",".join(missing_probes) if missing_probes else ""
        live_allowed = False
        swap_policy_tier = "blocked"
        swap_gate_reason = "missing/stale artifacts"
        log.append("verdict=BLOCKED_BY_ARTIFACTS")

    # F221G: Feed-only detection + swap-tier READY gate
    # Priority: if nonfeed_diagnostic signal present → override swap verdict with nonfeed diagnostic.
    # Otherwise: fall through to normal swap-tier verdicts (clean / diagnostic / hard_block).
    #
    # F232H CRITICAL: provider_surface_ok=False AND nonfeed_signal → DO NOT allow RUN_NOW.
    # The nonfeed signal path bypassed provider_surface_ok. We must re-check it here.
    # F232H CRITICAL: fallback_schema_blocked AND nonfeed_signal → DO NOT allow RUN_NOW.
    elif gate_live_allowed and ready == total:
        suggested_cmd = decision_data.get("suggested_live_command", "")
        suggested_highswap_cmd = decision_data.get("suggested_highswap_diagnostic_command", "")
        nonfeed_signal = (
            "nonfeed_diagnostic" in suggested_cmd.lower()
            or "nonfeed_diagnostic" in suggested_highswap_cmd.lower()
            or any("nonfeed_diagnostic" in r.lower() for r in gate_reasons)
        )

        if nonfeed_signal:
            # F232H: Even with nonfeed signal, provider_surface_ok=False blocks capability run
            if not provider_surface_ok:
                verdict = Verdict.BLOCKED_BY_PROVIDER_SURFACE
                next_action = NextAction.FIX_PROVIDER_SURFACE
                next_action_detail = "provider_surface_degraded: nonfeed capability blocked"
                live_allowed = False
                swap_policy_tier = "blocked"
                swap_gate_reason = "provider_surface_ok=False blocks nonfeed capability"
                log.append("verdict=BLOCKED_BY_PROVIDER_SURFACE (F232H: nonfeed_signal + provider_surface_degraded)")
            # F232H: Fallback schema blocks nonfeed capability run
            elif fallback_schema_blocked:
                verdict = Verdict.BLOCKED_BY_UNKNOWN
                next_action = NextAction.FIX_CONTRACT_GATE
                next_action_detail = "fallback schema detected: nonfeed capability blocked"
                live_allowed = False
                swap_policy_tier = "blocked"
                swap_gate_reason = "fallback_schema_blocked blocks nonfeed capability"
                log.append("verdict=BLOCKED_BY_UNKNOWN (F232H: nonfeed_signal + fallback_schema_blocked)")
            # F220-like feed-only: route to nonfeed_diagnostic180 regardless of swap tier
            elif uma.swap_used_gib <= CLEAN_SWAP_MAX_GIB:
                verdict = Verdict.READY_TO_RUN_NOW
                next_action = NextAction.RUN_NONFEED_DIAGNOSTIC
                next_action_detail = suggested_cmd or "nonfeed_diagnostic180 — F220-like feed-only detected"
                live_allowed = True
                uma.hardware_constrained = uma.swap_used_gib > CLEAN_SWAP_MAX_GIB
                uma.swap_policy_tier = "clean"
                uma.swap_gate_reason = "nonfeed_diagnostic: swap={:.2f}GiB".format(uma.swap_used_gib)
                hardware_constrained = uma.swap_used_gib > CLEAN_SWAP_MAX_GIB
                swap_policy_tier = "clean"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=RUN_NONFEED_DIAGNOSTIC (feed-only nonfeed_diagnostic path)")
            elif uma.swap_used_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
                verdict = Verdict.READY_DIAGNOSTIC_ONLY
                next_action = NextAction.RUN_NONFEED_DIAGNOSTIC
                next_action_detail = suggested_highswap_cmd or "nonfeed_diagnostic180 — F220-like feed-only, hardware taint"
                live_allowed = True
                uma.hardware_constrained = True
                uma.swap_policy_tier = "diagnostic"
                uma.swap_gate_reason = "nonfeed_diagnostic: swap={:.2f}GiB (diagnostic tier)".format(uma.swap_used_gib)
                hardware_constrained = True
                swap_policy_tier = "diagnostic"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=RUN_NONFEED_DIAGNOSTIC (feed-only, swap={:.2f}GiB, diagnostic tier)".format(uma.swap_used_gib))
            else:
                verdict = Verdict.READY_TO_RESTART_AND_RUN
                next_action = NextAction.RUN_NONFEED_DIAGNOSTIC
                next_action_detail = "swap={:.2f}GiB > {:.1f}GiB — restart then nonfeed_diagnostic180".format(uma.swap_used_gib, DIAGNOSTIC_SWAP_MAX_GIB)
                live_allowed = False
                uma.hardware_constrained = True
                uma.swap_policy_tier = "hard_block"
                uma.swap_gate_reason = "nonfeed_diagnostic: swap={:.2f}GiB > {:.1f}GiB".format(uma.swap_used_gib, HARD_BLOCK_SWAP_GIB)
                hardware_constrained = True
                swap_policy_tier = "hard_block"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=RUN_NONFEED_DIAGNOSTIC (feed-only, swap={:.2f}GiB, hard_block)".format(uma.swap_used_gib))
            log.append("F221G: nonfeed_diagnostic feed-only path selected")

        else:
            # No nonfeed signal — normal swap-tier verdicts (F220F policy)
            if uma.swap_used_gib <= CLEAN_SWAP_MAX_GIB:
                verdict = Verdict.READY_TO_RUN_NOW
                next_action = NextAction.RUN_LIVE_NOW
                next_action_detail = ""
                live_allowed = True
                uma.hardware_constrained = False
                uma.swap_policy_tier = "clean"
                uma.swap_gate_reason = "swap={:.2f}GiB <= {:.1f}GiB threshold".format(uma.swap_used_gib, CLEAN_SWAP_MAX_GIB)
                hardware_constrained = False
                swap_policy_tier = "clean"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=READY_TO_RUN_NOW")
            elif uma.swap_used_gib <= DIAGNOSTIC_SWAP_MAX_GIB:
                verdict = Verdict.READY_DIAGNOSTIC_ONLY
                next_action = NextAction.RUN_WITH_HARDWARE_TAINT
                next_action_detail = "swap={:.2f}GiB in ({:.1f}GiB, {:.1f}GiB] — hardware taint".format(uma.swap_used_gib, CLEAN_SWAP_MAX_GIB, DIAGNOSTIC_SWAP_MAX_GIB)
                live_allowed = True
                uma.hardware_constrained = True
                uma.swap_policy_tier = "diagnostic"
                uma.swap_gate_reason = "swap={:.2f}GiB in ({:.1f}GiB, {:.1f}GiB]".format(uma.swap_used_gib, CLEAN_SWAP_MAX_GIB, DIAGNOSTIC_SWAP_MAX_GIB)
                hardware_constrained = True
                swap_policy_tier = "diagnostic"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=READY_DIAGNOSTIC_ONLY (swap={:.2f})".format(uma.swap_used_gib))
            else:
                verdict = Verdict.READY_TO_RESTART_AND_RUN
                next_action = NextAction.RESTART_THEN_RUN_LIVE
                next_action_detail = "swap={:.2f}GiB > {:.1f}GiB — restart required".format(uma.swap_used_gib, DIAGNOSTIC_SWAP_MAX_GIB)
                live_allowed = False
                uma.hardware_constrained = True
                uma.swap_policy_tier = "hard_block"
                uma.swap_gate_reason = "swap={:.2f}GiB > {:.1f}GiB".format(uma.swap_used_gib, HARD_BLOCK_SWAP_GIB)
                hardware_constrained = True
                swap_policy_tier = "hard_block"
                swap_gate_reason = uma.swap_gate_reason
                log.append("verdict=READY_TO_RESTART_AND_RUN (swap={:.2f})".format(uma.swap_used_gib))

    # H: Catch-all unknown
    else:
        verdict = Verdict.BLOCKED_BY_UNKNOWN
        next_action = NextAction.FIX_CONTRACT_GATE
        next_action_detail = f"unhandled combination: gate={gate_decision} artifacts=ready:{ready}/{total} uma_state={uma.uma_state}"
        live_allowed = False
        log.append(f"verdict=BLOCKED_BY_UNKNOWN fallback (gate={gate_decision})")

    return CockpitResult(
        verdict=verdict,
        live_allowed=live_allowed,
        next_action=next_action,
        next_action_detail=next_action_detail,
        gate_decision=gate_decision,
        gate_live_allowed=gate_live_allowed,
        gate_reasons=gate_reasons,
        gate_warnings=gate_warnings,
        artifact_count=total,
        artifact_ready=ready,
        artifact_missing=missing,
        artifact_stale=stale,
        uma=uma,
        provider_surface_ok=provider_surface_ok,
        missing_required_probes=missing_probes,
        fallback_schema_blocked=fallback_schema_blocked,
        hardware_constrained=hardware_constrained,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
        merge_log=log,
        # F225E: F224 artifact gate
        f224_core_ready=f224_core_ready,
        f224_warnings=f224_warnings,
        missing_f224_artifacts=missing_f224_artifacts,
        # F231H: F231 Evidence Lift Pack gate
        f231_core_ready=f231_core_ready,
        f231_warnings=f231_warnings,
        missing_f231_artifacts=missing_f231_artifacts,
    )


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #

def render_markdown(result: CockpitResult, profile: str, query: str) -> str:
    """Render cockpit result as markdown report."""
    icon = "✅" if result.live_allowed else "❌"
    action_icon = "🚀" if result.next_action in (NextAction.RUN_LIVE_NOW, NextAction.RUN_WITH_HARDWARE_TAINT, NextAction.RESTART_THEN_RUN_LIVE, NextAction.RUN_NONFEED_DIAGNOSTIC) else "🔧"

    lines = [
        "# Pre-Live Artifact Cockpit Report",
        "",
        f"**Verdict:** {icon} `{result.verdict.value}`",
        f"**Live Allowed:** {result.live_allowed}",
        f"**Next Action:** {action_icon} `{result.next_action.value}`",
    ]

    if result.next_action_detail:
        lines.append(f"**Next Action Detail:** {result.next_action_detail}")

    lines.extend([
        "",
        "## Decision Gate",
        "",
        f"- **Gate Decision:** `{result.gate_decision}`",
        f"- **Gate Live Allowed:** {result.gate_live_allowed}",
    ])

    if result.gate_reasons:
        lines.append("")
        lines.append("**Reasons:**")
        for r in result.gate_reasons:
            lines.append(f"  - {r}")

    if result.gate_warnings:
        lines.append("")
        lines.append("**Warnings:**")
        for w in result.gate_warnings:
            lines.append(f"  - {w}")

    lines.extend([
        "",
        "## Artifact Pack",
        "",
        f"| Status | Count |",
        "|--------|-------|",
        f"| Total  | {result.artifact_count} |",
        f"| Ready  | {result.artifact_ready} |",
        f"| Missing | {result.artifact_missing} |",
        f"| Stale   | {result.artifact_stale} |",
    ])

    if result.missing_required_probes:
        lines.extend(["", "**Missing Required Probes:**"])
        for p in result.missing_required_probes:
            lines.append(f"  - `{p}`")

    lines.extend([
        "",
        "## Memory (UMA)",
        "",
        f"- **System Used:** {result.uma.system_used_gib:.2f} GiB",
        f"- **Swap Used:** {result.uma.swap_used_gib:.2f} GiB",
        f"- **Swap Detected:** {result.uma.swap_detected}",
        f"- **UMA State:** `{result.uma.uma_state}`",
        f"- **IO Only:** {result.uma.io_only}",
        f"- **Hardware Constrained:** `{result.uma.hardware_constrained}`",
        f"- **Swap Policy Tier:** `{result.uma.swap_policy_tier}`",
        f"- **Swap Gate Reason:** `{result.uma.swap_gate_reason}`",
    ])

    if result.uma.error:
        lines.append(f"- **UMA Error:** {result.uma.error}")

    lines.extend([
        "",
        "## Provider Surface",
        "",
        f"- **OK:** {result.provider_surface_ok}",
    ])

    # F224D: Sprint ID collision warning
    if result.sprint_collision and result.sprint_collision.has_collisions:
        lines.extend([
            "",
            "## ⚠️ Sprint ID Collision Warning",
            "",
            f"**{result.sprint_collision.collision_count} collision(s)** detected across {result.sprint_collision.total_probes_scanned} probes.",
        ])
        for w in result.sprint_collision.warnings:
            lines.append(f"- {w}")
        lines.extend([
            "",
            "**Impact:** Operator reports may show ambiguous sprint ID labels. "
            "Live run is NOT blocked — required artifacts are unambiguous when paths are explicit.",
            "",
            "**Resolution:** Use full alias (e.g. `F223D_PRODUCT_VALUE`) in queries to disambiguate.",
        ])

    lines.extend([
        "",
        "## Next Actions",
        "",
        f"1. `{result.next_action.value}`",
    ])

    if result.next_action == NextAction.RUN_MISSING_PROBE and result.next_action_detail:
        probes = result.next_action_detail.split(",")
        lines.append("")
        lines.append("Run these probe lanes to restore artifacts:")
        for probe in probes:
            probe_clean = probe.strip()
            lines.append(f"```bash")
            lines.append(f"python -m pytest tests/{probe_clean} -v --tb=short")
            lines.append(f"```")

    elif result.next_action == NextAction.FIX_PROVIDER_SURFACE:
        lines.extend(["", "Run provider surface probe lanes:"])
        lines.append("```bash")
        lines.append("python -m pytest tests/probe_f217c_public_bootstrap -v --tb=short")
        lines.append("python -m pytest tests/probe_f217d_ct_provider_resilience -v --tb=short")
        lines.append("```")

    elif result.next_action == NextAction.FIX_CONTRACT_GATE:
        lines.extend(["", "Investigate contract gate failures in the decision gate report."])

    elif result.next_action == NextAction.RUN_NONFEED_DIAGNOSTIC:
        lines.extend(["", "F220-like feed-only detected. Run nonfeed diagnostic profile:"])
        lines.append("```bash")
        cmd = result.next_action_detail or f"python benchmarks/live_sprint_measurement.py --profile nonfeed_diagnostic180 --query \"mozilla.org certificate transparency subdomains april 2026\" --live"
        lines.append(cmd)
        lines.append("```")

    lines.extend([
        "",
        "---",
        f"*Profile: `{profile}` | Query: `{query}`*",
    ])

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pre-Live Artifact Cockpit — Sprint F220D",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python tools/prelive_artifact_cockpit.py \\
                --decision-json probe_f219f_prelive_decision_gate/prelive_decision.json \\
                --artifact-pack-json probe_f219i_prelive_artifact_pack/artifact_pack.json \\
                --output-json probe_f220d_prelive_artifact_cockpit/prelive_artifact_cockpit.json \\
                --output-md probe_f220d_prelive_artifact_cockpit/REPORT_PRELIVE_ARTIFACT_COCKPIT.md

              # With optional readiness:
              python tools/prelive_artifact_cockpit.py \\
                --decision-json probe_f219f_prelive_decision_gate/prelive_decision.json \\
                --artifact-pack-json probe_f219i_prelive_artifact_pack/artifact_pack.json \\
                --readiness-json probe_f220c_clean_live_readiness/readiness.json \\
                --output-json prelive_artifact_cockpit.json
        """),
    )
    parser.add_argument(
        "--decision-json", "-d",
        type=Path,
        required=True,
        help="Path to prelive_decision.json (from prelive_decision_gate.py)",
    )
    parser.add_argument(
        "--artifact-pack-json", "-a",
        type=Path,
        required=True,
        help="Path to artifact_pack.json (from prelive_artifact_pack.py)",
    )
    parser.add_argument(
        "--readiness-json", "-r",
        type=Path,
        default=None,
        help="Path to clean_live_readiness.json (optional)",
    )
    parser.add_argument(
        "--output-json", "-o",
        type=Path,
        help="Write JSON report to this path.",
    )
    parser.add_argument(
        "--output-md", "-m",
        type=Path,
        help="Write markdown report to this path.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="nonfeed_diagnostic",
        help="Profile name for report header.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default='mozilla.org certificate transparency subdomains april 2026',
        help="Query string for report header.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print merge log and details.",
    )
    # F223H: Optional repo-root override for CWD guard
    parser.add_argument(
        "--repo-root", "-R",
        type=Path,
        default=None,
        help="Override expected universal root for CWD guard. "
             "Defaults to internal detection based on CWD.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # F223H: CWD guard
    cwd_state = _get_cwd_guard_state()
    if cwd_state["cwd_warning"]:
        print(f"CWD GUARD: {cwd_state['cwd_warning']}", file=sys.stderr)
        print("Aborting artifact scan due to wrong CWD.", file=sys.stderr)
        return 1

    # Validate inputs exist
    if not args.decision_json.exists():
        print(f"ERROR: decision JSON not found: {args.decision_json}", file=sys.stderr)
        return 1
    if not args.artifact_pack_json.exists():
        print(f"ERROR: artifact pack JSON not found: {args.artifact_pack_json}", file=sys.stderr)
        return 1

    # Load inputs
    decision_data = load_decision_gate(args.decision_json)
    artifact_data = load_artifact_pack(args.artifact_pack_json)
    readiness_data = load_readiness(args.readiness_json)

    # F224D: Scan for sprint ID collisions
    repo_root = args.repo_root or Path.cwd()
    collision_report = scan_probe_artifacts(repo_root)

    # Merge
    result = merge_cockpit(decision_data, artifact_data, readiness_data)

    # F224D: Attach collision warning (does not block live)
    if collision_report.has_collisions:
        result.sprint_collision = SprintIdCollisionWarning(
            has_collisions=True,
            collision_count=len(collision_report.collisions),
            total_probes_scanned=collision_report.total_probes_scanned,
            warnings=[f"Sprint ID collision detected: {c.sprint_id}" for c in collision_report.collisions],
        )

    # Verbose merge log
    if args.verbose:
        print("Merge log:")
        for entry in result.merge_log:
            print(f"  {entry}")
        print()

    # Console output
    icon = "✅" if result.live_allowed else "❌"
    print(f"{'='*60}")
    print(f"  Verdict:      {icon} {result.verdict.value}")
    print(f"  Live Allowed: {result.live_allowed}")
    print(f"  Next Action:  {result.next_action.value}")
    if result.next_action_detail:
        print(f"  Detail:       {result.next_action_detail}")
    print(f"{'='*60}")

    if result.gate_reasons:
        print("Gate reasons:")
        for r in result.gate_reasons:
            print(f"  - {r}")

    if result.missing_required_probes:
        print(f"Missing artifacts ({len(result.missing_required_probes)}):")
        for p in result.missing_required_probes:
            print(f"  - {p}")

    uma_sw = result.uma.swap_used_gib
    print(f"UMA: swap={uma_sw:.2f}GiB [clean<={CLEAN_SWAP_MAX_GIB:.1f}GiB | diagnostic<={DIAGNOSTIC_SWAP_MAX_GIB:.1f}GiB | hard_block>{HARD_BLOCK_SWAP_GIB:.1f}GiB]")
    print(f"Swap policy tier: {result.swap_policy_tier} | Hardware constrained: {result.hardware_constrained}")

    # Write JSON
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2, default=str)
        print(f"\nJSON report written: {args.output_json}")

    # Write Markdown
    if args.output_md:
        md_text = render_markdown(result, args.profile, args.query)
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_md, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        print(f"Markdown report written: {args.output_md}")

    return 0 if result.live_allowed else 1


if __name__ == "__main__":
    sys.exit(main())
