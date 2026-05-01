# F206AQ Hermetic Regression Baseline — Report

**Sprint:** F206AQ
**Date:** 2026-05-01
**Scope:** Broad hermetic regression baseline plan and runner for F206 stabilization lanes
**Method:** Manifest-only probe + static validation (no live execution, no MLX load, no network)
**Status:** READY — F206AO must complete before full regression can run

---

## 1. Manifest Structure

### hermetic_regression_manifest.json

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Sprint | F206AQ |
| Status | READY |
| Blocked by | None (hermetic baseline) |
| Precondition | F206AO must complete before MLX live runs |

### Lane Coverage

| Lane | Name | Status | Tests Collected | Tests Passed | Tests Failed |
|---|---|---|---|---|---|
| F206AE | PQ Export Hardening | PASS | 17 | 17 | 0 |
| F206AF | PQ Helper Path | PREEXISTING_BUG | 14 | 13 | 1 |
| F206AG | Qoder Reality | PASS | 57 | 57 | 0 |
| F206AH | Runtime Authority A/B | PASS | 37 | 37 | 0 |
| F206AI | Graph Authority | PASS | 16 | 16 | 0 |
| F206AJ | M1 Memory Authority Census | PASS | 39 | 39 | 0 |
| F206AL | M1 Memory Harmonization | PASS | 12 | 12 | 0 |
| **F206AO** | **MLX Wired Limit Canonical** | **PENDING** | **—** | **—** | **—** |
| **TOTAL** | **8 lanes** | | **192** | **191** | **1** |

### F206AF Pre-Existing Bug

**Test:** `TestImportTimeSafety::test_import_does_not_spawn_subprocess`
**Classification:** Test design conflates object initialization with module import. Production code correctly does NOT spawn subprocess at import time. `_run_helper_sync()` is only called by `is_available()` — a lazy status check, not import-triggered.
**Impact on baseline:** 1 known failure, not a production regression. Baseline status remains READY.

---

## 2. Baseline Profiles

### Profile: f206aq-hermetic (RUNNABLE NOW)

**Lanes:** F206AE, F206AF, F206AG, F206AH, F206AI, F206AJ, F206AL (7 lanes)
**Command:** `rtk proxy python -m pytest -q tests/probe_f206ae_pq_export_hardening tests/probe_f206af_pq_helper_path tests/probe_f206ag_qoder_reality tests/probe_f206ah_runtime_authority tests/probe_f206ai_graph_authority tests/probe_f206aj_m1_memory_authority tests/probe_f206al_m1_memory_harmonization`
**Verification command:** `rtk proxy python -m pytest -q tests/probe_f206aq_hermetic_regression`
**Can run now:** YES
**Notes:** All hermetic — static analysis only, no live imports, no MLX, no network.

### Profile: f206aq-full (BLOCKED)

**Lanes:** All 8 lanes including F206AO
**Blocked reason:** F206AO (MLX wired limit canonical) must complete first — resolves C1 (2.5GB vs 2.684GB inconsistency) and C4 (EMERGENCY vs CRITICAL threshold inversion)
**Can run now:** NO

---

## 3. Abort Conditions

All CLEAR for hermetic execution:

| Abort Condition | Enforced |
|---|---|
| Any production code edit | YES — manifest forbids |
| Any live sprint execution | YES — no run_sprint lane |
| Any live network request | YES — no network lane |
| Any MLX model load | YES — no MLX lane |
| Any edit to run_baseline.py | YES — read-only constraint |

---

## 4. Excluded from Hermetic Baseline

| Exclusion | Reason |
|---|---|
| smoke_runner | Pre-existing failure: `smoke_fetch_semaphore`, `smoke_adaptive_semaphore`, `smoke_semaphore_limit` — documented in F206J. Not in scope. |
| MLX model load | Blocked until F206AO resolves C1 (wired limit canonical) |
| live_sprint | Hermetic baseline only validates static correctness |
| network/reachability checks | No live network in hermetic mode |

---

## 5. F206AO Blocking Chain

```
F206AQ hermetic baseline
  └── f206aq-hermetic profile → RUNNABLE NOW (7 lanes, 191/192 passing)
  └── f206aq-full profile → BLOCKED until F206AO completes
        └── F206AO: MLX wired limit canonical + threshold harmonization
              ├── C1: MLX wired limit 2.5GB (__main__.py) vs 2.684GB (mlx_cache.py)
              └── C4: EMERGENCY_RAM_GB=6.2 < CRITICAL=6.5 (threshold inversion)
        └── After F206AO: full regression can run with live MLX
```

---

## 6. Test Results

### Probe Tests (F206AQ own lane)

```
27 passed in 0.57s
```

Tests validate:
- INV-1: manifest.json exists and is valid JSON
- INV-2: all stabilization lanes present (F206AE/AF/AG/AH/AI/AJ/AL)
- INV-3: F206AO is marked PENDING/blocked
- INV-4: no live network lane defined
- INV-5: no live sprint execution lane defined
- INV-6: no MLX model load lane defined
- INV-7: verification_command is reproducible
- INV-8: baseline status is READY
- INV-9: abort conditions cover all forbidden actions
- INV-10: F206AF failure is classified as pre-existing test bug
- INV-11-16: summary integrity checks (lane sums, status flags)

---

## 7. Reproducibility

### Verification (quick check)
```bash
rtk proxy python -m pytest -q tests/probe_f206aq_hermetic_regression
```
Result: 27 passed in 0.57s

### Full hermetic regression (7 lanes, no live execution)
```bash
rtk proxy python -m pytest -q \
  tests/probe_f206ae_pq_export_hardening \
  tests/probe_f206af_pq_helper_path \
  tests/probe_f206ag_qoder_reality \
  tests/probe_f206ah_runtime_authority \
  tests/probe_f206ai_graph_authority \
  tests/probe_f206aj_m1_memory_authority \
  tests/probe_f206al_m1_memory_harmonization
```

### Full regression with F206AO (only after F206AO completes)
```bash
# After F206AO completes, add F206AO lane to above command
# MLX wired limit canonical + threshold harmonization must pass first
```

---

## 8. Success Definition

| Criterion | Status |
|---|---|
| Hermetic regression manifest exists | ✅ `hermetic_regression_manifest.json` |
| Commands are reproducible | ✅ verification + full commands documented |
| Status is clear | ✅ READY — blocked lanes marked PENDING |
| F206AO clearly blocked | ✅ F206AO PENDING, f206aq-full profile blocked |
| F206AF failure classified | ✅ PREEXISTING_BUG, not production regression |
| No live network lane | ✅ confirmed |
| No live sprint lane | ✅ confirmed |
| No MLX model load lane | ✅ confirmed |
| Abort conditions documented | ✅ 5 conditions covering all forbidden actions |
| Probe tests pass | ✅ 27/27 passed in 0.57s |

---

## 9. Next Steps

| Step | Action | Blocked by |
|---|---|---|
| F206AO | Resolve C1 (MLX wired limit canonical) + C4 (threshold inversion) | NOT BLOCKED — independent sprint |
| f206aq-full profile | Add F206AO lane after F206AO completes | Waiting on F206AO |
| Full regression | Run all 8 lanes with live MLX (after F206AO) | Waiting on F206AO |

---

## Summary

| Metric | Value |
|---|---|
| Manifest created | ✅ `probe_f206aq_hermetic_regression/hermetic_regression_manifest.json` |
| Probe tests | 27 passed, 0 failed |
| Hermetic baseline | READY — f206aq-hermetic can run now |
| Full regression | BLOCKED — F206AO must complete first |
| F206AO status | PENDING — not yet written |
| Abort conditions | All CLEAR |
| Pre-existing bugs | 1 (F206AF test design issue, documented) |