---
children_hash: da2a165b1eb76c84ea5a82a6ded7de577044f0aa47175dc8e961dab0596ef5ca
compression_ratio: 0.4119469026548673
condensation_order: 2
covers: [feature-flags-gate-memory-management-across-stack.md, gil-reduction-pattern-batch-operations-over-single-calls.md, m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md, resource_governor/_index.md]
covers_token_total: 2260
summary_level: d2
token_count: 931
type: summary
---
<think>
The user is asking me to create a structural summary at the domain level for the resource_governor topic. They've provided detailed knowledge entries and want me to synthesize them into a concise overview that preserves key facts, relationships, and patterns while referencing entry names for drill-down.

Looking at the context, this is a curation task where I need to update/create a `_index.md` file at the domain level with this synthesized summary. The summary should:
- Preserve key facts, relationships, and patterns
- Reference entry names for drill-down
- Target ~20-30% compression
- Preserve entity names, file paths, API signatures, architectural decisions
- Discard verbose examples and repeated explanations

Let me synthesize the information from the child entries into a concise domain-level summary. The structure should include:
1. A clear domain overview
2. Key architectural components
3. Important patterns and relationships
4. References to child entries for drill-down

I'll format this as a proper _index.md file for the resource_governor domain.
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