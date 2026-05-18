# SPRINT RUNTIME INSTRUMENTATION PLAN

**Date:** 2026-05-18
**Author:** Based on `SPRINT_CRITICAL_PATH_BOTTLENECK_AUDIT.md`
**Goal:** Measure, not fix — prepare runtime observability for sprint critical path bottlenecks
**Forbidden:** No invasive scheduler refactor; no live network required

---

## 1. TIMER POINTS

All timer points use `time.monotonic()` (not `time.time()`) for wall-time deltas. Delta is computed as `t_after - t_before`. Each phase emits `(phase_name, elapsed_s, metadata_dict)`.

### 1.1 Preflight

| Label | Call site | Notes |
|-------|----------|-------|
| `preflight_start` | `SprintScheduler.__init__()` entry | Before any async work |
| `preflight_end` | After `_init_connection()` + lifecycle setup | Before `run()` is called |
| `preflight_duckdb_connect` | `_init_connection()` return | Isolated DuckDB init cost |

### 1.2 Acquisition Prelude

| Label | Call site | Notes |
|-------|----------|-------|
| `prelude_start` | `_run_mandatory_acquisition_prelude()` entry | F209A lane |
| `prelude_end` | After all prelude findings ingested | Before main run loop |
| `prelude_findings_count` | `len(prelude_findings)` | Gauge, not delta |

### 1.3 Each Lane (per iteration)

| Label | Call site | Notes |
|-------|----------|-------|
| `lane_start_{name}` | Inside each branch task before `run()` | `name` ∈ {`feed`, `public`, `ct`, `academic`, `nonfeed`} |
| `lane_end_{name}` | After lane task completes | Must pair with `lane_start_{name}` |
| `lane_accepted_{name}` | `lane_result.accepted_findings` | Count of accepted findings |

### 1.4 Public Pipeline (per candidate)

| Label | Call site | Notes |
|-------|----------|-------|
| `pipeline_run_start` | `live_public_pipeline.run()` entry | |
| `pipeline_discovery_start` | Before `DuckDuckGoAdapter` discovery | |
| `pipeline_discovery_end` | After discovery returns | |
| `pipeline_fetch_start` | Before `public_fetcher` | |
| `pipeline_fetch_end` | After fetcher returns | |
| `pipeline_candidate_count` | Number of candidates produced | Gauge |
| `pipeline_run_end` | `live_public_pipeline.run()` exit | |

### 1.5 Ingest Batch

| Label | Call site | Notes |
|-------|----------|-------|
| `ingest_batch_start` | `async_ingest_findings_batch()` entry | |
| `ingest_batch_end` | After `asyncio.gather(*_bg)` completes | |
| `ingest_batch_count` | `len(findings)` | Gauge |
| `ingest_duckdb_write_start` | Before `run_in_executor(duckdb_worker, …)` | Isolates DuckDB write cost |
| `ingest_duckdb_write_end` | After executor future resolves | |

### 1.6 Graph Accumulation

| Label | Call site | Notes |
|-------|----------|-------|
| `graph_accum_start` | `_accumulate_findings_to_graph()` entry | F198A wired |
| `graph_accum_end` | After all IOC upserts complete | |
| `graph_upsert_count` | `len(iocs) + len(rels)` | Gauge |

### 1.7 Windup Barrier

| Label | Call site | Notes |
|-------|----------|-------|
| `windup_barrier_start` | `should_enter_windup()` first True → before barrier | |
| `windup_barrier_end` | After `_ensure_nonfeed_predispatch_before_finalization()` returns | |
| `windup_guard_call` | Each `windup_guard()` invocation | Existing `_result.windup_guard_call_count` incremented here |
| `windup_guard_duration_s` | `windup_guard()` elapsed | Existing `_result.prewindup_barrier_duration_s` |

### 1.8 Export

| Label | Call site | Notes |
|-------|----------|-------|
| `export_start` | `export_sprint()` entry | |
| `export_markdown_end` | After markdown format writes | |
| `export_jsonld_end` | After JSON-LD writes | |
| `export_stix_end` | After STIX writes | |
| `export_end` | After all formats + parquet flush | |

---

## 2. METRICS

All metrics collected fail-soft (any exception in metrics path → logged, not raised). No metric collection may block the sprint path.

### 2.1 Wall Time

- **Per-phase delta** — `time.monotonic()` before/after each timer point above
- **Total sprint wall time** — recorded in `SprintSchedulerResult.loop_duration_ms` (already exists as `loop_duration_s`)
- **Per-lane elapsed** — stored in `SprintSchedulerResult.lane_elapsed_s` dict

### 2.2 Event Loop Lag

Measured via `asyncio.get_event_loop().slow_callback_duration()` (Python 3.11+) or a manual `loop.time() - loop.monotonic()` delta around a zero-duration await.

```
def measure_loop_lag(loop) -> float:
    t0 = loop.monotonic()
    await asyncio.sleep(0)
    return loop.monotonic() - t0
```

Called once per main-run-loop iteration. Emitted as `loop_lag_s`.

### 2.3 Task Count

| Metric | How |
|--------|-----|
| `bg_task_count` | `len(_bg_tasks)` sampled at windup entry |
| `pending_task_count` | `len([t for t in asyncio.all_tasks() if not t.done()])` sampled at windup |
| `lane_task_count` | `len(tasks)` inside the `asyncio.gather(*_tasks)` call |

### 2.4 Queue Sizes

| Metric | Source |
|--------|--------|
| `duckdb_queue_depth` | Approximated: `len(_pending_upserts)` in `duckdb_store.py` at ingest start |
| `parquet_flush_queue` | Tracked if `_maybe_flush_to_parquet()` task was created but not yet running |

### 2.5 Accepted Findings

| Metric | Source |
|--------|--------|
| `findings_accepted_total` | Sum of all lane `accepted_findings` counts |
| `findings_rejected_total` | `total - accepted` (from `CandidateAcceptanceReport`) |
| `findings_ingested_total` | Count of findings written to DuckDB |
| `findings_graph_upserted` | Count of IOC/rel upserts to graph service |

### 2.6 Memory RSS

```
import psutil, os
rss_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
```

Sampled at:
- Sprint start (before `run()`)
- Before each lane gather
- At windup entry
- At export start

Stored as `memory_rss_mb` timestamps array in result.

---

## 3. IMPLEMENTATION SKETCH

### 3.1 `SprintTimer` Context Manager

```python
# tools/sprint_timer.py

import time, contextlib, threading
from typing import Optional

class SprintTimer:
    """
    Fail-soft wall-time timer with structured output.
    All methods guard with try/except — metrics collection
    must never alter sprint behavior.
    """

    def __init__(self, emit_fn: Optional[callable] = None):
        self._emit_fn = emit_fn  # (label, elapsed_s, metadata) -> None
        self._stack: list[tuple[str, float]] = []

    @contextlib.contextmanager
    def phase(self, label: str, **metadata):
        t0 = time.monotonic()
        self._stack.append((label, t0))
        try:
            yield
        finally:
            t1 = time.monotonic()
            start_label, start_t = self._stack.pop()
            elapsed = t1 - start_t
            if self._emit_fn:
                try:
                    self._emit_fn(label, elapsed, metadata)
                except Exception:
                    pass  # fail-soft: never propagate

    def gauge(self, label: str, value: float):
        """Record a point-in-time metric."""
        if self._emit_fn:
            try:
                self._emit_fn(label, 0.0, {"value": value, "type": "gauge"})
            except Exception:
                pass
```

### 3.2 Integration with `SprintScheduler`

- Instantiate `SprintTimer` in `SprintScheduler.__init__()` as `self._timer`
- Store emitted events in a bounded list: `self._timer_events: list[dict]` with maxlen=500
- On `SprintSchedulerResult` construction, attach `_timer_events` as `_timer_events: list[dict] | None`
- The timer is always present; events are collected when `_timer` is wired

### 3.3 Fail-Soft Guarantees

```python
# Every timer call is wrapped:
try:
    self._timer.phase("prelude_end", findings_count=n)
except Exception:
    self._logger.debug("timer phase failed", exc_info=True)
```

### 3.4 No Blocking I/O in Metrics Path

- Metrics collection uses only `time.monotonic()` and in-memory reads
- `psutil` is called outside the hot path (sampled, not continuous)
- No file I/O, no network I/O, no DuckDB queries in any timer call site
- Event loop lag measurement uses `await asyncio.sleep(0)` — zero overhead when lag is zero

### 3.5 Event Loop Lag Measurement

```python
async def _measure_loop_lag(self) -> float:
    loop = asyncio.get_running_loop()
    t0 = loop.monotonic()
    await asyncio.sleep(0)
    return loop.monotonic() - t0
```

Called from inside `run()` main loop (not from a timer callback).

---

## 4. BENCHMARK COMMAND

### 4.1 Synthetic Sprint Benchmark

```bash
# No network. Small fixture dataset. Measures wall-time per phase.
pytest hledac/universal/tests/benchmark/test_sprint_instrumentation.py \
    --hermetic \
    --fixture=sprint_fixtures/minimal_5pct.yaml \
    -v
```

### 4.2 Fixture

`tests/benchmark/sprint_fixtures/minimal_5pct.yaml`:
- 5% of a production query (≤10 seed domains, ≤3 CT labels)
- No live fetches — all adapters mocked
- 10 findings total (5 accepted, 5 rejected)
- ~30 second simulated wall time cap

### 4.3 Expected Output

The benchmark produces:
- Per-phase wall time table (preflight, prelude, each lane, pipeline, ingest, graph, windup, export)
- Loop lag histogram (p50, p95, p99)
- Task count at windup
- Memory RSS delta (start → windup → end)
- Accepted/rejected/ingested finding counts

### 4.4 Probe Tests

| Probe | What it verifies |
|-------|-----------------|
| `probe_sprint_timer_phases` | All 8 timer point labels fire at least once in a synthetic sprint |
| `probe_sprint_timer_failsoft` | Timer exception does not propagate (caught, logged) |
| `probe_loop_lag_measured` | `loop_lag_s` appears in timer events |
| `probe_memory_rss_sampled` | `memory_rss_mb` appears at ≥3 timestamps |
| `probe_findings_count_match` | `findings_accepted_total` = sum of lane accepted counts |
| `probe_windup_barrier_timed` | `windup_barrier_start` → `windup_barrier_end` delta < 1ms in no-op case |

---

## 5. FORBIDDEN

- **No invasive scheduler refactor** — do not restructure `run()`, do not add timeouts to existing `asyncio.gather()` calls, do not change lane concurrency
- **No live network required** — all benchmark probes use mocked adapters; live fetches are never part of the benchmark
- **No new thread creation in metrics path** — no `ThreadPoolExecutor` inside timer calls
- **No blocking I/O in metrics path** — no file reads, no DB queries, no network calls
- **Do not instrument `_await_coro()`** — this is the F223-D M1 crash vector; instrumentation of that path requires separate M1 hardware testing

---

## 6. PHASE ORDER SUMMARY

```
preflight_start → preflight_end
    ↓
prelude_start → prelude_end
    ↓
[loop: while not is_terminal()]
    ├── lane_start_{name} → lane_end_{name}   (parallel)
    │     └── pipeline_run_start → pipeline_run_end
    ├── ingest_batch_start → ingest_batch_end
    │     └── ingest_duckdb_write_start → ingest_duckdb_write_end
    ├── graph_accum_start → graph_accum_end
    └── [loop lag measured each iteration]
    ↓
windup_barrier_start → windup_barrier_end
    ↓
export_start → export_end
```

---

*Plan only. No implementation. No code changes.*
