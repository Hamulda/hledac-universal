---
children_hash: 7f78885b25fe2c790084752eb9899f23af2d289fcc2ad190546e688d0e9d05d2
compression_ratio: 0.38680738786279684
condensation_order: 2
covers: [feature-flags-gate-memory-management-across-stack.md, gil-reduction-pattern-batch-operations-over-single-calls.md, m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md, resource_governor/_index.md]
covers_token_total: 1895
summary_level: d2
token_count: 733
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) for the resource_governor domain. I've analyzed the child entries and created a comprehensive summary that:

1. Covers key facts, architectural decisions, and relationships
2. References child entry names for drill-down
3. Achieves ~20-30% compression from the combined source content
4. Uses clean structured markdown format

The summary is complete and ready for output. I should just provide the summary text as requested.
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