# P001: DuckDB Dual-Write Redundancy — Analysis & Fix

## Current Architecture

### Two paths in sprint_scheduler → duckdb_store

```
sprint_scheduler._gate_then_ingest_and_accumulate
  ├─ small batch (≤100 items): _gate_then_ingest → store.drain_and_get_accepted()
  │     └─ WriteCoalescer.submit() [fire-and-forget queue]
  │           └─ WriteCoalescer._flush() → async_ingest_findings_batch()
  │                 └─ async_record_canonical_findings_batch_arrow()
  │                     ├─ WAL: _wal_put_many_sync() (LMDB)
  │                     └─ DuckDB: insert_findings_bulk_arrow() [Arrow register+INSERT]
  │
  └─ large batch (>100 items): _parallel_ingest → async_ingest_findings_batch(chunk)
        └─ async_record_canonical_findings_batch_arrow() [SAME Arrow path]
```

### Key code locations

| Component | Location | Role |
|-----------|----------|------|
| `WriteCoalescer` | `storage/write_coalescer.py:107` | asyncio Queue + flush loop |
| Coalescer init | `duckdb_store.py:3375-3388` | started in `async_initialize()` |
| Coalescer lazy init | `duckdb_store.py:3517-3534` | started in `ensure_connected()` |
| `submit_findings()` | `duckdb_store.py:4529-4565` | → `_coalescer.submit()` or direct |
| `drain_and_get_accepted()` | `duckdb_store.py:4567-4593` | → `_coalescer.drain_and_get_accepted()` |
| `async_ingest_findings_batch()` | `duckdb_store.py:7478` | canonical Arrow ingest |
| `async_record_canonical_findings_batch_arrow()` | `duckdb_store.py:6031` | Arrow INSERT path |
| `insert_findings_bulk_arrow()` | `duckdb_store.py:2307` | DuckDB register+INSERT |
| `_parallel_ingest()` | `sprint_scheduler.py:20188` | large batch bypass |
| `_duckdb_background_writer()` | `sprint_scheduler.py:19949` | queue drain loop |

---

## Root Cause Analysis

### The claim: "synchronous drain negates async batching"

**Partially correct, but misidentified.**

The synchronous drain (`stop_sync`) is called only from `aclose()` (shutdown path, L3252-3258).
It is NOT on the hot write path. The hot path is fully async.

However, `drain_and_get_accepted()` (L4567) IS on the hot path for small batches:
1. It drains the coalescer queue via `get_nowait()` + `_flush()`
2. Then calls `_flush_fn(findings)` = `async_ingest_findings_batch`

The redundancy is more subtle: **the WriteCoalescer adds a second, inferior batching layer
on top of `async_ingest_findings_batch`'s already-superior built-in batching.**

### WriteCoalescer batching parameters:
- `max_batch_size=50` (configurable via `HLEDAC_COALESCER_MAX_BATCH`)
- `flush_interval_s=0.5` (500ms)
- Adaptive: `fast_interval_s=0.005` (5ms) when queue < 5% of max

### async_ingest_findings_batch built-in batching:
- `CHUNK_SIZE` = `duckdb_settings["chunk_size"]` (default 1024)
- Pipeline queue maxsize = 4 (concurrent chunks)
- Concurrent WAL + DuckDB via `asyncio.gather`
- Arrow zero-copy INSERT (register + INSERT...SELECT)

### The actual problems:

**Problem 1: Coalescer defeats the Arrow pipeline.**
A 50-item coalescer batch is too small to saturate the Arrow INSERT pipeline.
WAL + DuckDB run concurrently — but a 50-item batch doesn't give DuckDB enough
work to overlap effectively with WAL. The ideal batch for Arrow is 500-1024 items.

**Problem 2: Double-queueing.**
`submit_findings()` → coalescer queue (50-item cap) → flush → `async_ingest_findings_batch`.
`async_ingest_findings_batch` has its OWN chunking (1024) and pipeline queue (depth 4).
The coalescer is a sub-optimal queue in front of a better queue.

**Problem 3: `drain_and_get_accepted` semantics are inverted.**
`drain_and_get_accepted` (L4567) is described as "flush pending + submit new."
It drains the entire coalescer queue (potentially thousands of items) before
processing the caller's batch. This adds unbounded latency — the caller waits
for all previously queued items to flush before their own batch is processed.

**Problem 4: stop_sync blocking (shutdown path).**
When `aclose()` calls `_coalescer.stop_sync()` (L3256), it can block for up to 10s
waiting for the coalescer loop to drain. This uses `asyncio.run()` in a thread
pool (L258-265 of write_coalescer.py) — synchronous blocking in async shutdown.

---

## Fix: Remove WriteCoalescer

### Rationale

`async_ingest_findings_batch` is already:
- **Chunked**: 1024-item chunks (vs coalescer's 50)
- **Pipelined**: 4 concurrent chunks (WAL+DuckDB overlap)
- **Concurrent**: WAL and DuckDB run simultaneously via `asyncio.gather`
- **Zero-copy**: Arrow register+INSERT (no Python per-row overhead)
- **Adaptive**: `pipeline_maxsize` scales with UMA state (F314-4b)

The coalescer's only benefit was reducing call frequency to `async_ingest_findings_batch`.
But `async_ingest_findings_batch` already batches internally — the coalescer just
adds latency (50-item max) and complexity.

Removing the coalescer:
1. **Eliminates double-queueing** — submissions go directly to `async_ingest_findings_batch`
2. **Uses optimal batch sizes** — 1024-item Arrow chunks
3. **Eliminates stop_sync blocking** — no more 10s shutdown timeout
4. **Reduces code complexity** — removes 519-line write_coalescer.py
5. **Reduces RAM** — no coalescer queue (16384 maxsize)
6. **Preserves semantics** — `submit_findings()` becomes fire-and-forget direct call

### Implementation Plan

#### Step 1: Update `submit_findings()` — remove coalescer, direct Arrow call

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~4529-4565

```python
# REMOVE coalescer submission
# BEFORE:
if self._coalescer is not None:
    await self._coalescer.submit(findings)
else:
    try:
        await self.async_ingest_findings_batch(findings)
    except Exception:
        pass

# AFTER: direct async_ingest_findings_batch call (fire-and-forget)
async def _fire_and_forget() -> None:
    try:
        await self.async_ingest_findings_batch(findings)
    except Exception:
        pass
asyncio.create_task(_fire_and_forget())
```

#### Step 2: Update `drain_and_get_accepted()` — direct Arrow call

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~4567-4593

```python
# REMOVE coalescer drain
# BEFORE:
if self._coalescer is not None:
    return await self._coalescer.drain_and_get_accepted(findings)
try:
    return await self.async_ingest_findings_batch(findings)
except Exception:
    return []

# AFTER: direct async_ingest_findings_batch call
try:
    return await self.async_ingest_findings_batch(findings)
except Exception:
    return []
```

#### Step 3: Remove `_coalescer` field from `__init__`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~1142-1144 (remove `_coalescer` initialization)

#### Step 4: Remove coalescer initialization in `async_initialize`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~3368-3388 (remove WriteCoalescer startup)

#### Step 5: Remove coalescer initialization in `ensure_connected`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~3516-3534 (remove coalescer lazy-start)

#### Step 6: Remove `_coalescer.stop_sync()` from `aclose`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~3247-3258 (remove stop_sync call)

#### Step 7: Remove coalescer imports and `CoalescerConfig`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~3373, 3518 (remove WriteCoalescer/CoalescerConfig imports)

#### Step 8: Update `__slots__` — remove `_coalescer`

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~949 (remove `_coalescer` from slots)

#### Step 9: Update metrics — remove coalescer-related stats

**File:** `knowledge/duckdb_store.py`  
**Lines:** ~1095, 1097 (remove `"arrow_coalescer_potential"`, `"arrow_coalescer_small_chunk"`)

#### Step 10: Archive `write_coalescer.py`

Move to `.scratch/deprecated/`.

### Verification

```bash
uv run pytest tests/test_duckdb_store.py -x -q
# Also check:
# - canonical_findings writes still work
# - Arrow path is used for batches ≥5
# - coalescer stats are gone from get_stats()
```

### Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Small batch latency increases (500ms → 1024 chunk) | LOW | Arrow path with 5-item min batch; 500ms coalescer interval was worst-case; direct call is faster |
| Fire-and-forget `submit_findings()` exceptions silently lost | LOW | Already had fail-safe `try/except` in both paths |
| Shutdown behavior changes | LOW | No more 10s stop_sync blocking — beneficial |
| Backpressure removed (coalescer queue gone) | LOW | `async_ingest_findings_batch` has its own 4-chunk pipeline backpressure |

### Invariants preserved

1. `async_ingest_findings_batch` is still the canonical write path
2. Arrow INSERT (zero-copy) is still the write mechanism
3. WAL-first invariant still preserved (WAL in `async_record_canonical_findings_batch_arrow`)
4. Quality gate still applied before write
5. Graph accumulation still triggered after successful ingest
6. Fail-safe everywhere — no path raises exceptions to callers

---

## Files to Modify

| File | Change |
|------|--------|
| `knowledge/duckdb_store.py` | Remove `_coalescer` field, initialization, `submit_findings()` coalescer path, `drain_and_get_accepted()` coalescer path, `aclose()` stop_sync call, metrics |
| `storage/write_coalescer.py` | Move to `.scratch/deprecated/` |
| `tests/test_duckdb_store.py` | Update any coalescer-related assertions |
