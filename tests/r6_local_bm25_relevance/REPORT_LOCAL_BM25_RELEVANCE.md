# SPRINT R6 — Local BM25 Relevance Over Accepted Findings
## F228C Implementation Report

---

## Integration Decision

**Chosen:** Option A — `runtime/sprint_advisory_runner.py`

Reason: `SprintAdvisoryRunner` already orchestrates all advisory steps (pivot planner → executor → governor → analyst brief → local search) at teardown. `_all_findings` is accessible via `getattr(self._scheduler, "_all_findings", [])`. Adding `build_search_documents_from_findings()` + step 5 required ~60 lines of new code and zero coupling to sidecar_bus.

`sidecar_bus.py` handles real-time accepted-finding sidecars (leak_sentinel, identity_stitching, etc.) during sprint execution — not the right seam for a post-sprint local search advisory.

---

## Changes Made

### `runtime/sprint_advisory_runner.py`
- **Added `build_search_documents_from_findings()`** (module-level function, lines 50–94):
  - Converts `CanonicalFinding` objects → `SearchDocument` records
  - Skips findings without `payload_text` (fail-soft)
  - Deduplicates by URL to prevent metadata explosion
  - Bound: `MAX_INDEXED_FINDINGS = 5000`
  - No canonical writes, no DuckDB calls

- **Extended `AdvisoryRunOutcome` dataclass** (4 new fields):
  - `local_search_indexed: int = 0` — number of findings indexed
  - `local_search_elapsed_ms: float = 0.0` — wall time
  - `local_search_top_results: list` — list[dict] with url/title/score/source_type/finding_id

- **Replaced `_run_local_search_advisory()`** (step 5):
  - New: indexes `_all_findings` via `build_search_documents_from_findings()`
  - New: searches via `LocalSearchSeam.search(query, top_k=10)`
  - New: populates all `local_search_*` telemetry fields
  - New: `asyncio.CancelledError` re-raised (GHOST_INVARIANT)
  - No DuckDB write, no persistent DB, no MLX, no network

- **Propagated new fields** through all 6 `AdvisoryRunOutcome()` return sites (steps 1–4 + analyst_brief)

- **Exported** `build_search_documents_from_findings` in `__all__`

### `tests/r6_local_bm25_relevance/test_r6_local_bm25_relevance.py`
18 probe tests covering all 15 requirements + bonus tests:
1. CanonicalFinding → SearchDocument conversion
2. Findings with payload_text indexed
3. Findings without payload_text skipped safely
4. Duplicate URL deduplication
5. LocalSearchSeam.search bounded top_k
6. Empty findings → empty docs
7. Advisory does not call DuckDB write
8. Advisory does not create persistent DB
9. No network calls in build_search_documents
10. No MLX/model load
11. No browser/stealth imports
12. MAX_RESULT_SET enforced
13. search_index import safe
14. Integration point fail-soft
15. CancelledError re-raised
+ Bonus: dict-like findings, MAX_INDEXED_FINDINGS bound, run_all_advisories step 5

---

## Architectural Compliance

| Requirement | Status |
|---|---|
| DuckDBShadowStore remains canonical facts authority | ✅ No DuckDB writes |
| LocalSearchSeam advisory/cache only | ✅ No new storage authority |
| No persistent new DB | ✅ In-memory only |
| No direct canonical writes from LocalSearchSeam | ✅ Advisory only |
| No embeddings/MLX/model load | ✅ No MLX imports |
| No network | ✅ `build_search_documents` has zero network calls |
| Bounded result count | ✅ `MAX_INDEXED_FINDINGS=5000`, `top_k=10` |
| Fail-soft | ✅ All exceptions caught, CancelledError re-raised |
| CancelledError propagated | ✅ `except asyncio.CancelledError: raise` |
| `__all__` exported | ✅ Added `build_search_documents_from_findings` |

---

## Test Results

```
tests/r6_local_bm25_relevance/  18 passed ✅
tests/r5x_nonfeed_integration_guard/  19 passed ✅ (regression)
```

---

## Files Modified

| File | Change |
|---|---|
| `runtime/sprint_advisory_runner.py` | Added `build_search_documents_from_findings()`, extended `AdvisoryRunOutcome`, replaced step 5 method, propagated fields |
| `tests/r6_local_bm25_relevance/test_r6_local_bm25_relevance.py` | 18 probe tests |

## Backups Created

- `knowledge/search_index.py.bak_R6_LOCAL_BM25_RELEVANCE`
- `runtime/sprint_advisory_runner.py.bak_R6_LOCAL_BM25_RELEVANCE`
- `runtime/sidecar_bus.py.bak_R6_LOCAL_BM25_RELEVANCE`
- `runtime/sprint_scheduler.py.bak_R6_LOCAL_BM25_RELEVANCE`

---

## What the Sprint Gains

- **Local relevance compass**: accepted findings indexed at teardown, searched with sprint query
- **Evidence surfacing**: `local_search_top_results` provides structured top-10 hits with url/title/score/source_type/finding_id
- **Telemetry**: `local_search_indexed`, `local_search_elapsed_ms`, `local_search_hits`, `local_search_source`, `local_search_error`
- **Zero cost**: no new DB, no model load, no network, purely in-memory LocalSearchSeam at sprint teardown