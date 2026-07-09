# POST_RESTART_COMMANDS — Sprint F232C

## ABORT RULE
If `final_prelive_readiness` does not return `READY_TO_RUN_NOW` after restart, **DO NOT** run live.

## MEMORY INSTRUCTION
Restart Mac, open only terminal, run readiness first.

## SEQUENCE

### Step 1: Final Pre-Live Readiness (POST-RESTART)
```bash
python -m tools.final_prelive_readiness \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --query "mozilla.org certificate transparency subdomains april 2026" \
  --output-json probe_f232c_final_post_restart_readiness/final_readiness.json \
  --output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md
```

Expected: `READY_TO_RUN_NOW` or `READY_DIAGNOSTIC_ONLY`.
If `READY_TO_RESTART_AND_RUN` → ABORT and restart Mac again.

### Step 2: Run Live (only if READY_TO_RUN_NOW)
```bash
python -m core --profile nonfeed_diagnostic180 \
  --query "mozilla.org certificate transparency subdomains april 2026" \
  --live --require-memory-ok
```

### Step 3: Research Quality Score
```bash
python -m tools.research_quality_score \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --sprint-id F232C \
  --output-json probe_f232c_final_post_restart_readiness/research_quality_score.json \
  --output-md probe_f232c_final_post_restart_readiness/RESEARCH_QUALITY_SCORE.md
```

### Step 4: Live Result Sanity
```bash
python -m tools.live_result_sanity \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --output-json probe_f232c_final_post_restart_readiness/live_result_sanity.json
```

### Step 5: Evidence Delta Memory
```bash
python -m tools.evidence_delta_memory \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --output-json probe_f232c_final_post_restart_readiness/evidence_delta_memory.json
```

### Step 6: F231 Artifact Inventory (Final)
```bash
python -m tools.f231_artifact_inventory \
  --repo-root . \
  --output-json probe_f232c_final_post_restart_readiness/f231_artifact_inventory.json \
  --output-md probe_f232c_final_post_restart_readiness/F231_ARTIFACT_INVENTORY.md
```

## CURRENT VERDICT

**Verdict:** `READY_TO_RESTART_AND_RUN`
**Live Allowed:** False
**Next Action:** `RESTART_THEN_RUN_LIVE`
**Swap:** 7.000 GiB (tier: hard_block)
**F231 Inventory:** F231_PACK_READY
**F231H Gate:** READY_FOR_INTEGRATION

### Blockers

- **memory** (HARD_BLOCK): swap=7.000GiB > 4.0GiB threshold (hard_block tier)

## NOTES

- All paths are absolute from repo root.
- No live execution until `READY_TO_RUN_NOW`.
- No network calls in readiness tools.
- No MLX/model loading.
