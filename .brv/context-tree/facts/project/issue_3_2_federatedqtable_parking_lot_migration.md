---
title: Issue 3.2 FederatedQTable parking_lot Migration
summary: 'Issue 3.2: Replaced DashMap with parking_lot::RwLock + AHashMap in federated_qtable.rs to fix segfaults caused by crossbeam/PyO3 GIL conflicts'
tags: []
related: [facts/project/issue_0_2_curl_cffi_caps_invariants.md, facts/project/issue_2_3_rayon_dispatch_channel_fix.md]
keywords: []
createdAt: '2026-07-24T17:57:50.632Z'
updatedAt: '2026-07-24T17:57:50.632Z'
---
## Reason
Document DashMap to parking_lot::RwLock migration resolving PyO3 GIL segfaults

## Raw Concept
**Task:**
Migrate federated_qtable.rs from DashMap to parking_lot::RwLock to fix PyO3 GIL segfaults

**Changes:**
- Replaced DashMap<String, f64> with RwLock<AHashMap<String, f64>>
- Changed MODULE_QTABLE singleton type
- Updated all .key()/.value() iterator calls to tuple destructuring (k, v)
- Added test_parking_lot_send_sync for Send+Sync verification

**Flow:**
Python call -> PyO3 -> RwLock read for queries, RwLock write for updates -> lane isolation via key prefix

**Timestamp:** 2026-07-24

**Patterns:**
- `^(\w+)::([^|]+)\|(.+)$` - Extracts lane, state_key, action from composite key

## Narrative
### Structure
RustFederatedQTable wraps parking_lot::RwLock<AHashMap> with lane isolation (key prefix lane::). RustFederatedQTableBatch enables rayon-parallel batch updates.

### Dependencies
parking_lot (RwLock), AHashMap (ahash), PyO3 for Python bindings, rayon for batch parallelism

### Highlights
atomic_q_update uses two-phase locking: read lock to compute next_max_q, then write lock for atomic CAS. Auto-eviction every ~100 updates when ≥50% capacity.

### Rules
Rule 1: Always hold read lock during max_q computation
Rule 2: NaN/Inf results clamped to 0.0
Rule 3: At capacity skips insert (no inline eviction)

### Examples
get_best_action(lane, state_key, actions) -> String
update(lane, state_key, action, reward, next_state_key) -> atomic Q-learning update

## Facts
- **dashmap_conflict**: DashMap uses crossbeam internally for sharding [project]
- **pyo3_gil_conflict**: Crossbeam shard locking conflicts with PyO3 GIL handling in Python async/ThreadPoolExecutor contexts [project]
- **parking_lot_safety**: parking_lot::RwLock is Send+Sync by default without unsafe [project]
- **qtable_type_change**: federated_qtable.rs field changed from DashMap<String, f64> to RwLock<AHashMap<String, f64>> [project]
- **module_qtable_type**: MODULE_QTABLE singleton changed from LazyLock<DashMap> to LazyLock<RwLock<AHashMap>> [project]
- **max_lanes**: MAX_LANES is 3 [project]
- **max_qtable_entries**: MAX_QTABLE_ENTRIES is 1024 per lane (3072 total) [project]
- **rayon_threads**: Rayon uses adaptive 1-4 threads via adaptive_scheduler::mixed_threshold() [project]
- **key_format**: Key format is lane::state_key|action [project]
- **persistence**: Persistence uses bincode with 2 MiB cap and flock-based atomic write [project]
- **api_stability**: No API changes - Python fallback shim unchanged [project]
