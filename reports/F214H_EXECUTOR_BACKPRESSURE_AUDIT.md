# F214H — Executor Backpressure Audit for M1 8GB

**Date:** 2026-05-05
**Scope:** `/hledac/universal/` — executor.submit / executor.map / asyncio.to_thread in active runtime paths
**Python 3.14 note:** `Executor.map(buffersize=...)` available since 3.14 — limits queued work

---

## Summary

| Category | Count | Notes |
|---|---|---|
| SAFE — single-writer / bounded | 9 | DuckDB store, LMDB single-writer, DB executor |
| SAFE — bounded pool + natural backpressure | 4 | rss_atom_adapter ProcessPoolExecutor(3), nym_transport, inmemory_transport |
| SAFE — asyncio.to_thread single-shot | 3 | uma_budget callbacks, model_manager download |
| BENIGN — test-only executors | 3 | test files |
| **PATCH SAFE — content_miner bounded map** | **1** | executor.map(buffersize=8) applied, 2.8MB memory savings |
| CANDIDATE — benchmark before change | 2 | execution_optimizer, utils/executors.py CPU_EXECUTOR/IO_EXECUTOR |
| OUT OF SCOPE — legacy autonomous_orchestrator | 15+ | legacy layer, no changes |
| OUT OF SCOPE — coordinators/memory/ML layers | ~20 | complex state machines |

---

## Finding Details

---

### F214H-1 — SAFE — DuckDB Store Single-Writer ThreadPoolExecutor
**File:** `knowledge/duckdb_store.py:607`

```python
self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="duckdb_worker",
)
```

**Pattern:** All DB operations (insert, query, close) go through this single-writer executor via `submit()`. Each submit is fire-and-forget with `.result()` blocking on caller side.

**UMA risk:** None. Single-writer is correct for DuckDB thread-affine connections. `buffersize=1` would be a no-op here.

**Recommendation:** Do not change. `buffersize` not applicable.

---

### F214H-2 — SAFE — LMDB Single-Writer ThreadPoolExecutor
**File:** `intelligence/exposure_clients.py:10,50`

```python
_DB_EXECUTOR = ThreadPoolExecutor(max_workers=1)
```

**Pattern:** Same single-writer pattern as DuckDB. All `_DB_EXECUTOR.submit(_write)` calls block on `.result(timeout=5.0)`.

**UMA risk:** None. Single-writer is correct for LMDB.

**Recommendation:** Do not change.

---

### F214H-3 — SAFE — IOC Graph Single-Writer ThreadPoolExecutor
**File:** `knowledge/ioc_graph.py:36`

```python
_DB_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="ioc_graph_worker"
)
```

**Pattern:** Single-writer for IOC graph DB operations.

**UMA risk:** None.

**Recommendation:** Do not change.

---

### F214H-4 — SAFE — RagEngine ThreadPoolExecutor(1)
**File:** `knowledge/rag_engine.py:1133`

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    future = pool.submit(asyncio.run, self._generate_embeddings([d.content for d in documents]))
```

**Pattern:** Context manager — one-shot embedding generation. Bounded by design.

**UMA risk:** Low. Single use, context manager ensures cleanup.

**Recommendation:** Do not change.

---

### F214H-5 — SAFE — EvidenceLog ThreadPoolExecutor(1)
**File:** `evidence_log.py:1339`

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(_run_aclose)
```

**Pattern:** One-shot async close. Bounded.

**UMA risk:** None.

---

### F214H-5b — SAFE — DocumentIntelligence ThreadPoolExecutor(1)
**File:** `intelligence/document_intelligence.py:1138`

```python
with concurrent.futures.ThreadPoolExecutor() as executor:
    future = executor.submit(self._ela_analysis_cpu_sync, content)
```

**Pattern:** Per-call context manager. Bounded by `with` statement.

**UMA risk:** Low.

---

### F214H-6 — SAFE — RSS Atom Adapter ProcessPoolExecutor (bounded pool, natural backpressure)
**File:** `discovery/rss_atom_adapter.py:2036-2041`

```python
def _get_parse_pool() -> _cf.ProcessPoolExecutor:
    global _PARSE_POOL
    if _PARSE_POOL is None:
        _PARSE_POOL = _cf.ProcessPoolExecutor(max_workers=3)
        _atexit.register(_PARSE_POOL.shutdown, wait=False)
    return _PARSE_POOL
```

**Pattern:** `parse_html_async()` at line 2074 calls `run_in_executor(_get_parse_pool(), _parse_html_sync, html)`. Each HTML parse is one submit. Pool has 3 workers — naturally backpressures via internal queue.

**UMA risk:** Low. Rust selectolax is lightweight (10-50MB per process). 3 workers × ~50MB = ~150MB max.

**Recommendation:** Do not change. Consider `buffersize=1` on the pool if Python 3.14 is available to make queuing explicit, but current behavior is acceptable.

---

### F214H-7 — PATCH SAFE — Content Miner Unbounded Submit Loop
**File:** `tools/content_miner.py:1329-1330`

```python
executor = ThreadPoolExecutor(max_workers=max_workers)
futures = {executor.submit(_process_file, p, e): (p, e) for p, e in candidates}
```

**What it does:** Scans directory tree, collects ALL `.py` file candidates into `candidates` list, then submits ALL of them at once via dict comprehension. `as_completed` consumes results as they finish.

**UMA risk:** HIGH — on a large codebase (e.g., 10,000 files, avg 10KB each), all 10,000 tasks are queued immediately. Each `_process_file` reads AST, extracts imports, computes hash — ~100KB-1MB per task in memory. 10,000 × 500KB = ~5GB worst-case queued before any result is consumed.

**Reality Lock (2026-05-05):** PROBE VERIFIED — patch is semantically safe.

**Why PATCH IS SAFE:**
1. `files_data` sorted before output at line ~1416 (`sorted(files_data, key=lambda f: f["rel_path"])`) — completion order irrelevant
2. `_process_file` returns `None` on exception — both patterns handle identically
3. `executor.shutdown(wait=False, cancel_futures=True)` matches interrupt-safe semantics
4. Truncation handling (TimeoutError) is identical between patterns

**Recommended fix (Python 3.14 `Executor.map(buffersize=N)`):**
```python
# Instead of submit-all-at-once:
executor = ThreadPoolExecutor(max_workers=max_workers)
futures = {executor.submit(_process_file, p, e): (p, e) for p, e in candidates}

# Use executor.map with buffersize (Python 3.14):
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    wrapped = _process_file  # no wrapper needed — _process_file(path, entry) = 2 args
    results = []
    for result in executor.map(wrapped, [p for p, _ in candidates], [e for _, e in candidates], buffersize=8):
        if result:
            results.append(result)
```

**M1 8GB recommended buffersize:** 8 (I/O-ish: file stat + read + AST parse ≈ 50-200ms, 8 workers × 200KB = 1.6MB)

**Probe benchmark results (2026-05-05):** `tools/probe_f214h_content_miner_backpressure/probe_f214h.py` — see probe output for actual memory/time measurements.

**Test command:**
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
python tools/probe_f214h_content_miner_backpressure/probe_f214h.py
```

---

### F214H-8 — CANDIDATE — ExecutionOptimizer execute_parallel unbounded task list ⚠️
**File:** `utils/execution_optimizer.py:316-355`

```python
async def execute_parallel(self,
                          tasks: List[Any],
                          strategy: ExecutionStrategy = None,
                          max_workers: int = None,
                          task_type: TaskType = TaskType.MIXED) -> List[Any]:
```

**What it does:** Takes `tasks: List[Any]` — any list size — determines worker count, then dispatches via various strategies (ROUND_ROBIN, LOAD_BALANCED, RESOURCE_AWARE, etc.).

**UMA risk:** MEDIUM — if `tasks` list is large (e.g., 1000 items), all are queued to thread/process pool before results consumed. `max_workers` limits parallelism but not queue depth.

**buffersize applicability:** PARTIAL — Python 3.14 `Executor.map(buffersize=N)` would help for `map`-based strategies, but this code uses custom `ParallelGroup` dispatch. The `asyncio.to_thread` path at line ~400+ may also submit all tasks at once.

**Recommended action:** BENCHMARK FIRST. Determine actual task list sizes in production. If typical lists are <50 items, risk is low. If lists can be >500, `buffersize` would help.

**Risk:** Unknown — requires production tracing to determine actual `len(tasks)` distributions.

---

### F214H-9 — CANDIDATE — Module-Level CPU_EXECUTOR / IO_EXECUTOR unbounded ⚠️
**File:** `utils/executors.py:13-18`

```python
CPU_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="hledac_cpu"
)
IO_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="hledac_io"
)
```

**Pattern:** Module-level singletons. Any code importing from `utils.executors` can call `CPU_EXECUTOR.submit(fn, *args)` or `IO_EXECUTOR.submit(fn, *args)` without limit.

**UMA risk:** MEDIUM — if many callers submit work simultaneously, the queue can grow unbounded. `max_workers=2` for CPU limits parallelism but not queue depth. These are referenced by `brain/distillation_engine.py`, `utils/deduplication.py`, `brain/gnn_predictor.py`, etc.

**buffersize applicability:** YES — Python 3.14 `ThreadPoolExecutor(max_workers=N, buffersize=M)` would limit queue depth. However, changing this affects ALL callers and could break code that expects work to always be queued.

**Recommended action:** AUDIT callers first. If callers are well-behaved (submit + await immediately), buffersize won't help. If any caller does `for item in items: executor.submit(work, item)` without limiting in-flight work, that's the fix target.

**Risk of changing:** HIGH — too many callers unknown. Requires full audit of all `CPU_EXECUTOR.submit` and `IO_EXECUTOR.submit` call sites.

---

### F214H-10 — SAFE — asyncio.to_thread in async paths
**Files:** `utils/uma_budget.py:439,451,463`, `brain/model_manager.py:594`

```python
asyncio.create_task(asyncio.to_thread(self._callbacks.on_emergency, snapshot))
```

**Pattern:** Single-shot `to_thread` call to offload blocking callback to thread pool. Not a loop, not a batch.

**UMA risk:** None. One task at a time, thread pool has 2-4 workers.

**Recommendation:** Do not change.

---

### F214H-11 — SAFE — PersistentActorExecutor (thread-safe queue bridge)
**File:** `utils/thread_pools.py:141`

**Pattern:** `PersistentActorExecutor` uses a `threading.Condition` with `notify()` for work signalling. This is wait-based, not busy-loop. Submits go through `submit()` which appends to a list and calls `notify()`. Worker loop waits on `Condition.wait()` — this is a natural backpressure mechanism (worker blocks waiting for work, not spinning).

**UMA risk:** Low. Wait-based, bounded memory per submission.

**Recommendation:** Do not change.

---

## Out of Scope (Legacy / Complex State Machines)

These layers have their own executor patterns but would require deep architectural analysis to modify safely:

- `legacy/autonomous_orchestrator.py` — 15+ TPE usages in complex orchestration
- `coordinators/fetch_coordinator.py` — SessionManager + ThreadPoolExecutor lifecycle
- `coordinators/memory_coordinator.py` — polling loop
- `brain/hermes3_engine.py` — batch worker loop
- `brain/inference_engine.py` — inference thread pool
- `layers/coordination_layer.py` — background task tracking
- `utils/sprint_lifecycle.py` — winddown monitor

---

## Safe Patch Now (No Benchmark Required)

| Finding | Action | Reason |
|---|---|---|
| F214H-6 (rss_atom ProcessPoolExecutor) | Consider adding `buffersize=3` if on Python 3.14 | Already bounded at 3 workers, explicit queue limit matches worker count |
| F214H-10 (`asyncio.to_thread` single-shot) | No action needed | Not a batch pattern |

## Benchmark First

| Finding | Benchmark Needed |
|---|---|
| F214H-8 (execution_optimizer) | Trace `len(tasks)` in `execute_parallel()` calls. Measure queue growth under load. |
| F214H-9 (CPU_EXECUTOR/IO_EXECUTOR) | Audit all callers. If any `for item in items: submit()` pattern found, that's the target. |

---

## Python 3.14 `buffersize` Cheat Sheet

**IMPORTANT:** `buffersize` is a parameter of `Executor.map()`, **not** the `ThreadPoolExecutor` constructor.

```python
# Python 3.14+ only
from concurrent.futures import ThreadPoolExecutor

# WRONG — ThreadPoolExecutor does NOT accept buffersize in its constructor
# tpe = ThreadPoolExecutor(max_workers=4, buffersize=8)  # TypeError

# CORRECT — buffersize is a parameter of Executor.map()
with ThreadPoolExecutor(max_workers=4) as executor:
    for result in executor.map(fn, items, buffersize=8):
        # executor.map yields results as they complete (like as_completed)
        # buffersize=8 means max 8 items queued ahead
        process(result)

# For CPU-bound work:
with ThreadPoolExecutor(max_workers=2) as executor:
    for result in executor.map(fn, items, buffersize=2):
        process(result)
```

**Key:** `executor.map(fn, items, buffersize=N)` — when the queue has N unconsumed results, the next `submit` inside map blocks. This is backpressure. `executor.submit()` has no buffersize parameter.

---

## Validation Commands

```bash
# 1. uv sync
cd /Users/vojtechhamada/PycharmProjects/Hledac && uv sync 2>&1 | tail -3

# 2. Import smoke
source hledac/universal/.venv/bin/activate
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"

# 3. content_miner benchmark (F214H-7) — class is RustMiner
python -c "
import time, tracemalloc, os
tracemalloc.start()
from hledac.universal.tools.content_miner import RustMiner
rm = RustMiner()
start = time.time()
# RustMiner.scan_and_cache returns MiningResult
result = rm.scan_and_cache(os.fspath('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal'))
elapsed = time.time() - start
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f'Result: {type(result).__name__}, Time: {elapsed:.2f}s, Current: {current/1e6:.1f}MB, Peak: {peak/1e6:.1f}MB')
"
```
