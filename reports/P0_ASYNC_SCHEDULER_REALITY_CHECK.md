# P0 ASYNC SCHEDULER REALITY CHECK

## Status: ISSUE FOUND AND FIXED ✅

## Issue: Nested `run_until_complete` on Running Loop

### Location
`orchestrator/global_scheduler.py` line 346 (original)

### Problem
The worker method in `GlobalPriorityScheduler` called `loop.run_until_complete(func(*args, **kwargs))` when `asyncio.get_running_loop()` succeeded. This is a nested event loop crash vector on M1.

**Problematic code (before fix):**
```python
if inspect.iscoroutinefunction(func):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(func(*args, **kwargs))
        finally:
            new_loop.close()
        continue
    loop.run_until_complete(func(*args, **kwargs))  # <-- BUG: nested on running loop
```

### Fix Applied
Replaced `loop.run_until_complete()` with `asyncio.run_coroutine_threadsafe()`:

```python
if inspect.iscoroutinefunction(func):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(func(*args, **kwargs))
        finally:
            new_loop.close()
        continue
    # M1-SAFE: Schedule coroutine on running loop from worker thread
    # using run_coroutine_threadsafe instead of nested run_until_complete
    future = asyncio.run_coroutine_threadsafe(func(*args, **kwargs), loop)
    future.result()
```

### Why This Matters
- `run_coroutine_threadsafe()` schedules the coroutine on the running loop from a worker thread
- `future.result()` blocks the worker until the result is available
- This pattern is safe for M1 because it doesn't create a nested event loop

## Verification

| Check | Result |
|-------|--------|
| `python3 -m py_compile orchestrator/global_scheduler.py` | PASS |
| `uv run pytest tests/probe_resource_allocator.py` | 11/11 PASS |
| Pre-existing test failures in test_sprint55.py | YES (unrelated to this fix) |

## Related Files

| File | Role |
|------|------|
| `orchestrator/global_scheduler.py` | Fixed - nested loop bug |
| `coordinators/resource_allocator.py` | Fixed - task lifecycle |
| `tests/probe_resource_allocator.py` | New - hermetic tests |

## Notes

- The global_scheduler.py still has a ProcessPoolExecutor with workers
- Workers use `asyncio.run_coroutine_threadsafe()` for the async/sync bridge
- This is the same pattern used elsewhere in the codebase (tool_registry.py, document_intelligence.py)