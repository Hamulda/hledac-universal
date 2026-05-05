# Sprint F214X: Execution Optimizer Strategy Correctness Fix

## Summary

Fixed 4 premature `return` bugs in `utils/execution_optimizer.py` that caused `result_count < input_count` across 3 execution strategies.

---

## Bugs Fixed

### Bug 1: `_execute_round_robin` — `execute_chunk()` inner function
**Location:** `utils/execution_optimizer.py:431`
**Symptom:** `return results` inside `for task in chunk` loop caused early exit after first task.
**Fix:** Moved `return results` to after the loop completes.
```python
# BEFORE (buggy)
async def execute_chunk(chunk):
    results = []
    for task in chunk:
        ...
        results.append(result)
        return results  # ← BUG: exits after first task!

# AFTER (fixed)
async def execute_chunk(chunk):
    results = []
    for task in chunk:
        ...
        results.append(result)
    return results  # ← FIXED: returns after all tasks in chunk
```

### Bug 2: `_distribute_tasks_load_balanced` — `return` inside loop
**Location:** `utils/execution_optimizer.py:592` (was inside `for i, task in enumerate(tasks)`)
**Symptom:** `return distribution` indented inside loop → returned after first task assigned.
**Fix:** Dedented `return distribution` to after the loop.
```python
# BEFORE (buggy)
for i, task in enumerate(tasks):
    worker_id = sorted_workers[i % len(sorted_workers)][0]
    distribution[worker_id].append(task)
    return distribution  # ← BUG: exits after first task!

# AFTER (fixed)
for i, task in enumerate(tasks):
    worker_id = sorted_workers[i % len(sorted_workers)][0]
    distribution[worker_id].append(task)
return distribution  # ← FIXED: returns after all tasks distributed
```

### Bug 3: `execute_worker_tasks()` inner function in `_execute_load_balanced`
**Location:** `utils/execution_optimizer.py:463`
**Symptom:** `return results` inside `for task in worker_tasks` loop caused early exit after first task.
**Fix:** Moved `return results` to after the loop completes.
```python
# BEFORE (buggy)
async def execute_worker_tasks(worker_id, worker_tasks):
    results = []
    for task in worker_tasks:
        ...
        results.append(None)
        return results  # ← BUG: exits after first task!

# AFTER (fixed)
async def execute_worker_tasks(worker_id, worker_tasks):
    results = []
    for task in worker_tasks:
        ...
        results.append(None)
    return results  # ← FIXED: returns after all worker tasks complete
```

### Bug 4: `_execute_adaptive` — `return results` inside `while` loop
**Location:** `utils/execution_optimizer.py:561`
**Symptom:** `return results` indented inside `while task_index < len(tasks)` loop → returned after first batch, leaving remaining tasks unprocessed.
**Fix:** Dedented `return results` to after the loop completes.
```python
# BEFORE (buggy)
while task_index < len(tasks):
    batch_size = min(current_workers * 2, len(tasks) - task_index)
    batch = tasks[task_index:task_index + batch_size]
    ...
    task_index += batch_size
    return results  # ← BUG: exits after first batch!

# AFTER (fixed)
while task_index < len(tasks):
    batch_size = min(current_workers * 2, len(tasks) - task_index)
    batch = tasks[task_index:task_index + batch_size]
    ...
    task_index += batch_size
return results  # ← FIXED: returns after all batches processed
```

---

## Validation

```
tests/probe_f214x_execution_optimizer_correctness/test_execution_optimizer_strategies.py: 13 passed, 1 warning
```

### Test Coverage

| Test | Strategy | Input | Expected | Status |
|------|----------|-------|----------|--------|
| `test_round_robin_5_tasks_returns_5_results` | round_robin | 5 tasks | 5 results | PASS |
| `test_round_robin_result_count_equals_input_count` | round_robin | N tasks | N results | PASS |
| `test_round_robin_preserves_order` | round_robin | 5 tasks | ordered [0-4] | PASS |
| `test_round_robin_async_tasks` | round_robin | 5 async tasks | 5 results | PASS |
| `test_load_balanced_8_tasks_returns_8_results` | load_balanced | 8 tasks | 8 results | PASS |
| `test_load_balanced_result_count_equals_input_count` | load_balanced | N tasks | N results | PASS |
| `test_load_balanced_exceptions_return_none` | load_balanced | 3 tasks (1 exc) | 3 results | PASS |
| `test_adaptive_9_tasks_returns_9_results` | adaptive | 9 tasks | 9 results | PASS |
| `test_adaptive_result_count_equals_input_count` | adaptive | N tasks | N results | PASS |
| `test_adaptive_async_and_sync_mixed` | adaptive | 5 mixed | 5 results | PASS |
| `test_round_robin_no_short_circuit` | round_robin | N tasks | >=N results | PASS |
| `test_load_balanced_no_short_circuit` | load_balanced | N tasks | >=N results | PASS |
| `test_adaptive_no_short_circuit` | adaptive | N tasks | >=N results | PASS |

---

## Constraints Maintained

- **No broad refactor** — only 4 targeted dedent fixes
- **Public API unchanged** — `execute_parallel()` signature intact
- **Default behavior preserved** — `execute_parallel` unchanged
- **No max_pending introduced**
- **No new dependencies**
- **Exception semantics preserved** — all strategies use `return_exceptions=True` via `asyncio.gather`
- **Result order preserved** — all strategies maintain input order in output

---

## Files Modified

| File | Changes |
|------|---------|
| `utils/execution_optimizer.py` | 4 bug fixes (dedent premature returns) |
| `tests/probe_f214x_execution_optimizer_correctness/test_execution_optimizer_strategies.py` | 13 new probe tests |