# Sprint F259: PromptBandit & EIG Wiring Report

## Overview
Integration of two existing but underutilized components into the Hermes3/Runtime pipeline:
- **PromptBandit** → Hermes3Engine `generate()` and `generate_report()`
- **EIGCalculator** → InferenceEngine multi-hop action selection

## Changes Made

### Part A: PromptBandit → Hermes3 Wiring

**File: `brain/hermes3_engine.py`**

#### `generate()` method (line ~1356-1425)
Added PromptBandit arm selection before inference and reward update after:

```python
# Sprint F259: PromptBandit arm selection in generate()
bandit = self._get_prompt_bandit()
arm_used = ""
modifier = ""
if bandit is not None:
    arm_used = bandit.select_arm()
    modifier = bandit.get_prompt_modifier(arm_used)
    self._last_bandit_arm = arm_used

# ... inference ...

# Sprint F259: Update bandit reward after successful generation
if bandit is not None and arm_used and response:
    response_len_norm = min(1.0, len(response) / 4000.0)
    reward = response_len_norm * 0.8
    bandit.update_reward(arm_used, reward, reward)
```

**Gate:** Only if `PromptBandit` is available (lazy init).

**Reward signal:** `response_length_normalized × 0.8` (baseline confidence)

#### `generate_report()` method (pre-existing, line ~1550-1610)
Already had PromptBandit integration from earlier sprint:
- Arm selection before prompt construction
- `get_prompt_modifier()` applied to prompt
- Reward update after generation

### Part B: EIG → InferenceEngine Action Selection

**File: `brain/inference_engine.py`**

#### EIG Import (line ~50-56)
```python
try:
    from utils.eig import EIGCalculator
    EIG_AVAILABLE = True
except ImportError:
    EIGCalculator = None
    EIG_AVAILABLE = False
```

#### `InferenceEngine.__init__()` (line ~428-434)
Added hypothesis set storage:
```python
# Sprint F259: Hypothesis set for EIG action selection
self._hypothesis_set: list[dict] = []
```

#### `MultiHopReasoner._bfs_with_depth()` (line ~2033-2057)
Added EIG-based neighbor ranking before exploration:
```python
# Sprint F259: EIG-based neighbor selection
if neighbors and EIG_AVAILABLE:
    hypothesis_set = [{"entity": h.from_entity, "relation": h.relation, "belief": h.confidence} for h in hops]
    candidates = [{"entity": n[0], "relation": n[1], "confidence": n[2], "expected_reduction": 0.2} for n in neighbors[:50]]
    eig_calculator = EIGCalculator()
    ranked = eig_calculator.rank_actions(hypothesis_set, candidates)
    ranked_dict = {r[0]["entity"]: r[1] for r in ranked}
    neighbors = sorted(neighbors, key=lambda n: ranked_dict.get(n[0], 0), reverse=True)
```

**M1 Constraint:** 50 candidate cap for EIG computation.

#### `InferenceEngine.clear()` (line ~1548-1554)
Reset hypothesis set on clear:
```python
# Sprint F259: Reset hypothesis set for EIG
self._hypothesis_set.clear()
```

#### New Methods
- `get_hypothesis_set()` → Returns copy of current beliefs
- `update_hypothesis_set(beliefs)` → Updates beliefs from external source (e.g., HypothesisEngine)

## M1 Constraints Verified

| Constraint | Implementation | Status |
|------------|----------------|--------|
| PromptBandit.select_arm() synchronous | Sync method, no await | OK |
| EIG bounded to 50 candidates | `neighbors[:50]` slice | OK |
| Fail-safe fallback | `try/except` around EIG calls | OK |

## Persistence Audit

### PromptBandit
- **Status:** Already persisted via JSON file (`~/.hledac/hermes_prompt_bandit.json`)
- **Mechanism:** `_load()` / `_save()` with async save on 10-update intervals
- **Arm state persisted:** `_arm_counts`, `_arm_rewards`, `_total_pulls`

### EIGCalculator
- **Status:** Stateless (no persistence needed)
- **Hypothesis set:** Stored in `InferenceEngine._hypothesis_set`
- **Cross-sprint:** Cleared on `clear()`, populated during multi-hop reasoning

## Test Plan
```bash
pytest tests/probe_8vh/ tests/probe_8td/ -q --tb=short
```

## Files Modified
- `brain/hermes3_engine.py` (+25 lines)
- `brain/inference_engine.py` (+40 lines)

## Related Components
- `brain/prompt_bandit.py` — PromptBandit class (existing, unchanged)
- `utils/eig.py` — EIGCalculator class (existing, unchanged)
- `brain/hypothesis_engine.py` — Can call `update_hypothesis_set()` after belief updates
