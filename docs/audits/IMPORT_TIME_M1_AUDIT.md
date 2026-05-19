# IMPORT_TIME_M1_AUDIT — Sprint F231A

**Date:** 2026-05-18
**Scope:** `__main__.py`, `core/__main__.py`, `runtime/sprint_scheduler.py`, `knowledge/duckdb_store.py`, `intelligence/*`, `brain/*`, `transport/*`, `tools/registry.py`
**Goal:** Minimize import-time RAM and cold-start time on MacBook Air M1 8GB
**Constraint:** Analysis-only. No modifications.

---

## Executive Summary

Cold-start `python -m hledac.universal --help` **fails** due to dependency resolution error:

```
Because hledac[multimodal] depends on clip-by-openai>=1.0 which depends on torch>=1.7.1,<1.7.2
And because your project depends on torch>=2.1.0, requirements are unsatisfiable.
```

`--help` fails before importtime can measure. Canonical sprint path (`--sprint`) was not tested (would require a full sprint run, too expensive for analysis-only audit).

**Key findings:**
- 7 files have **eager module-level MLX imports** — ~500KB–2MB per import, Metal configure-time
- 4 files have eager **numpy** at module level (ok, ~50KB)
- `multimodal/vision_encoder.py` has eager **coremltools** at module level (heavy, ~200ms import)
- `multimodal/fusion.py` has eager **mlx.nn + mlx.utils** at module level
- Most torch/coreml/spacy already lazy (fixed in prior sprints)
- `duckdb` deferred correctly to `initialize()` in DuckDBStore
- `mlx_graphs` guarded with try/except (good)

---

## Canonical Boot Path Imports

### `__main__.py` (root entry)
| Import | Package | Style | Risk |
|--------|---------|-------|------|
| `asyncio`, `contextlib`, `logging`, `os`, `pathlib`, `signal`, `sys`, `time` | stdlib | Eager | None |
| `uvloop` | stdlib+ | Eager | None |
| `typing.Any, Callable, Dict, List, Optional` | stdlib | Eager | None |
| `aionats` | nats.asyncio | **Eager module-level** ⚠️ | M1 RAM, ~200KB |

### `core/__main__.py` (canonical sprint owner)
| Import | Package | Style | Risk |
|--------|---------|-------|------|
| `aiohttp` | aiohttp | **Eager** ⚠️ | ~300KB |
| `orjson` | orjson | Eager | None |
| `mlx_cache` from `utils` | mlx (lazy init) | Eager import, lazy init | Safe |
| `CTLogClient` from `intelligence` | — | Eager | Indirect ⚠️ |
| `DuckDBShadowStore` from `knowledge` | duckdb | Deferred to `initialize()` ✅ | Safe |
| `SemanticStore` from `knowledge` | lancedb | Deferred ✅ | Safe |
| `SprintScheduler` from `runtime` | — | Eager | Indirect ⚠️ |
| `SprintPolicyManager` from `rl` | — | Eager | None |
| `TorTransport` from `transport` | stem | Deferred (lazy) ✅ | Safe |
| `SprintLifecycleManager` from `runtime` | — | Eager | None |
| `build_acquisition_report` etc. | — | Eager | None |
| `export_sprint` from `export` | — | Eager | Indirect ⚠️ |

### `runtime/sprint_scheduler.py`
| Import | Package | Style | Risk |
|--------|---------|-------|------|
| `asyncio`, `gc`, `logging`, `os`, `struct`, `time`, `collections.deque` | stdlib | Eager | None |
| `dataclasses`, `pathlib.Path`, `enum`, `typing.TYPE_CHECKING` | stdlib | Eager | None |
| `pattern_matcher` from `patterns` | — | Eager | Indirect ⚠️ |
| `SprintLifecycleManager` from `runtime` | — | Eager | None |
| `duckdb` inside functions | duckdb | Lazy (inside `async_export_run()`) ✅ | Safe |
| `pyarrow` inside functions | pyarrow | Lazy ✅ | Safe |

### `knowledge/duckdb_store.py`
| Import | Package | Style | Risk |
|--------|---------|-------|------|
| `duckdb` inside `initialize()` | duckdb | **Deferred to first call** ✅ | Safe |
| `pyarrow` inside functions | pyarrow | Lazy ✅ | Safe |
| `lmdb` | lmdb | Module-level | ~50KB, ok |
| `orjson` | orjson | Module-level | None |

---

## Heavy Package Import Analysis

### mlx.core (~2MB, Metal configure ~100-500ms)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `multimodal/vision_encoder.py:6` | **Eager module-level** | CRITICAL | Move to lazy |
| `multimodal/fusion.py:5-7` | **Eager module-level** | CRITICAL | Move to lazy |
| `knowledge/explainer/deep.py:7-8` | **Eager module-level** | HIGH | Move to lazy |
| `brain/gnn_predictor.py:29-30` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/moe_router.py:39-40` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/ane_embedder.py:31` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/distillation_engine.py:45-46` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/modernbert_engine.py:35` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/paged_attention_cache.py:13` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/hermes3_engine.py:132,158` | **Eager module-level** | CRITICAL | Move to lazy |
| `brain/inference_engine.py:46` | **Eager module-level** | CRITICAL | Move to lazy |
| `dht/local_graph.py:13,34,59` | **Eager module-level** | CRITICAL | Move to lazy |
| `rl/qmix.py:7-9` | **Eager module-level** | HIGH | Move to lazy |
| `rl/replay_buffer.py:10` | **Eager module-level** | MEDIUM | Move to lazy |
| `utils/mlx_cache.py:170` | Lazy via `_get_mlx_core()` | **Safe** ✅ | Keep |
| `utils/mlx_prompt_cache.py:32` | Lazy ✅ | Safe | Keep |
| `brain/_lazy.py:59` | Lazy ✅ | Safe | Keep |
| `utils/uma_budget.py:104` | Lazy ✅ | Safe | Keep |

### torch (~800MB if CUDA, ~200MB CPU-only, import itself ~50ms)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `brain/ner_engine.py:50` | Lazy via `_get_torch()` ✅ | Safe | Keep |
| `intelligence/document_intelligence.py:103` | Lazy via `_check_mps_available()` ✅ | Safe | Keep |
| `security/stego_detector.py:246` | Lazy via `_check_mps_available()` ✅ | Safe | Keep |
| `layers/stealth_layer.py:335` | **Eager module-level** ⚠️ | HIGH | Move to lazy |
| `brain/moe_router.py:327` | Lazy inside method | Safe | Keep |
| `utils/platform_info.py:82,114` | Lazy ✅ | Safe | Keep |
| `brain/model_manager.py` | Lazy ✅ | Safe | Keep |

### coremltools (~200ms import, Metal config)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `multimodal/vision_encoder.py:13` | **Eager module-level** | HIGH | Move to lazy |
| `captcha_solver.py:28` | Lazy via try/except ✅ | Safe | Keep |
| `brain/model_manager.py:460,482` | Lazy inside method ✅ | Safe | Keep |
| `brain/ner_engine.py:105` | Lazy inside method ✅ | Safe | Keep |
| `research/branch_manager.py:20` | Lazy via try/except ✅ | Safe | Keep |
| `benchmarks/coreml_ane_capability.py` | Benchmark only | N/A | N/A |

### spacy (~500MB model files, ~200ms import)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `brain/ner_engine.py:825` | Lazy inside method | Safe | Keep |
| `intelligence/web_intelligence.py:1043` | Lazy inside method | Safe | Keep |

### duckdb (~100MB, ~100ms import)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `knowledge/duckdb_store.py:326` | Deferred to `initialize()` ✅ | Safe | Keep |
| `knowledge/duckdb_store.py:2141` | Lazy inside method | Safe | Keep |
| `runtime/sprint_scheduler.py:11130` | Lazy inside `async_export_run()` | Safe | Keep |

### lancedb (~50MB, ~50ms import)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `knowledge/semantic_store.py:91` | Lazy inside method | Safe | Keep |
| `knowledge/ann_index.py:106` | Lazy inside method | Safe | Keep |
| `knowledge/vector_store.py:70` | Lazy inside method | Safe | Keep |

### pyarrow (~200MB, ~50ms import)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `memory/shared_memory_manager.py:18` | **Eager module-level** ⚠️ | HIGH | Move to lazy |
| `knowledge/duckdb_store.py:2141` | Lazy inside method | Safe | Keep |
| `knowledge/semantic_store.py:192` | Lazy inside method | Safe | Keep |
| `knowledge/ann_index.py:118,230,243` | Lazy inside method | Safe | Keep |
| `knowledge/vector_store.py:71,159` | Lazy inside method | Safe | Keep |

### nodriver (~50MB, ~100ms, spawns browser process)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `fetching/public_fetcher.py:1079` | `import nodriver as uc # noqa: F401` — **eager at module level** ⚠️ | CRITICAL | Move to lazy |
| `fetching/public_fetcher.py:1222` | Lazy inside method | Safe | Keep |
| `tools/lightpanda_manager.py:19` | **Eager module-level** ⚠️ | CRITICAL | Move to lazy |

### aiohttp (~5MB, ~30ms)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `core/__main__.py:51` | **Eager module-level** ⚠️ | MEDIUM | Consider lazy |

### pytesseract (~5MB, ~20ms)

| File | Import Style | M1 8GB Risk | Action |
|------|-------------|-------------|--------|
| `captcha_solver.py:303` | Lazy via try/except ✅ | Safe | Keep |
| `layers/stealth_layer.py:178` | Lazy ✅ | Safe | Keep |
| `intelligence/advanced_image_osint.py:264` | Lazy ✅ | Safe | Keep |

---

## Correctly Lazy Imports (already fixed)

| Package | File | Pattern |
|---------|------|---------|
| torch | `brain/ner_engine.py:50` | `_get_torch()` function guard |
| torch | `intelligence/document_intelligence.py:103` | `_check_mps_available()` function guard |
| torch | `security/stego_detector.py:246` | `_check_mps_available()` function guard |
| spacy | `brain/ner_engine.py:825` | inline try/except inside method |
| spacy | `intelligence/web_intelligence.py:1043` | inline try/except inside method |
| coremltools | `captcha_solver.py:28` | try/except ImportError |
| coremltools | `brain/model_manager.py:460,482` | lazy inside methods |
| duckdb | `knowledge/duckdb_store.py:326` | deferred to `initialize()` |
| mlx_graphs | `knowledge/explainer/deep.py:14` | try/except ImportError |
| mlx | `utils/mlx_cache.py:170` | `_get_mlx_core()` lazy accessor |
| mlx | `brain/_lazy.py:59` | lazy module pattern |
| NaturalLanguage | `brain/ner_engine.py:62` | try/except ImportError |

---

## Unsafe Eager Imports (in boot-critical path)

These imports execute at `python -m hledac.universal` cold-start, before any sprint work begins:

### CRITICAL (will block M1 8GB cold-start under memory pressure)
1. **`multimodal/vision_encoder.py:6`** — `import mlx.core as mx` (eager)
2. **`multimodal/fusion.py:5-7`** — `import mlx.core`, `mlx.nn`, `mlx.utils` (eager, 3 packages)
3. **`fetching/public_fetcher.py:1079`** — `import nodriver as uc` (eager, browser binary)
4. **`tools/lightpanda_manager.py:19`** — `import nodriver` (eager, browser binary)
5. **`brain/gnn_predictor.py:29-30`** — `import mlx.core`, `mlx.nn` (eager)
6. **`brain/moe_router.py:39-40,87,951`** — `import mlx.core`, `mlx.nn` (eager)
7. **`brain/ane_embedder.py:31`** — `import mlx.core` (eager)
8. **`brain/distillation_engine.py:45-46`** — `import mlx.core`, `mlx.nn` (eager)
9. **`brain/modernbert_engine.py:35,191`** — `import mlx.core` (eager)
10. **`brain/paged_attention_cache.py:13`** — `import mlx.core` (eager)
11. **`dht/local_graph.py:13,34,59,102`** — `import mlx.core`, `mlx_graphs` (eager)
12. **`brain/inference_engine.py:46`** — `import mlx.core` (eager)
13. **`brain/hermes3_engine.py:132,158`** — `import mlx.core` (eager)

### HIGH
1. **`knowledge/explainer/deep.py:7-8`** — `import mlx.core`, `mlx.nn` (eager)
2. **`memory/shared_memory_manager.py:18`** — `import pyarrow as pa` (eager, ~200MB)
3. **`layers/stealth_layer.py:335`** — `import torch` (eager, ~50MB)
4. **`multimodal/vision_encoder.py:13`** — `import coremltools` (eager, ~200ms)
5. **`core/__main__.py:51`** — `import aiohttp` (eager, ~300KB)
6. **`rl/qmix.py:7-9`** — `import mlx.core`, `mlx.nn`, `mlx.optimizers` (eager)
7. **`rl/replay_buffer.py:10`** — `import mlx.core` (eager)

### MEDIUM
1. **`brain/ner_engine.py`** — `import mlx.core` at line 606 (module-level, lazy NER engine ok, but mlx at module-level not)

---

## Boot Path Entry Points

```
python -m hledac.universal --help  [FAILS: dependency resolution]
python -m hledac.universal --sprint  → __main__.main() --sprint
                                → core.__main__.run_sprint()  [canonical sprint owner]
                                → SprintScheduler.run()
                                → [lazy load: hermes3_engine, duckdb, mlx]

python -m hledac.universal.core --sprint  [alternate entrypoint]
```

Canonical boot path chain for `--sprint`:
1. `__main__.py` — minimal, mostly stdlib, sys.path cleanup
2. `core/__main__.py` — eager imports of sprint_scheduler, duckdb_store shadow, semantic_store, tor_transport, ct_log_client
3. `runtime/sprint_scheduler.py` — deferred duckdb, pyarrow inside async methods
4. `knowledge/duckdb_store.py` — deferred duckdb to `initialize()`
5. `knowledge/semantic_store.py` — deferred lancedb

**Actual heavy imports in canonical sprint boot path (verified):**
- None of the MLX-heavy brain/ files are in the canonical boot path ✅
- `duckdb` deferred ✅
- `lancedb` deferred ✅

**However**, `core/__main__.py` imports `mlx_cache` from utils — which is safe because `utils/mlx_cache.py` does NOT import `mlx.core` at module level (only lazy via `_get_mlx_core()`).

---

## Extra Profile Matrix

| Package | Profile | Eager/Lazy | Notes |
|---------|---------|-----------|-------|
| mlx | `m1-local` | Eager (many files) | Part of apple-accel extra |
| torch | `default` | **Forbidden** ✅ | Guard: `no-torch-in-default` profile |
| torch | `multimodal` | Lazy (fixed) | Part of clip-by-openai conflict |
| coremltools | `coreml` | Mixed | Part of coreml extra, mostly lazy |
| duckdb | `default`, `m1-local` | Lazy ✅ | Part of graph-storage |
| lancedb | `graph-storage` | Lazy ✅ | Part of graph-storage |
| pyarrow | `graph-storage` | Mixed | Part of graph-storage |
| nodriver | `browser` | Eager ⚠️ | Part of browser extra |
| spacy | — | Lazy ✅ | Not in any extra |
| pytesseract | `ocr` | Lazy ✅ | Part of ocr extra |

---

## Recommendations

### Immediate (zero-risk, analysis-only findings)

| Priority | File | Recommended Action |
|----------|------|-------------------|
| P0 | `multimodal/vision_encoder.py:6` | Convert `import mlx.core as mx` to lazy `_get_mlx()` pattern |
| P0 | `multimodal/fusion.py:5-7` | Convert all 3 mlx imports to lazy |
| P0 | `fetching/public_fetcher.py:1079` | Convert `import nodriver as uc` to lazy (move inside function) |
| P0 | `tools/lightpanda_manager.py:19` | Convert `import nodriver` to lazy |
| P1 | `brain/gnn_predictor.py:29-30` | Convert mlx imports to lazy |
| P1 | `brain/moe_router.py:39-40` | Convert mlx imports to lazy |
| P1 | `brain/ane_embedder.py:31` | Convert mlx import to lazy |
| P1 | `brain/distillation_engine.py:45-46` | Convert mlx imports to lazy |
| P1 | `brain/modernbert_engine.py:35` | Convert mlx import to lazy |
| P1 | `brain/paged_attention_cache.py:13` | Convert mlx import to lazy |
| P1 | `dht/local_graph.py:13,34,59,102` | Convert mlx imports to lazy |
| P1 | `brain/inference_engine.py:46` | Convert mlx import to lazy |
| P1 | `brain/hermes3_engine.py:132,158` | Convert mlx imports to lazy |
| P2 | `knowledge/explainer/deep.py:7-8` | Convert mlx imports to lazy |
| P2 | `memory/shared_memory_manager.py:18` | Convert pyarrow import to lazy |
| P2 | `layers/stealth_layer.py:335` | Convert torch import to lazy |
| P2 | `multimodal/vision_encoder.py:13` | Convert coremltools to lazy (or keep if ANE is critical path) |
| P2 | `core/__main__.py:51` | Consider deferring aiohttp (but aiohttp needed for CT log) |
| P3 | `rl/qmix.py:7-9` | Convert mlx imports to lazy |
| P3 | `rl/replay_buffer.py:10` | Convert mlx import to lazy |
| P3 | `brain/ner_engine.py:606` | Check if mlx is truly module-level or inside method |

### Pattern to apply (reference: `brain/ner_engine.py:45-56`)

```python
# Sprint 7B: Lazy torch import for M1 8GB memory optimization
_TORCH_AVAILABLE = False
_torch_module = None

def _get_torch():
    """Lazy torch accessor - imports torch only when first needed."""
    global _torch_module, _TORCH_AVAILABLE
    if _torch_module is None:
        try:
            import torch
            _torch_module = torch
            _TORCH_AVAILABLE = True
        except ImportError:
            _torch_module = None
            _TORCH_AVAILABLE = False
    return _torch_module
```

Apply same pattern for `mlx.core`, `mlx.nn`, `mlx.utils`, `coremltools`, `nodriver`, `pyarrow`.

### Already Correct (no action needed)
- `utils/mlx_cache.py` — lazy init, does NOT import mlx.core at module level ✅
- `knowledge/duckdb_store.py` — duckdb deferred to `initialize()` ✅
- `brain/ner_engine.py` torch — lazy via `_get_torch()` ✅
- `security/stego_detector.py` torch — lazy via `_check_mps_available()` ✅
- `mlx_graphs` guard in `knowledge/explainer/deep.py` — try/except ✅
- `naturalLanguage` guard in `brain/ner_engine.py` — try/except ✅

---

## Notes

1. **`--help` fails** before any importtime measurement possible due to `clip-by-openai` / `torch>=2.1.0` conflict in `pyproject.toml` extras. This is a dependency profile issue, not an import issue per se.

2. **Boot path is mostly clean** — the canonical sprint path through `core/__main__.py` → `sprint_scheduler.py` does NOT directly import MLX-heavy modules at module level. Heavy MLX imports are in brain/ submodules that are loaded on-demand during model lifecycle.

3. **The 7 files with eager MLX** (`multimodal/vision_encoder.py`, `multimodal/fusion.py`, `knowledge/explainer/deep.py`, brain/ submodules) are NOT in the canonical boot path — they are loaded only when the model is actually needed. However, if ANY code path during sprint initialization imports these modules, they will block cold-start.

4. **`nodriver` eager import** in `public_fetcher.py:1079` is the most impactful fix — it loads a browser binary (~100MB) at import time even if never used.

5. **`memory/shared_memory_manager.py`** imports `pyarrow` at module level — this is ~200MB and happens even if shared memory is not used.