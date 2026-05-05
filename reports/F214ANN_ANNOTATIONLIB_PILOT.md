# F214ANN: annotationlib Tool Registry Pilot

**Date:** 2026-05-06
**Status:** NO_PATCH
**Based on:** `reports/F214R_ANNOTATIONLIB_INTROSPECTION.md`

---

## Verdict: NO_PATCH — No Annotation Introspection Target

F214R audit found zero runtime annotation introspection in production code paths. The annotationlib pilot has **no measurable target** in tool/plugin/CLI registry paths.

---

## Evidence

### F214R Key Findings (authoritative)

| Finding | Location | Evidence |
|---------|----------|----------|
| Dead import | `tool_registry.py:26` | `get_type_hints` imported, **never called** |
| No annotation reads | `get_tool_cards_for_hermes()` | Uses `model_json_schema()` on Pydantic BaseModel, not annotation introspection |
| Zero annotation introspection | All production paths | grep confirms: only test/analysis code uses `__annotations__` or `get_type_hints` |
| annotationlib unavailable | Python 3.13 | `ModuleNotFoundError: No module named 'annotationlib'` — ships in Python 3.14 |

### Tool Card Generation Path

```python
# tool_registry.py:253-262 — to_tool_card()
def to_tool_card(self) -> dict[str, Any]:
    return {
        "name": self.name,
        "description": self.description,
        "args_schema": self.args_schema.model_json_schema(),  # Pydantic introspection, not annotationlib
        "returns_schema": self.returns_schema.model_json_schema(),
        "cost_hints": self.cost_model.to_hermes_hint(),
        "rate_limits": self.rate_limits.to_hermes_hint(),
    }
```

**No annotation introspection** — uses Pydantic's `model_json_schema()` which is Pydantic's own machinery, not Hledac code.

### Registry Discovery Path

```python
# tool_registry.py:624-630 — get_tool_cards_for_hermes()
def get_tool_cards_for_hermes(self) -> list[dict[str, Any]]:
    return [tool.to_tool_card() for tool in self._tools.values()]
```

No `get_type_hints`, no `get_annotations`, no `__annotations__` access.

---

## Why NO_PATCH

1. **No target**: F214R found zero annotation introspection in production paths. Applying annotationlib pattern to zero calls provides zero benefit.

2. **Dead import cleanup ≠ annotationlib usage**: The unused `get_type_hints` import is just dead code, not a candidate for annotationlib refactoring.

3. **Pattern mismatch**: annotationlib's value is in `Format.FORWARDREF` for forward reference resolution and `Format.VALUE` for deferred annotation evaluation. The tool registry uses concrete Pydantic models with `model_json_schema()` — no annotation introspection, no forward refs to resolve.

4. **Performance insight**: Direct `__annotations__` access (0.06-0.07 µs/call) is 200-700x faster than `get_type_hints()` (12-25 µs/call). annotationlib (0.28-0.81 µs/call) sits in between. Neither pattern applies since the registry uses Pydantic's schema machinery, not annotation reads.

**Optional cleanup (separate from this pilot):** Remove dead `get_type_hints` import from `tool_registry.py:26`. This is hygiene, not annotationlib usage.

---

## What Was Considered

### Considered and rejected: add annotationlib to `Tool.args_schema` introspection

**Why rejected:**
- `args_schema` and `returns_schema` are Pydantic `type[BaseModel]` — introspected via `model_json_schema()`, not `__annotations__`
- No forward references to resolve — schemas are concrete models
- Zero calls in production code, so annotationlib pattern would be dead code

### Considered and rejected: clean up dead `get_type_hints` import

**Why rejected for this sprint:**
- Dead import removal is a hygiene fix, not an annotationlib pilot
- The pilot requires a **measurable benefit** from annotationlib usage
- Removing dead imports is a separate cleanup task, not this pilot's scope

---

## Validation

**Python 3.14.4 benchmark** (annotationlib available):

| Method | ToolMetadata | ReplayResult | ActivationResult |
|--------|-------------|--------------|------------------|
| `typing.get_type_hints()` | 25.13 µs/call | 14.27 µs/call | 12.34 µs/call |
| `__annotations__` direct | 0.06 µs/call | 0.07 µs/call | 0.06 µs/call |
| `annotationlib.get_annotations(VALUE)` | 0.28 µs/call | 0.28 µs/call | 0.28 µs/call |
| `annotationlib.get_annotations(FORWARDREF)` | 0.28 µs/cs | 0.29 µs/call | 0.29 µs/call |
| `annotationlib.get_annotations(STRING)` | 0.55 µs/call | 0.80 µs/call | 0.81 µs/call |

**Key insight:** `__annotations__` direct access is **200-700x faster** than `get_type_hints()`. annotationlib sits in the middle — 5-10x slower than `__annotations__` direct but 30-50x faster than `get_type_hints()`.

**annotationlib available:** ✅ Python 3.14.4 (`/Users/vojtechhamada/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14`)

```bash
# Import smoke — PASS
$ PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
IMPORT_OK

# Probe F214R with Python 3.14.4 — PASS
$ PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python3.14 tools/probe_f214r_annotationlib_introspection.py
VERDICT: NO_PATCH — ZERO production runtime annotation introspection found.

---

## Conclusion

F214ANN annotationlib pilot: **NO_PATCH**

The tool/plugin/CLI registry introspection path has zero annotation introspection calls. F214R's verdict is confirmed — there is no annotationlib target in production code paths. The dead `get_type_hints` import in `tool_registry.py:26` is cleanup, not annotationlib usage.

**If a future sprint identifies a concrete forward-reference resolution need** (e.g., TypedDict with forward refs being introspected at runtime), annotationlib pattern can be applied then with measurable benefit evidence.