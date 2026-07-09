# SPRINT F228D — ASYNC LOCK REALITY SEAL
## Lock Classification Report

**Date:** 2026-05-09
**Sprint:** F228D — ASYNC LOCK REALITY SEAL
**Files Audited:** `coordinators/memory_coordinator.py`, `knowledge/analytics_hook.py`, `knowledge/ann_index.py`

---

## Classification Summary

| File | Class / Module | Lock Name | Classification |
|------|---------------|-----------|----------------|
| `coordinators/memory_coordinator.py` | `MultiLevelContextCache` | `self._lock` | **NEEDS_ASYNC_LOCK** |
| `coordinators/memory_coordinator.py` | `UniversalMemoryCoordinator` | `self.lock` (line ~739) | **SAFE_SYNC_BOUNDARY** |
| `knowledge/analytics_hook.py` | `AnalyticsHook` | `_worker_lock` | **FALSE_POSITIVE** |
| `knowledge/ann_index.py` | `_ANNIndex` | `self._lock` | **SAFE_SYNC_BOUNDARY** |
| `knowledge/ann_index.py` | module-level | `_ann_index_lock` | **SAFE_SYNC_BOUNDARY** |

---

## 1. `MultiLevelContextCache._lock` — NEEDS_ASYNC_LOCK

**File:** `coordinators/memory_coordinator.py`
**Lock:** `self._lock` — was `threading.RLock()`, now `asyncio.Lock()`

### Evidence

All callers of `MultiLevelContextCache` async methods are async context:

```
get() → async def get()
set() → async def set()
clear() → async def clear()
_find_similar_entry_faiss() → async def
_find_similar_entry_hnsw() → async def
```

Inside these methods, lock scope is minimal — only dict/list mutations and stats counter updates:

```python
# coordinators/memory_coordinator.py — get() method
async with self._lock:
    self.stats["total_requests"] += 1
    # ... hit/miss update only, no await inside
```

No `await` occurs inside `async with self._lock:` blocks.

### Why Threading.Lock Was Wrong

`threading.RLock()` in an async context blocks the event loop when the lock is held by one async task and another async task tries to acquire it. Since `get()`, `set()`, `clear()` are `async def`, they hold the lock across `await` points implicitly. Threading.Lock would serialize all cache operations and block the event loop.

### Patch Applied

```python
# Before:
self._lock = threading.RLock()

# After:
self._lock: asyncio.Lock = asyncio.Lock()
```

All `with self._lock:` changed to `async with self._lock:` in async methods.

### Missing Imports Fixed

During patching, two missing imports were discovered and repaired:
- `from pathlib import Path` — needed at line 2360 in `__init__`
- `import hashlib` — needed at line 2637 in `set()`

---

## 2. `UniversalMemoryCoordinator.self.lock` — SAFE_SYNC_BOUNDARY

**File:** `coordinators/memory_coordinator.py`
**Lock:** `self.lock` (threading.Lock, ~line 739)

### Evidence

Sync boundary methods call only sync code:

```
allocate() → sync def, uses self.lock
free()     → sync def, uses self.lock
touch()    → sync def, uses self.lock
```

No async methods in `UniversalMemoryCoordinator` touch `self.lock`. The `self._lock` (asyncio.Lock) is separate and only guards async methods in `MultiLevelContextCache`.

### Classification Rationale

`self.lock` guards memory allocation tracking (`_allocated`, `_zone_usage`) which are written only from sync context (allocate/free/touch called from synchronous pipeline code). No async code path writes to these structures.

---

## 3. `AnalyticsHook._worker_lock` — FALSE_POSITIVE

**File:** `knowledge/analytics_hook.py`
**Lock:** `_worker_lock` — threading.Lock

### Evidence

`_worker_lock` guards `_worker_started` using **double-checked locking**:

```python
# Fast path — no lock acquired if already started
if self._worker_started:
    return

# Slow path — lock ensures only one thread starts worker
with self._worker_lock:
    if not self._worker_started:  # re-check under lock
        # ... start worker via loop.create_task()
```

`_ensure_worker()` is called from `shadow_record_finding()` → `enqueue()` (async call chain), BUT:

1. The thread that holds the lock during slow-path is the **same thread** that will start the async worker via `loop.create_task()` — no cross-thread handoff.
2. The worker function `self._worker()` is `async def`, but it's started via `create_task()` which is thread-safe.
3. No `await` occurs inside `with self._worker_lock:`.
4. `_closed` flag is never written inside the lock.

### Why Not NEETS_ASYNC_LOCK

`_worker_lock` is acquired only in the slow path (double-check). The fast path (when `_worker_started=True`) acquires **no lock at all**. When the slow path is taken, the thread already holds the GIL and the async loop is the same thread — `loop.create_task()` is safe.

---

## 4. `_ANNIndex._lock` — SAFE_SYNC_BOUNDARY

**File:** `knowledge/ann_index.py`
**Lock:** `self._lock` — threading.Lock

### Evidence

All callers are **sync def** from the embedding_pipeline sync context:

```
ann_search()  → sync def — called from embedding_pipeline
upsert()      → sync def — called from embedding_pipeline
close()       → sync def
```

The lock guards LanceDB `table.search()` and `table.add()` operations across ThreadPoolExecutor workers. No async def methods exist in `_ANNIndex`.

No `await` inside `with self._lock:` blocks.

### Module-level `_ann_index_lock` — SAFE_SYNC_BOUNDARY

Guards module-level singleton `_ann_index` initialization via double-checked locking. No async callers exist for `get_ann_index()`.

---

## Test Verification

```
23 passed, 16 warnings in 0.87s  (probe_f228d_async_lock_seal)
21 passed, 23 warnings in 1.57s (probe_m218e_memory_integration_guard)
```

No regression in memory integration guard tests.

---

## Files Modified

| File | Change |
|------|--------|
| `coordinators/memory_coordinator.py` | `_lock` RLock→asyncio.Lock; async with; added Path+hashlib imports |
| `knowledge/analytics_hook.py` | Safety comment on `_worker_lock` |
| `knowledge/ann_index.py` | Safety comments on `_lock` and `_ann_index_lock` |
| `tests/probe_f228d_async_lock_seal/test_memory_coordinator_async_lock.py` | New tests (6) |
| `tests/probe_f228d_async_lock_seal/test_analytics_hook_no_false_start.py` | New tests (6) |
| `tests/probe_f228d_async_lock_seal/test_ann_index_sync_safe.py` | New tests (8) |

---

## Backups Created

All modified source files have `.bak_F228D_ASYNC_LOCK_SEAL` backups.