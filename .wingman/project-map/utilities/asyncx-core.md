# asyncx-core

**Type:** Utility  
**Path:** `utils/asyncx/_core.py`  
**Status:** current

## Purpose

Core async utilities: task groups, cancellation, timeout handling.

## Key Functions

| Function | Purpose |
|----------|---------|
| `TaskGroup` | Scoped task management |
| `cancel_group(tasks)` | Cancel multiple tasks |
| `timeout_after(seconds, coro)` | Timeout wrapper |
| `wait_first(coros)` | Race multiple coroutines |

## Invariants

- [UAX-1] TaskGroup: cancel on exit
- [UAX-2] Timeout: raise asyncio.TimeoutError
- [UAX-3] Wait first: return on first completion

## Dependencies

- `asyncio`
