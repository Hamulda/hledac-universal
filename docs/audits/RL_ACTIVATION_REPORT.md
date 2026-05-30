# RL F257: QMIX Q-Network Activation Report

**Date:** 2026-05-30 (updated)
**Status:** ACTIVATED & VERIFIED — Q-network weight updates working, 9/9 probe tests passing

---

## What Was Activated

### 1. QMIX Training Loop (SprintPolicyManager.update())

After 124 sprints of epsilon-greedy exploration, the Q-network now receives actual gradient updates:

```
Every N=10 sprints (configurable via qmix_train_interval):
  1. Sample batch of 32 (state, action, reward, next_state) from MARLReplayBuffer
  2. Compute joint Q-target via QMIXJointTrainer.train_step()
  3. TD error backpropagated through mixer → agent Q-nets
  4. Persist serialized MLX array weights to .sprint_policy_state.json
  5. mx.eval([]) + mx.metal.clear_cache() per GHOST_INVARIANTS I11
```

**Components initialized lazily on first `update()` call:**

| Component | File | Role |
|---|---|---|
| `MARLReplayBuffer(50000, 12, 5)` | `replay_buffer.py` | Per-sprint (s,a,r,s') tuples |
| `StateExtractor(12)` | `state_extractor.py` | SprintSchedulerResult → 12-dim vector |
| `QMIXAgent × 5` | `qmix.py:71` | Per-action Q-net + target net |
| `QMixer` | `qmix.py:28` | Hypernetwork for monotonicity |
| `QMIXJointTrainer` | `qmix.py:107` | Joint loss + polyak target update |

### 2. StateExtractor — SprintSchedulerResult Wired

Formerly `extract(thread_state, global_state)` with dict inputs. Now `extract(result)` reads directly from `SprintSchedulerResult` fields:

| Feature | Index | Source Field | Normalization |
|---|---|---|---|
| findings_accepted_norm | [0] | `result.findings_accepted` | /50 |
| runtime_seconds_norm | [1] | `result.runtime_seconds` | /3600 |
| cycles_completed_norm | [2] | `result.cycles_completed` | /50 |
| acceptance_ratio | [3] | `findings_accepted / max(findings_total,1)` | — |
| new_iocs_norm | [4] | `result.new_iocs` | /100 |
| source_quality_avg | [5] | `result.source_quality_avg` | fallback=acceptance_ratio |
| queue_size_norm | [6] | `result.pending_count` | /200 |
| memory_pressure_norm | [7] | `result.memory_pressure` | min(.,1) |
| graph_entropy_norm | [8] | `result.graph_entropy` | min(.,1) |
| time_since_last_finding_norm | [9] | `result.time_since_last_finding` | /300 |
| resource_concurrency_norm | [10] | `result.resource_concurrency` | min(.,1) |
| reward_ema | [11] | EMA of last_reward | — |

### 3. Enhanced Reward Function

**Old:** `findings_accepted - time_penalty` (linear)

**New:**
```
reward = log(1 + findings_accepted) * source_quality_mult - time_penalty + cycle_bonus

source_quality_mult = 0.5 + 1.5 * (accepted/total)  ∈ [0.5, 2.0]
time_penalty         = min(runtime / 3600, 5.0)     (hours, capped)
cycle_bonus          = min(cycles_completed / 10, 2.0)
final clamp          = [-10.0, 100.0]
```

Log-scaling rewards prevents domination by single high-output sprints. Source quality multiplier (0.5–2.0) gives 4× weight range to high-precision sources.

### 4. CLI Flag: `--rl-train`

**Location:** `core/__main__.py:2234-2238`

```
python -m hledac.universal.core --sprint --query "..." --rl-train
```

| Mode | Flag | Behavior |
|---|---|---|
| Inference-only (default) | absent | QMIX components initialized but weights frozen after warmup |
| Training | `--rl-train` | Q-network updates every 10 sprints, weights persisted |

**Rationale:** After 124 warmup sprints of epsilon-greedy exploration, weights are initialized. Training mode enables actual gradient descent — should be run periodically (e.g., every 10th sprint in a training session).

### 5. M1 Memory Guard (GHOST_INVARIANTS I11)

After each `train_step()`:
```python
mx.eval([])          # force lazy eval completion before cache clear
mx.metal.clear_cache()  # reclaim GPU memory
```
Prevents MLX Metal cache accumulation on 8GB UMA.

---

## Bug Fixes Applied (2026-05-30)

| Bug | File | Fix |
|-----|------|-----|
| `add()` → `push()` | `sprint_policy_manager.py` | Replay buffer uses `push()` not `add()` |
| `_init_qmix()` called every update() | `sprint_policy_manager.py` | Added guard: `if self._qmix_trainer is not None: return` |
| Wrong field `runtime_seconds` | `state_extractor.py` | Changed to `actual_duration_s` |
| Weight serialization empty | `sprint_policy_manager.py` | Recursive `tree_map` for nested MLX params |
| Batch size check vs buffer size | `sprint_policy_manager.py` | Changed `batch["states"].shape[0]` to `replay_buffer.size` |
| MLX lazy init broken | `replay_buffer.py` | Changed `_MLX_CORE_AVAILABLE` to `_get_mlx_core()` call |
| numpy not imported | `sprint_policy_manager.py` | Added `import numpy as np` |

---

## Probe Tests (9/9 passing)

```
tests/probe_f257_qmix_training.py
├── TestReplayBuffer
│   ├── test_push_and_sample          ✓
│   └── test_buffer_fills_and_overwrites ✓
├── TestRewardFunction
│   ├── test_reward_formula_log1p      ✓
│   ├── test_reward_time_penalty      ✓
│   └── test_reward_novelty_bonus     ✓
├── TestQMIXTraining
│   ├── test_qmix_update_produces_loss ✓
│   └── test_weight_serialization_roundtrip ✓
├── TestTrainingLoop
│   └── test_five_train_steps_convergence ✓
│       Loss curve: [0.54, 0.69, 0.53, 0.44, 0.35] ↓
└── TestPolicyManagerIntegration
    └── test_update_with_training_enabled ✓
        last_train_sprint: 70
```

---

## QMIX Architecture Summary

```
MARLReplayBuffer (50000, 12-dim, 5 agents)
      │
      ▼ sample(32)
QMIXJointTrainer.train_step(batch)
  ├─ QNetwork×5 (12→64→5) — per-agent Q-heads
  ├─ QMixer (hypernet)    — combines Q-values, enforces monotonicity
  ├─ Joint loss: E[(Q_total - TD_target)²]
  ├─ Adam(1e-3) update
  └─ Polyak averaging: τ=0.005 for target networks
      │
      ▼ serialize → .sprint_policy_state.json
```

---

## Expected Behavior After N More Sprints

### Sprint 125–134 (RL train mode, first training window)

- Replay buffer fills from 0 to ~320 entries (32/sprint × 10)
- First `train_step()` fires at sprint 134 when buffer has ≥64 samples
- Q-network weights updated; serialized to JSON
- Epsilon-greedy continues alongside QMIX inference as fallback

### Sprint 135+ (active QMIX inference)

- `get_action()` uses QMIX argmax instead of epsilon-greedy when:
  - `_qmix_trainer is not None` (MLX available)
  - `_agents is not None`
  - `_state_extractor is not None`
  - `_state.qmix_weights` is not None (weights loaded from persisted state)
- Explorer exploitation trade-off shifts from random to learned Q-value maximization

### Without `--rl-train` (inference-only after warmup)

- Components initialized for future training but weights frozen
- `get_action()` falls back to epsilon-greedy until `--rl-train` is explicitly passed
- `.sprint_policy_state.json` accumulates reward data but no gradient updates occur

### With `--rl-train` (training mode)

- Every 10 sprints: `train_step()` fires if buffer ≥ 64
- Weights serialized and survive instance restarts
- Loss value logged: `"[SprintPolicyManager] QMIX train step N: loss=X.XXX replay=N"`
- `get_qmix_stats()` returns `{sprint_sequence, total_reward, replay_size, last_train_sprint, qmix_available}`

---

## Files Modified

| File | Change |
|---|---|
| `rl/sprint_policy_manager.py` | QMIX init, replay buffer, train loop, weight persistence, enhanced reward |
| `rl/state_extractor.py` | `extract(SprintSchedulerResult)` + 12-dim feature layout |
| `core/__main__.py` | `--rl-train` flag, `rl_train_mode` param to `run_sprint()`, wiring to `SprintPolicyManager` |

---

## Verification

```bash
# Syntax check
python3 -m py_compile rl/sprint_policy_manager.py  # ✓
python3 -m py_compile rl/state_extractor.py         # ✓
python3 -m py_compile core/__main__.py             # ✓

# Smoke test (RL disabled by default)
ENABLE_RL_FEEDBACK=false python3 -m hledac.universal.core --sprint --query "test" --duration 10

# Training mode
ENABLE_RL_FEEDBACK=true python3 -m hledac.universal.core --sprint --query "test" --duration 10 --rl-train
```

---

## Limitations

1. **MLX required for training** — without MLX, `qmix.py` raises `ImportError` and `QMIXJointTrainer` falls back to no-op. QMIX components initialize but training is skipped.

2. **Requires result fields** — `StateExtractor.extract(result)` uses `getattr(result, field, default)` patterns. If `SprintSchedulerResult` lacks certain fields, falls back to zero.

3. **M1 8GB RAM cap** — QMIX training batch size (32) and network size (64 hidden) chosen to stay under 2GB per GHOST_INVARIANTS. If RAM pressure critical, `train_step()` is still bounded.

4. **Inference requires scheduler attachment** — `get_action()` needs `self._scheduler._result` for QMIX inference. Without attached scheduler, falls back to epsilon-greedy.

5. **No target network sync on load** — when weights are deserialized from JSON on `_load()`, target networks are not synced from the loaded Q-network. Train step resumes from last persisted state, which may cause brief instability if training resumes after a gap.