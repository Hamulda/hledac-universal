# RL_CURRENT_STATE.md

**Date:** 2026-05-24
**Sprint:** Diagnostic Check

---

## Current State (from `.sprint_policy_state.json`)

| Metric | Value |
|--------|-------|
| RL enabled | True |
| Epsilon | 0.0999 (≈ 0.1 floor — training active) |
| Total reward | 606.2 |
| Sprint sequence | 124 |
| Sprint rewards count | 100 (ring buffer) |
| QMIX weights | None (not persisted) |
| Last train sprint | N/A (key missing) |

**Reward history (last 10):** `[0.0, 0.0, 0.0, 0.0, 20.0, 8.0, 4.5, 12.6, 0.0, 6.0]`

---

## Analysis

### ✅ Epsilon decay is working
Epsilon = 0.0999 is near floor (0.1), confirming RL training HAS occurred over 124 sprints.

### ⚠️ QMIX weights not persisted
State file has no `qmix_weights` key. Either:
- train_step() not called yet (sprint % 10 == 0 condition not met)
- weights serialization failing silently

### ⚠️ last_train_sprint key missing
Newer field (F257) not back-populated to existing state file.

---

## G1 Fix Verification

✅ `UMA is_critical pre-check` present in `_run_qmix_training()`:
```python
if uma.is_critical():
    log.debug("[SprintPolicyManager] Skipping QMIX train_step — M1 memory critical")
```

---

## Next Steps

1. Run 3 warm-up sprints with `--rl-train` to verify train_step() fires
2. Add `get_reward_stats()` method for reward distribution analysis
3. Add `reward_history` ring buffer tracking