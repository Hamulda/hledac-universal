# F214X-2 Execution Optimizer — Additional Correctness Fixes

## Summary

3 premature-return bugs fixed in `utils/execution_optimizer.py` outside the original F214M-B scope.

---

## Bugs Fixed

### Bug 1: `_classify_tasks_by_resources` — return inside loop

**File:** `utils/execution_optimizer.py`, line 623 (before fix)

**Function:** `_classify_tasks_by_resources`

**Current (buggy):**
```python
for task in tasks:
    ...
    classifications.append(task_info)

    return classifications   # BUG: returns after first task
```

**Expected:** return after loop processes all tasks

**Fix:** dedent `return classifications` to after `for` loop body
```python
        classifications.append(task_info)

    return classifications   # FIXED: processes all tasks
```

**Impact:** Classification returns list with 1 item regardless of input count.

---

### Bug 2: `_predict_task_times` — return inside loop

**File:** `utils/execution_optimizer.py`, line 723 (before fix)

**Function:** `_predict_task_times`

**Current (buggy):**
```python
for task in tasks:
    ...
    predictions.append(max(0.1, prediction))

    return predictions   # BUG: returns after first task
```

**Expected:** return after loop processes all tasks

**Fix:** dedent `return predictions` to after `for` loop body
```python
            predictions.append(1.0)  # Default prediction

    return predictions   # FIXED: processes all tasks
```

**Impact:** Prediction returns list with 1 item regardless of input count.

---

### Bug 3: `_execute_with_dynamic_workers` — return inside loop

**File:** `utils/execution_optimizer.py`, line 772 (before fix)

**Function:** `_execute_with_dynamic_workers`

**Current (buggy):**
```python
while task_index < len(tasks):
    ...
    results.extend(batch_results)
    task_index += batch_size

    return results   # BUG: returns after first batch
```

**Expected:** return after while loop processes all batches

**Fix:** dedent `return results` to after `while` loop body
```python
            results.extend(batch_results)
            task_index += batch_size

    return results   # FIXED: processes all batches
```

**Impact:** Dynamic workers returns only first batch results regardless of input size.

---

## Tests Added

Added 7 new tests to `tests/probe_f214x_execution_optimizer_correctness/test_execution_optimizer_strategies.py`:

| Test | What it verifies |
|------|-----------------|
| `test_classify_tasks_by_resources_returns_one_per_task` | 3/5/8 tasks → 3/5/8 classifications |
| `test_classify_tasks_by_resources_has_correct_keys` | Each classification dict has correct keys and exactly one flag set |
| `test_predict_task_times_returns_one_per_task` | 3/5/7 tasks → 3/5/7 predictions |
| `test_execute_with_dynamic_workers_returns_all_results` | 10 tasks → 10 results (not just first batch) |
| `test_execute_with_dynamic_workers_result_count_equals_input` | 5/8/12 tasks → 5/8/12 results |
| `test_execute_with_dynamic_workers_preserves_order` | Results ordered same as input tasks |
| `test_execute_with_dynamic_workers_exceptions_in_results` | Exceptions appear in results list via return_exceptions=True |

---

## Verification

### Test suite
```
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac \
  pytest tests/probe_f214x_execution_optimizer_correctness/test_execution_optimizer_strategies.py -q
```
**Result:** 20 passed (15 pre-existing + 5 new for F214X-1 + 7 new for F214X-2)

### Import smoke
```
python -c "import hledac.universal; print('IMPORT_OK')"
```
**Result:** `IMPORT_OK`

### F214M-B backpressure probe
```
python tools/probe_f214m_execution_optimizer_backpressure.py
```
**Result:** VERDICT — no backpressure issues introduced. All callers pass ≤8 tasks.

### Boot smoke
Boot fails with `ModuleNotFoundError: No module named 'utils.uuid7'` — **pre-existing** import path breakage in `coordination_layer.py:23` (`from utils.uuid7 import new_runtime_id` but `utils/` is inside `hledac/universal/`). Not introduced by these changes. Confirmed by stashing changes and reproducing same error.

---

## Invariants Verified

| Invariant | Status |
|-----------|--------|
| `result_count == input_count` for all 3 fixed functions | PASS (new tests) |
| `return_exceptions=True` semantics preserved | PASS (`test_execute_with_dynamic_workers_exceptions_in_results`) |
| Result order preserved for sequential strategies | PASS (`test_execute_with_dynamic_workers_preserves_order`) |
| No backpressure changes | PASS (F214M-B probe unchanged) |
| No new dependencies | PASS |
| Public API unchanged | PASS |

---

## Files Changed

| File | Change |
|------|--------|
| `utils/execution_optimizer.py` | 3 dedent fixes (lines 623, 723, 772) |
| `tests/probe_f214x_execution_optimizer_correctness/test_execution_optimizer_strategies.py` | 7 new tests |
