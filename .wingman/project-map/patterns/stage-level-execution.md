# Stage-Level Execution Strategy

## Metadata

- **Entry Path:** patterns/stage-level-execution
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** pattern

## Summary

CPU-bound stages offloaded to ThreadPoolExecutor to avoid blocking event loop on M1.

## Source Paths

- pipeline/_stage_graph.py

## Stage Levels

| Level | Strategy | Examples |
|-------|----------|----------|
| ASYNC_IO | asyncio native | fetch, discovery, dedup |
| CPU_BOUND | ThreadPoolExecutor | enrich, match, build |
| ASYNC_COORDINATED | Self-managed | fetch coordinator |

## Why

M1 8GB: asyncio event loop blocked by CPU-bound work causes performance degradation.

## CPU Pool Management

```python
async def _get_cpu_pool():
    global _CPU_POOL
    async with _CPU_POOL_LOCK:
        if _CPU_POOL is None:
            _CPU_POOL = ThreadPoolExecutor(max_workers=2)
    return _CPU_POOL
```
