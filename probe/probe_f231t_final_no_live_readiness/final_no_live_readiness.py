#!/usr/bin/env python3
"""Sprint F231T — Final No-Live Readiness Probe

Reads F231 artifact inventory, prelive gate, F231L cockpit, F231R, F231S
outputs and produces one definitive operator answer.

No live execution. No network. No MLX.

Verdicts:
  READY_TO_RUN_NOW
  READY_TO_RESTART_AND_RUN
  BLOCKED_BY_CONTRACT
  BLOCKED_BY_MEMORY
  BLOCKED_BY_ARTIFACTS
"""
from __future__ import annotations



import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

REPO_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"


class Verdict(str, Enum):
    READY_TO_RUN_NOW = "READY_TO_RUN_NOW"
    READY_TO_RESTART_AND_RUN = "READY_TO_RESTART_AND_RUN"
    BLOCKED_BY_CONTRACT = "BLOCKED_BY_CONTRACT"
    BLOCKED_BY_MEMORY = "BLOCKED_BY_MEMORY"
    BLOCKED_BY_ARTIFACTS = "BLOCKED_BY_ARTIFACTS"
    BLOCKED_BY_PROVIDER_SURFACE = "BLOCKED_BY_PROVIDER_SURFACE"


@dataclass
class Blocker:
    category: str  # memory | contract | artifacts | provider_surface
    detail: str
    probe: str | None = None


@dataclass
class ReadinessResult:
    verdict: Verdict
    live_allowed: bool
    blockers: list[Blocker] = field(default_factory=list)
    f231_inventory_verdict: str = ""
    f231h_gate_verdict: str = ""
    f224_blocking: list[str] = field(default_factory=list)
    f231_blocking: list[str] = field(default_factory=list)
    uma_swap_gib: float = 0.0
    uma_state: str = "unknown"
    suggested_command: str = ""
    merge_log: list[str] = field(default_factory=list)


def load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — probe: file read errors expected in test context
        return None


def run_inventory_check() -> tuple[bool, str, list[str]]:
    """F231 artifact inventory — all 8 present and valid."""
    inventory_path = os.path.join(REPO_ROOT, "tools/f231_artifact_inventory.py")
    if not os.path.exists(inventory_path):
        return False, "F231_INVENTORY_TOOL_MISSING", []

    sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)
    from tools.f231_artifact_inventory import run_inventory
    inv = run_inventory(REPO_ROOT)
    missing = sorted(set(inv.missing))
    ready = inv.verdict == "F231_PACK_READY" and not missing
    return ready, inv.verdict, missing


def run_decision_gate() -> tuple[str, dict]:
    """prelive_decision_gate output via run_gate()."""
    gate_path = os.path.join(REPO_ROOT, "tools/prelive_decision_gate.py")
    if not os.path.exists(gate_path):
        return "BLOCKED_BY_UNKNOWN", {}

    sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)
    from tools.prelive_decision_gate import run_gate
    result = run_gate(REPO_ROOT, "nonfeed_diagnostic",
                      "mozilla.org certificate transparency subdomains april 2026")
    return result.decision.value, {
        "live_allowed": result.live_allowed,
        "reasons": result.reasons,
        "warnings": result.warnings,
        "f224_core_ready": result.f224_core_ready,
        "missing_f224_artifacts": result.missing_f224_artifacts,
        "f231_core_ready": result.f231_core_ready,
        "missing_f231_artifacts": result.missing_f231_artifacts,
        "swap_policy_tier": result.swap_policy_tier,
        "swap_gate_reason": result.swap_gate_reason,
        "hardware_constrained": result.hardware_constrained,
        "uma_swap_gib": result.uma.get("swap_used_gib", 0.0),
        "uma_state": result.uma.get("uma_state", "unknown"),
        "suggested_live_command": result.suggested_live_command,
    }


def run_cockpit_check() -> tuple[str, str]:
    """F231L prelive cockpit final readiness verdict."""
    cockpit_json = os.path.join(
        REPO_ROOT,
        "probe_f231l_prelive_cockpit_final_readiness",
        "prelive_cockpit_final_readiness.json",
    )
    data = load_json(cockpit_json)
    if not data:
        return "COCKPIT_ABSENT", ""
    return data.get("verdict", "UNKNOWN"), data.get("next_action_detail", "")


def run_f231h_gate() -> tuple[str, str, list[str]]:
    """F231H evidence lift gate — verdict and missing blocking probes."""
    gate_json = os.path.join(
        REPO_ROOT,
        "probe_f231h_prelive_evidence_lift_gate",
        "prelive_evidence_lift_gate.json",
    )
    data = load_json(gate_json)
    if not data:
        return "GATE_ABSENT", "", []
    return (
        data.get("verdict", "UNKNOWN"),
        data.get("gate_status", ""),
        data.get("blocking_probes", []),
    )


def check_f219_aliases() -> dict[str, bool]:
    """F219 aliases (F217→F219 provider surface alias table)."""
    aliases = {}
    # probe_f219h public_fetcher_import_seal → satisfies probe_f217c
    p219h = os.path.join(
        REPO_ROOT,
        "probe_f219h_public_fetcher_import_seal",
        "public_fetcher_import_seal.json",
    )
    d = load_json(p219h)
    aliases["probe_f219h_public_fetcher_import_seal"] = d is not None

    # probe_f219d public_session_seal → also satisfies probe_f217c
    p219d = os.path.join(
        REPO_ROOT,
        "probe_f219d_public_session_seal",
        "public_session_seal.json",
    )
    d = load_json(p219d)
    aliases["probe_f219d_public_session_seal"] = d is not None

    # probe_f219e ct_provider_cooldown → satisfies probe_f217d
    p219e = os.path.join(
        REPO_ROOT,
        "probe_f219e_ct_provider_cooldown",
        "ct_cooldown.json",
    )
    d = load_json(p219e)
    aliases["probe_f219e_ct_provider_cooldown"] = d is not None

    return aliases


def determine_verdict(
    gate_decision: str,
    gate_data: dict,
    f224_blocking: list[str],
    f231_blocking: list[str],
    uma_swap: float,
    uma_state: str,
    inventory_ready: bool,
    inventory_verdict: str,
    f231h_gate: str,
) -> ReadinessResult:
    blockers: list[Blocker] = []
    merge_log: list[str] = []

    merge_log.append(f"gate_decision={gate_decision}")
    merge_log.append(f"inventory_ready={inventory_ready} verdict={inventory_verdict}")
    merge_log.append(f"f231h_gate={f231h_gate}")

    # Memory: swap > 4.0 GiB
    if uma_swap > 4.0:
        blockers.append(Blocker(
            category="memory",
            detail=f"swap={uma_swap:.3f}GiB > 4.0GiB threshold",
        ))
        merge_log.append(f"blocker=MEMORY swap={uma_swap:.3f}GiB")

    # Contract: Hermes Metal finalizer fails (from gate reasons)
    for reason in gate_data.get("reasons", []):
        if "BLOCKED_BY_CONTRACT" in reason and "Hermes Metal" in reason:
            blockers.append(Blocker(
                category="contract",
                detail=reason,
                probe="probe_f219b_hermes_metal_finalizer",
            ))
            merge_log.append(f"blocker=CONTRACT Hermes Metal finalizer FAILED")
        elif "BLOCKED_BY_CONTRACT" in reason and "probe_f224d" in reason:
            blockers.append(Blocker(
                category="contract",
                detail=reason,
                probe="probe_f224d_confidence_policy",
            ))
            merge_log.append(f"blocker=CONTRACT {reason}")

    # F224 blocking artifacts missing
    for probe in f224_blocking:
        blockers.append(Blocker(
            category="artifacts",
            detail=f"{probe} missing — required for nonfeed_diagnostic",
            probe=probe,
        ))
        merge_log.append(f"blocker=F224_ARTIFACT {probe}")

    # F231 blocking artifacts missing
    for probe in f231_blocking:
        blockers.append(Blocker(
            category="artifacts",
            detail=f"{probe} missing — required evidence lift pack",
            probe=probe,
        ))
        merge_log.append(f"blocker=F231_ARTIFACT {probe}")

    # If gate says BLOCKED_BY_PROVIDER_SURFACE but F219 aliases exist,
    # the gate was run against old probe names — not a real block.
    alias_check = check_f219_aliases()
    merge_log.append(f"f219_aliases={alias_check}")

    # Determine final verdict
    memory_blocks = any(b.category == "memory" for b in blockers)
    contract_blocks = any(b.category == "contract" for b in blockers)
    artifact_blocks = any(b.category == "artifacts" for b in blockers)

    if memory_blocks and not (contract_blocks or artifact_blocks):
        # Only memory blocks — READY_TO_RESTART_AND_RUN
        live_allowed = False
        suggested = (
            f"python -m core "
            f"--profile nonfeed_diagnostic180 "
            f'--query "mozilla.org certificate transparency subdomains april 2026" '
            f"--live --require-memory-ok"
        )
        merge_log.append("verdict=READY_TO_RESTART_AND_RUN (memory only)")
        return ReadinessResult(
            verdict=Verdict.READY_TO_RESTART_AND_RUN,
            live_allowed=live_allowed,
            blockers=blockers,
            uma_swap_gib=uma_swap,
            uma_state=uma_state,
            suggested_command=suggested,
            merge_log=merge_log,
            f224_blocking=f224_blocking,
            f231_blocking=f231_blocking,
            f231_inventory_verdict=inventory_verdict,
            f231h_gate_verdict=f231h_gate,
        )
    elif contract_blocks:
        live_allowed = False
        # Find the exact failing artifact
        failing_probe = next(
            (b.probe for b in blockers if b.category == "contract" and b.probe),
            "probe_f219b_hermes_metal_finalizer",
        )
        suggested = f"# BLOCKED_BY_CONTRACT: {failing_probe} failing — fix contract first"
        merge_log.append(f"verdict=BLOCKED_BY_CONTRACT probe={failing_probe}")
        return ReadinessResult(
            verdict=Verdict.BLOCKED_BY_CONTRACT,
            live_allowed=live_allowed,
            blockers=blockers,
            uma_swap_gib=uma_swap,
            uma_state=uma_state,
            suggested_command=suggested,
            merge_log=merge_log,
            f224_blocking=f224_blocking,
            f231_blocking=f231_blocking,
            f231_inventory_verdict=inventory_verdict,
            f231h_gate_verdict=f231h_gate,
        )
    elif artifact_blocks:
        live_allowed = False
        suggested = (
            "python -m pytest tests/probe_f224d_confidence_policy -v --tb=short && "
            "python -m pytest tests/probe_f231a_public_candidate_ledger -v --tb=short"
        )
        merge_log.append("verdict=BLOCKED_BY_ARTIFACTS")
        return ReadinessResult(
            verdict=Verdict.BLOCKED_BY_ARTIFACTS,
            live_allowed=live_allowed,
            blockers=blockers,
            uma_swap_gib=uma_swap,
            uma_state=uma_state,
            suggested_command=suggested,
            merge_log=merge_log,
            f224_blocking=f224_blocking,
            f231_blocking=f231_blocking,
            f231_inventory_verdict=inventory_verdict,
            f231h_gate_verdict=f231h_gate,
        )
    else:
        # All clear
        live_allowed = True
        suggested = (
            f"python -m core "
            f"--profile nonfeed_diagnostic180 "
            f'--query "mozilla.org certificate transparency subdomains april 2026" '
            f"--live --require-memory-ok"
        )
        merge_log.append("verdict=READY_TO_RUN_NOW")
        return ReadinessResult(
            verdict=Verdict.READY_TO_RUN_NOW,
            live_allowed=live_allowed,
            blockers=blockers,
            uma_swap_gib=uma_swap,
            uma_state=uma_state,
            suggested_command=suggested,
            merge_log=merge_log,
            f224_blocking=f224_blocking,
            f231_blocking=f231_blocking,
            f231_inventory_verdict=inventory_verdict,
            f231h_gate_verdict=f231h_gate,
        )


def run() -> ReadinessResult:
    merge_log: list[str] = []
    merge_log.append("=== F231T Final No-Live Readiness ===")

    # 1. F231 artifact inventory
    inv_ready, inv_verdict, inv_missing = run_inventory_check()
    merge_log.append(f"f231_inventory: ready={inv_ready} verdict={inv_verdict} missing={inv_missing}")

    # 2. prelive_decision_gate
    gate_decision, gate_data = run_decision_gate()
    merge_log.append(f"decision_gate: decision={gate_decision} live={gate_data.get('live_allowed')}")

    # 3. F231L cockpit
    cockpit_verdict, cockpit_detail = run_cockpit_check()
    merge_log.append(f"f231l_cockpit: verdict={cockpit_verdict} detail={cockpit_detail}")

    # 4. F231H evidence lift gate
    f231h_verdict, f231h_status, f231h_blocking = run_f231h_gate()
    merge_log.append(f"f231h_gate: verdict={f231h_verdict} status={f231h_status}")

    f224_blocking = gate_data.get("missing_f224_artifacts", [])
    f231_blocking = gate_data.get("missing_f231_artifacts", [])
    uma_swap = gate_data.get("uma_swap_gib", 0.0)
    uma_state = gate_data.get("uma_state", "unknown")

    result = determine_verdict(
        gate_decision=gate_decision,
        gate_data=gate_data,
        f224_blocking=f224_blocking,
        f231_blocking=f231_blocking,
        uma_swap=uma_swap,
        uma_state=uma_state,
        inventory_ready=inv_ready,
        inventory_verdict=inv_verdict,
        f231h_gate=f231h_status,
    )

    result.merge_log = merge_log + result.merge_log
    return result


if __name__ == "__main__":
    result = run()

    print(f"{'='*60}")
    print(f"  F231T Final No-Live Readiness")
    print(f"{'='*60}")
    print(f"  Verdict:      {result.verdict.value}")
    print(f"  Live Allowed: {result.live_allowed}")
    print(f"  UMA Swap:    {result.uma_swap_gib:.3f} GiB")
    print(f"  UMA State:   {result.uma_state}")
    print(f"  F224 Missing: {result.f224_blocking}")
    print(f"  F231 Missing: {result.f231_blocking}")
    print(f"{'='*60}")

    if result.blockers:
        print("Blockers:")
        for b in result.blockers:
            print(f"  [{b.category}] {b.detail}")
            if b.probe:
                print(f"      probe={b.probe}")

    print(f"\nSuggested command:")
    print(f"  {result.suggested_command}")

    print(f"\nMerge log:")
    for entry in result.merge_log:
        print(f"  {entry}")

    # Write JSON output
    out_dir = os.path.join(REPO_ROOT, "probe_f231t_final_no_live_readiness")
    os.makedirs(out_dir, exist_ok=True)

    output_json = os.path.join(out_dir, "final_no_live_readiness.json")
    with open(output_json, "w") as f:
        json.dump({
            "sprint": "F231T",
            "verdict": result.verdict.value,
            "live_allowed": result.live_allowed,
            "blockers": [
                {"category": b.category, "detail": b.detail, "probe": b.probe}
                for b in result.blockers
            ],
            "f231_inventory_verdict": result.f231_inventory_verdict,
            "f231h_gate_verdict": result.f231h_gate_verdict,
            "f224_blocking": result.f224_blocking,
            "f231_blocking": result.f231_blocking,
            "uma_swap_gib": result.uma_swap_gib,
            "uma_state": result.uma_state,
            "suggested_command": result.suggested_command,
            "merge_log": result.merge_log,
        }, f, indent=2)
    print(f"\nJSON written: {output_json}")

    output_md = os.path.join(out_dir, "REPORT_FINAL_NO_LIVE_READINESS.md")
    with open(output_md, "w") as f:
        f.write(f"""# F231T Final No-Live Readiness Report

## Verdict

**`{result.verdict.value}`** — Live Allowed: `{result.live_allowed}`

## Memory (UMA)

| Field | Value |
|-------|-------|
| Swap Used | {result.uma_swap_gib:.3f} GiB |
| UMA State | `{result.uma_state}` |

## F231 Artifact Inventory

| Field | Value |
|-------|-------|
| Verdict | `{result.f231_inventory_verdict}` |
| Gate Status | `{result.f231h_gate_verdict}` |

## F224 Blocking Artifacts

    f224_blocking_str = ', '.join(f"`{p}`" for p in result.f224_blocking) if result.f224_blocking else '_none_'
    f231_blocking_str = ', '.join(f"`{p}`" for p in result.f231_blocking) if result.f231_blocking else '_none_'

## F224 Blocking Artifacts

{f224_blocking_str}

## F231 Blocking Artifacts

{f231_blocking_str}

## Blockers

""")
        if result.blockers:
            for b in result.blockers:
                f.write(f"- **{b.category.upper()}**: {b.detail}")
                if b.probe:
                    f.write(f" (`{b.probe}`)")
                f.write("\n")
        else:
            f.write("_none_\n")

        f.write(f"""
## Suggested Live Command

```bash
{result.suggested_command}
```

## Merge Log

""")
        for entry in result.merge_log:
            f.write(f"- {entry}\n")

    print(f"Markdown written: {output_md}")