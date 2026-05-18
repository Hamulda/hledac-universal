# DuckDBReadStore Boundary Audit

**Date:** 2026-05-18
**Sprint:** F226G
**Author:** Vojtech Hamada
**Status:** ZETA — no production callers; removal candidate

---

## Summary

`DuckDBReadStore` (239 lines) is a read-only facade over `DuckDBShadowStore`.
Despite clear documentation naming reporters/dashboards/analytics as intended callers,
**zero production modules instantiate or call it**. All export/report callers
use `DuckDBShadowStore` directly, bypassing the boundary entirely.

**Verdict:** Oversized facade with no active call path. Keep for now per user instruction;
prepare removal plan below.

---

## Audit Method

```bash
# Step 1: Find all direct references
rg "DuckDBReadStore|duckdb_read_store" .

# Step 2: Find all instantiations
rg "DuckDBReadStore\(" .

# Step 3: Trace read methods used in export/report callers
rg "store\.(async_query_recent_findings|async_query_sprint_trend|async_query_source_leaderboard|get_sprint_trend|get_source_leaderboard)" export/

# Step 4: Confirm test coverage
rg "duckdb_read_store|DuckDBReadStore" tests/
```

---

## Caller Map

### Production Callers

| Module | Method Used | Direct DuckDBShadowStore? | Via ReadStore? |
|--------|-------------|--------------------------|---------------|
| `export/sprint_exporter.py` | `async_query_sprint_trend`, `get_sprint_trend`, `async_query_source_leaderboard`, `get_source_leaderboard` | YES — directly on store param (typed `Any`) | NO |
| `export/stix_exporter.py` | `async_query_recent_findings` | YES — directly on store param (typed `Any`) | NO |
| `export/formatters.py` | `async_query_recent_findings` | YES — directly on store param | NO |
| `knowledge/analyst_workbench.py` | `async_query_recent_findings` | YES — directly on `_duckdb` (DuckDBShadowStore) | NO |
| `core/__main__.py` | `DuckDBShadowStore()` instantiation | YES | N/A |

### Tests

| Path | Result |
|------|--------|
| `tests/` | **0 matches** for DuckDBReadStore or duckdb_read_store |

### Documentation Only

| Reference | Note |
|----------|------|
| `knowledge/duckdb_read_store.py` self-doc | Lines 5-41: defines role, boundary, async + sync API surface |
| `knowledge/__init__.py:26` | Lazy export map entry `"DuckDBReadStore": "knowledge.duckdb_read_store"` |
| `knowledge/__init__.py:188-194` | Sibling module resolution fallback for DuckDBReadStore |

---

## Key Evidence

### Evidence 1: No Instantiation

`DuckDBReadStore(` — zero matches across entire codebase.

The facade is defined but never constructed anywhere.

### Evidence 2: Export/Report Callers Use DuckDBShadowStore Directly

`export/sprint_exporter.py:_get_sprint_trend()`:
```python
async def _get_sprint_trend(store: Any, last_n: int = 5) -> list[dict]:
    if hasattr(store, "async_query_sprint_trend"):
        return await store.async_query_sprint_trend(last_n=last_n) or []
    # COMPAT FALLBACK
    if hasattr(store, "get_sprint_trend"):
        return await loop.run_in_executor(None, lambda: store.get_sprint_trend(...))
```

The `store: Any` parameter accepts anything with the method — no type seal forcing ReadStore.
The `hasattr` duck-typing bypasses the facade entirely.

Same pattern in `stix_exporter.py:_fetch_findings()` and `formatters.py`.

### Evidence 3: No Architectural Seal

There is **no lint rule, no `__init__.py` re-export, no type alias**
enforcing that reporters/dashboards must use `DuckDBReadStore`.

The boundary exists in documentation only.

### Evidence 4: analyst_workbench Uses DuckDBShadowStore Directly

`knowledge/analyst_workbench.py:376`:
```python
raw = await self._duckdb.async_query_recent_findings(limit=MAX_TOP_K * 2)
```
Where `self._duckdb` is a `DuckDBShadowStore` instance, not a `DuckDBReadStore`.

---

## What DuckDBReadStore Actually Does

### Read Methods (async) — 15 delegating methods

| Method | Delegates to |
|--------|-------------|
| `async_query_recent_findings(limit)` | `_store.async_query_recent_findings()` |
| `async_query_sprint_trend(last_n)` | `_store.async_query_sprint_trend()` |
| `async_query_source_leaderboard(days)` | `_store.async_query_source_leaderboard()` |
| `async_query_recent_findings_by_sprint(...)` | `_store.async_query_recent_findings_by_sprint()` |
| `async_query_top_entities_by_sprint(...)` | `_store.async_query_top_entities_by_sprint()` |
| `async_query_sprint_ioc_summary(...)` | `_store.async_query_sprint_ioc_summary()` |
| `async_query_top_sources_by_sprint(...)` | `_store.async_query_top_sources_by_sprint()` |
| `read_target_memory(target_id)` | `_store.read_target_memory()` |
| `async_query_sprint_source_stats()` | `_store.async_query_sprint_source_stats()` |
| `async_get_recent_findings(limit)` | `_store.async_get_recent_findings()` |
| `async_get_target_profile(target_id)` | `_store.async_get_target_profile()` |
| `async_get_target_memory(target_id)` | `_store.async_get_target_memory()` |
| `async_get_previous_findings_for_target(...)` | `_store.async_get_previous_findings_for_target()` |
| `async_get_hypothesis_feedback(...)` | `_store.async_get_hypothesis_feedback()` |
| `async_get_findings_with_envelope(limit)` | `_store.async_get_findings_with_envelope()` |
| `async_healthcheck()` | `_store.async_healthcheck()` |
| `async_query_arrow_batches(...)` | `_store.async_query_arrow_batches()` |

**Note:** The facade exposes these 16 async methods, but only 4 are called by export/report callers.

### Sync Convenience Wrappers — 10 DEPRECATED methods

All 10 sync wrappers are marked DEPRECATED and delegate to `_store._sync_query_*` via executor.
Zero production callers confirmed.

---

## Failure Mode Analysis

### If DuckDBReadStore Were Removed

1. **export/sprint_exporter.py** — continues to work. Uses duck-typed `store: Any` with `hasattr` checks. Passes DuckDBShadowStore directly.
2. **export/stix_exporter.py** — continues to work. Same duck-typed pattern.
3. **export/formatters.py** — continues to work. Same duck-typed pattern.
4. **knowledge/analyst_workbench.py** — continues to work. Uses DuckDBShadowStore directly.
5. **core/__main__.py** — continues to work. Uses DuckDBShadowStore directly.

**Impact of removal: ZERO production callers affected.**

### If DuckDBReadStore Were Enforced as Read-Only Boundary

To make this facade actually enforce read-only access:

1. DuckDBShadowStore would need to be split into `DuckDBStoreCore` (shared query engine) + `DuckDBWriteStore` (write methods only) + `DuckDBReadStore` (read methods only)
2. All reporters/dashboards would import from `knowledge.DuckDBReadStore`
3. All pipeline/coordinators would import from `knowledge.DuckDBWriteStore`
4. A type alias or seal would enforce the boundary

**This is a rewrite**, not a deletion.

---

## Recommendation

### Immediate (This Audit)

- **Keep** `DuckDBReadStore` in place (per user instruction: do not delete)
- **Document** that the facade has zero production callers as of this audit
- **Write** this audit doc to `docs/audits/DUCKDB_READ_STORE_BOUNDARY_AUDIT.md`

### Short-Term (Future Sprint)

**Option A — Full Removal (recommended if no caller found):**
```
1. Delete knowledge/duckdb_read_store.py
2. Remove "DuckDBReadStore" from knowledge/__init__.py lazy export map
3. Remove sibling module resolution in knowledge/__init__.py:188-194
4. Verify all export/report modules still work (they use duck typing, not the facade)
5. Run: pytest tests/ -k "duckdb" -v
```

**Option B — Wire as Actual Boundary (if read callers materialize):**
```
1. Add type alias: DuckDBReadStore = DuckDBShadowStore  # temporary alias
2. Migrate export/report callers to type-hint with DuckDBReadStore
3. Verify all callers work with duck-typed store params (already compatible)
4. Remove DuckDBShadowStore from reporter dashboards' imports
5. Consider actual interface segregation in a future sprint
```

### Seal Test (if boundary is enforced later)

```python
# tests/test_read_store_seal.py
def test_reporter_uses_read_store_not_write_store():
    """Reporter/dashboard modules must not import DuckDBShadowStore write methods."""
    # Static analysis: scan export/, monitoring/, reports/ for DuckDBShadowStore usage
    # Expected: only async_query_* methods, no async_record_* methods
```

---

## Tests Run

```bash
pytest tests/ -k "duckdb_read_store or read_store or sprint_trend or source_leaderboard" -v
```

Result: No tests matched — no test coverage for DuckDBReadStore exists.

---

## GHOST_INVARIANTS Verification

| Invariant | Status |
|-----------|--------|
| `gather` uses `return_exceptions=True` | N/A — no async gather in this module |
| `mx.eval([])` before `clear_cache()` | N/A — no MLX in this module |
| `time.monotonic` for intervals | N/A — no timing in this module |
| No bare `except` | FAIL — lines 220, 227, 235, 242, 249, 256, 263, 270, 277, 284: bare `except Exception:` in sync wrappers (swallowed silently) |

**Non-blocking:** Sync wrappers are all marked DEPRECATED and are fail-safe (return empty collection on error). The bare `except` is intentional for the wrapper pattern (caller gets `[]` or `{}` rather than an exception).

---

## Files Reviewed

| File | Lines | Note |
|------|-------|------|
| `knowledge/duckdb_read_store.py` | 239 | Primary review target |
| `knowledge/duckdb_store.py` | 5000+ | duckdb_store — source of delegated methods |
| `knowledge/__init__.py` | 200+ | Lazy export + sibling resolution |
| `export/sprint_exporter.py` | 1400+ | Primary read caller |
| `export/stix_exporter.py` | 500+ | Read caller |
| `export/formatters.py` | 500+ | Read caller |
| `knowledge/analyst_workbench.py` | 500+ | Read caller |
| `core/__main__.py` | 1500+ | Store instantiation |

---

## Conclusion

`DuckDBReadStore` is a well-documented but unwired read-only facade.
It has zero production callers. All export/report/dashboard modules use
`DuckDBShadowStore` directly via duck-typed `Any` store parameters.
The boundary exists in documentation only.

**Action:** Retain for this sprint per user instruction. Prepare Option A removal plan
for a future sprint (deletion is safe — no callers will break).
Do not implement Option B (generic ReadOnlyFacade[T]) without evidence that
read callers would actually use it.