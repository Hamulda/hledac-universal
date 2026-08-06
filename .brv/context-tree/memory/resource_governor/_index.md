---
children_hash: 94d1a7ba47f0c0211a455f5345a5e6315d06039c9f5b1528a039987a0a52f7b8
compression_ratio: 0.2763830259942235
condensation_order: 1
covers: [batch-operations-as-gil-mitigation-pattern.md, context.md, issue_007_mpsc_batch_send_optimization.md, m1-8gb-memory-budget-fragmentation.md, m1-8gb-unified-memory-as-system-wide-constraint.md, m1-8gb-unified-memory-constrains-3-subsystems.md, m1-bounds-configuration-fragmentation.md, m1_8gb_ram_priority_optimizations.md, m1resourcegovernor_implementation.md, rust_mpsc_architecture_issue_007.md, zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md]
covers_token_total: 4501
summary_level: d1
token_count: 1244
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. Let me analyze the entries to identify patterns, relationships, and key facts.

**Key themes across entries:**

1. **M1 8GB Unified Memory Architecture (UMA)** - A recurring constraint that shapes:
   - DuckDB limits (600MB/2 threads)
   - Metal cache ceiling (1.5 GiB)
   - HTTP/3 transport (5.5 GiB RSS)
   - Memory budget fragmentation across 4+ domains

2. **MPSC Batch Send Optimization (Issue-007)** - Performance optimization:
   - 2048 slots × 512B = ~1 MiB budget
   - ~1µs/event vs 5µs for single sends
   - crossbeam bounded channels
   - Zero-copy via msgspec.msgpack.encode()

3. **Batch Operations as GIL Mitigation** - Pattern across subsystems:
   - Arrow RecordBatches (100k rows/batch) in duckdb_store
   - MPSC 2048 slots × 512B in memory/resource_governor

4. **Zero-Copy IPC via msgspec** - Used in both:
   - MPSC batch communication
   - LayerStack UDS IPC

5. **ResourceGovernor** - 5-state hysteresis:
   - NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED
   - Concurrency presets with worker limits

**Relationships:**
- issue_007_mpsc_batch_send_optimization ↔ rust_mpsc_architecture_issue_007
- m1resourcegovernor_implementation ↔ m1_8gb_ram_priority_optimizations
- Multiple syntheses referencing duckdb_store, memory/resource_governor, transport_layers

**Key architectural decisions:**
- msgspec.Struct gc=False for hot-path DTOs


- GHOST_INVARIANTS CI enforcement
- Rust graph analytics (10-100× speedup potential)

Now I'll synthesize this into a cohesive d1 summary that captures the UMA constraint hierarchy and how batch operations mitigate GIL contention across storage, IPC, and compute layers.
</think>

# Resource Governor & Memory Architecture

## Overview
Memory management across Hledac Universal centers on the M1 8GB Unified Memory Architecture (UMA) as a hard constraint, MPSC batch communication for GIL mitigation, and a 5-state ResourceGovernor hysteresis system.

## M1 8GB Unified Memory — Cross-Subsystem Constraints

The ~6.25GB usable budget is split across three subsystems without a canonical source of truth:

| Subsystem | Limit | Key Config |
|-----------|-------|------------|
| DuckDB | 600MB, 2 threads | chunk_size=500, temp_limit=1GB |
| Metal Cache | 1.5 GiB | kv_bits=4, max_kv_size=8192 |
| HTTP/3 Transport | 5.5 GiB RSS | LRU 512 entries, concurrency=3 |

This fragmentation spans 4+ domains (duckdb_store, transport_layers, memory/resource_governor, facts/project) with conflicting hardcoded values.

## MPSC Batch Send Optimization (Issue-007)

Single Python→Rust call for N items reduces GIL acquisition overhead from N× to 1×:

- **Architecture**: crossbeam bounded MPSC + pipe wake-up fd
- **Capacity**: 2048 slots × 512B = ~1 MiB total budget
- **Performance**: ~1µs/event vs 5µs for N× individual calls
- **Zero-copy**: msgspec.msgpack.encode() on hot-path DTOs
- **Constraint**: Receiver<QueueItem> NOT Send — requires `#[pyclass(unsendable)]`

## ResourceGovernor 5-State Hysteresis

States: NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED

| State | Workers | Action |
|-------|---------|--------|
| NORMAL | 5 | normal operation |
| ELEVATED | 3 | warn fetch limits |
| CRITICAL | 1 | emergency fetch limits |
| EMERGENCY | 0 | halt acquisition |
| CIRED | 0 | CI environment safety |

Swap detected at 3.8 GiB threshold.

## Batch Operations as GIL Mitigation Pattern

Two implementations share the batch-first design:

- **duckdb_store**: Arrow RecordBatches (100k rows/batch), 1.5-2× faster than executemany
- **MPSC**: 2048×512B slots, ~1µs/event vs 5µs single-send

## Key Files

- `rust_extensions/src/mpsc_pool.rs` — MPSC batch channel implementation
- `evidence_log.py` — Python batch send wrapper (`_RustMPSCBytes.send_batch`)
- `memory/resource_governor/m1resourcegovernor_implementation.md` — 5-state hysteresis
- `memory/resource_governor/m1_8gb_ram_priority_optimizations.md` — P0-P3 priority matrix

## Priority Optimizations (Meadows Leverage)

| Priority | Optimization | Impact |
|----------|--------------|--------|
| P0 | msgspec.Struct gc=False | ~200 bytes/future saved |
| P0 | GHOST_INVARIANTS CI enforcement | catches I6, I4 violations |
| P1 | Per-lane RSS delta telemetry | ResourceGovernor visibility |
| P1 | Rust graph analytics | 10-100× speedup over igraph |

## Child Entry Mapping

- `batch-operations-as-gil-mitigation-pattern.md` — cross-domain synthesis
- `issue_007_mpsc_batch_send_optimization.md` — Python batch API
- `rust_mpsc_architecture_issue_007.md` — Rust crossbeam architecture
- `m1-8gb-unified-memory-constrains-3-subsystems.md` — constraint breakdown
- `m1-8gb-unified-memory-as-system-wide-constraint.md` — hardware context
- `m1_8gb_ram_priority_optimizations.md` — optimization roadmap
- `m1resourcegovernor_implementation.md` — state machine details
- `zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md` — msgspec usage