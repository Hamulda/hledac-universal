# Export/Report Pipeline Audit
**Date:** 2026-05-18
**Scope:** export/, core/__main__.py report generation, runtime/sprint_scheduler result objects, knowledge read paths

---

## Flow: Scheduler Result → Export → File Write

```
SprintScheduler.run() → returns SprintSchedulerResult
  ↓
__main__.py:_print_scorecard_report()
  → builds ExportHandoff (scorecard, runtime_truth, canonical_run_summary, top_nodes)
  → calls export_sprint(handoff)
    ↓
sprint_exporter.py:export_sprint() [thin dispatcher]
  → JSONFormatter.format() [actual logic in formatters.py]
    ├─ _build_product_value_summary() → PVS dict
    ├─ reconcile_terminal_truth() → F229A truth reconciliation
    ├─ _get_acquisition_truth() / _get_runtime_truth() → truth surfaces
    ├─ _build_capability_synthesis()
    ├─ json.dump(sanitized_obj, f) → {sprint_id}_report.json
    ├─ _generate_next_sprint_seeds() → {sprint_id}_next_seeds.json (+ .json.zst)
    ├─ _build_sprint_summary()
    ├─ _get_source_leaderboard() [async store query]
    ├─ async_query_recent_findings(limit=50/100) [4x separate calls]
    ├─ _build_operator_brief()
    ├─ semantic_dedup_findings() [ANE]
    └─ returns dict with all surfaces
```

---

## Finding 1 — Duplicate Serialization (MEDIUM)

**File:** `formatters.py:160-161` + `formatters.py:228-235` + `formatters.py:262-263`

```python
# Line 160-161: dict → JSON string
boundary_content = _make_serializable(eh.scorecard)
boundary_text = json.dumps(boundary_content, indent=2, default=str)

# Line 199: same string reassigned
sanitized_scorecard_raw = boundary_text

# Lines 228-235: parse back to dict
sanitized_obj = json.loads(sanitized_scorecard_raw)

# Lines 262-263: serialize AGAIN
with open(report_path, "w", ...) as f:
    json.dump(sanitized_obj, f, indent=2, default=str)
```

**Issue:** 3 serialization cycles (dict→str→dict→str) for the same data. `sanitized_scorecard_raw` is already a JSON string, but it's re-parsed and then re-serialized.

**Also:** `json.loads()` can fail on oversized input (line 235 truncated to 5000 chars silently — may corrupt data).

**Fix:** Keep `sanitized_obj` as dict throughout, skip round-trip through JSON string.

---

## Finding 2 — Four Separate Store Queries (MEDIUM)

**File:** `formatters.py:321-370`

```python
# Query 1: graph annotations
raw_findings = await store.async_query_recent_findings(limit=50)  # line 324

# Query 2: envelope findings
envelope_findings = await store.async_get_findings_with_envelope(limit=20)  # line 342

# Query 3: sprint_diff findings
all_findings = await store.async_query_recent_findings(limit=100)  # line 350
sprint_diff_findings = [f for f in all_findings if f.get("source_type") == "sprint_diff"]

# Query 4: kill_chain findings (same all_findings, re-filter)
kill_chain_findings = [f for f in all_findings if f.get("source_type") == "killchain_tag"]
```

**Issue:** 4 separate store round-trips. `sprint_diff` and `killchain_tag` filtering could be a single query with a `WHERE source_type IN (...)` filter, reducing 4 calls to 2.

**Memory:** Each `async_query_recent_findings` returns full finding dicts. For 100 findings with envelope data, this is not bounded.

---

## Finding 3 — Evidence Chains Load-All-Then-Slice (LOW)

**File:** `formatters.py:373-397`

```python
if export_mode == "full":
    all_chains = get_all_chains()      # Loads ALL chains from DB
    all_chains.sort(key=lambda c: len(c.steps), reverse=True)
    evidence_chains = [c.to_dict() for c in all_chains[:5]]  # Then slices to 5
```

**Issue:** If `get_all_chains()` returns 10,000 chains, all are loaded before the `[:5]` slice. No pre-filter bound.

**Bounds present:** `MAX_EXPORT_CHAINS=20` in `stix_exporter.py:514` but NOT in `formatters.py`.

---

## Finding 4 — Private Helper Leakage (LOW)

**Architecture stated in `formatters.py:17-23`:**
> "The 44 private helpers stay in sprint_exporter.py — JSONFormatter.format() calls them directly."
> "CONSTRAINT: No circular imports. sprint_exporter.py must NEVER import from formatters.py."

**Reality:**

`sprint_exporter.py` line 257:
```python
from hledac.universal.export.sprint_exporter import _build_investigation_packet
```

`_build_investigation_packet` is a private helper (`_`-prefixed) in `sprint_exporter.py` that is called from `JSONFormatter.format()` in `formatters.py`. This is correct — the docstring explicitly allows this pattern.

**However:** `formatters.py` line 257 imports `_build_investigation_packet` from `sprint_exporter`, but `_build_investigation_packet` itself imports from `runtime.investigation_planner` and `runtime.acquisition_telemetry_reconcile` — these are deep imports that pull in heavy runtime modules (numpy, etc.) even in "slim" export mode.

---

## Finding 5 — ANE Dedup Runs After Multiple Store Queries (LOW)

**File:** `formatters.py:399-405`

```python
# ANE dedup on envelope_findings
envelope_findings = await semantic_dedup_findings(envelope_findings, threshold=0.92)
```

**Issue:** `semantic_dedup_findings` triggers MLX/ANE load if not already loaded — a heavy GPU operation during the export phase. `export_mode == "slim"` should gate this but doesn't.

**Note:** `export_sprint()` docstring (line 459) says "slim" should skip heavy enrichment — but ANE dedup is unconditionally called.

---

## Finding 6 — export_manager.py ExportManager Has No Tests (HIGH)

**File:** `export_manager.py`

`ExportManager` class (lines 56-644) has:
- `export_markdown()` — no tests
- `export_graph_html()` — no tests
- `export_gexf()` — no tests
- `export_graph_sigma_html()` — no tests
- `export_timeline_html()` — no tests
- `export_research_report()` — no tests

This is a significant code surface with file I/O and graph rendering. Zero test coverage.

---

## Finding 7 — sprint_exporter Golden Tests Are Thin (MEDIUM)

The main integration tests are:
- `tests/probe_f214zstd2_transient_artifacts.py` — tests partial export with zstd
- `tests/probe_f214_scheduler_prelude_complete_truth.py` — truth surface reconciliation
- `tests/test_e2e_first_finding.py` — smoke test with live DuckDB

**Missing golden tests:**
- `markdown_reporter.render_diagnostic_markdown()` — no golden output snapshot test
- `stix_exporter.render_stix_bundle()` — no golden output snapshot test
- `stix_exporter.render_cti_stix_bundle()` — no golden output snapshot test
- `stix_exporter.render_full_stix_bundle()` — no golden output snapshot test
- `export_sprint()` JSON output structure — no schema validation test
- `_build_investigation_packet()` output shape — no schema validation

The `_build_investigation_packet` return shape (docstring lines 91-102) is completely untested.

---

## Finding 8 — report_truth_trace.py Is Orphaned (LOW)

**File:** `tools/report_truth_trace.py`

This tool exists in the tools/ directory but:
- Is not imported in any production path (`__main__.py`, `sprint_exporter.py`, `export_manager.py`)
- Has no test coverage
- Appears to be a debug/diagnostic utility not wired into the export pipeline

No evidence it participates in `canonical_run_summary` truth derivation.

---

## Finding 9 — canonical_run_summary Truth Flow Is Correct (VERIFIED)

Verified call chain:
```
core/__main__.py:run_sprint()
  → computes canonical_run_summary (lines ~2275)
  → stores in ExportHandoff.scorecard["canonical_run_summary"]
  → passes ExportHandoff to export_sprint()
    → JSONFormatter.format() attaches it to sanitized_obj (formatters.py:244-247)
```

No discrepancy between `runtime_truth` in scheduler and what gets exported. F229A reconciliation is applied correctly.

---

## Finding 10 — JSONFormatter Imports Heavy Modules in "slim" Mode (MEDIUM)

**File:** `formatters.py:165-197`

`slim` export mode (default) is supposed to skip heavy enrichment. However:
- `UniversalSecurityCoordinator` is imported inside the `if enable_security_enrichment` block — correct, gated
- `semantic_dedup_findings` (ANE) is NOT gated by `export_mode` — runs in both slim and full
- `get_all_chains()` (igraph) is gated by `export_mode == "full"` — correct

---

## Finding 11 — Backward-Compat Dict Handling Is Messy (LOW)

**File:** `sprint_exporter.py:350-405`

`export_partial_sprint()` accepts `ExportHandoff | dict` and has complex fallback logic:
```python
if eh and hasattr(eh, "runtime_truth"):
    runtime_truth = eh.runtime_truth or {}
elif isinstance(handoff, dict):
    runtime_truth = handoff.get("runtime_truth", {})
```

This dual-path code is repeated in multiple places. The docstring (line 450) states canonical producers always pass typed `ExportHandoff`, so these compat paths are dead code from the canonical path perspective.

---

## Summary Table

| # | Severity | Category | Location | Issue |
|---|----------|----------|----------|-------|
| 1 | MEDIUM | Performance | `formatters.py:160-263` | 3x serialization round-trip |
| 2 | MEDIUM | Performance | `formatters.py:321-370` | 4 separate store queries |
| 3 | LOW | Performance | `formatters.py:373-397` | load-all-then-slice evidence chains |
| 4 | LOW | Architecture | `formatters.py:17-23` docstring vs reality | Deep imports in investigation_packet |
| 5 | LOW | Performance | `formatters.py:399-405` | ANE dedup not gated by slim mode |
| 6 | HIGH | Correctness | `export_manager.py` | Zero test coverage on 644-line class |
| 7 | MEDIUM | Correctness | `export/` | Missing golden tests for JSON/STIX/MD output |
| 8 | LOW | Dead Code | `tools/report_truth_trace.py` | Orphaned, not wired to export pipeline |
| 9 | — | Verified | `core/__main__.py` | canonical_run_summary truth flow is correct |
| 10 | MEDIUM | Performance | `formatters.py:399` | ANE/gpu not gated by slim export mode |
| 11 | LOW | Correctness | `sprint_exporter.py:350-405` | backward-compat dict paths are dead code |

---

## Priority Fixes

1. **HIGH:** Add tests for `ExportManager` — the class has 644 lines and zero test coverage
2. **MEDIUM:** Gate ANE `semantic_dedup_findings` behind `export_mode == "full"`
3. **MEDIUM:** Reduce 4 store queries to 2 (sprint_diff + killchain in single call with `IN` filter)
4. **MEDIUM:** Add golden output tests for `markdown_reporter`, `stix_exporter` render functions
5. **MEDIUM:** Remove 3x serialization round-trip in `JSONFormatter.format()`
6. **LOW:** Add `MAX_EXPORT_CHAINS` bound to `formatters.py` evidence chain loading
7. **LOW:** Investigate/report_truth_trace.py utility — wire or delete
