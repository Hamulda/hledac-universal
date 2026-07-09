# Final Pre-Live Readiness — F232C

**Profile:** `nonfeed_diagnostic180`
**Query:** `mozilla.org certificate transparency subdomains april 2026`
**Date:** 2026-05-10

---

## Verdict: `READY_TO_RESTART_AND_RUN`

**Live Allowed:** False
**Next Action:** RESTART_THEN_RUN_LIVE
**Detail:** swap=7.000GiB > 4.0GiB — restart required

### Blockers

- **memory** (HARD_BLOCK): swap=7.000GiB > 4.0GiB threshold (hard_block tier)
  - swap=7.000GiB (threshold=4.0GiB)

---

## Swap / Memory

| Metric | Value |
|--------|-------|
| swap_used_gib | 7.000 |
| uma_state | unknown |
| swap_policy_tier | hard_block |
| swap_gate_reason | swap=7.000GiB > 4.0GiB |
| hardware_constrained | True |

**Thresholds:** clean<2.0GiB, diagnostic<4.0GiB, hard_block>=4.0GiB

## F231 Artifact Inventory

| Check | Value |
|-------|-------|
| verdict | F231_PACK_READY |
| F231H gate | READY_FOR_INTEGRATION () |
| F231 core ready | True |
| F231 blocking missing | none |
| F231 present | F231A, F231B, F231C, F231D, F231E, F231F, F231G, F231H |

## Gate Decision

| Check | Value |
|-------|-------|
| gate_decision | BLOCKED_BY_MEMORY |
| gate_live_allowed | False |
| F224 core ready | True |
| F224 blocking missing | ['probe_f224d_confidence_policy'] |
| provider_surface_ok | False |

**Gate Reasons (original):**
- BLOCKED_BY_MEMORY: swap=5.84GiB > 4.0GiB — restart required

---

## Post-Restart Command Pack

⚠️ **ABORT RULE:** If `final_prelive_readiness` does not return `READY_TO_RUN_NOW` after restart, do NOT run live.

**Memory instruction:** Restart Mac, open only terminal, run readiness first.

```bash
# 1. Run final pre-live readiness (post-restart)
python -m tools.final_prelive_readiness \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --query "mozilla.org certificate transparency subdomains april 2026" \
  --output-json probe_f232c_final_post_restart_readiness/final_readiness.json \
  --output-md probe_f232c_final_post_restart_readiness/FINAL_READINESS.md

# 2. If READY_TO_RUN_NOW — run live nonfeed_diagnostic180
python -m core --profile nonfeed_diagnostic180 --query "mozilla.org certificate transparency subdomains april 2026" --live --require-memory-ok

# 3. Research quality score
python -m tools.research_quality_score \
  --repo-root . --profile nonfeed_diagnostic180 --sprint-id F232C \
  --output-json probe_f232c_final_post_restart_readiness/research_quality_score.json \
  --output-md probe_f232c_final_post_restart_readiness/RESEARCH_QUALITY_SCORE.md

# 4. Live result sanity
python -m tools.live_result_sanity \
  --repo-root . --profile nonfeed_diagnostic180 \
  --output-json probe_f232c_final_post_restart_readiness/live_result_sanity.json

# 5. Evidence delta memory
python -m tools.evidence_delta_memory \
  --repo-root . --profile nonfeed_diagnostic180 \
  --output-json probe_f232c_final_post_restart_readiness/evidence_delta_memory.json

# 6. F231 artifact inventory (final)
python -m tools.f231_artifact_inventory \
  --repo-root . \
  --output-json probe_f232c_final_post_restart_readiness/f231_artifact_inventory.json \
  --output-md probe_f232c_final_post_restart_readiness/F231_ARTIFACT_INVENTORY.md
```

---

## Merge Log

- f231t_read: verdict=READY_TO_RESTART_AND_RUN swap=5.735
- gate_fresh: decision=BLOCKED_BY_MEMORY live_allowed=False
- f231_inventory: verdict=F231_PACK_READY present=['F231A', 'F231B', 'F231C', 'F231D', 'F231E', 'F231F', 'F231G', 'F231H'] missing=[]
- f231h_gate: verdict=READY_FOR_INTEGRATION status=
- f224_core_ready=True missing=['probe_f224d_confidence_policy']
- f231_core_ready=True missing=[]
- provider_surface_ok=False
- swap_used=7.000GiB uma_state=unknown
- contract_false_positives_stripped: 0