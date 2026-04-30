# Phase 1: Code Quality & Architecture Review

## Code Quality Findings (01A)

### CRITICAL (3) - M1 Crash Vectors

| # | Issue | File | Line |
|---|-------|------|------|
| 1 | `asyncio.run()` in ThreadPoolExecutor context | `utils/execution_optimizer.py` | 406 |
| 2 | `asyncio.run()` submitted to ThreadPoolExecutor | `utils/execution_optimizer.py` | 413 |
| 3 | `asyncio.run()` in thread-safe wrapper | `brain/inference_engine.py` | 442 |

**All three are M1 Apple Silicon crash vectors** - nested event loops inside worker threads crash Metal.

### HIGH (4)

| # | Issue | File |
|---|-------|------|
| 4 | Unbounded `_dedup_hot_cache_order.append()` | `knowledge/duckdb_store.py:6533,6539` |
| 5 | 31,049 line god object | `legacy/autonomous_orchestrator.py` |
| 6 | 113 string concatenations in loop (O(n²)) | `brain/hypothesis_engine.py` |
| 7 | Facade architecture confusion | `autonomous_orchestrator.py` |

### MEDIUM (8)
- ThreadPoolExecutor without cleanup (execution_optimizer.py:412)
- Unbounded `_wal_write_pending_sync_marker` (duckdb_store.py)
- No error handling in `loop.run_until_complete` (global_scheduler.py:155)
- `_check_gathered` return value not inspected (sprint_scheduler.py)
- `deque` without `maxlen` (sprint_scheduler.py)
- Batch chunking without backpressure (duckdb_store.py)
- `run_in_executor` without cancellation handling (fetch_coordinator.py:831)
- Race condition in `_dns_tunnel_executor` init (tool_registry.py:436)

### LOW (5)
- Comment-document code (hypothesis_engine.py)
- Redundant type checking (tool_registry.py:153)
- Missing error context in exceptions (duckdb_store.py)
- Hardcoded magic numbers (sprint_scheduler.py)
- Unused imports (hypothesis_engine.py)

---

## Architecture Findings (01B)

### CRITICAL (4)

| # | Issue | File |
|---|-------|------|
| 1 | Legacy facade re-exports 40+ symbols (false authority) | `autonomous_orchestrator.py` |
| 2 | SprintScheduler has 15+ injected dependencies | `runtime/sprint_scheduler.py:617-691` |
| 3 | DuckDB/LanceDB/Kuzu 4-system storage complexity | `knowledge/` |
| 4 | Async pattern inconsistency potential | `coordinators/fetch_coordinator.py:1014,1342` |

### HIGH (6)

| # | Issue | File |
|---|-------|------|
| 5 | Graph injection lacks runtime type safety | `duckdb_store.py:741-786` |
| 6 | Lightpanda pool resource leak risk (no atexit) | `fetch_coordinator.py:374-413` |
| 7 | Transport authority distributed (DORMANT code) | `transport_resolver.py` |
| 8 | DuckDB/LanceDB boundary confusion | `knowledge/duckdb_store.py:646-650` |
| 9 | DuckDB Store complex TypedDict contracts | `knowledge/duckdb_store.py:99-140` |
| 10 | Fetch coordinator transport selection complexity | `httpx_transport.py:8-42` |

### MEDIUM (5)
- Layer Manager facade confusion (layers/layer_manager.py)
- Graph service module-level singletons (graph_service.py:152-156)
- CoordinatorRegistry singleton pattern (coordinator_registry.py:58-73)
- ResourceGovernor dual authority with M1MemoryOptimizer (core/resource_governor.py, layers/memory_layer.py)
- SprintScheduler Result schema with 50+ fields (sprint_scheduler.py)

### LOW (3 - Positive)
- httpx_client lazy singleton pattern (exemplary)
- DuckDB batch chunking correctly bounded (max_batch_size=500)
- Storage tier documentation clear (TIER 1/2/3)

---

## Critical Issues for Phase 2 Context

1. **asyncio.run() in threads** - 3 CRITICAL M1 crash vectors to watch for in security review
2. **Unbounded collections** - duckdb_store and sprint_scheduler have potential DoS vectors
3. **Legacy facade** - architectural confusion may hide security issues
4. **Lightpanda browser pool** - resource cleanup needs security review
5. **Transport authority** - dormant code paths may have security implications

---

## Phase 1 Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Code Quality | 3 | 4 | 8 | 5 |
| Architecture | 4 | 6 | 5 | 3 |
| **TOTAL** | **7** | **10** | **13** | **8** |

**Top 3 Priority Actions:**
1. Fix 3 asyncio.run() M1 crash vectors (CRITICAL)
2. Remove autonomous_orchestrator facade from exports (CRITICAL)
3. Extract domain managers from SprintScheduler (HIGH)
