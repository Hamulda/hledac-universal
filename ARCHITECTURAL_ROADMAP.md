

### H6: Parallelization Opportunities (3 Major)

| Location | Current | Potential | Est. Speedup |
|----------|---------|----------|--------------|
| `_phases.py:753-771` (Report+RL+ToT) | Sequential | `asyncio.gather` | **~3x** |
| `_phases.py:1000-1006` (Academic+Augment) | Sequential | Partial parallel | **~2x** |
| `_phases.py:811-836` (Graph+Markdown) | Sequential | `asyncio.gather` | **~1.5x** |

#### Phase 6 (lines 753-771) — Highest Impact

```python
# CURRENT: Sequential (~T1 + T2 + T3)
generated_report = await _generate_and_store_report(...)
rl_result = await _run_rl_loop(ctx=ctx, all_page_results=all_page_results)
tot_result = await _run_hypothesis_tot(ctx=ctx, all_page_results=all_page_results)

# OPTIMIZED: Parallel (~max(T1, T2, T3))
report_task = _generate_and_store_report(...)
rl_task = _run_rl_loop(ctx=ctx, all_page_results=all_page_results)
tot_task = _run_hypothesis_tot(ctx=ctx, all_page_results=all_page_results)

results = await asyncio.gather(report_task, rl_task, tot_task, return_exceptions=True)
generated_report = results[0] or ""
tot_solution_count = max(
    results[1].get("tot_solution_count", 0),
    results[2].get("tot_solution_count", 0)
)
```

---

### H7: FetchCoordinator God Object

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟠 High — Maintainability |
| **File** | `coordinators/fetch_coordinator.py` |
| **Lines** | 6203 lines |

#### SOLID Violations

| Principle | Violation |
|-----------|-----------|
| **SRP** | 8+ distinct responsibilities |
| **ISP** | 80+ public methods |
| **OCP** | Adding transport requires modifying class |
| **DIP** | Direct imports of 30+ modules |
| **LSP** | `AIMDWindow` vs `PyAIMDController` behavior differs |

#### Extractable Components

| Section | Approx Lines | Candidate Class |
|---------|-------------|----------------|
| Transport Layer | 400 | `TransportCoordinator` |
| Micro-Sprint Engine | 600+ | `MicroSprintEngine` |
| Cover Traffic | 150 | `CoverTrafficManager` |
| Protocol Handlers | 220 | `ProtocolHandlerRegistry` |
| Post-Processing | 150 | `FetchPostProcessor` |

---

### H8: AIMD Semaphore Race Conditions

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟠 High — Data corruption under load |
| **File** | `coordinators/fetch_coordinator.py` |

#### Root Cause

Direct `_value` mutation on `asyncio.Semaphore` bypasses internal synchronization, causing TOCTOU races.

#### All Race Condition Locations

| Line | Issue | Severity |
|------|-------|----------|
| 1706 | `self._aimd_semaphore._value` read — TOCTOU | **High** |
| 2256 | `self._aimd_semaphore._value` read — TOCTOU | **High** |
| 2382 | `self._aimd_semaphore._value` read — TOCTOU | Medium |
| 2294 | Telemetry read not synchronized | Low |

#### Fix Pattern

```python
# Add dedicated lock
self._aimd_state_lock = asyncio.Lock()

async def _sync_aimd_semaphore(self, target_window: float) -> None:
    async with self._aimd_state_lock:
        diff = int(target_window) - self._aimd_semaphore._value
        if diff > 0:
            for _ in range(diff):
                self._aimd_semaphore.release()
```

---

### H9: One-Shot ThreadPool in DeepHermes3

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟠 High — Resource waste |
| **File** | `brain/deephermes3_engine.py:5665-5679` |

#### Root Cause

Creates a throwaway `ThreadPoolExecutor(max_workers=1)` for a single `mx.eval([])` call, then immediately destroys it. On a 60-second sprint with 100 turns, this spawns 100 threads.

#### Correct Pattern

```python
# CURRENT (WRONG):
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    pool.submit(mx.eval, [])

# CORRECT — use asyncio.to_thread:
await asyncio.to_thread(mx.eval, [])
```

---

## P2 — MEDIUM (Performance / Modernization)

### M1: Uncached psutil Calls in Hot Paths (6 Locations)

| Location | Current | Should Use |
|----------|---------|------------|
| `brain/moe_router.py:643` | `psutil.virtual_memory()` | `system_memory_sync()` |
| `layers/ghost.py:192` | `psutil.virtual_memory()` | `system_memory_sync()` |
| `layers/ghost.py:223,250` | `psutil.virtual_memory()` | `_get_cached_psutil()` |
| `layers/ghost.py:285` | `psutil.virtual_memory()` | `system_memory_sync()` |
| `knowledge/lancedb_store.py:873,887` | `psutil.virtual_memory()` | `system_memory_sync()` |
| `pipeline/public/_phases.py:797` | `psutil.Process()` | `process()` singleton |

**Caching infrastructure exists:** `_core/_psutil_cache.py`, `_core/psutil_shim.py`, `utils/sys_metrics.py`

---

### M2: stdlib json Instead of orjson/msgspec

| Location | Current | Should Use |
|----------|---------|------------|
| `multimodal/analyzer.py:223,227` | `json.dumps()` | `orjson.dumps()` |
| `transport/tor_transport.py:158` | `json.loads()` | `orjson.loads()` |
| `federated/bridge.py:397,449` | `json.dumps()` | `orjson.dumps()` |

---

### M3: Duplicate orjson Import

| Location | Pattern |
|----------|---------|
| `rl/sprint_policy_manager.py:17-40` | Multiple import blocks |

---

### M4: Missing `gc=False` on msgspec Struct (M1 Optimization)

| Location | Issue |
|----------|-------|
| `brain/hermes/planner.py:33,44` | Missing `gc=False` on hot-path DTOs |
| `brain/deephermes3_engine.py:5228` | Missing `gc=False` on local class |
| `brain/deephermes3_engine.py:6187` | Missing `gc=False` on probe schema |

#### Root Cause

`msgspec.Struct` with `gc=True` (default) adds GC overhead. On M1 8GB, every hot-path DTO should use `gc=False` via the compat wrapper.

#### Fix

```python
# Current:
class PlannerRuntimeResult(msgspec.Struct, frozen=True, kw_only=True):

# Should use compat wrapper:
from hledac.universal.compat.msgspec_gc_compat import Struct

class PlannerRuntimeResult(Struct, frozen=True, kw_only=True):
    # gc=False inherited from Struct
```

---

### M5: IOC Regex Pattern Mismatches

| IOC Type | Canonical (Rust) | `ner_engine.py` | Status |
|----------|------------------|-----------------|--------|
| CVE | `CVE-\d{4}-\d{4,}` | `\bCVE-\d{4}-\d{4,7}\b` | ❌ Diverges |
| Email | `[A-Za-z]{2,}` | `[A-Z\|a-z]{2,}` | ❌ Pipe in char class |
| URL | `[^\s<>"']+` | `[^\s<>"{}|\\^`\[\]]+` | ❌ Diverges |

#### Root Cause

`brain/ner_engine.py` has **hardcoded patterns** that diverge from the canonical source (`rust_extensions/src/ioc_patterns.rs`).

#### Canonical Path

```
tools/codegen_ioc_patterns.py → forensics/ioc_patterns_generated.py
                                   └── rust_extensions/src/ioc_patterns_generated.rs
```

#### Fix

**Option A:** Regenerate `ner_engine.py` patterns from canonical source.

**Option B:** Import from `forensics.ioc_patterns_generated` instead of hardcoding.

---

### M6: Module-Level Mutable Global State

| Location | Type | Issue |
|----------|------|-------|
| `meta_reasoning_coordinator.py:57-59` | `None→singleton` | **Broken double-checked locking** |
| `fetch_coordinator.py:272` | `TTLCache` | Not async-safe |
| `memory_coordinator.py:90-91` | `itertools.count` | Lock present but not async-safe |

#### Broken Double-Checked Locking

```python
# meta_reasoning_coordinator.py — WRONG
_PRM_SCORER = None
_PRM_SCORER_LOCK = threading.Lock()

def _get_prm_scorer():
    if _PRM_SCORER is not None:  # ❌ Data race — other thread may be writing
        return _PRM_SCORER
    with _PRM_SCORER_LOCK:
        if _PRM_SCORER is not None:  # Double-check after lock
            return _PRM_SCORER
        _PRM_SCORER = PRMScorer()  # ❌ Race between check and write
        return _PRM_SCORER
```

Python's memory model doesn't guarantee visibility across threads without synchronization primitives.

---

### M7: LMDB Spin-Lock Should Use `threading.Event`

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟡 Medium — Correctness |
| **File** | `_core/lmdb_unified.py:250-259` |

#### Current Pattern (Busy-Wait)

```python
spin_count = 0
while self._reopen_in_progress:
    if spin_count >= 10:
        raise RuntimeError("UnifiedLMDB reopen timed out")
    sleep_time = min(0.05 * math.exp(0.5 * spin_count), 1.0)
    time.sleep(sleep_time)  # ❌ Blocking, not Event-based
    spin_count += 1
```

#### Correct Pattern

```python
# Replace _reopen_in_progress (bool) with:
self._reopen_event = threading.Event()  # set() = not in progress, clear() = in progress

# In _emergency_shrink:
self._reopen_event.clear()  # signal reopen starting
# ... do reopen work ...
self._reopen_event.set()    # signal reopen complete

# In _ensure_init:
if not self._reopen_event.wait(timeout=10):  # ✅ Zero-CPU wait
    raise RuntimeError("UnifiedLMDB reopen timed out")
```

---

### M8: O(n) Session Cache Eviction

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟡 Medium — Performance |
| **File** | `brain/deephermes3_engine.py:3085-3095` |

#### Root Cause

```python
# O(n) scan for max — iterates ALL entries
evicted_key = max(self._session_cache_pool, key=lambda k: self._session_cache_pool[k][3])
```

#### Better Pattern: Max-Heap

```python
import heapq

# Track (-size, key) for max-heap
heapq.heappush(self._size_heap, (-cache_size, prompt_hash))

# Evict: O(log n) instead of O(n)
neg_size, evicted_key = heapq.heappop(self._size_heap)
```

---

### M9: GHOST_* Env Vars Not HLEDAC Prefixed

Multiple locations use `GHOST_*` prefix instead of `HLEDAC_*`.

---

### M10: Blocking Spin-Lock in LMDB Reopen

See M7 above.

---

## P3 — LOW (Cleanup / Polish)

### L1: Hardcoded Configuration Values

| Location | Issue |
|----------|-------|
| `discovery/duckdb_fts_store.py:82` | Hardcoded batch size |
| `coordinators/fetch_coordinator.py:192,724,728` | Magic numbers |
| `knowledge/duckdb_store.py:1341,2206` | Hardcoded limits |

---

### L2: Non-Standard API Key Prefixes

`SHODAN_API_KEY`, `CENSYS_API_ID`, `GREYNOISE_API_KEY` — should follow `HLEDAC_*` convention.

---

### L3: Rust Dead Code (ZOMBIE Modules)

| Status | Count |
|--------|-------|
| ZOMBIE modules | 50+ |
| Actually used via wiring | ~15 (compress, circuit_breaker, etc.) |

**Clarification:** Many "ZOMBIE" modules ARE used via the wiring layer (`rust_extensions/wiring/`). The audit found no Python callers directly, but the wiring layer is the integration path.

**Recommendation:** Run `rust_extensions/audit.py` to identify truly unused modules, then remove them to reduce compile time by ~30-40%.

---

### L4: Lazy Init Bug in `_core/__main__.py`

| Attribute | Detail |
|-----------|--------|
| **Severity** | 🟢 Low |
| **File** | `_core/__main__.py:46` |

```python
# CURRENT — defeats lazy pattern:
run_sprint = _get_runtime_run_sprint()  # ❌ Eager call at import

# CORRECT — use __getattr__:
def __getattr__(name: str):
    if name == "run_sprint":
        return _get_runtime_run_sprint()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

---

### L5: DeepHermes3 O(n) KV Cache Pool Eviction

Same as M8 — `max()` over all keys instead of heap.

---

## Parallelization Opportunities Summary

| Location | Phases | Est. Speedup |
|----------|--------|--------------|
| `_phases.py:753-771` | Report + RL + ToT | **~3x** |
| `_phases.py:1000-1006` | Academic + Augment | **~2x** |
| `_phases.py:811-836` | Graph + Markdown | **~1.5x** |

---
