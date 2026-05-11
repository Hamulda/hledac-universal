# Hledac Tech Debt — May 2026 Audit

Generated: 2026-05-11
Session: B (async), C (serialization), D (performance)

---

## Pre-existing Failures (NOT tech debt — known broken in test env)

- `test_tor_availability_cache` — venv missing tor pacakge
- `test_phase_constants_exist` — missing optional dep
- DuckDB tests — missing optional dep in venv
- `source_finding_bridge` — 3-tuple assertions broken

---

## CRITICAL

_(none identified in May 2026 sessions)_

---

## HIGH

### TODO D6 — PUBLIC Lane Timeout Budget Leak
**File:** `runtime/sprint_scheduler.py:4772`
**Priority:** high
**Effort:** medium
**Description:** PUBLIC lane timeout does not release per-lane budget. When PUBLIC times out, wall-clock continues to drain. Consider: per-lane `asyncio.timeout()` with explicit budget accounting and a shared `lane_budget_pool` that PUBLIC releases on timeout.
**Blocked by:** —

### TODO D7 — Per-Finding DuckDB Upsert
**File:** `runtime/sprint_scheduler.py:5675`
**Priority:** high
**Effort:** medium
**Description:** Per-finding DuckDB upsert (`upsert_ioc()` per finding). Batch upserts into groups of 100 findings before committing. DuckDB batch INSERT is ~10× faster than N individual upserts.
**Blocked by:** —

---

## MEDIUM

### TODO B4-2 — Redundant threading.Lock in JARM
**File:** `network/jarm_fingerprinter.py:166`
**Priority:** medium
**Effort:** low
**Description:** `threading.Lock` here is redundant — `_get_db()` is only called from single async context via `run_in_executor`. Consider removing lock entirely or replacing with `asyncio.Lock()` at the fingerprinter level. Low risk — not a deadlock, just noise.
**Blocked by:** —

### TODO B4-3 — Verify io_only_latch Lock Callers
**File:** `core/resource_governor.py:101`
**Priority:** medium
**Effort:** low
**Description:** Verify `_update_io_only_latch_with_lock` is only called via `run_in_executor`. If called from both async context and sync thread, `threading.Lock` is correct here. If async-only, replace with `asyncio.Lock()`.
**Blocked by:** —

### TODO B5-1 — fetchall() Without LIMIT on Examples Table
**File:** `brain/distillation_engine.py:351`
**Priority:** medium
**Effort:** medium
**Description:** `fetchall()` without LIMIT. If examples table grows beyond 10k rows, this will load all rows into RAM. Add: `LIMIT 10000 ORDER BY timestamp DESC` Or refactor to streaming cursor with yield.
**Blocked by:** —

### TODO B5-2 — O(n²) String Concatenation in Loop
**File:** `enhanced_research.py:2221`
**Priority:** medium
**Effort:** low
**Description:** O(n²) string concatenation in loop. Replace with: `parts = []; parts.append(...); synthesis = "".join(parts)`. Low priority unless this path handles > 100 sources per sprint.
**Blocked by:** —

### TODO D1 — Unbounded list.append() in Parquet Buffer
**File:** `runtime/sprint_scheduler.py:10072`
**Priority:** medium
**Effort:** medium
**Description:** `list.append()` without pre-allocation. For sprints with known capacity (`sprint_duration_s × estimated_finding_rate`), consider pre-allocating: `buffer = [None] * estimated_capacity`.
**Blocked by:** —

### TODO D1 — Per-Finding f-string Formatting
**File:** `runtime/sprint_scheduler.py:10147`
**Priority:** medium
**Effort:** medium
**Description:** f-string formatting per finding. Profile this path — if > 5% of sprint CPU, consider caching the format result.
**Blocked by:** —

### TODO D7 — LanceDB ANN Index Rebuild Exceeds M1 RAM
**File:** `knowledge/lancedb_store.py:862`
**Priority:** medium
**Effort:** medium
**Description:** LanceDB ANN index rebuild on startup can exceed 5GB on M1 8GB. Add a RAM check before rebuild: if `uma_available_gb < 4.0`: skip rebuild, use FLAT scan as fallback. Index rebuild should be a manual maintenance op.
**Blocked by:** —

---

## LOW

### TODO B1-4 — fetchall() Without LIMIT on Frontier Table
**File:** `utils/filtering.py:700`
**Priority:** low
**Effort:** low
**Description:** `fetchall()` without LIMIT on frontier table. Cold path (startup only) — safe for now. Add `LIMIT 50000` when frontier table exceeds 100k rows.
**Blocked by:** —

### TODO 8Q/8R — CanonicalFinding Shared DTO Module
**File:** `knowledge/duckdb_store.py:168`
**Priority:** low
**Effort:** medium
**Description:** Consider moving `CanonicalFinding` to shared DTO module if used outside storage layer. Currently referenced across storage boundary.
**Blocked by:** —

### TODO (shared_tensor.py) — Zero-Copy Metal Buffer
**File:** `utils/shared_tensor.py:3,21`
**Priority:** low
**Effort:** high
**Description:** Skutečný zero-copy vyžaduje Metal buffer – to je zatím TODO. `SharedTensor` používá MLX array wrapper, ale true zero-copy vyžaduje Metal shared memory. Aktuální impl je funkční ale ne optimální.
**Blocked by:** —

---

## LEGACY / NOT ACTIONABLE

These items reference deprecated `legacy/` modules or are conditionally deferred:

- `legacy/atomic_storage.py:1178` — `# TODO: Use Hermes for extraction (requires integration)` — LEGACY module
- `legacy/autonomous_orchestrator.py:26713` — `# TODO: actual archive fetch (future)` — LEGACY module
- `planning/htn_planner.py:660` — `# TODO 8S/8T: further refine per-task instrumentation if Hermes` — conditional future
- `planning/htn_planner.py:724` — `# TODO §7.4/§5.15: nahradit quality/corroboration score` — future design decision
- `discovery/discovery_planner.py:146` — `# TODO: commoncrawl-specific endpoint when adapter supports it` — soft, adapter not ready

---

## Summary

| Priority | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 2 |
| MEDIUM   | 7 |
| LOW      | 3 |
| LEGACY   | 5 |
| **Total**| **17** |

Top 3 by risk: D6 (PUBLIC lane budget leak), D7 (DuckDB per-finding upsert), B5-1 (unbounded fetchall).

---

## May 2026 Fixes

### FIXED — streaming_embedder `.shape[0]` on List (LanceDB caller audit)
**File:** `intelligence/streaming_embedder.py:215,266`
**Priority:** HIGH (latent bug, never surfaced in production)
**Fix:** Replace `.shape[0]` with `len()` — `List[List[float]]` has no `.shape`, only `np.ndarray` does.
- Line 215: `embs.shape[0] == len(ids)` → `len(embs) == len(ids)`
- Line 266: `embeddings.shape[0] > 0 and len(ids) == embeddings.shape[0]` → `len(embeddings) > 0 and len(ids) == len(embeddings)`
**Why it never failed:** `_embed_batch()` always returned `np.ndarray` via `_sync_embed_batch()`, but `_embed_fallback()` has the same pattern and the guard `if embs is not None` would silently skip on `AttributeError`. Both paths now use `len()` which works for both `List[List[float]]` and `np.ndarray`.
**Found by:** LanceDB caller audit (May 2026)
**Verified:** `python -m py_compile intelligence/streaming_embedder.py` — OK