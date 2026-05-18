# PYTEST_COLLECTION_REMAINING_ERRORS_AUDIT

**Date:** 2026-05-18
**Context:** P0 stabilization — compileall for core packages passes, collect-only still reports 10 ERROR modules
**Command:** `uv run --python 3.14 python -m pytest --collect-only -q`

---

## Summary

10 test modules fail collection with `ModuleNotFoundError`. No new collection errors introduced since P0.

| # | Test File | Error | Root Cause | Classification |
|---|-----------|-------|------------|----------------|
| 1 | `test_autonomous_orchestrator.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 2 | `test_e2e_pipeline.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 3 | `test_f223d_corroboration_scorer/test_corroboration.py` | `No module named 'runtime.evidence_corroboration'` | relative import `runtime.evidence_corroboration` (no `hledac.` prefix) in test | **#5 package** |
| 4 | `test_foca_integration.py` | `No module named 'mlx'` | production code eager-imports mlx without skip | **#3 lazy** |
| 5 | `test_sprint43.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 6 | `test_sprint44.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 7 | `test_sprint66/test_source_family_canonicalization.py` | `No module named 'runtime.acquisition_strategy'` | relative import + production eager import chain | **#5 package + #3 lazy** |
| 8 | `test_sprint79c/test_sprint79c_optimizations.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 9 | `test_sprint7a.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |
| 10 | `test_sprint7g.py` | `No module named 'tools.registry'` | production eager import chain | **#3 lazy** |

---

## Detailed Analysis

### Category Breakdown

#### Category 3: Production module has eager import → should be lazy (6 modules)

**Root module:** `tool_registry.py` (root-level, NOT `hledac.universal.tool_registry`)

```
hledac/universal/tool_registry.py:13
  from tools.registry import (
      SourceReputation,
      ToolRegistry,
      ...
  )
```

`tools.registry` is a separate top-level package under `Hledac/hledac/`:
```
Hledac/hledac/tools/registry.py   ← exists, but not on sys.path
```

The `tools/` directory at `Hledac/hledac/tools/` is NOT accessible as a Python package from within `hledac/universal/` because:
1. `sys.path[0]` is the `universal/` directory (empty string in CLI invocation)
2. The `hledac` parent package is not on `sys.path` when running `pytest` from `universal/`
3. `tools/` is NOT a submodule of `hledac.universal`

**Affecting tests:** `test_autonomous_orchestrator.py`, `test_e2e_pipeline.py`, `test_sprint43.py`, `test_sprint44.py`, `test_sprint79c/test_sprint79c_optimizations.py`, `test_sprint7a.py`, `test_sprint7g.py`

**Fix direction:** Convert `tool_registry.py` imports to lazy (defer `from tools.registry import ...` until first use inside functions/methods, not at module scope).

---

#### Category 5: Legacy test uses wrong package context (2 modules)

**`test_f223d_corroboration_scorer/test_corroboration.py`**
```python
from runtime.evidence_corroboration import (
    score_indicators_by_corroboration,
    ...
)
```
Uses bare `runtime.evidence_corroboration` instead of `hledac.universal.runtime.evidence_corroboration`. When run via `uv run python -m pytest`, the `runtime` package is NOT on `sys.path` — only `hledac.universal` is.

Note: The module `runtime/evidence_corroboration.py` exists and CAN be imported as `runtime.evidence_corroboration` when cwd is `hledac/universal/` and `sys.path[0]==''`. But pytest is not running from `hledac/universal/` — it's running the `hledac.universal` package as root, so the bare import fails.

**`test_sprint66/test_source_family_canonicalization.py`**
```python
from runtime.acquisition_strategy import (
    build_acquisition_plan,
    ...
)
```
Same issue — bare `runtime.` prefix. Additionally, `runtime/acquisition_strategy.py` has its own eager import chain (`from hledac.universal.runtime.source_finding_bridge import ...`) that fails because `hledac` is not importable from within the `runtime/` package.

---

#### Category 3+4: Production eager mlx import, no skip decorator (1 module)

**`test_foca_integration.py`**
```python
import mlx.core as mx  # eager import at module level
```
No `pytest.importorskip("mlx")` or `@pytest.mark.skipif`. `mlx` is an optional `[ml]` extra dependency, not a core dependency. This test should either:
1. Use `pytest.importorskip("mlx")` guard at import time, OR
2. Move the mlx-dependent code behind a lazy import

---

#### Category 5+3: Hybrid — wrong package context + production eager import (1 module)

**`test_sprint66/test_source_family_canonicalization.py`**
Both the test's bare `runtime.` import AND `runtime/acquisition_strategy.py`'s own `from hledac.universal.runtime.source_finding_bridge import ...` eager chain.

---

## Dependency Classification

| Dependency | Status | Required Action |
|------------|--------|-----------------|
| `mlx` | **Optional** (`[ml]` extra, Apple Silicon only) | Tests MUST use `pytest.importorskip("mlx")` |
| `lmdb` | **Core dependency** — in pyproject.toml core deps, installed in `.venv` | Should NOT be skipped |
| `msgspec` | **Core dependency** — in pyproject.toml core deps, installed | Should NOT be skipped |
| `ahocorasick` | **Core dependency** — in pyproject.toml core deps | Should NOT be skipped |

**Verification:**
```bash
$ uv run --python 3.14 python -c "import lmdb; print('lmdb:', lmdb.__version__)"
lmdb: 1.4.1

$ uv run --python 3.14 python -c "import msgspec; print('msgspec:', msgspec.__version__)"
msgspec: 0.20.0

$ uv run --python 3.14 python -c "import mlx; print('mlx:', mlx.__version__)"
ModuleNotFoundError: No module named 'mlx'   ← NOT installed (optional extra)
```

---

## Import Chain Analysis

### tools.registry chain
```
hledac/universal/tool_registry.py:13
  from tools.registry import SourceReputation, ToolRegistry, ...
         ↑
hledac/hledac/tools/registry.py   ← EXISTS at Hledac/hledac level
  (not accessible from hledac/universal/ via hledac.tools.registry)
```

The `tools/` directory under `Hledac/hledac/` is a separate package (`hledac.hledac.tools`?), NOT the same as the `hledac.universal.tools/` directory.

### runtime.evidence_corroboration chain
```
tests/test_f223d_corroboration_scorer/test_corroboration.py:20
  from runtime.evidence_corroboration import ...
         ↑
hledac/universal/runtime/evidence_corroboration.py   ← EXISTS as relative
  (only importable as hledac.universal.runtime.evidence_corroboration)
```

### runtime.acquisition_strategy chain
```
tests/test_sprint66/test_source_family_canonicalization.py:18
  from runtime.acquisition_strategy import build_acquisition_plan
         ↑
hledac/universal/runtime/acquisition_strategy.py:56
  from hledac.universal.runtime.source_finding_bridge import ...
         ↑
         hledac package NOT on sys.path
```

---

## Recommended Actions (NOT implemented — audit only)

| Priority | Test File | Action |
|----------|-----------|--------|
| P1 | `test_foca_integration.py` | Add `mlx = pytest.importorskip("mlx")` guard at module level OR mark `@pytest.mark.skipif(not _has_mlx, reason="requires mlx")` |
| P1 | All `tools.registry` failures | Convert `tool_registry.py` module-scope `from tools.registry import ...` to lazy imports inside functions |
| P2 | `test_f223d_corroboration_scorer/test_corroboration.py` | Change `from runtime.evidence_corroboration` → `from hledac.universal.runtime.evidence_corroboration` |
| P2 | `test_sprint66/test_source_family_canonicalization.py` | Fix bare `runtime.` import + defer `runtime/acquisition_strategy.py` eager `hledac.universal` chain to lazy |
| P3 | `test_sprint79c`, `test_sprint43`, `test_sprint44`, `test_sprint7a`, `test_sprint7g` | Same fix as P1 — deferred after `tool_registry.py` lazy conversion |

---

## Files to Modify (for reference only — NOT modified in this audit)

| File | Line | Current | Should Be |
|------|------|---------|-----------|
| `tool_registry.py` | 13-25 | `from tools.registry import (...)` | Lazy import inside functions |
| `test_foca_integration.py` | ~1 | `import mlx.core as mx` | `mlx = pytest.importorskip("mlx")` |
| `test_f223d_corroboration_scorer/test_corroboration.py` | 20 | `from runtime.evidence_corroboration import` | `from hledac.universal.runtime.evidence_corroboration import` |
| `test_sprint66/test_source_family_canonicalization.py` | 18 | `from runtime.acquisition_strategy import` | `from hledac.universal.runtime.acquisition_strategy import` |

---

## Verification Command

```bash
uv run --python 3.14 python -m pytest --collect-only -q 2>&1 | grep "^ERROR:" | wc -l
# Expected: 0 (after fixes)
```