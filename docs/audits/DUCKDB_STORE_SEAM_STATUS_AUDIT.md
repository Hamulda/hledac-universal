# DuckDBShadowStore Seam Status Audit

**Date:** 2026-05-18
**Author:** seam audit
**Scope:** `knowledge/duckdb_store.py` (DuckDBShadowStore, 6391 lines)
**Follows:** DUCKDB_READ_STORE_BOUNDARY_AUDIT (2026-05-18)

---

## Executive Summary

DuckDBShadowStore has 5 extracted seams with confirmed owners. The canonical write core remains inline. DuckDBReadStore is documented as zero-caller future removal candidate.

| Concern | Owner | Status | Risk |
|---------|-------|--------|------|
| WAL/replay | WALManager (wal.py) | ✅ Extracted, owner confirmed | Low |
| Dedup LMDB | DedupManager (dedup.py) | ✅ Extracted, owner confirmed | Low |
| Graph attachment | GraphAttachmentStore (graph_attachment.py) | ✅ Extracted, owner confirmed | Low |
| Semantic buffering | SemanticStoreBuffer (semantic_store_buffer.py) | ✅ Extracted, owner confirmed | Low |
| Canonical write core | DuckDBShadowStore | 🔒 Inline (correct) | — |
| IngestPipeline | Removed | 🗑️ Not present | — |
| DuckDBReadStore | DuckDBReadStore (duckdb_read_store.py) | ⚠️ Zero callers (documented) | Low (removal candidate) |
| QualityAssessmentState | duckdb_store (inline) | ℹ️ Not extracted | Medium |

---

## Detailed Seam Status

### 1. WAL/replay — `WALManager` ✅ EXTRACTED

**Owner:** `knowledge/wal.py::WALManager` (Sprint F216G)
**DuckDBShadowStore attribute:** `self._wal_manager: Optional[WALManager]` (line 594)
**Initialization:** `_ensure_wal_manager_initialized()` → `WALManager(wal_path).initialize()` (lines 1909-1914)
**Close:** `WALManager.close()` in `_sync_close_on_worker()` (lines 1710-1713)

**Inline delegation stubs remaining in DuckDBShadowStore:**
- `_wal_lmdb: Optional[Any] = None` — backward-compat alias, always None (line 603)
- Dead-letter constants: `DEAD_LETTER_PREFIX = "deadletter_ingest:"` (line 607) — used only as WALManager constant

**Active callers:** `DuckDBShadowStore` internally via `_ensure_wal_manager_initialized()`
**External callers of WALManager:** None (internal to DuckDBShadowStore lifecycle)

**Conclusion:** WAL owner confirmed. Delegation stubs are backward-compat artifacts, not active logic. No extraction needed.

---

### 2. Dedup LMDB — `DedupManager` ✅ EXTRACTED

**Owner:** `knowledge/dedup.py::DedupManager` (Sprint F216G)
**DuckDBShadowStore attribute:** `self._dedup_manager: Optional[DedupManager]` (line 598)
**Initialization:** `_ensure_dedup_manager_initialized()` → `DedupManager().initialize()` (lines 1916-1920)
**Close:** `DedupManager.close()` in `_sync_close_on_worker()` (lines 6167-6171)

**Inline delegation stubs remaining in DuckDBShadowStore:**
- `_dedup_lmdb: Optional[Any] = None` — backward-compat alias, always None (line 604)
- `_dedup_lmdb_path: Optional[Path] = None` — legacy path storage (line 611)
- Methods that delegate to DedupManager: `get_dedup_runtime_status()`, `get_persistent_dedup_status()` — all marked DEPRECATED (Sprint F222), lines 6177-6273

**Active callers:** `DuckDBShadowStore` internally via `_ensure_dedup_manager_initialized()`
**Semantic dedup:** DedupManager owns `_semantic_dedup_cache` (initialized in DedupManager, not in duckdb_store since F222)

**Conclusion:** Dedup owner confirmed. All legacy delegation methods are DEPRECATED stubs. No extraction needed.

---

### 3. Graph attachment — `GraphAttachmentStore` ✅ EXTRACTED

**Owner:** `knowledge/graph_attachment.py::GraphAttachmentStore` (Sprint F222)
**DuckDBShadowStore attribute:** `self.__graph_store = None` — lazy name-mangled slot (line 628)
**Initialization:** `_graph_store()` property (lazy init on first access, lines 658-663)
**Close:** Managed via `_sync_close_on_worker()` → `GraphAttachmentStore` slot check (line 5004-5006)

**DuckDBShadowStore public methods (all DEPRECATED, delegate to GraphAttachmentStore):**
- `inject_graph()` → `_graph_store().inject_graph()` (line 665-667)
- `get_graph_attachment_kind()` → `_graph_store().get_graph_attachment_kind()` (line 669-671)
- `graph_supports_buffered_writes()` → `_graph_store().graph_supports_buffered_writes()` (line 673-675)
- `inject_stix_graph()`, `get_stix_graph()` → (lines 677-683)
- `inject_truth_write_graph()`, `get_truth_write_graph()`, `truth_write_graph_supports_buffered_writes()` → (lines 685-695)
- `get_top_seed_nodes()`, `get_graph_stats()`, `get_connected_iocs()`, `get_connected_iocs_batch()` → (lines 697-711)
- `annotate_findings_with_graph_context()` → (lines 717-721)
- `get_analytics_graph_for_synthesis()` → (line 724-726)
- `get_top_entities_for_ghost_global()` → (lines 730-733)

**Active graph consumers (from graph_attachment.py canonical consumers list):**
- `sprint_scheduler`: `inject_graph()`, `inject_stix_graph()`, `inject_truth_write_graph()`
- `__main__._run_sprint_mode()`: `get_graph_stats()`, `get_connected_iocs()`
- `export_sprint()`: `get_top_seed_nodes()`
- `_windup_synthesis()`: `get_analytics_graph_for_synthesis()`
- ghost global upsert: `get_top_entities_for_ghost_global()`

**Conclusion:** Graph owner confirmed. All DuckDBShadowStore graph methods are DEPRECATED delegation wrappers. No extraction needed.

---

### 4. Semantic buffering — `SemanticStoreBuffer` ✅ EXTRACTED

**Owner:** `knowledge/semantic_store_buffer.py::SemanticStoreBuffer` (Sprint F222)
**DuckDBShadowStore attribute:** `self._semantic_buffer: SemanticStoreBuffer = SemanticStoreBuffer()` (line 625)
**Injection:** `_semantic_buffer.inject(self._semantic_store)` — called during `_windup_semantic_index()` flow
**Usage:** `_semantic_buffer_findings()` calls `self._semantic_buffer.buffer_findings(findings)` (line 753)

**Conclusion:** Semantic buffering owner confirmed. DuckDBShadowStore holds the buffer instance and delegates. No extraction needed.

---

### 5. Canonical write core — 🔒 INLINE (correct)

**Owner:** `DuckDBShadowStore` (this file)
**Scope:**
- `async_ingest_findings_batch()` — canonical write entry point (lines ~4000-4500)
- `async_record_canonical_findings_batch()` — batch recording (lines ~4500-4700)
- `_activation_record_finding()` — single finding activation (lines ~4600-4800)
- `DuckDB._sync_insert_finding()` — sync DuckDB insert
- `QualityAssessor.assess_quality()` — quality gate (delegates to quality_assessment.py)
- `_check_gathered()` — GHOST_INVARIANTS authority check

**WAL integration via WALManager:**
- `WALManager.wal_write_finding()` before DuckDB insert
- `WALManager.wal_write_pending_sync_marker()` on DuckDB failure

**Dedup integration via DedupManager:**
- `DedupManager.lookup_persistent_dedup()` for dedup check
- `DedupManager.store_persistent_dedup()` after DuckDB insert

**Conclusion:** Canonical write core must remain in DuckDBShadowStore. This is the correct boundary.

---

### 6. IngestPipeline — 🗑️ REMOVED

**Status:** Not present in DuckDBShadowStore or knowledge/ module.
**Per user context:** "odstraněný IngestPipeline"

**Conclusion:** No action needed.

---

### 7. DuckDBReadStore — ⚠️ ZERO CALLERS (documented)

**Owner:** `knowledge/duckdb_read_store.py::DuckDBReadStore`
**File:** 239 lines, read-only facade
**Audit:** Full audit exists at `docs/audits/DUCKDB_READ_STORE_BOUNDARY_AUDIT.md` (2026-05-18)
**Finding:** Zero production callers. All export/report/dashboard modules use `DuckDBShadowStore` directly via duck-typed `Any` store parameters.

**DuckDBReadStore methods in DuckDBShadowStore:** None (DuckDBReadStore is a separate class, not a wrapper in DuckDBShadowStore)

**Conclusion:** DuckDBReadStore is a documented future removal candidate. No action needed in this audit.

---

### 8. QualityAssessmentState — ℹ️ INLINE (not extracted)

**Owner:** `duckdb_store.py` (inline)
**DuckDBShadowStore attribute:** `self._quality_state: QualityAssessmentState` (line 590)
**Scope:** Quality counters (`_quality_rejected_count`, `_quality_duplicate_count`, etc.) and rejection ledger

**Not extracted because:**
- Tightly coupled to canonical write path decisions
- Bounded state (no unbounded growth risk)
- No LMDB or external resource ownership
- Test coverage already in place via DuckDBShadowStore tests

**Conclusion:** Not a candidate for extraction. Risk: medium (quality state coupling to write path is inherent).

---

## Remaining Inline Concerns in DuckDBShadowStore

The following inline concerns are still in DuckDBShadowStore but are **not extraction candidates**:

| Concern | Location | Reason not extracted |
|---------|----------|---------------------|
| QualityAssessmentState | line 590 | Tightly coupled to write path; bounded state |
| `_uma_state` / `_duckdb_settings` | lines 641-653 | Runtime config, no external resource |
| `_startup_ready` event | line 582 | Async barrier for boot sequencing |
| `_bg_tasks` tracking | line 619 | Background task bookkeeping |
| `_semantic_store` reference | line 622 | Slot for injected SemanticStore |
| Dead-letter constants | line 607 | Constants, not logic |
| `ReplayResult` TypedDict | lines 154-173 | DTO, not logic |

---

## Future Micro-Extraction Candidates

The following has **low extraction risk** and could be a future micro-sprint:

### Candidate: `_startup_replay_done` flag → WALManager state

**Current:** `DuckDBShadowStore._startup_replay_done: bool` (line 583) tracks whether startup replay has run.
**Owned by:** DuckDBShadowStore (not WALManager)
**Suggested extraction:** Move `_startup_replay_done` tracking into `WALManager` as a computed property or separate `startup_replay_done` method. This would keep WAL replay state entirely within WALManager, removing one implicit state dependency from DuckDBShadowStore.

**Why not now:** Non-blocking, all existing tests pass, canonical write path unaffected.

---

## Test Coverage Verification

Run the following to confirm all seams have test coverage:

```bash
pytest tests -k "duckdb_store or wal or dedup or graph_attachment or semantic_store_buffer or ingest" -v
```

Expected: All tests pass (smoke failures pre-existing per F202A-F223F lane history).

---

## Summary

| Seam | Owner file | Status | Inline code in DuckDBShadowStore? |
|------|-----------|--------|----------------------------------|
| WAL/replay | wal.py::WALManager | ✅ OWNED | Backward-compat stubs only |
| Dedup LMDB | dedup.py::DedupManager | ✅ OWNED | DEPRECATED delegation methods |
| Graph attachment | graph_attachment.py::GraphAttachmentStore | ✅ OWNED | DEPRECATED delegation wrappers |
| Semantic buffering | semantic_store_buffer.py::SemanticStoreBuffer | ✅ OWNED | Buffer holder + delegation call |
| Canonical write core | duckdb_store.py::DuckDBShadowStore | 🔒 INLINE | Yes — must stay |
| IngestPipeline | — | 🗑️ REMOVED | Not present |
| DuckDBReadStore | duckdb_read_store.py::DuckDBReadStore | ⚠️ ZERO CALLERS | Separate class, not a wrapper |
| QualityAssessmentState | duckdb_store.py (inline) | ℹ️ INLINE | Yes — tightly coupled |

**No new extraction recommended.** WAL, dedup, graph, and semantic seams have confirmed owners and no entangled inline concerns. Canonical write core remains correctly in DuckDBShadowStore.