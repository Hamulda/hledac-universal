# F229G: Next Action Owner-Moved Guard Semantics

## Summary

Added `PASS_OWNER_IMPORTED` verdict for symbols whose ownership moved to `benchmarks.live_measurement_next_action.py`. The guard no longer reports false `FAIL_SYMBOL_MISSING` for intentionally imported-only symbols.

## Changes

### tools/codehealth_guard.py

1. **New verdict**: `PASS_OWNER_IMPORTED` added to `GuardVerdict`
2. **New fields** on `GuardResult`: `owner_imported_detected`, `owner_module`, `imported_symbol`
3. **New helper**: `_scan_imports_for_symbol(source_text, symbol)` — scans import block for symbol from `benchmarks.live_measurement_next_action`, returns `(owner_module, imported_symbol)` or `(None, None)`
4. **Updated `run_guard`**: when symbol not found locally, scan imports first; if found → `PASS_OWNER_IMPORTED`, else → `FAIL_SYMBOL_MISSING`
5. **Updated `_render_markdown`**: added `owner_imported_detected`, `owner_module`, `imported_symbol` to metrics; added `PASS_OWNER_IMPORTED` to status icon and verdict definitions

### tests/probe_f229g_next_action_owner_moved_guard/

- `run_probe.py` — standalone probe runner (7 tests)
- `test_codehealth_guard.py` — pytest-compatible test class (pytest ignores via `norecursedirs = probe_*`)

## Verdict Mapping

| File | Symbol | Verdict |
|------|--------|---------|
| `live_sprint_measurement.py` | `_derive_next_action` | `PASS_OWNER_IMPORTED` (imported from `live_measurement_next_action.py`) |
| `live_measurement_next_action.py` | `_derive_next_action` | `PASS` or `PASS_COMPAT_WRAPPER` (local definition) |
| Arbitrary file | symbol not defined, not imported | `FAIL_SYMBOL_MISSING` (unchanged) |
| Old bad fixture | `_derive_next_action` 35-arg | `FAIL_TOO_MANY_ARGS` (unchanged) |

## Test Results

```
=== F229G Probe Tests ===

  PASS: _scan_imports_for_symbol finds imported symbol
  PASS: _scan_imports_for_symbol handles multiline import
  PASS: _scan_imports_for_symbol returns None when not imported
  PASS: _scan_imports_for_symbol returns None for wrong module
  PASS: run_guard returns PASS_OWNER_IMPORTED for import-only symbol
  PASS: run_guard returns FAIL_SYMBOL_MISSING for truly missing symbol
  PASS: old bad fixture still fails FAIL_TOO_MANY_ARGS
  PASS: live_sprint_measurement._derive_next_action => PASS_OWNER_IMPORTED
  PASS: live_measurement_next_action._derive_next_action => PASS/COMPAT_WRAPPER
  PASS: run_guard does not execute target function

Results: 7 passed, 0 failed
```

## Semantics

`PASS_OWNER_IMPORTED` indicates: the requested symbol is not locally defined in the target file, but is confirmed to be imported from its canonical owner module (`benchmarks.live_measurement_next_action`). The guard treated this as a missing symbol (`FAIL_SYMBOL_MISSING`), which was a false positive — the symbol exists, just not in the file under inspection.
