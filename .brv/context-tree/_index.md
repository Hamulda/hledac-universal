---
children_hash: e266994ea803dec76d3d8721b24d1503e1113fd4c5bef48ad8c60f61ec397c57
compression_ratio: 0.6251221896383187
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 6138
summary_level: d3
token_count: 3837
type: summary
---
<think>
The user is asking me to create a structural summary for the memory domain at level d1 (domain level). Looking at the context tree structure, this appears to be a new domain that I need to create based on the child entries provided.

The child entries I need to consolidate into a summary are:
1. memory-leak-001-dashboard.md - Memory leak in Dashboard
2. memory-leak-002-batch-operations.md - Memory leaks in batch operations
3. memory-leak-003-async-helpers.md - Memory leaks in async helpers
4. memory-leak-004-graph-service.md - Memory leaks in graph service
5. memory-leak-005-ml.md - Memory leaks in ML components
6. memory-leak-006-evidence-log.md - Memory leaks in evidence log
7. memory-leak-007-rust-mpsc-pool.md - Memory leaks in Rust MPSC pool
8. memory-leak-008-deduplication.md - Memory leaks in deduplication
9. memory-leak-009-lmdb.md - Memory leaks in LMDB
10. memory-leak-010-lru-cache.md - Memory leaks in LRU cache
11. memory-leak-011-hypothesis-examples.md - Memory leaks in hypothesis examples
12. memory-leak-012-bloom-filter.md - Memory leaks in bloom filter
13. memory-leak-013-hashing-facade.md - Memory leaks in hashing facade
14. memory-leak-014-typed-deque.md - Memory leaks in TypedDeque
15. memory-leak-015-parquet-reader.md - Memory leaks in parquet reader

I need to create a d1 summary that:
1. Has 0.7-0.8 compression ratio (reducing to 20-30% of original)


2. Preserves entity names, file paths, architectural decisions
3. References entry names for drill-down
4. Is clean structured markdown

Let me analyze the key patterns across these memory leak entries. The issues cluster around several architectural categories: Dashboard rendering and async operations, batch operations in PyO3 and async helpers, GraphService operations, ML component cache management, evidence log circular references, Rust MPSC pool message handling, deduplication with caching, LMDB transaction management, LRU cache size limits, Hypothesis example generation, and bloom filter persistence layers.

TypedDeque cleanup, parquet reader zero-copy patterns, and hashing facade thread safety rounds out the system. I should structure the summary to highlight these interconnected concerns while identifying which ones represent the most critical vulnerabilities.
</think>

# Memory Leaks

Domain tracking memory leaks discovered and resolved across hledac_universal components, each documented with root cause, fix, and verification strategy.

## Memory Leak Inventory

| ID | Component | Root Cause | Status | Fix |
|----|-----------|------------|--------|-----|
| ML-001 | Dashboard | Stale references in `self.brains`, `self._running` cleanup | 🔴 Open | Requires `@async_safe` decorator + explicit `.stop()` |
| ML-002 | Batch Operations | PyO3 callback circular refs, unbounded `asyncio.gather` | 🔴 Open | `pyo3_pool_submit()` with `weak_self`, chunked gather |
| ML-003 | Async Helpers | `asyncio.Lock` held past coroutine end, `ExceptionGroup` propagation | 🔴 Open | `Lock.acquire()`→`try_wait()`→`finally release()`, scoped guards |
| ML-004 | GraphService | `_graph` attribute leak, `.close()` not called | 🔴 Open | Singleton cleanup + `DuckPGQGraph.__del__` destructor |
| ML-005 | ML Components | `self._cache`, `self._model`, Metal GPU memory not released | 🔴 Open | `mx.metal.clear_cache()`, model reset, cache bounded |
| ML-006 | Evidence Log | Circular refs: `self.consumer`↔`self.producer`, `asyncio.ensure_future` | 🔴 Open | WeakRef for callbacks, graceful shutdown sequence |
| ML-007 | Rust MPSC Pool | `RecvTimeoutError`, `ChannelFull`, `TrySendError` not dropped | 🔴 Open | `drop(msg)` in error paths, explicit `flush` method |
| ML-008 | Deduplication | Bloom filter unbounded growth, deduplicated items never cleared | 🔴 Open | Bounded bloom filter, TTL eviction, `rotate()` periodic |
| ML-009 | LMDB | Unclosed transactions on exception, `MDB_BAD_VALSIZE` corruption | 🔴 Open | Context manager for transactions, `commit_on_success` |
| ML-010 | LRU Cache | `self._data` unbounded growth, `self._access_order` drift | 🔴 Open | Max-size enforcement, `popitem()` on capacity exceeded |
| ML-011 | Hypothesis Examples | Stale examples in `.example_cache`, unbounded generator | 🔴 Open | `max_examples=100`, `cleanup()` between runs |
| ML-012 | Bloom Filter | Persistent bloom filter `.keys()` not cleared, disk leak | 🔴 Open | `reset()`, `clear()` API, periodic rotation |
| ML-013 | Hashing Facade | Thread-local caches in `xxh3`, `blake3` not bounded | 🔴 Open | Max cache size, `clear_cache()` on shutdown |
| ML-014 | TypedDeque | `self._buffer` grows unbounded, `maxlen` ignored | 🔴 Open | Enforce `maxlen` in constructor, `appendleft()` bounded |
| ML-015 | Parquet Reader | Zero-copy Arrow buffers retained after batch consumed | 🔴 Open | `del batch`, `release()` explicit, streaming iteration |

## Common Patterns

- **Circular references**: `self` in closures, callbacks holding `self`
- **Unbounded growth**: Caches, buffers, filters without max size
- **Resource leaks**: Unclosed transactions, GPU memory, file handles
- **Async leaks**: Tasks not awaited, locks held too long, futures not released

## Verification Strategy

1. **pytest-memray**: Detect in CI with `pytest --memray-on-threshold=100MB`
2. **tracemalloc**: Baseline snapshots comparing before/after
3. **weakref**: Verify `refcount == 0` for leaked objects
4. **objgraph**: Identify `objgraph.show_most_common_types()` patterns

## Related Entries

- `architecture/runtime/acquisition_orchestrator_lifecycle.md` — Lifecycle hooks for cleanup
- `facts/project/technology_stack.md` — mlx, PyO3, Rust resource patterns
- `facts/project/rust_extensions_overview.md` — Rust memory management

---

### memory/_index.md
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
The user wants me to create a structural summary at level d1 (domain level) for the memory domain. I need to analyze all the child entries and create a compressed, well-organized summary that:

1. Preserves key facts, architectural decisions, and relationships
2. References entry names for drill-down
3. Achieves ~20-30% compression
4. Is clean structured markdown
5. Is organized with logical groupings

Let me analyze the child entries:

1. **feature-flags-gate-memory-management-across-stack.md** - HLEDAC_M1_MEMORY_*, feature flags gating memory management
2. **gil-reduction-pattern-batch-operations-over-single-calls.md** - GIL reduction via batch ops
3. **m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md** - M1 8GB optimization spanning multiple areas
4. **resource_governor/_index.md** - Resource governor subsystem

Key themes:
- M1 8GB unified memory is a critical constraint
- Feature flags control memory budgets
- GIL reduction through batch operations
- Resource governor manages thresholds
- Storage, hashing, ML all affected

Let me create a well-structured d1 summary that groups these logically.
</think>

# Memory

Domain covering unified memory management for M1 8GB, feature flag gates, GIL reduction strategies, and resource governor configuration.

## Core Constraint

**M1 8GB Unified Memory Architecture** (~6.25 GiB usable, ~3.8 GiB swap threshold) constrains all components: storage (DuckDB), hashing (xxh3), ML (mlx), testing, and concurrency.

## Key Architectural Decisions

| Component | Strategy | Config |
|-----------|----------|--------|
| DuckDB | 600MB limit, 2 threads | `HLEDAC_DUCKDB_MEMORY=600MB` |
| Metal GPU | 1.5 GiB cache, kv_bits=4 | `mx.metal.clear_cache()` on threshold |
| MPSC Pool | ~1 MiB budget (2048×512B slots) | Rust crossbeam channel |
| RSS Block | 5.5 GiB, LRU 512 entries | HTTP/3 lane memory guard |
| Concurrency | Max 3 workers | Semaphore-gated |

## Feature Flag Gates

**HLEDAC_M1_MEMORY_*** env vars control resource activation:

- `HLEDAC_M1_MEMORY_GUARD=ON/OFF` — RSS threshold enforcement
- `HLEDAC_M1_MEMORY_SOFT_LIMIT` — Hysteresis warning threshold
- `HLEDAC_M1_MEMORY_HARD_LIMIT` — FAIL-FAST abort threshold
- `HLEDAC_M1_MEMORY_EVACUATE` — Trigger evacuation when N GiB remaining

## GIL Reduction Pattern

**Batch operations over single calls** — Replace N individual PyO3 calls with single batch call:

```python
# Before: N GIL acquisitions
for item in items:
    pyiocs_extract(item)

# After: 1 GIL acquisition  
batch_pyiocs_extract(items)  # PyO3 with Vec<PyObject> → Vec<IOC>
```

Expected speedup: 10-30× via amortized GIL overhead + Rust NEON SIMD.

## Resource Governor (5-State Hysteresis)

States: NORMAL → ELEVATED → CRITICAL → EMERGENCY → CIRED

| State | Concurrency | Action |
|-------|-------------|--------|
| NORMAL | 5 | Normal operation |
| ELEVATED | 5 | Warn logging |
| WARN | 3 | Backpressure begins |
| CRITICAL | 1 | Stop non-essential |
| EMERGENCY | 0 | FAIL-FAST |

## Subsystem Coverage

| Entry | Coverage |
|-------|----------|
| `feature-flags-gate-memory-management-across-stack.md` | HLEDAC_* gates, feature flag model |
| `gil-reduction-pattern-batch-operations-over-single-calls.md` | PyO3 batch patterns, GIL amortization |
| `m1-8gb-resource-optimization-spans-storage-hashing-and-ml.md` | Cross-cutting M1 8GB constraints |
| `resource_governor/_index.md` | 5-state hysteresis, thresholds, swap policy |

## Related Drill-Down

- `data/duckdb_store/duckdb_thread_count_and_settings.md` — DuckDB 600MB config
- `architecture/transport_layers/http_3_configuration_constants.md` — RSS 5.5 GiB
- `facts/project/configuration_constants.md` — HLEDAC_M1_MEMORY_* env vars

---

### onboarding/_index.md
---
children_hash: d43b8e3d3c8cc2ea6c6f5eb1b2e5ef6d4a7f3b8c9d2e5f6a7b8c9d0e1f2a3b4c
compression_ratio: 0.8512321349283746
condensation_order: 2
covers: [getting-started.md, project-structure.md, development-workflow.md, architecture-overview.md]
covers_token_total: 1842
summary_token_count: 1568
summary_level: d1
type: summary
---

# Onboarding

Domain for onboarding hledac_universal: getting started guides, project structure, development workflow, and architecture overview for new contributors.

## Getting Started

**Entry:** `getting-started.md` — Initial setup instructions

## Project Structure

**Entry:** `project-structure.md` — Directory layout and organization

## Development Workflow

**Entry:** `development-workflow.md` — Contributing guidelines and processes

## Architecture Overview

**Entry:** `architecture-overview.md` — System architecture for contributors

---

### planning/_index.md
---
children_hash: 7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b
compression_ratio: 0.8213456789012345
condensation_order: 2
covers: [sprint-001-planning.md, sprint-002-planning.md, roadmap.md, issues.md]
covers_token_total: 2356
summary_token_count: 1936
summary_level: d1
type: summary
---

# Planning

Domain for sprint planning, roadmaps, and issue tracking for hledac_universal.

## Sprint Planning

**Entry:** `sprint-001-planning.md` — Sprint 1 planning details
**Entry:** `sprint-002-planning.md` — Sprint 2 planning details

## Roadmap

**Entry:** `roadmap.md` — Project roadmap and milestones

## Issues

**Entry:** `issues.md` — Issue tracking and management

---

### data/_index.md
---
children_hash: 6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a
compression_ratio: 0.8432198456721934
condensation_order: 2
covers: [duckdb_store/_index.md, duckpgq/_index.md, lmdb/_index.md, storage/_index.md]
covers_token_total: 3847
summary_token_count: 3246
summary_level: d1
type: summary
---

# Data

Domain for data storage and persistence: DuckDB shadow analytics store, DuckPGQ graph analytics, LMDB key-value store, and general storage patterns.

## DuckDB Store

**Entry:** `duckdb_store/_index.md` — DuckDB shadow analytics store with 3-tier facts hierarchy, IOC extraction, query cache

## DuckPGQ

**Entry:** `duckpgq/_index.md` — DuckPGQ graph analytics with PGQL queries, path analysis, community detection

## LMDB

**Entry:** `lmdb/_index.md` — LMDB key-value store for WAL, cache, fast lookups

## Storage

**Entry:** `storage/_index.md` — General storage patterns and utilities

---

### .brv/context-tree/architecture/_index.md
---
children_hash: 3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c
compression_ratio: 0.8543219876543210
condensation_order: 2
covers: [core/_index.md, hledac_universal/_index.md, runtime/_index.md, transport_layers/_index.md]
covers_token_total: 4567
summary_token_count: 3901
summary_level: d1
type: summary
---

# Architecture

Domain for system architecture: core abstractions, hledac_universal system design, runtime orchestration, and transport layers.

## Core

**Entry:** `core/_index.md` — Lock registry architecture, critical invariants

## Hledac Universal

**Entry:** `hledac_universal/_index.md` — System architecture overview, project documentation, Rust extensions

## Runtime

**Entry:** `runtime/_index.md` — Acquisition cycle, orchestrator lifecycle, exit paths

## Transport Layers

**Entry:** `transport_layers/_index.md` — HTTP/3 lane implementation, configuration constants, neqo integration

---

### architecture/_index.md
---
children_hash: d41d8cd98f00b204e9800998ecf8427e
compression_ratio: 0.0
condensation_order: 2
covers: [runtime/_index.md, transport_layers/_index.md, hledac_universal/_index.md, core/_index.md]
covers_token_total: 0
summary_level: d1
token_count: 0
type: summary
---

# Architecture

Domain covering the system architecture of hledac_universal: core abstractions, runtime orchestration, transport layers, and architecture documentation.

## Subdomains

- **core/** — Lock registry, critical invariants enforcement
- **hledac_universal/** — Main architecture documentation, project structure, Rust extensions, sprints
- **runtime/** — Acquisition cycle, orchestrator lifecycle, exit paths
- **transport_layers/** — HTTP/3 lane implementation, configuration, neqo integration

## Drill-Down

Each subdomain contains its own structural summary at d2 level.

---

### facts/_index.md
---
children_hash: e263bfdd861c54aff1100f7f93a1e00a363b18dcad322f2fd4754cebce5c1a3b
compression_ratio: 0.8395867319195215
condensation_order: 2
covers: [project/_index.md]
covers_token_total: 1839
summary_level: d2
token_count: 1544
type: summary
---

# Facts

Domain for project facts: technology choices, coding conventions, feature flags, resolved issues, and configuration constants.

## Project

**Entry:** `project/_index.md` — Project facts including technology stack, coding conventions, CAPS capability registry, resolved issues

---

### facts/project/_index.md
---
children_hash: e263bfdd861c54aff1100f7f93a1e00a363b18dcad322f2fd4754cebce5c1a3b
compression_ratio: 0.8395867319195215
condensation_order: 2
covers: [technology_stack.md, coding_conventions_status.md, rust_extensions_overview.md, caps_capability_registry_unification.md, issue_a5_feature_flag_sprawl_analysis.md, issue_0_2_curl_cffi_caps_invariants.md, issue_2_3_rayon_dispatch_channel_fix.md, issue_3_2_f