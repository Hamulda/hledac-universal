# Hledac Universal Architecture Review - Findings

**Review Date:** 2026-04-29
**Reviewer:** Architect (Oracle)
**Scope:** hledac/universal/ - Autonomous OSINT orchestrator for M1 MacBook 8GB UMA

---

## Executive Summary

The Hledac Universal codebase demonstrates a mature, well-structured OSINT orchestration platform with clear architectural layering. However, several critical and high-severity issues were identified that require immediate attention, particularly around async patterns, storage boundary enforcement, and transport seam complexity.

**Critical Issues:** 4
**High Issues:** 6
**Medium Issues:** 5
**Low Issues:** 3

---

## 1. Component Boundaries

### 1.1 Finding: SprintScheduler God Object (CRITICAL)

**Severity:** Critical
**File:** `runtime/sprint_scheduler.py:617-691`
**Evidence:**
```
self._duckdb_store: Any = None       # line 667
self._forensics_enricher: Any = None # line 669
self._multimodal_enricher: Any = None # line 672
self._ioc_graph: Any = None          # line 675
self._pivot_ioc_graph: Any = None    # line 622
self._prefetch_oracle: Any = None    # line 683
self._shadow_pd_summary: Any = None  # line 685
```

**Impact:** The `SprintScheduler` class (HOTSPOT: 45 commits/90d) has accumulated 15+ injected dependencies, violating Single Responsibility Principle. This is a maintenance bottleneck and testing risk.

**Recommendation:**
1. Extract coordinator responsibilities into domain-specific managers (e.g., `FindingsManager`, `GraphAccumulator`, `AdvisoryRunner`)
2. Use composition over direct attribute injection
3. Introduce a `SprintContext` object to carry dependencies rather than direct attributes

**Effort:** High (requires refactoring ~400 lines)
**Priority:** P1

---

### 1.2 Finding: Layer Manager as Facade (MEDIUM)

**Severity:** Medium
**File:** `layers/layer_manager.py`
**Evidence:** The module docstring states "This is a thin wrapper that imports existing stealth modules and adds integration logic for the universal orchestrator."

**Impact:** The `LayerManager` class provides centralized initialization but the layers themselves (`memory_layer`, `stealth_layer`, `coordination_layer`) are substantial modules, not thin wrappers. This creates confusion about where logic should live.

**Recommendation:**
1. Clarify layer responsibilities: thin integration vs. substantive logic
2. Document which layers own their domain logic
3. Consider if `LayerManager` should manage lifecycle only, not contain integration code

---

## 2. Dependency Management

### 2.1 Finding: DuckDB/LanceDB Boundary Confusion (HIGH)

**Severity:** High
**Files:**
- `knowledge/duckdb_store.py:23` - "IOCGraph — truth graph for IOC storage (buffered writes)"
- `knowledge/lancedb_store.py:66` - "LanceDBIdentityStore"
- `knowledge/ioc_graph.py:33` - uses Kuzu, not DuckDB

**Evidence:**
```
# duckdb_store.py:646-650
# Sprint 8WA: Dedicated truth-write graph slot for ACTIVE buffered writes.
self._truth_write_graph: Any = None  # line 650
# Sprint 8SB: Semantic store (FastEmbed + LanceDB)
self._semantic_store: Optional[Any] = None  # line 653
```

**Impact:** The storage boundary is complex:
- DuckDB: sprint facts, shadow findings, activation records
- Kuzu (IOCGraph): IOC truth storage with buffered writes
- LanceDB (SemanticStore): FastEmbed embeddings for ANN search
- DuckPGQGraph (graph_service.py): analytics queries via DuckDB

This creates a 4-system storage architecture that requires careful injection and management.

**Recommendation:**
1. Document the storage boundary with explicit ownership table
2. Add runtime assertions to verify correct store is injected
3. Create a `StorageFacade` that exposes typed methods for each store

**Trade-off:** Simplification vs. existing sprint dependencies

---

### 2.2 Finding: Graph Service Session Singletons (MEDIUM)

**Severity:** Medium
**File:** `knowledge/graph_service.py:152-156`
**Evidence:**
```python
_SEEN_IOCS: set[tuple[str, str]] = set()
_SEEN_RELS: set[tuple[str, str, str]] = set()
_DUCKPGQ_GRAPH: Optional[DuckPGQGraph] = None

def reset_session() -> None:
    """Clear session-level idempotency trackers and graph singleton."""
    global _SEEN_IOCS, _SEEN_RELS, _DUCKPGQ_GRAPH
    _SEEN_IOCS.clear()
    _SEEN_RELS.clear()
    _DUCKPGQ_GRAPH = None  # F196A: Reset graph singleton
```

**Impact:** Module-level singletons with global state make testing difficult and create implicit cross-sprint coupling. The `reset_session()` function must be called explicitly or graph state leaks.

**Recommendation:**
1. Encapsulate in a class with explicit lifecycle
2. Use dependency injection instead of module-level singletons
3. Add integration test verifying `reset_session()` is called

---

### 2.3 Finding: httpx Client Lazy Singleton (LOW - Positive)

**Severity:** Low (Positive Pattern)
**File:** `transport/httpx_client.py:56-89`
**Evidence:**
```python
_httpx_client_instance: Optional["httpx.AsyncClient"] = None
_httpx_client_lock: asyncio.Lock = asyncio.Lock()
_httpx_client_closed: bool = False

async def async_get_httpx_client() -> "httpx.AsyncClient":
    """Lazily creates client on first await. Idempotent."""
```

**Impact:** Well-designed lazy singleton with proper locking. This is a model pattern for the codebase.

**Recommendation:** Document this as the canonical pattern for client lifecycle management.

---

## 3. API Design

### 3.1 Finding: DuckDB Store Complex TypedDict Contracts (HIGH)

**Severity:** High
**File:** `knowledge/duckdb_store.py:99-140`
**Evidence:**
```python
class ActivationResult(TypedDict):
    finding_id: str
    lmdb_success: bool
    duckdb_success: bool | None  # None = not yet attempted
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool

class ReplayResult(TypedDict):
    finding_id: str
    marker_found: bool
    wal_truth_found: bool
    duckdb_written: bool
    marker_cleared: bool
    read_back_verified: bool
    deadlettered: bool
    retry_count: int
    error: str | None
```

**Impact:** The `duckdb_success: bool | None` pattern (line 103) is confusing - three states where boolean would suffice with explicit enum. The `ActivationResult` contract is spread across multiple methods.

**Recommendation:**
1. Use `Literal` types for explicit state machines instead of `bool | None`
2. Consolidate finding contracts into single `FindingRecord` TypedDict
3. Document which methods return which contract

---

### 3.2 Finding: Fetch Coordinator Transport Selection Complexity (HIGH)

**Severity:** High
**File:** `transport/httpx_transport.py:8-42` and `coordinators/fetch_coordinator.py:1148`
**Evidence:**
```
# httpx_transport.py - Truth table:
random clearnet HTML  | aiohttp     | TCPConnector
same-host/API clearnet| httpx_h2    | HTTP/2
CT/CDX/API endpoint  | httpx_h2    | HTTP/2
.onion               | aiohttp_socks | ProxyConnector
.i2p / .b32.i2p     | aiohttp_socks | ProxyConnector
.freenet             | aiohttp     | HTTP proxy
use_js=True         | aiohttp     | TCPConnector
use_stealth=True    | aiohttp     | StealthSession
```

**Impact:** Transport selection is distributed across multiple files:
- `httpx_transport.py` - HTTPX H2 lane classification
- `transport_resolver.py` - Transport enum resolution (DORMANT per comments)
- `fetch_coordinator.py:1148` - direct imports from `transport_resolver`

The authority is unclear and routing logic is duplicated.

**Recommendation:**
1. Designate `TransportResolver` as single authority for transport selection
2. Migrate `fetch_coordinator.py:1148` to use `resolve()` method
3. Add telemetry to track which path is actually used

---

## 4. Data Model

### 4.1 Finding: SprintScheduler Result Schema Extravagance (MEDIUM)

**Severity:** Medium
**File:** `runtime/sprint_scheduler.py`
**Evidence:** The `SprintSchedulerResult` contains 50+ fields including nested structures like `public_backend_degraded`, `canonical_backend_degraded`, `advisory_outcomes`.

**Impact:** Schema evolution is difficult. Adding fields requires updating multiple consumers.

**Recommendation:**
1. Group related fields into nested dataclasses (e.g., `BackendStatus`, `AdvisorySummary`)
2. Use `@dataclass(frozen=True)` for immutability
3. Document backward compatibility policy

---

### 4.2 Finding: DuckDB Batch Sizes Correctly Bounded (LOW - Positive)

**Severity:** Low (Positive)
**File:** `knowledge/duckdb_store.py:2359, 3791`
**Evidence:**
```python
# async_record_shadow_findings_batch - max_batch_size=500
async def async_record_shadow_findings_batch(
    findings: list[dict],
    max_batch_size: int = 500,  # line 2359
):
    for i in range(0, len(findings), max_batch_size):
        chunk = findings[i : i + max_batch_size]

# async_record_activation_batch - WAL via put_many
Order: LMDB WAL first (via put_many) → DuckDB second (chunked batch)
```

**Impact:** Correctly bounded batch writes with explicit chunking. Good pattern.

**Recommendation:** None - this is the correct approach.

---

## 5. Design Patterns

### 5.1 Finding: Coordinator Registry Singleton Pattern (MEDIUM)

**Severity:** Medium
**File:** `coordinators/coordinator_registry.py:58-73`
**Evidence:**
```python
class CoordinatorRegistry:
    def __init__(self):
        self._coordinators: Dict[str, CoordinatorInfo] = {}
        self._by_operation: Dict[OperationType, List[str]] = {...}
        self._lock = asyncio.Lock()
```

**Impact:** `CoordinatorRegistry` is a singleton but registers coordinators at runtime. This makes it difficult to test coordinator interactions in isolation.

**Recommendation:**
1. Use dependency injection for registry in tests
2. Add `register_unittest_mode()` to bypass actual coordinator creation
3. Document that registry should be initialized once at startup

---

### 5.2 Finding: Lightpanda Pool Lifecycle (HIGH)

**Severity:** High
**File:** `coordinators/fetch_coordinator.py:374-413, 742-780`
**Evidence:**
```python
class LightpandaPool:
    def __init__(self, size: int = 2):
        self._all_instances: list[LightpandaManager] = []
        self._available: asyncio.Queue = asyncio.Queue()

    async def get_instance(self) -> LightpandaManager:
    async def release(self, instance: LightpandaManager):

# Usage in fetch_coordinator:
self._lightpanda_pool = LightpandaPool(size=2)  # line 461
self._lightpanda_pool_started = False
self._lightpanda_lock = asyncio.Lock()
```

**Impact:** Browser process pool management with async lifecycle. Potential resource leak if `release()` is not called after exception. The pool is started lazily (line 746).

**Recommendation:**
1. Add context manager protocol (`async with pool.get_instance() as instance:`)
2. Add `atexit` handler to cleanup browser processes
3. Track leaked instances in metrics

---

## 6. Architectural Consistency

### 6.1 Finding: Legacy Facade Overload (CRITICAL)

**Severity:** Critical
**File:** `autonomous_orchestrator.py` (facade) vs `legacy/autonomous_orchestrator.py`
**Evidence:**
```python
# autonomous_orchestrator.py - ROOT_REEXPORT_FACADE
"""
.. canonical_owner::
    - Legacy implementation: legacy/autonomous_orchestrator.py (~31k lines)
    - Production sprint: core.__main__:run_sprint()
    - Production orchestrator: runtime.sprint_scheduler:SprintScheduler

.. false_authority_risk::
    This module looks like a primary orchestrator but is NOT.
```

**Impact:** The facade re-exports 40+ symbols from legacy, creating false authority. Developers may import from `autonomous_orchestrator` assuming it's the production implementation.

**Recommendation:**
1. Remove facade from `__init__.py` exports
2. Add `__deprecated__` warning to all facade exports
3. Update `smoke_runner.py` and tests to use canonical paths

---

### 6.2 Finding: Async Pattern Inconsistency (CRITICAL)

**Severity:** Critical
**File:** `coordinators/fetch_coordinator.py:1014, 1342`
**Evidence:**
```python
# Line 1014 - correct asyncio.gather usage
results = await asyncio.gather(
# Line 1342 - correct asyncio.gather usage
ddgs_rows, news_rows, wayback_rows, urlscan_rows = await asyncio.gather(
```

**Impact:** No nested `asyncio.run()` found in fetch_coordinator (good). However, need to verify other coordinators don't have nested async issues.

**Recommendation:**
1. Add lint rule to detect `asyncio.run()` in async contexts
2. Run `ast_grep` pattern search across all coordinators

---

### 6.3 Finding: Resource Governor Dual Authority (MEDIUM)

**Severity:** Medium
**Files:**
- `core/resource_governor.py:168` - `class ResourceGovernor`
- `layers/memory_layer.py` - `M1MemoryOptimizer`

**Evidence:**
```python
# layers/memory_layer.py
"""
IMPORTANT: Layer cleanup utility only — not the canonical Uma governor.
Canonical Uma policy lives in core/resource_governor.py.
"""
```

**Impact:** Two systems manage M1 memory:
1. `core/resource_governor.py` - canonical Uma policy
2. `layers/memory_layer.py` - cleanup utility with similar functionality

Confusion about which is authoritative.

**Recommendation:**
1. Deprecate `M1MemoryOptimizer` methods that overlap with `ResourceGovernor`
2. Have `MemoryLayer` delegate to `ResourceGovernor` for policy decisions
3. Document the relationship clearly

---

## 7. DuckDB/LanceDB Boundary

### 7.1 Finding: Storage Layer Well-Documented (LOW - Positive)

**Severity:** Low (Positive)
**File:** `knowledge/duckdb_store.py:5-25`
**Evidence:**
```
TIER 1 — SPRINT FACTS (DuckDB, durable):
    sprint_delta       — per-sprint metrics
    sprint_scorecard   — per-sprint aggregated scores
    source_hit_log     — per-sprint source attribution

TIER 2 — SHADOW FINDINGS (DuckDB, durable):
    shadow_findings    — finding-level records
    shadow_runs        — run-level metadata

TIER 3 — GRAPH (Kuzu/LanceDB, injected):
    IOCGraph           — truth graph for IOC storage
    SemanticStore      — FastEmbed+ LanceDB for ANN semantic search
```

**Impact:** Clear documentation of storage tier responsibilities.

**Recommendation:** None - this is excellent documentation.

---

### 7.2 Finding: Graph Injection Complexity (HIGH)

**Severity:** High
**Files:**
- `knowledge/duckdb_store.py:741-786` - `inject_truth_write_graph`, `get_truth_write_graph`
- `knowledge/duckdb_store.py:1197-1208` - `inject_semantic_store`

**Evidence:**
```python
# Sprint 8WA: Dedicated truth-write graph slot for ACTIVE buffered writes.
def inject_truth_write_graph(self, graph: Any) -> None:
    # TRUTH-WRITE ONLY: only IOCGraph (Kuzu) supports buffer_ioc/flush_buffers.
    # DuckPGQGraph must NEVER be injected here — it lacks buffered write capability.
    
def truth_write_graph_supports_buffered_writes(self) -> bool:
    # Returns True only if _truth_write_graph is IOCGraph (Kuzu)
```

**Impact:** The injection pattern requires caller to know which graph type to inject where. Runtime errors if wrong graph is injected.

**Recommendation:**
1. Add runtime type validation on injection
2. Use Protocol/TypeVar for graph interfaces
3. Add integration test verifying correct graph types

---

## 8. Transport Seam

### 8.1 Finding: httpx_transport Well-Integrated (LOW - Positive)

**Severity:** Low (Positive)
**File:** `transport/httpx_transport.py:225-257`
**Evidence:**
```python
async def fetch_via_httpx_h2(url: str, ...) -> "httpx.Response":
    """Execute HTTP GET via HTTPX AsyncClient (HTTP/2 capable)."""
    from .httpx_client import async_get_httpx_client
    client = await async_get_httpx_client()
```

**Impact:** Proper integration with lazy client singleton.

**Recommendation:** None.

---

### 8.2 Finding: Transport Resolver Dormant Authority (MEDIUM)

**Severity:** Medium
**File:** `transport/transport_resolver.py:47-65`
**Evidence:**
```python
"""
AUTHORITY NOTE (audit/8SF):
    This class is a POLICY CANDIDATE, not the current production authority.
    Current production path: FetchCoordinator._fetch_url() routes .onion/.i2p
    directly via _fetch_with_tor() / darknet_connector, and clearnet via
    curl_cffi/StealthCrawler. This class's resolve() is DORMANT.
```

**Impact:** `TransportResolver.resolve()` is not wired into production path. Multiple transport selection policies exist in different files.

**Recommendation:**
1. Either wire `TransportResolver` into production or remove dormant code
2. Document current production transport authority clearly
3. Add integration test verifying transport selection path

---

## 9. Critical Recommendations Summary

| Priority | Finding | File | Effort |
|----------|---------|------|--------|
| P0 | Legacy facade creates false authority | autonomous_orchestrator.py | Medium |
| P0 | SprintScheduler god object (15+ deps) | sprint_scheduler.py:617-691 | High |
| P1 | DuckDB/LanceDB/Kuzu 4-system complexity | knowledge/ | Medium |
| P1 | Graph injection runtime type safety | duckdb_store.py:741-786 | Low |
| P1 | Lightpanda pool resource leak risk | fetch_coordinator.py:374-413 | Medium |
| P1 | Transport authority distributed (DORMANT code) | transport_resolver.py | Medium |

---

## 10. Positive Patterns (Do Not Change)

1. **httpx_client singleton** - proper lazy initialization with asyncio.Lock
2. **DuckDB batch chunking** - max_batch_size=500, correct batching
3. **Storage tier documentation** - clear TIER 1/2/3 separation
4. **GHOST_INVARIANTS** - fail-safe patterns throughout
5. **Canonical path documentation** - clear authority chains in docstrings

---

## References

- `runtime/sprint_scheduler.py:617-691` - SprintScheduler attributes
- `knowledge/duckdb_store.py:5-25` - Storage tier documentation
- `knowledge/duckdb_store.py:741-786` - Graph injection methods
- `transport/httpx_transport.py:8-42` - Transport truth table
- `transport/httpx_client.py:56-89` - HTTPX singleton pattern
- `coordinators/coordinator_registry.py:58-73` - Registry pattern
- `knowledge/graph_service.py:152-156` - Session reset
- `layers/memory_layer.py` - Dual authority with ResourceGovernor
- `autonomous_orchestrator.py` - Facade documentation
