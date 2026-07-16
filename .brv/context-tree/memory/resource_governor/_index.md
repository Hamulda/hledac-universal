---
children_hash: 505a504a834926d51d440d92d1d48665baa4ba0555b41a6ed97ff1a9e3c67aa2
compression_ratio: 0.35247524752475246
condensation_order: 1
covers: [context.md, issue_007_mpsc_batch_send_optimization.md, m1_8gb_ram_priority_optimizations.md, m1resourcegovernor_implementation.md, rust_mpsc_architecture_issue_007.md]
covers_token_total: 2525
summary_level: d1
token_count: 890
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided child entries. I need to:

1. Analyze the child entries about resource_governor
2. Preserve key facts, architectural decisions, and relationships
3. Reference entry names for drill-down
4. Target ~20-30% compression
5. Output clean structured markdown

Let me identify the key themes:
- ResourceGovernor for M1 8GB RAM UMA system
- MPSC batch send optimization (ISSUE-007)
- Memory management with hysteresis state machine
- Concurrency control with presets
- Swap tiered policy
- Dual-channel TTL cache

Key relationships:
- issue_007_mpsc_batch_send_optimization.md ↔ rust_mpsc_architecture_issue_007.md
- m1_8gb_ram_priority_optimizations.md ↔ m1resourcegovernor_implementation.md
- m1resourcegovernor_implementation.md consolidated from uma_memory_management.md

Key architectural decisions:
- MPSC with 2048 slots × 512 bytes = ~1 MiB budget
- 5-state machine (NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED)
- Zero-copy via msgspec.msgpack
- #[pyclass(unsendable)] for Receiver
- P0-P3 priority optimizations

Let me write the summary.
</think>

# resource_governor

## Overview
Memory management system for M1 8GB Unified Memory Architecture (UMA) with priority-based optimizations, MPSC batch communication, and hysteresis-based concurrency control.

## Architecture

**5-State Hysteresis Machine**
- States: NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED
- Flow: `memory_check → hysteresis_state → concurrency_adjust → action_take`
- Dual-channel TTL cache with swap tiered policy

**MPSC Batch Communication** (see: `issue_007_mpsc_batch_send_optimization.md`, `rust_mpsc_architecture_issue_007.md`)
- Single Python→Rust call via `send_batch()` for N items
- crossbeam bounded channel (2048 slots × 512 bytes = ~1 MiB budget)
- Zero-copy encoding via `msgspec.msgpack.encode()`
- Performance: ~1µs/event vs 5µs for N× individual calls
- Two channels: `_mpsc` (no asyncio) and `_mpsc2` (asyncio integration)

## Concurrency Presets

| State | Workers | Fetch Limit |
|-------|---------|-------------|
| emergency | 0 | 0 |
| critical | 1 | min |
| warn | 3 | reduced |
| ok | 5 | normal |

## Key Relationships

- `issue_007_mpsc_batch_send_optimization.md` documents Python wrapper and batch API
- `rust_mpsc_architecture_issue_007.md` documents Rust channel architecture
- `m1_8gb_ram_priority_optimizations.md` ranks optimizations P0-P3 by Meadows leverage
- `m1resourcegovernor_implementation.md` consolidated from `uma_memory_management.md` (canonical source)

## P0-P3 Priority Matrix

**P0** (low effort, high impact):
- `msgspec.Struct gc=False` on hot-path DTOs (~200B/future × N)
- Enforce GHOST_INVARIANTS in CI via ruff/mypy plugins

**P1**:
- Per-lane RSS delta telemetry for ResourceGovernor visibility
- Rust graph analytics (10-100× speedup over Python igraph)

**P2**:
- `evidence_quality` telemetry (findings_with_citation / total_findings)
- Adaptive ResourceGovernor thresholds based on swap history

**P3**:
- DuckDB chunk tuning (MAX_CHUNK_SIZE=500, MAX_CHUNK_CONCURRENCY=2)

## Critical Implementation Rules

1. `Receiver<QueueItem>` is NOT Send — requires `#[pyclass(unsendable)]`
2. GHOST_INVARIANTS I6: `asyncio.gather` must have `return_exceptions=True`
3. GHOST_INVARIANTS I4: `mx.eval([])` must precede `mx.metal.clear_cache()`
4. Capacity headroom: 2× evidence_log maxsize=500 (2048 slots)

## Hardware Calibration

| Config | Usable Memory |
|--------|---------------|
| M1 8GB | 6.25 GB |
| M2 16GB | — |
| M3 24GB | — |