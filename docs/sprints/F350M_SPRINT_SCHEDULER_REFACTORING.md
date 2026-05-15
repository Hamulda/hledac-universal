# SprintScheduler Refactoring — Sprint F350M

## Problem Statement

`runtime/sprint_scheduler.py` (11,908 lines, 148 symbols) is a **god object** that conflates:

| Responsibility | Evidence |
|---|---|
| Lifecycle management | `run()` lines 1738–2260+, `SprintLifecycleRunner` embedded, 56 lifecycle-method matches |
| Memory pressure monitoring | `_memory_pressure_loop()` line 10748, background task management |
| 20 sidecar invocations | `_run_*_sidecar` / `_run_*_advisory` methods, `SidecarDispatcher` |
| Enrichment lifecycle | `_enrich_ct_findings_forensics`, `_enrich_findings_multimodal` |
| Init/close for 4 subsystems | `_init_forensics/multimodal/metrics_registry`, `_close_dedup/forensics/multimodal/metrics_registry` |
| Acquisition planning | `build_acquisition_plan()` call, `_acquisition_plan` state |
| Result accumulation | `_reset_result()` 62 lines, `_result` object with 100+ fields |
| Feed/public/CT branch dispatch | `_run_feed_branch`, `_run_public_branch`, `_run_ct_branch` |
| Pivot execution | `_drain_pivot_queue`, `_execute_pivot`, `_speculative_prefetch` |
| Export coordination | `_run_export`, `_run_cti_export`, `_maybe_export_partial` |

**Every change requires navigating all 148 symbols.**

---

## What Already Exists (Good Extractions)

The file has already extracted several pieces, which validates the approach but they remain **entangled** with the god object:

1. **SprintLifecycleRunner** (line 1765): `setup()`, `tick()`, `ensure_active()`, `windup_guard()`, `sleep_or_abort()`, `post_sleep_gate()`, `is_terminal()`, `abort_requested`, `abort_reason`, `current_phase`
2. **SidecarDispatcher** (line 1784): dispatch bookkeeping for all 20 sidecars
3. **create_sidecar_bus()** (line 1781): `FindingSidecarBus` factory
4. **_LifecycleAdapter** (line 103): Normalizes lifecycle API between runtime/ and utils/

These are already first-class interfaces — they just live inside `SprintScheduler.__init__` as attribute assignments rather than constructor parameters.

---

## Proposed Extraction Plan

### New Modules

#### 1. `runtime/sprint_lifecycle_runner.py` (NEW)
```python
class SprintLifecycleRunner:
    """Owns lifecycle.tick(), windup_guard(), is_terminal(), abort_requested."""
    def __init__(self, lifecycle, adapter):
    def setup(self): ...
    def tick(self, now_monotonic=None): ...
    def ensure_active(self, now_monotonic=None): ...
    def windup_guard(self, now_monotonic, pre_windup_barrier): ...
    def sleep_or_abort(self, seconds): ...
    def post_sleep_gate(self, now_monotonic): ...
    def is_terminal(self): ...
    def abort_requested(self): ...
    @property def current_phase(self): ...
```

#### 2. `runtime/sidecar_orchestrator.py` (NEW — most important)
```python
class SidecarOrchestrator:
    """
    Invokes all sidecars and advisories against accepted findings.
    Sidecar authors can test against this interface without running SprintScheduler.
    Deletion test: if this is deleted, all 20 sidecar call sites must reappear in sprint_scheduler.
    """
    def __init__(self, governor, result_sink):
    async def dispatch_findings(self, source_branch, findings, store, query, sprint_id): ...
    async def run_leak_sentinel_sidecar(self, findings, store, query): ...
    async def run_temporal_archaeology_sidecar(self, findings, store, query): ...
    async def run_pivot_planner_advisory(self, findings): ...
    async def run_resource_governor_advisory(self): ...
    async def run_analyst_brief_advisory(self): ...
    async def run_identity_stitching_sidecar(self, findings, store, query): ...
    async def run_exposure_correlator_sidecar(self, findings, store, query): ...
    # ... all 20 sidecar methods
```

#### 3. `runtime/enrichment_lifecycle.py` (NEW)
```python
class EnrichmentLifecycle:
    """
    Manages forensics enricher, multimodal enricher, LMDB paths, init/flush/close.
    Fail-safe throughout — never raises.
    """
    def __init__(self, config, result):
    async def init(self): ...       # calls _init_forensics + _init_multimodal
    async def flush(self): ...     # calls _flush_forensics + _flush_multimodal
    async def close(self): ...      # calls _close_forensics + _close_multimodal
    async def enrich_ct_findings(self, findings): ...
    async def enrich_findings_multimodal(self, findings): ...
```

#### 4. `runtime/memory_pressure_monitor.py` (NEW)
```python
class MemoryPressureMonitor:
    """
    Background task that adjusts concurrency based on memory pressure.
    Isolated — does not touch SprintScheduler state directly.
    """
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def get_fetch_semaphore(self) -> asyncio.Semaphore: ...
```

#### 5. `runtime/sprint_scheduler_result.py` (NEW)
```python
# Extract SprintSchedulerResult to its own module
@dataclass
class SprintSchedulerResult:
    # All 100+ fields currently in __reset_result
```

### SprintScheduler After Refactoring

```python
class SprintScheduler:
    """Thin coordinator — wires together extracted modules."""

    def __init__(self, config, ct_log_client=None):
        self._config = config
        self._ct_log_client = ct_log_client
        # All internal state removed — injected as None or created lazily

    async def run(
        self,
        lifecycle,              # LifecycleManager — passed in, owned by caller
        sources,
        now_monotonic=None,
        query="",
        duckdb_store=None,
        ct_log_client=None,     # Override __init__ client
        policy_manager=None,
        progress_callback=None,
    ) -> SprintSchedulerResult:
        # Setup phase — create all extracted components
        runner = SprintLifecycleRunner(lifecycle, _LifecycleAdapter(lifecycle))
        runner.setup()
        self._reset_result()

        governor = self._init_governor()
        enrichment = EnrichmentLifecycle(self._config, self._result)
        await enrichment.init()

        sidecar_orchestrator = SidecarOrchestrator(
            governor=governor,
            result_sink=self._result,
            duckdb_store=duckdb_store,
        )

        memory_monitor = MemoryPressureMonitor(
            governor=governor,
            fetch_semaphore_ref=lambda: self._fetch_semaphore,
        )
        await memory_monitor.start()

        # Main loop — delegate to runner
        while not runner.is_terminal():
            # Lifecycle management — delegated to SprintLifecycleRunner
            if self._check_hard_deadline():
                await self._finalize_hard_deadline(...)
                break
            if runner.abort_requested:
                await self._finalize_abort(...)
                break

            phase = runner.tick(now_monotonic)
            cycle_ok = await self._run_one_cycle(...)

            if not cycle_ok:
                continue

            await runner.sleep_or_abort(self._config.cycle_sleep_s)
            if runner.post_sleep_gate(now_monotonic):
                break

        # Teardown — delegate to extracted components
        await enrichment.close()
        await memory_monitor.stop()
        await self._run_export(lifecycle)
        return self._result
```

---

## Extracted Module Boundaries

| Module | Moved Methods | Lines Removed |
|---|---|---|
| `SprintLifecycleRunner` | `tick()`, `windup_guard()`, `is_terminal()`, `abort_requested()`, `sleep_or_abort()`, `post_sleep_gate()`, `setup()`, `ensure_active()` | ~0 (already extracted) |
| `SidecarOrchestrator` | All 20 `_run_*_sidecar()`, `_run_*_advisory()`, `_dispatch_accepted_findings_sidecars()` | ~800 |
| `EnrichmentLifecycle` | `_init_forensics/multimodal`, `_flush_forensics/multimodal`, `_close_forensics/multimodal`, `_enrich_ct_findings_forensics`, `_enrich_findings_multimodal` | ~400 |
| `MemoryPressureMonitor` | `_memory_pressure_loop()` | ~30 |
| `SprintSchedulerResult` | Result dataclass fields, `_reset_result()` | ~200 |

**Total: ~1,430 lines extracted → sprint_scheduler.py shrinks to ~10,500 lines**

---

## Invariants Table

| Test | What It Verifies |
|---|---|
| `test_sidecar_orchestrator_deletion` | If `SidecarOrchestrator` is deleted, all 20 sidecar call sites must reappear in `SprintScheduler` |
| `test_enrichment_lifecycle_isolation` | `EnrichmentLifecycle` can be tested standalone without `SprintScheduler` |
| `test_memory_pressure_monitor_no_scheduler_state` | `MemoryPressureMonitor` does not read/write any `SprintScheduler` internal state |
| `test_lifecycle_runner_boundary` | `SprintScheduler.run()` calls `runner.tick()` and `runner.windup_guard()` — never calls `lifecycle.tick()` directly |
| `test_sidecar_orchestrator_wires_to_result` | All sidecar results flow to `result_sink` — no side effect on orchestrator state |

---

## Phase 1 — Extract SprintLifecycleRunner (Lowest Risk)

Already done at line 1765 — just move to own file:

```
runtime/sprint_lifecycle_runner.py  (NEW)
runtime/sprint_scheduler.py         (REMOVE: lifecycle runner init/attribute)
```

### Verification
```bash
# No behavior change — just module relocation
pytest tests/probe_8sa/ -v
pytest tests/probe_f200a/ -v
```

---

## Phase 2 — Extract SidecarOrchestrator

### Before (sprint_scheduler.py)
```python
# Line 5769
async def _dispatch_accepted_findings_sidecars(self, source_branch, findings, store, query):
    if self._sidecar_dispatcher is None:
        return
    await self._sidecar_dispatcher.dispatch(source_branch=source_branch, findings=findings, ...)
```

### After (sidecar_orchestrator.py)
```python
class SidecarOrchestrator:
    def __init__(self, governor, result_sink):
        self._bus = create_sidecar_bus(governor=governor)
        self._dispatcher = SidecarDispatcher(bus=self._bus, governor=governor, result_sink=result_sink)

    async def dispatch_findings(self, source_branch, findings, store, query, sprint_id):
        if self._dispatcher is None:
            return
        await self._dispatcher.dispatch(source_branch=source_branch, findings=findings, ...)
```

### SprintScheduler After Extraction
```python
class SprintScheduler:
    def __init__(self, config, ct_log_client=None):
        self._config = config
        self._ct_log_client = ct_log_client

    def inject_sidecar_orchestrator(self, orchestrator):
        self._sidecar_orchestrator = orchestrator  # NEW

    # Replace _dispatch_accepted_findings_sidecars with:
    async def _dispatch_accepted_findings_sidecars(self, source_branch, findings, store, query):
        if self._sidecar_orchestrator is None:
            return
        await self._sidecar_orchestrator.dispatch_findings(source_branch, findings, store, query, self.sprint_id or "")
```

### Sidecar Methods to Move
All 20 sidecar methods become `async def run_*` methods on `SidecarOrchestrator`:
- `_run_identity_stitching_sidecar` → `run_identity_stitching(findings, store, query)`
- `_run_exposure_correlator_sidecar` → `run_exposure_correlator(findings, store, query)`
- `_run_leak_sentinel_sidecar` → `run_leak_sentinel(findings, store, query)`
- `_run_temporal_archaeology_sidecar` → `run_temporal_archaeology(findings, store, query)`
- `_run_evidence_triage_sidecar` → `run_evidence_triage(findings, store, query)`
- `_run_sprint_diff_sidecar` → `run_sprint_diff(findings, store, query)`
- `_run_kill_chain_tagging_sidecar` → `run_kill_chain_tagging(findings, store, query)`
- `_run_embedding_sidecar` → `run_embedding(findings, store, query)`
- `_run_wayback_diff_sidecar` → `run_wayback_diff(findings, store, query)`
- `_run_rir_correlator_sidecar` → `run_rir_correlator(findings, store, query)`
- `_run_social_identity_surface_sidecar` → `run_social_identity_surface(findings, store, query)`
- `_run_pivot_planner_advisory` → `run_pivot_planner_advisory()`
- `_run_pivot_executor_advisory` → `run_pivot_executor_advisory()`
- `_run_resource_governor_advisory` → `run_resource_governor_advisory()`
- `_run_analyst_brief_advisory` → `run_analyst_brief_advisory()`
- `_run_ct_to_passivedns_pivot_advisory` → `run_ct_to_passivedns_pivot_advisory()`
- `_run_bgp_advisory_sidecar` → `run_bgp_advisory()`
- `_run_wayback_cdx_deep_sidecar` → `run_wayback_cdx_deep()`

---

## Phase 3 — Extract EnrichmentLifecycle

Move: `_init_forensics`, `_init_multimodal`, `_flush_forensics`, `_flush_multimodal`, `_close_forensics`, `_close_multimodal`, `_enrich_ct_findings_forensics`, `_enrich_findings_multimodal`

### After
```python
class EnrichmentLifecycle:
    def __init__(self, config, result_sink):
        self._config = config
        self._result = result_sink
        self._forensics_enricher = None
        self._multimodal_enricher = None

    async def init(self):
        await self._init_forensics()
        await self._init_multimodal()

    async def flush(self):
        await self._flush_forensics()
        await self._flush_multimodal()

    async def close(self):
        await self._close_forensics()
        await self._close_multimodal()

    async def enrich_ct_findings(self, findings):
        await self._enrich_ct_findings_forensics(findings)
        await self._enrich_findings_multimodal(findings)
```

---

## Phase 4 — Extract MemoryPressureMonitor

Move: `_memory_pressure_loop()` → `MemoryPressureMonitor.start()`

```python
class MemoryPressureMonitor:
    def __init__(self, governor, fetch_semaphore_ref):
        self._governor = governor
        self._fetch_semaphore_ref = fetch_semaphore_ref  # lambda to get current semaphore

    async def start(self):
        self._task = asyncio.create_task(self._run(), name="sprint:memory_pressure_loop")

    async def _run(self):
        while True:
            limits = get_recommended_concurrency()
            self._fetch_semaphore_ref()._value = limits["fetch"]
            interval = 10 if limits["fetch"] <= 2 else 30
            await asyncio.sleep(interval)

    async def stop(self):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
```

---

## Phase 5 — SprintScheduler as Thin Coordinator

After Phases 1-4, `run()` method becomes readable (~300 lines):

```python
async def run(self, lifecycle, sources, now_monotonic=None, query="",
              duckdb_store=None, ct_log_client=None, policy_manager=None,
              progress_callback=None) -> SprintSchedulerResult:
    adapter = _LifecycleAdapter(lifecycle)
    runner = SprintLifecycleRunner(lifecycle, adapter)
    runner.setup()
    self._reset_result()
    self._query = query

    governor = self._init_governor()
    enrichment = EnrichmentLifecycle(self._config, self._result)
    await enrichment.init()

    sidecar_orchestrator = SidecarOrchestrator(governor, self._result)
    self.inject_sidecar_orchestrator(sidecar_orchestrator)

    memory_monitor = MemoryPressureMonitor(governor, lambda: self._fetch_semaphore)
    await memory_monitor.start()

    await self._load_dedup()
    await self._run_mandatory_acquisition_prelude(...)

    try:
        while not runner.is_terminal():
            if not self._check_hard_deadline():
                await self._finalize_hard_deadline(...)
                break
            if self._stop_requested:
                await self._finalize_stop_requested(...)
                break
            if runner.abort_requested:
                await self._finalize_abort(...)
                break

            phase = runner.tick(now_monotonic)
            await self._maybe_dispatch_nonfeed_probe_lanes(query, duckdb_store)

            if not runner.windup_guard(now_monotonic, pre_windup_barrier=...):
                if self._result.cycles_started >= self._config.max_cycles:
                    await self._finalize_max_cycles(...)
                    break

                self._result.cycles_started += 1
                cycle_ok = await self._run_one_cycle(lifecycle, ordered_sources, ...)
                self._tick_metrics_on_cycle_end()

                if progress_callback:
                    progress_callback(self._result, phase, elapsed_s)

                if not cycle_ok:
                    continue

            await runner.sleep_or_abort(self._config.cycle_sleep_s)
            if runner.post_sleep_gate(now_monotonic):
                await self._maybe_export_partial(lifecycle)
                break

            self._result.cycles_completed += 1
    finally:
        await enrichment.close()
        await memory_monitor.stop()
        await self._run_export(lifecycle)

    return self._result
```

---

## Dependencies After Refactoring

```
runtime/sprint_scheduler.py
├── runtime/sprint_lifecycle_runner.py    (extracted)
├── runtime/sidecar_orchestrator.py        (extracted)
├── runtime/enrichment_lifecycle.py        (extracted)
├── runtime/memory_pressure_monitor.py      (extracted)
├── runtime/sprint_scheduler_result.py     (extracted)
├── knowledge/duckdb_store.py              (no change)
├── knowledge/graph_service.py             (no change)
├── brain/hermes3_engine.py                (no change)
└── fetch_coordinator.py                   (no change)
```

---

## Test Strategy

### New Tests
- `tests/probe_f350m/test_sidecar_orchestrator_wired.py` — verify all 20 sidecars are called through orchestrator
- `tests/probe_f350m/test_enrichment_lifecycle_isolated.py` — test forensics/multimodal without full scheduler
- `tests/probe_f350m/test_memory_pressure_monitor_no_scheduler_state.py` — verify no SprintScheduler internal state touched
- `tests/probe_f350m/test_sprint_scheduler_thin_coordinator.py` — verify run() delegates to extracted modules

### Existing Tests (must continue passing)
- All 136 existing test files importing `SprintScheduler` — verify no import breakage
- `probe_8sa` (lifecycle adapter), `probe_f200a` (prefetch oracle), `probe_f202b-f202i` (sidecar lanes) — all must pass without modification

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| 136 test files import SprintScheduler | Use `TYPE_CHECKING` for imports, provide backward-compatible re-export in sprint_scheduler.py |
| Sidecar interfaces have implicit dependencies on `self._result`, `self._governor`, `self._sidecar_bus` | Pass all dependencies explicitly via constructor — no implicit `self` access |
| Memory pressure monitor needs access to `_fetch_semaphore` | Use `fetch_semaphore_ref: Callable[[], asyncio.Semaphore]` — monitor never holds semaphore reference |
| 20 sidecar methods share state via `self._result` | Result is passed as `result_sink` — sidecars call `result_sink.accepted_findings += n` directly |
| `_enrich_ct_findings_forensics` and `_enrich_findings_multimodal` called from multiple call sites | Make `EnrichmentLifecycle` methods have same signature, wire via orchestrator |
| `SprintScheduler.__init__` has 15+ injected dependencies | Extract to `SprintSchedulerDeps` dataclass, passed to run() or injectable |

---

## Implementation Order

1. **Phase 1**: `SprintLifecycleRunner` → own file (already exists as class, just move)
2. **Phase 2**: `SidecarOrchestrator` → own file (largest extraction, ~800 lines)
3. **Phase 3**: `EnrichmentLifecycle` → own file (~400 lines)
4. **Phase 4**: `MemoryPressureMonitor` → own file (~30 lines)
5. **Phase 5**: `SprintSchedulerResult` → own file + thin `run()` refactor
6. **Phase 6**: Update `__init__.py` exports, verify 136 test files pass
7. **Phase 7**: Commit with `git add runtime/sprint_lifecycle_runner.py runtime/sidecar_orchestrator.py runtime/enrichment_lifecycle.py runtime/memory_pressure_monitor.py runtime/sprint_scheduler_result.py runtime/sprint_scheduler.py`

---

## Success Criteria

- [ ] `SprintScheduler` run() method ≤ 300 lines
- [ ] Each extracted module has its own test file
- [ ] 136 existing tests pass without modification (backward-compatible re-exports)
- [ ] Deletion test: `SidecarOrchestrator` can be removed and re-added without touching `SprintScheduler` internals
- [ ] Sidecar authors can test against `SidecarOrchestrator` interface without running full scheduler