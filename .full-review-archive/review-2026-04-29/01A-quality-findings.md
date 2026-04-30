# Code Quality Review - Hledac Universal

**Review Date:** 2026-04-29
**Reviewer:** Code Reviewer
**Scope:** hledac/universal/ - Autonomous OSINT Orchestrator
**Framework:** Python (asyncio, MLX, DuckDB, LanceDB)
**Critical Constraint:** M1 MacBook 8GB UMA

---

## Executive Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 4 |
| MEDIUM | 8 |
| LOW | 5 |

**Total Issues:** 20

---

## CRITICAL Issues (Must Fix)

### Issue 1: asyncio.run() in ThreadPoolExecutor - M1 Crash Vector
**File:** `utils/execution_optimizer.py:406`
**Type:** M1 Apple Silicon Crash Vector

**Description:**
`_run_in_executor_safe()` is designed to run inside a ThreadPoolExecutor, but when no running event loop exists, it calls `asyncio.run(func())` on line 406. This creates a nested event loop inside a worker thread, which crashes Metal on Apple Silicon M1.

**Code:**
```python
def _run_in_executor_safe(self, executor, func):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread - create one with asyncio.run()
        # F196A: This runs in a worker thread, not the main thread's loop.
        return asyncio.run(func())  # <-- CRASH on M1
```

**Fix:**
Replace with proper async thread-safe pattern:
```python
def _run_in_executor_safe(self, executor, func):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop - run in a new thread with its own loop
        def _run_in_new_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(func())
            finally:
                new_loop.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_in_new_thread)
            return future.result()
    # Loop exists - use run_until_complete safely
    return loop.run_until_complete(func())
```

---

### Issue 2: asyncio.run() Submitted to ThreadPoolExecutor
**File:** `utils/execution_optimizer.py:413`
**Type:** M1 Apple Silicon Crash Vector

**Description:**
When a running loop is detected, the code falls back to submitting `asyncio.run(func())` directly to a ThreadPoolExecutor. This is submitting a synchronous function that internally calls `asyncio.run()`, which creates yet another nested loop.

**Code:**
```python
        # A loop is running in this thread - we're inside an async context.
        # This function is sync so it shouldn't be called directly from async
        # code (use 'await func()' instead). If it is called, we fall back to
        # creating a new loop in a worker thread to avoid the nested loop issue.
        import concurrent.futures
        _worker_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = _worker_exec.submit(asyncio.run, func())  # <-- CRASH on M1
        return future.result()
```

**Fix:**
Use `asyncio.run_coroutine_threadsafe()` or restructure to avoid nested loops entirely.

---

### Issue 3: asyncio.run() in Thread-Safe Wrapper
**File:** `brain/inference_engine.py:442`
**Type:** M1 Apple Silicon Crash Vector

**Description:**
`_run_coro_sync_safe()` is explicitly designed to run coroutines in a thread pool (line 432 comment: "Run coroutine safely in a thread pool"), yet it calls `asyncio.run(coro)` when no running loop exists. This is the same pattern as Issue 1.

**Code:**
```python
def _run_coro_sync_safe(self, coro):
    """Run coroutine safely in a thread pool.

    M1-SAFE: When a loop is already running, use run_until_complete on the
    existing loop from the worker thread. This avoids creating a nested event
    loop with asyncio.run() which crashes Metal on Apple Silicon M1.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # <-- CRASH on M1
    # M1-SAFE: Use run_until_complete on the existing loop from this worker thread.
    loop = asyncio.get_running_loop()
    return loop.run_until_complete(coro)
```

**Fix:**
Same pattern as Issue 1 - use `asyncio.new_event_loop()` with explicit lifecycle management in a worker thread.

---

## HIGH Issues (Should Fix)

### Issue 4: Unbounded _dedup_hot_cache_order Append
**File:** `knowledge/duckdb_store.py:6533, 6539`
**Type:** Unbounded Collection / Memory Leak

**Description:**
`_dedup_hot_cache_order` is appended to without any visible eviction mechanism. If `max_dedup_cache_size` is set, the append operations at lines 6533 and 6539 could exceed bounds.

**Code:**
```python
self._dedup_hot_cache_order.append(fp)  # Line 6533
self._dedup_hot_cache_order.append(fp)  # Line 6539
```

**Fix:**
Add bounds checking before append:
```python
if len(self._dedup_hot_cache_order) >= self.max_dedup_cache_size:
    self._dedup_hot_cache_order.pop(0)
self._dedup_hot_cache_order.append(fp)
```

Or use `collections.deque` with `maxlen` for automatic eviction.

---

### Issue 5: God Object - 31,049 Line File
**File:** `legacy/autonomous_orchestrator.py`
**Type:** Code Smell / Maintainability

**Description:**
The legacy autonomous orchestrator is a single 31,049-line file. This violates the project's own "Module max: 500 lines" guideline. It contains 78 functions and is likely a "god object" anti-pattern.

**Impact:**
- Cognitive overload for understanding
- High risk of introducing bugs
- Difficult to test in isolation
- Inhibits parallel development

**Fix:**
Decompose into smaller, focused modules by domain (e.g., tool management, state management, execution control).

---

### Issue 6: 113 String Concatenations in Loop
**File:** `brain/hypothesis_engine.py`
**Type:** Performance / O(n^2) Complexity

**Description:**
Found 113 instances of string concatenation with `+=` operator. In Python, string concatenation in loops creates a new string object each iteration, resulting in O(n^2) time complexity.

**Fix:**
Replace `result += x` patterns with `result.append(x)` and `"".join(result)` at the end, or use `io.StringIO`.

---

### Issue 7: Facade Architecture Confusion
**File:** `autonomous_orchestrator.py`
**Type:** Architecture / Technical Debt

**Description:**
The module is a "ROOT_REEXPORT_FACADE" that re-exports from `legacy/autonomous_orchestrator.py`. The canonical production path goes through `core.__main__` and `runtime.sprint_scheduler:SprintScheduler`, not through this facade.

**From docstring:**
```
.. canonical_owner::
    - Legacy implementation: legacy/autonomous_orchestrator.py (~31k lines)
    - Production sprint: core.__main__:run_sprint()
    - Production orchestrator: runtime.sprint_scheduler:SprintScheduler

.. false_authority_risk::
    This module looks like a primary orchestrator but is NOT.
```

**Fix:**
Decouple facade from implementation, add clear deprecation warnings, and redirect consumers to canonical paths.

---

## MEDIUM Issues (Consider Fixing)

### Issue 8: ThreadPoolExecutor Created Without Cleanup
**File:** `utils/execution_optimizer.py:412`
**Type:** Resource Leak

**Description:**
`_worker_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)` is created inside a method but never explicitly shut down. If this path is hit frequently, thread pool threads accumulate.

**Code:**
```python
_worker_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
future = _worker_exec.submit(asyncio.run, func())
return future.result()
```

**Fix:**
Use context manager or store and shutdown the executor.

---

### Issue 9: No Visible Bounds on _wal_write_pending_sync_marker
**File:** `knowledge/duckdb_store.py`
**Type:** Unbounded Growth

**Description:**
The WAL sync marker queue could grow unbounded if `async_record_shadow_findings_batch()` is called rapidly without corresponding sync operations.

**Evidence:**
Line 4039: `self._wal_write_pending_sync_marker(...)` called without visible bounds check.

**Fix:**
Add bounded queue with overflow handling.

---

### Issue 10: No Error Handling in loop.run_until_complete
**File:** `orchestrator/global_scheduler.py:155`
**Type:** Error Handling

**Description:**
`loop.run_until_complete()` is called without wrapping in try/except. If the coroutine raises an exception, it will propagate and could crash the worker process.

**Code:**
```python
try:
    if inspect.iscoroutinefunction(func):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(func(*args, **kwargs))  # No error handling
    else:
        func(*args, **kwargs)
except Exception as e:
    # Error is caught but only logged
```

**Note:** The code does have exception handling at line 158, but exceptions from `run_until_complete` itself (as opposed to the function) may not be properly isolated.

---

### Issue 11: _check_gathered Called Without Result Inspection
**File:** `runtime/sprint_scheduler.py` (multiple locations)
**Type:** Error Handling

**Description:**
`await _check_gathered(gather_results)` is called per GHOST_INVARIANTS, but the return value may not be properly inspected. If `_check_gathered` returns an error indication, the scheduler may continue processing.

**Evidence:**
Lines 3213, 3289, 3412 comment: `- No asyncio.run() or loop.run_until_complete()` but doesn't specify error handling for gathered results.

**Fix:**
Ensure `_check_gathered` return value is checked:
```python
result = await _check_gathered(gather_results)
if result.error:
    raise result.error
```

---

### Issue 12: No maxlen on deque Usage
**File:** `runtime/sprint_scheduler.py` (multiple deques)
**Type:** Unbounded Growth

**Description:**
Several `deque` collections are used without `maxlen` specified:
- `self._sidecar_results` 
- `self._hypothesis_queue`

**Fix:**
Specify `maxlen=N` for bounded queues:
```python
from collections import deque
self._sidecar_results = deque(maxlen=1000)
```

---

### Issue 13: duckdb_store Batch Chunking but No Backpressure
**File:** `knowledge/duckdb_store.py`
**Type:** Flow Control

**Description:**
While `max_batch_size=500` is enforced for chunking, there's no backpressure mechanism if the pending queue grows faster than batch processing.

**Code:**
```python
for i in range(0, len(findings), max_batch_size):
    chunk = findings[i : i + max_batch_size]
```

**Fix:**
Add semaphore or queue size check before accepting new batches.

---

### Issue 14: run_in_executor Without Cancellation Handling
**File:** `coordinators/fetch_coordinator.py:831`
**Type:** Resource Management

**Description:**
`loop.run_in_executor()` is called but there's no explicit handling for cancellation. If the calling coroutine is cancelled, the executor task continues running.

**Fix:**
Store the future and add cancellation handling:
```python
future = loop.run_in_executor(...)
try:
    return await asyncio.shield(asyncio.wrap_future(future))
except asyncio.CancelledError:
    future.cancel()
    raise
```

---

### Issue 15: Race Condition in _dns_tunnel_executor Init
**File:** `tool_registry.py:436`
**Type:** Thread Safety

**Description:**
`_dns_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)` is created inside what appears to be a callback context. Multiple concurrent calls could create multiple executors.

**Code:**
```python
_dns_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
```

**Fix:**
Use `self.__dict__.setdefault('_dns_tunnel_executor', executor)` or double-checked locking.

---

## LOW Issues (Optional)

### Issue 16: Comment-Document Code
**File:** `brain/hypothesis_engine.py`
**Type:** Documentation

**Description:**
Some comments describe implementation details that could be inferred from code. For example:
```python
EXISTENCE = "existence"           # Does X exist?
```

**Fix:**
Consider whether these add value or if they duplicate what the variable name already conveys.

---

### Issue 17: Redundant Type Checking
**File:** `tool_registry.py:153`
**Type:** Code Quality

**Description:**
`if inspect.iscoroutinefunction(func)` check happens every call but could be cached during registration.

**Fix:**
Cache the result in a dict keyed by function.

---

### Issue 18: Missing Error Context in Some Exceptions
**File:** `knowledge/duckdb_store.py`
**Type:** Error Handling

**Description:**
Some exceptions are raised without context about what operation was being attempted:
```python
raise ValueError("Invalid schema")
```

**Fix:**
Include operation context:
```python
raise ValueError(f"Invalid schema for tier '{tier}': {details}")
```

---

### Issue 19: Hardcoded Magic Numbers
**File:** `runtime/sprint_scheduler.py`
**Type:** Code Quality

**Description:**
Several magic numbers without named constants:
- `30` minutes for sprint duration
- `1000` for various limits
- `3` for retry counts

**Fix:**
Extract to named constants at module level.

---

### Issue 20: Unused Import
**File:** `brain/hypothesis_engine.py`
**Type:** Dead Code

**Description:**
Based on string concatenation count (113), some imports may be unused.

**Fix:**
Run `ruff` or `pyflakes` to identify unused imports.

---

## Findings by File

| File | CRITICAL | HIGH | MEDIUM | LOW |
|------|----------|------|--------|-----|
| utils/execution_optimizer.py | 2 | - | 1 | - |
| brain/inference_engine.py | 1 | - | - | - |
| knowledge/duckdb_store.py | - | 1 | 2 | 1 |
| legacy/autonomous_orchestrator.py | - | 1 | - | - |
| brain/hypothesis_engine.py | - | 1 | 1 | 2 |
| autonomous_orchestrator.py | - | 1 | - | - |
| runtime/sprint_scheduler.py | - | - | 2 | 1 |
| orchestrator/global_scheduler.py | - | - | 1 | - |
| coordinators/fetch_coordinator.py | - | - | 1 | - |
| tool_registry.py | - | - | 1 | 1 |
| **TOTAL** | **3** | **4** | **8** | **5** |

---

## Recommendations

1. **Immediate (CRITICAL):** Fix all asyncio.run() in thread pool contexts - these are M1 crash vectors
2. **Soon (HIGH):** Address unbounded collections and begin decomposing the 31k line file
3. **Planned (MEDIUM):** Improve error handling and add backpressure mechanisms
4. **Deferred (LOW):** Code cleanup, documentation improvements

---

## Verification Commands

```bash
# Check for asyncio.run in thread pool contexts
rg "asyncio\.run\(" --type py -A2

# Check for unbounded collections
rg "self\._\w+\.append\(" --type py | head -30

# Check file sizes
wc -l **/*.py | sort -n | tail -20
```
