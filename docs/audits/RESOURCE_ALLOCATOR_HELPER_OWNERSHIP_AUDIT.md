# Resource Allocator Helper Ownership Audit

**File:** `coordinators/resource_allocator.py`
**Audited:** 2026-05-18
**Scope:** `_ResourceCapacitySampler`, `ResourceAwareScheduler`, `ParallelExecutionOptimizer`, `IntelligentResourceAllocator`, `CapacitySnapshot`

---

## Caller Map

| Class | Production | Coordinators | Tests | Docs Only |
|---|---|---|---|---|
| `_ResourceCapacitySampler` | `IntelligentResourceAllocator` (owner) | — | `probe_memory_layer.py` | — |
| `IntelligentResourceAllocator` | — (exported, not instantiated in prod) | `execution_coordinator.py` (import attempt fails silently) | `probe_memory_layer.py`, `test_sprint55.py` | `ARCHITECTURE_MAP.py` |
| `ResourceAwareScheduler` | `resource_allocator.main()` CLI only | — | `probe_resource_allocator.py` | `ARCHITECTURE_MAP.py` |
| `ParallelExecutionOptimizer` | — (stale import path in execution_coordinator) | — | — | `ARCHITECTURE_MAP.py` |
| `CapacitySnapshot` | `_ResourceCapacitySampler` | — | `probe_memory_layer.py` (mock) | — |

---

## Per-Class Analysis

### 1. `_ResourceCapacitySampler` — **REAL SEAM, ACTIVE**

**Status:** Internal helper, NOT orphaned.

**Owner:** `IntelligentResourceAllocator` (instantiated at `__init__` line 224).

**Public surface:**
- `async sample() -> CapacitySnapshot` — called by `IntelligentResourceAllocator.get_cur_capacity()`

**Real invariants:**
- TTL caching: `_CPU_TTL_S = 3.0`, `_METAL_TTL_S = 300.0`
- Double-checked locking via `asyncio.Lock()` per field
- `asyncio.to_thread` offload for ALL blocking calls (`_get_cpu_sync`, `_get_metal_sync`)
- `CapacitySnapshot` is `@dataclass(frozen=True)`

**Test callers:**
- `tests/probe_memory_layer.py::TestResourceCapacitySamplerOffload` — 5 tests verifying:
  - `asyncio.to_thread` is used for CPU reads
  - `psutil.cpu_percent(interval=0.0)` (non-blocking) is used, NOT `interval=1`
  - Metal cache prevents repeated `system_profiler` calls within 300s TTL
  - `TimeoutExpired` from `system_profiler` returns `False` (fail-soft)

**Deletion impact:** Removing it breaks `IntelligentResourceAllocator.get_cur_capacity()` which is the canonical resource sampling seam. Complexity does NOT disappear — it must be replaced, not deleted.

**Recommendation:** Keep inline as private helper. No promotion needed.

---

### 2. `IntelligentResourceAllocator` — **ACTIVE**

**Status:** Exported, has test coverage, designed for production use.

**Public surface:**
- `async get_cur_capacity() -> ResourceCapacity` — canonical resource sampling
- `async can_use_ane() -> bool` — ANE availability check
- `async get_recommended_concurrency(task_type) -> int` — concurrency hint
- `async req_resources(req) -> bool` — resource request
- `async release_resources(task_id)` — release
- `async monitor_and_optimize()` — background monitor loop
- `get_alloc_stats() -> dict`
- `export_alloc_report(filepath)`

**Real state:**
- `_pending_requests_dict` — bounded O(1) dict (`MAX_PENDING_RESOURCE_REQUESTS = 1000`)
- `active_allocations` — live allocations
- `completed_allocations` — `deque(maxlen=2000)` (bounded, M218C)
- `_capacity_sampler` — `_ResourceCapacitySampler` instance
- `_prediction_model`, `_anomaly_detector`, `_scaler` — lazy sklearn (fail-soft)

**Production callers:**
- None found beyond tests. Exported from `__init__.py` and `_catalog.py`
- `execution_coordinator.py` attempts import from `hledac.tools.preserved_logic.parallel_execution_optimizer` (fails silently via try/except)
- `probe_memory_layer.py` line 209 instantiates it for testing

**Test callers:**
- `tests/probe_memory_layer.py::test_get_current_capacity_uses_sampler`
- `tests/test_sprint55.py` — multiple tests with mocked `_load_config`

**Deletion impact:** Complexity collapses into callers. Would need to replicate `get_cur_capacity()` seam + resource history + bounded allocations.

**Recommendation:** Keep. Consider promoting `get_cur_capacity()` to a standalone public API since it's the canonical M1 resource sampling path.

---

### 3. `ResourceAwareScheduler` — **ACTIVE (CLI integration only)**

**Status:** Has dedicated test suite, real task lifecycle invariants.

**Public surface:**
- `async schedule_task(task_id, task_func, resource_req) -> bool`
- `active_task_count() -> int`
- `async shutdown(timeout=30.0)`

**Real state:**
- `_tasks: dict[str, asyncio.Task]` — task registry
- `_shutdown_event: asyncio.Event | None`

**Real invariants:**
- Task lifecycle: create → register → done_callback cleanup → remove from registry
- `done_callback` logs non-`CancelledError` exceptions, removes task from registry
- `CancelledError` re-raised after cleanup (not swallowed)
- Shutdown waits for tasks, cancels after timeout, drains pending

**Production callers:**
- `resource_allocator.main()` CLI only (lines 901+)

**Test callers:**
- `tests/probe_resource_allocator.py::TestResourceAwareSchedulerTaskLifecycle` — 12 tests covering all invariants above

**Deletion impact:** Removes task lifecycle management. Would need to replace if scheduler pattern is adopted.

**Recommendation:** Keep. If `IntelligentResourceAllocator` is kept, `ResourceAwareScheduler` provides a coherent task-scheduling pattern. If deleted, complexity is LOW — it mainly wraps the allocator with task registry.

---

### 4. `ParallelExecutionOptimizer` — **ORPHANED EXTRACTION**

**Status:** Dead code. Failed extraction attempt.

**Evidence:**
1. `execution_coordinator.py` line 166 imports from `hledac.tools.preserved_logic.parallel_execution_optimizer` — this path does NOT exist (confirmed in `broken_imports.json`)
2. The import is wrapped in try/except, so it fails silently — `_parallel_executor` stays `None`
3. `_execute_parallel_processing()` line 379 raises `RuntimeError("ParallelExecutionOptimizer not available")` if `_parallel_executor` is falsy — this is the LIVE path
4. `ParallelExecutionOptimizer.optimize_parallel_exec()` has a DIFFERENT signature than what `execution_coordinator.py` calls (`execute_parallel(tasks)` vs `optimize_parallel_exec(tasks, max_parallel_tasks=None)`)
5. **Zero test coverage**
6. **Zero production instantiations** (not even `resource_allocator.main()` uses it — the `main()` uses `IntelligentResourceAllocator` + `ResourceAwareScheduler` + `ParallelExecutionOptimizer` directly, but that `main()` is a CLI test harness, not production code)

**Deletion impact:** NONE. Removing it reduces complexity. The execution coordinator already handles the unavailable state correctly (raises `RuntimeError`).

**Deletion plan:**
1. Remove `class ParallelExecutionOptimizer` from `resource_allocator.py`
2. Remove `main()` block that instantiates it (lines 901+)
3. Remove `ParallelExecutionOptimizer` from `__init__.py` exports
4. Remove from `_catalog.py`
5. Remove stale import block from `execution_coordinator.py` lines 164-176 (optional cleanup)
6. No test changes needed (no tests touch this class)

**Recommendation:** Delete as part of this audit commit.

---

### 5. `CapacitySnapshot` — **ACTIVE (dataclass)**

**Status:** Data transfer object, `@dataclass(frozen=True)`.

**Role:** Return type of `_ResourceCapacitySampler.sample()`. Immutable. No standalone identity.

**Deletion impact:** Would need to replace with a plain dict or a different dataclass. LOW.

**Recommendation:** Keep as internal DTO.

---

## Summary Table

| Class | Status | Reason | Recommendation |
|---|---|---|---|
| `_ResourceCapacitySampler` | ACTIVE (real seam) | Internal helper of `IntelligentResourceAllocator`; `asyncio.to_thread` offload + TTL cache; 5 tests | Keep inline |
| `IntelligentResourceAllocator` | ACTIVE | Exported, test-covered, canonical resource sampling seam | Keep; consider promoting `get_cur_capacity()` |
| `ResourceAwareScheduler` | ACTIVE (CLI only) | Task lifecycle with 12 dedicated tests; real invariants | Keep |
| `ParallelExecutionOptimizer` | ORPHANED | Zero production callers; stale import path; signature mismatch with execution_coordinator; zero tests | DELETE |
| `CapacitySnapshot` | ACTIVE (DTO) | Frozen dataclass; internal DTO for sampler | Keep inline |

---

## Deletion Plan for `ParallelExecutionOptimizer`

**Files to edit:**

1. **`coordinators/resource_allocator.py`**
   - Delete `class ParallelExecutionOptimizer` (lines ~870-900)
   - Delete `main()` block that uses it

2. **`coordinators/__init__.py`**
   - Remove `ParallelExecutionOptimizer` from export list

3. **`coordinators/_catalog.py`**
   - Remove `'ParallelExecutionOptimizer'` from catalog

4. **`coordinators/execution_coordinator.py`** (optional cleanup)
   - Remove stale try/except import block (lines 164-176) — the import path `hledac.tools.preserved_logic.parallel_execution_optimizer` doesn't exist and never did

**Do NOT delete:** No tests reference `ParallelExecutionOptimizer`, so no test changes needed.

---

## Non-Migration Note

This audit does NOT migrate to `core/resource_governor` or `utils/uma_budget`. Those are separate code paths with different mandates (M1ResourceGovernor is advisory/read-only; UMA budget is memory pressure monitoring). `IntelligentResourceAllocator` manages task-level resource allocation with prediction and autoscaling — a distinct responsibility.

---

## Test Command

```bash
pytest tests/probe_resource_allocator.py tests/probe_memory_layer.py -v -q 2>&1 | tail -20
```

Expected: all probe tests pass (no changes to tested behavior).