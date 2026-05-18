# Export Report Pipeline — First Fix Plan
**Date:** 2026-05-18
**Finding:** #1 from `EXPORT_REPORT_PIPELINE_AUDIT.md` — Duplicate Serialization (MEDIUM)
**Fix:** Remove JSON round-trip in `JSONFormatter.format()`
**Scope:** `export/formatters.py` only. No broad export rewrite.

---

## Finding 1 — Duplicate Serialization (MEDIUM)

### Current flow (3 serialization cycles)

```
formatters.py:160-161  dict → JSON str
  boundary_content = _make_serializable(eh.scorecard)
  boundary_text = json.dumps(boundary_content, indent=2, default=str)

formatters.py:163/199  str assignment (no transformation)
  sanitized_scorecard_raw = boundary_text

formatters.py:228-235  JSON str → dict (PARSE BACK)
  sanitized_obj = json.loads(sanitized_scorecard_raw)
  # ... truncated fallback ...
  sanitized_obj = json.loads(sanitized_scorecard_raw[:5000]) if sanitized_scorecard_raw else {}

formatters.py:262-263  dict → JSON file (SERIALIZE AGAIN)
  with open(report_path, "w", ...) as f:
      json.dump(sanitized_obj, f, indent=2, default=str)
```

### Problem
- 3 serialization cycles for identical data
- `json.loads()` can fail on oversized input; truncation at 5000 silently corrupts data
- `sanitized_scorecard_raw` string is only needed as input to `sanitize_outbound()` (expects str)

### Fix design

After `sanitize_outbound()` returns `gate_result["sanitized"]` (a JSON string), immediately parse it back to dict before any further processing. Then work with `sanitized_obj` as dict throughout.

New flow:
```
boundary_content = _make_serializable(eh.scorecard)         # dict
# security gate — still needs str for sanitize_outbound()
boundary_text = json.dumps(boundary_content, ...)
sanitized_str = gate_result.get("sanitized", boundary_text)
sanitized_obj = json.loads(sanitized_str)                   # parse once, back to dict
# proceed with dict operations on sanitized_obj
sanitized_obj["product_value_summary"] = pvs
...
with open(report_path, "w") as f:
    json.dump(sanitized_obj, f, indent=2, default=str)       # serialize once
```

Key changes:
1. Rename `sanitized_scorecard_raw` → `sanitized_str` (clarify it's a string intermediate)
2. Parse to dict immediately after gate returns
3. All downstream operations work on dict only
4. Remove the parse-truncation fallback (line 235) — the truncation was a symptom of the unnecessary roundtrip

### Canonical path analysis

`boundary_text` is only consumed by:
- `sec_coordinator.sanitize_outbound(boundary_text, ...)` — requires string ✅
- `sanitized_scorecard_raw = boundary_text` (fallback when security disabled) ✅

After the fix, `sanitized_str` serves the same purpose. Dict operations no longer need to re-parse.

---

## Test Plan

### Probe test: `test_f234_export_serialization_fix`
**File:** `tests/probe_f234_export_serialization_fix.py` (new)

**Golden output requirements:**
- `report.json` structure: `{sprint_id, product_value_summary, capability_synthesis, analyst_brief?, canonical_run_summary?, runtime_truth?, acquisition_truth?, investigation_packet?}`
- Output shape identical to current (backward compat)
- No truncation — full scorecard preserved
- No corruption of nested objects

**Test steps:**
1. Create `ExportHandoff` with nested scorecard (nested dicts, lists, strings >5000 chars)
2. Run `export_sprint()` with `export_mode="full"`
3. Load `{sprint_id}_report.json`
4. Assert key nested fields present and uncorrupted
5. Assert length of scorecard in JSON ≥ original scorecard dict size

### Invariants to preserve (golden tests)
| Test | Invariant |
|------|-----------|
| `test_json_shape_unchanged` | Output dict has same keys as current |
| `test_nested_scorecard_preserved` | Nested dicts/lists survive round-trip |
| `test_no_truncation` | scorecard field size in JSON ≥ size before export |
| `test_parse_error_fallback` | Invalid JSON from sanitize_outbound falls back to degraded dict |

---

## Implementation steps (when golden test exists)

1. `export/formatters.py:163` — rename `sanitized_scorecard_raw` → `sanitized_str`
2. `formatters.py:172` — `sanitized_str = gate_result["sanitized"]`
3. `formatters.py:180` — `sanitized_str = json.dumps(degraded, default=str)`
4. `formatters.py:191` — same
5. `formatters.py:199` — `sanitized_str = boundary_text`
6. `formatters.py:228` — `sanitized_obj = json.loads(sanitized_str)`
7. `formatters.py:235` — remove truncation fallback (symptom of bug, not fix)
8. `formatters.py:241-258` — all dict operations unchanged (now operate on pre-parsed dict)

---

## Backward compatibility

- JSON output schema unchanged (same dict structure, same keys)
- `sanitize_outbound()` still receives JSON string (same interface)
- Fallback degraded dict on parse error preserved (line 229-237)
- `report.json` file format identical

---

## Verification

```bash
pytest tests/probe_f234_export_serialization_fix.py -v
pytest tests/probe_f214zstd2_transient_artifacts.py -v
pytest tests/probe_f214_scheduler_prelude_complete_truth.py -v
```

All must pass before declaring fix complete.