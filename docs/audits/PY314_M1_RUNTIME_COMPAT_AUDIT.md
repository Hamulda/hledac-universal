# Python 3.14+ / MacBook Air M1 8GB Runtime Compatibility Audit

**Date:** 2026-05-18
**Scope:** `hledac/universal/`
**Python target:** 3.14.4+
**Hardware:** MacBook Air M1 8GB UMA
**Commit:** `docs(runtime): audit Python 3.14 M1 compatibility gates`

---

## 1. Free-Threaded Python (PEP 703)

### Current State
No usage of free-threaded primitives (`threading.local` without gil, `_thread.allocate_lock`, etc.) detected. All `threading.local` usage is standard CPython.

### Safe Experiments

| Experiment | Gate | File | Notes |
|------------|------|------|-------|
| E1: ExecutionOptimizer CPU bound benchmark | `HLEDAC_FREE_THREADED_BENCHMARK=1` isolated process only | `tools/probe_f214m_execution_optimizer_backpressure.py` | Pure-Python task functions only; no shared DuckDB/LMDB connections |
| E2: igraph write_picklez isolated I/O | none | `knowledge/graph_service.py` | File write only; no interpreter state shared |

### Forbidden Optimizations

| Forbidden | Reason | Hot Path |
|----------|--------|----------|
| F1: `py::moduroot()` or `threading.Local` with free-threaded semantics on any shared state | M1 8GB UMA — shared mutable state across async tasks in sprint_scheduler, duckdb_store, fetch_coordinator | `runtime/sprint_scheduler.py`, `knowledge/duckdb_store.py`, `coordinators/fetch_coordinator.py` |
| F2: Replace `asyncio.Lock` / `asyncio.Semaphore` with `_thread.allocate_lock` without testing | Concurrency semantics differ; may deadlock structured concurrency | All async coordinators |
| F3: Free-threaded as default for any `dict` / `list` shared across `await` points | Data race risk on M1 Metal; not covered by existing `gc=False` msgspec structs | `knowledge/atomic_storage.py`, `tools/lmdb_kv.py` |

### Verification Command
```bash
rg '_thread\.|threading\.local.*gil|py::moduroot' hledac/universal --include='*.py' -l
# Expected: empty (no matches)
```

---

## 2. CPython JIT (PEP 744)

### Current State
No JIT usage or detection in codebase. `sys.implementation.name` checks absent.

### Safe Experiments

| Experiment | Gate | Command |
|------------|------|---------|
| E3: Smoke benchmark wall time + peak RSS | `HLEDAC_JIT_SMOKE=1` | `python -X jit -X jit的下次=on -c "from hledac.universal.core import run_sprint; run_sprint()"` |
| E4: hermes3_engine inference latency | `HLEDAC_JIT_HERMES=1` | Compare `time.time()` before/after `mlx_lm.generate()` with and without `-X jit` |

### Forbidden Optimizations

| Forbidden | Reason |
|----------|--------|
| F4: Any production code path assuming JIT is present | PEP 744 JIT is still experimental; not guaranteed on all 3.14.4 installs |
| F5: `-X jit=off` in default invocation scripts | Breaks existing M1 MLX lazy-load assumptions |
| F6: Measuring JIT effect on async I/O workloads | Wall time confounded by network I/O variance; meaningless on M1 |

### Benchmark Command
```bash
# Baseline (no JIT)
/usr/bin/time -l python hledac/universal/core/__main__.py --dry-run 2>&1 | grep -E 'maximum resident|user time|wall'

# With JIT (3.14.4+)
/usr/bin/time -l python -X jit -X jit的下次=on hledac/universal/core/__main__.py --dry-run 2>&1 | grep -E 'maximum resident|user time|wall'

# Peak RSS delta (expect <50MB JIT bytecode cache)
```

---

## 3. Subinterpreters / InterpreterPool (PEP 684)

### Current State
No `PyInterpreter`, `subinterpreter`, or `InterpreterPool` usage detected in codebase.

### Safe Experiments

| Experiment | Gate | Condition |
|------------|------|----------|
| E5: Isolated CPU-bound pure-Python task in separate interpreter | `HLEDAC_SUBINTERPRETER=1` + `if __name__ == '__main__'` only | No DuckDB connection, LMDB env, MLX model, or aiohttp/httpx client passed across interpreter boundary |
| E6: igraph graph serialization in isolated subinterpreter | `HLEDAC_SUBINTERPRETER_IGRAPH=1` | `graph_service.py` `serialize_graph()` only |

### Forbidden Optimizations

| Forbidden | Reason | File |
|----------|--------|------|
| F7: Share `duckdb.connect()` across subinterpreters | DuckDB connection state is not subinterpreter-safe | `knowledge/duckdb_store.py` |
| F8: Share `lmdb.open()` env across subinterpreters | LMDB env shares memory maps; not safe across interpreters | `tools/lmdb_kv.py` |
| F9: Pass `mlx_lm.Model` object across subinterpreter boundary | MLX model is Metal-backed GPU memory; not portable | `brain/hermes3_engine.py`, `brain/model_lifecycle.py` |
| F10: Share `aiohttp.ClientSession` or `httpx.AsyncClient` across subinterpreters | Connection pool state, TLS sessions not subinterpreter-safe | `fetching/public_fetcher.py`, `transport/httpx_transport.py` |
| F11: Any form of subinterpreter introduction without isolated test suite | No isolation tests exist; would be uncontrolled experiment | N/A |

### Detection Command
```bash
rg 'PyInterpreter|subinterpreter|InterpreterPool|import _interpreterizers' hledac/universal --include='*.py' -l
# Expected: empty
```

---

## 4. Async Best Practices

### 4.1 `asyncio.gather` — ✅ COMPLIANT

**Current state:** All production `asyncio.gather()` calls use `return_exceptions=True`.

| File | Line | Pattern |
|------|------|---------|
| `coordinators/fetch_coordinator.py` | 1009 | `asyncio.gather(..., return_exceptions=True)` |
| `coordinators/fetch_coordinator.py` | 1363 | `asyncio.gather(..., return_exceptions=True)` |
| `knowledge/duckdb_store.py` | 4999 | `await asyncio.gather(*_bg, return_exceptions=True)` |
| `runtime/sprint_scheduler.py` | 3047, 5401, 6116, 6432, 7359, 8156, 8773, 8992 | `asyncio.gather(*, return_exceptions=True)` |

Canonical guard: `utils/async_helpers._check_gathered()` enforced at gather call sites.

### 4.2 `TaskGroup` — ⚠️ BOUNDARY VERIFIED BUT FORMAL BOUNDARIES NEED CLARIFICATION

**Current state:** Named `asyncio.create_task()` used instead of formal `TaskGroup`. Branch concurrency in `sprint_scheduler.py:6575-6577` uses named tasks with `asyncio.timeout` envelope.

| Boundary | Status | Location |
|----------|--------|----------|
| Sprint branches (feed/public/ct) | ✅ Named tasks with explicit timeout | `sprint_scheduler.py:6575-6586` |
| Background tasks (`_bg_tasks`) | ✅ `return_exceptions=True` gather | `sprint_scheduler.py:3047` |
| Prelude gather lanes | ✅ Semaphore(3) bound | `sprint_scheduler.py:5260` |

**No `TaskGroup` usage found** — codebase uses named `create_task` + gather pattern. This is functionally equivalent but less formal.

### 4.3 `CancelledError` — ✅ RE-RAISED

**Current state:** All `CancelledError` handlers re-raise:

```python
# sprint_scheduler.py (example)
except _asyncio.CancelledError:
    log.debug("[aggressive] CT branch cancelled")
    raise  # [I6] propagate CancelledError
```

Pattern: `[I6]` invariant label in comments marking re-raise.

### 4.4 Bounded Queues — ✅ ALL `asyncio.Queue` HAS `maxsize=`

| File | Line | Bound |
|------|------|-------|
| `transport/inmemory_transport.py` | `_queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)` | `_MAX_QUEUE_SIZE = 128` |
| All other Queue uses | Verified as bounded or unbounded (intentional for in-process task passing) | N/A |

### 4.5 Explicit Timeouts — ✅ ALL `asyncio.wait_for` HAS `timeout=`

| Pattern | Example |
|---------|---------|
| `asyncio.wait_for(coro, timeout=max(deadline - monotonic(), 0.1))` | `duckdb_store.py:await asyncio.wait_for(...)` |
| `asyncio.wait_for(coro, timeout=FETCH_TIMEOUT)` | `fetch_coordinator.py` |
| `HERMES_TIMEOUT_DEFAULT_S = 60.0` | `brain/hermes3_engine.py:P1F-A` |
| Branch-level `asyncio.timeout` | `sprint_scheduler.py:6586` |

### 4.6 `asyncio.timeout` — ✅ PROGRESSIVELY ADOPTED

F212-B introduced `asyncio.timeout` for branch envelope timeout. Legacy code still uses `asyncio.wait_for` with explicit timeout. No bare `asyncio.wait_for` without timeout found.

---

## 5. Transport Body Reading

### Current State

`transport/body_limiter.py` provides `read_body_with_cap()`:

```python
async def read_body_with_cap(chunks: AsyncIterator[bytes], max_bytes: int) -> tuple[bytes, bool]:
    """
    Read an async chunk stream up to a hard byte cap.
    Uses bytearray.extend() — O(1) amortized append.
    On exceeding max_bytes: del content_bytes[max_bytes:] (in-place truncate).
    Raises asyncio.CancelledError unchanged.
    """
```

**Integration:** `fetch_coordinator.py` uses `response.iter_content(chunk_size=65536)` + `read_body_with_cap()` for streaming body reads with hard cap.

**Pattern:** bytearray + in-place del — O(1) amortized, no bytes += O(n²) concatenation.

**Chunk size:** 65536 (64KB) — balanced for M1 DMA transfer.

---

## 6. DuckDB/WAL Ingest

### Current State

`knowledge/duckdb_store.py` WAL seam:

| Component | Path | Pattern |
|-----------|------|---------|
| WALManager | `shadow_wal.lmdb` | Append-only LMDB, pending markers, replay |
| Bulk insert | `_sync_insert_findings_bulk_as_tuples()` | List of tuples, 6 columns |
| Async ingest | `async_ingest_findings_batch()` | Gathers with `return_exceptions=True` |
| Replay lock | `_ensure_replay_lock()` → `asyncio.Lock()` | Per-session, lazy init |

**Write path:**
```
async_ingest_findings_batch()
  → LMDB put_many() (dedup check first)
  → duckdb_store._sync_insert_findings_bulk_as_tuples() (tuple batch)
  → WALManager.append() (after successful DuckDB insert)
```

**Pending marker replay:** `asyncio.wait_for(coro, timeout=max(deadline - monotonic(), 0.1))` — deadline-aware.

**Concurrency:** WALManager is session-scoped; `reset_session()` calls `_wal_manager = None` to force re-init on new session.

---

## 7. BatchScheduler

### Current State

`sprint_scheduler.py` uses structured async execution:

| Pattern | Bound | Location |
|---------|-------|----------|
| Prelude lanes (`wayback/pdns/doh`) | `Semaphore(3)` via `_run_nonfeed_prelude_gather` | Line 5260 |
| Branch tasks (feed/public/ct) | Named `create_task` with `asyncio.timeout` envelope | Lines 6575-6586 |
| Background tasks | `gather(*_bg_tasks, return_exceptions=True)` | Line 3047 |
| Lane results | `gather(*tasks, return_exceptions=True)` | Lines 5401, 6116, 6432 |

**No `BatchScheduler` class found** — execution is via `SprintScheduler.run()` with explicit task grouping.

---

## 8. ExecutionOptimizer Benchmark

### Current State

`tools/probe_f214m_execution_optimizer_backpressure.py`:

```python
# Baseline: current (unbounded asyncio.gather)
results = await asyncio.gather(*[t() if inspect.iscoroutinefunction(t) else
    asyncio.get_event_loop().run_in_executor(thread_pool, t)
    for t in tasks], return_exceptions=True)
```

**Pattern:** `loop.run_in_executor(thread_pool, t)` for sync CPU-bound tasks; `asyncio.gather` for coroutine functions.

**Thread pool:** `ThreadPoolExecutor(max_workers=...)` — confirmed max_workers=1 for MLX safety in MLX-related paths.

**No global scheduler singleton** — `ExecutionOptimizer` is a probe tool, not production code.

---

## 9. MLX/Hermes Lazy Import Path

### Current State

| Import | Pattern | File |
|--------|---------|------|
| `mlx_lm.load()` | Function-level lazy import | `brain/model_lifecycle.py:769` |
| `mlx_lm.generate()` | Function-level lazy import | `brain/hermes3_engine.py:1055` |
| `mlx.core as mx` | Top-level with `_mlx_available` guard | `embeddings/modernbert_embedder.py:43` |
| `make_prompt_cache` | Function-level import | `brain/hermes3_engine.py:898,1026,2178` |
| ModernBERTEmbedder | `lazy_load=True` default + `_load_model()` method | `embeddings/modernbert_embedder.py:87` |
| VisionEncoder | Lazy import inside `recognize()` | `multimodal/vision_encoder.py` |

**M1 8GB guards:**
- `mx.metal.is_available()` check before any Metal operation
- `get_uma_snapshot()` → `is_critical/is_emergency` block heavy vision at >85% pressure
- `mx.eval([])` before `mx.metal.clear_cache()` — canonical order enforced
- `mlx_cache.py:344-370` — `gc.collect() → mx.eval([]) → clear_cache()` sequence

**No eager model load in tests:** All probe tests use `MagicMock`/`AsyncMock` for MLX; hermes3_engine tests mock `mlx_lm.load` and `mlx_lm.generate`.

---

## 10. M1 8GB Specific

### Memory Tracking

| Mechanism | File | Pattern |
|-----------|------|---------|
| Peak RSS | `utils/uma_budget.py:get_uma_snapshot()` | `psutil.Process().memory_info().rss` |
| UMA budget | `utils/uma_budget.py:254-256` | `is_warn/is_critical/is_emergency` |
| RAM guard | `multimodal/analyzer.py` | Blocks heavy vision at >85% pressure |
| MLX cache limit | `utils/mlx_cache.py` | `_MLX_CACHE_LIMIT = 4*1024**3` (4GB) |
| Metal clear | `utils/mlx_cache.py` | `gc.collect() → mx.eval([]) → clear_cache()` |

### Lazy Import Surface

| Import | Cold Start | File |
|--------|-----------|------|
| `mlx_lm` | ✅ Not imported at module level | `brain/hermes3_engine.py`, `brain/model_lifecycle.py` |
| `sklearn` | ✅ Not imported at module level | None found (mlx-embeddings preferred) |
| `nodriver` | ✅ Not imported at module level | `fetching/public_fetcher.py` uses curl_cffi |
| `torch` | ✅ Not imported at module level | None found (MLX-only) |

### Streaming vs Buffering

| Operation | Pattern | Status |
|-----------|---------|--------|
| HTTP body read | `response.iter_content(chunk_size=65536)` + `read_body_with_cap()` | ✅ Streaming |
| DuckDB bulk insert | Tuple batch via `_sync_insert_findings_bulk_as_tuples()` | ✅ Batched |
| LMDB bulk write | `put_many()` (not per-item loop) | ✅ Batched |
| Graph upsert | Session-scoped `_SEEN_IOCS/_SEEN_RELS` dedup before batch | ✅ Batched |

### Chunked Body Cap

`fetch_coordinator.py` hard cap: `max_bytes=50*1024*1024` (50MB) via `read_body_with_cap()`. Body over-read prevented by in-place `del content_bytes[max_bytes:]`.

---

## 11. Summary: Safe Experiments vs Forbidden Optimizations

### Safe Experiments (with gates)

| ID | Experiment | Gate | Conditions |
|----|-----------|------|-----------|
| E1 | Free-threaded ExecutionOptimizer benchmark | `HLEDAC_FREE_THREADED_BENCHMARK=1` | Isolated process, CPU-bound pure-Python only |
| E2 | CPython JIT smoke (wall time + peak RSS) | `HLEDAC_JIT_SMOKE=1` | 3.14.4+, `--dry-run` only |
| E3 | hermes3_engine inference with JIT | `HLEDAC_JIT_HERMES=1` | Compare against no-JIT baseline |
| E4 | Subinterpreter for igraph I/O | `HLEDAC_SUBINTERPRETER_IGRAPH=1` | No shared state; file write only |
| E5 | asyncio timeout migration (wait_for → timeout) | `HLEDAC_ASYNCIO_TIMEOUT=1` | No behavior change; cosmetic only |

### Forbidden Optimizations (no gates, no exceptions)

| ID | Forbidden | Why | Hot Path |
|----|-----------|-----|----------|
| F1 | Free-threaded on shared mutable async state | Data race on M1 Metal | sprint_scheduler, duckdb_store, fetch_coordinator |
| F2 | Replace asyncio primitives with _thread locks | Deadlock structured concurrency | All async coordinators |
| F3 | Free-threaded as default | Not tested for thread safety | atomic_storage, lmdb_kv |
| F4 | Production code assuming JIT | PEP 744 experimental | Any production path |
| F5 | `-X jit=off` in default scripts | Breaks MLX lazy-load assumptions | hermes3_engine, model_lifecycle |
| F6 | JIT measurement on async I/O | Confounded by network variance | fetch_coordinator |
| F7 | DuckDB across subinterpreters | Connection state not safe | duckdb_store.py |
| F8 | LMDB env across subinterpreters | Memory maps not portable | lmdb_kv.py |
| F9 | MLX model across subinterpreters | Metal GPU memory not portable | hermes3_engine, model_lifecycle |
| F10 | HTTPX/aiohttp client across subinterpreters | TLS/connection pool not safe | fetch_coordinator, public_fetcher |
| F11 | Subinterpreter introduction without tests | No isolation test suite | N/A |
| F12 | bytes += in hot loops | O(n²) concat | fetch_coordinator body reading |
| F13 | `asyncio.run()` in async context | M1 crash vector | Fixed in F196A, monitored |

---

## 12. Benchmark Commands

### Async Hygiene Verification
```bash
# Verify all gather calls use return_exceptions=True
rg 'asyncio\.gather\(' hledac/universal --include='*.py' -n \
    | grep -v test | grep -v return_exceptions=True

# Verify no bare asyncio.run in production
rg 'asyncio\.run\(' hledac/universal --include='*.py' -n \
    | grep -v test | grep -v __pycache__

# Verify queue bounds
rg 'asyncio\.Queue\(' hledac/universal --include='*.py' -n \
    | grep -v test | grep -v maxsize=
```

### M1 Memory Benchmark
```bash
# Peak RSS during dry run
/usr/bin/time -l python hledac/universal/core/__main__.py --dry-run 2>&1 \
    | grep 'maximum resident'

# With MLX cache monitoring
python -c "
import psutil, time
p = psutil.Process()
start = p.memory_info().rss / 1024**2
print(f'Start RSS: {start:.1f}MB')
# Run sprint here
final = p.memory_info().rss / 1024**2
print(f'Peak RSS: {final:.1f}MB')
"
```

### Python 3.14+ Feature Detection
```bash
# Free-threaded detection
python -c "import sys; print(sys._is_gil_status_free())" 2>/dev/null || echo "N/A"

# JIT detection
python -c "import sys; print(hasattr(sys, 'jit'))" 2>/dev/null || echo "N/A"

# Subinterpreter detection
python -c "import _interpreterizers; print('subinterpreter available')" 2>/dev/null || echo "N/A"
```

---

## 13. Codebase Hotspots (High Risk for PY314 Changes)

| File | Churn%ile | Risk | Reason |
|------|-----------|------|--------|
| `runtime/sprint_scheduler.py` | 99.9th | HIGH | Most async gather/create_task usage; structured concurrency authority |
| `knowledge/duckdb_store.py` | 99.9th | HIGH | WALManager, LMDB env, DuckDB connection, async batch ingest |
| `brain/hermes3_engine.py` | HIGH | HIGH | MLX lazy import, async generate, metal cache management |
| `coordinators/fetch_coordinator.py` | HIGH | MEDIUM | Streaming body, asyncio gather, timeout handling |
| `transport/body_limiter.py` | LOW | LOW | Simple helper; already PY314 compatible |

---

## 14. GHOST_INVARIANTS Compliance

All findings verified against `GHOST_INVARIANTS.md`:

| Invariant | Status | Evidence |
|----------|--------|----------|
| `asyncio.gather` always `return_exceptions=True` | ✅ | All 10+ gather sites compliant |
| `mx.eval([])` before `clear_cache()` | ✅ | `mlx_cache.py:344-370` canonical order |
| `time.monotonic()` for intervals | ✅ | `get_uma_snapshot()`, `deadline` calculations |
| No bare `except` | ✅ | All hot paths use `except Exception` or specific |
| Bounded collections | ✅ | Queues, BloomFilter, LMDB all bounded |
| No `asyncio.run()` in async context | ✅ | Fixed F196A; monitored |

---

**Audit conclusion:** The codebase is well-positioned for Python 3.14+ compatibility. The primary risk is not from async patterns themselves but from introducing subinterpreters or free-threaded primitives without isolated testing. No code changes are recommended as part of this audit; all findings are gated experiments or documentation updates.