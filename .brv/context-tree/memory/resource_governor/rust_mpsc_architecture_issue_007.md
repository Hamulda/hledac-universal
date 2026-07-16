---
title: Rust MPSC Architecture (Issue-007)
summary: 'MPSC architecture: crossbeam bounded channel for Python-Rust batch communication with 2048 slot capacity, zero-copy bytes, and unsendable receiver'
tags: []
related: [memory/resource_governor/issue_007_mpsc_batch_send_optimization.md]
keywords: []
createdAt: '2026-07-16T11:05:18.062Z'
updatedAt: '2026-07-16T11:05:18.062Z'
---
## Reason
Document Rust MPSC channel architecture for evidence log batch communication

## Raw Concept
**Task:**
Document Rust MPSC channel architecture for evidence log batch communication

**Changes:**
- Created crossbeam MPSC bounded channel for batch sends
- Implemented send_batch() for single Python→Rust call with N items
- Implemented recv_batch() for non-blocking drain
- ISSUE-064: Added #[pyclass(unsendable)] due to Receiver not being Send

**Files:**
- evidence_log.py
- rust_extensions/src/mpsc_pool.rs

**Flow:**
Python append() x N → send_batch() → crossbeam MPSC → recv_batch() → SQLite BLOB insert

**Timestamp:** 2025-07-16

## Narrative
### Structure
Two MPSC channels: Primary _mpsc (capacity=2048, asyncio_fallback=False) for main batch path, Secondary _mpsc2 (capacity=2048, asyncio_fallback=True) for asyncio events

### Dependencies
Uses crossbeam crate for MPSC, msgspec.msgpack for zero-copy encoding

### Highlights
Single Python→Rust call for N items (~1µs/event vs 5µs for N× calls), zero-copy bytes via msgspec.msgpack.encode(), 1 MiB memory budget (2048 slots × 512 bytes)

### Rules
Rule 1: Receiver<QueueItem> is NOT Send — requires #[pyclass(unsendable)]
Rule 2: MPSCPool must stay in originating Python async thread
Rule 3: Backpressure via MPSC bounded capacity

### Examples
Example: send_batch([msg1, msg2, msg3]) → recv_batch(max_items=100) → executemany(INSERT)

## Facts
- **mpsc_capacity**: MPSC_DEFAULT_CAPACITY is 2048 slots [project]
- **mpsc_slot_bytes**: MPSC_SLOT_BYTES is 512 bytes per slot [project]
- **mpsc_memory_budget**: Total MPSC memory budget is ~1 MiB [project]
- **mpsc_performance**: send_batch() achieves ~1µs/event vs 5µs for N× individual calls [project]
- **unsendable_reason**: #[pyclass(unsendable)] required because Receiver<QueueItem> is NOT Send [project]
- **mpsc_channel_count**: Two MPSC channels exist: _mpsc (no asyncio) and _mpsc2 (asyncio integration) [project]
