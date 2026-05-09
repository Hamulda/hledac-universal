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
import re
import subprocess
import sys
import textwrap
from collections import defaultdict
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
    ("probe_f219e_ct_provider_cooldown", "ct_provider_cooldown.json"),
]

# Optional probe lanes
_OPTIONAL_PROBES = [
    ("probe_f219f_prelive_decision_gate", "prelive_decision_gate.json"),
]

# F224 artifact lanes — read-only readiness signals
# Blocking: F224A (worker_pool import seal), F224C (discovery provider gap), F224D (confidence policy)
# Warning: F224B (claims extraction), F224E (type checking hygiene)
_F224_PROBES = [
    ("probe_f224a_worker_pool_import_seal", "worker_pool_import_seal.json"),
    ("probe_f224b_claims_extraction_v1", "claims_extraction_v1.json"),
    ("probe_f224c_discovery_provider_gap", "discovery_provider_gap.json"),
    ("probe_f224d_confidence_policy", "confidence_policy.json"),
    ("probe_f224e_type_checking_hygiene", "type_checking_hygiene.json"),
]


# --------------------------------------------------------------------------- #
# Sprint ID Collision Detection — F224D (shared with cockpit)
# --------------------------------------------------------------------------- #

_SPRINT_ID_RE = re.compile(r"^F(\d{3,})[A-Z]?(?:_[A-Z_]+)?$")


def _canonical_base(sprint_id: str) -> tuple[str, str]:
    m = _SPRINT_ID_RE.match(sprint_id)
    if not m:
        return sprint_id, ""
    digits = m.group(1)
    suffix = sprint_id[len(f"F{digits}"):]
    return f"F{digits}", suffix if suffix else ""


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


def scan_probe_artifacts(repo_root: Path) -> SprintCollisionReport:
    """Scan probe_f* directories for sprint ID collisions."""
    universal_root = repo_root / "hledac" / "universal"
    probe_root = universal_root if universal_root.exists() else repo_root

    by_base: dict = defaultdict(lambda: defaultdict(list))
    probe_dirs_found = []

    try:
        for item in sorted(probe_root.iterdir()):
            if not item.is_dir() or not item.name.startswith("probe_f"):
                continue
            name = item.name
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

    lines = ["", "## ⚠️ Sprint ID Collision Warning", ""]
    lines.append(f"**{len(report.collisions)} collision(s)** detected across {report.total_probes_scanned} probes scanned.")
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
        lines.append(f"**Action:** Use full alias (e.g. `{coll.aliases[0]}`) to disambiguate. "
                    "Live is NOT blocked — required artifacts are unambiguous when paths are explicit.")
        lines.append("")

    return lines


@dataclass
class ArtifactPackResult:
    required: list
    optional: list
    commands: list
    overall: ArtifactStatus
    collision_report: Optional[SprintCollisionReport] = None


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


def check_all_artifacts_with_f224(repo_root: Path) -> tuple[list[ProbeArtifact], list[ProbeArtifact], list[ProbeArtifact]]:
    """Check all required, optional, and F224 artifact probes. Returns (required, optional, f224)."""
    required, optional = check_all_artifacts(repo_root)
    f224 = []
    for probe_dir, filename in _F224_PROBES:
        artifact = check_artifact(repo_root, probe_dir, filename, required=False)
        f224.append(artifact)
    return required, optional, f224


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


def render_json(
    required: list[ProbeArtifact],
    commands: list[str],
    overall: ArtifactStatus,
    f224: Optional[list[ProbeArtifact]] = None,
) -> dict:
    """Render the JSON report."""
    f224_section = {}
    if f224 is not None:
        f224_section = {
            "f224_artifacts": [
                {
                    "probe_dir": a.probe_dir,
                    "filename": a.filename,
                    "path": a.full_path,
                    "found": a.found,
                    "status": a.status.value,
                    "parse_error": a.parse_error,
                }
                for a in f224
            ],
            "f224_all_present": all(a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE for a in f224),
            "f224_present_count": sum(1 for a in f224 if a.status == ArtifactStatus.READY_FOR_PRELIVE_GATE),
            "f224_total": len(f224),
        }
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
        **f224_section,
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
    required, optional, f224 = check_all_artifacts_with_f224(repo_root)
    overall = overall_status(required)
    commands = build_regeneration_commands(required)

    # F224D: Scan for sprint ID collisions
    collision_report = scan_probe_artifacts(repo_root)
    collision_warnings = render_collision_warning(collision_report) if collision_report.has_collisions else []

    if collision_warnings and args.verbose:
        print("\n⚠️ Sprint ID Collision detected:")
        for w in collision_warnings:
            print(f"  {w}")

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
        required, optional, f224 = check_all_artifacts_with_f224(repo_root)
        overall = overall_status(required)
        commands = build_regeneration_commands(required)
        print("\n" + "=" * 60)
        print(f"Post-regeneration status: {overall.value}")

    # Write outputs
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        json_out = render_json(required, commands, overall, f224)
        # F224D: inject collision data into JSON output
        if collision_report.has_collisions:
            json_out["sprint_id_collisions"] = {
                "has_collisions": True,
                "collision_count": len(collision_report.collisions),
                "total_probes_scanned": collision_report.total_probes_scanned,
                "collisions": [
                    {
                        "sprint_id": c.sprint_id,
                        "aliases": c.aliases,
                        "probe_dirs": c.probe_dirs,
                        "report_paths": c.report_paths,
                        "json_paths": c.json_paths,
                    }
                    for c in collision_report.collisions
                ],
            }
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(json_out, fh, indent=2)
        print(f"\nJSON report written to: {args.output_json}")

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        md_content = render_markdown(required, commands, overall)
        # Append collision warnings to markdown
        if collision_warnings:
            md_content += "\n" + "\n".join(collision_warnings)
        with open(args.output_md, "w", encoding="utf-8") as fh:
            fh.write(md_content)
        print(f"Markdown report written to: {args.output_md}")

    # Exit code reflects overall status
    return 0 if overall == ArtifactStatus.READY_FOR_PRELIVE_GATE else 1


if __name__ == "__main__":
    sys.exit(main())