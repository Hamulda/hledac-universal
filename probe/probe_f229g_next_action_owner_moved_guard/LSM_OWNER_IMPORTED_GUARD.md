# LSM Owner-Imported Guard — F229G Architectural Note

## Decision

When `run_guard()` cannot find a symbol as a local definition, it now checks whether the symbol is imported from its canonical owner module before returning `FAIL_SYMBOL_MISSING`. This avoids false positives for symbols whose ownership legitimately moved.

## Before / After

| Scenario | Before (F229E) | After (F229G) |
|----------|--------------|--------------|
| `live_sprint_measurement.py` + `_derive_next_action` | `FAIL_SYMBOL_MISSING` | `PASS_OWNER_IMPORTED` |
| `live_measurement_next_action.py` + `_derive_next_action` | `PASS_COMPAT_WRAPPER` | `PASS_COMPAT_WRAPPER` (unchanged) |
| File with no such symbol, no import | `FAIL_SYMBOL_MISSING` | `FAIL_SYMBOL_MISSING` (unchanged) |

## Mechanism

```
run_guard(file, symbol)
  └── if func_node is None:
        owner, sym = _scan_imports_for_symbol(source_text, symbol)
        └── if owner found: PASS_OWNER_IMPORTED
        └── else: FAIL_SYMBOL_MISSING
```

The scan collects the full `from benchmarks.live_measurement_next_action import (...)` block and checks whether `symbol` appears in the imported names.

## New Fields in GuardResult

- `owner_imported_detected: bool` — True when verdict is `PASS_OWNER_IMPORTED`
- `owner_module: str | None` — canonical owner module (e.g. `benchmarks.live_measurement_next_action`)
- `imported_symbol: str | None` — the symbol name as imported

## Design Notes

- `PASS_OWNER_IMPORTED` is semantically distinct from `PASS_OWNER_DELEGATED`:
  - `PASS_OWNER_DELEGATED`: local definition exists and delegates to imported function (compatibility shim pattern)
  - `PASS_OWNER_IMPORTED`: no local definition; symbol is purely imported from canonical owner
- `PASS_OWNER_IMPORTED` contributes to the pass-set in `_render_markdown` (green ✅)
- The `owner_module` field enables future extension: other owner modules could be recognized by pattern
