# F227D Extraction Guard — Probe Report

**Sprint:** F227D  
**Date:** 2026-05-09  
**Objective:** Hermetic AST guard for F227 extraction boundary (schema/parser/markdown)

---

## Verdict

**EXTRACTION_GUARD_PASS** ✅

---

## What This Guard Does

Prevents `live_sprint_measurement.py` from silently re-absorbing code that was
extracted into dedicated modules in F227A/B/C:

| Module | Must Contain | Must NOT Contain |
|--------|-------------|------------------|
| `benchmarks/live_measurement_schema.py` | `RunMode`, `MeasurementStatus`, `RunQualityVerdict`, `LiveMeasurementResult` | runtime imports |
| `benchmarks/live_measurement_parser.py` | `parse_sprint_report` export | runtime imports |
| `benchmarks/live_measurement_markdown.py` | `render_live_measurement_markdown` export | runtime imports |
| `benchmarks/live_sprint_measurement.py` | wrapper/delegation only | schema class definitions, inline parser/markdown logic |

---

## Check Matrix

| Check | Pass | What It Catches |
|-------|------|-----------------|
| `extracted_module_exists_schema` | ✅ | schema module deleted or moved |
| `extracted_module_exists_parser` | ✅ | parser module deleted or moved |
| `extracted_module_exists_markdown` | ✅ | markdown module deleted or moved |
| `runner_imports_schema` | ✅ | runner has no knowledge of extracted schema |
| `schema_classes_not_in_runner` | ✅ | `class RunMode` / `class LiveMeasurementResult` etc. defined in runner |
| `render_md_delegation` | ✅ | `_render_md` inlines logic instead of delegating to markdown module |
| `parse_sprint_report_delegation` | ✅ | `_parse_sprint_report` inlines logic instead of delegating to parser |
| `schema_no_runtime_import` | ✅ | schema module imports runtime/scheduler/MLX/aiohttp |
| `parser_no_runtime_import` | ✅ | parser module imports runtime/scheduler/MLX/aiohttp |
| `markdown_no_runtime_import` | ✅ | markdown module imports runtime/scheduler/MLX/aiohttp |
| `live_measurement_parser_has_required_exports` | ✅ | `parse_sprint_report` missing from parser |
| `live_measurement_markdown_has_required_exports` | ✅ | `render_live_measurement_markdown` missing from markdown |

---

## Verdicts

| Verdict | Meaning |
|---------|---------|
| `EXTRACTION_GUARD_PASS` | All checks pass — boundary intact |
| `FAIL_SCHEMA_DRIFT` | Schema classes found in runner, or runner doesn't import schema |
| `FAIL_PARSER_DRIFT` | Parser missing exports, or runner doesn't delegate parsing |
| `FAIL_MARKDOWN_DRIFT` | Markdown missing exports, or runner doesn't delegate rendering |
| `FAIL_RUNTIME_IMPORT_IN_EXTRACTED_MODULE` | Extracted module imports runtime/MLX/network |
| `FAIL_MISSING_EXTRACTED_MODULE` | Schema/parser/markdown module not found |

---

## CLI Usage

```bash
python tools/live_measurement_extraction_guard.py \
  --repo-root . \
  --output-json probe_f227d_live_measurement_extraction_guard/live_extraction_guard.json \
  --output-md probe_f227d_live_measurement_extraction_guard/LIVE_EXTRACTION_GUARD.md
```

Exit code 0 = pass, 1 = fail.

---

## Test Coverage

14 probe tests covering:
- ✅ Current repo passes
- ✅ JSON/markdown output files written
- ✅ Schema class in runner → `FAIL_SCHEMA_DRIFT`
- ✅ Runtime import in parser → `FAIL_RUNTIME_IMPORT`
- ✅ Markdown module missing → `FAIL_MISSING_MODULE`
- ✅ Runner missing schema import → `FAIL_SCHEMA_DRIFT`
- ✅ Parser missing `parse_sprint_report` → `FAIL_PARSER_DRIFT`
- ✅ Markdown missing `render_live_measurement_markdown` → `FAIL_MARKDOWN_DRIFT`
- ✅ No live execution (no recursive calls)
- ✅ No network calls
- ✅ `_render_md` non-delegation detected
- ✅ `_parse_sprint_report` non-delegation detected
- ✅ All check functions return correctly-typed tuples