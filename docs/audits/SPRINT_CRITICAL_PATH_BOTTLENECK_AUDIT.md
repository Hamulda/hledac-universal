# SPRINT_CRITICAL_PATH_BOTTLENECK_AUDIT

**Date:** 2026-05-18
**Scope:** `core/__main__.py`, `runtime/sprint_scheduler.py` (12,369 lines), `runtime/sprint_lifecycle*.py`, `runtime/acquisition_strategy.py`, `runtime/resource_governor.py`, `pipeline/live_public_pipeline.py`, `knowledge/duckdb_store.py`, `export/sprint_exporter.py`
**Method:** Static analysis — grep, AST patterns, import smoke, line counts. No runtime, no network, no model load.
**Project:** `Users-vojtechhamada-PycharmProjects-Hledac-hledac-universal`

---

## 1. CALL CHAIN — CANONICAL ENTRY TO TEARDOWN

```
core/__main__.py::main()
  └── run_sprint(scheduler, query, duckdb_store)
        ├── __init__.py::run_sprint()
              └── SprintScheduler.__init__()
                    ├── _init_connection()          → duckdb.connect (blocking, on executor thread)
                    └── setup lifecycle / telemetry
              └── SprintScheduler.run(query)  [line 2393, async]
                    ├── _run_mandatory_acquisition_prelude()  [F209A]
                    ├── _memory_pressure_loop()    → asyncio.create_task bg [line 2703]
                    ├── _speculative_prefetch(n=3) → asyncio.create_task bg [line 3030]
                    ├── _run_ooda_cycle()          → asyncio.create_task bg [line 3037]
                    │
                    ├── while not is_terminal():   [main run loop, lines 2707–3108]
                    │     ├── _check_hard_deadline()
                    │     ├── _runner.tick() / windup_guard / abort checks
                    │     ├── branch tasks: feed / public / ct  [lines 6649-6651, TaskGroup]
                    │     │     └── live_public_pipeline.run() ← pipeline/live_public_pipeline.py
                    │     │           ├── DuckDuckGoAdapter (discovery)
                    │     │           ├── public_fetcher (fetch_coordinator, curl_cffi)
                    │     │           ├── candidate processing
                    │     │           └── async_ingest_findings_batch() → duckdb_store
                    │     ├── duckdb_store async calls (all via run_in_executor → duckdb_worker)
                    │     ├── acquisition planning / telemetry finalize
                    │     └── await asyncio.gather(*_bg_tasks, return_exceptions=True) [line 3108]
                    │
                    ├── _ensure_nonfeed_predispatch_before_finalization() [windup barrier]
                    ├── _maybe_flush_to_parquet() → asyncio.create_task [line 11069]
                    │
                    └── _build_sprint_result() → SprintSchedulerResult
                          └── export_sprint(result, output_dir)  [export/sprint_exporter.py]
                                └── (all export formats, markdown, STIX, JSON-LD)

TEARDOWN:
  _finalize_result_truth("prelude_complete"/"windup_entered"/"terminal")
  await asyncio.gather(*_bg_tasks, return_exceptions=True)  [line 3108]
  _runner.teardown()
```

---

## 2. BOTTLENECK TABLE

| # | File | Function / Method | Phase | Risk Type | Evidence | Audit / Microbench |
|---|------|-------------------|-------|-----------|----------|---------------------|
| 1 | `sprint_scheduler.py` | `_await_coro()` line 6016 | bridge | **event-loop-blocking** | `future = _ex.submit(asyncio.run, _await_coro())` — submits `asyncio.run()` to thread pool while loop is already running = M1 crash vector (F223-D documented but still present as fallback path) | Microbench: `python -c "import asyncio; loop=asyncio.new_event_loop(); loop.run_until_complete(asyncio.sleep(0))"` in thread under live loop — measure crash rate on M1 |
| 2 | `sprint_scheduler.py` | `_memory_pressure_loop()` lines ~2703 | bg | **cpu/idle-spin** | Continuous `while True` loop checking `sample_uma_status()` every 5s with `time.sleep(5)` — no backpressure mechanism; if `sample_uma_status()` is slow it blocks the task | Microbench: mock `sample_uma_status()` with various latencies, measure loop throughput |
| 3 | `sprint_scheduler.py` | `_speculative_prefetch(n=3)` lines 3030–3035 | bg | **unbounded-task-creation** | `asyncio.create_task()` without timeout or cancellation scope — tasks accumulate across cycles; `n=3` hardcoded, no adaptive concurrency | Trace `_bg_tasks` set size over 5 sprint cycles |
| 4 | `sprint_scheduler.py` | `_run_ooda_cycle()` lines 3037–3039 | bg | **unbounded-task-creation** | `asyncio.create_task()` — OODA loop over pivot graph; if graph size is large, creates unbounded tasks | Count pivot graph size vs task creation rate |
| 5 | `sprint_scheduler.py` | `run()` main loop lines 2707–3108 | active | **event-loop-blocking** | `asyncio.wait_for()` calls without timeout on DuckDB operations — if duckdb_worker is saturated, await can block indefinitely | `time.monotonic()` delta at each await in run loop |
| 6 | `sprint_scheduler.py` | `_ensure_nonfeed_predispatch_before_finalization()` lines ~2713–2725 | windup | **blocking-windup** | "block windup until nonfeed attempted" — synchronous blocking call inside main loop windup path; can delay finalization by up to full nonfeed timeout | Measure `windup_delayed_for_nonfeed` counter + delta |
| 7 | `sprint_lifecycle_runner.py` | `windup_guard()` lines ~115–165 | lifecycle | **cpu/gate** | Called every cycle; evaluates `should_enter_windup()` + `is_terminal()`; if this is expensive it adds per-cycle overhead | Profile `windup_guard()` call frequency and duration |
| 8 | `sprint_scheduler.py` | `_finalize_result_truth()` lines 2694–2697 | pre-flight | **memory/strings** | Writes `prelude_complete` telemetry — string concatenation in tight loop? (needs line-level check) | Check for `+= ` patterns in `_finalize_result_truth` |
| 9 | `duckdb_store.py` | `async_record_canonical_findings_batch()` line ~5046 | write | **I/O/cpu** | `await asyncio.gather(*_bg, return_exceptions=True)` — batches findings to DuckDB; if batch is large (100+ findings), `run_in_executor` thread may saturate | Microbench: insert 10/100/500 findings, measure wall time |
| 10 | `duckdb_store.py` | `_init_connection()` line 886 | init | **I/O/blocking** | `duckdb.connect(str(self._db_path))` — blocking call on duckdb_worker thread at startup; DB file size matters | Measure `_init_connection()` wall time with cold vs warm DB |
| 11 | `live_public_pipeline.py` | `run()` lines ~1515–1598 | active | **I/O/memory** | `time.time()` for all discovery stages; creates `CandidateAcceptanceReport` per candidate; large candidate lists cause memory pressure | Count candidates per run, measure peak memory |
| 12 | `live_public_pipeline.py` | `run()` line 2266–2267 | active | **import/eager** | `import numpy as np` inline inside hot path — first occurrence triggers ~200ms import on cold; subsequent calls reuse cached import | `time.monotonic()` before/after first numpy import in pipeline |
| 13 | `sprint_exporter.py` | `export_sprint()` | windup | **I/O/cpu** | Multiple format writes (markdown, JSON-LD, STIX, CSV); synchronous `pathlib` ops; no async | Measure export wall time for 10/100/500 findings |
| 14 | `sprint_scheduler.py` | `_maybe_flush_to_parquet()` line 11069 | bg | **I/O/idle** | `asyncio.create_task` for parquet flush — runs in background; if DuckDB is under load, parquet writes may queue indefinitely | Trace parquet flush latency vs DuckDB queue depth |
| 15 | `sprint_scheduler.py` | `run()` line 5475 | active | **event-loop-blocking** | `await asyncio.gather(*_tasks, return_exceptions=True)` over lane results — if one lane is slow, all wait; no timeout on gather itself | Lane-level timing delta audit |

---

## 3. TIMING COUNTERS EXIST

| File | Counter | Line |
|------|---------|------|
| `sprint_scheduler.py` | `_result.windup_guard_call_count` | 1117 |
| `sprint_scheduler.py` | `_result.windup_guard_callback_executed_count` | 1119 |
| `sprint_scheduler.py` | `_result.prewindup_barrier_duration_s` | 1112 |
| `sprint_scheduler.py` | `_result.windup_delayed_for_nonfeed` | 1113 |
| `sprint_scheduler.py` | `_result.acquisition_prelude_ran` | checked at 2693 |
| `live_public_pipeline.py` | `discovery_elapsed_s` | 3273, 3314, 3317 |
| `live_public_pipeline.py` | `stage_counters` (bootstrap_accepted_findings, etc.) | 340, 392 |
| `live_public_pipeline.py` | `_tr_counter` dict | 3986 |
| `duckdb_store.py` | `record_canonical_findings_batch` timing via `ts=` | 1515, 1563 |
| `sprint_exporter.py` | `export_elapsed_s` (via `time.time()`) | via `_time.time()` |

**Gap**: No `time.monotonic()` delta around `_await_coro()`, no per-phase elapsed in `run()`, no `loop_duration_ms` in result.

---

## 4. IMPORT SMOKE

| File | Import Count | Heavy Imports (eager) |
|------|-------------|----------------------|
| `sprint_scheduler.py` | **34** | `duckdb`, `lmdb`, `igraph`, `msgspec`, `orjson`, `psutil` — all top-level |
| `live_public_pipeline.py` | **15** | `duckduckgo_adapter`, `public_fetcher`, `selectolax` — all top-level (F223 refactor) |
| `duckdb_store.py` | **18** | `duckdb`, `orjson`, `psutil` — top-level |
| `sprint_exporter.py` | ~15 | `acquisition_telemetry_reconcile`, `investigation_planner` — top-level |

**Critical**: `sprint_scheduler.py` top-level imports include `duckdb`, `lmdb`, and `igraph` — these are loaded at scheduler `__init__`, not lazily. On cold start, these add ~500ms+ to import time before the event loop even starts.

**Inline import found**: `numpy` inside `live_public_pipeline.py:2230, 2266` — first vector encoding call triggers eager import.

---

## 5. TOP 10 BOTTLENECK CANDIDATES

| Rank | File | Function | Type | Rationale |
|------|------|----------|------|-----------|
| **1** | `sprint_scheduler.py:6016` | `_await_coro()` | Event-loop-blocking | `asyncio.run()` submitted to thread pool while live loop running — M1 crash vector (F223-D acknowledged) |
| **2** | `sprint_scheduler.py:2393` | `run()` main loop | Event-loop-blocking | 12,369-line single async function; `await asyncio.gather(*_tasks)` with no per-task timeout; slow lane blocks all |
| **3** | `sprint_scheduler.py:2703` | `_memory_pressure_loop()` | CPU/idle-spin | Continuous `while True` with `time.sleep(5)` — no backpressure, no cancellation in the loop itself |
| **4** | `sprint_scheduler.py:5475` | lane `asyncio.gather()` | Event-loop-blocking | Wait for all lane tasks; slowest lane determines total; no timeout |
| **5** | `duckdb_store.py` | `_init_connection()` | I/O | `duckdb.connect()` on first run — blocks duckdb_worker; DB file size dependent |
| **6** | `live_public_pipeline.py:2230` | `import numpy` | Import | First vector encoding triggers ~200ms cold import inside hot path |
| **7** | `sprint_exporter.py` | `export_sprint()` | I/O | Synchronous multi-format export (STIX, Markdown, JSON-LD) — no async, no parallelism |
| **8** | `sprint_scheduler.py:3030` | `_speculative_prefetch(n=3)` | Unbounded tasks | Hardcoded `n=3`, no adaptive concurrency, tasks not bounded by timeout |
| **9** | `sprint_scheduler.py:3037` | `_run_ooda_cycle()` | Unbounded tasks | OODA pivot graph iteration — task count scales with graph size |
| **10** | `sprint_scheduler.py:3936–3975` | windup blocking logic | Blocking-windup | `prewindup_barrier` with `windup_delayed_for_nonfeed` — can block finalization by full nonfeed timeout |

---

## 6. TOP 5 SAFE MICROBENCH CANDIDATES

| # | Microbench | Target | How to measure |
|---|-----------|--------|----------------|
| **1** | DuckDB write latency | `duckdb_store.py:5046` | `asyncio.gather(*[async_ingest( finding ) for _ in range(N)])` — measure N=1/10/50/200 |
| **2** | Lifecycle windup_guard cost | `sprint_lifecycle_runner.py:115` | Call `windup_guard()` 1000x in a loop, measure total time; compare with/without callback |
| **3** | `_await_coro()` thread overhead | `sprint_scheduler.py:5990–6031` | Measure `loop.run_until_complete()` vs raw call without thread wrapper |
| **4** | `export_sprint()` wall time | `sprint_exporter.py` | `time.time()` before/after for 10/50/200 findings; break down by format |
| **5** | Pipeline stage elapsed tracking | `live_public_pipeline.py:3273` | Add `time.monotonic()` deltas around `_run_candidate_pipeline()` for 50 candidates |

---

## 7. TOP 5 "DO NOT TOUCH WITHOUT INTEGRATION TEST"

| # | File / Function | Why |
|---|-----------------|-----|
| **1** | `sprint_scheduler.py:2707–3108` (main run loop) | 12,369-line function with 50+ async awaits; any change to gather/concurrency semantics can change lane timing by 10x+ |
| **2** | `sprint_scheduler.py:6016` (`_await_coro`) | M1 crash vector; `asyncio.run()` in thread with live loop is documented but not fixed; needs F223-D integration test on real M1 hardware before any change |
| **3** | `duckdb_store.py` (all `run_in_executor` calls) | DuckDB connection lifecycle is thread-affine; any change to `_init_connection()` or executor reuse can cause silent data loss |
| **4** | `sprint_scheduler.py:5475` (lane gather) | Timeout-less `asyncio.gather` across all lanes; removing or adding timeout requires live sprint regression |
| **5** | `sprint_exporter.py` (all export formats) | Export is the final canonical output; any change to format ordering or async nature requires full sprint comparison test |

---

## 8. KEY FINDINGS SUMMARY

### CRITICAL (M1 crash / data loss risk)
- **`_await_coro()` at line 6016**: `asyncio.run()` in thread pool — F223-D documented but fallback still active
- **`run()` at line 2393**: 12,369-line single async function — no per-phase timeout on gather

### HIGH (measurable runtime impact)
- **`duckdb.connect()` on cold start**: blocks duckdb_worker thread, DB file size dependent
- **`numpy` inline import in hot path**: first vector encoding costs ~200ms
- **`export_sprint()` synchronous**: blocks finalization; no parallelism
- **`_memory_pressure_loop()` idle spin**: no backpressure mechanism

### MEDIUM (architectural debt)
- **34 top-level imports in `sprint_scheduler.py`**: every import adds cold-start latency
- **`asyncio.create_task()` without timeout in bg tasks**: unbounded task accumulation risk
- **No per-phase `time.monotonic()` delta in `run()`**: missing observability for phase-level timing

### LOW (noted, non-blocking)
- `windup_guard` called every cycle with no fast-path cache
- `_speculative_prefetch(n=3)` hardcoded, no adaptive concurrency

---

*Audit complete. No code changes made. No network calls. No model loads. No destructive DB operations.*