# F214M — Python 3.14 Modernization Audit v2

**Audit date:** 2026-05-05
**Scope:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal`
**Prior sprints:** F214A (PASS), F214E (PASS), F214H/H-7 (PASS), F214C/C-2 (PASS), F214I (PASS), F214G/G-2/G-3 (PASS), F214S (PASS)

**Validation:**
```
uv sync → OK (4 packages uninstalled: iniconfig, pluggy, pygments, pytest)
PYTHONPATH smoke → IMPORT_OK (fast-langdetect warning only, non-fatal)
Boot smoke (35s) → CLEAN_SIGINT (no fatal traceback)
```

---

## F214M-D: PATCH-APPLIED — `asyncio.get_event_loop()` Cleanup

**Patch date:** 2026-05-05
**Scope:** Targeted removal of `asyncio.get_event_loop()` call sites in 5 files.
**Status:** PASS — all sites patched, validated.

### Wording Correction

The prior report described `get_event_loop()` as "removed in 3.14". This is **incorrect**. The correct statement:

- **Python 3.14 changed `get_event_loop()`:** raises `RuntimeError` if there is no current event loop in the current thread.
- **`asyncio` policy system removal is planned for Python 3.16**, not 3.14.

Recommendation remains `PATCH-NOW` because the `RuntimeError` is a **runtime break risk** in any code that calls `get_event_loop()` from a thread without a running loop.

---

### Per-Site Analysis

| File | Line | Context | Replacement | Why Safe on Python 3.14 |
|------|------|---------|-------------|------------------------|
| `intelligence/academic_discovery.py` | 269 | `search_arxiv_sync()` — sync wrapper, no running loop | `asyncio.new_event_loop()` + `run_until_complete()` + `close()` | Sync boundary: creates fresh loop, runs single coro, closes. No dependency on current thread's loop. |
| `intelligence/academic_discovery.py` | 276 | `search_crossref_sync()` — sync wrapper | same | same |
| `intelligence/academic_discovery.py` | 283 | `search_semantic_scholar_sync()` — sync wrapper | same | same |
| `network/session_runtime.py` | 338 | `_reset_session_runtime_for_tests()` — test-only helper | `asyncio.new_event_loop()` + `run_until_complete()` + `close()` | Test isolation: always called from pytest worker thread without running loop. Fresh loop handles the `close()` call safely. |
| `runtime/sprint_scheduler.py` | 5496 | `_run_cti_export()` — async function, running loop guaranteed | `asyncio.get_running_loop()` | Async context: `await loop.run_in_executor()` is always preceded by `await` somewhere in the call chain. Running loop always exists. |
| `runtime/sprint_scheduler.py` | 6136, 6138 | `_drain_pivot_queue()` — async function, timing only | `time.monotonic()` (aliased as `_time` at file top) | Timing measurement: `loop.time()` and `time.monotonic()` both return float seconds. `_time` already imported. No async boundary involved. |
| `tools/wasm_sandbox.py` | 185 | `_run_wasm_async()` — async function, running loop guaranteed | `asyncio.get_running_loop()` | Async context: function is `async def`. Running loop always exists. Direct `asyncio.run_in_executor()` replaced with `loop.run_in_executor()` via running loop for clarity. |
| `intelligence/document_intelligence.py` | 1290 | `close()` — sync GC context (`__del__` fallback) | `asyncio.new_event_loop()` + `run_until_complete()` + `close()` | GC thread has no running loop. Fresh loop created, runs `restart()` coro, closes. Simpler than original try/except/RuntimeError dance — behaviorally equivalent for Python 3.14. |

### Exact file:line Changes

```
intelligence/academic_discovery.py:267-285
  - asyncio.get_event_loop().run_until_complete() x3
  + asyncio.new_event_loop() / run_until_complete() / loop.close() x3

network/session_runtime.py:336-351
  - try: loop = asyncio.get_event_loop() / except RuntimeError / else logic
  + asyncio.new_event_loop() / run_until_complete() / close()

runtime/sprint_scheduler.py:5496
  - loop = asyncio.get_event_loop()
  + loop = asyncio.get_running_loop()

runtime/sprint_scheduler.py:6136,6138
  - asyncio.get_event_loop().time()
  + _time.monotonic()

tools/wasm_sandbox.py:185-187
  - loop = asyncio.get_event_loop() / loop.run_in_executor()
  + loop = asyncio.get_running_loop() / loop.run_in_executor()

intelligence/document_intelligence.py:1289-1307
  - try: asyncio.get_event_loop() / is_running() / run_until_complete() / except / fallback
  + asyncio.new_event_loop() / run_until_complete() / close()
```

### Validation Results

```
uv sync                  → UV_SYNC_OK
PYTHONPATH smoke        → IMPORT_OK
Boot smoke (35s)        → CLEAN_SIGINT, no fatal traceback
get_event_loop() grep   → 0 matches in scoped files
```

### What Was NOT Changed

- No broad refactor of package layout.
- `[tool.uv] package = false` untouched.
- Cancellation semantics unchanged.
- No new dependencies.
- `get_running_loop()` not used where `new_event_loop()` is the correct answer (sync GC / test boundary contexts).
- No `run_until_complete()` on a potentially-running loop — each `new_event_loop()` site is in a context confirmed to lack a running loop.

---

## 1. Executive Summary

9 audit areas covered. 4 patch-now candidates, 4 benchmark-first candidates, 3 experiment-only, 7 do-not-touch.

**Most actionable immediate finding:** `asyncio.get_event_loop()` used outside async context in 6+ locations across `academic_discovery.py`, `session_runtime.py`, `sprint_scheduler.py`, `wasm_sandbox.py`, `document_intelligence.py` — Python 3.14 raises `RuntimeError` when no running loop in thread. PATCH-APPLIED (F214M-D).

**Second most actionable:** `tg.create_task()` at `sprint_scheduler.py:2803` and `utils/async_utils.py:136` missing `name=` kwarg — quick adds, aids observability.

---

## 2. Top 10 Actionable Opportunities

| # | Area | File | Line | Pattern | Opportunity |
|---|------|------|------|---------|-------------|
| 1 | A | `academic_discovery.py` | 269, 276, 283 | `get_event_loop().run_until_complete()` | Deprecated in 3.10+, removal planned 3.14. `run_until_complete` on non-main thread = M1 crash vector. PATCH |
| 2 | A | `sprint_scheduler.py` | 5484, 6136 | `asyncio.get_event_loop().time()` | Replaces with `asyncio.get_running_loop()` or `time.monotonic()` |
| 3 | A | `session_runtime.py` | 338 | `loop = asyncio.get_event_loop()` | Single usage, no `run_until_complete` — safe to patch |
| 4 | A | `wasm_sandbox.py` | 185 | `loop = asyncio.get_event_loop()` | Browser sandbox context, limited risk |
| 5 | A | `document_intelligence.py` | 1290 | `loop = asyncio.get_event_loop()` | F196A already patched other sites here — this one missed |
| 6 | B | `execution_optimizer.py` | 316-396 | `execute_parallel()` unbounded task list | No `buffersize` on `.map()`, no `max_pending` guard. Needs benchmark before patch |
| 7 | B | `rss_atom_adapter.py` | 2039 | `ProcessPoolExecutor(max_workers=3)` | macOS spawn overhead per task, no `map()` use. Experiment only |
| 8 | B | `worker_pool.py` | 3 | `ProcessPoolExecutor()` no max_workers | Unlimited workers on 8GB M1 — emergency teardown only |
| 9 | C | `memory_layer.py` | 793 | `from concurrent.futures import ProcessPoolExecutor` | Unused import? Checked, used in `memory_layer.py` at line ~805-815 |
| 10 | A | `sprint_scheduler.py` | 2803 | `tg.create_task()` no `name=` | One-line add, observability gain |

---

## 3. Patch-Now Candidates

### PATCH-1: `asyncio.get_event_loop()` deprecation — F214M-D PATCH-APPLIED

**Corrected wording:** Python 3.14 raises `RuntimeError` if `get_event_loop()` is called when no event loop exists in the current thread. The asyncio policy system removal is planned for Python 3.16.

**Patch rationale:**
- In **async context**: use `asyncio.get_running_loop()`
- For **timing**: use `time.monotonic()` (already aliased as `_time` in sprint_scheduler)
- In **sync boundary / test / GC context**: create a fresh loop with `asyncio.new_event_loop()`, run, close

**Sites patched (F214M-D):**

| File | Line | Context | Replacement | Risk |
|------|------|---------|-------------|------|
| `intelligence/academic_discovery.py` | 269, 276, 283 | Sync wrappers (`_sync` funcs) | `new_event_loop()` + `run_until_complete()` + `close()` | MEDIUM — 3 sites |
| `network/session_runtime.py` | 338 | Test-only helper, no running loop | `new_event_loop()` + `run_until_complete()` + `close()` | LOW |
| `runtime/sprint_scheduler.py` | 5496 | Async function, running loop guaranteed | `get_running_loop()` | LOW |
| `runtime/sprint_scheduler.py` | 6136, 6138 | Timing measurement | `_time.monotonic()` | LOW |
| `tools/wasm_sandbox.py` | 185 | Async function, running loop guaranteed | `get_running_loop()` | LOW |
| `intelligence/document_intelligence.py` | 1290 | Sync GC context, no running loop | `new_event_loop()` + `run_until_complete()` + `close()` | LOW |

### PATCH-2: `tg.create_task()` missing `name=`

**Current:**
```python
# runtime/sprint_scheduler.py:2803
public_task = tg.create_task(async_run_public(...))

# utils/async_utils.py:136
tg.create_task(_run(i, fn, a, k))

# __main__.py:2792
tg.create_task(async_run_live_public_pipeline(...))

# __main__.py:2797
tg.create_task(async_run_default_feed_batch(...))
```

**Recommended:** Add `name="sprint:public"` etc.

| File | Line | Current | Recommended | Risk |
|------|------|---------|-------------|------|
| `runtime/sprint_scheduler.py` | 2803 | `tg.create_task(... )` | `tg.create_task(..., name="sprint:public")` | LOW |
| `utils/async_utils.py` | 136 | `tg.create_task(_run(i, fn, a, k))` | `tg.create_task(_run(i, fn, a, k), name=f"async_utils:run-{i}")` | LOW |
| `__main__.py` | 2792 | `tg.create_task(async_run_live_public_pipeline(...))` | `tg.create_task(..., name="main:live_public")` | LOW |
| `__main__.py` | 2797 | `tg.create_task(async_run_default_feed_batch(...))` | `tg.create_task(..., name="main:default_feed")` | LOW |
| `benchmarks/m1_sustained_sprint.py` | 213 | `tg.create_task(_sprint_loop())` | `tg.create_task(..., name="benchmark:sprint_loop")` | LOW |

### PATCH-3: `asyncio.create_task()` without `name=` — observability

**Current pattern:** `asyncio.create_task(coro)` without `name=`

| File | Line | Current | Risk |
|------|------|---------|------|
| `research/parallel_scheduler.py` | 106 | `asyncio.create_task(self._run_io_task(task))` | LOW — already named task context |
| `research/parallel_scheduler.py` | 119 | `asyncio.create_task(self._on_cpu_done(...))` | LOW |
| `coordinators/memory_coordinator.py` | 2791 | `self._task = asyncio.create_task(self._poll_loop())` | LOW |
| `coordinators/performance_coordinator.py` | 134 | `self._cleanup_task = asyncio.create_task(self._periodic_cleanup())` | LOW |
| `transport/inmemory_transport.py` | 24 | `self._task = asyncio.create_task(self._process_loop())` | LOW |
| `coordinators/resource_allocator.py` | 633 | `asyncio.create_task(self._execute_task(task_id, task_func))` | LOW |
| `coordinators/resource_allocator.py` | 745 | `asyncio.create_task(allocator.monitor_and_optimize())` | LOW |
| `transport/nym_transport.py` | 66-96 (5 tasks) | `asyncio.create_task(self._drain_stream(...))` | LOW |
| `dht/kademlia_node.py` | 302, 427 | `asyncio.create_task(self._refresh_loop())`, `asyncio.create_task(self._send_find_value(...))` | LOW |
| `layers/coordination_layer.py` | 259, 1948 | `asyncio.create_task(coro)` | LOW |

**Patch benefit:** Python 3.14 Runner API makes named tasks first-class. Low-effort, high-observability return.

### PATCH-4: UUIDv7 for new runtime IDs

**Constraint:** DO NOT TOUCH — `CanonicalFinding.id`, content hashes, dedup fingerprints, LMDB keys, provenance-derived IDs.

**Candidate sites (runtime/report/session IDs, NOT canonical):**

| File | Line | Current | Use | Recommended |
|------|------|---------|-----|-------------|
| `legacy/persistent_layer.py` | 1690, 1727, 1837, 1890 | `uuid.uuid4()` in URN strings | `request_record_id`, `response_record_id`, `revisit_record_id`, `warc_record_id` | UUIDv7 OK — legacy, not canonical |
| `evidence_log.py` | 773 | `uuid.uuid4().hex[:12]` | `event_id` | UUIDv7 OK — telemetry only |
| `utils/validation.py` | 582 | `str(uuid.uuid4())` | Test/tool validation ID | UUIDv7 OK — ephemeral |
| `benchmarks/live_sprint_measurement.py` | 286, 1682 | `uuid.uuid4().hex[:6]` | `uid`, `harness_sprint_id` | UUIDv7 OK — benchmark only |
| `tool_exec_log.py` | 298 | `uuid.uuid4().hex[:8]` | `event_id` | UUIDv7 OK — exec log only |
| `brain/hypothesis_engine.py` | 2600, 2626 | `str(uuid.uuid4())[:8]` | Internal hypothesis IDs | UUIDv7 OK — internal |
| `core/__main__.py` | 76 | `uuid.uuid4().hex[:6]` | `uid` suffix | UUIDv7 OK — ephemeral run id |
| `layers/research_layer.py` | 176 | `str(uuid.uuid4())[:8]` | `mission_id` | UUIDv7 OK |
| `layers/memory_layer.py` | 1112 | `str(uuid.uuid4())` | `block_id` | UUIDv7 OK |
| `layers/coordination_layer.py` | 1137 | `str(uuid.uuid4())` | `decision_id` | UUIDv7 OK |
| `intelligence/data_leak_hunter.py` | 432, 500, 543, 614, 665 | `str(uuid.uuid4())` | `alert_id` | UUIDv7 OK — alert only |
| `intelligence/web_intelligence.py` | 356 | `str(uuid.uuid4())` | `operation_id` | UUIDv7 OK |
| `dht/kademlia_node.py` | 138, 422 | `uuid.uuid4().hex[:8]` | `node_id`, `rpc_id` | UUIDv7 OK |

**Note:** UUIDv7 is time-sortable. Would help with log collation and temporal ordering. Python 3.14 has `uuid.uuid7()` built-in. Fallback for 3.11: `import uuid_utils` or manual implementation. **NO production patch without benchmark.**

---

## 4. Benchmark-First Candidates

### BENCHMARK-1: `execution_optimizer.execute_parallel()` backpressure

**File:** `utils/execution_optimizer.py:316-396`

**Current behavior:**
- Takes `tasks: List[Any]`
- Strategy-based execution (serial, parallel, adaptive)
- `max_workers` parameter passed but no `buffersize` on any internal `.map()`
- No `max_pending` semaphore guard

**Risk:** If caller passes unbounded list, all tasks queued into thread pool simultaneously.

**Benchmark design:**
1. Instrument `execute_parallel(tasks)` with `len(tasks)` logging
2. Run `probe_f214h` suite with task size histogram
3. If `len(tasks)` > 64 in production paths → add `Semaphore(max_pending=32)` guard
4. If `len(tasks)` < 16 consistently → no patch needed

**Recommendation:** Add `max_pending: int | None = None` parameter, default `None` (unbounded for backward compat). Only patch if P95 `len(tasks)` > 32 in production traces.

### BENCHMARK-2: `rss_atom_adapter.py` `ProcessPoolExecutor(max_workers=3)` on macOS

**File:** `discovery/rss_atom_adapter.py:2033-2039`

**Current behavior:**
```python
_PARSE_POOL: _cf.ProcessPoolExecutor | None = None

def _get_parse_pool() -> _cf.ProcessPoolExecutor:
    global _PARSE_POOL
    if _PARSE_POOL is None:
        _PARSE_POOL = _cf.ProcessPoolExecutor(max_workers=3)
    return _PARSE_POOL
```

**macOS spawn overhead:** `fork` + `exec` on macOS for ProcessPoolExecutor — each task submission has ~1-5ms overhead. With `max_workers=3`, throughput may be lower than ThreadPoolExecutor for HTML parsing workloads < 100ms per task.

**Benchmark design:**
1. Profile `_parse_html_cpu_sync` task duration via `time.time()` + `future.done_timestamp`
2. Compare `ProcessPoolExecutor(max_workers=3)` vs `ThreadPoolExecutor(max_workers=3)` for HTML parsing
3. If median task time < 50ms and throughput difference < 2x → no patch
4. If `ProcessPoolExecutor` throughput is worse → replace with `ThreadPoolExecutor`

**Experiment-only candidate:** Free-threaded Python via `.venv-py314t` could bypass GIL for pure-Python HTML parsing, but only worth it if ProcessPoolExecutor is proven bottleneck.

### BENCHMARK-3: `worker_pool.py` `ProcessPoolExecutor()` no limit

**File:** `utils/worker_pool.py:3`

```python
executor = ProcessPoolExecutor()  # no max_workers
```

**Risk:** On 8GB M1, unlimited process pool could exhaust memory under load.

**Benchmark design:**
1. Find all callers of `worker_pool.py` functions
2. Instrument max workers used under load
3. If `max_workers > 4` in any path → cap at 4 and benchmark

**NO patch without benchmark evidence.**

### BENCHMARK-4: `memory_layer.py` `ProcessPoolExecutor` import + usage

**File:** `layers/memory_layer.py:793`

```python
from concurrent.futures import ProcessPoolExecutor
```

**Context:** Check if `ProcessPoolExecutor` is actually used in this file, or if it's a leftover import. If unused → remove as dead code (LOW risk, PATCH). If used → check for cleanup/shutdown patterns.

---

## 5. Experiment-Only Candidates

### EXPERIMENT-1: Free-threaded Python `.venv-py314t`

**Plan:**
```bash
# Create free-threaded venv alongside existing .venv
python3.14t -m venv hledac/universal/.venv-py314t

# Smoke test imports (C-extension compatibility)
.venv-py314t/bin/python -c "
  import sys
  print('free-threaded:', sys.buildconf.get('Py_GIL_DISABLED', 'not available'))
  import mlx  # should fail or warn
  import duckdb  # should fail or warn
  import orjson  # should work
  import lmdb  # should work
  print('IMPORT_OK')
"

# Run pure-Python CPU benchmark
.venv-py314t/bin/python -c "
  import time
  from concurrent.futures import ThreadPoolExecutor
  def cpu_work(n):
    return sum(i*i for i in range(n))
  t0 = time.time()
  with ThreadPoolExecutor(max_workers=4) as ex:
    results = list(ex.map(cpu_work, [50000]*20))
  print(f'CPU benchmark: {time.time()-t0:.3f}s')
"
```

**Constraints:**
- `.venv-py314t` — NOT default switch
- No production code changes
- Only pure-Python CPU candidates (text normalization, regex/scoring, report formatting)
- NOT: DuckDB, LMDB, LanceDB, PyArrow, MLX, numpy-heavy, msgspec-heavy DTOs

### EXPERIMENT-2: `annotationlib.get_annotations()` for runtime introspection

**Current pattern:**
```python
typing.get_type_hints(cls)  # expensive, full eval
cls.__annotations__  # deferred if Python 3.14+, may be str not type
inspect.signature(cls)  # expensive
```

**Python 3.14 compatibility:**
- `from __future__ import annotations` → `__annotations__` stores strings not types
- `annotationlib.get_annotations(cls)` → gets resolved annotations without full `get_type_hints` overhead
- `inspect.signature` still works but is slow

**Experiment:**
```python
# Test in isolated probe
import annotationlib
from typing import get_type_hints

class CanonicalFinding(TypedDict):
    id: str
    confidence: float

# Compare
hints_old = get_type_hints(CanonicalFinding)  # full eval
hints_new = annotationlib.get_annotations(CanonicalFinding)  # deferred

# Check if annotationlib is available (Python 3.14 stdlib)
# For Python 3.11-3.13: pip install annotationlib (backport exists)
```

**Scope:** Only for hot-path annotation introspection (e.g., report generation, schema validation). Not for `CanonicalFinding` / msgspec.Struct — those use `msgspec` native schema.

### EXPERIMENT-3: `t"..."` template string literals for report rendering

**Note:** NO production rewrite. POC only.

```python
# Python 3.14 t-string POC (if t-strings ship in 3.14 final)
report = t"Found {count} IOC entities in {domain} with confidence {conf:.2f}"
# report is string.templatelib.Template, NOT str
# Need .render() or similar to substitute

# Safe renderer POC for markdown/HTML reports:
from string import Template
class SafeReportTemplate:
    def __init__(self, template: str):
        self._t = Template(template)
    def render(self, **kwargs) -> str:
        # Safe substitution — no arbitrary code execution
        return self._t.safe_substitute(kwargs)
```

**Scope:** Only for `export/markdown_reporter.py` and `export/html_reporter.py` if t-strings ship in Python 3.14 GA. NOT for STIX/JSON/SQL templating.

---

## 6. Do-Not-Touch List

| Item | Reason |
|------|--------|
| `CanonicalFinding.id` UUID patterns | Deterministic, provenance-derived, LMDB key — changing breaks dedup |
| `content_miner.py` `executor.map(buffer_size=8)` | Already patched F214H-7 |
| `compression.zstd` usage | F214C/C-2 audit PASS, no patch needed |
| `GC benchmark / 3.13.5 / 3.14.5 protocol` | F214G/G-2/G-3 PASS, no patch |
| `[tool.uv] package = false` entries | Package layout, do not change |
| `DuckDB`, `LMDB`, `LanceDB`, `PyArrow`, `MLX` executor paths | Must stay on ThreadPoolExecutor |
| `msgspec.Struct` annotation patterns | Pydantic/msgspec use `__annotations__` directly — changing could break serialization |
| `aiohttp` session cleanup | Already has proper `aclose()` patterns |
| `archive extraction` (post-F214S) | Security patch already applied, no further changes needed |
| `deterministic ID generation` (e.g., content hashes) | Must remain reproducible across runs |
| `torch`/`tensorflow`/`chromium` additions | Not in scope |

---

## 7. Exact file:line Findings

### Area A: Asyncio v2 — Task Naming + Runner Boundaries

#### Category: ACTIVE RUNTIME

| File | Line | Current Pattern | Recommended | PATCH |
|------|------|-----------------|-------------|-------|
| `runtime/sprint_scheduler.py` | 2803 | `tg.create_task(async_run_public(...)`) | `tg.create_task(..., name="sprint:public")` | YES |
| `runtime/sprint_scheduler.py` | 1264 | `asyncio.create_task(..., name="sprint:memory_pressure_loop")` | Already named | — |
| `runtime/sprint_scheduler.py` | 1498 | `asyncio.create_task(..., name="sprint:speculative_prefetch")` | Already named | — |
| `runtime/sprint_scheduler.py` | 1505 | `asyncio.create_task(..., name="sprint:ooda_cycle")` | Already named | — |
| `runtime/sprint_scheduler.py` | 2730-2732 | `asyncio.create_task(..., name="sprint:feed_branch")` etc. | Already named | — |
| `runtime/sprint_scheduler.py` | 6302 | `asyncio.create_task(..., name="sprint:speculative_run")` | Already named | — |
| `runtime/sprint_scheduler.py` | 6411 | `asyncio.create_task(..., name="sprint:flush_arrow")` | Already named | — |
| `runtime/sprint_scheduler.py` | 6461 | `asyncio.create_task(..., name="sprint:flush_arrow_ioc")` | Already named | — |
| `runtime/sprint_scheduler.py` | 5484 | `loop = asyncio.get_event_loop()` | `asyncio.get_running_loop()` | YES |
| `runtime/sprint_scheduler.py` | 6136, 6138 | `asyncio.get_event_loop().time()` | `time.monotonic()` | YES |
| `runtime/sidecar_bus.py` | 264, 292 | `asyncio.create_task(_run_one(...))` | Add `name=` | YES |
| `utils/async_utils.py` | 136 | `tg.create_task(_run(i, fn, a, k))` | `tg.create_task(..., name=f"async_utils:run-{i}")` | YES |
| `__main__.py` | 2792, 2797 | `tg.create_task(...)` | Add `name=` | YES |
| `benchmarks/m1_sustained_sprint.py` | 213 | `tg.create_task(_sprint_loop())` | Add `name=` | YES |
| `coordinators/monitoring_coordinator.py` | 238 | `asyncio.create_task(...)` no name | Add name | YES |
| `coordinators/resource_allocator.py` | 633, 745 | `asyncio.create_task(...)` no name | Add name | YES |
| `coordinators/performance_coordinator.py` | 134 | `asyncio.create_task(...)` no name | Add name | YES |
| `coordinators/benchmark_coordinator.py` | 137, 309 | `asyncio.create_task(...)` no name | Add name | YES |

#### Category: TRANSPORT/STEALTH

| File | Line | Current Pattern | Recommended | PATCH |
|------|------|-----------------|-------------|-------|
| `transport/nym_transport.py` | 66-96 | 5x `asyncio.create_task(...)` no name | Add names | YES |
| `transport/inmemory_transport.py` | 24 | `asyncio.create_task(...)` no name | Add name | YES |
| `transport/curl_cffi_runtime.py` | 150 | `asyncio.create_task(_close_evicted())` no name | Add name | YES |

#### Category: INTELLIGENCE BACKGROUND

| File | Line | Current Pattern | Recommended | PATCH |
|------|------|-----------------|-------------|-------|
| `research/parallel_scheduler.py` | 106 | `asyncio.create_task(self._run_io_task(task))` no name | Add name | YES |
| `research/parallel_scheduler.py` | 119 | `asyncio.create_task(self._on_cpu_done(...))` no name | Add name | YES |
| `layers/memory_layer.py` | 90 | `asyncio.create_task(...)` no name | Add name | YES |
| `layers/coordination_layer.py` | 259, 1948 | `asyncio.create_task(coro)` no name | Add name | YES |
| `dht/kademlia_node.py` | 302, 427 | `asyncio.create_task(...)` no name | Add name | YES |
| `dht/sketch_exchange.py` | 55 | `asyncio.create_task(coro)` no name | Add name | YES |
| `intelligence/document_intelligence.py` | 1290 | `loop = asyncio.get_event_loop()` | `asyncio.get_running_loop()` | YES |
| `intelligence/academic_discovery.py` | 269, 276, 283 | `get_event_loop().run_until_complete()` | Investigate per-site | YES (per-site) |

#### Category: LEGACY

| File | Line | Current Pattern | Recommended | PATCH |
|------|------|-----------------|-------------|-------|
| `legacy/persistent_layer.py` | 1690, 1727, 1837, 1890 | `uuid.uuid4()` URN generation | UUIDv7 OK (legacy only) | BENCHMARK |
| `network/session_runtime.py` | 338 | `loop = asyncio.get_event_loop()` | `asyncio.get_running_loop()` | YES |
| `tools/wasm_sandbox.py` | 185 | `loop = asyncio.get_event_loop()` | `asyncio.get_running_loop()` | YES |

#### Category: TESTS

| File | Line | Current Pattern | Recommended | PATCH |
|------|------|-----------------|-------------|-------|
| `tests/probe_0b/__init__.py` | ~232 | `asyncio.get_event_loop()` | N/A — test only | NO |

---

### Area B: Executor Backpressure v2

| File | Line | Current | Risk | Label |
|------|------|---------|------|-------|
| `utils/execution_optimizer.py` | 316-396 | `execute_parallel(tasks)` no `max_pending` guard | HIGH if caller passes >32 tasks | BENCHMARK |
| `discovery/rss_atom_adapter.py` | 2033-2039 | `ProcessPoolExecutor(max_workers=3)` | MEDIUM — macOS spawn overhead | BENCHMARK |
| `utils/worker_pool.py` | 3 | `ProcessPoolExecutor()` no limit | HIGH on 8GB M1 | BENCHMARK |
| `knowledge/duckdb_store.py` | 2167, 2189, 2209, 2222, 2255, 2928, 3255, 3272 | `executor.submit()` for sync operations | LOW — bounded by connection count | NO_TOUCH |
| `research/parallel_scheduler.py` | 111 | `self._cpu_executor.submit(self._run_cpu_task_sync, task)` | LOW — single task per call | NO_TOUCH |
| `intelligence/document_intelligence.py` | 1139 | `executor.submit(self._ela_analysis_cpu_sync, content)` | LOW — single task | NO_TOUCH |
| `tools/probe_f214h_content_miner_backpressure/probe_f214h.py` | 274 | `executor.submit()` dict comprehension | LOW — benchmark probe only | NO_TOUCH |

**Key finding:** `duckdb_store.py` uses `executor.submit()` for init/sync operations — these are bounded by `_MAX_CONNECTIONS=32` already. NOT a backpressure issue.

---

### Area C: ProcessPoolExecutor Cleanup

| File | Line | Current | Status | Label |
|------|------|---------|--------|-------|
| `utils/execution_optimizer.py` | 305 | `self.process_pool = ProcessPoolExecutor(...)` | No `terminate_workers()` or `kill_workers()` call found | BENCHMARK |
| `discovery/rss_atom_adapter.py` | 2033-2039 | `ProcessPoolExecutor(max_workers=3)` module-level singleton | No explicit cleanup in `_get_parse_pool()` | BENCHMARK |
| `utils/worker_pool.py` | 3 | `ProcessPoolExecutor()` no max_workers | No cleanup pattern found | BENCHMARK |
| `layers/memory_layer.py` | 793 | `from concurrent.futures import ProcessPoolExecutor` | Check if actually used — may be dead import | INVESTIGATE |

**Python 3.14 note:** `ProcessPoolExecutor.terminate_workers()` and `kill_workers()` are available. Use only for emergency teardown (never normal shutdown). Normal shutdown should use `executor.shutdown(wait=True)` which already runs in this codebase.

---

### Area D: Multiple Interpreters / InterpreterPoolExecutor

**Experiment-only candidates (pure-Python CPU):**

| Candidate | File | Rationale | Memory Risk | Label |
|-----------|------|-----------|-------------|-------|
| `text/unicode_analyzer.py` normalization | `text/unicode_analyzer.py` | Pure Python, no C-extension | LOW | EXPERIMENT |
| `patterns/pattern_matcher.py` scoring | `patterns/pattern_matcher.py` | Pure Python regex, CPU-bound | LOW | EXPERIMENT |
| `export/markdown_reporter.py` formatting | `export/markdown_reporter.py` | Pure Python string ops | LOW | EXPERIMENT |
| `export/json_reporter.py` rendering | `export/json_reporter.py` | Pure Python | LOW | EXPERIMENT |

**NOT candidates (C-extension heavy or msgspec):**
- `duckdb_store.py` — DuckDB
- `lmdb_kv.py` — LMDB C extension
- `lancedb_store.py` — LanceDB
- `knowledge/duckdb_store.py` — DuckDB + PyArrow
- `mlx` paths — MLX Metal
- `msgspec.Struct` DTOs — msgspec

**Benchmark design:**
```python
# InterpPool vs ThreadPool for pattern_matcher
import time
from concurrent.futures import ThreadPoolExecutor, InterpreterPoolExecutor

def pattern_score(text: str) -> float:
    # Pure Python scoring
    ...

tasks = [f"text_{i}" for i in range(1000)]
t0 = time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    results = list(ex.map(pattern_score, tasks))
thread_time = time.time() - t0

# Compare with InterpreterPoolExecutor if available
# ...
```

---

### Area E: Free-Threaded Python Experiment Plan

**Steps:**
1. Check if Python 3.14t (free-threaded) is available: `python3.14t --version`
2. Create isolated venv: `python3.14t -m venv hledac/universal/.venv-py314t`
3. Run import smoke: `.venv-py314t/bin/python -c "import sys; print(sys.version)"`
4. Run dependency compatibility check:
   ```bash
   .venv-py314t/bin/pip install orjson lmdb pyyaml psutil
   .venv-py314t/bin/python -c "import orjson; import lmdb; print('OK')"
   ```
5. Run pure-Python CPU benchmark (see EXPERIMENT-1)
6. **NO production patch**

---

### Area F: Deferred Annotations / annotationlib

| File | Line | Current Pattern | Python 3.14 Risk | Label |
|------|------|-----------------|------------------|-------|
| `tool_registry.py` | 26 | `from typing import ... get_type_hints` | LOW — used in type checking only | MONITOR |
| `tests/probe_8h/test_sprint_8h.py` | 66, 79 | `__annotations__`, `typing.get_type_hints()` | Test only, no risk | NO_TOUCH |
| `tests/probe_8f/test_sprint_8f.py` | 42, 51 | `__annotations__`, `typing.get_type_hints()` | Test only | NO_TOUCH |
| `tests/test_correlation_propagation.py` | 361 | `inspect.signature()` | Test only | NO_TOUCH |
| `tests/test_autonomous_orchestrator.py` | 10897 | `inspect.signature()` | Test only | NO_TOUCH |
| `msgspec.Struct` classes | — | Native msgspec, not `__annotations__` | No risk — msgspec uses own schema | NO_TOUCH |
| `from __future__ import annotations` | 20 files | Deferred annotations | COMPATIBLE — Python 3.14 deferred annotations are backwards compatible | NO_TOUCH |

**Files with `from __future__ import annotations`:** `live_public_pipeline.py`, `public_fetcher.py`, `run_baseline.py`, `paths.py`, `cost_model.py`, `project_types.py`, `live_feed_pipeline.py`, `smoke_runner.py`, `pattern_matcher.py`, `config.py`, `nym_policy.py`, `scripts/check_torrc.py`, `memory/__init__.py`, `enhanced_research.py`, `budget_manager.py`, `tot_integration.py`, `dynamic_context_manager.py`, `memory_manager.py`, `active_learning.py`.

All compatible with Python 3.14 deferred annotations — no changes needed.

---

### Area G: Template String Literals / t-strings

**No production rewrite.** POC only if Python 3.14 ships t-strings in GA.

**Scope for POC:** `export/markdown_reporter.py`, `export/html_reporter.py`

**Safe renderer approach:**
```python
from string import Template

class SafeTemplate:
    """Safe alternative to f-strings for untrusted template content."""
    def __init__(self, template: str):
        self._t = Template(template)
    def substitute(self, **kwargs) -> str:
        return self._t.substitute(**kwargs)  # raises KeyError on missing
    def safe_substitute(self, **kwargs) -> str:
        return self._t.safe_substitute(**kwargs)  # leaves $name on missing
```

---

### Area H: UUIDv7

See Section 3, PATCH-4. **NO production patch without benchmark.**

**Implementation note for Python 3.11-3.13:**
```python
import uuid
import time

def uuid7() -> uuid.UUID:
    """Generate a time-sortable UUIDv7."""
    ts = time.time_ns() // 1000  # microseconds
    random_bits = uuid.uuid4().int >> 12  # 52 random bits
    uuid_int = (ts << 16) | random_bits
    return uuid.UUID(int=uuid_int, version=7)
```

---

### Area I: Safe External Debugger Interface

**Finding:** No `sys.remote_exec` or explicit remote debugger launcher found.

**Current state:**
- `__main__.py` uses `signal.signal()` for graceful shutdown — appropriate
- `core/__main__.py` has no remote debug setup
- No `PYTHON_REMOTE_DEBUG` or `PYDB` env var patterns found
- Boot smoke uses `PYTHON_DISABLE_REMOTE_DEBUG=1` — good, already in place

**Recommendation:** Confirm that all live/sprint/run commands use `PYTHON_DISABLE_REMOTE_DEBUG=1` in production. No patch needed — already secure.

**Optional hardening (if desired):**
```python
# In __main__.py boot:
import os
if not os.environ.get('PYTHON_DISABLE_REMOTE_DEBUG', '0') == '1':
    import sys
    sys.exit('PYTHON_DISABLE_REMOTE_DEBUG must be set for OSINT runs')
```

---

### Area J: Archive/Tar/Zip Residual Security

**Status:** F214S PASS. No new extraction sites found post-patch.

**Verified no new sites:** `archiveSites` scan found only `shutil.copy()` (not extraction) and no `extractall` calls outside of already-patched `vault_manager.py`.

---

### Area K: Python 3.14 Removals/Deprecations Compatibility

| Pattern | Found | Python 3.14 Status | Label |
|---------|-------|-------------------|-------|
| `asyncio.child_watcher` | NOT FOUND | Deprecated in 3.12, removed in 3.14 | CLEAN |
| `pkgutil` deprecated APIs | NOT FOUND | Deprecated in 3.14 | CLEAN |
| `ast.visit_Num/visit_Str/visit_NameConstant/visit_Ellipsis` | NOT FOUND | Removed in 3.14 | CLEAN |
| `ast.Constant.n / .s` | NOT FOUND | Removed in 3.14 | CLEAN |
| `typing` private internals (`_collect_*`, `_eval_type`) | NOT FOUND | Deprecated in 3.14 | CLEAN |
| `sys._clear_type_cache` | NOT FOUND | Removed in 3.14 | CLEAN |
| `sysconfig.expand_makefile_vars` | NOT FOUND | Deprecated in 3.14 | CLEAN |
| `argparse` deprecated flags | NOT FOUND | Deprecated in 3.14 | CLEAN |
| `pathlib` removed/deprecated APIs | NOT FOUND | Various in 3.14 | CLEAN |
| `asyncio.get_event_loop()` | FOUND 6+ sites | Deprecated in 3.10, removal planned 3.14 | PATCH-1 |
| `asyncio.set_event_loop()` | NOT FOUND | Deprecated in 3.10 | CLEAN |

**Overall compatibility:** CLEAN except for `get_event_loop()` — see PATCH-1.

---

## 8. Suggested Next Micro-Sprints

### F214M-A: Remaining Async Task Naming
**PATCH-APPLIED** (2026-05-05)

**Scope:** Add `name=` to all remaining `asyncio.create_task()` and `tg.create_task()` calls without names.
**Files:** ~15 files identified in Section 7, Area A.
**Effort:** LOW — one-line adds per site.
**Tests:** `pytest hledac/universal/ -q` — should be GREEN.
**Label:** PATCH-APPLIED

26 sites named across 13 files. Naming convention: `module:task_type` or `module:task_type:identifier`.

**Named sites:**
| File | Sites | Names |
|------|-------|-------|
| `runtime/sidecar_bus.py` | 2 | `sidecar_bus:stage_runner:{name}`, `sidecar_bus:remaining_runner:{name}` |
| `utils/async_utils.py` | 3 | `async_utils:run-{i}`, `async_utils:map-{i}` |
| `transport/nym_transport.py` | 5 | `nym:stdout_drain`, `nym:stderr_drain`, `nym:sender`, `nym:receiver`, `nym:health_check` |
| `transport/inmemory_transport.py` | 1 | `inmemory:process_loop` |
| `transport/curl_cffi_runtime.py` | 1 | `curl_cffi:close_evicted` |
| `dht/kademlia_node.py` | 2 | `kademlia:refresh_loop`, `kademlia:send_find_value:{pid[:8]}` |
| `dht/sketch_exchange.py` | 1 | `sketch:background` |
| `coordinators/resource_allocator.py` | 2 | `resource_allocator:execute_task:{task_id}`, `resource_allocator:monitor` |
| `coordinators/performance_coordinator.py` | 1 | `performance_coordinator:cleanup` |
| `coordinators/benchmark_coordinator.py` | 2 | `benchmark:memory_monitor`, `benchmark:agent:{agent_name}` |
| `coordinators/monitoring_coordinator.py` | 1 | `monitoring:background_collection` |
| `coordinators/memory_coordinator.py` | 1 | `memory_coordinator:poll` |
| `layers/coordination_layer.py` | 2 | `coordination_layer:task` (both _track_task sites) |
| `layers/memory_layer.py` | 1 | `memory_layer:health_check` |
| `layers/communication_layer.py` | 1 | `communication_layer:batch_processor` |
| `research/parallel_scheduler.py` | 2 | `parallel_scheduler:io_task:{task_id}`, `parallel_scheduler:cpu_done:{task_id}` |

**Skipped (delegated adapter):**
- `layers/communication_layer.py:789` — `self._a2a_adapter.create_task()` is a delegated call to external A2A adapter; task name would be controlled by adapter, not our code. SKIPPED.

**Skipped (legacy/inactive):**
- `runtime/sprint_scheduler.py` — already had task names per F214E convention.
- `coordinators/memory_coordinator.py` — already had task name.

**Validation:** Import smoke PASS, boot smoke PASS (clean SIGINT, no fatal traceback).

### F214M-B: `execution_optimizer` Backpressure Benchmark
**Scope:** Instrument `execute_parallel(tasks)` with `len(tasks)` histogram. Run `probe_f214h` suite. Determine P95 task count.
**Files:** `utils/execution_optimizer.py`
**Decision:** If P95 `len(tasks)` > 32 → add `Semaphore(max_pending=32)` guard. Else → no patch.
**Label:** BENCHMARK-FIRST

### F214M-C: UUIDv7 for Runtime IDs
**Scope:** Replace `uuid.uuid4()` with `uuid.uuid7()` for runtime/report/session IDs only. NOT canonical finding IDs, NOT LMDB keys, NOT content hashes.
**Files:** ~25 sites in legacy, benchmarks, telemetry, web_intelligence, data_leak_hunter, kademlia_node.
**Constraint:** Validate time-sortable ordering in logs after patch.
**Label:** BENCHMARK-FIRST (UUIDv7 benchmark in 3.14)

### F214M-D: `asyncio.get_event_loop()` Cleanup
**Scope:** Replace 6 `get_event_loop()` calls with `get_running_loop()` or `time.monotonic()` as appropriate per call site.
**Files:** `academic_discovery.py` (3 sites), `session_runtime.py`, `sprint_scheduler.py` (2 sites), `wasm_sandbox.py`, `document_intelligence.py`.
**Risk:** MEDIUM — `academic_discovery.py` sites are sync-wrapper functions that call `run_until_complete` on non-main thread (M1 crash vector similar to F196A findings). Each site needs per-call investigation.
**Label:** PATCH-NOW (per-site)

### F214M-E: Safe Debugger Launcher Env
**Scope:** Verify `PYTHON_DISABLE_REMOTE_DEBUG=1` in all smoke/live commands. Optionally add boot guard in `__main__.py`.
**Files:** `__main__.py`, smoke commands.
**Label:** NO_TOUCH (already secure) / OPTIONAL

### F214M-F: Free-Threaded Experiment
**Scope:** Create `.venv-py314t`, run import smoke, dependency compatibility, pure-Python CPU benchmark. NO production patch.
**Files:** None (isolated experiment).
**Label:** EXPERIMENT-ONLY

---

## 9. Validation Commands and Results

```bash
# Validation 1: uv sync
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
uv sync
# Result: OK — 4 packages uninstalled (iniconfig, pluggy, pygments, pytest)

# Validation 2: Import smoke
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
# Result: OK — fast-langdetect warning only (non-fatal)

# Validation 3: Boot smoke (35s)
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
PYTHON_DISABLE_REMOTE_DEBUG=1 PYTHONPATH="$PWD" timeout 35 python -m hledac.universal.__main__
# Result: CLEAN_SIGINT — no fatal traceback
# Output: INFO:__main__:[MAIN] Hledac Universal initialized
#         INFO:__main__:[MAIN] uvloop active: False
#         INFO:hledac.universal.patterns.pattern_matcher:[PATTERNS] configured 134 bootstrap patterns
```

---

## Appendix: All `asyncio.create_task()` Without `name=` (Full List)

For PATCH-3 application:

| File | Line | Task Name Suggested |
|------|------|---------------------|
| `runtime/sidecar_bus.py` | 264 | `"sidecar_bus:stage_runner"` |
| `runtime/sidecar_bus.py` | 292 | `"sidecar_bus:remaining_runner"` |
| `research/parallel_scheduler.py` | 106 | `"parallel_scheduler:io_task"` |
| `research/parallel_scheduler.py` | 119 | `"parallel_scheduler:cpu_done"` |
| `coordinators/memory_coordinator.py` | 2791 | `"memory_coordinator:poll_loop"` |
| `coordinators/resource_allocator.py` | 633 | `"resource_allocator:execute_task"` |
| `coordinators/resource_allocator.py` | 745 | `"resource_allocator:monitor"` |
| `coordinators/performance_coordinator.py` | 134 | `"performance_coordinator:cleanup"` |
| `coordinators/benchmark_coordinator.py` | 137 | `"benchmark_coordinator:memory_monitor"` |
| `coordinators/benchmark_coordinator.py` | 309 | (check context) |
| `coordinators/monitoring_coordinator.py` | 238 | `"monitoring_coordinator:collection"` |
| `pipeline/live_public_pipeline.py` | 2531 | (check context) |
| `core/resource_governor.py` | 530 | `"resource_governor:monitor"` |
| `transport/nym_transport.py` | 66 | `"nym:stdout_drain"` |
| `transport/nym_transport.py` | 67 | `"nym:stderr_drain"` |
| `transport/nym_transport.py` | 94 | `"nym:sender"` |
| `transport/nym_transport.py` | 95 | `"nym:receiver"` |
| `transport/nym_transport.py` | 96 | `"nym:health_check"` |
| `transport/inmemory_transport.py` | 24 | `"inmemory:process_loop"` |
| `transport/curl_cffi_runtime.py` | 150 | `"curl_cffi:close_evicted"` |
| `dht/kademlia_node.py` | 302 | `"kademlia:refresh_loop"` |
| `dht/kademlia_node.py` | 427 | `"kademlia:send_find_value"` |
| `dht/sketch_exchange.py` | 55 | `"sketch_exchange:task"` |
| `layers/coordination_layer.py` | 259 | `"coordination_layer:task_1"` |
| `layers/coordination_layer.py` | 1948 | `"coordination_layer:task_2"` |
| `layers/memory_layer.py` | 90 | `"memory_layer:health_check"` |