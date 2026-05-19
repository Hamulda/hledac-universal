# Local Storage Architecture Audit — M1 8GB

**Date:** 2026-05-18
**Scope:** `knowledge/duckdb_store.py`, `knowledge/wal.py`, `knowledge/dedup.py`, `knowledge/semantic_store.py`, `knowledge/semantic_store_buffer.py`, `knowledge/lancedb*`, `knowledge/graph_attachment.py`
**Goal:** Optimize local-first storage for M1 8GB UMA

---

## Finding Ingest Flow (Canonical Write Path)

```
SprintScheduler.store.async_ingest_findings_batch(findings)
│
├─ 1. Quality Gate: _assess_finding_quality()  [duckdb_store.py:4551]
│   ├─ Hot cache lookup (in-memory, bounded)
│   ├─ LMDB persistent dedup lookup
│   ├─ URL-first fingerprint
│   └─ Semantic dedup (short strings only) → LanceDB ANN
│
├─ 2. WAL Write: _wal_manager.wal_put_many()  [wal.py:357]
│   └─ shadow_wal.lmdb (64MB map, append-only)
│
├─ 3. DuckDB Bulk Insert: _sync_insert_findings_bulk_as_tuples()  [duckdb_store.py:4974]
│   └─ .duckdb file (400MB limit, 2 threads normal)
│
├─ 4. Graph (Truth-Write): _graph_ingest_findings()  [duckdb_store.py:802]
│   └─ IOCGraph/Kuzu via buffer_ioc() (fire-and-forget)
│
├─ 5. Semantic Buffer: _semantic_buffer_findings()  [duckdb_store.py:868]
│   └─ SemanticStoreBuffer → LanceDB (flush at WINDUP)
│
└─ 6. Graph (Cross-Sprint): _accumulate_findings_to_graph()  [sprint_scheduler.py:7268]
    └─ DuckPGQ upsert_ioc_batch() (MAX_GRAPH_BATCH=1000)
```

---

## CRITICAL Issues (M1 Crash Risk / Data Loss)

### C1: WAL Eviction Loads All Markers Into Memory
**File:** `knowledge/wal.py:311`
**Severity:** CRITICAL — M1 memory, data integrity
```python
markers: list[tuple[float, str]] = []  # ALL markers loaded
# ... sort ...
for i in range(evict_count):
    _, key = markers[i]
    self._wal_lmdb.delete(key)  # Individual txn per delete
```
- **Problem:** `_evict_oldest_pending_markers()` loads ALL markers into a Python list before sorting
- **Impact:** With 10,000 markers at ~200 bytes = ~2MB Python overhead + O(n log n) sort in-process
- **Fix:** Use `wal_lmdb.cursor()` with `get_items()` range iteration without full materialization

### C2: WAL Eviction Individual LMDB Deletes in Loop
**File:** `knowledge/wal.py:336-339`
**Severity:** CRITICAL — Performance
```python
for i in range(evict_count):
    _, key = markers[i]
    if self._wal_lmdb.delete(key):
        evicted += 1
```
- **Problem:** Individual LMDB transaction per delete (10,000 markers = 10,000 transactions)
- **Fix:** Use `delete_many()` or batch cursor delete

### C3: LanceDB LMDB Per-Item Write Transactions
**File:** `knowledge/lancedb_kv.py:241-243`
**Severity:** CRITICAL — Performance, M1
```python
for key, value in zip(keys, values):
    with self._db.begin(write=True) as txn:
        txn.put(key, value)
```
- **Problem:** Per-item LMDB transaction instead of `put_many()`
- **Invariant Violation:** CLAUDE.md: "LMDB bulk write: vždy přes put_many()"
- **Fix:** Use `lmdb_kv.put_many()` pattern

### C4: ANN Index Rebuild Exceeds M1 8GB
**File:** `knowledge/lancedb_kv.py:864-866`
**Severity:** CRITICAL — M1 memory, TODO D7
```python
# TODO D7: 5GB risk on M1 8GB
# ANN index rebuild can exceed available RAM
```
- **Problem:** No RAM guard before ANN index rebuild
- **Impact:** `usearch` index build loads 10,000 vectors without memory check
- **Fix:** Add `psutil.virtual_memory()` check before rebuild, defer to disk

### C5: LanceDB _scan_and_evict O(n) Sync Blocking
**File:** `knowledge/lancedb_kv.py:957-978`
**Severity:** CRITICAL — M1 blocking, async violation
```python
def _scan_and_evict(self) -> int:
    # Full DB scan O(n) on every eviction
    # No asyncio.to_thread wrapper
```
- **Problem:** O(n) database scan on every eviction, synchronous blocking
- **Impact:** Blocks event loop when called via `asyncio.to_thread`
- **Fix:** Add chunked iteration + memory guard

### C6: Duplicate WAL Writes in Batch Path
**File:** `knowledge/duckdb_store.py:4464 + 4495`
**Severity:** CRITICAL — Duplicate work, invariant confusion
```python
# Line 4464: WAL write for ALL findings
wal_put_many(items)
# Line 4495: DuckDB insert (different path)
_sync_insert_findings_bulk_as_tuples(rows)
```
- **Problem:** `_canonical_findings_batch_to_activation_results` writes to WAL then DuckDB separately
- **vs single path:** `async_record_canonical_finding` writes WAL twice (wal_put + pending sync marker on failure)
- **Fix:** Batch path should write pending sync markers on partial DuckDB failure

### C7: Triple-Increment _accepted_count
**File:** `knowledge/duckdb_store.py:3935 + 4759 + 4796`
**Severity:** HIGH — Counter inaccuracy
```python
# Line 3935 (in async_record_canonical_findings_batch):
self._accepted_count += len([r for r in results if r["lmdb_success"]])
# Line 4759 (in async_ingest_findings_batch):
self._accepted_count += len(accepted_findings)
# Line 4796 (fail-open exception path):
self._accepted_count += len(findings)
```
- **Problem:** Counter incremented 3x for same findings in fail-open paths
- **Fix:** Single increment point after quality gate

### C8: Duplicate _store_persistent_dedup Calls
**File:** `knowledge/duckdb_store.py:4609 + 4625 + 4708`
**Severity:** HIGH — Redundant writes
```python
# Line 4609: After LMDB persistent lookup
_store_persistent_dedup(fp, ...)
# Line 4625: URL-first path returns
return _store_persistent_dedup(fp, ...)
# Line 4708: Short string/entropy path (never reached structurally)
_store_persistent_dedup(fp, ...)
```
- **Problem:** Same fingerprint stored multiple times in same call path
- **Fix:** Single store point after all dedup checks pass

---

## HIGH Issues (Performance / Correctness)

### H1: Dedup LMDB Per-Item Transactions
**File:** `knowledge/dedup.py:195-198`
**Severity:** HIGH — Performance
```python
def store_persistent_dedup(self, fp: str, value: str) -> bool:
    with self._dedup_lmdb._env.begin(write=True) as txn:
        txn.put(key, value_bytes)
```
- **Problem:** Individual transaction per `store_persistent_dedup()` call
- **Impact:** N calls = N transactions (N=500 batch size = 500 transactions)
- **Fix:** Add `store_persistent_dedup_batch()` using `put_many()`

### H2: Dedup Hot Cache FIFO Instead of LRU
**File:** `knowledge/dedup.py:88-90, 210-225`
**Severity:** HIGH — M1 memory, cache efficiency
```python
self._dedup_hot_cache: dict[str, str] = {}
self._dedup_hot_cache_order: OrderedDict = OrderedDict()  # FIFO
```
- **Problem:** FIFO eviction evicts frequently accessed items prematurely
- **Impact:** Cache thrashing for popular fingerprints
- **Fix:** Use `functools.lru_cache` or `cachetools.LRUCache`

### H3: Semantic Store NumPy Double Conversion
**File:** `knowledge/semantic_store.py:185`
**Severity:** MEDIUM — Performance waste
```python
np.array(emb, dtype="float32").tolist()  # Creates numpy array then converts back
```
- **Problem:** LanceDB accepts list directly; unnecessary numpy overhead
- **Fix:** Pass list directly to LanceDB

### H4: Dedup Silent Exception Swallow
**File:** `knowledge/dedup.py:199-200`
**Severity:** MEDIUM — Fail-soft gap
```python
except Exception as e:
    self._dedup_lmdb_last_error = f"store failed for fp={fp[:8]}: {e}"
    # Silent — no logging, no propagation
```
- **Problem:** Dedup store failure silently swallowed; caller unaware
- **Impact:** May mask storage failures
- **Fix:** Add `logger.warning()` on store failure

### H5: WAL Silent Fail on Writes
**File:** `knowledge/wal.py:129-130`
**Severity:** MEDIUM — Fail-soft gap
```python
except Exception:
    return False  # Silent fail — no logging
```
- **Problem:** WAL write failure returns False silently
- **Impact:** Data loss without visibility
- **Fix:** Add `logger.warning()` on WAL write failure

### H6: Hot Cache Unbounded Growth Between Evictions
**File:** `knowledge/dedup.py:221-225`
**Severity:** MEDIUM — M1 memory
```python
# Eviction only triggered when cache exceeds threshold
# But threshold check happens on every store, not on a timer
if len(self._dedup_hot_cache) > self._hot_cache_max:
    self._evict_from_hot_cache()
```
- **Problem:** Cache can grow large between eviction checks
- **Impact:** Memory pressure on M1
- **Fix:** Explicit size check on every path; don't rely on store trigger

### H7: Semantic Buffer No Explicit Bounds
**File:** `duckdb_store.py:868` + `semantic_store_buffer.py`
**Severity:** MEDIUM — M1 memory
```python
def _semantic_buffer_findings(findings):
    self._semantic_store_buffer.buffer_findings(findings)
```
- **Problem:** SemanticStoreBuffer has no local bounds; relies on SemanticStore._MAX_PENDING=10_000
- **Impact:** 10,000 findings × average embedding size could exceed M1 limits
- **Fix:** Add explicit memory guard before buffer_findings

### H8: MAX_GRAPH_BATCH Not Enforced
**File:** `duckdb_store.py:7268` + `graph_accumulator.py:90`
**Severity:** MEDIUM — Invariant gap
```python
# duckdb_store.py:7268 calls directly
graph_service.upsert_ioc_batch(rows)
# graph_accumulator.py has MAX_GRAPH_BATCH=1000 but only used in tests
```
- **Problem:** Chunking bound exists but not enforced in production call chain
- **Impact:** Large sprints could overwhelm DuckPGQ
- **Fix:** Enforce chunking in `duckdb_store.py` before calling `upsert_ioc_batch`

---

## Storage Configuration Summary

| Component | Parameter | Value | Assessment |
|-----------|-----------|-------|------------|
| DuckDB | memory_limit (normal) | 400MB | OK for M1 |
| DuckDB | threads | 2 | OK for M1 UMA |
| DuckDB | safe_mode (critical) | true | OK |
| DuckDB | preserve_insertion_order | false | Good for write perf |
| DuckDB | enable_object_cache | false | Good for M1 |
| LMDB (WAL) | map_size | 64MB | Conservative OK |
| LMDB (Dedup) | map_size | 64MB | Conservative OK |
| LMDB | readahead | False | M1 M218C optimization |
| LMDB | writemap | False | Safer |
| LanceDB | cache | 256MB default, 512MB cap | OK |
| LanceDB | vector batch | 16 | Small, N+1 risk |
| usearch | vectors | 10,000 | No RAM guard |
| Semantic Store | _MAX_PENDING | 10,000 | OK |

---

## Performance Bottleneck Candidates

| Rank | Bottleneck | Location | Impact |
|------|------------|----------|--------|
| 1 | LMDB per-item transactions | wal.py:336-339, dedup.py:195-198, lancedb_kv.py:241-243 | O(n) transactions |
| 2 | O(n) WAL eviction sort | wal.py:311 | Full marker list in memory |
| 3 | ANN index rebuild 5GB | lancedb_kv.py:864-866 | M1 crash risk |
| 4 | _scan_and_evict O(n) sync | lancedb_kv.py:957-978 | Event loop blocking |
| 5 | FIFO hot cache thrashing | dedup.py:88-90 | Cache inefficiency |

---

## Benchmark Candidates

| Benchmark | Purpose | Metrics |
|-----------|---------|---------|
| `bench_wal_eviction` | WAL eviction with 10k markers | Time, memory peak |
| `bench_lmdb_batch_vs_item` | put_many vs per-item transactions | Throughput, latency |
| `bench_lancedb_scan_evict` | _scan_and_evict performance | Time vs DB size |
| `bench_semantic_buffer` | SemanticStoreBuffer flush | Memory, time |
| `bench_duckdb_batch_insert` | Bulk vs row-by-row | Time, DuckDB memory |

---

## Invariants (Storage Safety)

1. **WAL durability:** All canonical findings written to LMDB WAL before DuckDB
2. **LMDB bulk write:** Always use `put_many()` — never per-item `begin(write=True)` in loop
3. **DuckDB memory:** Never exceed 400MB normal / 200MB emergency
4. **LanceDB cache:** Never exceed 512MB hard cap
5. **Fail-soft:** All storage operations return False/empty on failure, never raise
6. **No sync in async:** All blocking I/O offloaded via `run_in_executor` or `asyncio.to_thread`
7. **M1 RAM guard:** Heavy operations (ANN rebuild, vision encoding) check `is_critical` before proceeding
8. **Graph batch:** `upsert_ioc_batch` chunked to MAX_GRAPH_BATCH=1000
9. **Semantic buffer:** Bounded by `_MAX_PENDING=10_000` with eviction

---

## Recommendations (Priority Order)

1. **Immediate:** Fix LMDB per-item transactions (C2, H1) → use `put_many()`
2. **Immediate:** Add RAM guard before ANN index rebuild (C4)
3. **High:** Replace FIFO with LRU in dedup hot cache (H2)
4. **High:** Remove duplicate _store_persistent_dedup calls (C8)
5. **High:** Fix triple-increment _accepted_count (C7)
6. **Medium:** Add logging to silent WAL/dedup failures (H4, H5)
7. **Medium:** Enforce MAX_GRAPH_BATCH in production call chain (H8)
8. **Medium:** Add explicit memory guard to semantic buffer (H7)
9. **Low:** Remove numpy double conversion in semantic store (H3)
10. **Low:** Consider 32MB map_size for WAL (savings ~0.5MB RAM)
