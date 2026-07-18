---
children_hash: 7d8a3fa6a15f31a3a7dd94f8335a4f8ef5c294561e8fd3d07feeb89c8a7fa3b8
compression_ratio: 0.2768537768537769
condensation_order: 1
covers: [context.md, issue_007_mpsc_batch_send_optimization.md, m1-8gb-unified-memory-as-system-wide-constraint.md, m1_8gb_ram_priority_optimizations.md, m1resourcegovernor_implementation.md, rust_mpsc_architecture_issue_007.md]
covers_token_total: 2886
summary_level: d1
token_count: 799
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about resource_governor. I need to:

1. Condense child entries into a structural overview
2. Preserve key facts, relationships, and patterns
3. Reference entry names for drill-down
4. Target ~20-30% compression
5. Output clean structured markdown

Let me analyze the entries:

1. **context.md** - Overview of UMA memory management with key concepts
2. **issue_007_mpsc_batch_send_optimization.md** - MPSC batch send optimization
3. **m1-8gb-unified-memory-as-system-wide-constraint.md** - Hardware constraint synthesis
4. **m1_8gb_ram_priority_optimizations.md** - Priority matrix for RAM optimizations
5. **m1resourcegovernor_implementation.md** - ResourceGovernor implementation details
6. **rust_mpsc_architecture_issue_007.md** - Rust MPSC architecture

Key themes to preserve:
- M1 8GB UMA constraint
- 5-state hysteresis state machine (NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED)
- Concurrency presets (0-5 workers based on state)
- MPSC batch send optimization (2048 slots × 512 bytes = 1 MiB)
- msgspec gc=False optimization
- ResourceGovernor thresholds

Let me create a concise structural summary.
</think>

# Resource Governor — Domain Summary

## Core Theme
M1 8GB Unified Memory Architecture (UMA) is the system-wide hardware constraint shaping all memory management decisions. ResourceGovernor implements a 5-state hysteresis state machine with concurrency presets to prevent OOM kills during inference pipelines.

## Architecture

### Hysteresis State Machine (5 States)
**NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED**

Flow: `memory_check → hysteresis_state → concurrency_adjust → action_take`

### Concurrency Presets by State
| State | Workers | Fetch Limits |
|-------|---------|--------------|
| NORMAL | 5 | Full |
| ELEVATED | 5 | Reduced |
| CRITICAL | 1 | Minimal |
| EMERGENCY | 0 | Suspended |
| CIRED | 0 | Suspended |

### MPSC Batch Communication (Issue-007)
Two crossbeam bounded channels: `_mpsc` (asyncio_fallback=False) and `_mpsc2` (asyncio_fallback=True)
- Capacity: 2048 slots × 512 bytes = **1 MiB total budget**
- Performance: ~1µs/event vs 5µs for N× individual calls
- Receiver requires `#[pyclass(unsendable)]` (not Send)

## Key Optimizations

### P0 (High Leverage)
- `msgspec.Struct gc=False` — saves ~200 bytes/future × N futures
- GHOST_INVARIANTS CI enforcement via ruff/mypy plugins

### P1 (Medium Leverage)
- Per-lane RSS delta telemetry for ResourceGovernor visibility
- Rust graph analytics (10-100× speedup over Python igraph)

### P2 (Ongoing)
- evidence_quality telemetry (findings_with_citation / total_findings)
- Adaptive ResourceGovernor thresholds based on swap history

## Related Entries

- **architecture/hledac_universal** — M1 Metal cache hard limit 1.5 GiB
- **data/duckdb_store** — 600MB limit, 2 threads, 500 chunk size
- **facts/project/technology_stack** — PyO3, msgspec, crossbeam dependencies
- **testing/conftest** — Session-scoped event loop for M1 8GB

## Dependencies
- psutil (memory detection)
- concurrent.futures (worker pool)
- crossbeam (MPSC channels)
- msgspec.msgpack (zero-copy encoding)