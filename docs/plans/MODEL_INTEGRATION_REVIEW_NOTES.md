# Model Integration Review Notes — 2026-05-14
**Scope**: `hledac/universal/` corrective consistency pass
**Files changed**: `MODEL_INTEGRATION_PLAN.md`, `model_integration_matrix.json`

---

## What Was Corrected

### 1. Overconfident Claims Fixed

| Original Claim | Location | Problem | Corrected To |
|---|---|---|---|
| "already optimal for retrieval" | Plan line 140 | No benchmark data | "current production-safe baseline for retrieval" |
| "Already optimal" | Plan line 256 (table) | Same unproven claim | "benchmark_candidate — consolidation first" |
| "already correct" | Plan line 190 | No benchmark | "GLiNER-Relex primary until benchmark disproves" |
| "Already implemented" | Section 5 (memory governor) | Overstated — one RSS check ≠ a governor | "partially implemented — single RSS check only" |
| "Already present" | Section 5 (circuit breaker) | Fetch-domain circuit breaker ≠ model-level CB | "missing — fetch domain CB exists, model-level CB missing" |
| "already in place" | Section 5 (prompt security) | `_sanitize_for_llm` ≠ prompt injection sandbox | "partially implemented — text sanitization only" |
| "Already uses Outlines" | Section 5 (output validator) | Outlines ≠ evidence grounding validator | "partially implemented — schema constrained, no grounding" |
| "Already handles missing models" | Section 5 (offline registry) | Discovery ≠ offline registry with pinned hashes | "partially implemented — 3-tier discovery, no hash verification" |
| "canonical unload exists" | Section 5 (graceful shutdown) | unload() exists but no request draining | "partially implemented — unload exists, request draining missing" |
| "Already exists" | Section 5 (adaptive context) | dir exists, contents unknown | "uncertain — context_optimization/ exists, scope unknown" |
| "already lazy-loads" | Section 5 (progressive warming) | lazy ≠ staged progressive warming | "partially implemented — lazy load, no staged warm" |

### 2. Model ID Consistency Fixed

**Plan had typo**: `jinaai/jena-reranker-v2-base` (line ~258 in replacement matrix)
**Correct ID**: `jinaai/jina-reranker-v2-base`
**JSON matrix**: Already had the correct ID `jinaai/jina-reranker-v2-base` ✅

Fixed in plan replacement table to match JSON.

### 3. KV Cache Estimate Corrected

| | Original | Corrected |
|---|---|---|
| Estimate | 32 MB | **224 MB @ 4-bit, 8K context** |
| Formula | Handwavy | `28 layers × 2(K,V) × 8 KV heads × 128 head_dim × 8192 ctx × 0.5 bytes` |
| Error | Underestimated by ~7x | n/a |

**Conservative range**: 56 MB (4K ctx, 4-bit) to 896 MB (8K ctx, fp16)
**Marked**: `estimate_uncertain` — actual depends on MLX internal quantization granularity

### 4. Section 5 Production Layer Claims Corrected

The plan's Section 5 equated partial existing functionality with complete production layers.
Corrected by distinguishing:

| What Exists | What Does NOT Exist |
|---|---|
| Fetch domain circuit breaker (`FetchCoordinator._record_domain_failure`) | Model-level OOM/timeout circuit breaker (`InferenceGuard`) |
| `_sanitize_for_llm` text cleaner | Full prompt injection sandbox with content policy |
| Outlines schema generation | Evidence-grounding validator (output matches evidence) |
| 3-tier model discovery | Offline model registry with pinned SHA hashes |
| `unload()` method | Request draining (in-flight requests finish before unload) |
| Bounded queues in sprint_scheduler | Adaptive backpressure based on memory/load |
| `context_optimization/` dir | Confirmed adaptive context policy implementation |
| Lazy engine load | Staged progressive model warming |
| `ane_embedder.py` separate from MLX | **Worker/process isolation** (single-process architecture) |
| Local logging | Cross-worker trace context / OpenTelemetry |

### 5. Phase Roadmap Restructured

**Phase 0 (strictly cleanup)**:
- Consolidate embedding wrappers — NOT benchmark or model changes
- Remove bf16 Hermes from `layers/memory_layer.py`
- Clarify reranker ownership (FlashRank in synthesis_runner is canonical)
- Clarify VLM/OCR dispatcher ownership (VisionOCR primary, VLM fallback)
- Fix model ID drift and comments

**Phase 1 (safe registry only)**:
- Explicit structured generation model config
- Primary/fallback reasoner config
- Keep existing Hermes as fallback
- **No primary model swap**

**Phase 2 (benchmark harness)**:
- Required BEFORE any primary model replacement
- All Phase 3+ model swaps gate on Phase 2 benchmarks passing

**Phase 3 (model swaps)**:
- Only after Phase 2 benchmarks pass

**Phase 4 (production runtime guardrails)**:
- InferenceGuard, circuit breaker, prompt injection sandbox, adaptive context, local trace log, backpressure, graceful shutdown

### 6. M1 8GB Conservative Changes

- Removed claim that "8GB is usable by AI" — replaced with hard ceiling language
- Removed recommendation to add both bge and jina reranker as active runtime choices (pick one after benchmark)
- Kept GLiNER-Relex as primary unless benchmark disproves
- Kept ModernBERT baseline until embedding wrappers consolidated
- Removed any assumption of two heavy models loaded together

---

## Files Updated

- `MODEL_INTEGRATION_PLAN.md` — all corrections above applied
- `model_integration_matrix.json` — added `current_status`, `confidence`, `blocked_by`, `benchmark_dataset_needed`, `implementation_phase` fields
- `MODEL_INTEGRATION_REVIEW_NOTES.md` (this file)

## Remaining Unknowns (not changed — needs investigation)

1. `context_optimization/` directory contents — could be empty or experimental
2. `brain/model_swap_manager.py` completeness — referenced but not audited
3. `runtime/memory_authority.py` — F202J governor wiring verified from memory but not runtime-audited
4. Whether `InferenceGuard` class exists anywhere in the codebase
5. `ssm_reranker.py` model ID and runtime — marked poorly documented in original plan

---

*No application runtime code was changed. This is a documentation-only corrective pass.*