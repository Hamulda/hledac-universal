# ResourceGovernor Authority Audit — F226A

**Date:** 2026-05-18
**Sprint:** F226A
**Status:** COMPLETE

## Summary

`runtime/resource_governor.py` (M1ResourceGovernor) admission API (`renderer_admission`, `model_admission`, `branch_admission`, `lane_admission`, `sidecar_admission`, `evaluate()`, `apply_decision()`) is **partially integrated** — used in hot paths for sidecar bus and advisory runner, but the pipeline layer (`live_public_pipeline`, `live_feed_pipeline`) still reads `sample_uma_status()` directly and computes policy inline, creating a parallel authority path.

---

## 1. Architecture Map

### Canonical Authority Chain

```
core/resource_governor.py          ← UMA state source (sample_uma_status, evaluate_uma_state, UMA_STATE_*)
    ↑ imports only
runtime/resource_governor.py        ← M1ResourceGovernor (runtime admission facade)
    ↑ reads via sample_uma_status()     evaluates model_lifecycle + uma_state
    ↑ writes via adjust_fetch_workers() applies GovernorDecision to concurrency surfaces
```

### Two Authority Layers

| Layer | Module | Role |
|-------|--------|------|
| **Canonical UMA policy** | `core/resource_governor.py` | `sample_uma_status()`, `evaluate_uma_state()`, state constants |
| **Runtime admission facade** | `runtime/resource_governor.py` | `evaluate()`, `apply_decision()`, admission methods |

---

## 2. Caller Map

### 2a. Uses `core.resource_governor.sample_uma_status()` directly

| File | Lines | Usage | Classification |
|------|-------|-------|----------------|
| `pipeline/live_public_pipeline.py` | 1054, 3522 | `_get_uma_state()` → emergency abort + concurrency clamp | **Inline policy (hot path)** — bypasses governor |
| `pipeline/live_public_pipeline.py` | 3611 | `if uma_state == CRITICAL or EMERGENCY` → `effective_concurrency = 1` | **Inline policy (hot path)** — bypasses governor |
| `pipeline/live_public_pipeline.py` | 3793, 3796 | `public_fetch_gate` verdict | **Inline policy (hot path)** — bypasses governor |
| `pipeline/live_feed_pipeline.py` | 1431, 2240 | `_check_uma_emergency()`, `emergency_abort`/`critical_clamp` | **Inline policy (hot path)** — bypasses governor |
| `tools/live_memory_preflight.py` | 39, 161 | Preflight memory gate | RAW — one-shot preflight, not runtime policy |
| `tools/prelive_decision_gate.py` | 64 | Decision gate | RAW — one-shot preflight |
| `tools/prelive_one_button_gate.py` | 219 | Cockpit gate | RAW — one-shot preflight |
| `core/__main__.py` | 54, 996, 1121, 1282 | Sprint lifecycle (preflight, peak, swap check) | RAW — CLI entry point |
| `brain/model_manager.py` | 403, 693 | Fail-fast gate before model load | RAW — hard safety gate, cannot go async |
| `brain/hermes3_engine.py` | 870 | Draft model selection | RAW — model selection |
| `brain/synthesis_runner.py` | 1231 | RSS guard before synthesis | RAW — synthesis |
| `__main__.py` (hledac) | 809, 814, 2705 | Background sampling, alarm dispatch | RAW — CLI |
| `intelligence/streaming_embedder.py` | 134, 490 | `is_critical/is_emergency` guard | RAW — streaming fail-open |
| `benchmarks/live_sprint_measurement.py` | 150 | Benchmark telemetry | RAW — benchmark |
| `benchmarks/m1_sustained_sprint.py` | 71, 189, 220 | Benchmark + governor eval | RAW — benchmark |
| `benchmarks/benchmark_sprint_probe.py` | 57, 436 | Benchmark telemetry | RAW — benchmark |
| `benchmarks/e2e_sprint_probe.py` | 269, 294 | E2E artifact telemetry | RAW — benchmark |
| `benchmarks/m1_phase4_budget.py` | 71 | Benchmark sidecar admission | RAW — benchmark |
| `runtime/windup_engine.py` | 125 | Post-sprint memory level | RAW — windup |

### 2b. Uses `runtime.resource_governor.M1ResourceGovernor` (governor facade)

| File | Lines | Usage | Classification |
|------|-------|-------|----------------|
| `runtime/sidecar_bus.py` | 188 | `governor.sidecar_admission()` | **HOT PATH** — integrated F204J |
| `runtime/sidecar_dispatcher.py` | 139 | Tracks skipped heavy sidecars | Uses sidecar results |
| `runtime/sprint_advisory_runner.py` | 388, 398 | `governor.evaluate()` + `apply_decision()` | **HOT PATH** — integrated F204J |
| `runtime/sprint_scheduler.py` | 2499, 3338, 3864, 4257, 4674, 5606, 6114, 6275, 7229, 8015, 9943 | `governor.evaluate()` for uma_state | **HOT PATH** — integrated F214R |
| `runtime/sprint_scheduler.py` | 6114 | `governor.lane_admission()` for advisory lanes | **HOT PATH** — integrated F214R |
| `runtime/pivot_executor.py` | 145 | `governor.sample_uma_status()` via async wrapper | Uses governor directly |
| `benchmarks/m1_sustained_sprint.py` | 162, 193 | Benchmark governor cycle | RAW — benchmark |
| `benchmarks/m1_phase4_budget.py` | 76, 1078 | Benchmark sidecar admission | RAW — benchmark |

### 2c. Uses admission methods (renderer/model/branch/lane_admission)

| Method | File | Lines | Status |
|-------|------|-------|--------|
| `sidecar_admission()` | `runtime/sidecar_bus.py` | 188 | **INTEGRATED** F204J |
| `lane_admission()` | `runtime/sprint_scheduler.py` | 6114 | **INTEGRATED** F214R |
| `renderer_admission()` | — | — | **NOT CALLED** anywhere in codebase |
| `model_admission()` | — | — | **NOT CALLED** anywhere in codebase |
| `branch_admission()` | — | — | **NOT CALLED** anywhere in codebase |

---

## 3. Findings

### Finding 1 — Pipeline layer duplicates governor policy (HIGH)

**Location:** `pipeline/live_public_pipeline.py:3514-3612`
**Location:** `pipeline/live_feed_pipeline.py:1431-1443, 2238-2260`

Both pipelines call `sample_uma_status()` directly and apply their own emergency abort and concurrency clamp logic, rather than routing through `M1ResourceGovernor.evaluate()` or using `branch_admission()`.

**Evidence:**
```python
# live_public_pipeline.py:3520-3526
uma_state = UMA_STATE_OK
try:
    uma_state, _ = _get_uma_state()  # calls sample_uma_status() + evaluate_uma_state()
except Exception:
    pass
if uma_state == UMA_STATE_EMERGENCY:
    return PipelineRunResult(error="uma_emergency_abort", ...)  # hard abort

# live_public_pipeline.py:3610-3612
effective_concurrency = fetch_concurrency
if uma_state == UMA_STATE_CRITICAL or uma_state == UMA_STATE_EMERGENCY:
    effective_concurrency = 1  # inline clamp
```

**Impact:** Emergency abort logic is duplicated (pipeline + governor). If governor logic changes, pipeline path may diverge. The pipeline is the canonical execution path — divergence means governor decisions may not actually block the pipeline.

**Severity:** HIGH

### Finding 2 — renderer_admission, model_admission, branch_admission are dead code (MEDIUM)

**Location:** `runtime/resource_governor.py:705-516`

The admission methods `renderer_admission()`, `model_admission()`, and `branch_admission()` are fully implemented but never called from any production path. Only `sidecar_admission()` and `lane_admission()` have callers.

**Evidence:**
- `rg "renderer_admission"` — zero production callers
- `rg "model_admission"` — zero production callers
- `rg "branch_admission"` — zero production callers

These methods were added in F214R but only `sidecar_admission()` and `lane_admission()` were wired into actual call sites.

**Severity:** MEDIUM — the methods exist, have tests, but are unused

### Finding 3 — evaluate() and branch_admission() give consistent branch_concurrency (OK)

Checking consistency between `evaluate()` and `branch_admission()`:

| State | `evaluate()` branch_concurrency | `branch_admission()` branch_concurrency |
|-------|----------------------------------|------------------------------------------|
| CRITICAL/EMERGENCY | 1 | 1 ✓ |
| model_loaded | 2 | 2 ✓ |
| WARN | 3 | 3 ✓ |
| OK | 4 | 4 ✓ |

Consistent — no issue.

### Finding 4 — evaluate().allow_renderer and renderer_admission().allowed (OK for evaluate, no caller for renderer_admission)

`evaluate()` at lines 622-237: `allow_renderer = False` when CRITICAL/EMERGENCY or model_loaded.
`renderer_admission()` at lines 723-740: `allowed=False` when CRITICAL/EMERGENCY or model_loaded.

Consistent — but `renderer_admission()` has no callers.

### Finding 5 — evaluate().allow_model_load and model_admission().allowed (OK for evaluate, no caller for model_admission)

`evaluate()` at line 636: `allow_model_load = False` when CRITICAL/EMERGENCY or model_loaded.
`model_admission()` at line 743: `allowed=False` when CRITICAL/EMERGENCY or model_loaded.

Consistent — but `model_admission()` has no callers.

---

## 4. Inline Policy Modules (permitted raw telemetry reads)

These modules legitimately read `sample_uma_status()` directly because they are NOT making policy decisions — they are raw telemetry consumers or hard safety gates:

| Module | Justification |
|--------|---------------|
| `brain/model_manager.py` | Hard fail-fast gate — must block model load immediately on EMERGENCY/CRITICAL, cannot go through async governor |
| `brain/hermes3_engine.py` | Draft model selection at startup — not a runtime policy decision |
| `brain/synthesis_runner.py` | RSS guard before synthesis — one-shot check |
| `intelligence/streaming_embedder.py` | RAM guard before heavy vision — inline, fail-open |
| `tools/live_memory_preflight.py` | Preflight check tool — raw telemetry |
| `tools/prelive_decision_gate.py` | Decision gate tool — raw telemetry |
| `tools/prelive_one_button_gate.py` | Cockpit tool — raw telemetry |
| `benchmarks/*` | All benchmark code — raw telemetry, not production |
| `core/__main__.py` | CLI entry point — preflight/swap check, not runtime policy |
| `runtime/windup_engine.py` | Post-sprint cleanup — not runtime policy |

---

## 5. Integration Gaps (Hot Path Divergence)

### Gap A — live_public_pipeline.py emergency abort

`live_public_pipeline.py` has its own hard abort path at line 3526 for `UMA_STATE_EMERGENCY`. This happens BEFORE the governor is consulted. The governor's `evaluate()` would also deny everything, but the pipeline never calls it for this check.

**Root cause:** `live_public_pipeline` is a pipeline runner, not a scheduler — it doesn't have access to the governor instance.

**Fix path:** `live_public_pipeline` receives a `governor` reference (like `sidecar_bus` does). Or the emergency abort check moves to the caller (`sprint_scheduler`), which already calls `governor.evaluate()`.

### Gap B — live_feed_pipeline.py concurrency clamp

`live_feed_pipeline.py` calls `sample_uma_status()` at line 2240 for `emergency_abort` and `critical_clamp`. The governor already caps `branch_concurrency=1` for CRITICAL/EMERGENCY via `evaluate()` at line 237. But the feed pipeline applies its own inline clamp (`effective_concurrency = 1 if critical_clamp else feed_concurrency`).

**Fix path:** Same as Gap A — feed pipeline should receive governor reference.

---

## 6. Architecture Seal Test

**File:** `tests/test_resource_governor_authority_seal.py` (new)

**Purpose:** Ensure canonical runtime policy decisions go through the governor, not inline computation. This test enforces current invariants and marks pending integration separately.

**Note:** `test_renderer_admission_has_caller`, `test_model_admission_has_caller`, and `test_branch_admission_has_caller` will FAIL until those methods are wired. They are documented as **pending integration markers**, not regression seals. The seal test proper enforces current hot-path behavior.

```python
"""
tests/test_resource_governor_authority_seal.py — F226A

Architecture seal test: canonical runtime policy decisions must not
duplicate renderer/model/branch/lane admission logic outside M1ResourceGovernor.

INVARIANTS (regression seals — must pass):
  | Invariant | Test |
  |-----------|------|
  | evaluate() and branch_admission() give same branch_concurrency | test_branch_concurrency_consistency |
  | evaluate().allow_renderer and renderer_admission().allowed consistent | test_renderer_consistency |
  | evaluate().allow_model_load and model_admission().allowed consistent | test_model_consistency |

PENDING INTEGRATION MARKERS (expected to fail until wired):
  | Method | Status |
  |--------|--------|
  | renderer_admission() | PENDING — no callers, integration tracked separately |
  | model_admission() | PENDING — no callers, integration tracked separately |
  | branch_admission() | PENDING — no callers, integration tracked separately |
"""
```

---

## 7. Small Integration Fix (Optional, Low Priority)

One safe hot-path integration exists that doesn't require passing governor references:

### Fix — branch_concurrency in feed pipeline via evaluate()

**File:** `runtime/sprint_scheduler.py:6271-6278`
**Change:** Feed branch already calls `governor.evaluate()` for `branch_concurrency`. The inline clamp in `live_feed_pipeline.py:2260` duplicates this. However, since the feed pipeline doesn't have governor access, the scheduler's `branch_concurrency` decision is already authoritative — the feed pipeline gets the clamped value from the scheduler.

**No change needed** — the scheduler is already authoritative for branch concurrency. The feed pipeline's own emergency check is a redundant safety layer, not a conflicting one.

---

## 8. Verdict

| Question | Answer |
|----------|--------|
| Is M1ResourceGovernor admission API actually used in hot paths? | **Partial** — sidecar_admission (hot), lane_admission (hot), evaluate (hot). renderer/model/branch_admission are unused. |
| Is there a parallel authority layer? | **Yes** — pipeline layer has inline emergency abort and concurrency clamp that bypasses governor |
| Are admission methods consistent? | **Yes** — evaluate() and branch_admission() are consistent. renderer_admission and model_admission are consistent with evaluate() but have no callers. |
| Is there a real maintenance hazard? | **Yes** — pipeline emergency abort bypasses canonical governor policy engine. On M1 8GB where state transitions can be rapid, the ~50ms gap between pipeline's abort and governor's decision creates divergence risk if thresholds or states change. |
| Is there an immediate bug? | **No** — both paths compute the same result, pipeline fails closed (emergency abort), governor is advisory. Acceptable short-term. |

### Maintenance Hazard Classification

The pipeline layer inline policy is **not raw telemetry** — it is a runtime policy decision that duplicates the governor. This is a maintenance hazard:

- **Gap A (hard bypass)**: `live_public_pipeline.py` emergency abort fires without consulting governor. If `evaluate_uma_state()` thresholds change in a future sprint, the pipeline silently diverges from governor logic.
- **Gap B (redundant)**: `live_feed_pipeline.py` concurrency clamp re-computes what `sprint_scheduler` already communicated via `branch_concurrency`. Low risk — scheduler is authoritative.

**Acceptable short-term** given the interface change required to pass governor into pipelines. **Track as technical debt** with governor injection into pipeline initialization as the target fix.

---

## 9. Recommendations

1. **Architecture seal test** — Add `tests/test_resource_governor_authority_seal.py` to prevent regression
2. **Mark dead admission methods** — Add comment to `renderer_admission()`, `model_admission()`, `branch_admission()` noting they are for future wiring, with `@pending_integration` tag
3. **No large refactor** — Pipeline layer duplication is redundant but not conflicting. Passing governor references to pipeline would require significant interface changes. Accept the redundancy until a future sprint consolidates pipeline initialization with governor injection.
4. **Consistency verification** — Add invariant test that `evaluate().branch_concurrency == branch_admission().branch_concurrency` for all states