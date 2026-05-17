# Sprint F227B: SprintGraphAccumulator — Phase B Audit Plan
## Pivot Relation Seam

**Date:** 2026-05-18
**Phase:** B (no production refactor — audit/test plan only)
**Status:** Updated after code-review feedback

---

## 1. Caller / Coupling Map

### `self._ioc_graph` (DuckPGQGraph — stateless pass-through)

| Property | Value |
|---|---|
| Creation | Lazy init in `_buffer_ioc_pivot`: `DuckPGQGraph()` (line 10660) |
| Injection | None — created internally, NOT injected |
| Calls | `_buffer_ioc_pivot` line 10673: `self._ioc_graph.add_relation(...)` |
| Side effects | Adds edge to DuckPGQGraph in-memory |
| Scheduler-owned state | NO — graph owns its own state |

**Graph type:** `quantum_pathfinder.DuckPGQGraph` (line 10659 import)
**Critical:** `DuckPGQGraph.add_relation` (quantum_pathfinder.py:1256-1263) has **NO internal try/except**. Any extraction must add the wrapper.

### `self._pivot_ioc_graph` (IOCGraph — scheduler-owned queue coupling)

| Property | Value |
|---|---|
| Creation | External injection via `inject_ioc_graph(ioc_graph)` (line 10352) |
| Injection site | Caller (e.g., `core.__main__.run_sprint`) passes graph instance |
| Calls | `_buffer_ioc_pivot` line 10681: `await self._pivot_ioc_graph.buffer_ioc(...)` |
| Side effects | Writes to external graph + triggers `enqueue_pivot` |
| Scheduler-owned state | YES — reference to caller's graph object |
| Lifecycle | Caller-owned — this boundary does NOT manage caller graph lifecycle |

**NOT extracted.** Caller graph lifecycle is out of scope.

### `enqueue_pivot` (scheduler-owned pivot queue)

| Property | Value |
|---|---|
| Creation | `_pivot_queue = asyncio.Queue(maxsize=MAX_PIVOT_QUEUE)` in `__init__` |
| Calls | `_buffer_ioc_pivot` line 10684: `self.enqueue_pivot(...)` |
| Drain site | `_drain_pivot_queue()` consumed by scheduler's pivot loop |
| Scheduler-owned state | YES — `_pivot_queue` is scheduler-internal |

**NOT extracted.** Queue management is scheduler's responsibility.

---

## 2. `_buffer_ioc_pivot` Telemetry / Side Effects

```python
async def _buffer_ioc_pivot(self, ioc_type, ioc_value, confidence):
    # 1. Lazy init of _ioc_graph (DuckPGQGraph) — no telemetry
    if not hasattr(self, "_ioc_graph"):
        self._ioc_graph = DuckPGQGraph()
    # 2. URL parse for domain extraction — no telemetry
    domain = urlparse(ioc_value).netloc
    # 3. add_relation to _ioc_graph — no telemetry
    self._ioc_graph.add_relation(ioc_value, domain or ioc_value, ...)
    # 4. buffer_ioc to _pivot_ioc_graph — caller-owned, no scheduler telemetry
    # 5. enqueue_pivot — updates _pivot_stats["total"]
```

**Telemetry:** Only `self._pivot_stats["total"]` (line 10522) — incremented on enqueue, not on add_relation.

**No logging, no metrics, no side effects on sprint result.**

---

## 3. Proposed Boundary

### Extract: `SprintGraphAccumulator.buffer_pivot_relation(ioc_value, ioc_type, confidence) -> None`

**Scope:**
- Wrap ONLY `self._ioc_graph.add_relation(...)` (line 10673-10677)
- No queue management
- No `_pivot_ioc_graph.buffer_ioc()`
- No `enqueue_pivot`

**Signature:**
```python
def buffer_pivot_relation(
    self,
    ioc_value: str,
    ioc_type: str,
    confidence: float,
) -> None:
    """Add pivot edge to DuckPGQGraph. Fail-safe: graph errors are silently dropped."""
    if not hasattr(self, "_ioc_graph"):
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
        self._ioc_graph = DuckPGQGraph()
    # Extract domain for URL-type IOCs
    domain = None
    try:
        from urllib.parse import urlparse
        domain = urlparse(ioc_value).netloc
    except Exception:
        pass
    try:
        self._ioc_graph.add_relation(
            ioc_value,
            domain or ioc_value,
            rel_type="pivot",
            evidence="pivot",
        )
    except Exception:
        pass  # fail-safe — must be added since DuckPGQGraph.add_relation has no internal try/except
```

**CRITICAL I3 implementation:** `DuckPGQGraph.add_relation` (quantum_pathfinder.py:1256) has NO internal exception handling. The extracted method MUST wrap the call in `try/except Exception` — this is the fail-safe guarantee.

**Kept in scheduler:**
- `self._pivot_ioc_graph` reference (from `inject_ioc_graph`)
- `_pivot_ioc_graph.buffer_ioc()` call
- `self._pivot_queue` and `enqueue_pivot()`
- `_pivot_stats` dict

---

## 4. Test Plan

### 4.1 Unit Tests (probe_f227b/)

| Test | Description |
|---|---|
| `test_buffer_pivot_relation_url_extracts_domain` | Given `ioc_value="https://evil.com/path"`, `ioc_type="url"`, verifies `add_relation` called with `domain="evil.com"` |
| `test_buffer_pivot_relation_non_url_uses_value_as_target` | Given `ioc_value="1.2.3.4"`, `ioc_type="ipv4"`, verifies `add_relation` called with target=ioc_value (no urlparse) |
| `test_buffer_pivot_relation_lazy_init_creates_graph` | First call creates `_ioc_graph` lazily |
| `test_buffer_pivot_relation_fail_soft_on_graph_error` | `DuckPGQGraph.add_relation` raises → silently dropped, no propagated error |
| `test_buffer_pivot_relation_duckdb_import_failure_is_silent` | `DuckPGQGraph()` constructor raises `ImportError` → no exception propagated |
| `test_buffer_pivot_relation_no_queue_interaction` | Verifies `enqueue_pivot` NOT called (dual-mock: call_count==0 + assert_not_called) |
| `test_scheduler_retains_pivot_queue_after_extraction` | After extraction, `enqueue_pivot` and `_pivot_ioc_graph.buffer_ioc` remain in scheduler |

### 4.2 Edge Case Tests

| Test | Description |
|---|---|
| `test_buffer_pivot_relation_duckdb_import_failure_is_silent` | DuckPGQGraph construction raises ImportError → silently ignored, no exception propagated |

### 4.3 Integration Tests

| Test | Description |
|---|---|
| `test_pivot_flow_end_to_end` | Full pivot: finding → `_buffer_ioc_pivot` → graph write + enqueue |
| `test_inject_ioc_graph_still_works` | After extraction, `inject_ioc_graph()` still sets `_pivot_ioc_graph` |
| `test_pivot_queue_draining_unchanged` | `_drain_pivot_queue` still processes queued pivots correctly |

### 4.4 Regression Tests (existing)

| Test file | Purpose |
|---|---|
| `tests/probe_f227a/` | Phase A probe tests (accumulate_findings) |
| `tests/runtime/probe_f227a/` | Phase A scheduler integration |

---

## 5. Invariants to Verify

| # | Invariant | Verification method |
|---|---|---|
| I1 | `buffer_pivot_relation` does NOT call `enqueue_pivot` | Dual-mock: `enqueue_pivot.call_count == 0` + `assert_not_called()` — catches double-call bug where both mock and real method fire |
| I2 | `buffer_pivot_relation` does NOT call `buffer_ioc` | Same dual-assert pattern |
| I3 | `buffer_pivot_relation` is fail-safe (graph errors silently dropped) | Patch `add_relation` to raise → no exception propagates. **CRITICAL:** `DuckPGQGraph.add_relation` has NO internal try/except — extracted method MUST add the wrapper itself. |
| I4 | Scheduler retains `_pivot_ioc_graph` ref after extraction | Verify `inject_ioc_graph` still sets field |
| I5 | Scheduler retains `_pivot_queue` after extraction | Verify `enqueue_pivot` still puts to queue |
| I6 | Phase A extraction (`accumulate_findings`) still works | Existing probe tests pass |
| I7 | DuckPGQGraph() construction failure is handled | Patch constructor to raise → silently ignored |

**I3 implementation note:** `DuckPGQGraph.add_relation` (quantum_pathfinder.py:1256) has no internal try/except. The extracted `buffer_pivot_relation` MUST wrap `self._ioc_graph.add_relation(...)` in `try/except Exception`.

---

## 6. Files to Modify (Phase B implementation — future)

| File | Change |
|---|---|
| `runtime/graph_accumulator.py` | Add `buffer_pivot_relation()` method with fail-safe wrapper |
| `runtime/sprint_scheduler.py` | Call `self._graph_accumulator.buffer_pivot_relation(...)` instead of direct `add_relation`; keep `_buffer_ioc_pivot` for queue wiring |
| `tests/probe_f227b/` | New probe tests |

---

## 7. What NOT to Touch

- `_pivot_ioc_graph.buffer_ioc()` — caller-owned, not extracted
- `enqueue_pivot()` — scheduler-owned queue, not extracted
- `_pivot_queue` management — scheduler-owned
- Caller graph lifecycle (`inject_ioc_graph` reference lifetime) — out of scope
- Any graph authority documentation (doc fix not in scope)
- New graph backend introduction

---

## 8. Run Command

```bash
pytest tests/probe_f227a/ -v -q
```

After Phase B implementation:
```bash
pytest tests/probe_f227b/ -v -q
```