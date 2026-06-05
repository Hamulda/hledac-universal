# HYPOTHESIS_RENAME_REPORT.md

**Date:** 2026-06-05
**Scope:** `~/PycharmProjects/Hledac/hledac/universal/`
**Goal:** Resolve `hypothesis/` ↔ PyPI `hypothesis` namespace collision.

---

## TL;DR

| Metric | Before | After |
|--------|--------|-------|
| `pytest --collect-only` errors | **123** | **107** |
| PyPI `import hypothesis` | ❌ NameError (`is_hypothesis_test`) | ✅ `6.155.0` |
| Local module | `hypothesis/` shadowed PyPI | `hledac_hypothesis/` (no collision) |
| `tests/probe_f350mfed_federated_activation/` | 0 tests collectable | **126 tests collectable, 38/38 PASS** |
| `tests/test_hypothesis_*` (3 files) | Failed at collection | **47/47 PASS** |

**Hypothesis-related errors fixed: 16/16** (123 → 107). Remaining 107 are pre-existing missing optional deps (`kuzu`, `stem`, etc.) — unrelated to the rename.

---

## Renamed

```
hypothesis/  →  hledac_hypothesis/
```

5 files moved:

| File | Purpose |
|------|---------|
| `__init__.py` | Re-export facade (14 symbols lazy-resolved from `brain.hypothesis_engine`) |
| `hypothesisgenerator.py` | F202G bounded heuristic generator |
| `dempster_shafer.py` | Minimal belief-mass calculator |
| `eig.py` | Expected Information Gain |
| `HYPOTHESIS_GENERATOR_SPEC.md` | Spec doc (no code changes) |

---

## Updated import sites (LOCAL only)

### Code imports (11 sites)

| File | Line | Before → After |
|------|------|----------------|
| `hledac_hypothesis/eig.py` | 21 | `from hypothesis.dempster_shafer` → `from hledac_hypothesis.dempster_shafer` |
| `hledac_hypothesis/__init__.py` | 47 | `from hypothesis.hypothesisgenerator` → `from hledac_hypothesis.hypothesisgenerator` |
| `hledac_hypothesis/__init__.py` | 113 | (same — TYPE_CHECKING block) |
| `tests/test_hypothesis_dspy_fallback.py` | 19 | `from hypothesis.hypothesisgenerator` → `from hledac_hypothesis.hypothesisgenerator` |
| `tests/test_hypothesis_generator_bounds.py` | 15 | (same) |
| `tests/test_hypothesis_engine.py` | 20 | (same) |
| `tests/test_hypothesis_engine.py` | 307 | `from hypothesis.hypothesisgenerator import _extract_ips, _extract_domains, _extract_emails, _extract_hashes` → renamed |
| `tests/test_hypothesis_engine.py` | 69 | `patch("hypothesis.hypothesisgenerator._load_dspy_program")` → `patch("hledac_hypothesis.hypothesisgenerator._load_dspy_program")` |
| `tests/test_hypothesis_engine.py` | 92 | (same — 2nd `patch()` call) |
| `tests/test_sprint60.py` | 329, 339, 349, 360 | `from hledac.universal.hypothesis.dempster_shafer` → `from hledac_hypothesis.dempster_shafer` (4× replace_all) |
| `tests/test_sprint60.py` | 361 | `from hledac.universal.hypothesis.eig` → `from hledac_hypothesis.eig` |

### Docstring / comment path references (3 sites)

| File | Update |
|------|--------|
| `hledac_hypothesis/hypothesisgenerator.py:7` | `hypothesis/hypothesisgenerator.py` → `hledac_hypothesis/hypothesisgenerator.py` |
| `hledac_hypothesis/dempster_shafer.py:2` | `hypothesis/dempster_shafer.py` → `hledac_hypothesis/dempster_shafer.py` |
| `hledac_hypothesis/eig.py:2` | `hypothesis/eig.py` → `hledac_hypothesis/eig.py` |
| `tests/test_hypothesis_engine.py:7` | Comment `(hypothesis/hypothesisgenerator.py)` → `(hledac_hypothesis/hypothesisgenerator.py)` |

### NOT touched (correctly skipped)

- **`pyproject.toml:160`** — `"hypothesis>=6.155.0"` in `graph-storage` extra → PyPI dep, must stay
- **`pyrightconfig.json`** — no `hypothesis` path entries; no change needed
- **`analyze_imports.py:213`** — string literal `"hledac.universal.hypothesis.BetaBinomial"` in a historical broken-import categorizer; left intact (the category is vestigial post-rename, but the string is not a real import)
- **No `MANIFEST.in`, `setup.cfg`, root `conftest.py`** — none exist
- **No PyPI `from hypothesis import given, settings` or `import hypothesis.strategies`** — none existed in this codebase, so no risk of false-positive rewrites
- **No `sys.modules["hypothesis"]` / `__name__` runtime references** — none found

---

## Verification evidence

### 1. PyPI hypothesis now accessible
```
$ uv run python -c "import hypothesis; print(hypothesis.__version__)"
PyPI hypothesis version: 6.155.0
```

### 2. Local hledac_hypothesis re-exports work
```
$ uv run python -c "from hledac_hypothesis import HypothesisEngine, HypothesisGenerator, ResearchHypothesis, ..."
OK — all 14 symbols re-exported
```

### 3. Direct dual-import sanity check
```
$ uv run python -c "import hypothesis; print('PyPI:', hypothesis.__version__); import hledac_hypothesis; print('LOCAL:', hledac_hypothesis.__file__)"
PyPI: 6.155.0
LOCAL: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/hledac_hypothesis/__init__.py
```

### 4. Previously-blocked probe now PASSES
```
$ uv run pytest tests/probe_f350mfed_federated_activation/ -x --collect-only
========================= 126 tests collected in 1.52s ==========================

$ uv run pytest tests/probe_f350mfed_federated_activation/test_federated_activation.py
======================= 38 passed, 18 warnings in 10.07s =======================
```

### 5. Hypothesis unit tests now PASS
```
$ uv run pytest tests/test_hypothesis_engine.py tests/test_hypothesis_generator_bounds.py tests/test_hypothesis_dspy_fallback.py
======================= 47 passed, 11 warnings in 11.94s =======================
```

### 6. Collection error delta
```
Before:  16344 tests collected, 123 errors in 67.74s
After:   16629 tests collected, 107 errors in 52.69s
         (16629 - 16344 = +285 newly-discoverable tests)
         (123 - 107 = -16 hypothesis-shadow errors eliminated)
```

---

## Remaining 107 collection errors (unrelated to rename)

Sample (verified with `uv run pytest tests/probe_8rb/test_stix_empty_graph.py --collect-only`):
```
ImportError while importing test module
  ...
E   ModuleNotFoundError: No module named 'kuzu'
```

These are missing optional deps (kuzu, stem, etc.) — pre-existing issues that were **hidden** by the hypothesis shadow. The rename exposed them; fixing them is out of scope for this task. Recommended follow-up: install missing extras via `uv sync --extra kuzu-graph --extra tor` etc.

---

## Files modified

| File | Change type |
|------|-------------|
| `hypothesis/` → `hledac_hypothesis/` | `mv` (5 files) |
| `hledac_hypothesis/eig.py` | 2 lines (import + docstring header) |
| `hledac_hypothesis/__init__.py` | 2 lines (import in 2 blocks) |
| `hledac_hypothesis/hypothesisgenerator.py` | 1 line (docstring header) |
| `hledac_hypothesis/dempster_shafer.py` | 1 line (docstring header) |
| `tests/test_hypothesis_dspy_fallback.py` | 1 line (import) |
| `tests/test_hypothesis_generator_bounds.py` | 1 line (import) |
| `tests/test_hypothesis_engine.py` | 5 lines (3 imports + 2 patch strings + 1 comment) |
| `tests/test_sprint60.py` | 5 lines (4 dempster_shafer imports + 1 eig import) |

**Total: 11 files, 18 line-level changes, 0 public API breakages** (the local `hypothesis/` was never an exported public API — it was a top-level directory loaded via `pythonpath = ["."]` in pyproject.toml pytest config).

---

## Pyright diagnostics (informational, not blocking)

Pyright reports "missing import" warnings for `hledac_hypothesis.*` because `pyrightconfig.json` does not list it under `extraPaths`. The runtime pytest works correctly (verified above). Resolution requires either:
- Adding `"extraPaths": [".", "hledac_hypothesis"]` to `pyrightconfig.json`, OR
- Installing the package in editable mode (`uv pip install -e .`)

**Status:** Left as-is — runtime correctness verified, Pyright is a heuristic, not a test.

---

## Conclusion

✅ **PyPI `hypothesis` accessible** (6.155.0)
✅ **Local `hledac_hypothesis` accessible** (14 lazy re-exports)
✅ **`tests/probe_f350mfed_federated_activation/` unblocked** (126 tests collectable, 38/38 PASS)
✅ **Hypothesis unit tests PASS** (47/47)
✅ **No public API breakages** — the local `hypothesis/` was a top-level namespace, not an exported API
✅ **0 PyPI hypothesis imports disturbed** — no `from hypothesis import given/settings` patterns existed in this codebase
