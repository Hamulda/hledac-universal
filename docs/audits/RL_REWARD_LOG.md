# RL_REWARD_LOG.md

**Date:** 2026-05-24
**Sprint:** F257 RL Diagnostic

---

## First Real Reward Values (from 124 sprint history)

| Metric | Value |
|--------|-------|
| Mean reward | 4.599 |
| Min | 0.0 |
| Max | 20.0 |
| Count | 100 |

**Last 10 rewards:** `[0.0, 0.0, 0.0, 0.0, 20.0, 8.0, 4.5, 12.6, 0.0, 6.0]`

---

## Reward Distribution

Sprints show clear pattern:
- 0.0 rewards → no findings accepted that sprint
- 4.5-20.0 rewards → finding-based rewards
- Repeated cycles suggest periodic exploration

---

## Action Statistics (TBD after warm-up sprints)

Run 3 warm-up sprints with `--rl-train` to verify:
1. train_step() fires every 10 sprints
2. epsilon decay continues
3. QMIX weights persist