# Sprint F232: Graph Accumulation Seam Audit

**Date:** 2026-05-18
**Status:** AUDIT COMPLETE
**Scope:** runtime/sprint_scheduler.py graph accumulation seam

---

## 1. Caller Map

All graph writes flow through `DuckPGQGraph` (DuckDB backend). `graph_service` is the module-level singleton that returns the same `_DUCKPGQ_GRAPH` instance. `self._ioc_graph` is a direct instantiation — same class, same backend.

| # | Line | Scheduler Method | Target | Note |
|---|------|-----------------|--------|------|
| W1 | 7128 | `_accumulate_findings_to_graph` | `graph_service.upsert_ioc_batch(rows)` | Canonical finding upsert |
| W2 | 10696 | `_buffer_ioc_pivot` | `self._ioc_graph.add_relation()` | Pivot edge creation |
| W3 | 10704 | `_buffer_ioc_pivot` | `_pivot_ioc_graph.buffer_ioc()` | Secondary pivot buffer |
| W4 | 10996 | `buffer_ioc` | `self._ioc_graph.add_relation()` | NER-extracted IOC edge |
| R1 | 8433 | `_get_graph_signal` | `graph_service.graph_stats()` | READ — teardown only |
| R2 | 5832 | `reset_session` | `graph_service.reset_session()` | RESET — teardown only |

## 2. Key Finding: Single Backend

The report `REPORT_GRAPH_AUTHORITY.md` declares `IOCGraph (Kuzu)` as TRUTH and `DuckPGQGraph (DuckDB)` as analytics donor. However, the runtime implementation routes ALL writes through `DuckPGQGraph` — `graph_service.upsert_ioc_batch` delegates to `_get_graph()` which returns `DuckPGQGraph`, not IOCGraph.

This means the "canonical write path" described in the report (scheduler → graph_service → IOCGraph) is actually scheduler → graph_service → DuckPGQGraph. The Kuzu path exists in the module structure but is not the active runtime target.

## 3. Adapter Design: SprintGraphAccumulator

### Interface

```python
class SprintGraphAccumulator:
    """Wraps graph_service write seam. Fail-soft: never blocks sprint."""

    def accumulate_findings(
        self,
        findings: list,
        sprint_id: str = "",
    ) -> int:
        """Batch upsert findings as IOC nodes. Returns count upserted."""

    def buffer_pivot_relation(
        self,
        src: str,
        dst: str,
        rel_type: str = "pivot",
        evidence: str = "",
    ) -> None:
        """Add relation to DuckPGQGraph. Silently no-op on error."""

    def buffer_pivot_ioc(
        self,
        ioc_type: str,
        ioc_value: str,
        confidence: float,
    ) -> None:
        """Buffer IOC to pivot graph and re-enqueue."""

    def get_stats(self) -> dict:
        """Return {graph_nodes, graph_edges, graph_pgq_available}. {} on error."""

    def reset(self) -> None:
        """Clear session-level idempotency trackers."""
```

### Invariants

| # | Invariant | Why |
|---|-----------|-----|
| I1 | `accumulate_findings` returns 0 on any error | Fail-soft: graph must never block sprint |
| I2 | `buffer_pivot_relation` silently no-ops on error | M1 8GB — cannot crash on OOM |
| I3 | `get_stats` returns `{}` on error | Non-blocking read, used only at teardown |
| I4 | No network calls | Graph is local DuckDB |
| I5 | No MLX/model access | Sprint runs alongside model |
| I6 | `reset` clears session trackers | Prevents cross-sprint state leakage |

## 4. Extractability Assessment

**Can this be extracted?**
- `accumulate_findings`: YES — pure transformation (findings → rows → upsert_ioc_batch). No scheduler state dependencies.
- `buffer_pivot_relation`: YES — stateless pass-through to `_ioc_graph.add_relation()`.
- `buffer_pivot_relation` (W3): YES — stateless async pass-through to `_pivot_ioc_graph.buffer_ioc()`.
- `get_stats`: YES — stateless read-only pass-through.
- `reset`: YES — stateless pass-through to `graph_service.reset_session()`.

**Cannot be extracted (scheduler state coupling):**
- `_ioc_graph` initialization (lazy, per-instance): couples to scheduler lifecycle
- `_pivot_ioc_graph` injection point: scheduler owns the reference
- `enqueue_pivot()` calls in `_buffer_ioc_pivot`: scheduler owns the queue

**Verdict:** Phase 1 adapter is feasible for `accumulate_findings`, `buffer_pivot_relation` (W2), and `get_stats`. The async `buffer_pivot_ioc` (W3) is coupled to `enqueue_pivot` which is scheduler-owned — not extractable without moving the queue.

## 5. Test Plan

### Phase 1: Audit Doc (this file) — DONE
### Phase 2: Tests (in `tests/probe_f232_graph_accumulation/`)

| Test | What it verifies |
|------|-----------------|
| `test_accumulate_findings_returns_zero_on_empty` | Empty list → 0, no graph calls |
| `test_accumulate_findings_returns_zero_on_graph_error` | Graph raises → 0, no exception |
| `test_accumulate_findings_builds_correct_rows` | Findings with finding_id → correct (fid, src_type, confidence, sprint_id) rows |
| `test_accumulate_findings_calls_upsert_ioc_batch` | Verify batch call count = 1 |
| `test_buffer_pivot_relation_fail_soft` | Exception → no exception raised |
| `test_get_stats_returns_dict` | Returns {graph_nodes, graph_edges} |
| `test_get_stats_returns_empty_on_error` | Graph error → {} |
| `test_reset_clears_session` | Calls graph_service.reset_session |

All tests use MagicMock/AsyncMock. No live graph, no network, no model.

## 6. Commit Plan

1. `docs(runtime): audit scheduler graph accumulation seam` — this file
2. `tests(probe_f232): add graph accumulation adapter tests` — tests only
3. (Future sprint) `refactor(runtime): extract sprint graph accumulation adapter` — actual extraction

## 7. Discrepancy with F206AI Report

The F206AI report at `probe_graph_authority/REPORT_GRAPH_AUTHORITY.md` line 38 says:
```
runtime/sprint_scheduler.py
  └─ _accumulate_findings_to_graph (line ~1844)
       └─ graph_service.upsert_ioc()
            └─ IOCGraph.upsert_ioc() [Kuzu TRUTH STORE]
```

This is incorrect. `graph_service.upsert_ioc()` delegates to `DuckPGQGraph` via `_get_graph()`, not IOCGraph. The Kuzu-backed IOCGraph is a separate module (`knowledge/ioc_graph.py`) that is NOT called by the scheduler's graph accumulation path.

Update required: The canonical write path in `REPORT_GRAPH_AUTHORITY.md` should reflect DuckPGQGraph as the actual backend, with IOCGraph noted as a separate potential future path.