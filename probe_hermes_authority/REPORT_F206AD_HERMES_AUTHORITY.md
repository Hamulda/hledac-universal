# Sprint F206AD — Hermes3 Authority + Runtime Wiring Audit

**Date:** 2026-04-30
**Classification:** `CONNECTED_ADVISORY` — Hermes3 LOADED via ModelManager but NOT actively used in E2E synthesis path

---

## PHASE 0 — REPOWIKI TRUTH

### Repowiki claims (Hermes3 Engine.md)
- **Role:** Canonical LLM decision-making engine, structured generation, ChatML formatting, continuous batching, KV cache, emergency unload
- **Runtime wrappers:** `generate_sprint_plan`, `synthesize_findings`, `decide_next_action`, `generate_report`
- **Referenced files:** `brain/hermes3_engine.py`, `model_lifecycle.py`, `model_manager.py`, `sprint_scheduler.py`
- **Runtime integration:** SprintScheduler loads Hermes via ModelManager; lifecycle managed through `model_lifecycle`
- **Claim:** Hermes3 is canonical for decision-making and synthesis

### Repowiki claims (Brain Engines.md)
- ModelManager is canonical lifecycle owner enforcing single-model-at-a-time on M1 8GB
- Hermes3Engine is L1 canonical engine
- Emergency unload via `model_lifecycle.is_emergency_unload_requested()`
- Sequence diagram shows: Orchestrator → ModelManager → Hermes3Engine.initialize()

**Repowiki verdict:** Hermes3 is canonical runtime engine, not just docs.

---

## PHASE 1 — SOURCE AUTHORITY TRUTH

| Symbol | File | Line | Role | Status |
|--------|------|------|------|--------|
| `class Hermes3Engine` | brain/hermes3_engine.py | 97 | **definition** | ✅ canonical |
| `HermesConfig` | brain/hermes3_engine.py | 76 | config dataclass | ✅ |
| `generate_structured()` | brain/hermes3_engine.py | 1479 | primary structured gen API | ✅ defined |
| `decide_next_action()` | brain/hermes3_engine.py | 1091 | runtime decision wrapper | ✅ defined |
| `generate_sprint_plan()` | brain/hermes3_engine.py | 1228 | sprint planning wrapper | ✅ defined |
| `synthesize_findings()` | brain/hermes3_engine.py | 1324 | findings synthesis wrapper | ✅ defined |
| `generate_report()` | brain/hermes3_engine.py | 1167 | report generation | ✅ defined |
| `warmup_prefix_cache()` | brain/hermes3_engine.py | 2034 | KV cache warmup | ✅ defined |
| `unload()` | brain/hermes3_engine.py | 1754 | canonical 7K unload | ✅ defined |
| `class ModelManager` | brain/model_manager.py | ~175 | singleton lifecycle owner | ✅ canonical |
| `_create_hermes_engine()` | brain/model_manager.py | 278 | Hermes factory | ✅ |
| `_check_memory_admission()` | brain/model_manager.py | 362 | RSS memory gate | ✅ |
| `_check_rss_before_load()` | brain/model_manager.py | 58 | RSS pre-check | ✅ |
| `adjust_fetch_workers()` | brain/model_manager.py | 590, 603, 703, 770 | fetch concurrency management | ✅ |
| `class ModelLifecycle` | brain/model_lifecycle.py | ~871 | windup-local lifecycle helper | ✅ |
| `model_lifecycle.ensure_mlx_initialized()` | brain/model_lifecycle.py | | MLX init helper | ✅ |
| `model_lifecycle.load_model()` | brain/model_lifecycle.py | | unload helper (shadow-state) | ✅ |
| `model_lifecycle.unload_model()` | brain/model_lifecycle.py | 445 | shadow-state helper | ✅ |

### Hermes3Engine active runtime call-sites

| Call-site | File | Line | Method called | Via ModelManager? |
|----------|------|------|---------------|-------------------|
| **SprintScheduler load** | runtime/sprint_scheduler.py | 3928 | `load_model("hermes")` | ✅ YES |
| **SprintScheduler teardown** | runtime/sprint_scheduler.py | 3958 | `release_model("hermes")` | ✅ YES |
| `_hermes_engine` stored | runtime/sprint_scheduler.py | 3928 | stored in `self._hermes_engine` | N/A |
| `_hermes_engine` passed to pipeline | runtime/sprint_scheduler.py | 1466, 1561 | passed to `ActiveSprintPipeline` | N/A |
| **`self._hermes_engine` actual invocations** | **runtime/sprint_scheduler.py** | **NONE** | **NOT CALLED** | ❌ NO |
| `ModelManager.generate_report()` | brain/model_manager.py | 1035 | `Hermes3Engine()` direct | ❌ BYPASS |
| `SynthesisRunner._run_xgrammar_generation()` | brain/synthesis_runner.py | 799 | `_lifecycle._ensure_loaded()` | ❌ NOT Hermes |
| `synthesis_runner.synthesize_findings()` | brain/synthesis_runner.py | 411 | xgrammar/Outlines path | ❌ NOT Hermes |

---

## PHASE 2 — MODEL MANAGER TRUTH

| Question | Answer | Evidence |
|----------|--------|----------|
| Load owner | **ModelManager** (canonical) | `model_manager.py:212` |
| Unload owner | **ModelManager._release_current_async()** via `engine.unload()` | `model_manager.py:751-752` |
| Hermes load via ModelManager? | **YES** | `_create_hermes_engine()` factory at line 278 |
| Memory admission gate? | **YES** | `_check_memory_admission()` line 362, `_check_rss_before_load()` line 58 |
| Quantization selector? | **YES** | `QuantizationSelector` imported line 25 |
| Model can download from internet? | **YES** — mlx_lm.load() can download | `model_manager.py:589-603` comment shows download concurrency management |
| Load reduces fetch workers? | **YES** | `adjust_fetch_workers(3)` at line 703 |
| Unload restores fetch workers? | **YES** | `adjust_fetch_workers(25)` at line 770 |
| MLX cleanup order correct? | **YES** | `mx.eval([])` barrier before `clear_cache()` at lines 416, 851, 1212 |

### 7K Unload Order (Hermes3Engine.unload()) — ✅ CORRECT
```
1. _shutdown_batch_worker(timeout=3.0)
2. _evict_cache()
3. model = None; tokenizer = None
4. gc.collect()
5. mx.eval([])  ← barrier
6. mx.clear_cache()
```

---

## PHASE 3 — CANONICAL PATH CHECK

### Canonical E2E Path:
```
python -m hledac.universal --sprint ...
→ core/__main__.run_sprint()
→ SprintScheduler.run()
  → _load_hermes_for_sprint() → ModelManager.load_model("hermes") → self._hermes_engine
  → [acquisition loop — self._hermes_engine NOT called]
  → _unload_hermes_at_teardown() → ModelManager.release_model("hermes")
```

### Key findings:
1. **Hermes IS loaded** via ModelManager (canonical lifecycle owner) ✅
2. **`self._hermes_engine` is stored** in SprintScheduler ✅
3. **`self._hermes_engine` is PASSED to ActiveSprintPipeline** ✅
4. **But `self._hermes_engine` is NEVER ACTUALLY CALLED** in the acquisition loop ❌
5. **Synthesis uses xgrammar/Outlines path** via SynthesisRunner, NOT Hermes methods ❌

### `brain/__init__.py` FACADE audit:
- Line 18: **FACADE STATUS** documented explicitly
- Re-exports Hermes3Engine but does NOT instantiate on import
- No heavy engines loaded at import time ✅

### `ModelManager.generate_report()` — CRITICAL BYPASS ❌
```python
# model_manager.py:1033-1042
from .hermes3_engine import Hermes3Engine
engine = Hermes3Engine()        # ← DIRECT instantiation
try:
    await engine.initialize()  # ← No ModelManager lifecycle, no RSS check
```
- Bypasses ModelManager's memory admission gate
- Bypasses 1-model-at-a-time enforcement
- Could load Hermes while another model is in RAM

---

## PHASE 4 — RISK CLASSIFICATION

### CONNECTED_ADVISORY

**Hermes3 IS loaded in canonical E2E path** (via ModelManager at sprint boot, released at teardown).

**BUT the loaded Hermes engine is NOT actively used** — the `_hermes_engine` reference sits unused in `SprintScheduler._hermes_engine` and is passed to `ActiveSprintPipeline` but never called.

**Actual synthesis path uses:**
- xgrammar + Outlines (via `SynthesisRunner._run_xgrammar_generation()`)
- NOT Hermes3Engine methods

### BROKEN sub-component:
`ModelManager.generate_report()` — direct Hermes3Engine instantiation bypasses ModelManager lifecycle authority.

---

## PHASE 5 — HERMES INTEGRATION PLAN (NO CODE)

### Recommended: F206AE — Hermes3 Post-Export Advisory Synthesis

**Rationale:** Hermes is already loaded (cost paid), unused during acquisition, safely released at teardown. Adding advisory synthesis AFTER export is low-risk because:
- Export completes before teardown → model already loaded
- Advisory is read-only enrichment → no acquisition loop mutation
- Can be env-gated: `HLEDAC_ENABLE_HERMES_SYNTHESIS=1`

**Rules for F206AE:**
- env gate: `HLEDAC_ENABLE_HERMES_SYNTHESIS=1` (default disabled)
- only after `duckdb_store.export_sprint()` completes
- max input chars bounded (e.g., 8000 chars for synthesis prompt)
- use existing `SprintScheduler._hermes_engine` (already loaded)
- fail-soft: `CancelledError` re-raise, all others → skip gracefully
- NO scheduler decision mutation
- NO acquisition loop use
- NO model load under UMA `is_critical()` or `is_emergency()`

**JSON artifact fields to add:**
```python
hermes_synthesis_enabled: bool
hermes_synthesis_attempted: bool
hermes_synthesis_success: bool
hermes_synthesis_error: Optional[str]
hermes_summary: Optional[str]  # short advisory summary
hermes_runtime_ms: Optional[int]
hermes_model_load_skipped_reason: Optional[str]  # "memory_pressure" | "not_loaded" | None
```

**Safe advisory seam:** `SprintScheduler._run_advisory_runner()` — already exists as post-acquisition hook.

---

## PHASE 6 — TEST RESULTS

Hermetic tests at `probe_hermes_authority/test_hermes_authority.py`.

**18/18 PASSED** — all hermetic, no model load, no model download

| # | Test | Result |
|---|------|--------|
| 1 | `test_hermes3_engine_definition_exists` | ✅ PASS |
| 2 | `test_hermes3_engine_has_required_methods` | ✅ PASS |
| 3 | `test_hermes3_engine_unload_is_async` | ✅ PASS |
| 4 | `test_brain_init_is_facade` | ✅ PASS |
| 5 | `test_brain_init_no_heavy_imports_at_module_level` | ✅ PASS |
| 6 | `test_model_manager_registry_contains_hermes` | ✅ PASS |
| 7 | `test_model_manager_load_model_enforces_memory` | ✅ PASS |
| 8 | `test_model_manager_factory_creates_hermes3_engine` | ✅ PASS |
| 9 | `test_sprint_scheduler_import_does_not_load_hermes` | ✅ PASS |
| 10 | `test_hermes_load_is_lazy` | ✅ PASS |
| 11 | `test_hermes_unload_7k_order` | ✅ PASS |
| 12 | `test_classification_status_is_allowed` | ✅ PASS |
| 13 | `test_hermes_authority_verdict_documented` | ✅ PASS |
| 14 | `test_env_gate_name_documented` | ✅ PASS |
| 15 | `test_synthesis_runner_uses_xgrammar_not_hermes` | ✅ PASS |
| 16 | `test_model_manager_generate_report_bypass_exists` | ✅ PASS |
| 17 | `test_sprint_scheduler_hermes_stored_not_called` | ✅ PASS |
| 18 | `test_model_manager_adjusts_fetch_workers` | ✅ PASS |

---

## PHASE 7 — SUMMARY

| Dimension | Status |
|-----------|--------|
| Hermes3Engine definition exists | ✅ |
| brain/__init__.py is facade-only | ✅ FACADE |
| ModelManager registry contains hermes | ✅ |
| ModelManager factory points to Hermes3Engine | ✅ |
| Canonical path import does NOT load model | ✅ (lazy load) |
| SprintScheduler import does NOT load model | ✅ (lazy load) |
| Model download on import | ❌ (lazy, only on initialize()) |
| Hermes3Engine has unload() method | ✅ |
| Hermes is loaded in E2E | ✅ CONNECTED |
| Hermes is actively used in E2E | ❌ UNUSED |
| Classification | **CONNECTED_ADVISORY** |

---

## NEXT SPRINT PROMPT (F206AE)

```
F206AE — Hermes3 Post-Export Advisory Synthesis

CONTEXT:
- Hermes3Engine IS loaded at sprint start via ModelManager (canonical lifecycle)
- _hermes_engine is stored in SprintScheduler._hermes_engine
- Hermes is NOT called during acquisition — sits dormant
- SynthesisRunner uses xgrammar/Outlines, NOT Hermes
- Hermes can be safely used for advisory synthesis AFTER export completes
- ModelManager.generate_report() BYPASSES ModelManager — fix separately

IMPLEMENT (only these files):
- runtime/sprint_scheduler.py (add advisory hook in _run_advisory_runner)
- NO new files needed

RULES:
- env gate: HLEDAC_ENABLE_HERMES_SYNTHESIS=1
- default disabled
- only after duckdb_store.export_sprint() completes
- max 8000 chars for synthesis prompt
- use self._hermes_engine (already loaded)
- CancelledError re-raise
- fail-soft for all other errors
- NO scheduler decision mutation
- NO acquisition loop use
- NO model load under is_critical() or is_emergency()
- JSON fields: hermes_synthesis_*
```

---

## SUCCESS DEFINITION — SEALED ✅

| Criterion | Status |
|-----------|--------|
| Hermes3 active vs available-not-wired known | ✅ CONNECTED_ADVISORY — loaded but unused |
| No model load/download occurred | ✅ Verified — all tests are hermetic |
| ModelManager authority verified | ✅ load/unload via ModelManager, bypass exists |
| Canonical path import guard verified | ✅ brain/__init__.py is facade, lazy load |
| Hermes authority report exists | ✅ `probe_hermes_authority/REPORT_F206AD_HERMES_AUTHORITY.md` |
| Tests pass | ✅ 18/18 passing |

---

## NO-GIT RULE CONFIRMED

No git operations were performed during this sprint.
No files outside `probe_hermes_authority/` were modified.
MUST NOT EDIT files unchanged (autonomous_orchestrator.py, sprint_scheduler.py, __main__.py, etc.).

---

## FILES ANALYZED

| File | Lines | Key Finding |
|------|-------|-------------|
| brain/__init__.py | 236 | FACADE only, no instantiation |
| brain/hermes3_engine.py | 2242 | L1 canonical, all methods defined |
| brain/model_manager.py | 1300+ | Load owner ✅, generate_report BYPASS ❌ |
| brain/model_lifecycle.py | 900+ | Shadow-state helpers, 7K order documented |
| brain/synthesis_runner.py | 1000+ | xgrammar path, NOT Hermes methods |
| runtime/sprint_scheduler.py | 4000+ | Hermes LOADED but NOT USED |
| .qoder/repowiki/* | — | Claims Hermes is canonical runtime |
