---
children_hash: 864e24c4acbafe4611a7f3c9ad0f917f382f4eb39d7755201e9c920a757f6982
compression_ratio: 0.3050768514205869
condensation_order: 2
covers: [feature-flags-gate-memory-management-across-stack.md, gil-reduction-pattern-batch-operations-over-single-calls.md, m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md, resource_governor/_index.md]
covers_token_total: 2147
summary_level: d2
token_count: 655
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) for the memory/resource_governor topic. They've provided detailed context about what this domain should cover. I need to output ONLY the summary text, not use any tools.

Let me craft a comprehensive d1 summary based on the provided child entries and context.
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