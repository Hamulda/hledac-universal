---
children_hash: df7edaa99cedc523142638b4efca2ea5ed01cd367c1761cebd96e3c84de211ea
compression_ratio: 0.2800409941070971
condensation_order: 1
covers: [context.md, issue_007_mpsc_batch_send_optimization.md, m1-8gb-memory-budget-fragmentation.md, m1-8gb-unified-memory-as-system-wide-constraint.md, m1-bounds-configuration-fragmentation.md, m1_8gb_ram_priority_optimizations.md, m1resourcegovernor_implementation.md, rust_mpsc_architecture_issue_007.md, zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md]
covers_token_total: 3903
summary_level: d1
token_count: 1093
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries. These are all related to memory/resource_governor topic.

Let me analyze the key content:

1. **context.md** - Overview of UMA memory management with threshold configuration, concurrency control, swap safety policies, and dual-channel TTL cache

2. **issue_007_mpsc_batch_send_optimization.md** - MPSC batch send optimization with 2048 slots, 512B per slot, ~1 MiB budget, single Python→Rust call, GIL overhead reduction

3. **m1-8gb-memory-budget-fragmentation.md** - Memory budget fragmentation across domains (DuckDB 600MB, Metal cache formula, etc.)

4. **m1-8gb-unified-memory-as-system-wide-constraint.md** - M1 8GB hardware limit affecting all subsystems with 5-state hysteresis

5. **m1-bounds-configuration-fragmentation.md** - Configuration fragmentation across duckdb_store, transport_layers, resource_governor

6. **m1_8gb_ram_priority_optimizations.md** - P0-P3 priority matrix with msgspec gc=False, GHOST_INVARIANTS CI, Rust graph analytics

7. **m1resourcegovernor_implementation.md** - ResourceGovernor implementation with 5-state machine, dual-channel TTL cache, concurrency presets

8. **rust_mpsc_architecture_issue_007.md** - Rust MPSC architecture with crossbeam bounded channels

9. **zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md** - msgspec zero-copy spanning memory and architecture

Key patterns:
- M1 8GB UMA budget is the core constraint
- ResourceGovernor manages memory via 5-state hysteresis (NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED)


- MPSC optimization reduces batch sends to a single Python→Rust call with ~1µs/event, and msgspec with gc=False provides zero-copy benefits. Configuration values are scattered across DuckDB (600MB/2threads), Metal cache (1.5GiB), MPSC pool (1 MiB), and RSS (5.5GiB/512 LRU), creating fragmentation. The 5-state machine controls concurrency through presets like emergency=0 workers, critical=1, warn=3, soft_warn=5, ok=5, and dual-channel TTL cache manages memory with swap tiered policy.
</think>

# Resource Governor

UMA memory management for M1 8GB unified memory — threshold configuration, concurrency control, swap safety policies, and MPSC batch optimization.

## Core Architecture

- **5-State Hysteresis Machine**: NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED
- **Dual-Channel TTL Cache**: Alongside swap tiered policy for memory reclamation
- **Concurrency Presets**: emergency=0, critical=1, warn=3, soft_warn=5, ok=5 workers
- **Flow**: memory_check → hysteresis_state → concurrency_adjust → action_take

## Key Subsystems

### MPSC Batch Optimization (Issue-007)
- **m1resourcegovernor_implementation.md**: ResourceGovernor 5-state machine with swap tiers, thermal 82°C, lock ordering rules
- **rust_mpsc_architecture_issue_007.md**: Crossbeam bounded channel architecture
- **issue_007_mpsc_batch_send_optimization.md**: Batch send optimization — 2048 slots × 512B = ~1 MiB budget, ~1µs/event vs 5µs
- **zero-copy-ipc-via-msgspec-msgpack-spans-memory-architecture.md**: msgspec gc=False zero-copy spanning memory and LayerStack UDS IPC

## M1 8GB Constraints

### Fragmentation Issues
- **m1-8gb-memory-budget-fragmentation.md**: DuckDB claims 600MB but duckdb_store.py overrides to 2 threads; no canonical budget doc
- **m1-bounds-configuration-fragmentation.md**: Thresholds scattered across 4+ domains without shared constants

### Hardware Context
- **m1-8gb-unified-memory-as-system-wide-constraint.md**: ~6.25GB usable, swap_detected at 3.8 GiB, affects storage/concurrency/ML/testing
- **m1_8gb_ram_priority_optimizations.md**: P0 msgspec gc=False (~200B/future), P0 GHOST_INVARIANTS CI, P1 Rust graph analytics 10-100× speedup

## Memory Budget Distribution

| Subsystem | Limit | Config |
|-----------|-------|--------|
| DuckDB | 600MB, 2 threads | facts/project |
| Metal Cache | 1.5 GiB, kv_bits=4 | resource_governor |
| MPSC Pool | ~1 MiB | rust_extensions |
| RSS Block | 5.5 GiB, LRU 512 | transport_layers |

## Related Entries

- `data/duckdb_store/duckdb_thread_count_and_settings.md` — DuckDB 600MB/2threads
- `architecture/transport_layers/http_3_configuration_constants.md` — RSS 5.5 GiB config
- `facts/project/configuration_constants.md` — HLEDAC_M1_MEMORY_* env vars
- `facts/project/m1_8gb_unified_memory.md` — Hardware baseline