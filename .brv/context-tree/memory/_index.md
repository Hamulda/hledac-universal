---
children_hash: 552c4b22c6d599b53f42dc5c4cc3b35f474e02b6908f53a22d0f9017ac65b6a7
compression_ratio: 0.591046061127852
condensation_order: 2
covers: [feature-flags-gate-memory-management-across-stack.md, gil-reduction-pattern-batch-operations-over-single-calls.md, m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md, resource_governor/_index.md]
covers_token_total: 2323
summary_level: d2
token_count: 1373
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) for the architecture domain. I need to synthesize the child entries into a cohesive overview that captures the key themes, relationships, and architectural decisions.

Let me create the summary based on the detailed analysis provided:

1. **Core Theme**: The architecture is shaped by the M1 8GB Unified Memory constraint
2. **Key Components**: 
   - MPSC batch send optimization for GIL mitigation
   - ResourceGovernor 5-state hysteresis
   - Batch operations as a cross-domain pattern
   - Zero-copy IPC via msgspec

3. **Relationships**: Multiple entries are related through these themes
4. **Key architectural decisions**: msgspec.Struct, GHOST_INVARIANTS, Rust graph analytics

I should create a comprehensive d1 summary that:
- Provides an overview of the architecture domain
- Highlights the key architectural patterns (M1 memory constraint, batch operations, GIL mitigation)
- Shows how child entries relate to these themes
- Includes key facts and relationships

Let me write this directly as a response without using tools.
</think>

# Architecture Domain Summary

## Overview
The `architecture/` domain captures the structural design, cross-subsystem constraints, and performance optimizations across Hledac Universal. The architecture is fundamentally shaped by the M1 8GB Unified Memory Architecture (UMA) as a hard constraint, with batch-first designs serving as the primary GIL mitigation pattern.

## Core Themes

### 1. M1 8GB Unified Memory — System-Wide Constraint
The ~6.25GB usable memory budget is fragmented across three subsystems without a canonical source of truth:

| Subsystem | Limit | Key Config |
|-----------|-------|------------|
| DuckDB | 600MB, 2 threads | chunk_size=500, temp_limit=1GB |
| Metal Cache | 1.5 GiB | kv_bits=4, max_kv_size=8192 |
| HTTP/3 Transport | 5.5 GiB RSS | LRU 512 entries, concurrency=3 |

This fragmentation spans 4+ domains (duckdb_store, transport_layers, memory/resource_governor, facts/project) with conflicting hardcoded values identified as a cross-domain enforcement gap.

### 2. MPSC Batch Send Optimization (Issue-007)
Single Python→Rust call for N items reduces GIL acquisition overhead from N× to 1×:
- **Architecture**: crossbeam bounded MPSC + pipe wake-up fd
- **Capacity**: 2048 slots × 512B = ~1 MiB total budget
- **Performance**: ~1µs/event vs 5µs for N× individual sends
- **Zero-copy**: msgspec.msgpack.encode() on hot-path DTOs
- **Constraint**: Receiver<QueueItem> NOT Send — requires `#[pyclass(unsendable)]`

### 3. ResourceGovernor 5-State Hysteresis
States: NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED

| State | Workers | Action |
|-------|---------|--------|
| NORMAL | 5 | normal operation |
| ELEVATED | 3 | warn fetch limits |
| CRITICAL | 1 | emergency fetch limits |
| EMERGENCY | 0 | halt acquisition |
| CIRED | 0 | CI environment safety |

Swap detected at 3.8 GiB threshold with cross-domain visibility gap (no telemetry from DuckDB/Metal).

### 4. Batch Operations as Cross-Domain Pattern
Two implementations share the batch-first design philosophy:
- **duckdb_store**: Arrow RecordBatches (100k rows/batch), 1.5-2× faster than executemany
- **MPSC**: 2048×512B slots, ~1µs/event vs 5µs single-send

### 5. LayerStack & LayerProtocol
Python-based orchestration of Rust protocol adapters with zero-copy UDS IPC via msgspec.msgpack.

## Child Entry Relationships

### Memory/Resource Management
- `m1-8gb-unified-memory-as-system-wide-constraint.md` — Hardware context
- `m1-8gb-unified-memory-constrains-3-subsystems.md` — Constraint breakdown
- `m1resourcegovernor_implementation.md` — 5-state hysteresis state machine
- `m1_8gb_ram_priority_optimizations.md` — P0-P3 optimization roadmap

### IPC/Communication
- `batch-operations-as-gil-mitigation-pattern.md` — Cross-domain synthesis
- `issue_007_mpsc_batch_send_optimization.md` — Python batch API
- `rust_mpsc_architecture_issue_007.md` — Rust crossbeam architecture
- `zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md` — msgspec usage

### Subsystem Architectures
- `duckdb_kuzu_dual_graph_architecture.md` — DuckDB + Kuzu dual graph
- `rust_extensions_architecture.md` — PyO3/Rust integration
- `sidecar_bus_architecture.md` — Sidecar orchestration
- `vectorstore_architecture.md` — Embedding pipeline
- `semantic_deduplicator.md` — Dedup module

## Key Architectural Decisions

| Decision | Rationale | Source |
|----------|-----------|--------|
| msgspec.Struct with gc=False | ~200 bytes/future on hot-path DTOs | Priority P0 |
| GHOST_INVARIANTS in CI | Enforces critical invariants (I6, I4) | Priority P0 |
| Rust graph analytics | 10-100× speedup potential over igraph | Priority P1 |
| Per-lane RSS delta telemetry | ResourceGovernor cross-domain visibility | Priority P1 |

## Cross-Domain Enforcement Gaps

1. **Memory constraint fragmentation**: DuckDB 600MB, Metal 1.5GiB, HTTP/3 5.5GiB RSS hardcoded across 4+ domains
2. **ResourceGovernor visibility**: No telemetry from DuckDB/Metal subsystems
3. **Canonical truth source**: No single domain owns the unified memory budget

## Key Files
- `rust_extensions/src/mpsc_pool.rs` — MPSC batch channel implementation
- `evidence_log.py` — Python batch send wrapper (`_RustMPSCBytes.send_batch`)
- `memory/resource_governor/m1resourcegovernor_implementation.md` — State machine details
- `memory/resource_governor/m1_8gb_ram_priority_optimizations.md` — Optimization roadmap