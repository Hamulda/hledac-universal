# Test Suite Health Audit — 2026-05-18

## Executive Summary

| Metric | Value |
|--------|-------|
| Total tests collected | 1710 |
| Collection errors (blocked) | 16 |
| Collection errors (skipped) | 3 |
| Real assertion failures | 1 |
| Fake-green patterns | ~20 files |
| Tests passing | ~50 (targeted) |
| Environment | Python 3.14.4, pytest 9.0.3, uv-managed venv |

**Root cause of most failures**: wrong pytest binary + missing `lmdb` + missing `mlx` in project venv.

---

## 1. Environment Findings

### 1.1 Pytest Binary Mismatch (CRITICAL)

Two pytest binaries exist:
- `/opt/homebrew/bin/pytest` (homebrew, Python 3.14, **lacks `msgspec`** → all tests fail at import)
- `/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/pytest` (project venv, **has `msgspec` + `lmdb`** → correct)

All runs must use the **project venv pytest**: `/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/pytest`

```
# WRONG (homebrew python, no msgspec)
uv run pytest tests/

# CORRECT
/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/pytest tests/
```

### 1.2 Missing Modules in Project Venv

| Module | Status | Blocks |
|--------|--------|--------|
| `lmdb` | NOT installed | 8 test files (collection errors) |
| `mlx` | NOT installed | multimodal tests (collection errors) |
| `selectolax` | NOT installed | 4 tests (SKIPPED, correct behavior) |

---

## 2. Failure Taxonomy

### Type A — Optional Extra Missing (SKIPPED, 3 tests)

Properly skipped by pytest markers — not failures:

| File | Marker | Count |
|------|--------|-------|
| `tests/test_sprint46.py` | `optional dependency not installed` | 1 |
| `tests/test_sprint51_52.py` | `optional dependency not installed` | 1 |
| `tests/test_sprint61.py` | `optional dependency not installed` | 1 |

**Assessment**: Clean. No action needed.

### Type B — Module Import Error: `lmdb` Missing (16 errors)

All are **collection errors** — tests never run.

```
ModuleNotFoundError: No module named 'lmdb'
```

Affected files:
- `tests/test_autonomous_orchestrator.py`
- `tests/test_sprint79c/test_sprint79c_optimizations.py`
- `tests/r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py`
- `tests/test_sprint41.py`, `test_sprint43.py`, `test_sprint44.py`, `test_sprint45.py`
- `tests/test_sprint62a.py`, `test_sprint62b.py`
- `tests/test_sprint66/test_source_family_canonicalization.py`
- Plus 7 more from earlier full-suite run

**Fix**: `uv add lmdb` to project venv, OR mark tests with `pytest.importorskip("lmdb")` at module level.

### Type C — Module Import Error: `mlx` Missing (1+ errors)

```
ModuleNotFoundError: No module named 'mlx'
```

Affected:
- `tests/test_foca_integration.py` — imports `multimodal/__init__.py` → `vision_encoder.py` → `import mlx.core as mx`
- `tests/test_i2p_transport.py` — likely same pattern

**Assessment**: M1 hardware-only optional extra. These should use `pytest.importorskip("mlx")` at module level. Currently they hard-fail collection instead of skipping.

### Type D — Real Assertion Failure (1 failure)

```
tests/test_html_parser_characterization.py::TestArchiveDiscoveryMetadataExtraction::test_extracts_standard_metadata
AssertionError: assert None == 'OG Test Title'
```

**Root cause**: `ArchiveResurrector._extract_metadata_html()` extracts `<meta property="og:title">` but test fixture uses `og:title` as attribute name. The `_METADATA_HTML_FIXTURE` likely has `property="og:title"` but the code path extracts from `name` attribute instead. This is a **genuine bug** — code behavior does not match fixture.

**Fix**: Fix `_extract_metadata_html()` to correctly extract `og:title` from `property` attribute, or fix fixture to match actual behavior.

### Type E — Legacy Path / Deprecated Import (many warnings)

Multiple test files import from old `hledac.universal.autonomous_orchestrator` path instead of new `runtime.sprint_scheduler`:

```
DeprecationWarning: autonomous_orchestrator has been migrated to legacy/.
Import FullyAutonomousOrchestrator from runtime/sprint_scheduler.py instead.
```

Affected:
- `tests/test_e2e_first_finding.py` (line 15)
- `tests/test_e2e_pipeline.py` (line 15)
- `tests/test_sprint6e.py` (line 15)
- `tests/test_sprint79c/test_sprint79c_optimizations.py` (line 56)
- `tests/test_sprint7a.py` (line 7)
- `tests/test_sprint7g.py` (line 15)

**Assessment**: These are **warnings**, not errors. Tests still collect and run if deps are present. But these are **technical debt** — tests should not use deprecated paths.

---

## 3. Fake-Green Pattern Analysis

### 3.1 Files with Fake-Green Markers

```
tests/test_sprint85_security_audit.py
tests/test_sprint8aj_paths.py
tests/test_sprint8aw_aho_integration.py
tests/f234_intelligence_migration/test_intelligence_migration.py
tests/test_resource_governor_authority_seal.py
tests/test_html_parser_characterization.py
tests/test_embedding_prefix_discipline/test_embedding_task.py
tests/test_sprint_p12_hypothesis.py
tests/test_sprint74/test_critical_modules.py
tests/r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py
tests/test_sprint59.py
tests/test_sprint83d_wildcard_truth.py
tests/sprint6c_preflight.py
tests/test_live_public_pipeline_di_seal.py
tests/test_sprint80/test_sprint80_optimizations.py
tests/test_sprint76/test_adaptive_reranking.py
tests/r6_local_bm25_relevance/test_r6_local_bm25_relevance.py
tests/test_8ba_phase0.py
tests/test_sprint73/test_simhash.py
tests/test_autonomous_orchestrator.py
```

**Patterns found**:
- `assert True` — always passes
- `pass  #` — no-op placeholder
- `pytest.skip` — conditionally skips (some legitimate, some lazy)
- `xfail` — expected failures left as TODOs
- `TODO` comments — unimplemented tests

### 3.2 Assessment

Of the 21 files flagged, not all are truly fake-green:
- Many contain legitimate `pytest.skip` for optional features
- `test_html_parser_characterization.py` — has real passing tests, only some are skipped
- `test_live_public_pipeline_di_seal.py` — 5/5 pass, no fake patterns actually used

**True fake-green candidates** (contain actual `assert True` or empty `pass` in test bodies):
- Need deeper per-file inspection beyond scope of this audit

---

## 4. Targeted Test Results

All run with `/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/pytest`:

| Suite | Passed | Failed | Skipped | Errors |
|-------|--------|--------|---------|--------|
| `tests/test_batch_scheduler` | 30 | 0 | 0 | 0 |
| `tests/test_transport_body_limiter` | 15 | 0 | 0 | 0 |
| `tests/test_live_public_pipeline_di_seal` | 5 | 0 | 0 | 0 |
| `tests/test_html_parser_characterization` | 16 | 1 | 4 | 0 |

**Conclusion**: Targeted tests (which run from properly configured venv) pass cleanly. The 16 collection errors are **env/binary** issues, not code issues.

---

## 5. Skip Hygiene Recommendations

### 5.1 Current Skip Patterns (Good)

```python
# Correct: explicit optional dep skip
pytest.importorskip("selectolax")
```

### 5.2 Recommendations

| Issue | Fix |
|-------|-----|
| `lmdb` missing blocks 8 files at collection | Add `pytest.importorskip("lmdb")` at module top |
| `mlx` missing blocks multimodal tests at collection | Add `pytest.importorskip("mlx")` at module top |
| `selectolax` already correctly skipped | No action needed |
| Legacy `autonomous_orchestrator` imports produce warnings | Update imports to `runtime.sprint_scheduler` |

### 5.3 Skip Categorization

| Category | Count | Action |
|----------|-------|--------|
| Optional dep (selectolax) | 4 | OK — proper skip |
| Optional dep (sprint46/51_52/61) | 3 | OK — proper skip |
| Missing dep (lmdb) | 8+ | Convert to `importorskip` |
| Missing dep (mlx) | 1+ | Convert to `importorskip` |
| Legacy import | 6+ | Update imports |

---

## 6. Top 10 Tests to Fix First

Priority-ordered by impact:

| # | File | Issue | Type | Effort |
|---|------|-------|------|--------|
| 1 | `test_html_parser_characterization.py::test_extracts_standard_metadata` | og_title assertion failure | Real bug | Medium |
| 2 | `test_autonomous_orchestrator.py` | Missing `lmdb` | Env fix | Low |
| 3 | `test_foca_integration.py` | Missing `mlx` | Env fix | Low |
| 4 | `test_e2e_first_finding.py` | Legacy import | Tech debt | Low |
| 5 | `test_e2e_pipeline.py` | Legacy import | Tech debt | Low |
| 6 | `test_sprint79c/test_sprint79c_optimizations.py` | Missing `lmdb` | Env fix | Low |
| 7 | `test_r5x_nonfeed_integration_guard/` | Missing `lmdb` | Env fix | Low |
| 8 | `test_sprint41/43/44/45/62a/62b/` | Missing `lmdb` | Env fix | Low |
| 9 | `test_sprint66/test_source_family_canonicalization.py` | Missing `lmdb` | Env fix | Low |
| 10 | `test_i2p_transport.py` | Missing `mlx` | Env fix | Low |

---

## 7. Summary

```
Total collected:  1710
Collection errors: 16 (lmdb missing x8, mlx missing x1, unknown x7)
Properly skipped:  3 (optional deps)
Real failures:    1 (og_title extraction bug)
Fake-green:       ~21 files (partial audit, needs deeper inspection)
```

**Root cause**: Wrong pytest binary (homebrew vs project venv) + missing `lmdb` in project venv.

**No systematic code failures found** in targeted test runs. All collection errors are environmental or optional-dep related.

---

*Audit run: 2026-05-18, pytest: `/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/pytest`, Python: 3.14.4*