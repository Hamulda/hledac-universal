---
title: parallel Async Helper
summary: parallel() replaces deprecated bounded_gather with 4 exception policies (raise/first/collect/log), concurrency control, taskgroup backend, and I6/I7/I8 invariants
tags: []
related: [facts/project/hledac_universal_claude_md.md, facts/project/rust_extensions_overview.md, facts/project/issue_2_3_rayon_dispatch_channel_fix.md]
keywords: []
createdAt: '2026-07-16T11:06:29.925Z'
updatedAt: '2026-07-16T11:06:29.925Z'
---
## Reason
Document parallel() unified async concurrency helper with exception policies

## Raw Concept
**Task:**
Document parallel() async concurrency helper function

**Files:**
- utils/async_helpers.py

**Flow:**
coroutines -> policy routing -> execution -> error handling -> ParallelResult

**Timestamp:** 2026-07-16

**Author:** ISSUE-006

**Patterns:**
- `raise|first|collect|log` - Valid exception policy values
- `asyncio\.TaskGroup` - TaskGroup backend marker
- `asyncio\.timeout` - Timeout backend marker

## Narrative
### Structure
parallel() is a unified async concurrency helper in utils/async_helpers.py. Replaces bounded_gather (deprecated per rule #2997), safe_gather_strict, safe_gather_ok, safe_gather_fire_and_forget.

### Dependencies
Requires Python 3.11+ for taskgroup and timeout backends; falls back to gather+semaphore otherwise

### Highlights
Generic async function with 4 exception policies. Concurrency controlled via semaphore. Returns ParallelResult with ok/errors/re_raised. I6/I7/I8 invariants govern exception routing.

### Rules
Rule: I6 — asyncio.CancelledError always re-raised
Rule: I7 — BaseException (non-Exception) always re-raised
Rule: I8 — Exception routed per policy

### Examples
result = await parallel([fetch(url) for url in urls], concurrency=5, policy="collect", ctx="fetch.urls")

## Facts
- **parallel_deprecation**: parallel() replaces bounded_gather, safe_gather_strict, safe_gather_ok, safe_gather_fire_and_forget [project]
- **deprecation_rule**: bounded_gather is deprecated per rule #2997 [convention]
- **exception_policies**: parallel() supports 4 exception policies: raise, first, collect (default), log [project]
- **concurrency_control**: concurrency parameter caps parallel tasks via semaphore [project]
- **taskgroup_backend**: taskgroup=True uses asyncio.TaskGroup (3.11+), False uses gather+semaphore [project]
- **timeout_handling**: timeout parameter uses asyncio.timeout (3.11+) [project]
- **return_type**: Returns ParallelResult dataclass with ok, errors, re_raised fields [project]
- **invariants_i6_i7_i8**: I6: CancelledError always re-raised; I7: BaseException (non-Exception) always re-raised; I8: Exception routed per policy [project]
