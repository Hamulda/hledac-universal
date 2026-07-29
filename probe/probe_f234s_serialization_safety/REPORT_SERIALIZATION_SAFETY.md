# F234S Serialization Safety Report

## Sprint
**F234S — LIVE SERIALIZATION SAFETY SWEEP**

## Date
2026-05-11

## Verdict
✅ ALL ASSERTIONS PASSED — 30/30

---

## Bug Fixes Applied

### 1. Cycle Detection in `_safe_dataclass_to_dict` (CRITICAL)
**Problem**: `_safe_dataclass_to_dict` recursed into dataclass fields without cycle detection, causing `RecursionError` on self-referential dataclass instances.

**Fix**: Added `id(obj)`-based `_seen` set to detect cycles:
```python
obj_id = id(obj)
if obj_id in _seen:
    return f"<circular: {obj.__class__.__name__}>"
_seen.add(obj_id)
```

**Assertion**: 2 (self-reference without RecursionError) ✅

### 2. Enum Values Not Serialized (HIGH)
**Problem**: `class Color(str, Enum)` matches `is_dataclass()` — check order was wrong, Enum objects returned as-is instead of `.value`.

**Fix**: Check `isinstance(obj, Enum)` BEFORE `is_dataclass()` check:
```python
if not dataclasses.is_dataclass(obj):
    if isinstance(obj, Enum):
        return obj.value
```

**Assertion**: 3 (Enum values) ✅

### 3. Dict-Level Circular References in `json.dumps` (CRITICAL)
**Problem**: `json.dumps(default=str)` does NOT handle circular references in dicts — raises `ValueError: Circular reference detected`.

**Fix**: Added `_make_serializable()` pre-processing to replace dict/list cycles before JSON encoding:
```python
def safe_to_json(obj, indent=2) -> str:
    d = _safe_dataclass_to_dict(obj)
    d = _make_serializable(d)  # replaces cycles
    return json.dumps(d, indent=indent)
```

**Assertion**: 7 (self-referential live_kpi), 8 (nested acquisition_report) ✅

---

## Assertions Verified

| # | Assertion | Status |
|---|-----------|--------|
| 1 | No direct dataclasses.asdict() in measurement files | ✅ PASS |
| 2 | _safe_dataclass_to_dict handles self-reference | ✅ PASS |
| 3 | _safe_dataclass_to_dict handles Enum values | ✅ PASS |
| 4 | _safe_dataclass_to_dict handles pathlib.Path | ✅ PASS |
| 5 | _safe_dataclass_to_dict handles tuples/lists/dicts | ✅ PASS |
| 6 | _safe_dataclass_to_dict handles msgspec.Struct-like objects | ✅ PASS |
| 7 | LiveMeasurementResult.to_json() with self-ref live_kpi | ✅ PASS |
| 8 | LiveMeasurementResult.to_json() with nested acquisition_report | ✅ PASS |
| 9 | Output JSON deterministic for tests | ✅ PASS |
| 10 | No live network | ✅ PASS (no network calls in probe) |
| 11 | No MLX/model load | ✅ PASS (no MLX in probe) |
| 12 | No browser/stealth | ✅ PASS (no browser/stealth in probe) |
| 13 | No acquisition truth semantics changed | ✅ PASS |

---

## Files Changed

### utils/serialization.py (FIXED)
- Added `_make_serializable()` function for dict/list cycle detection
- Fixed `_safe_dataclass_to_dict()`: added `id()`-based cycle guard, fixed Enum check order
- Fixed `safe_to_json()`: pre-processes through `_make_serializable()` before `json.dumps()`

### No other files modified (secondary files not touched)

---

## Probes

- `probe_f234s_serialization_safety/test_serialiation_safety.py` — 30 assertions, all pass
- `tests/probe_f234s_serialization_safety/test_serialiation_safety.py` — same probe (copy for pytest discovery)

---

## Abort Conditions Check

| Condition | Status |
|-----------|--------|
| Editing F234B-owned source-family/report files | ❌ NOT DONE (compliant) |
| Live network | ❌ NOT DONE (compliant) |
| Model/MLX load | ❌ NOT DONE (compliant) |
| Browser/stealth | ❌ NOT DONE (compliant) |
| Changing acquisition semantics | ❌ NOT DONE (compliant) |

---

## Key Invariants

| Invariant | Test |
|-----------|------|
| SelfRef dataclass with parent/child cycle → no RecursionError | test_self_referential_dataclass ✅ |
| Color Enum value serializes to "blue" not `Color.BLUE` | test_nested_dataclass_with_enum ✅ |
| live_kpi with self-ref dict → to_json() succeeds | test_live_measurement_self_referential_live_kpi ✅ |
| acquisition_report with nested self-ref → to_json() succeeds | test_live_measurement_nested_dataclass_like_acquisition_report ✅ |
| BenchmarkResult → JSON deterministic | test_benchmark_result_serialization ✅ |