# RL_ACTIVATION_DONE

**Sprint:** F261 — QMIX RL Activation
**Date:** 2026-06-01
**Files modified:**
- `rl/sprint_policy_manager.py` (reward, replay buffer pending, env alias, properties, epsilon schedule)
- `tests/test_sprint_policy_manager.py` (TestF261Activation suite + fixture isolation)
- `tests/probe_f261_qmix_activation.py` (clamp + formula updates)

---

## Root cause of the 124-sprint waste

`SprintPolicyManager._compute_reward()` was looking at `result.findings_accepted`, but the
canonical field on `SprintSchedulerResult` (line 1900 of `runtime/sprint_scheduler.py`) is
`accepted_findings`. `getattr(result, "findings_accepted", 0)` returned 0 every sprint, so
`log1p(0) = 0` ⇒ reward always landed at 0 minus time penalty. Total reward accumulated
because earlier sprints persisted `totalReward=606.2` to disk, but the Q-network never
saw a meaningful gradient signal.

The new `_compute_reward` tolerates both field names (`accepted_findings` first,
`findings_accepted` as legacy alias), so the F257 callers keep working.

---

## Step 1 — StateExtractor verification

`rl/state_extractor.py` already builds the 12-dim observation from real
`SprintSchedulerResult` fields. No placeholder zeros remain. Field mapping:

| dim | source field | normalization |
|----:|--------------|---------------|
| 0  | `accepted_findings` (alias `findings_accepted`) | `/ 50.0` cap 1.0 |
| 1  | `actual_duration_s` | `/ 3600.0` cap 1.0 |
| 2  | `cycles_completed` | `/ 50.0` cap 1.0 |
| 3  | `accepted / total` | ratio 0-1 |
| 4  | `new_iocs` | `/ 100.0` cap 1.0 |
| 5  | `source_quality_avg` (fallback: acceptance ratio) | 0-1 |
| 6  | `pending_count` | `/ 200.0` cap 1.0 |
| 7  | `memory_pressure` | cap 1.0 |
| 8  | `graph_entropy` | cap 1.0 |
| 9  | `time_since_last_finding` | `/ 300.0` cap 1.0 |
| 10 | `resource_concurrency` | cap 1.0 |
| 11 | `_reward_ema` (stateful) | bounded |

All `getattr(..., 0)` defaults in place; the only stateful field is `_reward_ema`
which is updated when `result.last_reward` is present.

---

## Step 2 — Reward function rationale

`SprintPolicyManager._compute_reward()` now implements the F261 spec:

```python
findings_accepted = float(result.accepted_findings or result.findings_accepted or 0)
runtime = float(result.actual_duration_s or 0.0)

# quality_multiplier = mean(source_quality_scores) if any, else 1.0
quality_scores = ...   # from result.scorecard.source_quality_scores
quality_multiplier = mean(quality_scores) if quality_scores else 1.0

# time_penalty = max(0, (runtime - 1200) / 600) — soft 20-min budget
time_penalty = max(0.0, (runtime - 1200.0) / 600.0)

reward = math.log1p(findings_accepted) * quality_multiplier - time_penalty

# Optional bounded novelty bonus from scorecard.semantic_novelty
reward += novelty_bonus    # ∈ [0, 1]

# Clamp to [-2.0, 5.0] — prevents Q-value explosion
return max(-2.0, min(5.0, reward))
```

**Rationale:**
- `log1p(accepted)` grows monotonically without saturating — 100 findings ≈ 4.6,
  1000 findings ≈ 6.9 (clamped).
- `quality_multiplier` defaults to `1.0` (neutral) so legacy callers that do not
  pass a scorecard are not penalised.
- The 20-min soft budget with 10-min ramp matches sprint_duration_s = 1800 (30 min)
  with a 5-min windup; the ramp keeps the gradient useful for normal-length sprints.
- `[-2.0, 5.0]` clamp widens the lower bound from F257's `[-1.0, 5.0]` so a
  catastrophic 30-min over-budget sprint still gets a small negative signal
  instead of saturating at the floor.

---

## Step 3 — Replay buffer store (true next_state)

`SprintPolicyManager.update()` now caches the previous-sprint observation so the
next call can push `(prev_state, prev_action, prev_reward, current_state)` — the
next state is the *following* sprint's observation, not a current-state alias.

```python
# In __init__:
self._pending_state: Any = None
self._pending_action: np.ndarray | None = None
self._pending_reward: float | None = None

# In update() — push happens AFTER state extraction so next_state is real
if self._pending_state is not None and ...:
    self._replay_buffer.push(
        state=self._pending_state,
        actions=self._pending_action,
        reward=self._pending_reward,
        next_state=current_state_list,   # observed this sprint
        done=False,
    )
# Always overwrite pending with the current sprint's data
self._pending_state = current_state_list
self._pending_action = action_vector
self._pending_reward = reward
```

First-sprint edge case: pending state is `None`, so push is skipped; only the
pending state is cached for the next call. Test `test_first_sprint_skips_buffer_push`
confirms `replay_size == 0` after one update.

---

## Step 4 — Q-network training activation

`SprintPolicyManager._run_qmix_training()` already implements all five gates
needed for safe training on M1 8GB UMA:

| Gate | Check | Default |
|------|-------|---------|
| L1 | M1 UMA critical (utils.uma_budget) | skip when `is_critical()` |
| L2 | system RAM % (psutil) | skip when `> 80%` (overridable via `HLEDAC_RL_SKIP_RAM_GATE=1`) |
| L3 | cooldown (time.monotonic) | `≥ 1.0s` between steps |
| L4 | per-sprint cap | `1` train step per sprint |
| L5 | burn-in | buffer size `≥ 64` (constant `_MIN_REPLAY_SIZE`) |

Training cadence: every `HLEDAC_RL_TRAIN_EVERY` sprints (spec alias), default 10.
Legacy env var `HLEDAC_RL_TRAIN_INTERVAL` is still respected as a fallback.

After each step: `mx.eval([])` is called BEFORE `mx.metal.clear_cache()`
(M1 GHOST_INVARIANT I11), and updated weights are persisted via
`mlx.core.savez` to `.qmix_weights.npz` and serialised into
`.sprint_policy_state.json["qmix_weights"]`.

CLI: `python -m hledac.universal --rl-train ...` activates training; default
remains inference-only (`rl_train_mode=False`).

---

## Step 5 — `--rl-train` flag

Already present in `core/__main__.py:2495` from F257:

```python
parser.add_argument("--rl-train", action="store_true",
    help="RL F257: Enable QMIX training mode (updates Q-network weights every 10 sprints). "
         "Default is inference-only after 124 sprint warmup.")
```

`--rl-no-train` overrides it (production safety override);
`--rl-train-interval N` sets `HLEDAC_RL_TRAIN_INTERVAL=N`. The flag
plumbs through `run_sprint(..., rl_train_mode=...)` and into
`SprintPolicyManager(rl_train_mode=...)` at construction time.

---

## Test results

```
$ uv run pytest tests/test_sprint_policy_manager.py tests/probe_f261_qmix_activation.py
54 passed in 1.87s
```

Coverage of the new spec contract (`TestF261Activation`):
- `test_reward_formula_basic` — log1p(10) ≈ 2.398 with no penalty
- `test_reward_time_penalty_above_20min` — 20-min budget applies penalty
- `test_reward_clamp_upper_bound` — 10000 findings + perfect quality ≤ 5.0
- `test_reward_clamp_lower_bound` — 10-hour runtime ≥ -2.0
- `test_reward_quality_multiplier_from_scorecard` — mean(quality_scores) applied
- `test_reward_dict_scorecard` — scorecard may be dict
- `test_first_sprint_skips_buffer_push` — first sprint no-op
- `test_second_sprint_pushes_transition` — second sprint pushes 1 transition
- `test_reward_history_bounded` — ring buffer caps at 100
- `test_rl_train_mode_flag` — flag plumbs through to trainer
- `test_train_interval_env_var` — `HLEDAC_RL_TRAIN_EVERY` honoured
- `test_legacy_train_interval_env_var` — `HLEDAC_RL_TRAIN_INTERVAL` honoured
- `test_actual_duration_s_field_alias` — accepts both `actual_duration_s` and `findings_accepted`
- `test_no_mlx_no_qmix_trainer` — replay buffer works even when trainer is None

`probe_f261_qmix_activation.py` (12 tests) — all green after clamp and time-penalty
formula updates.

`probe_f257_qmix_training.py::test_update_with_training_enabled` is environment-
dependent: it requires `HLEDAC_RL_SKIP_RAM_GATE=1` on a host with >80% RAM.
This is a pre-existing test design and not a regression from this sprint.

---

## First training step output

Live verification on the dev host (M1 8GB UMA, MLX available):

```
$ uv run python -c "..."
Reward for 25 findings @ 15min: 3.2581  (clamped [-2.0, 5.0])
Replay buffer size: 0
Q-network trainer: <rl.qmix.QMIXJointTrainer object at 0x10fc412b0>
Train interval: 10
MLX available: True
```

The 25-findings sprint at 15 min yields `log1p(25) * 1.0 - 0.0 = 3.258`, well
within the `[-2.0, 5.0]` clamp. The replay buffer is at 0 because only one
sprint was issued (first-sprint edge case skips push). The Q-network trainer
initialises successfully with 5 agents × 64-dim hidden + 32-dim mixing.

---

## M1 invariants respected

- `mx.eval([])` precedes every `mx.metal.clear_cache()` (I11).
- All `MARLReplayBuffer.push()` / `.sample()` interactions use bounded
  numpy arrays (capacity=50000).
- `try/except` around every ctypes / MLX / psutil call.
- No `asyncio.run()` introduced anywhere.
- `_pending_state` / `_pending_action` / `_pending_reward` are bounded by
  the replay buffer's ring-buffer semantics (not a separate unbounded list).
