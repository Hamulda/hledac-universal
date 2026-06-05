# HYPOTHESIS_TIER4_EXTRACTION.md

**Sprint:** F262OBS-Tier4
**Datum:** 2026-06-02
**Scope:** Tier 4 extraction — `SourceHint` + `HypothesisPack` (711 LOC monolith slice)
**Status:** ✅ COMPLETE — 0 regressions

---

## TL;DR

Two pure-DTO classes lifted verbatim from the 4 460 LOC `brain/hypothesis_engine.py`
into a focused submodule of the `brain/hypothesis/` package. Byte-for-byte
preserved. `HypothesisEngine` orchestrator (3 753 LOC) intentionally
**deferred** to F263OBS per the prompt scope.

**Correction vs. initial estimate:** the F262OBS prompt stated ~713 LOC; the
actual extraction was 711 LOC. My /zoom-out sub-estimate of "150 LOC" was
incorrect — `HypothesisPack` carries 17 methods (3 `cached_property` + 1
`property` + 13 plain methods), not a thin DTO shell.

---

## 1. Before/After LOC

| File | Before (LOC) | After (LOC) | Delta | Notes |
|------|--------------|-------------|-------|-------|
| `brain/hypothesis_engine.py` | 4 460 | 3 753 | **-707** | Pack body 711 LOC + headers |
| `brain/hypothesis/__init__.py` | 102 | 109 | +7 | Nové exporty + aktualizovaný docstring |
| `brain/hypothesis/packs.py` | 0 | 744 | **+744** | Nový soubor — DTOs + 17 metod |
| `tests/probe_packs_extraction.py` | 0 | 170 | +170 | Nový probe — 11 testů |
| **Net change** | — | — | **+207** | Včetně testů a dokumentace |

`hypothesis_engine.py`: **4 460 → 3 753 LOC** = **-707 LOC / -15.8 %** monolith reduction.

**Combined Tier 1+2+3+4 monolith reduction:**
`hypothesis_engine.py`: 5 373 → 3 753 LOC = **-1 620 LOC / -30.2 %** celkem.

---

## 2. Extraction Targets

### 2.1 `SourceHint` (5 LOC)

- **Origin lines:** 620–625 in `hypothesis_engine.py`
- **New home:** `brain/hypothesis/packs.py`
- **API surface:** 3 fields
  - `source: str`
  - `quality: float` (0-1)
  - `hint_type: str = "general"`
- Pure DTO, duck-typed (no inheritance), used as `.source`/`.quality`/`.hint_type`
  by `HypothesisPack` methods that probe `hasattr(hint, "source")` for safety.

### 2.2 `HypothesisPack` (706 LOC)

- **Origin lines:** 628–1 334 in `hypothesis_engine.py`
- **New home:** `brain/hypothesis/packs.py`
- **API surface:** 17 members
  - 5 fields: `hypotheses`, `suggested_queries`, `ioc_follow_ups`, `source_hints`, `provenance`
  - 3 `@functools.cached_property`:
    `signal_quality`, `confidence_note`, `what_matters_first`
  - 1 `@property`:
    `operator_shortlist`
  - 13 plain methods:
    `is_empty`, `summary`, `top_queries`, `pivot_trail`,
    `next_best_actions`, `why_best_first`, `discarded_as_redundant`,
    `action_confidence`, `track_recommendation`, `best_track`,
    `investigation_tracks`, `best_first_path`, `actionable_shortlist`
- **Constructor signature:** `(hypotheses=[] , suggested_queries=[], ioc_follow_ups=[],
  source_hints=[], provenance="heuristic")` — all `default_factory=list`
  except the str default.

### 2.3 NOT extracted (per prompt scope)

| Class / Function | LOC | Why deferred |
|------------------|-----|--------------|
| `Hypothesis` | ~200 | Carries unique `add_test_result`, `_ds_engine` — intentional home in `hypothesis_engine.py` |
| `HypothesisEngine` (orchestrator) | 3 126 | Largest piece — needs dedicated F263OBS sprint with refactor budget |
| `explain_with_mlx` (module-level) | 59 | MLX-LM helper, kept adjacent to MLX glue |

---

## 3. Design Decisions

### 3.1 Why plain `@dataclass` (not `@dataclass(slots=True, frozen=True)`)

`HypothesisPack` fields are mutable `list[dict[str, Any]]` / `list[Any]`
containers populated incrementally by builders
(`build_hypothesis_pack()`, `_model_assisted_hypothesis_pack()` in
`HypothesisEngine`). A `frozen=True` dataclass would forbid
`self.hypotheses.append(...)` in those builders and force a redesign
of the engine's builder API — out of scope for Tier 4.

`slots=True` is also rejected because:
- It conflicts with the `cached_property` decorators (slots have stricter
  storage semantics and a `cached_property` with a private cache attr
  would need extra plumbing)
- The M1 RAM benefit (~30 %) is outweighed by the cached_property
  compatibility risk
- Plain `@dataclass` is what the original was — preservation > micro-opt

Plain `@dataclass` is the only correct, byte-for-byte preserving choice.

### 3.2 Why zero engine coupling

Verified by `awk` over the HypothesisPack body (L628-1334):

```
grep -E "hypothesis_engine|mlx|Metal|cache_limit|HypothesisEngine" → 0 matches
grep -E "^import |^from " → 0 matches (no inline imports)
```

This makes the extraction safe:
- `HypothesisPack` can be re-imported by any caller (no engine dependency)
- The shim in `hypothesis_engine.py` is one-way (engine re-exports, doesn't depend)
- Forward import `from brain.hypothesis.packs import HypothesisPack` works
  without any engine-side import

### 3.3 Backward-compat shim pattern

In `brain/hypothesis_engine.py` (replaces old L619-1334, ~715 lines):

```python
# =============================================================================
# SourceHint + HypothesisPack (extracted to brain.hypothesis.packs — C4 Tier-4)
# =============================================================================
from brain.hypothesis.packs import (  # noqa: E402,F401
    SourceHint,
    HypothesisPack,
)
```

Identity check: `Old is New` (the legacy import path returns the exact
same class object, not a copy). Confirmed by probe tests
`test_legacy_*_is_same_object`.

### 3.4 `__init__.py` export inventory

Added to `brain/hypothesis/__init__.py`:
- `SourceHint` (from `.packs`)
- `HypothesisPack` (from `.packs`)

Updated docstring to reflect **current** extraction state (Tier-1+2+3+4)
and document that `HypothesisEngine` orchestrator is the only remaining
extraction target (F263OBS).

---

## 4. Regression Matrix

All tests were run with `uv run pytest` (the project's `uv` env).

| Test suite | Count | Result | Notes |
|------------|------:|--------|-------|
| `tests/probe_f11_triad_connection.py` | 14 | ✅ PASS | F11 ghost invariants |
| `tests/probe_f26x1_deprecated_shim.py` | 8 passed, 1 skipped | ✅ PASS | Skip pre-existing |
| `tests/probe_hypothesis_types_extraction.py` | 12 | ✅ PASS | Tier-1+2 regression |
| `tests/probe_source_type_centralization.py` | 19 | ✅ PASS | Source-type centralization invariant |
| `tests/probe_adversarial_extraction.py` | 6 | ✅ PASS | Tier-3 regression |
| `tests/probe_explainer_extraction.py` | 4 | ✅ PASS | Tier-3 regression |
| `tests/probe_packs_extraction.py` (NEW) | 11 | ✅ PASS | Tier-4 verification |
| **TOTAL** | **~74** | **✅ 0 regressions** | 1 pre-existing skip unrelated |

F11 sublanes (`probe_f11a..d`, `probe_f1100a..e`) lack `__init__.py` so
pytest cannot discover them as packages — this is a pre-existing setup
issue, not a regression from extraction. Only `probe_f11_triad_connection.py`
runs in this category.

---

## 5. Probe Test Coverage

### 5.1 `tests/probe_packs_extraction.py` (11 tests)

| Test | What it verifies |
|------|------------------|
| `test_legacy_source_hint_import_works` | `from brain.hypothesis_engine import SourceHint` still works |
| `test_legacy_hypothesis_pack_import_works` | `from brain.hypothesis_engine import HypothesisPack` still works |
| `test_legacy_source_hint_is_same_object` | Identity check (legacy == new) |
| `test_legacy_hypothesis_pack_is_same_object` | Identity check (legacy == new) |
| `test_package_exports_packs` | `from brain.hypothesis import SourceHint, HypothesisPack` works + `__all__` membership |
| `test_source_hint_construction` | `SourceHint(source, quality, hint_type="general")` default preserved |
| `test_empty_hypothesis_pack` | Empty pack: `is_empty`, `signal_quality="weak"`, `summary="empty"`, `best_first_path=None` |
| `test_rich_hypothesis_pack_signal_quality` | 3+2+1 items → `signal_quality="strong"`, `confidence_note="moderate pack"`, `what_matters_first` starts with `"Pivot on IOC"` |
| `test_best_first_path_prefers_ioc` | IOC pivot beats broad query (priority 0.7 IOC vs 0.9 query) |
| `test_actionable_shortlist_respects_max` | `actionable_shortlist(max_items=2)` returns 2 highest-priority items |
| `test_source_hint_used_in_action_confidence` | Source hint quality boosts confidence (0.4 → 0.48 for quality=0.9) |

---

## 6. Architectural Hotspots (post-Tier-4)

```
brain/hypothesis/                       package __init__ (109 LOC)
├── __init__.py                         re-exports all submodules
├── _types.py                           enums + DTOs + Protocol (387 LOC)
├── adversarial.py                      AdversarialVerifier (895 LOC)  ← Tier-3
├── explainer.py                        SimpleNodeAblationExplainer (113 LOC)  ← Tier-3
└── packs.py                            SourceHint + HypothesisPack (744 LOC)  ← Tier-4 NEW
```

Still inside `brain/hypothesis_engine.py` (3 753 LOC):
- Enums + DTOs (kept for backward compat — `_types.py` is the canonical home)
- `Hypothesis` class (intentional — has engine-specific methods)
- `HypothesisEngine` orchestrator (3 126 LOC, dedicated F263OBS sprint planned)
- `explain_with_mlx` helper (kept adjacent to MLX glue)

---

## 7. Combined Tier 1+2+3+4 Reduction

| Sprint | Scope | LOC Removed | Cumulative |
|--------|-------|------------:|-----------:|
| Tier 1+2 (F262OBS) | `_types.py` (enums + DTOs) | 387 | 387 |
| Tier 3 (F262OBS-Tier3) | `adversarial.py` + `explainer.py` | 913 | 1 300 |
| **Tier 4 (F262OBS-Tier4)** | **`packs.py`** | **707** | **2 007** |
| F263OBS (planned) | `HypothesisEngine` orchestrator | ~3 126 | ~5 133 |

**Current state:** `hypothesis_engine.py` is 3 753 LOC (down from 5 373).
After F263OBS, the monolith is reduced to ~600 LOC (just `Hypothesis` class
+ `explain_with_mlx` + `HypothesisEngine` body without extracted parts).

---

## 8. Next Steps (deferred work)

| Item | LOC | Proposed sprint |
|------|-----|-----------------|
| Extract `HypothesisEngine` orchestrator | 3 126 | F263OBS — needs refactor budget, propose splitting into `_causal.py`, `_pivot.py`, `_advisory.py` |
| Move `explain_with_mlx` to `brain/hypothesis/explainer.py` | 59 | Trivial, can be bundled with explainer cleanup |

---

## 9. Files Touched

```
M  brain/hypothesis_engine.py                              -707 LOC (SourceHint + HypothesisPack body)
M  brain/hypothesis/__init__.py                            +7 LOC (exports + docstring)
A  brain/hypothesis/packs.py                               +744 LOC (NEW)
A  tests/probe_packs_extraction.py                         +170 LOC (NEW, 11 tests)
```

**2 modified + 2 created. Net monolith reduction: -707 LOC (-15.8 %).**

---

*Generated 2026-06-02 as part of C4 sprint refactoring (F262OBS-Tier4).*
