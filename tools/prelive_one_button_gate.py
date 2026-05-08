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
    provider_surface_ok: bool = True
    fallback_schema_blocked: bool = False
    swap_policy_tier: str = "unknown"
    swap_gate_reason: str = ""
    suggested_command: str = ""
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
            "provider_surface_ok": self.provider_surface_ok,
            "fallback_schema_blocked": self.fallback_schema_blocked,
            "swap_policy_tier": self.swap_policy_tier,
            "swap_gate_reason": self.swap_gate_reason,
            "suggested_command": self.suggested_command,
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

    # 8. Build suggested command
    encoded_query = query.replace('"', '\\"')
    suggested_command = (
        f"rtk proxy python benchmarks/live_sprint_measurement.py "
        f"--profile {profile} "
        f'--query "{encoded_query}" '
        f"--live "
        f"--require-memory-ok"
    )

    # 9. Decision tree
    # Rule 1: Missing F221 artifacts → DO_NOT_RUN_FIX_ARTIFACTS
    if missing_f221:
        verdict = OneButtonVerdict.DO_NOT_RUN_FIX_ARTIFACTS
        live_allowed = False
        reasons.append(f"Missing required F221 probe artifacts: {', '.join(missing_f221)}")
        if missing_cross_sprint:
            reasons.append(f"Also missing cross-sprint artifacts: {', '.join(missing_cross_sprint)}")

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
        provider_surface_ok=provider_surface_ok,
        fallback_schema_blocked=fallback_blocked,
        swap_policy_tier=swap_policy_tier,
        swap_gate_reason=swap_gate_reason,
        suggested_command=suggested_command,
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

    lines.extend(["", "---", "", "## Provider Surface", ""])
    ps_icon = "✅" if result.provider_surface_ok else "❌"
    lines.append(f"- **OK:** {ps_icon} `{result.provider_surface_ok}`")
    lines.append(f"- **Fallback Schema Blocked:** `{result.fallback_schema_blocked}`")

    if result.triage_verdict:
        lines.extend(["", "---", "", "## Last-Live Triage", ""])
        lines.append(f"- **Triage Verdict:** `{result.triage_verdict}`")
        lines.append(f"- **Another Live Useful:** `{result.triage_another_live_useful}`")

    lines.extend(["", "---", "", "## Suggested Command", ""])
    lines.append(f"```bash\n{result.suggested_command}\n```")

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
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"ERROR: repo root does not exist: {repo_root}", file=sys.stderr)
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
    uma_sw = result.uma.get("swap_used_gib", 0)
    print(f"UMA: swap={uma_sw:.3f}GiB")
    print()
    print(f"Suggested command:")
    print(f"  {result.suggested_command}")

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
