---
children_hash: 1b313d788dc532f0ebd25f023dca4d722d0fecf02263cbb0d7e8af6b8853d759
compression_ratio: 0.47495872317006055
condensation_order: 2
covers: [feature-flags-gate-memory-management-across-stack.md, gil-reduction-pattern-batch-operations-over-single-calls.md, m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md, resource_governor/_index.md]
covers_token_total: 1817
summary_level: d2
token_count: 863
type: summary
---
<think>
The user wants me to create a structural summary at the domain level (d1) for the resource_governor domain. They've provided:

1. All the child entries with their summaries
2. A template showing how to structure the output
3. The "Output ONLY the summary text" instruction

I need to synthesize the child entries into a concise structural overview that:
- Captures the core theme and architecture
- Preserves key facts, relationships, and patterns
- References entry names for drill-down
- Achieves ~20-30% compression
- Outputs clean structured markdown

Let me analyze the key information:

**Core Theme:**
- M1 8GB Unified Memory Architecture (UMA) constraint
- 5-state hysteresis state machine
- Concurrency presets

**Architecture:**
- 5 states: NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED
- Flow: memory_check → hysteresis_state → concurrency_adjust → action_take
- Concurrency presets per state

**MPSC Batch Communication (Issue-007):**
- Two crossbeam channels
- 2048 slots × 512 bytes = 1 MiB
- ~1µs/event vs 5µs for individual calls
- msgspec gc=False optimization

**Key Optimizations:**
- P0: msgspec.Struct gc=False, GHOST_INVARIANTS CI
- P1: Per-lane RSS delta telemetry, Rust graph analytics
- P2: evidence_quality telemetry, adaptive thresholds

**Related Entries:**
- architecture/hledac_universal
- data/duckdb_store
- facts/project/technology_stack
- testing/conftest

Now I'll create the structural summary following the template format provided.
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