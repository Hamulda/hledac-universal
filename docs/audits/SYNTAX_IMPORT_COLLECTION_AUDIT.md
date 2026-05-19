# Syntax / Import / Collection Audit

**Date:** 2026-05-18
**Scope:** `hledac/universal/` (analysis-only, no fixes)
**Python:** uv-managed, py3.14 system + py3.13 venv

---

## 1. Syntax Compile (`uv run python -m compileall -q .`)

**Result:** FAILED — 5 files with syntax/encoding errors.

| File | Error | Type | Fix Priority |
|------|-------|------|--------------|
| `benchmarks/coreml_ane_capability.py:324` | `SyntaxError: invalid syntax` — unterminated string literal | real bug | P0 |
| `probe_f231t_final_no_live_readiness/final_no_live_readiness.py:455` | `SyntaxError: unterminated string literal` | real bug | P0 |
| `utils/find_files.py:48` | `IndentationError: expected an indented block after 'if'` | real bug | P0 |
| `utils/optimize_imports.py:54` | `IndentationError: unexpected indent` | real bug | P0 |
| `.venv-py3135/...nodriver/cdp/network.py:1365` | `SyntaxError: Non-UTF-8 code starting with '\xb1'` | third-party, ignore | N/A |

**Root cause:** `benchmarks/` and `probe_f231t_final_no_live_readiness/` are test/benchmark code outside the main package. `utils/find_files.py` and `utils/optimize_imports.py` have genuine indentation bugs.

---

## 2. Import Smoke Test

All 6 key modules failed with:
```
ModuleNotFoundError: No module named 'hledac'
```

| Module | Status | Note |
|--------|--------|------|
| `hledac.universal.core.__main__` | FAIL | Not in package context (running from hledac/universal/ without -m) |
| `hledac.universal.runtime.sprint_scheduler` | FAIL | Same |
| `hledac.universal.pipeline.live_public_pipeline` | FAIL | Same |
| `hledac.universal.transport.body_limiter` | FAIL | Same |
| `hledac.universal.knowledge.duckdb_store` | FAIL | Same |
| `hledac.universal.tools.registry` | FAIL | Same |

**Diagnosis:** Not a code issue — `uv run python - <<'PY'` does not place the file in package context. Run via `uv run python -m hledac.universal.<module>` or `cd .. && uv run python -c "from hledac.universal import ..."` to get proper import path.

---

## 3. Pytest Collection (`uv run pytest --collect-only -q`)

**Result:** 2294 tests collected, 9 ERRORs during collection, 1 SKIPPED.

### 9 Files With Collection Errors

**Root cause 1 — `tool_registry.py:13` unprefixed sibling import:**
`tool_registry.py` (project root) imports `from tools.registry import` — a sibling package at the same level. When pytest runs from within `hledac/universal/`, Python's path resolution cannot find `tools` as a sibling to `hledac/universal/`. The actual `tools/registry.py` does exist (not a deleted module — it has the `ToolRegistry` class).

**Root cause 2 — missing deps blocking core modules:**

| File | Root Cause | Missing Dep |
|------|------------|-------------|
| `tests/ct_lane_closure/test_ct_lane_closure.py` | `knowledge/duckdb_store.py:118` → `msgspec` | required dep |
| `tests/r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py` | `runtime/sprint_scheduler.py:42` → `ahocorasick` | required dep |
| `tests/test_foca_integration.py` | `multimodal/vision_encoder.py:6` → `mlx.core` | M1 optional extra |
| `tests/test_sprint62a.py` | `import mlx.core as mx` | M1 optional extra |

**Previously claimed root causes that were WRONG:**
- `runtime.acquisition_strategy` — **NOT deleted.** Exists at `runtime/acquisition_strategy.py`. Error is NOT from a missing module.
- `runtime.evidence_corroboration` — **NOT deleted.** Exists at `runtime/evidence_corroboration.py`. Error is NOT from a missing module.

### Confirmed Collection Error Files

| File | Verified Root Cause | Category |
|------|---------------------|----------|
| `tests/test_autonomous_orchestrator.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_e2e_pipeline.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_sprint43.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_sprint44.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_sprint79c/test_sprint79c_optimizations.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_sprint7a.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_sprint7g.py` | `tool_registry.py:13` unprefixed `tools.registry` | real bug |
| `tests/test_f223d_corroboration_scorer/test_corroboration.py` | unconfirmed (runtime.evidence_corroboration exists) | investigation needed |
| `tests/test_sprint66/test_source_family_canonicalization.py` | unconfirmed (runtime.acquisition_strategy exists) | investigation needed |

**Also found (from full scan):**

| File | Root Cause | Category |
|------|------------|----------|
| `tests/ct_lane_closure/test_ct_lane_closure.py` | `knowledge/duckdb_store.py:118` → `msgspec` missing | missing optional extra |
| `tests/r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py` | `runtime/sprint_scheduler.py:42` → `ahocorasick` missing | missing optional extra |
| `tests/security_layer_async_io/test_security_layer.py` | same patterns | unconfirmed |
| `tests/swarm_coordinator_characterization/` (3 files) | same patterns | unconfirmed |
| `tests/test_e2e_first_finding.py` | same patterns | unconfirmed |
| `tests/test_e2e_pipeline_smoke.py` | same patterns | unconfirmed |
| `tests/test_embedding_prefix_discipline/test_embedding_task.py` | same patterns | unconfirmed |
| `tests/test_foca_integration.py` | `multimodal/vision_encoder.py:6` → `mlx.core` missing | optional extra |
| `tests/test_i2p_transport.py` | unconfirmed | unconfirmed |
| `tests/test_ipfs_canonical.py` | unconfirmed | unconfirmed |
| `tests/test_pattern_matcher.py` | unconfirmed | unconfirmed |

---

## 4. Unique ModuleNotFoundError Patterns (from pytest collection)

| Module | Missing | Category | Verified |
|--------|---------|----------|----------|
| `tools.registry` | path resolution failure | **REAL BUG** — `tool_registry.py:13` imports from `tools/` sibling; fails in pytest context | ✅ confirmed |
| `runtime.acquisition_strategy` | module EXISTS at `runtime/` | NOT missing — error is something else | ❌ incorrect |
| `runtime.evidence_corroboration` | module EXISTS at `runtime/` | NOT missing — error is something else | ❌ incorrect |
| `msgspec` | not installed in pytest env | **REAL BUG** — required by `tools/registry.py:26` | ✅ confirmed |
| `ahocorasick` | not installed in pytest env | **REAL BUG** — required by `runtime/sprint_scheduler.py` | ✅ confirmed |
| `yaml` | not installed | **REAL BUG** — required dep | ✅ confirmed |
| `mlx` / `mlx.core` | not installed (no M1) | optional extra | ✅ confirmed |
| `lmdb` | not installed | optional extra | ✅ confirmed |
| `aiohttp` | not installed | optional extra | ✅ confirmed |
| `numpy` | not installed | optional extra | ✅ confirmed |
| `anyio` | not installed | optional extra | ✅ confirmed |

---

## 5. Summary Table

| Category | Count | Notes |
|----------|-------|-------|
| Syntax errors (confirmed) | 2 | `benchmarks/coreml_ane_capability.py:324`, `probe_f231t_final_no_live_readiness/final_no_live_readiness.py:455` — unterminated string literals |
| Syntax errors (reported by compileall, unverified) | 2 | `utils/find_files.py:48`, `utils/optimize_imports.py:54` — reported by compileall |
| Collection errors — confirmed root cause | 7 | `tool_registry.py:13` unprefixed sibling import `tools.registry` |
| Collection errors — root cause unconfirmed | 2 | `test_f223d_corroboration.py`, `test_sprint66/...` — modules exist, actual error TBD |
| Missing required deps | 3 | `msgspec`, `ahocorasick`, `yaml` — block core modules |
| Missing optional deps | 5+ | `mlx`, `lmdb`, `aiohttp`, `numpy`, `anyio` |
| Tests collected successfully | 2294 | — |
| Skipped (optional dep) | 1 | `tests/test_sprint51_52.py` |

---

## 6. Suggested Next Fixes (Analysis Only)

| Priority | Action | Target |
|----------|--------|--------|
| P0 | Investigate `tool_registry.py:13` — `from tools.registry import` sibling import breaks pytest collection for 7 test files. The `tools/registry.py` exists; the import path is wrong for the project structure. | 7 failing test files |
| P0 | Investigate `test_f223d_corroboration.py` and `test_sprint66/...` — their claimed missing modules (`runtime.acquisition_strategy`, `runtime.evidence_corroboration`) exist; root cause is something else. | 2 failing test files |
| P1 | Install `msgspec`, `ahocorasick`, `yaml` in pytest env — block core modules | `duckdb_store.py`, `sprint_scheduler.py` |
| P2 | Fix unterminated string literal `benchmarks/coreml_ane_capability.py:324` | syntax compile |
| P2 | Fix unterminated string literal `probe_f231t_final_no_live_readiness/final_no_live_readiness.py:455` | syntax compile |
| P2 | Verify/fix `utils/find_files.py` and `utils/optimize_imports.py` indentation errors | syntax compile |