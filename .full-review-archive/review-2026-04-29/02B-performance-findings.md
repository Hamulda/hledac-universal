# Performance & Scalability Analysis — Hledac Universal

**Date:** 2026-04-29
**Scope:** `hledac/universal/` — Autonomous OSINT orchestrator for M1 MacBook 8GB UMA
**Reviewer:** Performance Engineering

---

## Executive Summary

M1 8GB UMA presents hard constraints. Found **3 CRITICAL**, **5 HIGH**, **4 MEDIUM** issues. Key bottlenecks: asyncio.run() M1 crash vectors, O(n) deque operations in hot path, DuckDB connection per-query overhead, and LanceDB batch memory pressure.

---

## Critical Issues (M1 Crash Risk)

### P0-1: `asyncio.run()` in Thread Pool — M1 Metal Crash Vector

**Files:**
- `utils/execution_optimizer.py:406` — `return asyncio.run(func())`
- `utils/execution_optimizer.py:413` — `future = _worker_exec.submit(asyncio.run, func())`
- `brain/inference_engine.py:442` — `return asyncio.run(coro)`

**Evidence:**
```python
# execution_optimizer.py:401-414
try:
    asyncio.get_running_loop()
except RuntimeError:
    # No running loop in this thread - create one with asyncio.run()
    # F196A: This runs in a worker thread, not the main thread's loop.
    return asyncio.run(func())  # ← CRASH on M1
# A loop is running in this thread - ...
import concurrent.futures
_worker_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1)
future = _worker_exec.submit(asyncio.run, func())  # ← CRASH on M1
```

```python
# inference_engine.py:439-446
try:
    asyncio.get_running_loop()
except RuntimeError:
    return asyncio.run(coro)  # ← CRASH on M1
loop = asyncio.get_running_loop()
return loop.run_until_complete(coro)  # ← SAFE path
```

**Impact:** Metal driver crash on Apple Silicon M1. `asyncio.run()` creates a nested event loop that conflicts with Metal's internal event loop.

**Fix:** Replace with `loop.run_until_complete(coro)` using the existing loop from the worker thread:
```python
# Safe M1 pattern
loop = asyncio.new_event_loop()
try:
    return loop.run_until_complete(coro)
finally:
    loop.close()
```

---

### P0-2: `deque.remove()` O(n) in Hot Path — `_dedup_hot_cache_order`

**File:** `knowledge/duckdb_store.py:6530`

**Evidence:**
```python
def _add_to_hot_cache(self, fp: str, finding_id: str) -> None:
    if fp in self._dedup_hot_cache:
        try:
            self._dedup_hot_cache_order.remove(fp)  # ← O(n) per MRU update
        except ValueError:
            pass
```

**Impact:** `_DEDUP_HOT_CACHE_MAX = 10_000`. With frequent cache hits, `remove()` walks the entire deque. At 10K entries, this is O(10000) per operation in the dedup hot path.

**Fix — Use `dict` for O(1) order tracking:**
```python
class OrderTracker:
    """O(1) LRU order tracking via dict + sequence counter."""
    def __init__(self, max_size: int):
        self._order: dict[str, int] = {}
        self._counter = 0
        self._max_size = max_size

    def touch(self, key: str) -> None:
        self._order[key] = self._counter
        self._counter += 1

    def evict_one(self) -> str | None:
        if not self._order:
            return None
        oldest = min(self._order, key=self._order.get)
        del self._order[oldest]
        return oldest
```

---

### P0-3: DuckDB `:memory:` Connection Per-Query Overhead

**File:** `knowledge/duckdb_store.py:6252`

**Evidence:**
```python
# Line 6252 — called for EVERY get_finding_envelope call
conn = duckdb.connect(str(self._db_path))  # ← new connection each time
try:
    result = conn.execute(query, [finding_id]).fetchone()
finally:
    conn.close()
```

**Impact:** DuckDB connection establishment has significant overhead (~1-5ms). If called in a loop for N findings, total overhead = N × 1-5ms.

**Fix — Use persistent connection or batch:**
```python
class DuckDBStore:
    def __init__(self, db_path: str | None):
        self._db_path = db_path
        self._conn_pool: dict[str, duckdb.DuckDBPyConnection] = {}

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        key = str(self._db_path)
        if key not in self._conn_pool:
            self._conn_pool[key] = duckdb.connect(key)
        return self._conn_pool[key]
```

---

## High Issues

### P1-1: LanceDB Batch Memory Pressure — Unbounded `batch_size`

**File:** `knowledge/lancedb_store.py:283-331`

**Evidence:**
```python
async def _embed_batch(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # batch_size=16 is small, but called repeatedly
        # No RAM check before embedding generation
```

**Impact:** On M1 8GB, embedding generation competes with MLX model memory. No `uma_budget.is_critical()` guard before embedding.

**Fix — Add RAM guard:**
```python
from utils.uma_budget import is_uma_critical

async def _embed_batch(self, texts: List[str], batch_size: int = 16) -> List[List[float]]:
    if is_uma_critical():
        logger.warning("Skipping embedding due to M1 critical memory pressure")
        return [[0.0] * self._embedding_dim for _ in texts]
    # ... existing logic
```

---

### P1-2: SprintScheduler 24 Lazy-Injected Dependencies

**File:** `runtime/sprint_scheduler.py:622-727`

**Evidence:**
```python
self._duckdb_read_con: Optional[Any] = None       # line 642
self._hermes_engine: Any = None                  # line 648
self._memory_manager: Any = None                 # line 649
self._ioc_scorer: Any = None                     # line 665
self._duckdb_store: Any = None                    # line 667
self._forensics_enricher: Any = None              # line 669
self._multimodal_enricher: Any = None            # line 672
self._ioc_graph: Any = None                      # line 675
self._prefetch_oracle: Any = None                # line 683
# ... 15 more
```

**Impact:** All initialized to `None`, resolved lazily on first access. Hard to reason about initialization order, impossible to validate at construction time. 7 `inject_*` methods.

**Fix — Use dataclass with `__post_init__` validation:**
```python
@dataclass
class SprintSchedulerDeps:
    duckdb_store: "DuckDBStore"
    hermes_engine: "HermesEngine"
    forensics_enricher: "ForensicsEnricher"
    multimodal_enricher: "MultimodalEnricher"
    # ... required deps

    def __post_init__(self):
        missing = [k for k, v in self.__dict__.items() if v is None]
        if missing:
            raise ValueError(f"Uninitialized deps: {missing}")
```

---

### P1-3: DuckDB `PRAGMA threads=2` — Underutilized Parallelism

**File:** `knowledge/duckdb_store.py:1345, 1359, 1367`

**Evidence:**
```python
conn.execute("PRAGMA threads=2")  # hardcoded, M1 has 4 efficiency cores
```

**Impact:** DuckDB parallel query execution limited to 2 threads. M1 MacBook has 4 efficiency + 4 performance cores.

**Fix:**
```python
import os
THREAD_COUNT = min(os.cpu_count() or 2, 8)
conn.execute(f"PRAGMA threads={THREAD_COUNT}")
```

---

### P1-4: `intelligence/document_intelligence.py:1319` — Unguarded `asyncio.run()`

**File:** `intelligence/document_intelligence.py:1318-1319`

**Evidence:**
```python
except RuntimeError:
    # No running loop - safe to use asyncio.run()
    forensics = asyncio.run(self._forensics.analyze_image(content))  # ← M1 risk
```

**Impact:** Same M1 Metal crash vector, but only triggered when no running loop exists.

**Fix:** Same as P0-1 — use `loop.run_until_complete()` pattern.

---

### P1-5: MLX `mx.eval([])` Missing Before `clear_cache` in Some Paths

**File:** `utils/mlx_cache.py:353-358`

**Evidence:**
```python
# Line 353-358 — canonical order is gc.collect → mx.eval([]) → clear_cache
if hasattr(mx.metal, 'clear_cache'):
    mx.metal.clear_cache()  # ← missing mx.eval([]) barrier
elif hasattr(mx, 'clear_cache'):
    mx.clear_cache()
```

**Impact:** On M1 8GB, lazy evaluation queue may not be flushed before cache clear. Could cause brief over-budget.

**Fix:**
```python
# Always use canonical order
gc.collect()
if hasattr(mx, 'eval'):
    mx.eval([])  # barrier — flush GPU queue
if hasattr(mx.metal, 'clear_cache'):
    mx.metal.clear_cache()
elif hasattr(mx, 'clear_cache'):
    mx.clear_cache()
```

---

## Medium Issues

### P2-1: `_metrics_history` Unbounded If `maxlen` Not Set

**File:** `layers/memory_layer.py:80`

**Evidence:**
```python
self._metrics_history: deque = deque()  # ← no maxlen
# ...
self._metrics_history.append(metrics)
if len(self._metrics_history) > self._max_history:
    self._metrics_history.popleft()
```

**Impact:** Manual eviction check works but `deque()` without `maxlen` can grow unbounded if eviction check is skipped. Also, manual popleft() is O(n).

**Fix — Use `maxlen`:**
```python
self._metrics_history: deque = deque(maxlen=self._max_history)
# Then simple: self._metrics_history.append(metrics) — auto-evicts
```

---

### P2-2: DuckDB `_file_conn` Not Thread-Safe for Concurrent Access

**File:** `knowledge/duckdb_store.py:1600`

**Evidence:**
```python
result = self._file_conn.execute(query, [limit]).fetchall()
# _file_conn is a single DuckDB connection, not thread-safe
```

**Impact:** If multiple threads access `_file_conn` concurrently, SQLite-style locking occurs or data corruption.

**Fix:** Use `duckdb.connect()` per worker thread, or use connection pool.

---

### P2-3: `PRAGMA threads=2` in `:memory:` Mode Useless

**File:** `knowledge/duckdb_store.py:1362-1367`

**Evidence:**
```python
self._persistent_conn = duckdb.connect(":memory:")
self._persistent_conn.execute("PRAGMA threads=2")  # ← :memory: is single-threaded
```

**Impact:** Misleading configuration. In-memory database doesn't benefit from parallelism.

**Fix:** Remove or set to 1.

---

### P2-4: UM1 Budget 6.0GB Warn / 6.5GB Critical — Tight on 8GB

**File:** `utils/uma_budget.py:60-62`

**Evidence:**
```python
_WARN_THRESHOLD_MB: int = 6_144      # 6.0 GB
_CRITICAL_THRESHOLD_MB: int = 6_656  # 6.5 GB
_EMERGENCY_THRESHOLD_MB: int = 7_168  # 7.0 GB
```

**Impact:** With macOS using ~2.5GB baseline, only 1.5GB for MLX + application before WARN, 0.5GB before CRITICAL. Hermes-3-3B-4bit needs ~2GB. Tight margin.

**Fix — Tighten thresholds for 8GB UMA:**
```python
_WARN_THRESHOLD_MB: int = 5_120       # 5.0 GB — earlier warning
_CRITICAL_THRESHOLD_MB: int = 5_632   # 5.5 GB
_EMERGENCY_THRESHOLD_MB: int = 6_144   # 6.0 GB
```

---

## Low Issues

### P3-1: `DuckDBStore` 6680 Lines — God Object

**File:** `knowledge/duckdb_store.py`

**Impact:** Single 6680-line file with 150+ methods. Hard to test, impossible to reason about.

**Fix:** Split by domain (findings, targets, sprints, analytics).

---

### P3-2: LanceDB `batch_size=16` — May Be Suboptimal

**File:** `knowledge/lancedb_store.py:283`

**Impact:** 16 is conservative. M1 8GB may handle larger batches for embedding inference.

**Fix:** Make batch_size configurable, start at 32.

---

## Summary Table

| ID | Severity | File | Issue | Est. Impact |
|----|----------|------|-------|--------------|
| P0-1 | CRITICAL | execution_optimizer.py:406,413 | `asyncio.run()` M1 crash | Metal crash |
| P0-2 | CRITICAL | duckdb_store.py:6530 | `deque.remove()` O(n) | 10K×10K=100M ops |
| P0-3 | CRITICAL | duckdb_store.py:6252 | Per-query connection | N×5ms overhead |
| P1-1 | HIGH | lancedb_store.py:283 | No RAM guard on batch | OOM on M1 |
| P1-2 | HIGH | sprint_scheduler.py:622 | 24 lazy deps | Init order bugs |
| P1-3 | HIGH | duckdb_store.py:1345 | `threads=2` hardcoded | Underutilized |
| P1-4 | HIGH | document_intelligence.py:1319 | `asyncio.run()` M1 risk | Metal crash |
| P1-5 | HIGH | mlx_cache.py:353 | Missing `mx.eval([])` | Brief over-budget |
| P2-1 | MEDIUM | memory_layer.py:80 | No `maxlen` on deque | Unbounded growth |
| P2-2 | MEDIUM | duckdb_store.py:1600 | `_file_conn` not thread-safe | Race condition |
| P2-3 | MEDIUM | duckdb_store.py:1362 | `threads=2` on `:memory:` | Misleading config |
| P2-4 | MEDIUM | uma_budget.py:60 | Thresholds too loose | Late warning |
| P3-1 | LOW | duckdb_store.py | 6680-line god object | Maintainability |
| P3-2 | LOW | lancedb_store.py:283 | batch_size=16 conservative | Throughput |

---

## Verification Commands

```bash
# Test asyncio.run patterns
rg -n 'asyncio\.run\(' --type py hledac/universal/

# Test deque.remove in hot path
rg -n 'dedup_hot_cache_order\.remove' hledac/universal/

# Test DuckDB connection per query
rg -n 'duckdb\.connect.*self\._db_path' hledac/universal/

# Test RAM guard before embedding
rg -n 'is_uma_critical|mx\.eval\(' hledac/universal/utils/mlx_cache.py
```
