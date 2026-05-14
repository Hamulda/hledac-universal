# CoreML/ANE Integration Audit — Sprint F216A

**Date**: 2026-05-14
**Runtime**: MacBook Air M1 8GB (UMA)
**Status**: Audit + Benchmark + Integration-Readiness (NO production swaps)

---

## 1. Executive Summary

The codebase has **5 active CoreML/ANE paths**. Only **2 are fully wired and production-ready** (ANEEmbedder for semantic dedup, VisionOCR for OCR). The remaining 3 have structural issues or are test-only stubs. No path currently needs replacement — this audit establishes the baseline for future ANE/CoreML migrations.

**Recommendation**: Keep ANEEmbedder as-is (working), advance VisionOCR integration, fix captcha_solver dead code, retire the vision_encoder dummy-mode-only path, and defer NER CoreML until real model files are confirmed.

---

## 2. Active CoreML/ANE Paths — Inventory

### 2.1 ANEEmbedder (`brain/ane_embedder.py`, 600 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | ACTIVE — fully wired, working fallback chain |
| **Model** | Pre-compiled `AllMiniLML6V2.mlmodelc` or MLX ModernBERT fallback |
| **Path** | `~/.hledac/models/AllMiniLML6V2.mlmodelc` |
| **Runtime** | CoreML (ANE) → MLX ModernBERT → hash fallback |
| **Uses ANE?** | Yes (primary path when model file exists) |
| **Telemetry** | `ane_embed_attempted`, `ane_embed_fallback_used`, `ane_warmup_executed` |
| **Called by** | `semantic_dedup_findings()` — cross-sprint dedup |
| **M1 safe?** | Yes — fallback chain prevents OOM |
| **Production?** | Yes — deployed in sprint F228B |

**Fallback chain**: CoreML MiniLM → MLX ModernBERT → url+title hash
**CoreML compilation**: `ct.models.MLModel.compileModelAtURL()` runs on first load if `.mlmodel` present but `.mlmodelc` absent.

**Findings**:
- `model_name="minilm_ane"` parameter is misleading — actual file is `AllMiniLML6V2.mlmodelc`, not `{model_name}.mlmodelc`
- `convert_to_ane()` checks `AllMiniLML6V2.mlmodelc` (NOT `minilm_ane.mlmodelc`)
- `_coreml_embed()` helper exists but is not a named function in the file (grep confirms)

### 2.2 VisionEncoder (`multimodal/vision_encoder.py`, 88 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | ACTIVE (dummy mode, no real model) |
| **Model** | User-provided CoreML path — no default model shipped |
| **Runtime** | CoreML → random embedding fallback |
| **Uses ANE?** | Attempts to (`compute_units=ct.ComputeUnit.ALL`), but model_path is None by default |
| **Called by** | `multimodal/analyzer.py` — evidence triage sidecar |
| **M1 safe?** | Yes — dummy mode when no model |
| **Production?** | Partial — wired but no bundled model |

**Findings**:
- `model_path=None` default → immediately falls to `mx.random.normal()` dummy mode
- `compute_units=ct.ComputeUnit.ALL` would use ANE if real model present
- 1280-dim dummy embeddings are stable but meaningless
- No model file is bundled with the project

**Integration concern**: `VisionEncoder` is called from `multimodal/analyzer.py:_run_evidence_triage_sidecar()` but only returns dummy embeddings — actual document encoding is done by `VisionOCR` (Apple Vision OCR). This suggests `VisionEncoder` may be dead code on the document path.

### 2.3 VisionCaptchaSolver (`captcha_solver.py`, 421 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | ACTIVE (lazy import, no model bundled) |
| **Model** | User-provided YOLO CoreML path only |
| **Runtime** | YOLO CoreML + VNCoreMLModel (ANE when model present) |
| **Uses ANE?** | Only if `model_path` provided and `use_ane=True` |
| **Called by** | Unknown — grep shows no call sites |
| **M1 safe?** | Yes — lazy load, no crash if model absent |
| **Production?** | Not confirmed — no production call sites |

**Findings**:
- `_COREML_AVAILABLE` and `_VN_AVAILABLE` flags correctly gate runtime
- `model_path=None` by default → text-only mode (no crash)
- `_result_cache` with TTL works correctly
- `solve_image_captcha()` uses `_2captcha_api_key` (external service, not CoreML inference)

**Dead code concern**: `VNCoreMLModel.modelForMLModel()` is called in `_load_model()` but no production call site found for `VisionCaptchaSolver`. The CAPTCHA solving goes through `2captcha` API, not the local CoreML model. This suggests the CoreML model path is experimental/test-only.

### 2.4 VisionOCR (`tools/ocr_engine.py`, 117 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | ACTIVE — canonical OCR path |
| **Model** | Apple Vision via `ocrmac` (wraps Vision framework) |
| **Runtime** | macOS Vision framework (ANE-assisted on M1) |
| **Uses ANE?** | Yes (Vision framework uses ANE automatically on M1) |
| **Called by** | `multimodal/analyzer.py` and `forensics/metadata_extractor.py` |
| **M1 safe?** | Yes — Apple-native, lazy import |
| **Production?** | Yes — canonical document OCR |

**Findings**:
- `ocrmac` is a lazy import, not a hard dependency
- `VisionOCR.__init__` imports at class definition time (not lazy)
- `MAX_IMAGE_SIZE = 100 * 1024 * 1024` (100MB fail-safe bound)
- Returns structured `OCRResult` with text, confidence, bounding boxes

**Integration**: This is the correct, production-wired path for document OCR. No changes needed.

### 2.5 NER CoreML (`brain/ner_engine.py`, 1634 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | FALLBACK-ONLY — ANE path never actually called in production |
| **Model** | `~/.hledac/models/ner.mlmodel` (not bundled) |
| **Runtime** | PyTorch GLiNER (primary) + NaturalLanguage (ANE attempt) + CoreML NER (untested) |
| **Uses ANE?** | Attempts to, via `_nl_process_sync()` and `_load_coreml_model()` |
| **Called by** | `brain/ner_engine.py` internal, `extract_entities_from_texts()` |
| **M1 safe?** | Yes — falls back gracefully |
| **Production?** | PyTorch path used in production, ANE path untested |

**Findings**:
- `_nl_process_sync()` uses `NLTagger` from NaturalLanguage framework — this IS a real ANE path (NLTagger uses ANE on M1)
- `_load_coreml_model()` looks for `ner.mlmodel` — never called in any observed production run
- ANE predictions counter `_ane_predictions` exists but is never incremented in the code path
- `extract_entities_from_texts()` uses PyTorch GLiNER, not ANE

**Concern**: The `ner.mlmodel` path requires a pre-compiled CoreML file that does not ship with the project. The NaturalLanguage ANE path is real but yields unverified entity quality. Both ANE paths should be considered experimental until a benchmark validates entity quality against the PyTorch baseline.

### 2.6 VLMAnalyzer (`tools/vlm_analyzer.py`, 164 lines)

| Attribute | Value |
|-----------|-------|
| **Status** | ACTIVE but HEAVY — *(removed — M1 8GB unsafe)* |
| **Model** | `*(removed — M1 8GB unsafe)*` |
| **Runtime** | MLX-VLM (Metal, not ANE) |
| **Uses ANE?** | No |
| **Called by** | `multimodal/analyzer.py` (complex image analysis) |
| **M1 safe?** | No — heavy model OOM risk on M1 8GB |
| **Production?** | Used but memory-expensive |

**Findings**:
- Singleton pattern works correctly
- `mx.eval([])` + `mx.metal.clear_cache()` invariant correct in `unload()`
- Memory guard at 5GB RSS skip
- Should be replaced with `SmolVLM2-500M` or `Qwen2-VL-2B-Instruct-4bit` (per MODEL_INTEGRATION_PLAN)

---

## 3. Benchmark Harness — `benchmarks/coreml_ane_capability.py`

### Design Principles

1. **Hermetic** — no network, no model download, no external API calls
2. **CI-safe** — all CoreML/Vision availability checks return structural data, not runtime inference quality
3. **M1-safe** — no heavy model loads, no OOM risk
4. **JSON output** — machine-readable for CI pipelines
5. **Probe-test aligned** — results mirror what probe tests validate

### What the benchmark CAN test (without models/downloads):

| Check | How |
|-------|-----|
| CoreML tools availability + version | `coremltools.__version__` |
| VNCoreMLModel class availability | Lazy import from Vision |
| Vision framework availability | `VNImageRequestHandler` availability |
| ANE availability signal | `NLTagger` availability |
| ocrmac availability | Lazy import |
| ANEEmbedder structure | `_ANE_TELEMETRY`, `ANEStatusResult` dataclass |
| VisionEncoder dummy mode | `VisionEncoder()._model is None` when no path |
| CaptchaSolver lazy init | `_result_cache`, `_cache_timestamps` structure |
| Fallback chain completeness | Which fallbacks are wired vs missing |
| Model file existence | Path lookups for `.mlmodelc` files (user-provided, not bundled) |

### What the benchmark CANNOT test (requires real models):

- Actual ANE inference latency vs MLX
- Entity quality of NLTagger vs GLiNER
- VisionEncoder encoding quality
- CaptchaSolver YOLO detection accuracy

### Benchmark Output Schema

```json
{
  "hermetic": true,
  "timestamp": "ISO8601",
  "runtime": "M1 8GB macOS",
  "ANE": {
    "available": true,
    "version": "6.0+",
    "neural_engine_device_name": "Apple Neural Engine",
    "coreml_tools_version": 7.0,
    "nitro_present": false
  },
  "paths": {
    "ane_embedder": {
      "status": "active",
      "inference_path": "coreml | mlx | hash_fallback",
      "model_exists": false,
      "mlx_fallback_configured": true,
      "telemetry_instrumented": true,
      "fallback_chain_complete": true
    },
    "vision_encoder": {
      "status": "dummy_mode",
      "model_path_provided": false,
      "coreml_loaded": false,
      "compute_units": "ALL",
      "requires_user_model": true,
      "production_wired": true
    },
    "vision_captcha_solver": {
      "status": "lazy_placeholder",
      "model_path_provided": false,
      "vncore_ml_model_class_available": true,
      "coreml_tools_available": true,
      "has_production_call_site": false,
      "note": "2captcha API primary, CoreML path experimental"
    },
    "vision_ocr": {
      "status": "active_canonical",
      "ocrmac_available": true,
      "apple_vision_ane_assisted": true,
      "production_call_sites": 2,
      "lazy_import": true,
      "max_image_size_bytes": 104857600
    },
    "ner_engine_ane_path": {
      "status": "fallback_unused",
      "nl_tagger_available": true,
      "coreml_ner_model_exists": false,
      "production_path": "pytorch_gliner",
      "entity_quality_unverified": true
    },
    "vlm_analyzer": {
      "status": "active_heavy",
      "model": "*(removed — M1 8GB unsafe)*",
      "uses_ane": false,
      "mlx_vlm_available": true,
      "m1_8gb_safe": false,
      "recommended_replacement": "SmolVLM2-500M-4bit"
    }
  },
  "duplication_assessment": {
    "vision_ocr_vs_vision_encoder": "OCR is canonical for documents, VisionEncoder dummy mode provides no value",
    "ner_gliner_vs_ner_ane": "GLiNER is production path, ANE path unverified and unused",
    "captcha_2captcha_vs_coreml": "2captcha is the real path, CoreML YOLO path has no production call site"
  },
  "recommended_actions": [
    {"priority": "P1", "action": "Audit VisionEncoder call sites — confirm dummy mode vs broken integration"},
    {"priority": "P2", "action": "Add ANEEmbedder benchmark: measure fallback latency (hash vs MLX vs CoreML)"},
    {"priority": "P2", "action": "Replace *(removed — M1 8GB unsafe)* with SmolVLM2-500M in VLMAnalyzer"},
    {"priority": "P3", "action": "Remove or guard VisionCaptchaSolver CoreML path behind feature flag"},
    {"priority": "P3", "action": "Add NER ANE path quality benchmark against GLiNER baseline"}
  ]
}
```

---

## 4. Integration Recommendations

### 4.1 Keep (Working — No Change Needed)

| Path | Reason |
|------|--------|
| **VisionOCR** | Canonical OCR, Apple-native, ANE-assisted, production-wired |
| **ANEEmbedder** | Real ANE path with fallback chain, telemetry, deployed |
| **NEREngine (PyTorch)** | Production path, correct lazy loading |

### 4.2 Benchmark Before Changing

| Path | Benchmark needed |
|------|-----------------|
| **VisionEncoder** | Confirm dummy mode is dead code or confirm actual call sites in `multimodal/analyzer.py` |
| **VisionCaptchaSolver** | Confirm production call site absence; mark as experimental if none found |
| **NER ANE path** | Quality benchmark: NLTagger vs GLiNER entity F1 on a held-out set |

### 4.3 Future CoreML/ANE Candidates

| Role | Candidate | Why ANE/CoreML |
|------|-----------|----------------|
| **Embedder (semantic dedup)** | AllMiniLML6V2 (current) | Already working |
| **OCR** | VisionOCR (current) | Already working |
| **Small VLM** | SmolVLM2-500M | ANE-friendly, M1-safe |
| **NER (lightweight)** | NLTagger | Already in codebase, ANE-accelerated |
| **Document encoder** | VisionEncoder (requires model) | Real CoreML path if model provided |

### 4.4 Do NOT Move to CoreML/ANE

| Role | Reason |
|------|--------|
| **Primary reasoner** | Hermes 3B — MLX is correct; ANE doesn't support LLMs |
| **Reranker** | FlashRank ONNX is correct; ANE not designed for cross-encoder |
| **Embedder (LLM-based)** | ModernBERT MLX is correct; CoreML MiniLM is only for fast dedup |

---

## 5. What Changes Later (Not Now)

These are concrete, specific future changes identified during audit — documented for when implementation is approved.

| File | Change | Trigger |
|------|--------|---------|
| `brain/ane_embedder.py:255` | `model_name="minilm_ane"` → actual model path `AllMiniLML6V2.mlmodelc` (fix misleading naming) | Later (cosmetic only) |
| `tools/vlm_analyzer.py:65` | Replace `*(removed — M1 8GB unsafe)*` with `mlx-community/SmolVLM2-500M-4bit` | After benchmark confirms quality |
| `multimodal/vision_encoder.py` | Either wire a real model or remove from `multimodal/analyzer.py` call path | After audit confirms dummy-only |
| `brain/ner_engine.py` | Add benchmark comparing NLTagger vs GLiNER F1 | Before activating ANE path |
| `captcha_solver.py` | Gate CoreML path behind `HLEDAC_ENABLE_CAPTCHA_COREML` or remove dead code | After call site audit |
| `brain/ane_embedder.py` | Add `--ane-benchmark` CLI flag to measure CoreML vs MLX vs hash fallback latency | When benchmark harness approved |

---

## 6. Probe Test Plan (`probe_f216A`)

**Focus**: Structural validation of CoreML/ANE paths — availability, fallback wiring, no model downloads.

| Test | Validates |
|------|-----------|
| `test_ane_status_result_dataclass` | `ANEStatusResult` has all required fields |
| `test_ane_telemetry_counters` | `_ANE_TELEMETRY` keys are `str → int` |
| `test_ane_embedder_fallback_chain` | ANEEmbedder has `is_loaded`, `_mlx_model`, `_fallback_embedder` |
| `test_vision_encoder_dummy_mode` | `VisionEncoder()` with `model_path=None` → `._model is None` |
| `test_vision_captcha_solver_lazy_init` | `VisionCaptchaSolver()` → no model loaded at init |
| `test_vision_ocr_lazy_import` | `VisionOCR` has `ocrmac` as lazy import |
| `test_ner_nl_tagger_available` | `NaturalLanguage.NLTagger` available on M1 |
| `test_coreml_tools_version_check` | `coremltools.__version__` returns a float |
| `test_vncore_ml_model_class_available` | `VNCoreMLModel` class is importable from Vision |

---

## 7. Invariants

| # | Invariant | Test name |
|---|-----------|-----------|
| 1 | ANEEmbedder has `is_loaded` property (bool) | `test_ane_embedder_has_is_loaded` |
| 2 | ANEEmbedder fallback chain: CoreML → MLX → hash | `test_ane_embedder_fallback_chain` |
| 3 | VisionEncoder dummy mode when `model_path=None` | `test_vision_encoder_dummy_mode` |
| 4 | VisionCaptchaSolver lazy init — no model load at `__init__` | `test_vision_captcha_solver_lazy_init` |
| 5 | VisionOCR uses Apple Vision (ocrmac), not VLM | `test_vision_ocr_is_apple_vision` |
| 6 | NEREngine uses PyTorch GLiNER (not ANE) for production | `test_ner_engine_production_path_pytorch` |
| 7 | ANE telemetry counters are reset-able | `test_ane_telemetry_reset` |
| 8 | CoreML availability check does not crash without model files | `test_coreml_availability_no_crash` |
| 9 | VNCoreMLModel requires explicit model_path (not auto-downloaded) | `test_vncore_ml_no_auto_download` |
| 10 | VLMAnalyzer uses MLX-VLM, not CoreML | `test_vlm_analyzer_uses_mlx_vlm` |

---

## 8. Files to Create

| File | Purpose |
|------|--------|
| `COREML_ANE_INTEGRATION_PLAN.md` | This document |
| `coreml_ane_integration_matrix.json` | Machine-readable matrix of all paths |
| `benchmarks/coreml_ane_capability.py` | Benchmark harness (hermetic, no model downloads) |
| `tests/probe_f216a_coreml_ane_capability/__init__.py` | Probe test package |
| `tests/probe_f216a_coreml_ane_capability/test_coreml_ane_capability.py` | Probe tests |

---

## 9. Sprint Metadata

```
Sprint: F216A
Focus: CoreML/ANE audit + benchmark readiness
Runtime: MacBook Air M1 8GB (UMA)
Constraints: No model swaps, no downloads, no browser/network in tests
Probe ID: F216A
Canonical benchmark: benchmarks/coreml_ane_capability.py --hermetic --json
```