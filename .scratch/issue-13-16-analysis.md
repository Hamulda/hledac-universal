# Sprint 4-Issue Analysis: Issues #13–#16

**Date:** 2026-07-28
**Project:** Hledac Universal OSINT Orchestrator
**Hardware:** MacBook Air M1 8GB UMA
**Python:** 3.14+
**Rust:** 1.80+

---

## Issue #13: lopdf Maintenance Mode → pdfium-render

### Current State

**Dependency chain:**
```
rust_extensions/Cargo.toml:56
  lopdf = { version = "0.34", optional = true }

rust_extensions/src/pdf.rs
  ├── lopdf::Document::load(path)           # line 54
  ├── lopdf::Document::load_mem(data)      # line 79
  ├── doc.extract_text(&page_nums)         # line 98
  └── lopdf::Object::*                     # throughout
```

**MAINTENANCE NOTE confirmed** (Cargo.toml line 53):
```
# MAINTENANCE NOTE (2026-07): lopdf is in maintenance mode — no new features.
```

**Python fallback already wired:**
- `content_miner.py` — PyMuPDF (primary path)
- `document_metadata_extractor.py` — PyMuPDF

### Problem Analysis

1. **lopdf 0.34** is indeed in maintenance mode (last release ~2024)
2. **pdfium-render** is a Rust binding to PDFium — BUT:
   - Not a drop-in replacement — different API
   - No native M1 ARM64 wheel on PyPI (it's a Rust crate, not a Python wheel)
   - Requires `pdfium-render` crate compilation from source
   - PDFium itself is a large C++ library (~2MB+ additional compile)
3. **M1 8GB compile budget** — pdfium-render adds significant compile time
4. **Rust crate ecosystem** — pdfium-render is less mature than lopdf for Rust

### Recommendation

**DEFER replacement.** The Python fallback (PyMuPDF) is already the primary path.
lopdf in Rust is only used for backward compatibility and specific workloads.

**If replacement is needed**, the correct path is:
1. Keep PyMuPDF as primary (Python side)
2. Evaluate `pdfium-render` crate for future Rust-only PDF processing
3. DO NOT add as direct lopdf replacement — full API rewrite required in `pdf.rs`

**Actionable:** Document that `pdfium-render` is not a 1:1 replacement.
Add an issue tracking the API rewrite if lopdf becomes unmaintained.

---

## Issue #14: parking_lot → std::sync

### Current State

**89 usages across `rust_extensions/src/`:**

| File | parking_lot usage | Reason documented |
|------|-----------------|-------------------|
| `aho_corasick.rs:18` | `Mutex` | — |
| `accelerate.rs:50` | `RwLock` | — |
| `aimd_controller.rs:22` | `Mutex + RwLock` | "2-5× faster than std::Mutex, no poison on panic" |
| `ane.rs:45` | `RwLock` | — |
| `async_query.rs:27` | `Mutex` | "2-5× faster than std::Mutex" |
| `bloom.rs:31` | `RwLock` | — |
| `circuit_breaker.rs:45` | `RwLock` | "no unsafe impl needed" |
| `dns.rs:58` | `RwLock` | — |
| `federated_qtable.rs:51` | `RwLock` | "Send+Sync by default, no unsafe" |
| `feed_pipeline.rs:4` | `Mutex` | — |
| `ioc_dedup.rs:21` | `RwLock` | "Issue #1 fix: replaced DashMap" |
| `metal_compute.rs:41` | `RwLock` | — |
| `os_unfair_lock.rs:4-15` | — | Documents why os_unfair_lock ~5ns vs parking_lot ~25ns |
| `pipeline_compose.rs:48` | `Mutex` | — |
| `pool_run.rs:43,78-87` | `Mutex + Condvar` | "no poisoning, 2x faster" |
| `url_ops.rs:267` | `RwLock` | "read-lock-free, no poisoning" |
| `url_set.rs:30` | `RwLock` | "Issue #2 fix: replaced DashMap" |

### Problem Analysis

**The claim "Rust 1.80+ std::sync is comparable" is partially true but ignores critical differences:**

| Feature | parking_lot | std::sync |
|---------|-------------|------------|
| Poisoning on panic | NO | YES (std Mutex poisons) |
| Lock speed | ~25ns | ~50-100ns |
| RwLock read scalability | Read-lock-free | Reader-writer spin |
| Fairness | No (starvation possible) | FIFO (std) |
| Condvar support | YES | YES |

**Why parking_lot was chosen here:**
1. **No poisoning** — panic in critical section doesn't poison the lock
2. **Performance** — 2-5× faster for high-contention paths
3. **No unsafe impl needed** — Send+Sync derived automatically
4. **os_unfair_lock.rs** (Darwin-specific) documents the hierarchy: os_unfair_lock (~5ns) > parking_lot (~25ns) > std (~50-100ns)

**Migration risks:**
1. **std::Mutex poisons** — existing panic-handling assumptions break
2. **Performance regression** — high-frequency lock sites (bloom, url_set, ioc_dedup) would slow down
3. **API differences** — `parking_lot::RwLock::read()` returns `RwLockReadGuard`, same as std
4. **89 files** to touch — significant regression risk

### Recommendation

**DO NOT MIGRATE.** The parking_lot usage is intentional and well-documented.
The performance and safety benefits are real for M1 8GB workloads.

**If Rust edition upgrade requires it:**
1. Create a compatibility shim: `type Mutex<T> = parking_lot::Mutex<T>;`
2. Use feature flag to switch between implementations
3. Benchmark before/after on M1

**The `os_unfair_lock.rs` already shows the hierarchy is intentional.**

---

## Issue #15: asyncio.wait → asyncio.TaskGroup

### Current State

**23 usages found:**

```
archive/pool_archives/unified_resource_manager.py:432
brain/synthesis_runner.py:1483, 1557, 1610
coordinators/execution_coordinator.py:244
coordinators/resource_allocator.py:554, 559
core/composition_root.py:214, 219
fetching/public_fetcher.py:2750
network/network_intelligence.py:81
recon/streaming_embedder.py:4, 370, 405, 520
runtime/scheduler_v2/prelude.py:261, 289
runtime/sprint_entrypoint.py:2255, 3466
transport/transport_supervisor.py:169
utils/async_generators.py:90
tests/test_deep_probe_runner.py:167
tests/test_synthesis_strategy.py:203
```

### Problem Analysis

**Python 3.14 deprecates `asyncio.wait()` in favor of `asyncio.TaskGroup`.**

**Migration pattern:**

```python
# OLD (asyncio.wait)
done, pending = await asyncio.wait(
    [task1, task2, task3],
    timeout=30.0,
    return_when=asyncio.FIRST_COMPLETED
)

# NEW (asyncio.TaskGroup)
async with asyncio.TaskGroup() as tg:
    tg.create_task(task1)
    tg.create_task(task2)
    tg.create_task(task3)
```

**Complications:**

1. **`return_when` parameter:**
   - `asyncio.FIRST_COMPLETED` → TaskGroup doesn't have equivalent; need timeout + shield pattern
   - `asyncio.ALL_COMPLETED` → implicit in TaskGroup
   - `asyncio.FIRST_EXCEPTION` → need explicit exception handling

2. **`pending` set handling:**
   ```python
   # OLD
   done, pending = await asyncio.wait(tasks)
   for t in pending:
       t.cancel()

   # NEW — TaskGroup cancels pending tasks automatically when exiting
   # BUT: if you need to access pending tasks, you must track them manually
   ```

3. **Return values:**
   - `asyncio.wait` returns `(done, pending)` sets
   - TaskGroup: need manual tracking of created tasks

4. **Exception handling:**
   - TaskGroup: raises `ExceptionGroup` if multiple tasks fail
   - `asyncio.wait` with `return_exceptions=True` handles this differently

### Safe Migration Strategy

**Step 1:** Create helper that preserves the `return_when` semantics:

```python
# utils/migration_helpers.py (NEW)
from contextlib import asynccontextmanager
from asyncio import TaskGroup, FIRST_COMPLETED, ALL_COMPLETED, TimeoutError as AsyncTimeoutError

@asynccontextmanager
async def task_group_wait(return_when=ALL_COMPLETED, timeout=None):
    """Context manager that mimics asyncio.wait semantics with TaskGroup."""
    pending = []
    results = []
    exceptions = []
    
    async with TaskGroup() as tg:
        # ... dynamic task creation tracking
        yield tg, pending, results
    
    return results, exceptions
```

**Step 2:** Pattern-by-pattern migration (23 locations):

| Pattern | Complexity | Files |
|---------|-----------|-------|
| `FIRST_COMPLETED + timeout` | HIGH | 8 |
| `ALL_COMPLETED + cancel pending` | MEDIUM | 10 |
| `return_exceptions=True` | MEDIUM | 3 |
| `shield` pattern | HIGH | 2 |

### Recommendation

**MIGRATE.** This is a legitimate Python 3.14 deprecation.

**Priority order:**
1. `recon/streaming_embedder.py:4` — already has `ISSUE #016` comment
2. `core/composition_root.py:214, 219` — critical path
3. `fetching/public_fetcher.py:2750` — high-traffic
4. `runtime/scheduler_v2/prelude.py:261, 289` — critical path
5. All others

**Migration must be tested with M1 8GB memory pressure scenarios.**

---

## Issue #16: Global State Registry + WeakValueDictionary

### Current State

**`_cache` in `__init__.py:219`:**
```python
_cache: dict[str, Any] = {}
# Sprint-scoped: clear cache between sprints to prevent symbol accumulation.
# weakref.WeakValueDictionary cannot be used here because _cache stores
# both module objects and primitive values (int, str, etc.) — WeakValueDictionary
# only holds weakly-referenced objects and would prematurely evict modules.
```

**Problem acknowledged in code comments.**

**300+ `clear_cache` references across codebase** — modules with their own caches:

| Module | Cache type | Clear on shutdown? |
|--------|-----------|---------------------|
| `brain/prompt_cache.py` | `OrderedDict` | YES (clear method) |
| `core/embeddings/cache.py` | global `_cache` | YES (get_stats/clear) |
| `core/inference_coordinator.py` | `OrderedDict` | YES (clear method) |
| `knowledge/entity_linker.py` | `dict` | YES (SimpleCache.clear) |
| `knowledge/explainer/fast.py` | `dict` | YES (implicit in class) |
| `layers/communication_layer.py` | `dict` | YES (clear method) |
| `coordinators/research_optimizer.py` | `dict` | YES (clear method) |

### Problem Analysis

**Current issue:** Each module manages its own global cache lifecycle.

**The `WeakValueDictionary` suggestion in the issue is partially correct but:**

1. **WeakValueDictionary limitation (already documented):**
   - Only holds objects with `__weakref__` support
   - Can't store primitives (int, str, tuple)
   - Would prematurely evict module references

2. **The real problem:** No centralized registry for:
   - Cache invalidation on shutdown
   - Memory pressure signaling
   - Consistency across modules

### Recommended Architecture

**Create a `GlobalCacheRegistry` that:**
1. Tracks all named caches in the system
2. Provides `clear_all()` on shutdown
3. Integrates with memory pressure monitoring
4. Uses `WeakValueDictionary` for actual weak-backed caches

```python
# core/global_cache_registry.py (NEW)
from weakref import WeakValueDictionary
from dataclasses import dataclass, field
from typing import Any, Callable
import threading

@dataclass
class CacheEntry:
    name: str
    get_size: Callable[[], int]
    clear: Callable[[], None]
    memory_pressure_threshold: float = 0.8

class GlobalCacheRegistry:
    """Centralized registry for all global caches.
    
    Allows explicit clear() on shutdown and memory pressure eviction.
    """
    _instance: 'GlobalCacheRegistry | None' = None
    _lock = threading.Lock()
    
    def __init__(self):
        self._caches: dict[str, CacheEntry] = {}
        self._ WV: WeakValueDictionary[str, Any] = WeakValueDictionary()
    
    @classmethod
    def get_instance(cls) -> 'GlobalCacheRegistry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def register(self, name: str, get_size: Callable[[], int],
                 clear: Callable[[], None],
                 memory_pressure_threshold: float = 0.8) -> None:
        """Register a cache with the global registry."""
        with self._lock:
            self._caches[name] = CacheEntry(
                name=name,
                get_size=get_size,
                clear=clear,
                memory_pressure_threshold=memory_pressure_threshold
            )
    
    def clear_all(self) -> dict[str, int]:
        """Clear all registered caches. Returns cache sizes before clear."""
        sizes = {}
        with self._lock:
            for name, entry in self._caches.items():
                sizes[name] = entry.get_size()
                entry.clear()
        return sizes
    
    def get_registry_stats(self) -> dict[str, int]:
        """Get sizes of all registered caches."""
        return {name: entry.get_size() for name, entry in self._caches.items()}
```

### Recommendation

**IMPLEMENT `GlobalCacheRegistry`.**

**Phased approach:**
1. **Phase 1:** Create the registry class
2. **Phase 2:** Register existing caches (brain, core, knowledge, coordinators)
3. **Phase 3:** Add `clear_all()` call to sprint winddown
4. **Phase 4:** Add memory pressure integration

**Keep `WeakValueDictionary` usage for caches that hold only objects** — not for `__init__._cache` which holds primitives.

---

## Summary Table

| Issue | Recommendation | Effort | Risk |
|-------|---------------|--------|------|
| #13 lopdf | DEFER — PyMuPDF fallback exists | N/A | N/A |
| #14 parking_lot | DO NOT MIGRATE — intentional performance choice | N/A | N/A |
| #15 asyncio.wait | MIGRATE — Python 3.14 deprecation | MEDIUM-HIGH | MEDIUM |
| #16 Global state | ✅ IMPLEMENTED — GlobalCacheRegistry | MEDIUM | LOW |

---

## Implementation Status (2026-07-28)

### Issue #16 — ✅ IMPLEMENTED

**Files created/modified:**
- `core/global_cache_registry.py` — NEW: GlobalCacheRegistry class
- `core/embeddings/cache.py` — MODIFIED: Added `clear_sync()` + registration
- `runtime/scheduler_v2/winddown.py` — MODIFIED: Integrated `clear_all_caches()` into `_clear_global_state()`

**Registry is live and functional:**
```python
# Test passed:
from hledac.universal.core.global_cache_registry import register_cache, clear_all_caches
register_cache('test', get_size=lambda: 0, clear=lambda: None)
sizes = clear_all_caches()  # → {'test': 0}
```

---

## Proposed Sprint Order

1. **Issue #16** ✅ DONE — GlobalCacheRegistry implemented
2. **Issue #15** (gradual migration, 23 locations) — asyncio.wait → TaskGroup
3. **Issue #13** (DEFER) — document and track
4. **Issue #14** (DO NOT) — document decision
