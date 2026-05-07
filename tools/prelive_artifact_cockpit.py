#!/usr/bin/env python3
"""
Pre-Live Artifact Cockpit — Sprint F220D

Merges:
  - prelive decision gate verdict + UMA
  - artifact pack status
  - clean live readiness (optional)
  - provider surface status

Produces a single verdict: READY_TO_RUN_NOW | READY_TO_RESTART_AND_RUN |
  BLOCKED_BY_ARTIFACTS | BLOCKED_BY_MEMORY | BLOCKED_BY_PROVIDER_SURFACE |
  BLOCKED_BY_UNKNOWN

Plus exact next action for the operator.

No live execution. No network. No MLX load. No SprintScheduler.
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
# Verdict & Action enums
# --------------------------------------------------------------------------- #

class Verdict(str, Enum):
    READY_TO_RUN_NOW = "READY_TO_RUN_NOW"
    READY_TO_RESTART_AND_RUN = "READY_TO_RESTART_AND_RUN"
    BLOCKED_BY_ARTIFACTS = "BLOCKED_BY_ARTIFACTS"
    BLOCKED_BY_MEMORY = "BLOCKED_BY_MEMORY"
    BLOCKED_BY_PROVIDER_SURFACE = "BLOCKED_BY_PROVIDER_SURFACE"
    BLOCKED_BY_UNKNOWN = "BLOCKED_BY_UNKNOWN"


class NextAction(str, Enum):
    RUN_LIVE_NOW = "run_live_now"
    RESTART_THEN_RUN_LIVE = "restart_then_run_live"
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

    # Raw merge log for traceability
    merge_log: list[str] = field(default_factory=list)

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
            },
            "provider_surface_ok": self.provider_surface_ok,
            "missing_required_probes": self.missing_required_probes,
            "fallback_schema_blocked": self.fallback_schema_blocked,
            "merge_log": self.merge_log,
        }


# --------------------------------------------------------------------------- #
# Memory thresholds (M1 8GB UMA safe)
# --------------------------------------------------------------------------- #

_MEMORY_CLEAN_SWAP_GIB: float = 0.5   # below this = clean swap
_MEMORY_HIGH_SWAP_GIB: float = 2.0    # above this = restart needed
_MEMORY_CRITICAL_SWAP_GIB: float = 4.0  # above this = blocked


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
    """
    Returns True if provider surface is OK (all required probes found + passing).
    Mirrors prelive_decision_gate logic for public_bootstrap + ct_provider_resilience.
    """
    checked = decision_data.get("checked_reports", {})

    # Required provider surface probes
    pub_bootstrap = checked.get("probe_f217c_public_bootstrap", {})
    ct_resilience = checked.get("probe_f217d_ct_provider_resilience", {})

    # Both missing = blocked
    if not pub_bootstrap.get("found") and not ct_resilience.get("found"):
        return False

    # New probes (F219 alias)
    pub_session_seal = checked.get("probe_f219d_public_session_seal", {})
    ct_cooldown = checked.get("probe_f219e_ct_provider_cooldown", {})

    # If old missing but new present and passing → OK
    if not pub_bootstrap.get("found") and pub_session_seal.get("found") and pub_session_seal.get("pass"):
        return True
    if not ct_resilience.get("found") and ct_cooldown.get("found") and ct_cooldown.get("pass"):
        return True

    # Old present and passing → OK
    if pub_bootstrap.get("found") and pub_bootstrap.get("pass"):
        return True
    if ct_resilience.get("found") and ct_resilience.get("pass"):
        return True

    # New present but failing → blocked
    if pub_session_seal.get("found") and not pub_session_seal.get("pass"):
        return False
    if ct_cooldown.get("found") and not ct_cooldown.get("pass"):
        return False

    return True


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

    # ------------------------------------------------------------------------- #
    # Verdicts
    # ------------------------------------------------------------------------- #

    # A: Gate blocked by unknown / fallback schema
    if fallback_schema_blocked or gate_decision == "BLOCKED_BY_UNKNOWN":
        verdict = Verdict.BLOCKED_BY_UNKNOWN
        next_action = NextAction.FIX_CONTRACT_GATE
        next_action_detail = "fallback schema detected in prelive reports"
        live_allowed = False
        log.append("verdict=BLOCKED_BY_UNKNOWN (fallback schema)")

    # B: Gate blocked by provider surface
    elif gate_decision == "BLOCKED_BY_PROVIDER_SURFACE" or not provider_surface_ok:
        verdict = Verdict.BLOCKED_BY_PROVIDER_SURFACE
        next_action = NextAction.FIX_PROVIDER_SURFACE
        next_action_detail = ""
        live_allowed = False
        log.append("verdict=BLOCKED_BY_PROVIDER_SURFACE")

    # C: Gate blocked by memory
    elif gate_decision == "BLOCKED_BY_MEMORY":
        verdict = Verdict.BLOCKED_BY_MEMORY
        next_action = NextAction.RESTART_THEN_RUN_LIVE
        next_action_detail = "memory pressure requires restart before live"
        live_allowed = False
        log.append("verdict=BLOCKED_BY_MEMORY")

    # D: Gate blocked by contract (non-provider-surface)
    elif gate_decision == "BLOCKED_BY_CONTRACT":
        verdict = Verdict.BLOCKED_BY_ARTIFACTS
        next_action = NextAction.FIX_CONTRACT_GATE
        next_action_detail = ""
        live_allowed = False
        log.append("verdict=BLOCKED_BY_CONTRACT → BLOCKED_BY_ARTIFACTS")

    # E: Gate READY but missing/stale artifacts
    elif missing > 0 or stale > 0:
        verdict = Verdict.BLOCKED_BY_ARTIFACTS
        next_action = NextAction.RUN_MISSING_PROBE
        next_action_detail = ",".join(missing_probes) if missing_probes else ""
        live_allowed = False
        log.append("verdict=BLOCKED_BY_ARTIFACTS")

    # F: Gate READY, all artifacts ready, swap clean
    elif gate_live_allowed and ready == total and uma.swap_used_gib < _MEMORY_HIGH_SWAP_GIB:
        verdict = Verdict.READY_TO_RUN_NOW
        next_action = NextAction.RUN_LIVE_NOW
        next_action_detail = ""
        live_allowed = True
        log.append("verdict=READY_TO_RUN_NOW")

    # G: Gate READY, all artifacts ready, but high swap → restart
    elif gate_live_allowed and ready == total and uma.swap_used_gib >= _MEMORY_HIGH_SWAP_GIB:
        verdict = Verdict.READY_TO_RESTART_AND_RUN
        next_action = NextAction.RESTART_THEN_RUN_LIVE
        next_action_detail = f"swap={uma.swap_used_gib:.2f}GiB above threshold"
        live_allowed = False
        log.append(f"verdict=READY_TO_RESTART_AND_RUN (swap={uma.swap_used_gib:.2f})")

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
        merge_log=log,
    )


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #

def render_markdown(result: CockpitResult, profile: str, query: str) -> str:
    """Render cockpit result as markdown report."""
    icon = "✅" if result.live_allowed else "❌"
    action_icon = "🚀" if result.next_action in (NextAction.RUN_LIVE_NOW, NextAction.RESTART_THEN_RUN_LIVE) else "🔧"

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
    ])

    if result.uma.error:
        lines.append(f"- **UMA Error:** {result.uma.error}")

    lines.extend([
        "",
        "## Provider Surface",
        "",
        f"- **OK:** {result.provider_surface_ok}",
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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

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

    # Merge
    result = merge_cockpit(decision_data, artifact_data, readiness_data)

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
    print(f"UMA: swap={uma_sw:.2f}GiB [{_MEMORY_CLEAN_SWAP_GIB}/{_MEMORY_HIGH_SWAP_GIB}/{_MEMORY_CRITICAL_SWAP_GIB}]")

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
