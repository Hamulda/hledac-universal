# F216B Vision/VLM Cleanup Plan — Resolved F216C

## Status: 7B VLM References Removed (F216C Complete)

All references to `mlx-community/llava-1.5-7b-4bit` have been removed:

- `tools/vlm_analyzer.py` — no hardcoded model id; `VLM_MODEL_ID` env var must be set explicitly
- `benchmarks/vision_vlm_routing_benchmark.py` — `MANUAL_HEAVY_VLM` → `FUTURE_SMALL_VLM_DEFERRED`
- Documentation updated — see `tests/probe_f216c_remove_7b_vlm/F216C_REMOVE_7B_VLM.md`

## Policy

- No local VLM is configured by default on M1 8GB
- `VLM_MODEL_ID` env var must be set explicitly to enable any VLM
- OCR-first pipeline is canonical and remains unchanged
- VisionOCR remains production-wired (Apple Vision via ocrmac)
- Future small VLM support is deferred to a benchmark sprint

## 1. VisionOCR ✅ ACTIVE (Canonical OCR)
- **File**: `tools/ocr_engine.py`
- **Status**: Production-wired, Apple Vision via `ocrmac`, fail-safe
- **Route**: OCR-first — always attempted before any VLM path
- **Memory**: ~50MB, no GPU required, ANE-assisted on M1

## 2. VLMAnalyzer ✅ RESOLVED (F216C) — No 7B Default on M1 8GB
- **File**: `tools/vlm_analyzer.py`
- **Status**: No hardcoded model id. `VLM_MODEL_ID` env var required for any VLM.
- **Behavior**: `analyze()` returns `""` when `VLM_MODEL_ID` unset — fail-closed
- **Policy**: OCR-first canonical; VLM deferred to future small model benchmark

## 3. VisionEncoder ⚠️ DUMMY MODE (Known Issue — Out of Scope for F216C)
- **File**: `multimodal/vision_encoder.py`
- **Status**: `model_path=None` → dummy mode returns `mx.random.normal()` embeddings
- **Note**: Not addressed in F216C — separate future sprint

## 4. Routing Policy (as implemented in benchmarks/vision_vlm_routing_benchmark.py)

| Route | Description |
|---|---|
| `ocr_only` | VisionOCR sufficient, no VLM |
| `ocr_then_small_vlm` | OCR insufficient, small VLM after benchmark |
| `future_small_vlm_deferred` | No heavy VLM on M1 8GB, deferred to future benchmark |
| `skip_due_to_memory` | M1 memory pressure or oversized image |
| `unsupported` | OCR failure, no VLM available |

---

*F216C — 7B VLM references removed. Future small VLM benchmark deferred.*
