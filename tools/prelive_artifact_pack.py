#!/usr/bin/env python3
"""
prelive_artifact_pack.py — Deterministic artifact pack validator/regenerator for pre-live gate inputs.

No live network. No model load. No dependency install.
Checks expected report artifacts and reports missing/stale ones.

Usage:
    python tools/prelive_artifact_pack.py --repo-root . --output-json probe_f219i_prelive_artifact_pack/artifact_pack.json --output-md probe_f219i_prelive_artifact_pack/REPORT_PRELIVE_ARTIFACT_PACK.md
    python tools/prelive_artifact_pack.py --repo-root . --run-probes
"""

import argparse
import json
import sys
import subprocess
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Required probe lanes and their artifact filenames
_REQUIRED_PROBES = [
    ("probe_f216h_nonfeed_recovery_guard", "nonfeed_recovery_guard.json"),
    ("probe_f216i_zero_findings_quality", "sanity_zero_findings.json"),
    ("probe_f216i_zero_findings_quality", "zero_findings_quality.json"),
    ("probe_f217c_public_bootstrap", "public_bootstrap.json"),
    ("probe_f217d_ct_provider_resilience", "ct_provider_resilience.json"),
    ("probe_f217e_nonfeed_candidate_ledger", "candidate_ledger.json"),
    ("probe_m218e_memory_integration_guard", "memory_integration_guard.json"),
    ("probe_f219a_surface_contract", "surface_contract.json"),
    ("probe_f219b_hermes_metal_finalizer", "hermes_metal_finalizer.json"),
    ("probe_f219d_public_session_seal", "public_session_seal.json"),
    ("probe_f219e_ct_provider_cooldown", "ct_cooldown.json"),
]

# Optional probe lanes
_OPTIONAL_PROBES = [
    ("probe_f219f_prelive_decision_gate", "prelive_decision_gate.json"),
]


class ArtifactStatus(str, Enum):
    READY_FOR_PRELIVE_GATE = "READY_FOR_PRELIVE_GATE"
    MISSING_REQUIRED = "MISSING_REQUIRED"
    STALE_OR_CORRUPT = "STALE_OR_CORRUPT"
    OPTIONAL_MISSING = "OPTIONAL_MISSING"


@dataclass
class ProbeArtifact:
    probe_dir: str
    filename: str
    full_path: str
    found: bool
    parse_error: Optional[str] = None
    data: dict = field(default_factory=dict)
    status: ArtifactStatus = ArtifactStatus.MISSING_REQUIRED


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #

def check_artifact(
    repo_root: Path, probe_dir: str, filename: str, *, required: bool = True
) -> ProbeArtifact:
    """Check a single artifact file exists and is valid JSON."""
    full_path = str(repo_root / probe_dir / filename)
    artifact = ProbeArtifact(
        probe_dir=probe_dir,
        filename=filename,
        full_path=full_path,
        found=False,
    )

    p = Path(full_path)
    if not p.exists():
        artifact.status = ArtifactStatus.MISSING_REQUIRED if required else ArtifactStatus.OPTIONAL_MISSING
        return artifact

    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        artifact.found = True
        artifact.data = data
        artifact.status = ArtifactStatus.READY_FOR_PRELIVE_GATE
    except json.JSONDecodeError as exc:
        artifact.parse_error = str(exc)
        artifact.status = ArtifactStatus.STALE_OR_CORRUPT
    except Exception as exc:
        artifact.parse_error = str(exc)
        artifact.status = ArtifactStatus.STALE_OR_CORRUPT

    return artifact


def check_all_artifacts(repo_root: Path) -> tuple[list[ProbeArtifact], list[ProbeArtifact]]:
    """Check all required and optional artifacts. Returns (required_artifacts, optional_artifacts)."""
    required = []
    optional = []

    for probe_dir, filename in _REQUIRED_PROBES:
        artifact = check_artifact(repo_root, probe_dir, filename, required=True)
        required.append(artifact)

    for probe_dir, filename in _OPTIONAL_PROBES:
        artifact = check_artifact(repo_root, probe_dir, filename, required=False)
        optional.append(artifact)

    return required, optional


def build_regeneration_commands(
    required: list[ProbeArtifact],
) -> list[str]:
    """Build pytest commands to regenerate missing artifacts."""
    commands = []

    # Deduplicate probe dirs that need regeneration
    missing_required_dirs = {
        (a.probe_dir, a.filename)
        for a in required
        if a.status != ArtifactStatus.READY_FOR_PRELIVE_GATE
    }

    for probe_dir, _ in sorted(missing_required_dirs):
        # Each probe dir has a test file named test_<probe>.py
        test_file = f"{probe_dir}/test_{probe_dir.split('_', 1)[1]}.py"
        cmd = f"python -m pytest -v {test_file} --tb=short"
        commands.append(cmd)

    return commands


def overall_status(required: list[ProbeArtifact]) -> ArtifactStatus:
    """Derive overall artifact pack status."""
    # Any MISSING_REQUIRED or STALE_OR_CORRUPT among required → overall is MISSING_REQUIRED
    for a in required:
        if a.status in (ArtifactStatus.MISSING_REQUIRED, ArtifactStatus.STALE_OR_CORRUPT):
            return ArtifactStatus.MISSING_REQUIRED
    return ArtifactStatus.READY_FOR_PRELIVE_GATE


def render_markdown(
    required: list[ProbeArtifact],
    commands: list[str],
    overall: ArtifactStatus,
) -> str:
    """Render the markdown report."""
    lines = [
        "# Prelive Artifact Pack Report",
        "",
        f"**Status:** `{overall.value}`",
        "",
        "## Required Artifacts",
        "",
        "| Probe | Artifact | Status |",
        "|-------|----------|--------|",
    ]

    for a in required:
        if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE:
            icon = "✅"
        elif a.status == ArtifactStatus.MISSING_REQUIRED:
            icon = "❌"
        else:
            icon = "⚠️"
        path_display = f"`{a.probe_dir}/{a.filename}`"
        error_note = f" *(parse error: {a.parse_error})*" if a.parse_error else ""
        lines.append(f"| {a.probe_dir} | {path_display} | {icon} {a.status.value}{error_note} |")

    if commands:
        lines.extend(["", "## Regeneration Commands", ""])
        lines.append("Run these commands to regenerate missing artifacts:")
        lines.append("")
        for cmd in commands:
            lines.append(f"```bash")
            lines.append(cmd)
            lines.append(f"```")
            lines.append("")
    else:
        lines.extend(["", "## Regeneration Commands", "", "All required artifacts present. No regeneration needed.", ""])

    lines.extend([
        "",
        "## Pytest Commands Reference",
        "",
        "```bash",
        "# Check all artifacts:",
        f"python -m pytest tests/probe_f219i_prelive_artifact_pack -v --tb=short",
        "",
        "# Regenerate specific probe lane:",
        "# python -m pytest tests/probe_f217c_public_bootstrap -v --tb=short",
        "# python -m pytest tests/probe_f217d_ct_provider_resilience -v --tb=short",
        f"```",
        "",
    ])

    return "\n".join(lines)
    """Render the markdown report."""
    lines = [
        "# Prelive Artifact Pack Report",
        "",
        f"**Status:** `{overall.value}`",
        "",
        "## Required Artifacts",
        "",
        "| Probe | Artifact | Status |",
        "|-------|----------|--------|",
    ]

    for a in required:
        if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE:
            icon = "✅"
        elif a.status == ArtifactStatus.MISSING_REQUIRED:
            icon = "❌"
        else:
            icon = "⚠️"
        path_display = f"`{a.probe_dir}/{a.filename}`"
        error_note = f" *(parse error: {a.parse_error})*" if a.parse_error else ""
        lines.append(f"| {a.probe_dir} | {path_display} | {icon} {a.status.value}{error_note} |")

    if optional:
        lines.extend(["", "## Optional Artifacts", ""])
        for a in optional:
            if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE:
                icon = "✅"
            else:
                icon = "⚠️"
            path_display = f"`{a.probe_dir}/{a.filename}`"
            lines.append(f"| {a.probe_dir} | {path_display} | {icon} {a.status.value} |")

    if commands:
        lines.extend(["", "## Regeneration Commands", ""])
        lines.append("Run these commands to regenerate missing artifacts:")
        lines.append("")
        for cmd in commands:
            lines.append(f"```bash")
            lines.append(cmd)
            lines.append(f"```")
            lines.append("")
    else:
        lines.extend(["", "## Regeneration Commands", "", "All required artifacts present. No regeneration needed.", ""])

    lines.extend([
        "",
        "## Pytest Commands Reference",
        "",
        "```bash",
        "# Check all artifacts:",
        f"python -m pytest tests/probe_f219i_prelive_artifact_pack -v --tb=short",
        "",
        "# Regenerate specific probe lane:",
        "# python -m pytest tests/probe_f217c_public_bootstrap -v --tb=short",
        "# python -m pytest tests/probe_f217d_ct_provider_resilience -v --tb=short",
        f"```",
        "",
    ])

    return "\n".join(lines)


def render_json(
    required: list[ProbeArtifact],
    commands: list[str],
    overall: ArtifactStatus,
) -> dict:
    """Render the JSON report."""
    return {
        "status": overall.value,
        "overall": overall.value,
        "required_artifacts": [
            {
                "probe_dir": a.probe_dir,
                "filename": a.filename,
                "path": a.full_path,
                "found": a.found,
                "status": a.status.value,
                "parse_error": a.parse_error,
            }
            for a in required
        ],
        "missing_required": [
            f"{a.probe_dir}/{a.filename}"
            for a in required
            if a.status != ArtifactStatus.READY_FOR_PRELIVE_GATE
        ],
        "regeneration_commands": commands,
        "probe_count": {
            "total_required": len(required),
            "found": sum(1 for a in required if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE),
            "missing_required": sum(1 for a in required if a.status == ArtifactStatus.MISSING_REQUIRED),
            "stale_or_corrupt": sum(1 for a in required if a.status == ArtifactStatus.STALE_OR_CORRUPT),
        },
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and regenerate prelive artifact pack.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Inspect only (default mode - no tests run)
              python tools/prelive_artifact_pack.py --repo-root .

              # Inspect and generate reports
              python tools/prelive_artifact_pack.py --repo-root . \\
                --output-json probe_f219i_prelive_artifact_pack/artifact_pack.json \\
                --output-md probe_f219i_prelive_artifact_pack/REPORT_PRELIVE_ARTIFACT_PACK.md

              # Inspect and print regeneration commands without running them
              python tools/prelive_artifact_pack.py --repo-root . --run-probes

              # Dry-run: inspect and emit commands only (same as default)
              python tools/prelive_artifact_pack.py --repo-root .
        """),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Repository root (default: .)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Write JSON report to this path.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        help="Write markdown report to this path.",
    )
    parser.add_argument(
        "--run-probes",
        action="store_true",
        help=(
            "Execute pytest commands to regenerate missing artifacts. "
            "Without this flag, only inspects and reports (default behavior)."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed status per artifact.",
    )

    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    # Check all artifacts
    required, optional = check_all_artifacts(repo_root)
    overall = overall_status(required)
    commands = build_regeneration_commands(required)

    # Print to stdout
    print(f"Artifact Pack Status: {overall.value}")
    print()

    if args.verbose:
        for a in required:
            status_icon = "✅" if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE else "❌"
            print(f"  {status_icon} {a.probe_dir}/{a.filename}: {a.status.value}")
            if a.parse_error:
                print(f"     Parse error: {a.parse_error}")

    missing_count = sum(1 for a in required if a.status != ArtifactStatus.READY_FOR_PRELIVE_GATE)
    print(f"Required: {len(required) - missing_count}/{len(required)} present ({missing_count} missing)")

    if commands:
        print(f"\nRegeneration commands needed: {len(commands)}")
        if not args.run_probes:
            print("(use --run-probes to execute them)")

    # Execute regeneration if requested
    if args.run_probes and commands:
        print("\n" + "=" * 60)
        print("Running pytest to regenerate artifacts...")
        print("=" * 60)
        for cmd in commands:
            print(f"\n$ {cmd}")
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(repo_root),
                capture_output=False,
            )
            if result.returncode != 0:
                print(f"WARNING: Command exited with {result.returncode}")
        # Re-check after regeneration attempt
        required, optional = check_all_artifacts(repo_root)
        overall = overall_status(required)
        commands = build_regeneration_commands(required)
        print("\n" + "=" * 60)
        print(f"Post-regeneration status: {overall.value}")

    # Write outputs
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(render_json(required, commands, overall), fh, indent=2)
        print(f"\nJSON report written to: {args.output_json}")

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_md, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(required, commands, overall))
        print(f"Markdown report written to: {args.output_md}")

    # Exit code reflects overall status
    return 0 if overall == ArtifactStatus.READY_FOR_PRELIVE_GATE else 1


if __name__ == "__main__":
    sys.exit(main())