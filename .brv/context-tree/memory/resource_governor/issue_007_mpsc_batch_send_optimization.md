---
title: ISSUE-007 MPSC Batch Send Optimization
summary: 'ISSUE-007: MPSC batch send optimization using Rust send_batch - single Python→Rust call for N items, memory budget 1 MiB (2048×512B slots), addresses GIL acquisition overhead'
tags: []
related: [memory/resource_governor/uma_memory_management.md, memory/resource_governor/rust_mpsc_architecture_issue_007.md]
keywords: []
createdAt: '2026-07-14T09:33:22.786Z'
updatedAt: '2026-07-14T09:33:22.786Z'
---
## Reason
Document MPSC batch send optimization reducing GIL overhead from N× to 1×

## Raw Concept
**Task:**
ISSUE-007: MPSC Batch Send Optimization

**Changes:**
- Added Rust send_batch for N-item batch sends in single Python→Rust call
- Added Python _RustMPSCBytes.send_batch wrapper
- Added create_events_batch for batch event submission
- Fixed ISSUE-064: #[pyclass(unsendable)] for Receiver (not Send)

**Files:**
- rust_extensions/src/mpsc_pool.rs
- evidence_log.py

**Flow:**
append() x N -> send_batch -> crossbeam MPSC -> recv_batch -> Python async

**Timestamp:** 2026-07-14

**Patterns:**
- `MPSC_DEFAULT_CAPACITY.*=.*\d+` - MPSC capacity constant definition
- `MPSC_SLOT_BYTES.*=.*\d+` - MPSC slot size constant

## Narrative
### Structure
Python asyncio event loop + crossbeam bounded MPSC + pipe wake-up fd. Python holds Senders (cloned). Rust holds Receiver. Pipe delivers async wake-up.

### Dependencies
crossbeam channel library, PyO3 #[pyclass(unsendable)], msgspec for serialization

### Highlights
Single Python→Rust call for N items reduces GIL acquisition overhead from N× to 1×. Zero-copy bytes via msgspec.msgpack.encode(). Backpressure handled via MPSC bounded capacity.

### Rules
Rule: Receiver<QueueItem> is NOT Send (requires #[pyclass(unsendable)])
Rule: Senders are Send but Receiver is the constraint
Rule: Each send() still does to_vec() internally (crossbeam requirement)

### Examples
3 call sites in sprint_scheduler.py: I2P findings, steganography findings, graph_rag insights
Test cases: test_pool_create, test_add_sender, test_send_and_recv, test_full_backpressure(capacity=2), test_multi_sender, test_recv_batch_limits

## Facts
- **mpsc_capacity**: MPSC_DEFAULT_CAPACITY = 2048 slots [project]
- **mpsc_slot_bytes**: MPSC_SLOT_BYTES = 512 bytes per slot [project]
- **mpsc_memory_budget**: Memory budget: 2048 × 512B ≈ 1 MiB total [project]
- **batch_performance**: Performance: ~1µs/event vs 5µs for N× individual calls [project]
- **capacity_headroom_ratio**: Capacity is 2× evidence_log maxsize=500 for headroom [project]
- **unsendable_requirement**: ISSUE-064: #[pyclass(unsendable)] required because Receiver is NOT Send [project]
- **zero_copy_path**: ISSUE-006: Zero-copy path via msgspec.msgpack.encode() [project]
