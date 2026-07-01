# Architektura Analysis — 2026-07-01

## Executive Summary

5 prioritetých refaktorů pro M1 8GB + Python 3.14+ kompatibilitu.

---

## P0: contextvars.ContextVar Migration

### Current State
- `sprint_scheduler.py`: **32,856 lines** — 7 module-level globals + 50+ instance dicts
- ContextVars already partially used:
  - `_advisory_log_lru_var` (L231)
  - `_advisory_log_lru_list_var` (L234)
  - `_advisory_log_suppressed_total_var` (L237)
- 39× `asyncio.create_task()` calls throughout
- Most state in `SprintScheduler` instance dicts: `_seen_hashes`, `_source_weights`, `_novelty_bonuses`, `_feed_accepted_per_source`, `_source_economics`, `_speculative_dns_cache`, etc.

### Problem
- Globals make concurrent sprint execution impossible
- Hard to test isolated components
- State bleeds between async tasks

### Solution (Python 3.14+ Compatible)
```python
# 1. Extract MutableState into contextvars
from contextvars import ContextVar

_sprint_state: ContextVar[dict] = ContextVar('sprint_state', default={})

# 2. Replace all _dict-style state with structured context
@dataclass
class SprintContext:
    seen_hashes: dict[str, bool] = field(default_factory=dict)
    source_weights: dict[str, float] = field(default_factory=dict)
    novelty_bonuses: dict[str, float] = field(default_factory=dict)
    feed_accepted: dict[str, int] = field(default_factory=dict)
    source_economics: dict[str, 'SourceEconomics'] = field(default_factory=dict)
    speculative_dns: dict[str, list[str]] = field(default_factory=dict)
    fetch_latency_ema: dict[str, float] = field(default_factory=dict)
    arrow_batch: list[dict] = field(default_factory=list)

# 3. Access via context
def get_sprint_context() -> SprintContext:
    ctx = _sprint_state.get()
    if 'sprint' not in ctx:
        ctx['sprint'] = SprintContext()
    return ctx['sprint']

# 4. Wrap run() to establish fresh context per sprint
async def run(self, ...):
    token = _sprint_state.set({})  # Fresh context per sprint
    try:
        # ... existing logic unchanged, just access via get_sprint_context()
        pass
    finally:
        _sprint_state.reset(token)
```

### Why This Works for Python 3.14+
- `contextvars` is stdlib since 3.7
- Copy-on-write semantics = no shared mutable state bugs
- `ContextVar` properly integrated with `asyncio.Task` in 3.11+
- No third-party dependencies

### Effort: **3 days**

---

## P1: DuckDB Dual-Write Redundancy

### Current State
`async_ingest_findings_batch` (L7401) je **jediná canonical write path**:
- Quality gate → WAL (`_wal_put_many_sync`) → DuckDB Arrow (`_duckdb_arrow_sync`)
- WAL-first invariant: DuckDB awaits WAL per chunk
- No redundancy — sequential pipeline, not parallel writes

### Evidence
```
L7560: # Pipeline WAL + DuckDB for this chunk CONCURRENTLY
L7659: # P1-2: Operator-level confirmation - DuckDB canonical write confirmed
L7662: "[DuckDB] written %d records (sprint F265-P1-2 canonical write verification)"
```

### Verdict: **Žádná akce nutná** — dual-write již odstraněno v Sprint F265-P1-2.

---

## P1: Split God Objects

### Current State
| File | Lines | Problem |
|------|-------|---------|
| `sprint_scheduler.py` | 32,856 | 50+ instance dicts, module-level globals |
| `duckdb_store.py` | 9,759 | WAL manager, Arrow executor, checkpoint loop, graph updates |

### DuckDBShadowStore Decomposition (9,759L → target ~3,000L)

Extrahovat jako composition:

```python
# 1. WALManager — vlastní třída
class WALManager:
    """F285: Manages shadow_wal.lmdb lifecycle."""
    def __init__(self, wal_path: Path): ...
    def wal_put_many(self, items: list[tuple[str, dict]]) -> bool: ...
    def compact(self) -> dict: ...
    def flush(self) -> None: ...

# 2. DuckDBArrowExecutor — vlastní třída
class DuckDBArrowExecutor:
    """Arrow zero-copy INSERT executor."""
    def __init__(self, db_path: Path): ...
    def execute(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]: ...

# 3. QualityGate — vlastní třída
class QualityGate:
    """CPU-only, deterministic quality assessment."""
    def assess(self, finding: CanonicalFinding) -> FindingQualityDecision: ...
    def assess_batch(self, findings: list[CanonicalFinding]) -> list[FindingQualityDecision]: ...

# 4. DuckDBShadowStore = composition
class DuckDBShadowStore:
    def __init__(self, ...):
        self._wal = WALManager(...)
        self._arrow = DuckDBArrowExecutor(...)
        self._quality = QualityGate(...)
        self._graph: DuckPGQGraph | None = None  # F300-GRAPH canonical
```

### SprintScheduler Decomposition (32,856L → target ~10,000L)

Extrahovat sidecar komponenty:

```python
# 1. AcquisitionPlanner — lane orchestration
class AcquisitionPlanner:
    """Manages _acquisition_plan, lane budgets, source economics."""
    def plan(self, query: str, sources: Sequence[str]) -> AcquisitionPlan: ...
    def update_economics(self, feed_url: str, result: Any, cycle: int) -> None: ...

# 2. FindingProcessor — dedup, quality, storage
class FindingProcessor:
    """Manages _seen_hashes, _is_duplicate, quality assessments."""
    async def process(self, findings: list) -> list: ...

# 3. PivotEngine — hypothesis, dark pivots, BGP
class PivotEngine:
    """Manages _pivot_queue, hypothesis generation."""
    async def execute_pivots(self, max_tasks: int) -> int: ...

# 4. SprintScheduler = composition
class SprintScheduler:
    def __init__(self, ...):
        self._planner = AcquisitionPlanner(...)
        self._processor = FindingProcessor(...)
        self._pivot = PivotEngine(...)
```

### Effort: **2 days**

---

## P2: Consolidate Duplicate Graph Systems

### Current State
| Graph | File | Role | Status |
|-------|------|------|--------|
| `DuckPGQGraph` | `graph/quantum_pathfinder.py` | Canonical IOC store | **WIRED** (F272) |
| `IOCGraph` | `knowledge/ioc_graph.py` | Legacy graph | **Deprecated** |

### Evidence
```python
# duckdb_store.py L19: F272: DuckDB ioc_graph table removed; IOC storage via DuckPGQGraph
# duckdb_store.py L7618: duckdb_store init (graph is None if DuckPGQGraph fails to init)
# sprint_scheduler.py L28271: F300-GRAPH: DuckPGQGraph is the sole canonical graph backend
# sprint_scheduler.py L20283: DuckPGQGraph.find_connected()
```

### IOCGraph Usage Sites
```python
# intelligence/entity_signal_extractor.py
# intelligence/bgp_advisor_adapter.py
# intelligence/stealth_crawler.py
```

### Solution
1. **Remove** `knowledge/ioc_graph.py` — deprecated since F272
2. **Redirect** remaining callers to `DuckPGQGraph` via adapter if needed
3. **Delete** `graph_service.py` if only wrapping IOCGraph

### Effort: **4 hours**

---

## P2: LMDB Compaction Schedule

### Current State
`duckdb_store.py` má WALManager (`shadow_wal.lmdb`) bez compaction schedule:
```python
L7665: if self._wal_manager is not None:
L7666:     compact_result = self._wal_manager.compact()
```

LMDB compaction best practices:
- Call `environment.compact()` periodically (not after every write)
- M1 8GB: `map_size` should be 2-3× expected data size
- Free space reclamation after bulk deletes

### Solution
```python
# In WALManager or duckdb_store.py
import lmdb

class WALManager:
    def __init__(self, wal_path: Path, map_size: int = 16 * 1024 * 1024):  # 16MB default
        self._env = lmdb.open(str(wal_path), map_size=map_size)
        self._last_compact_check = time.monotonic()
        self._compact_interval_s = 3600  # 1 hour
    
    def compact_if_needed(self) -> dict | None:
        """Compact LMDB if interval elapsed."""
        now = time.monotonic()
        if now - self._last_compact_check < self._compact_interval_s:
            return None
        self._last_compact_check = now
        return self.compact()
    
    def compact(self) -> dict:
        """M1 8GB: compact in background to avoid blocking."""
        # gradual compaction - doesn't block
        with self._env.begin() as txn:
            return self._env.compact(txn)
```

### Effort: **1 hour**

---

## Python 3.14+ Compatibility Notes

### Breaking Changes to Anticipate
1. **`asyncio.Task` context isolation** — already compatible with contextvars
2. **`typing.Self`** — replace `cls` patterns in `@classmethod`
3. **`datetime UTC`** — `datetime.timezone.utc` → `datetime.UTC` (3.11+)
4. **`re.Pattern`** — type hints already use `re.Pattern`, not `Pattern`
5. **No `typing.ByteString`** — deprecated since 3.9, use `bytes`

### No Action Needed
- `contextvars` — stable since 3.7
- `asyncio` — stable
- `dataclasses` — stable

---

## Implementation Order

| Priority | Task | Effort | Files |
|----------|------|--------|-------|
| P2 | Consolidate graphs (lowest risk) | 4h | `knowledge/ioc_graph.py` |
| P2 | LMDB compaction | 1h | `knowledge/duckdb_store.py` |
| P1 | DuckDB composition refactor | 2d | `knowledge/duckdb_store.py` |
| P0 | contextvars migration | 3d | `runtime/sprint_scheduler.py` |
| P1 | SprintScheduler decomposition | 2d | `runtime/sprint_scheduler.py` |

---

## Risk Assessment

| Task | Risk | Mitigation |
|------|------|------------|
| P0 contextvars | High — affects all async flows | Incremental: one ctx var at a time, test after each |
| P1 DuckDB composition | Medium — WAL/Arrow coupling | Extract WALManager first, test, then Arrow |
| P1 SprintScheduler | High — 32K LOC, many deps | Use adapter pattern for gradual migration |
| P2 graphs | Low — deprecated code removal | Verify no live callers before delete |
| P2 LMDB | Low — additive change | Monitor WAL size after compaction |
