---
children_hash: 97d06da0ebe0e2abd2e7f6856aee299a723fe6e09d53f457b250b53ed4687e31
compression_ratio: 0.3431151241534989
condensation_order: 1
covers: [context.md, issue_007_mpsc_batch_send_optimization.md, m1-8gb-memory-budget-fragmentation.md, m1-8gb-unified-memory-as-system-wide-constraint.md, m1_8gb_ram_priority_optimizations.md, m1resourcegovernor_implementation.md, rust_mpsc_architecture_issue_007.md, zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md]
covers_token_total: 3544
summary_level: d1
token_count: 1216
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about the resource_governor topic. I need to synthesize these child entries into a concise overview that:
- Preserves key facts, relationships, and patterns
- References entry names for drill-down
- Targets ~20-30% compression
- Preserves entity names, file paths, API signatures, architectural decisions
- Discards verbose examples, repeated explanations

Let me analyze the entries:

1. **context.md** - Overview of resource_governor: UMA memory management, threshold configuration, concurrency control, swap safety policies

2. **issue_007_mpsc_batch_send_optimization.md** - MPSC batch send optimization using Rust send_batch, 1 MiB memory budget, GIL reduction

3. **m1-8gb-memory-budget-fragmentation.md** - Memory budget hardcoded across 4 domains without canonical allocation

4. **m1-8gb-unified-memory-as-system-wide-constraint.md** - M1 8GB hardware limit shapes architecture across storage, concurrency, ML inference

5. **m1_8gb_ram_priority_optimizations.md** - P0-P3 priority matrix for M1 8GB RAM optimizations

6. **m1resourcegovernor_implementation.md** - 5-state hysteresis machine, dual-channel TTL cache, concurrency presets

7. **rust_mpsc_architecture_issue_007.md** - MPSC architecture for Python-Rust batch communication

8. **zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md** - msgspec.msgpack with gc=False for zero-copy serialization

Key patterns and relationships:
- M1 8GB UMA is the system-wide constraint
- ResourceGovernor uses 5-state hysteresis machine
- MPSC optimization reduces GIL overhead


- msgspec.msgpack with gc=False eliminates serialization overhead and enables zero-copy optimization
- DuckDB constrained to 600MB with 2 threads; Metal cache capped at 1.5 GiB
- Concurrency presets dynamically adjust worker count based on system state

I'll organize this into thematic clusters: the M1 8GB hardware boundary, ResourceGovernor's memory management and state transitions, performance optimizations through MPSC batching and zero-copy serialization, priority-based optimization strategy, and the fragmentation challenge across domains.
</think>

# Resource Governor

## Overview

The `resource_governor` domain manages M1 8GB Unified Memory Architecture (UMA) as a system-wide constraint, covering threshold configuration, concurrency control, swap safety policies, and zero-copy IPC optimization.

## M1 8GB Unified Memory Architecture

Hardware constraint: ~6.25GB usable budget with swap detection at 3.8 GiB.

**Memory Allocation:**
- DuckDB: 600MB cap, 2 threads, 1GB temp, chunk size 500
- Metal cache: ceiling 1.5 GiB (kv_bits=4, max_kv_size=8192)
- MPSC pool: 1 MiB total (2048 × 512B slots)

**Problem:** Memory budgets hardcoded across 4 domains without canonical source of truth (`m1-8gb-memory-budget-fragmentation.md`).

## ResourceGovernor 5-State Hysteresis

State machine: NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED

**Concurrency Presets** (emergency→ok):
- emergency: 0 workers
- critical: 1 worker
- warn: 3 workers
- soft_warn: 5 workers
- ok: 5 workers

**Dependencies:** psutil for memory detection, concurrent.futures for worker pool management.

Flow: `memory_check → hysteresis_state → concurrency_adjust → action_take`

See `m1resourcegovernor_implementation.md`, `uma_memory_management.md`

## MPSC Batch Send Optimization (Issue-007)

Python→Rust batch communication reduces GIL acquisition from N× to 1×.

**Architecture:**
- crossbeam bounded MPSC (capacity=2048)
- Two channels: `_mpsc` (no asyncio), `_mpsc2` (asyncio integration)
- Zero-copy via `msgspec.msgpack.encode()`

**Performance:** ~1µs/event vs 5µs for N× individual calls

**Constraint:** `Receiver<QueueItem>` is NOT Send — requires `#[pyclass(unsendable)]`

See `rust_mpsc_architecture_issue_007.md`, `issue_007_mpsc_batch_send_optimization.md`

## Zero-Copy Serialization Pattern

`msgspec.Struct` with `gc=False` saves ~200 bytes per future instance.

**Applications:**
- MPSC batch events (2048 slots × 512B)
- LayerStack UDS IPC via msgspec.msgpack

**P0 Optimization:** Add `gc=False` to msgspec.Struct classes in evidence_log.py, finding.py, pipeline/*.py

See `zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md`

## Priority Optimization Matrix

| Priority | Item | Impact |
|----------|------|--------|
| P0 | msgspec Struct gc=False | ~200B/future × 10K findings = 2MB saved |
| P0 | GHOST_INVARIANTS CI enforcement | 10 invariants (I4, I6, etc.) |
| P1 | Per-lane RSS delta telemetry | ResourceGovernor visibility |
| P1 | Rust graph analytics | 10-100× speedup over Python igraph |
| P2 | evidence_quality telemetry | findings_with_citation / total_findings |
| P2 | Adaptive thresholds | Swap history-based adjustment |

See `m1_8gb_ram_priority_optimizations.md`