#!/usr/bin/env python3
"""F231 Artifact Inventory — read-only pack state reporter.

Verifies presence, validity, and size of F231A–F231H probe artifacts.
No live execution. No network. No MLX.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional


# ------------------------------------------------------------------
# Artifact manifest
# ------------------------------------------------------------------
ARTIFACTS: dict[str, tuple[str, str]] = {
    "F231A": ("probe_f231a_public_candidate_ledger", "public_candidate_ledger.json"),
    "F231B": ("probe_f231b_ct_acceptance_lift", "ct_acceptance_lift.json"),
    "F231C": ("probe_f231c_advisory_evidence_surface", "advisory_evidence_surface.json"),
    "F231D": ("probe_f231d_research_quality_v2", "research_quality_v2.json"),
    "F231E": ("probe_f231e_research_quality_comparable_field", "research_quality_comparable_field.json"),
    "F231F": ("probe_f231f_evidence_depth_aliases", "evidence_depth_aliases.json"),
    "F231G": ("probe_f231g_quality_sanity_bundle_smoke", "quality_sanity_bundle_smoke.json"),
    "F231H": ("probe_f231h_prelive_evidence_lift_gate", "prelive_evidence_lift_gate.json"),
}

# F231H blocking set — artifacts the gate requires to be present
GATE_BLOCKING_SET = {"F231A", "F231B", "F231C", "F231D", "F231E", "F231F", "F231G"}


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------
@dataclass
class ArtifactResult:
    name: str
    exists: bool
    valid_json: Optional[bool] = None
    size_bytes: Optional[int] = None
    test_count: Optional[int] = None
    verdict: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PackInventory:
    verdict: str
    gate_status: str
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    missing_blocking: list[str] = field(default_factory=list)
    artifacts: dict[str, ArtifactResult] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serialize ArtifactResult objects
        d["artifacts"] = {k: asdict(v) for k, v in self.artifacts.items()}
        return d


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------
def inspect_artifact(repo_root: str, probe_dir: str, artifact_file: str) -> ArtifactResult:
    path = os.path.join(repo_root, probe_dir, artifact_file)
    name = probe_dir.replace("probe_", "").upper()
    if not os.path.exists(path):
        return ArtifactResult(name=name, exists=False)
    try:
        size = os.path.getsize(path)
        with open(path) as fh:
            data = json.load(fh)
        return ArtifactResult(
            name=name,
            exists=True,
            valid_json=True,
            size_bytes=size,
            test_count=data.get("test_count"),
            verdict=data.get("verdict"),
        )
    except json.JSONDecodeError as e:
        return ArtifactResult(name=name, exists=True, valid_json=False, error=str(e))
    except Exception as e:
        return ArtifactResult(name=name, exists=True, valid_json=False, error=str(e))


def run_inventory(repo_root: str) -> PackInventory:
    artifacts: dict[str, ArtifactResult] = {}
    present: list[str] = []
    missing: list[str] = []
    malformed: list[str] = []

    for key, (probe_dir, artifact_file) in ARTIFACTS.items():
        result = inspect_artifact(repo_root, probe_dir, artifact_file)
        artifacts[key] = result
        if not result.exists:
            missing.append(key)
        elif result.valid_json is False:
            malformed.append(key)
        elif result.valid_json is True:
            present.append(key)

    # Determine pack verdict
    if set(present) == set(ARTIFACTS):
        verdict = "F231_PACK_READY"
    elif malformed:
        verdict = "F231_PACK_MALFORMED_ARTIFACTS"
    else:
        verdict = "F231_PACK_MISSING_ARTIFACTS"

    # Gate cross-check
    missing_blocking = sorted(set(GATE_BLOCKING_SET) & set(missing))
    gate_status = (
        "GATE_CORRECTLY_BLOCKING" if missing_blocking else "GATE_STALE"
    )

    return PackInventory(
        verdict=verdict,
        gate_status=gate_status,
        present=present,
        missing=missing,
        malformed=malformed,
        missing_blocking=missing_blocking,
        artifacts=artifacts,
    )


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="F231 Artifact Inventory (read-only)")
    p.add_argument(
        "--repo-root",
        default=".",
        help="Repo root (default: .)",
    )
    p.add_argument(
        "--output-json",
        metavar="PATH",
        help="Write JSON report to PATH",
    )
    p.add_argument(
        "--output-md",
        metavar="PATH",
        help="Write markdown report to PATH",
    )
    p.add_argument(
        "--gate-artifact",
        metavar="PATH",
        help="Path to F231H gate JSON (overrides default probe dir lookup)",
    )
    return p


def _render_md(inv: PackInventory) -> str:
    lines = [
        "# F231 Artifact Inventory",
        "",
        f"**Verdict:** `{inv.verdict}`",
        f"**Gate Status:** `{inv.gate_status}`",
        "",
        "## Artifact Table",
        "",
        "| Artifact | Status | Size (bytes) | Test Count | Verdict |",
        "|:---------|:-------|-------------:|------------|:--------|",
    ]
    for key, r in sorted(inv.artifacts.items()):
        if not r.exists:
            status = "❌ MISSING"
            size = "—"
            tc = "—"
            v = "—"
        elif r.valid_json is False:
            status = "⚠️ MALFORMED"
            size = str(r.size_bytes) if r.size_bytes else "—"
            tc = "—"
            v = f"`{r.error}`"
        else:
            status = "✅ OK"
            size = str(r.size_bytes)
            tc = str(r.test_count) if r.test_count is not None else "—"
            v = r.verdict or "—"
        lines.append(f"| {key} | {status} | {size} | {tc} | {v} |")

    lines += [
        "",
        "## Gate Cross-Check",
        "",
        f"- Blocking set required by F231H: `{sorted(GATE_BLOCKING_SET)}`",
        f"- Present: `{sorted(inv.present)}`",
        f"- Missing: `{sorted(inv.missing)}`",
        f"- Malformed: `{sorted(inv.malformed)}`",
        f"- Missing blocking artifacts: `{inv.missing_blocking}`",
        "",
        f"**Conclusion:** `{inv.gate_status}`",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)

    inv = run_inventory(repo_root)

    # Console output
    print(f"Verdict:     {inv.verdict}")
    print(f"Gate Status: {inv.gate_status}")
    print(f"Present:     {sorted(inv.present)}")
    print(f"Missing:     {sorted(inv.missing)}")
    print(f"Malformed:   {sorted(inv.malformed)}")
    if inv.missing_blocking:
        print(f"Missing Blocking: {inv.missing_blocking}")

    # Gate-specific check using gate artifact
    gate_path = args.gate_artifact
    if not gate_path:
        gate_path = os.path.join(
            repo_root,
            "probe_f231h_prelive_evidence_lift_gate",
            "prelive_evidence_lift_gate.json",
        )
    if os.path.exists(gate_path):
        with open(gate_path) as f:
            gate_data = json.load(f)
        gate_blocking = gate_data.get("blocking_probes", [])
        gate_verdict = gate_data.get("verdict", "UNKNOWN")
        print(f"\nF231H gate artifact verdict: {gate_verdict}")
        print(f"F231H blocking probes: {gate_blocking}")

    # JSON output
    if args.output_json:
        out_path = os.path.abspath(args.output_json)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(inv.to_dict(), f, indent=2)
        print(f"\nJSON written: {out_path}")

    # Markdown output
    if args.output_md:
        out_path = os.path.abspath(args.output_md)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(_render_md(inv))
        print(f"Markdown written: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())