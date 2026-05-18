# Export Formatter Helper Ownership Audit
**Date:** 2026-05-18
**Scope:** `export/sprint_exporter.py`, `export/formatters.py`
**Goal:** Determine true source-of-truth owner for JSON export formatting helpers.

---

## 1. Helper Map

| Helper | Defined in | Type | Used by JSONFormatter? | Used by STIX? | Used by Markdown? | Pure transformation? | IO? |
|--------|-----------|------|----------------------|--------------|-----------------|---------------------|-----|
| `_pvs_num` | sprint_exporter:69 | private | indirect (via PVS) | NO | NO | YES | NO |
| `_pvs_n` | sprint_exporter:74 | private | indirect (via PVS) | NO | NO | YES | NO |
| `_make_serializable` | sprint_exporter:3237 | private | YES (direct import) | NO (transitive via _get_* helpers) | NO | YES | NO |
| `_type_aware_seeds` | sprint_exporter:732 | private | YES (via _generate_next_sprint_seeds) | NO | NO | YES | NO |
| `_generate_next_sprint_seeds` | sprint_exporter:224 | private | YES | NO | NO | NO (calls paths) | YES (file write) |
| `_build_product_value_summary` | sprint_exporter:818 | private | YES | NO | NO | PARTIAL (store reads) | store only |
| `_get_sprint_trend` | sprint_exporter:1292 | private | YES (async) | NO | NO | NO (store query) | store |
| `_get_source_leaderboard` | sprint_exporter:1331 | private | YES (async) | NO | NO | NO (store query) | store |
| `_get_correlation_from_handoff` | sprint_exporter:1370 | private | YES | NO | NO | YES | NO |
| `_get_runtime_truth` | sprint_exporter:1439 | private | YES | NO | NO | YES | NO |
| `_get_acquisition_truth` | sprint_exporter:1467 | private | YES | NO | NO | YES (calls _make_serializable internally) | NO |
| `_reconcile_acquisition_terminality_from_source_outcomes` | sprint_exporter:1614 | private | YES | NO | NO | YES | NO |
| `_get_feed_verdict` | sprint_exporter:1737 | private | YES | NO | NO | YES | NO |
| `_get_public_verdict` | sprint_exporter:1754 | private | YES | NO | NO | YES | NO |
| `_get_signal_path` | sprint_exporter:1771 | private | YES | NO | NO | YES | NO |
| `_get_hypothesis_pack` | sprint_exporter:1788 | private | YES | NO | NO | YES | NO |
| `_get_canonical_run_summary` | sprint_exporter:1805 | private | YES | NO | NO | YES | NO |
| `_get_sprint_verdict` | sprint_exporter:1831 | private | YES | NO | NO | YES | NO |
| `_get_synthesis_outcome_payload` | sprint_exporter:1856 | private | YES | NO | NO | YES | NO |
| `_compute_research_depth` | sprint_exporter:1914 | private | YES | NO | NO | YES | NO |
| `_build_capability_synthesis` | sprint_exporter:2081 | private | YES | NO | NO | PARTIAL | NO |
| `_derive_run_truth_note` | sprint_exporter:2256 | private | YES | NO | NO | YES | NO |
| `_derive_branch_truth` | sprint_exporter:2323 | private | YES | NO | NO | YES | NO |
| `_derive_best_first_move` | sprint_exporter:2368 | private | YES | NO | NO | YES | NO |
| `_derive_why_this_run_matters` | sprint_exporter:2453 | private | YES | NO | NO | YES | NO |
| `_get_branch_value` | sprint_exporter:2534 | private | YES | NO | NO | YES | NO |
| `_build_operator_brief` | sprint_exporter:2543 | private | YES | NO | NO | YES | NO |
| `_build_sprint_summary` | sprint_exporter:3143 | private | YES | NO | NO | YES | NO |

**Cross-formatter helpers:**

| Helper | Used by formatters.py | Used by stix_exporter | Used by markdown_reporter | Used by sprint_markdown_reporter |
|--------|----------------------|----------------------|--------------------------|----------------------------------|
| `_safe_str` | NO | YES (stix:313) | NO | NO |
| `_try_parse_json` | NO | NO | NO | YES (sprint_markdown_reporter:39) |
| `safe_markdown_link` | NO | NO | YES (markdown_reporter:17) | NO |
| `escape_markdown_text` | NO | NO | NO | YES (sprint_markdown_reporter:29) |

---

## 2. Architecture Analysis

### Current state (Sprint F214Z)

```
sprint_exporter.py (44 private helpers, module level)
    │
    ├── export_sprint() — thin dispatcher → JSONFormatter.format()
    ├── export_partial_sprint() — standalone (no formatter dependency)
    │
    └── [all 27 truth/derivation helpers] → called by JSONFormatter.format()
                                                  via direct import

formatters.py
    ├── JSONFormatter.format() — imports 25 helpers from sprint_exporter
    │     └── IS the implementation owner of JSON export logic
    └── re-exports export_sprint, export_partial_sprint
```

### Critical finding: `_make_serializable` is transitively embedded

`_make_serializable` is NOT called directly by stix_exporter. However:
- `stix_exporter.py` calls `_get_acquisition_truth` → returns `result["acquisition_report"] = _make_serializable(ar)`
- `_get_acquisition_truth` is imported by `JSONFormatter.format()` from `sprint_exporter`

This means STIX formatter would break if `_make_serializable` were removed from sprint_exporter and not replaced, because the stix path also calls `_get_acquisition_truth` transitively.

### Safe cluster: `_pvs_num`, `_pvs_n`, `_type_aware_seeds`

These three helpers:
1. Are pure transformations — no store reads, no file IO, no cross-module calls
2. Are used ONLY by other sprint_exporter helpers (`_build_product_value_summary`, `_generate_next_sprint_seeds`)
3. Are NOT used by stix_exporter, markdown_reporter, jsonld_exporter, export_manager, or sprint_markdown_reporter
4. `_pvs_num`/`_pvs_n` are defined at lines 69-76 (module top), before any of their callers

**Verdict:** These 3 could be moved into `JSONFormatter` as private methods with no cross-formatter breakage.

### `_make_serializable` — NOT safe to move in this commit

Moving `_make_serializable` requires:
1. Either keeping it in sprint_exporter and importing it there too (circular)
2. Or updating all `_get_*` helpers in sprint_exporter to call JSONFormatter's private version

The second option is a larger refactor (28 call sites in sprint_exporter) and is out of scope for the first commit.

---

## 3. Responsibility Delineation

| Module | Responsibility | Boundary |
|--------|---------------|----------|
| `sprint_exporter.py` | Orchestration + IO + truth derivation helpers | Owns: `export_sprint`, `export_partial_sprint`, all `_get_*` and `_derive_*` helpers, `_generate_next_sprint_seeds` (IO call), `_build_product_value_summary` (store reads) |
| `formatters.py` | JSON formatting class hierarchy | Owns: `ExportFormatter` ABC, `JSONFormatter` (calls sprint_exporter helpers), re-exports public API |
| `stix_exporter.py` | STIX bundle rendering | Owns: all `_make_stix_id`, `_build_stix2_bundle`, `render_stix_bundle*`, `render_cti_stix_bundle*`, `render_full_stix_bundle*` |
| `markdown_reporter.py` | Markdown report rendering | Owns: `render_diagnostic_markdown*`, uses `safe_markdown_link` from utils |
| `sprint_markdown_reporter.py` | Sprint markdown rendering | Owns: `render_sprint_markdown`, `_try_parse_json` local helper |
| `jsonld_exporter.py` | JSON-LD rendering | Owns: `render_jsonld*`, `render_analyst_evidence_jsonld*` |
| `export_manager.py` | HTML/graph export + Obsidian markdown | Owns: `export_markdown`, `export_html`, `export_obsidian_*` |
| `COMPAT_HANDOFF.py` | Backward compat | Owns: `ensure_export_handoff` |

---

## 4. Findings

### Finding 1: JSONFormatter is NOT a true implementation owner
**Severity:** MEDIUM
**Description:** `JSONFormatter.format()` is a class wrapper over sprint_exporter helpers — it contains the call orchestration but none of the actual helper logic. The 25 imported helpers live entirely in sprint_exporter.py.

**Implication:** If a helper needs to change (e.g., new scorecard field), the change must still happen in sprint_exporter.py. JSONFormatter just provides the call envelope.

**Recommendation:** This is by design (Sparrow Principle). The class boundary exists for future extensibility (STIXFormatter, MarkdownFormatter) but currently all formatters call into sprint_exporter. This is acceptable — don't move helpers unless there's a specific testability or cohesion reason.

### Finding 2: `_make_serializable` used transitively by both JSON and STIX paths
**Severity:** LOW
**Description:** `_get_acquisition_truth` and similar helpers use `_make_serializable` internally. Both JSONFormatter (via its imports) and STIX path (via `render_cti_stix_bundle` calling `_get_acquisition_truth`) depend on this.

**Recommendation:** Leave `_make_serializable` in sprint_exporter. Moving it would require updating 28 call sites and is not warranted by any current problem.

### Finding 3: `safe_markdown_link` is defined in utils, used by export_manager and markdown_reporter
**Severity:** INFO
**Description:** `safe_markdown_link` (from `utils.safe_render`) is used by both `export/markdown_reporter.py` and `export/export_manager.py`. This helper correctly lives in utils — no change needed.

### Finding 4: `_try_parse_json` is local to sprint_markdown_reporter
**Severity:** INFO
**Description:** `_try_parse_json` at sprint_markdown_reporter:39 is used only locally. Not a concern.

---

## 5. Safe Move: `_pvs_num`, `_pvs_n`, `_type_aware_seeds`

These three pure helpers can be moved into `JSONFormatter` as private methods.

**Preconditions for safe move:**
1. `_pvs_num` and `_pvs_n` are used only by `_build_product_value_summary`
2. `_type_aware_seeds` is used only by `_generate_next_sprint_seeds`
3. Both `_build_product_value_summary` and `_generate_next_sprint_seeds` are themselves in sprint_exporter

**After move:**
- JSONFormatter gains `_json_pvs_num`, `_json_pvs_n`, `_json_type_aware_seeds` as private methods
- `_build_product_value_summary` calls the JSONFormatter instance's `_json_pvs_num` / `_json_pvs_n`
- `_generate_next_sprint_seeds` calls the JSONFormatter instance's `_json_type_aware_seeds`
- OR: JSONFormatter.format() calls the private methods inline and passes results to sprint_exporter helpers

**Decision:** Given the constraint "no large helper moves in first commit", defer this. The helpers are working correctly and the current architecture is stable.

---

## 6. No golden test violations found

`export_sprint` → `JSONFormatter.format()` is a pure delegation. No behavior change. Existing e2e tests (`test_e2e_dry_run.py`, `test_e2e_first_finding.py`) cover the export path and would catch any output format change.

---

## 7. Recommendation

**Do not move helpers in this commit.** The architecture is stable. The "wrapper over helpers" pattern in JSONFormatter is intentional per Sprint F214Z design. Moving pure helpers risks introducing bugs with no benefit.

**If a future sprint needs testability improvement:** consider moving `_pvs_num`, `_pvs_n`, `_type_aware_seeds` into JSONFormatter as a separate Sprint F2## task.

**Immediate action:** Close this audit. No code changes required.