"""
Sprint F231L — Pre-Live Cockpit Final Readiness Check

Operator answer before live. No live execution, no network, no MLX load.


Verdicts:
  READY_TO_RUN_NOW
  READY_TO_RESTART_AND_RUN
  BLOCKED_BY_ARTIFACTS
  BLOCKED_BY_MEMORY
  BLOCKED_BY_PROVIDER_SURFACE

Uses:
  - F231J artifact inventory (probe_f231j_artifact_inventory/f231_artifact_inventory.json)
  - prelive_decision_gate (probe_f219f_prelive_decision_gate/prelive_decision.json)
  - prelive_artifact_pack (probe_f219i_prelive_artifact_pack/artifact_pack.json)
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
BASE = Path('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')
_F231_BLOCKING_PROBES = ['probe_f231a_public_candidate_ledger', 'probe_f231b_ct_acceptance_lift', 'probe_f231c_advisory_evidence_surface', 'probe_f231d_research_quality_v2', 'probe_f231e_research_quality_comparable_field', 'probe_f231f_evidence_depth_aliases', 'probe_f231g_quality_sanity_bundle_smoke']
_CLEAN_SWAP_MAX_GIB = 2.0
_DIAGNOSTIC_SWAP_MAX_GIB = 4.0

@dataclass(slots=True)
class ReadinessResult:
    verdict: str
    next_action: str
    next_action_detail: str = ''
    f231_inventory_loaded: bool = False
    f231_missing: list[str] = field(default_factory=list)
    decision_gate_path: str = ''
    artifact_pack_path: str = ''
    uma_swap_gib: float = 0.0
    gate_decision: str = ''
    missing_artifacts: list[str] = field(default_factory=list)

def load_optional_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def extract_swap_from_decision(decision_data: dict) -> float:
    uma = decision_data.get('uma', {})
    if isinstance(uma, dict):
        return float(uma.get('swap_used_gib', 0.0))
    return 0.0

def get_f231_missing(inventory_path: Path) -> tuple[bool, list[str]]:
    """Load F231J inventory, return (loaded_ok, missing_probes)."""
    if not inventory_path.exists():
        return (False, list(_F231_BLOCKING_PROBES))
    try:
        data = json.load(open(inventory_path))
        missing = data.get('missing', [])
        return (True, missing)
    except Exception:
        return (False, list(_F231_BLOCKING_PROBES))

def check_live_allowed(decision_data: dict, artifact_pack: dict) -> tuple[str, list[str]]:
    """Check if gate decision and artifact pack allow live."""
    gate_decision = decision_data.get('decision', 'BLOCKED_BY_UNKNOWN')
    live_allowed = decision_data.get('live_allowed', False)
    missing_required = artifact_pack.get('missing_required', [])
    overall = artifact_pack.get('overall', 'UNKNOWN')
    return (gate_decision, missing_required)

def derive_verdict(gate_decision: str, f231_missing: list[str], swap_gib: float, missing_artifacts: list[str]) -> ReadinessResult:
    """Derive final operator verdict."""
    if gate_decision == 'BLOCKED_BY_PROVIDER_SURFACE':
        return ReadinessResult(verdict='BLOCKED_BY_PROVIDER_SURFACE', next_action='fix_provider_surface', next_action_detail='probe_f217c_public_bootstrap and/or probe_f217d_ct_provider_resilience missing')
    if gate_decision == 'BLOCKED_BY_MEMORY':
        return ReadinessResult(verdict='BLOCKED_BY_MEMORY', next_action='restart_then_run_live', next_action_detail=f'swap={swap_gib:.2f}GiB — restart required before live')
    if f231_missing:
        return ReadinessResult(verdict='BLOCKED_BY_ARTIFACTS', next_action='run_missing_probe', next_action_detail=','.join((f'probe_{m.lower()}' for m in f231_missing)), f231_missing=f231_missing)
    if missing_artifacts:
        return ReadinessResult(verdict='BLOCKED_BY_ARTIFACTS', next_action='run_missing_probe', next_action_detail=','.join(missing_artifacts))
    if swap_gib <= _CLEAN_SWAP_MAX_GIB:
        return ReadinessResult(verdict='READY_TO_RUN_NOW', next_action='run_live_now', next_action_detail='nonfeed_diagnostic180 — exact command below', uma_swap_gib=swap_gib)
    elif swap_gib <= _DIAGNOSTIC_SWAP_MAX_GIB:
        return ReadinessResult(verdict='READY_DIAGNOSTIC_ONLY', next_action='run_with_hardware_taint', next_action_detail=f'swap={swap_gib:.2f}GiB in (2.0, 4.0]GiB — hardware taint', uma_swap_gib=swap_gib)
    else:
        return ReadinessResult(verdict='READY_TO_RESTART_AND_RUN', next_action='restart_then_run_live', next_action_detail=f'swap={swap_gib:.2f}GiB > 4.0GiB — restart required', uma_swap_gib=swap_gib)


def build_result(decision_path: Path, artifact_pack_path: Path, f231_inventory_path: Path) -> ReadinessResult:
    decision_data = load_optional_json(decision_path) or {}
    artifact_pack = load_optional_json(artifact_pack_path) or {}
    f231_loaded, f231_missing = get_f231_missing(f231_inventory_path)
    swap_gib = extract_swap_from_decision(decision_data)
    gate_decision, missing_artifacts = check_live_allowed(decision_data, artifact_pack)
    result = derive_verdict(gate_decision, f231_missing, swap_gib, missing_artifacts)
    result.decision_gate_path = str(decision_path)
    result.artifact_pack_path = str(artifact_pack_path)
    result.f231_inventory_loaded = f231_loaded
    result.gate_decision = gate_decision
    result.missing_artifacts = missing_artifacts
    return result

def render_markdown(result: ReadinessResult, profile: str, query: str) -> str:
    icon = '✅' if result.verdict in ('READY_TO_RUN_NOW', 'READY_DIAGNOSTIC_ONLY') else '❌'
    lines = ['# Pre-Live Cockpit Final Readiness — Sprint F231L', '', f'**Verdict:** {icon} `{result.verdict}`', f'**Next Action:** `{result.next_action}`']
    if result.next_action_detail:
        lines.append(f'**Detail:** `{result.next_action_detail}`')
    lines.extend(['', '## Component Status', '', f'- **F231 Inventory Loaded:** `{result.f231_inventory_loaded}`', f"- **F231 Missing:** `{result.f231_missing or 'none'}`", f'- **Gate Decision:** `{result.gate_decision}`', f'- **UMA Swap:** `{result.uma_swap_gib:.2f}GiB`', f"- **Missing Artifacts:** `{result.missing_artifacts or 'none'}`", '', '## Next Actions'])
    if result.verdict == 'READY_TO_RUN_NOW':
        lines.extend(['', 'Ready to run nonfeed_diagnostic180 live:', '```bash', f'python -m core --profile nonfeed_diagnostic180 --query {repr(query)} --live', '```'])
    elif result.verdict == 'READY_TO_RESTART_AND_RUN':
        lines.extend(['', 'Swap too high. Restart then run:', '```bash', '# restart terminal / session first', f'python -m core --profile nonfeed_diagnostic180 --query {repr(query)} --live', '```'])
    elif result.verdict == 'BLOCKED_BY_ARTIFACTS':
        lines.extend(['', 'Run missing probe lanes:', '```bash'])
        for probe in result.f231_missing or result.missing_artifacts:
            lines.append(f'python -m pytest tests/{probe} -v --tb=short')
        lines.append('```')
    lines.extend(['', f'*Profile: `{profile}` | Query: `{query}`*'])
    return '\n'.join(lines)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='F231L Pre-Live Cockpit Final Readiness')
    parser.add_argument('--decision-json', '-d', type=Path, default=BASE / 'probe_f219f_prelive_decision_gate/prelive_decision.json', help='Decision gate JSON path')
    parser.add_argument('--artifact-pack-json', '-a', type=Path, default=BASE / 'probe_f219i_prelive_artifact_pack/artifact_pack.json', help='Artifact pack JSON path')
    parser.add_argument('--f231-inventory-json', '-f', type=Path, default=BASE / 'probe_f231j_artifact_inventory/f231_artifact_inventory.json', help='F231J artifact inventory JSON path')
    parser.add_argument('--output-json', '-o', type=Path, help='Write JSON output')
    parser.add_argument('--output-md', '-m', type=Path, help='Write markdown report')
    parser.add_argument('--profile', default='nonfeed_diagnostic180', help='Profile name for report header')
    parser.add_argument('--query', default='mozilla.org certificate transparency subdomains april 2026', help='Query for report header')
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = build_result(args.decision_json, args.artifact_pack_json, args.f231_inventory_json)
    icon = '✅' if result.verdict in ('READY_TO_RUN_NOW', 'READY_DIAGNOSTIC_ONLY') else '❌'
    print(f"{'=' * 60}")
    print(f'  Verdict:      {icon} {result.verdict}')
    print(f'  Next Action:  {result.next_action}')
    if result.next_action_detail:
        print(f'  Detail:       {result.next_action_detail}')
    print(f"  F231 Missing: {result.f231_missing or 'none'}")
    print(f'  Gate:         {result.gate_decision}')
    print(f'  Swap:         {result.uma_swap_gib:.2f}GiB')
    print(f"{'=' * 60}")
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, 'w', encoding='utf-8') as f:
            json.dump(result.__dict__, f, indent=2, default=str)
        print(f'JSON: {args.output_json}')
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        md = render_markdown(result, args.profile, args.query)
        with open(args.output_md, 'w', encoding='utf-8') as f:
            f.write(md)
        print(f'MD: {args.output_md}')
    return 0 if result.verdict in ('READY_TO_RUN_NOW', 'READY_DIAGNOSTIC_ONLY') else 1
if __name__ == '__main__':
    sys.exit(main())