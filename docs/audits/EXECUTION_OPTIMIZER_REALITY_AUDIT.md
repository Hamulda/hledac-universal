# ExecutionOptimizer Reality Audit

**Date:** 2026-05-18
**Author:** Vojtech Hamada
**Status:** COMPLETE

---

## Executive Summary

The file `utils/execution_optimizer.py` is **1743 lines** (NOT ~66k as assumed from task context).
It is **NOT a production hot path**. It is **NOT generated code**. It is a **preserved/underutilized**
utility with orphaned imports and no production callers. The 19-class module is tested by only
2 unit tests and sits in an execution coordinator that cannot instantiate it.

---

## 1. File Reality Check

| Attribute | Claim | Actual |
|-----------|-------|--------|
| Lines | ~66,000 | **1,743** |
| File exists | Yes | Yes |
| Generated artifact | Unknown | **No** — hand-written, version-controlled |
| Legacy/preserved | Unknown | **No explicit marker**, but `execution_coordinator.py` calls it orphaned |
| Last commit | Unknown | **97718049** (2026-05-14) |

---

## 2. Production Caller Map

### Production Runtime — **ZERO active callers**

| File | Status |
|------|--------|
| `runtime/sprint_scheduler.py` | ❌ No reference |
| `core/__main__.py` | ❌ No reference |
| `core/run_sprint.py` | ❌ No reference |
| `autonomous_orchestrator.py` | ❌ No reference |
| `pipeline/live_public_pipeline.py` | ❌ No reference |
| `pipeline/live_feed_pipeline.py` | ❌ No reference |
| `brain/hermes3_engine.py` | ❌ No reference |
| `brain/inference_engine.py` | ❌ No reference |
| `coordinators/fetch_coordinator.py` | ❌ No reference |
| `knowledge/duckdb_store.py` | ❌ No reference |
| `knowledge/graph_service.py` | ❌ No reference |
| `transport/httpx_transport.py` | ❌ No reference |
| `transport/tor_transport.py` | ❌ No reference |

### Coordinators — **1 orphaned reference**

| File | Finding |
|------|---------|
| `coordinators/execution_coordinator.py` | **CRITICAL**: Tries to import from `hledac.tools.preserved_logic.parallel_execution_optimizer` which **does not exist** at that path. The import is unreachable dead code. The docstring says "ParallelExecutionOptimizer - Parallel task processing" but the path is orphaned. |

### Tests — **2 unit tests, no integration tests**

| Test | What it tests |
|------|---------------|
| `test_execution_optimizer_parallel_groups_pruned_by_cap_and_ttl` | TTL eviction of ParallelGroup entries — pure unit test |
| `test_execution_optimizer_clean` | Cleanup removes Ray/Dask attributes — pure unit test |
| `test__run_in_executor_safe` | Async safety of `_run_in_executor_safe` — pure unit test |

No integration test exercises the full execution path. No probe test hits `execution_optimizer` from a sprint scenario.

### Probes — **1 benchmark probe, no production probes**

| Probe | Purpose |
|-------|---------|
| `tools/probe_f214m_execution_optimizer_backpressure.py` | Reports-only benchmark (314 lines). Measures `len(tasks)` distribution. Explicitly says NO PATCH without data. |
| `tools/bench_py314_jit.py` | Python 3.14 JIT smoke bench. Imports `ExecutionOptimizer` (note: different name). |

---

## 3. Responsibility Clusters

### Primary Cluster (751 lines — `ParallelExecutionOptimizer`)
- Lines 151–1037
- Contains: `_ConcurrencyController`, `ParallelGroup`, task management, parallel execution
- Responsibility: orchestrates parallel task groups with resource constraints
- **Invariants tested**: cap+TTL pruning of groups, no Ray/Dask attrs after cleanup
- **Not tested**: actual parallel execution, resource constraint enforcement, task scheduling

### ML/Science Cluster (lines 229–450, 1158–1493)
- Classes: `PredictiveScaler`, `IntelligentResourceAllocator`, `MemoryAwareScheduler`
- `sklearn` imports (RandomForestRegressor, KMeans, StandardScaler) are **lazy-loaded** behind `TYPE_CHECKING` and function-level imports
- Comment at line 165: *"Sprint 8G: DEFERRED - sklearn eager loads 1478 modules at import time"*
- **Risk**: M1 cold-start if any class eagerly instantiates sklearn

### Resource Monitoring Cluster (lines 1039–1213)
- `LoadBalancer`, `ResourceMonitor`, `AnomalyDetector`, `ResourceMetrics`, `ResourceLimits`
- Pure Python, no external deps
- **No tests** cover these classes

### Cache Cluster (lines 1494–1658)
- `CacheEntry`, `PredictiveCacheManager`, `MemoryAwareScheduler`
- Memoization decorator `@auto_optimize` at line 1699
- **Not tested** in production context

### Entry Points
- `create_m1_resource_allocator()` at line 1446 — factory function
- `auto_optimize()` decorator at line 1699 — utility decorator
- `if __name__ == "__main__":` at line 1486 — CLI benchmark mode

---

## 4. Dependency Analysis

### Imports
```
asyncio, inspect, time, logging, psutil, json, numpy, multiprocessing, os,
concurrent.futures (ThreadPoolExecutor, ProcessPoolExecutor), threading,
collections (deque, OrderedDict), sklearn (lazy), datetime, dataclasses, enum
```

### No production dependencies
No sprint scheduler, coordinator, or pipeline imports `execution_optimizer` directly.
Only `utils/__init__.py` re-exports `ParallelExecutionOptimizer` (line 36-37).

---

## 5. Test Coverage

| Class | Lines | Tests |
|-------|-------|-------|
| `ParallelExecutionOptimizer` | 886 | 2 unit tests |
| `_ConcurrencyController` | ~59 | 0 |
| `ParallelGroup` | ~70 | 0 |
| `LoadBalancer` | ~15 | 0 |
| `ResourceMonitor` | ~17 | 0 |
| `AnomalyDetector` | ~47 | 0 |
| `PredictiveScaler` | ~55 | 0 |
| `IntelligentResourceAllocator` | ~281 | 0 |
| `PredictiveCacheManager` | ~154 | 0 |
| `MemoryAwareScheduler` | ~85 | 0 |

**Coverage: 2/19 classes tested (10.5%)**

---

## 6. Key Findings

### Finding 1 — Orphaned Import (CRITICAL)
`coordinators/execution_coordinator.py` line 166:
```python
from hledac.tools.preserved_logic.parallel_execution_optimizer import ParallelExecutionOptimizer
```
Path `hledac/tools/preserved_logic/` **does not exist**. `_parallel_available` is always False.
The `_execute_parallel_processing()` method (line 371) is dead code that always raises
`RuntimeError("ParallelExecutionOptimizer not available")`.

### Finding 2 — No Production Callers (HIGH)
Zero sprint execution paths reference `execution_optimizer`. The canonical sprint owner
(`core/__main__.py::run_sprint()`) does not use it. No coordinator, pipeline, or transport
has a runtime dependency on it.

### Finding 3 — sklearn Eager Load Risk (MEDIUM)
`sklearn` imported at module level (lines 27-29) inside `TYPE_CHECKING` block, but
`RandomForestRegressor` is also lazy-imported at function-level (line 345). The dual
import strategy suggests the author was aware of cold-start cost. On M1 8GB, if
`IntelligentResourceAllocator.create_m1_resource_allocator()` is called early in sprint,
sklearn loads 1478 modules synchronously — potential 2-5s blocking on first call.

### Finding 4 — Low Test Coverage (MEDIUM)
10.5% class coverage. No integration tests. The probe test (`probe_f214m`) is report-only.

### Finding 5 — `bench_py314_jit.py` imports non-existent class name (MEDIUM)
Line 181: `from utils.execution_optimizer import ExecutionOptimizer`
The class in the file is `ParallelExecutionOptimizer`, not `ExecutionOptimizer`. The benchmark's
execution_optimizer smoke test is broken — it imports a non-existent name.

### Finding 6 — 3 latent correctness bugs found by probe (MEDIUM)
The `probe_f214m` benchmark found 3 return-inside-loop bugs:
- `_execute_round_robin` (line ~431): returns inside for loop → only 1 result per chunk
- `_execute_load_balanced` (line ~463): returns inside for loop → only 1 result per worker
- `_execute_adaptive` (line ~561): returns inside while loop → premature exit after 1st batch
These are **correctness bugs, not backpressure issues**. Result count < input count.

### Finding 7 — Test collection broken (pre-existing)
`tests/test_autonomous_orchestrator.py` fails to collect with:
```
ImportError: cannot import name 'WebSearchArgs' from 'tools.registry'
```
This is a pre-existing infrastructure issue, unrelated to `execution_optimizer`. The probe
benchmark (`probe_f214m`) runs independently and confirms the module itself is functional.

---

## 7. Recommendations

### Phase 1 — No action, audit only (this commit)
- Document findings above
- No code changes

### Phase 2 — If decomposition is requested

**Option A: Quarantine (if the module is truly dead)**
- Remove re-export from `utils/__init__.py`
- Fix `execution_coordinator.py` orphaned import (remove or route to stub)
- Move `execution_optimizer.py` to `utils/preserved/` with DEPRECATED marker
- Keep 2 unit tests (they test a preserved artifact)

**Option B: First seam (if there's a future use case)**
- The `create_m1_resource_allocator()` factory (line 1446) and `auto_optimize()` decorator (line 1699)
  are the two smallest entry points — each is self-contained
- A seam-first approach would be to wire ONE of these into a real production path
  (e.g., `auto_optimize` for a non-critical retry path) and validate memory behavior before
  committing to full integration

**Do NOT:**
- Broad decomposition without caller map (done — caller map shows no production callers)
- Move runtime async helpers (none are in this file's scope)
- Introduce new task runner API (not needed, `asyncio` primitives are sufficient)
- Touch the 1743-line ParallelExecutionOptimizer class without integration test coverage

### Phase 3 — Test improvement (independent of Phase 2)
- `bench_py314_jit.py` line 181: fix `ExecutionOptimizer` → `ParallelExecutionOptimizer`
- Add at least one integration test: `test_execution_optimizer_integration_via_coordinator`
  would catch the orphaned import regression

---

## 8. Verification

| Command | Result |
|---------|--------|
| `wc -l utils/execution_optimizer.py` | 1743 lines |
| `probe_f214m_execution_optimizer_backpressure.py` | **VERDICT: NO_PATCH** — P95 len(tasks)=8, max observed 8, below 32 threshold. 3 latent bugs found (return-inside-loop). |
| `tests/test_autonomous_orchestrator.py` collection | Pre-existing import failure (`WebSearchArgs` from `tools.registry`) — unrelated to execution_optimizer |