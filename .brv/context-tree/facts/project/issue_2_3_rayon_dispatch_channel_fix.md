---
title: Issue 2.3 Rayon Dispatch Channel Fix
summary: 'Issue 2.3: Added rayon_dispatch.rs with crossbeam-channel to eliminate 25× context-switch overhead from double threading'
tags: []
related: [facts/project/parallel_async_helper.md]
keywords: []
createdAt: '2026-07-24T17:45:53.765Z'
updatedAt: '2026-07-24T17:45:53.765Z'
---
## Reason
Document Issue 2.3 fix for double asyncio.to_thread overhead

## Raw Concept
**Task:**
Fix double asyncio.to_thread overhead in UnifiedExecutor.run_cpu_bound()

**Changes:**
- Added rayon_dispatch.rs with crossbeam-channel submission queue
- 1 dispatcher thread per pool type runs pool.install() and consumes from channel
- asyncio.to_thread() now only sends to channel instead of spawning threads
- Added Condvar for GIL-safe wait between submit and join
- Python API: rayon_submit_channel(), rayon_join_channel(), rayon_abort_channel()
- Legacy fallback to rayon_submit if channel version unavailable

**Files:**
- rust_extensions/src/rayon_dispatch.rs
- rust_extensions/src/lib.rs
- runtime/unified_executor.py

**Flow:**
asyncio.to_thread() -> channel send (~5μs) -> dispatcher consumes -> rayon pool executes

**Timestamp:** 2025-07-24

**Patterns:**
- `^rayon_submit_channel$` - Python FFI function for channel-based submission
- `^rayon_join_channel$` - Python FFI function for joining channel-based tasks
- `^rayon_abort_channel$` - Python FFI function for aborting channel-based tasks

## Narrative
### Structure
New rayon_dispatch.rs module handles dispatch with crossbeam-channel. Each pool type (cpu/io/mixed) has one dispatcher thread that runs pool.install() and consumes tasks from its channel. asyncio.to_thread() is now just a fast channel send operation.

### Dependencies
Requires crossbeam-channel crate. Legacy rayon_submit fallback for backward compatibility.

### Highlights
Eliminates 25× context-switch overhead by replacing 2-threads-per-task with 1-dispatcher-per-pool architecture

### Examples
Python usage: rayon_submit_channel(pool_type, func, *args) returns handle for rayon_join_channel(handle)

## Facts
- **thread_creation_overhead**: asyncio.to_thread() -> rayon_submit() -> thread::spawn() created 2 OS threads per task [project]
- **context_switch_overhead**: On 4 P-cores with 100 tasks, double threading caused 25× context-switch overhead [project]
- **dispatcher_architecture**: New rayon_dispatch.rs uses crossbeam-channel for 1 dispatcher thread per pool type (cpu/io/mixed) [project]
- **submission_latency**: Channel submission is ~5μs vs ~500μs for thread::spawn [project]
- **channel_ownership**: crossbeam-channel Receiver is Send+Sync (unlike std::sync::mpsc) [project]
- **gil_safe_wait**: Condvar used for GIL-safe wait between submit and join [project]
