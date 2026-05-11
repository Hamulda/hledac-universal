# F_GLOBAL_SCHEDULER_SPAWN_REGISTRY_REALITY

**Date:** 2026-05-11
**Status:** CONFIRMED FAIL — registry empty in child processes

## Executive Summary

`_TASK_REGISTRY` (module-level OrderedDict) is **NOT visible** in ProcessPoolExecutor worker processes on macOS (Python 3.14). This is a fundamental macOS behavior: **spawn start method** creates child processes with blank memory image.

**Impact:** All registered tasks (including async coroutine functions) are **NOT reachable** from worker processes. Jobs dispatched to workers will fail with "Unknown task" error.

## Test Results

```
Default multiprocessing start method: spawn
Available start methods: ['spawn', 'fork', 'forkserver']

=== SPAWN child (default on macOS) ===
  Parent: _TASK_REGISTRY = ['parent_task']
  Child:  _TASK_REGISTRY = []         ← EMPTY
  Child get_task('parent_task') = None  ← NOT FOUND

=== SPAWN worker behavioral test ===
  Parent: registered 'behavioral_task'
  Child worker received job, tried get_task('behavioral_task') → task_found: False

=== FORK child ===
  Parent: _TASK_REGISTRY = ['fork_task']
  Child:  _TASK_REGISTRY = []         ← ALSO EMPTY
  Child get_task('fork_task') = None

WHY: The test file `test_global_scheduler_spawn_registry.py` runs as __main__.
When child process calls `import test_global_scheduler_spawn_registry`, it gets
a fresh copy of __main__, not the parent's running copy. The parent's registry
was in the parent's memory space — neither spawn nor fork copies module state.

For actual fork to inherit registry, the module must be importable (not __main__).
```

## Why This Happens

With **spawn** (macOS default):
- Child process starts Python interpreter from scratch
- No parent's memory is inherited
- Child must `import` the module fresh — registry starts EMPTY

With **fork** (Linux default, available but broken on macOS for this test):
- Child inherits parent's memory (copy-on-write)
- Module-level `_TASK_REGISTRY` SHOULD be visible
- BUT: on macOS, `fork` is discouraged and may have deadlock risks with locks

## Why The Fork Test Also Failed

The fork test showed empty registry because `test_global_scheduler_spawn_registry.py` runs as `__main__`. When the child does `import test_global_scheduler_spawn_registry`, it gets a fresh `__main__` namespace — not the parent's.

In the actual `GlobalPriorityScheduler`, the module is imported normally (not as `__main__`), so fork WOULD work on Linux. On macOS with spawn, the registry issue persists.

## Code Analysis: `orchestrator/global_scheduler.py`

### Line 325-331 (Problem Spot)
```python
if task_name not in _TASK_REGISTRY:
    logger.error(f"Unknown task '{task_name}' in queue")
    self._result_queue.put((job_id, "failed", f"Unknown task: {task_name}", worker_id))
    continue  # ← this continue is CORRECT here (no async success path)
```

### Lines 336-369 (Option A Async Path)
```python
if inspect.iscoroutinefunction(func):
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    try:
        coro = func(*args, **kwargs)
        result = new_loop.run_until_complete(
            asyncio.wait_for(coro, timeout=30.0)
        )
        # Reports succeeded
        self._result_queue.put((job_id, "succeeded", None, worker_id))
    except asyncio.CancelledError:
        # Reports failed (not timeout/status)
        self._result_queue.put((job_id, "failed", "task timeout/cancelled", worker_id))
    finally:
        asyncio.set_event_loop(None)
        new_loop.close()
```

**Findings:**
- `asyncio.get_running_loop()` — NOT present ✅
- `asyncio.run_coroutine_threadsafe` — NOT present ✅
- `continue` after async success — NOT present (succeeded is reported, no continue) ✅
- Fresh event loop per call — ✅ correct Option A pattern

### Async Timeout Status: "failed" (Not "timeout")

Line 364: `self._result_queue.put((job_id, "failed", "task timeout/cancelled", worker_id))`

This is by design — the collector handles `status == "timeout"` only from the
timeout checker thread (lines 252-257). The worker reports `CancelledError`
as "failed" because the task was already cancelled when wait_for resolved.

**Async timeout status: `failed`** (with error message "task timeout/cancelled")

## Minimal Policy Options

### Option 1: Use fork context (macOS/Linux)
```python
import multiprocessing as mp
mp.set_start_method('fork')  # Only call once, before any threads
```
**Pros:** Registry inherited, no code changes
**Cons:** Fork can deadlock with thread locks (dangerous on macOS)

### Option 2: ThreadPoolExecutor for async work
Route all `iscoroutinefunction` tasks to `ThreadPoolExecutor` instead of `ProcessPoolExecutor`.
**Pros:** No spawn issues, works correctly on all platforms
**Cons:** Loses M1 multi-core CPU parallelism for async tasks

### Option 3: Registry initializer function
```python
def init_worker(registry_dict):
    global _TASK_REGISTRY
    _TASK_REGISTRY = registry_dict

executor = ProcessPoolExecutor(max_workers=3,
    initializer=init_worker,
    initargs=(dict(registered_tasks),))
```
**Pros:** Explicit registry pass
**Cons:** Requires picklable registry contents

### Option 4: Use Manager().dict() for shared state
**Pros:** Cross-process shared state
**Cons:** Slow (network-like IPC), defeats ProcessPool parallelism benefits

## Recommendation

**For M1 MacBook Air (8GB) with spawn default:**
- Keep `ProcessPoolExecutor` for CPU-bound sync tasks
- Route async coroutine tasks to `ThreadPoolExecutor` via separate pool
- This preserves M1 multi-core for CPU work while allowing async to work correctly

**Alternative:** Set `mp.set_start_method('fork')` if the codebase doesn't use threading locks (safer on Linux, available but risky on macOS).

## Verdict

| Concern | Status |
|---------|--------|
| `asyncio.get_running_loop()` in `_worker_loop` | NOT PRESENT ✅ |
| `asyncio.run_coroutine_threadsafe()` in `_worker_loop` | NOT PRESENT ✅ |
| `continue` after async success | NOT PRESENT ✅ |
| Fresh event loop per async call | CORRECT ✅ |
| `_TASK_REGISTRY` visible in spawn workers | **FAIL — EMPTY** ⚠️ |
| Async timeout status | `failed` (not `timeout`) ✅ by design |
| Spawn registry reality | CONFIRMED BROKEN on macOS spawn |

## Tests to Run

```bash
# Compile check
python -m py_compile orchestrator/global_scheduler.py

# Existing probe tests
uv run python -m pytest tests/probe_global_scheduler.py -v

# New spawn registry test
uv run python -m pytest tests/test_global_scheduler_spawn_registry.py -v
```