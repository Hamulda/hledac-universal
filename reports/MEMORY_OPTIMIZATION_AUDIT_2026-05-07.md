# Memory Optimization Audit — 2026-05-07

**Scope:** hledac/universal/ | **Hardware:** MacBook Air M1 8GB UMA | **Python:** 3.14.4
**Already Completed (M218A-D):** gc.freeze/set_threshold, DuckDB memory_limit=400MB, LMDB readahead=False, MAX_LANE_REJECTIONS=1000

---

## RECOMMENDED TECHNIQUES

### B4: `mx.stream()` for batch scope

**File:** `core/mlx_embeddings.py:300`
**Change:**
```python
with mx.stream(mx.gpu):
    outputs = self._model(
        input_ids=inputs.input_ids,
        attention_mask=inputs.attention_mask
    )
```
**File:** `utils/ane_pipelines.py:embed_batch()` (line 190+)
**Change:** Same — wrap the model forward in `with mx.stream(mx.gpu):`

**Why:** Without `mx.stream()`, Metal buffers from completed batches are held until the next `mx.eval()` anywhere in the process. With stream context, buffers are scoped to the with-block and released immediately. Estimated: **-50-150MB UMA** from buffer reuse.

**Risk:** Low — pure scoping, no computation change. `mx.stream` is a no-op if GPU stream is already the default.

**Effort:** 30 minutes

**Status:** Not implemented — zero `mx.stream` usage found in codebase

---

### B3: Combined UMA guard (Metal + RSS) pre-batch

**File:** `embedding_pipeline.py:_generate_embeddings_chunk()` (line ~241)
**File:** `embedding_pipeline.py:streaming_embed_findings()` (line ~546)

**Change:** Before submitting an embedding batch:
```python
import psutil

if hasattr(mx, 'metal') and hasattr(mx.metal, 'get_active_memory'):
    active_mb = mx.metal.get_active_memory() / 1024**2
    rss_mb = psutil.Process().memory_info().rss / 1024**2
    # 6.5GB combined UMA ceiling (Metal buffers + compiled kernels + LanceDB ops + Python heap)
    if active_mb + rss_mb > 6656:
        mx.eval([])  # flush pending ops
        mx.metal.clear_cache()  # release Metal buffers
        # optionally downgrade batch size
```

**Why:** Fixed 2.5GB Metal threshold ignores that Metal also holds compiled kernels and LanceDB ops — not just embedding buffers. CombinedUMA guard (active_mb + rss_mb > 6656) captures the true UMA pressure picture. Threshold 6656MB is an initial calibration — adjust based on benchmark results. Prevents **-200-400MB spikes** by triggering cache flush before batch submission.

**Risk:** Low — fail-soft, no behavioral change to correct operation. Guard is advisory. Threshold tunable via benchmark.

**Effort:** 1-2 hours

**Status:** Not implemented — `get_active_memory()` exists in `mlx_memory.py:114` but only used for logging, not as pre-batch gate

---

### C2: Bounded `asyncio.Queue` for producer/consumer pipelines

**File:** `prefetch/prefetch_cache.py:27`
**Change:** `asyncio.Queue()` → `asyncio.Queue(maxsize=1000)`

**File:** `utils/async_utils.py:175`
**Change:** `asyncio.Queue()` → `asyncio.Queue(maxsize=max_concurrent * 2)`

**Why:** `PrefetchCache._write_queue` is unbounded — if writer loop lags (slow disk I/O), queue grows without limit. `async_utils.bounded_map` semaphore enforces concurrency but result queue has no bound — all completed results sit in memory until consumer drains them. **Concrete risk** of unbounded memory growth.

**Risk:** Low — back-pressure is correct semantics. Producers will block when queue is full, preventing memory explosion.

**Effort:** 30 minutes

**Status:** 3 unbounded queues found (2 in active code, 1 in legacy)

---

### C4: `asyncio.timeout()` instead of `asyncio.wait_for()`

**File:** `coordinators/agent_coordination_engine.py:362`
**Change:**
```python
# Before:
data = await asyncio.wait_for(executor(request), timeout=request.timeout)
# After:
async with asyncio.timeout(request.timeout):
    data = await executor(request)
# Exception type: asyncio.TimeoutError → TimeoutError
```

**File:** `coordinators/monitoring_coordinator.py:533`
**Change:** Same pattern — `async with asyncio.timeout(interval):`

**File:** `tools/bench_gc_314_runtime.py:251`
**Change:** Same pattern — `async with asyncio.timeout(timeout_s):`

**Why:** Python 3.11+ `asyncio.timeout()` is the structured replacement for `wait_for`. Avoids dangling tasks on timeout edge cases. Cleaner semantics. Exception type changes from `asyncio.TimeoutError` to `TimeoutError` — callers already catch specifically.

**Risk:** Low — behavioral change minimal. All three callsites already catch the specific exception type.

**Effort:** 1 hour

**Status:** Not implemented — 3 `asyncio.wait_for` callsites found

---

### F2: `PYTHONMALLOCSTATS=1` for allocator diagnostics

**File:** `tools/` benchmark runner scripts

**Change:** Add `PYTHONMALLOCSTATS=1` to pytest runs for memory-sensitive tests:
```bash
PYTHONMALLOCSTATS=1 pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q
```

Also add to CLAUDE.md as diagnostic option.

**Why:** Prints pymalloc arena stats on Python exit. Helps diagnose fragmentation after optimization sprints. No runtime overhead — output only on exit. **High diagnostic value, zero risk.**

**Risk:** None — diagnostic output only

**Effort:** 10 minutes

**Status:** Not implemented — not used anywhere

---

### E2: `tracemalloc` snapshot diff — opt-in only

**File:** `runtime/sprint_scheduler.py` — add in `run_sprint()`, gated by env var

**Change:**
```python
import tracemalloc

def run_sprint(...):
    if os.environ.get("HLEDAC_TRACEMALLOC"):
        tracemalloc.start(10)  # 10 frame depth limit
        snap_before = tracemalloc.take_snapshot()
    # ... sprint run ...
    if os.environ.get("HLEDAC_TRACEMALLOC"):
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()
        diff = snap_after.compare_to(snap_before, 'lineno')
        for stat in diff[:10]:
            logger.info(f"Alloc delta: {stat}")
```

**Why:** 5% overhead always-on in a full sprint with thousands of findings and HTTP fetches is non-trivial. Gating behind `HLEDAC_TRACEMALLOC` env var means diagnostic sprints are opt-in, production sprints are unaffected.

**Risk:** Low — env-gated, stdlib only, 10-frame depth limits memory used by snapshots.

**Effort:** 2-3 hours

**Status:** Not implemented — `benchmark_coordinator.py` uses tracemalloc but not wired into sprint lifecycle, and not env-gated

---

### E4: `gc.callbacks` for sprint-level GC telemetry

**File:** `runtime/sprint_scheduler.py` — add in `run_sprint()`

**Change:**
```python
import gc

_gc_stats = []

def _gc_callback(phase, info):
    _gc_stats.append({'gen': info['generation'], 'collected': info.get('collected', -1)})

def run_sprint(...):
    gc.callbacks.append(_gc_callback)
    gc.set_threshold(0, 0, 0)  # M218A already sets this
    try:
        # ... sprint run ...
    finally:
        gc.callbacks.remove(_gc_callback)
```

**Why:** M218A added gc.freeze/set_threshold but not per-collection callbacks. gc.callbacks show GC frequency and pause time during sprint runs, informing whether further GC tuning is needed. Complements M218A's reactive cleanup with proactive telemetry.

**Risk:** Low — must remove callbacks at teardown (try/finally ensures cleanup). Fail-safe if callback fails.

**Effort:** 1-2 hours

**Status:** Not implemented — no gc.callbacks usage in source

---

### F1: `PYTHONMALLOC=mimalloc` — Sprint 0 (system-level, no code change)

**Setup:** `brew install mimalloc` + add to `.env` or launch config

**Change:** In `.env` or shell profile:
```bash
export PYTHONMALLOC=mimalloc
```

**Why:** mimalloc provides ~5-15% faster allocation and better memory reuse for long-running multi-sprint sessions. Python 3.14 has official support via `PYTHONMALLOC` env var. **Zero code change** — pure env configuration. Addresses fragmentation on M1 UMA over extended sessions.

**Why not always applied:** Not installed (`brew list mimalloc` returns not installed). Requires `brew install mimalloc` first. Also: allocator change affects all Python memory — benchmark comparison required before committing to ensure no regressions with MLX Metal memory management.

**Risk:** Medium — requires `brew install` and benchmark validation. Could have unexpected interactions with MLX Metal memory management.

**Effort:** 10 minutes (env config) + benchmark validation (2-4 hours)

**Status:** Not installed — excluded from sprints until installed and benchmarked

**Recommendation:** Run as Sprint 0 before B3/B4 — install mimalloc, run e2e benchmark, compare RSS and throughput vs pymalloc before integrating into sprint plan.

---

## REMOVED FROM AUDIT (verified inapplicable)

### D3: `mmap_mode='r'` for np.load — REMOVED (zero impact)
- `vectors_file` is `.npz` (ZIP archive), NOT `.npy`
- `np.load()` with `mmap_mode='r'` only works on `.npy` files, not `.npz` archives
- Additionally, line 570 has `data[key].copy()` which forces Python heap allocation regardless of mmap
- **No impact on this codebase** — confirmed by code inspection

---

## PRIORITY SUMMARY

| Tech | Impact | Risk | Effort | When |
|------|--------|------|--------|------|
| B4: mx.stream() | -50-150MB UMA | LOW | 30 min | Sprint 1 |
| B3: Combined UMA guard | -200-400MB spike | LOW | 1-2h | Sprint 1 |
| C2: Bounded Queue | unbounded prevention | LOW | 30 min | Sprint 2 |
| C4: asyncio.timeout | correctness | LOW | 1h | Sprint 2 |
| F2: PYTHONMALLOCSTATS | diagnostic | NONE | 10 min | Sprint 3 |
| E2: tracemalloc diff (opt-in) | observability | LOW | 2-3h | Sprint 3 |
| E4: gc.callbacks | observability | LOW | 1-2h | Sprint 4 |
| F1: mimalloc | allocator speedup | MEDIUM | 10min + benchmark | Sprint 0 (pre-sprint) |

---

## SPRINT PLAN

### Sprint 0: mimalloc benchmark (before main sprints)
**Prerequisite:** `brew install mimalloc`
**Action:** Run e2e benchmark with `PYTHONMALLOC=mimalloc` vs default. Compare RSS and throughput.
**If benchmark passes:** Add `PYTHONMALLOC=mimalloc` to `.env`, move to Sprint 1
**If benchmark fails:** Document reason, skip F1

### Sprint 1: Metal Memory Discipline (B4, B3)
**Theme:** Proactive Metal memory management before batch submission and buffer scoping.
**Changes:**
1. `core/mlx_embeddings.py:300` — wrap model forward in `with mx.stream(mx.gpu):`
2. `utils/ane_pipelines.py:embed_batch()` — same mx.stream wrapping
3. `embedding_pipeline.py` — add combined UMA guard (active_mb + rss_mb > 6656) pre-batch
**Smoke test:** `pytest hledac/universal/tests/test_sprint8ay_mlx_memory.py -q && pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q`

### Sprint 2: Asyncio Correctness + Queue Bounds (C2, C4)
**Changes:**
1. `prefetch/prefetch_cache.py:27` — `asyncio.Queue(maxsize=1000)`
2. `utils/async_utils.py:175` — `asyncio.Queue(maxsize=max_concurrent * 2)`
3. `coordinators/agent_coordination_engine.py:362` — `async with asyncio.timeout()`
4. `coordinators/monitoring_coordinator.py:533` — same timeout pattern
**Smoke test:** `pytest hledac/universal/tests/probe_f214h_content_miner_backpressure/ -q && pytest hledac/universal/coordinators/ -q`

### Sprint 3: Diagnostics + Opt-in Observability (F2, E2)
**Changes:**
1. Add `PYTHONMALLOCSTATS=1` to benchmark runner scripts (F2)
2. Add env-gated tracemalloc snapshot diff in `sprint_scheduler.py:run_sprint()` (E2)
**Smoke test:** `PYTHONMALLOCSTATS=1 pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q 2>&1 | grep -i arena`

### Sprint 4: GC Telemetry (E4)
**Changes:**
1. `runtime/sprint_scheduler.py` — add gc.callbacks for GC frequency/pause tracking
**Smoke test:** `pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q && pytest hledac/universal/tests/test_sprint8ay_mlx_memory.py -q`

---

## ALREADY WELL-OPTIMIZED (evidence)

| Technique | Evidence |
|-----------|----------|
| M218A gc.freeze/set_threshold/MLX unload | `utils/mlx_memory.py:236-237` — `set_memory_limit` + hasattr guard |
| M218B DuckDB memory_limit=400MB | `knowledge/duckdb_store.py:1413-1437` — all 3 conns, `enable_object_cache=false` |
| M218C LMDB readahead=False | `tools/lmdb_kv.py:103,114,286` — `readahead=False` + M218C comment |
| M218D MAX_LANE_REJECTIONS=1000 | `runtime/sprint_scheduler.py:51` — bound + eviction at line 5047-5049 |
| Future annotations | sprint_scheduler, resource_governor, duckdb_store, fetch_coordinator all confirmed |
| No unbounded dict caches | All caches bounded (LRU, TTL, max_entries, explicit eviction) |
| LMDB zero-copy memoryview | `lmdb_kv.py:129-134` — `buffers=True`, orjson accepts memoryview directly |
| Embedding batch already SoA | `streaming_embedder.py:164` — `(batch_size, 256) float32` numpy array |
| gather bounded + return_exceptions | All gather calls use fixed-size lists, GHOST_INVARIANTS enforced |
| resource.getrusage in benchmarks | `windup_engine.py:221`, `probe_f214int_interpreter_pool.py:76` |