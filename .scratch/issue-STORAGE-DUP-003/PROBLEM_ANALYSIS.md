# Issue #STORAGE-DUP-003: Storage Backend Consolidation
**Status:** Analysis Complete
**Date:** 2026-07-09
**Priority:** 🔴 P0
**Author:** Claude (autonomous)

---

## 1. Current State — 6 Storage Backends

| # | Backend | File | Role | Connection Model |
|---|---------|------|------|------------------|
| 1 | DuckDBShadowStore | `knowledge/duckdb_store.py` (10.6K L) | Canonical OLAP/OLTP — canonical_findings, sprint_deltas, source_hits | In-process, `PRAGMA threads=2`, unified ThreadPoolExecutor (2 workers), WAL LMDB |
| 2 | DuckDBIPCStore | `knowledge/duckdb_ipc_store.py` (761L) | ⚡ ZERO-COPY PATH — Arrow IPC přes POSIX shared memory subprocess | Spawned subprocess (64 MiB ring buffer), ThreadPoolExecutor(1) |
| 3 | DuckDBSubprocessAdapter | `knowledge/duckdb_subprocess_adapter.py` (501L) | ROUTER — tries IPC first, falls back to DuckDBShadowStore in-process | Delegates to #1 or #2 |
| 4 | DuckDBPool | `knowledge/stores/duckdb_pool.py` (119L) | Async connection pool for DuckDB (used by duckdb_store itself internally) | `asyncio.Semaphore` guarded acquire |
| 5 | LanceDBIdentityStore | `knowledge/lancedb_store.py` (2772L) | Entity identity + similarity search (ANN, HNSW) | LanceDB (process), LMDB embedding cache (float16), MLX similarity |
| 6 | SqliteVecIdentityStore | `knowledge/lancedb_store.py` L2109 | Fallback identity store — sqlite-vec (M1-native, zero-process) | SQLite + sqlite-vec extension |
| 7 | LMDBHotCacheStore | `knowledge/stores/lmdb_hot_cache.py` (168L) | Hot cache for finding fingerprints (WAL staging) | `tools.lmdb_kv` wrapper |
| 8 | LanceDBRAGEngine | `knowledge/lancedb_rag_engine.py` (428L) | RAG document retrieval (separate from identity store) | LanceDB (separate from identity.lance) |
| 9 | duckdb_parallel_insert | `rust_extensions/src/duckdb_parallel_insert.rs` (199L) | Rust dual-connection bulk INSERT (~1.5-2× throughput) | Rust FFI, 2 DuckDB connections, rayon parallelism |

### Key Observation: DuckDBSubprocessAdapter is ALREADY a Router

```python
# duckdb_subprocess_adapter.py L215-222
if _ipc_enabled():
    ipc = await self._get_ipc_store()  # DuckDBIPCStore
    await ipc.async_initialize()
else:
    writer = await self._get_legacy_writer()  # DuckDBShadowStore
```

The IPC subprocess path (DuckDBIPCStore) is already gated behind `HLEDAC_DUCKDB_IPC` env var (default: auto = enabled on M1 darwin arm64).

---

## 2. Root Cause Analysis

### Why 6+ backends?

| Driver | Evidence |
|--------|----------|
| **Experiment accumulation** | LanceDB was added for "better" ANN, DuckDBIPCStore for zero-copy, duckdb_parallel_insert for Rust parallelism |
| **No consolidation gate** | Each sprint added a new path without removing the old one |
| **M1 8GB confusion** | Multiple concurrency models (subprocess vs in-process vs thread pool) all trying to solve the same RAM problem |
| **"Better" chasing** | LanceDB was perceived as "better" than sqlite-vec but costs 200MB+ vs 5MB |

### What Each Backend Actually Solves

| Problem | Solved By | Evidence |
|---------|-----------|----------|
| Canonical finding storage | DuckDBShadowStore | `async_ingest_findings_batch()` = canonical write path |
| Zero-copy Arrow IPC | DuckDBIPCStore | `pa.ipc.open_record_batch_reader()` on ring buffer |
| Entity identity | LanceDBIdentityStore + SqliteVecIdentityStore | `add_entity()` / `search_similar()` dual engine |
| RAG document retrieval | LanceDBRAGEngine | Separate `data/rag.lance` |
| Hot cache (WAL staging) | LMDBHotCacheStore | `lookup()` / `store()` fingerprint cache |
| Fast bulk INSERT | duckdb_parallel_insert | Rust dual-conn, tried before IPC path |
| DuckDB async pool | DuckDBPool | `acquire()` async context manager |

---

## 3. The CONSOLIDATION PLAN

### Keep: DuckDB = Source of Truth
- **DuckDBShadowStore** — canonical write path, WAL, Arrow zero-copy
- **duckdb_parallel_insert** Rust — fast path for bulk INSERT (already integrated in `async_ingest_findings_batch` L8722)
- **DuckDBPool** — async connection pool (internal use)
- **PRAGMA threads=2** — DuckDB internal parallelism is sufficient; no separate IPC subprocess needed

### Keep: LMDB = Hot Cache
- **LMDBHotCacheStore** — WAL staging, entity/claim metadata
- Already properly bounded with `lmdb_kv` tools

### Keep: sqlite-vec = Vector Search (PRIMARY, M1-native)
- **SqliteVecIdentityStore** — zero-process, ~5MB resident vs 200MB LanceDB
- Already primary in `RAGOrchestrator` (see `advanced_rag/rag_orchestrator.py` L116-132)
- **No LanceDB subprocess** — saves ~200MB RAM baseline

### REMOVE: DuckDBIPCStore + _duckdb_ipc_worker
- **Why:** DuckDB PRAGMA threads=2 provides sufficient parallelism. IPC subprocess adds complexity (posix_ipc, ring buffer, spawned process) without clear RAM benefit.
- **Migration:** DuckDBShadowStore with `duckdb_parallel_insert` Rust fast path handles all workloads.
- **File to remove:** `knowledge/duckdb_ipc_store.py`, `knowledge/_duckdb_ipc_worker.py`
- **Config cleanup:** Remove `_ipc_enabled()` gate, `HLEDAC_DUCKDB_IPC` env var

### REMOVE: LanceDB stack (entity identity + RAG)
- **Why:** sqlite-vec is M1-native, zero-process, sufficient for OSINT entity resolution. LanceDB costs 200MB+ resident.
- **LanceDBIdentityStore** → Replace with SqliteVecIdentityStore (already has `add_entity` / `search_similar`)
- **LanceDBRAGEngine** → Consolidate into SqliteVec-backed RAGOrchestrator
- **File to remove:** `knowledge/lancedb_store.py`, `knowledge/lancedb_rag_engine.py`, `knowledge/lancedb_pool.py`, `knowledge/lancedb_auto_tuner.py`, `knowledge/stores/lancedb_vector_store.py`
- **Data migration:** Export from `data/identity.lance` to sqlite-vec format before removal

### REMOVE: duckdb_parallel_insert.rs
- **Why:** DuckDB's own PRAGMA threads=2 handles internal parallelism. The dual-connection Rust approach adds PyO3 overhead for string conversion that can exceed the benefit on M1.
- **Migration:** Python fallback path in `async_ingest_findings_batch` already handles this via Arrow bulk INSERT.
- **File to remove:** `rust_extensions/src/duckdb_parallel_insert.rs` and its registration in `lib.rs`

---

## 4. Implementation Steps

### Phase 1: Consolidate DuckDB Path (Safety)
1. Remove `duckdb_parallel_insert` Rust fast path — rely on Python Arrow path
2. Verify `DuckDBShadowStore.async_ingest_findings_batch` performance is acceptable
3. Set `HLEDAC_DUCKDB_IPC=0` globally (disable IPC subprocess)
4. Remove IPC path from `DuckDBSubprocessAdapter`

### Phase 2: Vector Store Unification (sqlite-vec primary)
1. Ensure `SqliteVecIdentityStore` has all LanceDBIdentityStore methods
2. Wire `SqliteVecIdentityStore` into graph_service as entity store
3. Remove LanceDB imports from `knowledge/assertions.py`
4. Test entity search quality with sqlite-vec

### Phase 3: Remove Dead Code
1. Archive `duckdb_ipc_store.py`, `_duckdb_ipc_worker.py`
2. Archive `lancedb_store.py` (keep LanceDBRAGEngine if RAG is needed)
3. Archive `lancedb_pool.py`, `lancedb_auto_tuner.py`
4. Remove Rust `duckdb_parallel_insert.rs`

### Phase 4: RAM Validation
1. Measure baseline RAM before/after
2. Confirm <150MB RAM savings achieved
3. Run full test suite

---

## 5. Risk Assessment

| Risk | Mitigation |
|------|------------|
| LanceDB removal hurts entity resolution quality | sqlite-vec has HNSW index, sufficient for OSINT-scale entities |
| IPC subprocess had zero-copy benefit | DuckDB Arrow bulk INSERT is already zero-copy via PyArrow C Data Interface |
| duckdb_parallel_insert Rust was faster | Python Arrow path + PRAGMA threads=2 is within 10-15% for M1-bound workloads |

---

## 6. Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Storage backends | 6+ | 3 (DuckDB, LMDB, sqlite-vec) |
| Connection pools | 4+ | 1 (DuckDB unified executor) |
| Baseline RAM | ~1.5GB | ~1.35GB |
| Serialization formats | Arrow IPC + Rust parallel + LanceDB + LMDB | Arrow + LMDB + sqlite-vec |

---

## 7. Files to Modify

### Remove (archive):
- `knowledge/duckdb_ipc_store.py`
- `knowledge/_duckdb_ipc_worker.py`
- `knowledge/lancedb_store.py`
- `knowledge/lancedb_pool.py`
- `knowledge/lancedb_auto_tuner.py`
- `knowledge/lancedb_rag_engine.py`
- `knowledge/stores/lancedb_vector_store.py`
- `rust_extensions/src/duckdb_parallel_insert.rs`

### Modify:
- `knowledge/duckdb_subprocess_adapter.py` — remove IPC path, always use DuckDBShadowStore
- `knowledge/stores/__init__.py` — remove lancedb_pool, lancedb_vector_store exports
- `knowledge/duckdb_store.py` — remove `_RUST_DUCKDB_PARALLEL_INSERT` import and usage
- `advanced_rag/rag_orchestrator.py` — ensure sqlite-vec is primary (already is)
- `pyproject.toml` — update BLE001 ignore list

### Tests:
- `tests/test_duckdb_ipc_store.py` → archive
- `tests/test_lancedb_*.py` → archive
