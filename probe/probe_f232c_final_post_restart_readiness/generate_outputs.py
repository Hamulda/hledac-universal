#!/usr/bin/env python3
"""Generate F232C output files."""

import importlib.util
import json
import os
import sys

spec = importlib.util.spec_from_file_location(
    "tools.final_prelive_readiness",
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tools/final_prelive_readiness.py",
)
module = importlib.util.module_from_spec(spec)
sys.modules["tools.final_prelive_readiness"] = module
spec.loader.exec_module(module)
fpr = module

REPO_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"
PROFILE = "nonfeed_diagnostic180"
QUERY = "mozilla.org certificate transparency subdomains april 2026"

result = fpr.run_final_readiness(REPO_ROOT, PROFILE, QUERY)

out_dir = os.path.join(REPO_ROOT, "probe_f232c_final_post_restart_readiness")
os.makedirs(out_dir, exist_ok=True)

json_path = os.path.join(out_dir, "final_post_restart_readiness.json")
with open(json_path, "w") as f:
    json.dump(result.to_dict(), f, indent=2, default=str)
print(f"JSON written: {json_path}")

md_path = os.path.join(out_dir, "REPORT_FINAL_POST_RESTART_READINESS.md")
md_text = fpr.render_markdown(result, PROFILE, QUERY)
with open(md_path, "w") as f:
    f.write(md_text)
print(f"Markdown written: {md_path}")

# POST_RESTART_COMMANDS.md
commands_md = (
    """# POST_RESTART_COMMANDS — Sprint F232C

## ABORT RULE
If `final_prelive_readiness` does not return `READY_TO_RUN_NOW` after restart, **DO NOT** run live.

## MEMORY INSTRUCTION
Restart Mac, open only terminal, run readiness first.

## SEQUENCE

### Step 1: Final Pre-Live Readiness (POST-RESTART)
```bash
python -m tools.final_prelive_readiness \\
  --repo-root . \\
  --profile nonfeed_diagnostic180 \\
  --query "mozilla.org certificate transparency subdomains april 2026" \\
  --output-json probe_f232c_final_post_restart_readiness/final_readiness.json \\
  --output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md
```

Expected: `READY_TO_RUN_NOW` or `READY_DIAGNOSTIC_ONLY`.
If `READY_TO_RESTART_AND_RUN` → ABORT and restart Mac again.

### Step 2: Run Live (only if READY_TO_RUN_NOW)
```bash
python -m core --profile nonfeed_diagnostic180 \\
  --query "mozilla.org certificate transparency subdomains april 2026" \\
  --live --require-memory-ok
```

### Step 3: Research Quality Score
```bash
python -m tools.research_quality_score \\
  --repo-root . \\
  --profile nonfeed_diagnostic180 \\
  --sprint-id F232C \\
  --output-json probe_f232c_final_post_restart_readiness/research_quality_score.json \\
  --output-md probe_f232c_final_post_restart_readiness/RESEARCH_QUALITY_SCORE.md
```

### Step 4: Live Result Sanity
```bash
python -m tools.live_result_sanity \\
  --repo-root . \\
  --profile nonfeed_diagnostic180 \\
  --output-json probe_f232c_final_post_restart_readiness/live_result_sanity.json
```

### Step 5: Evidence Delta Memory
```bash
python -m tools.evidence_delta_memory \\
  --repo-root . \\
  --profile nonfeed_diagnostic180 \\
  --output-json probe_f232c_final_post_restart_readiness/evidence_delta_memory.json
```

### Step 6: F231 Artifact Inventory (Final)
```bash
python -m tools.f231_artifact_inventory \\
  --repo-root . \\
  --output-json probe_f232c_final_post_restart_readiness/f231_artifact_inventory.json \\
  --output-md probe_f232c_final_post_restart_readiness/F231_ARTIFACT_INVENTORY.md
```

## CURRENT VERDICT

**Verdict:** `"""
    + result.verdict.value
    + """`
**Live Allowed:** """
    + str(result.live_allowed)
    + """
**Next Action:** `"""
    + result.next_action.value
    + """`
**Swap:** """
    + f"{result.swap_used_gib:.3f} GiB (tier: {result.swap_policy_tier})"
    + """
**F231 Inventory:** """
    + result.f231_inventory_verdict
    + """
**F231H Gate:** """
    + result.f231h_gate_verdict
    + """

"""
)
if result.blockers:
    commands_md += "### Blockers\n\n"
    for b in result.blockers:
        commands_md += f"- **{b.category}** ({b.severity}): {b.detail}\n"

commands_md += """
## NOTES

- All paths are absolute from repo root.
- No live execution until `READY_TO_RUN_NOW`.
- No network calls in readiness tools.
- No MLX/model loading.
"""

with open(os.path.join(out_dir, "POST_RESTART_COMMANDS.md"), "w") as f:
    f.write(commands_md)
print("POST_RESTART_COMMANDS.md written")

# post_restart_commands.json
commands_json = {
    "abort_rule": "If final_prelive_readiness != READY_TO_RUN_NOW, do not run live.",
    "memory_instruction": "Restart Mac, open only terminal, run readiness first.",
    "sequence": [
        {
            "step": 1,
            "name": "final_prelive_readiness",
            "command": (
                "python -m tools.final_prelive_readiness "
                "--repo-root . --profile nonfeed_diagnostic180 "
                '--query "mozilla.org certificate transparency subdomains april 2026" '
                "--output-json probe_f232c_final_post_restart_readiness/final_readiness.json "
                "--output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md"
            ),
            "expected": "READY_TO_RUN_NOW or READY_DIAGNOSTIC_ONLY",
            "abort_if": "READY_TO_RESTART_AND_RUN",
        },
        {
            "step": 2,
            "name": "live_nonfeed_diagnostic180",
            "command": (
                "python -m core --profile nonfeed_diagnostic180 "
                '--query "mozilla.org certificate transparency subdomains april 2026" '
                "--live --require-memory-ok"
            ),
            "only_if": "READY_TO_RUN_NOW",
        },
        {
            "step": 3,
            "name": "research_quality_score",
            "command": (
                "python -m tools.research_quality_score "
                "--repo-root . --profile nonfeed_diagnostic180 --sprint-id F232C "
                "--output-json probe_f232c_final_post_restart_readiness/research_quality_score.json "
                "--output-md probe_f232c_final_post_restart_readiness/RESEARCH_QUALITY_SCORE.md"
            ),
        },
        {
            "step": 4,
            "name": "live_result_sanity",
            "command": (
                "python -m tools.live_result_sanity "
                "--repo-root . --profile nonfeed_diagnostic180 "
                "--output-json probe_f232c_final_post_restart_readiness/live_result_sanity.json"
            ),
        },
        {
            "step": 5,
            "name": "evidence_delta_memory",
            "command": (
                "python -m tools.evidence_delta_memory "
                "--repo-root . --profile nonfeed_diagnostic180 "
                "--output-json probe_f232c_final_post_restart_readiness/evidence_delta_memory.json"
            ),
        },
        {
            "step": 6,
            "name": "f231_artifact_inventory",
            "command": (
                "python -m tools.f231_artifact_inventory "
                "--repo-root . "
                "--output-json probe_f232c_final_post_restart_readiness/f231_artifact_inventory.json "
                "--output-md probe_f232c_final_post_restart_readiness/F231_ARTIFACT_INVENTORY.md"
            ),
        },
    ],
    "current_verdict": result.verdict.value,
    "live_allowed": result.live_allowed,
    "next_action": result.next_action.value,
    "swap_used_gib": result.swap_used_gib,
    "swap_policy_tier": result.swap_policy_tier,
    "f231_inventory_verdict": result.f231_inventory_verdict,
    "f231h_gate_verdict": result.f231h_gate_verdict,
    "f231_core_ready": result.f231_core_ready,
    "f224_core_ready": result.f224_core_ready,
    "provider_surface_ok": result.provider_surface_ok,
}

json_path2 = os.path.join(out_dir, "post_restart_commands.json")
with open(json_path2, "w") as f:
    json.dump(commands_json, f, indent=2)
print("post_restart_commands.json written")

print()
print(f"Final verdict: {result.verdict.value}")
print(f"Swap used: {result.swap_used_gib:.3f} GiB")
print(f"F231 core ready: {result.f231_core_ready}")
print(f"F231 inventory: {result.f231_inventory_verdict}")
