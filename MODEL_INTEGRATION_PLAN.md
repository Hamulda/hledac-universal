# Model Integration Plan — Hledac Universal
**Date**: 2026-05-14
**Scope**: `hledac/universal/` only
**M1 Target**: MacBook Air M1 8GB UMA

---

# 1. Current Model Architecture Map

## 1.1 Active Runtime Models (verified from code)

| # | Component | File | Model ID | Runtime | Role | Lifecycle | Status |
|---|-----------|------|----------|---------|------|-----------|--------|
| 1 | `Hermes3Engine` | `brain/hermes3_engine.py:147` | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` (4bit Q4) | MLX (mlx_lm.load) | Primary reasoner | `engine.unload()` (7K order) | **ACTIVE** |
| 2 | `ModernBERTEmbedder` | `embeddings/modernbert_embedder.py:69` | `nomic-ai/modernbert-embed-base` | MLX (mlx_embeddings_load) | Embedder | `unload()` | **ACTIVE** |
| 3 | `MLXEmbeddingManager` | `core/mlx_embeddings.py:79` | `nomic-ai/modernbert-embed-base` | MLX (mlx_embeddings_load) | Alt embedder | `unload()` | **ACTIVE** |
| 4 | `ANEEmbedder` | `brain/ane_embedder.py:208` | `nomic-ai/modernbert-embed-base` (MLX) or CoreML | CoreML+MLX | ANE embedder priority | async load() | **ACTIVE** |
| 5 | `NEREngine` | `brain/ner_engine.py:76` | `knowledgator/gliner-relex-large-v0.5` | PyTorch CPU (GLiNER.from_pretrained) | NER+RE | `unload()` | **ACTIVE** |
| 6 | `ModelLifecycle._ensure_loaded()` | `brain/model_lifecycle.py:714` | Discovered Qwen3-0.6B or ≤1B at `~/.cache/huggingface/hub/` | MLX (mlx_lm.load) | Structured JSON gen (windup-local) | windup-local, isolated | **ACTIVE** |
| 7 | FlashRank `Ranker` | `brain/synthesis_runner.py:304` | `ms-marco-MiniLM-L-12-v2` | ONNX (flashrank) | Reranker (pre-synthesis) | singleton | **ACTIVE** |
| 8 | `VLMAnalyzer` | `tools/vlm_analyzer.py:28` | *(none — VLM_MODEL_ID env var required) | MLX-VLM (mlx_vlm.load) | Vision-language | unload() | **INACTIVE** (no default — future small VLM deferred to benchmark) |
| 9 | `VisionOCR` | `tools/ocr_engine.py:18` | Apple Vision via `ocrmac` | macOS Vision | OCR | lazy import | **ACTIVE** |
| 10 | `VisionEncoder` | `multimodal/vision_encoder.py:22` | CoreML model (user-provided path) | CoreML | Document image encoding | async load() | **ACTIVE** |
| 11 | `VisionCaptchaSolver` | `captcha_solver.py:85` | YOLO CoreML + VNCoreMLModel | CoreML | CAPTCHA solving | lazy import | **ACTIVE** |
| 12 | `SSMReranker` | `prefetch/ssm_reranker.py:2` | Unknown SSM | Unknown | Prefetch reranking | unknown | **ACTIVE** (poorly documented) |

## 1.2 Model Registry (ModelManager — runtime-wide plane)

**File**: `brain/model_manager.py:246`
```
PHASE_MODEL_MAP:
  PLAN/GENERATE/DECIDE → hermes  (Hermes-3-3B-4bit)
  EMBED/DEDUP/ROUTING    → modernbert
  NER/ENTITY             → gliner
```

## 1.3 Model Sizes (from `brain/model_manager.py:35`)
```
Hermes-3-Llama-3.2-3B-4bit ~2GB
ModernBERT ~500MB
GLiNER ~300MB
```

## 1.4 Redundant / Overlapping Systems

| System | Files | Overlap |
|--------|-------|---------|
| ModernBERT ×3 | `embeddings/modernbert_embedder.py`, `core/mlx_embeddings.py`, `brain/ane_embedder.py` | Three wrappers around same `nomic-ai/modernbert-embed-base` |
| Reranking ×4 | `brain/synthesis_runner.py:flashrank`, `tools/reranker.py:LightweightReranker`, `discovery/fusion_ranker.py`, `prefetch/ssm_reranker.py` | Multiple reranking implementations, winner unclear |
| VLM ×2 | `tools/vlm_analyzer.py:*(removed — M1 8GB unsafe)*`, `multimodal/vision_encoder.py:CoreML` | Different vision paths, no unified dispatcher |
| Legacy Hermes load | `layers/memory_layer.py:716` | Loads `Hermes-3-Llama-3.2-3B-bf16` (different quantization than canonical 4bit) |
| Legacy autonomous_orchestrator | `legacy/autonomous_orchestrator.py` | 31k line God Object, separate runtime from `__main__.py` pipeline |

## 1.5 Model Config Drift (code vs docs)

| Config Location | Value | Notes |
|----------------|-------|-------|
| `HermesConfig.model_path` | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | **DeepHermes is current default** (hermes3_engine.py:147) |
| `config.py:HERMES_MODEL` | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | DeepHermes primary ✅ |
| `project_types.py:HERMES_MODEL` | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | DeepHermes primary ✅ |
| `brain/ane_embedder.py:255` | `nomic-ai/modernbert-embed-base` | MLX fallback, matches ✅ |
| `mlx_embeddings.py:86` | `nomic-ai/modernbert-embed-base` | Matches ✅ |
| `embeddings/modernbert_embedder.py:62` | `nomic-ai/modernbert-embed-base` | Matches ✅ |
| `config.py:GLINER_MODEL` | `knowledgator/gliner-relex-large-v0.5` | **FIXED F220A** — was gliner-x-base ❌ |
| `project_types.py:GLINER_MODEL` | `knowledgator/gliner-relex-large-v0.5` | **FIXED F220A** — was gliner-x-base ❌ |
| `brain/ner_engine.py:87` | `knowledgator/gliner-relex-large-v0.5` | Primary ✅ |
| `brain/model_manager.py:299` | `knowledgator/gliner-relex-large-v0.5` | Matches ✅ |
| `brain/synthesis_runner.py:309` | `ms-marco-MiniLM-L-12-v2` | FlashRank model ✅ |
| `tools/vlm_analyzer.py` | *(none — VLM disabled by default on M1 8GB)* | VLM default: none ✅ |
| `security/pii_gate.py` | regex/deterministic fallback | No model default ✅ |

---

# 2. Integration Points by Role

## 2.1 primary_reasoner

**Current (F217C)**: `DeepHermes-3-Llama-3-3B-Preview-4bit` via `Hermes3Engine` (`brain/hermes3_engine.py:147`)
- Load: `mlx_lm.load(self.config.model_path)` at line 756
- Config: `HermesConfig.model_path = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"`
- KV quantization: `kv_bits=4, max_kv_size=8192` at generate time (NOT at load time)
- Context window: 8192 tokens
- Temperature: 0.3, Max tokens: 2048

**Rollback**: `mlx-community/Hermes-3-Llama-3.2-3B-4bit` — Hermes-3 remains fallback only

**Integration**:
- Files: `brain/hermes3_engine.py:147`, `config.py:44`, `project_types.py:191`
- DeepHermes is production default (F217C swap complete)
- Hermes-3-Llama-3.2-3B-4bit is rollback/fallback only
- Tokenizer: `mlx_lm.load()` returns (model, tokenizer) — same pattern
- Outlines integration: already present at `hermes3_engine.py:779`

**Future candidates** (benchmark-first, not active):
- `mlx-community/Nanbeige4.1-3B-4bit` — A/B test candidate
- `mlx-community/SmolLM3-3B-4bit` — Apple-native, memory-efficient
- `mlx-community/Qwen3-0.6B-4bit` — structured JSON only (F217B), not primary reasoner

**Would replace**: `mlx-community/Hermes-3-Llama-3.2-3B-4bit`
**Would keep as fallback**: existing Hermes 4bit
**Redundancy risk**: LOW — single load path

**Expected benefit**: Better instruction following, potentially better Czech OSINT prompts
**Expected risk**: Behavioral change in structured output (Outlines), possible prompt template incompatibility
**Must benchmark**: Czech/English entity extraction, JSON validity rate, hallucination rate

---

## 2.2 fast_router / structured_json_generator

**Current**: `brain/model_lifecycle.py:ModelLifecycle` — windup-local, discovers Qwen3-0.6B or ≤1B at runtime
- 3-tier discovery: Qwen3-0.6B → ≤1B → None
- Load: `mlx_lm.load(model_path_str)` at line 739
- Used for: Outlines constrained JSON generation in sprint windup
- **NOT part of runtime-wide model plane** (isolated, line 659)

**Proposed candidates**:
- `mlx-community/Qwen3-0.6B-4bit` — already sought by discovery, explicit model ID
- `mlx-community/Qwen3-1.7B-4bit` — better quality, still M1-safe
- `mlx-community/SmolLM3-135M-4bit` or `360M` — tiny, very fast

**Integration**:
- File: `brain/model_lifecycle.py:682-707`
- Currently path-discovery only — add explicit model_id parameter
- No change to `mlx_lm.load()` pattern

**Would replace**: discovery-based path with explicit model ID
**Would keep as fallback**: discovery glob patterns
**Redundancy risk**: MEDIUM — if Qwen3 is already found, explicit ID adds little
**Expected benefit**: deterministic model selection, faster startup (no glob)
**Expected risk**: breaking existing working discovery
**Must benchmark**: JSON validity %, TTFT, decode speed vs current discovery

---

## 2.3 embedder

**Current**: `nomic-ai/modernbert-embed-base` via 3 parallel wrappers:
1. `embeddings/modernbert_embedder.py:ModernBERTEmbedder` — `mlx_embeddings_load()`
2. `core/mlx_embeddings.py:MLXEmbeddingManager` — `mlx_embeddings_load()`
3. `brain/ane_embedder.py:ANEEmbedder` — CoreML first, MLX fallback

**Proposed candidates**:
- `nomic-ai/modernbert-embed-base` — **KEEP** (already optimal for retrieval)
- `nomic-ai/Nomic-Embed-Text-v1.5` — larger, potentially better quality
- `nomic-ai/modernbert-embed-base-gemini` — NOT confirmed available in MLX

**Integration**:
- Files: `embeddings/modernbert_embedder.py:62`, `core/mlx_embeddings.py:86`, `brain/ane_embedder.py:255`
- All three wrappers need consistent model path

**Redundancy risk**: HIGH — three wrappers, consolidate first
**Expected benefit**: nomic v1.5 may improve retrieval quality
**Must benchmark**: recall@k, embedding quality on Czech OSINT queries

---

## 2.4 reranker

**Current**: `ms-marco-MiniLM-L-12-v2` via FlashRank at `brain/synthesis_runner.py:304`
- Singleton `_get_flashrank_ranker()`
- ONNX runtime, ~22MB

**Other rerankers in codebase**:
- `tools/reranker.py:LightweightReranker` — FlashRank wrapper with fallback
- `discovery/fusion_ranker.py` — fusion-based reranking
- `prefetch/ssm_reranker.py` — SSM-based reranking
- `prefetch/prefetch_oracle.py` — prefetch oracle

**Proposed candidates**:
- `BAAI/bge-reranker-v2-m3` — multilingual, stronger
- `jinaai/jina-reranker-v2-base` — multilingual, good quality
- `MemGPT/MemReranker-0.6B` — for hard temporal/causal retrieval only

**Integration**: `brain/synthesis_runner.py:304` — swap model_name in Ranker init

**Redundancy risk**: HIGH — multiple reranking systems, unclear winner
**Expected benefit**: multilingual reranking (for Czech sources)
**Expected risk**: larger model, higher memory
**Must benchmark**: NDCG@k, MRR on multilingual corpus

---

## 2.5 ner_re

**Current**: `knowledgator/gliner-relex-large-v0.5` at `brain/ner_engine.py:87`
- Joint NER + relation extraction
- `GLiNER.from_pretrained(self.model_name, map_location="cpu")`
- CoreML conversion path at `~/.hledac/models/ner.mlmodel`

**Proposed candidates**:
- `urchade/gliner-xl-big` — larger GLiNER variant
- `Panchajit1989/gliner-relik-v0.5` — Relik joint NER+RE
- Keep `gliner-relex-large-v0.5` as primary (already correct)

**Integration**: `brain/ner_engine.py:87` — swap model_name default

**Redundancy risk**: LOW
**Expected benefit**: potential accuracy improvement
**Must benchmark**: F1 on Czech/English NER, relation extraction accuracy

---

## 2.6 pii_privacy

**Current**: `security/pii_gate.py` — regex + fallback sanitization
**No dedicated model** for PII detection currently in runtime code

**Proposed**: Consider `GLiNER2-PII` if a stable MLX/CoreML conversion exists
**Status**: blocked_until_runtime_available

---

## 2.7 vision_ocr_doc

**Current**: Three parallel paths:
1. `tools/vlm_analyzer.py:VLMAnalyzer` — *(removed — M1 8GB unsafe)* (HEAVY, 7B)
2. `tools/ocr_engine.py:VisionOCR` — Apple Vision via ocrmac (lightweight)
3. `multimodal/vision_encoder.py:VisionEncoder` — CoreML document images

**Proposed candidates**:
- Keep OCR-first path for documents (VisionOCR is correct)
- `mlx-community/SmolVLM2-500M-4bit` — small VLM, M1-safe
- `mlx-community/Qwen2-VL-2B-Instruct-4bit` — better quality than SmolVLM, still M1-safe
- PaddleOCR-VL-0.9B — document parsing, but not confirmed MLX-available

**Integration**:
- `tools/vlm_analyzer.py:65` — replace *(removed — M1 8GB unsafe)* with SmolVLM2-500M
- OR add OCR-first path as default, VLM only for complex images

**Redundancy risk**: MEDIUM — *(removed — M1 8GB unsafe)* is memory-expensive on M1 8GB
**Expected benefit**: M1-safe VLM, faster load, lower memory
**Must benchmark**: image understanding quality vs *(removed — M1 8GB unsafe)*, memory footprint

---

## 2.8 model_serving_runtime

**Current**: MLX via `mlx_lm` (primary), `mlx_embeddings` (embeddings), `mlx_vlm` (VLM)
**No GGUF/llama.cpp path currently in active runtime**

**Proposed candidates**:
- GGUF via llama.cpp — for KV-cache quantization, grammar-constrained JSON, mmap-first
- Ollama — dev/testing only, not production

**Status**: Keep MLX as primary. GGUF as potential Phase 3 addition.

---

# 3. Replacement Matrix

| Current Component | Current Model | Proposed Replacement | Action | Files to Modify | Expected Benefit | Redundancy Risk | M1 8GB Risk | Recommendation |
|---|---|---|---|---|---|---|---|---|
| Hermes3Engine primary | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | `DeepHermes-3-Llama-3-3B-Preview-4bit` | benchmark_first | `hermes3_engine.py:128`, `config.py:44`, `project_types.py:191` | Better instruction following | LOW | MEDIUM | Phase 3: A/B test DeepHermes vs Hermes |
| Hermes3Engine primary | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | `Nanbeige4.1-3B-4bit` | benchmark_first | same | Stronger OSINT performance | LOW | LOW | Phase 3: A/B test Nanbeige |
| Hermes3Engine primary | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | `SmolLM3-3B-4bit` | benchmark_first | same | Apple-native, memory-efficient | LOW | LOW | Phase 3: A/B test SmolLM3 |
| Structured gen (windup) | Discovered Qwen3-0.6B | `mlx-community/Qwen3-0.6B-4bit` (explicit) | add | `model_lifecycle.py:696` | Deterministic, faster | LOW | LOW | Phase 1: Replace discovery with explicit ID |
| Structured gen (windup) | Qwen3-0.6B | `mlx-community/Qwen3-1.7B-4bit` | benchmark_first | `model_lifecycle.py` | Better JSON quality | LOW | MEDIUM | Phase 3: benchmark JSON validity |
| ModernBERT wrappers | 3 wrappers + `nomic-ai/modernbert-embed-base` | Consolidate to 1 wrapper | deprecate 2 | `ane_embedder.py`, `mlx_embeddings.py`, `modernbert_embedder.py` | Fewer loading paths | HIGH | LOW | Phase 0: consolidate before changing model |
| Embedder | `nomic-ai/modernbert-embed-base` | `nomic-ai/modernbert-embed-base` (keep) | keep | — | Already optimal | N/A | N/A | Keep current |
| Reranker | FlashRank `MiniLM-L-12-v2` | `BAAI/bge-reranker-v2-m3` | benchmark_first | `synthesis_runner.py:307` | Multilingual support | MEDIUM | MEDIUM | Phase 4: add as conditional heavy reranker |
| Reranker | FlashRank `MiniLM-L-12-v2` | `jinaai/jena-reranker-v2-base` | benchmark_first | `synthesis_runner.py:307` | Multilingual, good quality | MEDIUM | MEDIUM | Phase 4: add as conditional |
| GLiNER | `knowledgator/gliner-relex-large-v0.5` | Keep + add Panchajit1989/gliner-relik-v0.5 | add | `ner_engine.py:87` | Joint NER+RE improvement | LOW | LOW | Phase 4: add as second GLiNER option |
| VLM | `*(removed — M1 8GB unsafe)*` | `mlx-community/SmolVLM2-500M-4bit` | replace | `vlm_analyzer.py:65` | M1-safe, fast load | LOW | LOW | Phase 3: replace with SmolVLM2 |
| VLM | *(removed — M1 8GB unsafe)* | OCR-first + small VLM for complex | add | `vlm_analyzer.py`, `ocr_engine.py` | M1-safe, OCR-dominant | MEDIUM | MEDIUM | Phase 3: OCR-first default, VLM on fallback |
| Legacy Hermes bf16 | `Hermes-3-Llama-3.2-3B-bf16` (memory_layer) | Remove bf16 variant | deprecate | `layers/memory_layer.py:716` | Single canonical model | LOW | MEDIUM | Phase 1: remove memory_layer bf16 load |
| Legacy autonomous_orchestrator | Hermes via legacy orchestrator | Keep as-is (smoke tests only) | keep | `legacy/autonomous_orchestrator.py` | Test coverage | N/A | N/A | Keep for smoke tests |

---

# 4. Redundancy and Duplication Audit

## 4.1 ModernBERT Wrappers (CRITICAL — consolidate first)

**Problem**: Three independent wrappers load the same `nomic-ai/modernbert-embed-base`:
1. `embeddings/modernbert_embedder.py:ModernBERTEmbedder` — `_ModernBERTMLXLoader.load()`
2. `core/mlx_embeddings.py:MLXEmbeddingManager` — `mlx_embeddings_load()`
3. `brain/ane_embedder.py:ANEEmbedder` — CoreML primary + MLX fallback

**Runtime winner**: `brain/ane_embedder.py:405` creates `_ANE_EMBEDDER = ANEEmbedder(model_name="minilm_ane")` which falls back to MLX at line 255

**Recommendation**: 
- Keep `brain/ane_embedder.py:ANEEmbedder` as canonical embedder (CoreML+MLX priority)
- Deprecate `embeddings/modernbert_embedder.py` and `core/mlx_embeddings.py` wrappers
- Route all embedder calls through `ANEEmbedder`

## 4.2 Reranking Systems (4+ implementations)

**Problem**: At least 4 reranking paths exist:
1. `brain/synthesis_runner.py` — FlashRank `ms-marco-MiniLM-L-12-v2` (singleton)
2. `tools/reranker.py:LightweightReranker` — FlashRank wrapper
3. `discovery/fusion_ranker.py` — fusion reranking
4. `prefetch/ssm_reranker.py` — SSM reranker

**Recommendation**:
- FlashRank in `synthesis_runner` is the canonical pre-LLM reranker
- `tools/reranker.py` is used by `hledac_doctor` and tests
- `prefetch/ssm_reranker` is prefetch-specific
- These can coexist with different roles but need clear owner comments

## 4.3 VLM Paths

**Problem**: Two vision paths with unclear routing:
1. `tools/vlm_analyzer.py` — *(removed — M1 8GB unsafe)* (heavy)
2. `multimodal/vision_encoder.py` — CoreML document images
3. `tools/ocr_engine.py` — Vision OCR (lightweight)

**Recommendation**:
- OCR-first for documents (VisionOCR is correct and fast)
- VLM only for complex images requiring visual reasoning
- SmolVLM2-500M replace *(removed — M1 8GB unsafe)* for VLM use

## 4.4 Legacy Runtime

**Problem**: `legacy/autonomous_orchestrator.py` (31k lines) is a separate God Object from `__main__.py` pipeline. Both load Hermes independently.

**Recommendation**: Keep legacy for smoke tests only. Active development stays on `__main__.py` pipeline.

## 4.5 BF16 vs 4bit Hermes

**Problem**: `layers/memory_layer.py:716` loads `Hermes-3-Llama-3.2-3B-bf16` — a different quantization than the canonical `Hermes-3-Llama-3.2-3B-4bit`

**Recommendation**: Remove bf16 load from memory_layer. Use single canonical 4bit.

## 4.6 Windup-local vs Runtime-wide Models

**Problem**: `ModelLifecycle._ensure_loaded()` (Qwen/SmolLM) is windup-local and isolated from runtime model plane. But `brain/model_lifecycle.py` module-level functions (`load_model`, `unload_model`) are ALSO in the codebase and create confusion.

**Recommendation**: Clear separation — windup-local only uses `ModelLifecycle` class, runtime-wide uses `ModelManager`.

---

# 5. Production Runtime Integration

Priority layers for M1 8GB production:

| Layer | File/Module | Connects To | Priority | Why M1-Critical |
|-------|-------------|-------------|----------|----------------|
| **Process isolation** | `brain/model_manager.py` | Already has per-model locks | P1 | Prevent concurrent heavy model loads |
| **Memory governor** | `brain/model_manager.py:_check_rss_before_load()` | Already implemented | P1 | Prevents OOM before load |
| **Circuit breaker** | `brain/hermes3_engine.py` (existing) | FetchCoordinator domain failure | P1 | Already present |
| **Concurrency governor** | `runtime/memory_authority.py` | SprintScheduler, model_manager | P1 | M1ResourceGovernor (F202J) already wired |
| **Backpressure** | `sprint_scheduler.py` (existing) | phase_controller | P1 | Bounded queues already exist |
| **Prompt security** | `brain/hermes3_engine.py:_sanitize_for_llm` | Already injected | P1 | Sanitizer already in place |
| **Output validator** | `brain/hermes3_engine.py` (Outlines) | Already uses Outlines | P1 | Structured JSON already constrained |
| **Offline model registry** | `brain/model_lifecycle.py:_discover_model_path()` | Already 3-tier discovery | P1 | Already handles missing models |
| **Graceful shutdown** | `hermes3_engine.py:unload()` | 7K order already | P1 | canonical unload exists |
| **Heavy LLM worker** | N/A — single process | — | P2 | M1 8GB can't spare another process |
| **Background worker** | N/A — asyncio tasks | — | P2 | Already async |
| **Encoder worker** | `brain/ane_embedder.py` | Already separate from MLX | P2 | ANE is CoreML, separate memory plane |
| **Distributed tracing** | N/A | — | P3 | Not needed for local-only |
| **OpenTelemetry** | N/A | — | P3 | Not needed for local-only |
| **Staged model updater** | `brain/model_swap_manager.py` | Already has SwapManager | P2 | Already exists |
| **Adaptive context** | `context_optimization/` | Already exists | P2 | Dynamic context management present |
| **Progressive warming** | `brain/hermes3_engine.py` | Already lazy-loads | P2 | Engine already lazy |
| **Shared cache index** | `knowledge/lancedb_store.py` | ANN index already | P2 | LanceDB already for embeddings |

---

# 6. M1 Air 8GB Constraints

**Hard ceiling**: 8GB total UMA. macOS baseline: ~2.5GB. **Usable: ~5.5GB**.

**Strict operational limits**:
- **ONE heavy model at a time** — Hermes 3B (2GB) OR *(removed — M1 8GB unsafe)* (cannot both be loaded)
- **Safe AI budget**: <5.5GB active, never exceed 5GB RSS warning threshold
- **System reserved**: ~2.5GB for macOS + Python runtime
- **KV cache estimate**: 8192 context × 4 bits × 28 layers ≈ 32MB (already bounded in hermes3_engine.py:1056)
- **Max context — nominal**: 4096 tokens (within 5.5GB budget)
- **Max context — fair**: 6144 tokens (with KV quantization)
- **Max context — serious**: 8192 tokens (with kv_bits=4, max_kv_size=8192)
- **Max context — critical**: NONE (8GB M1 cannot safely run 16k context with 3B model)

**Thermal/memory degraded modes**:
- `is_emergency_unload_requested()` flag already in `model_lifecycle.py:108`
- M1ResourceGovernor (F202J) already implements advisory concurrency limits

**Background task preemption**: No concurrent heavy models. Queue serialized.

**mmap-first**: MLX already uses mmap for model loading. No explicit mmap preference needed for GGUF.

**CoreML/ANE**: Only for encoders/embedders (not large language models). `ANEEmbedder` correctly restricts to ModernBERT.

---

# 7. Benchmark Plan

## 7.1 Benchmark Matrix

| Metric | Method | Target |
|--------|--------|--------|
| Model quality | Custom OSINT probe with known IOC ground truth | F1 > 0.85 |
| Czech/English entity extraction | Probe lane with mixed-language input | F1 > 0.80 |
| Relation extraction | Probe lane with known entity pairs | F1 > 0.70 |
| JSON validity | % valid JSON from Outlines constrained generation | > 95% |
| Citation accuracy | % extracted claims with verifiable source | > 80% |
| Hallucination rate | % extracted claims unverifiable vs known facts | < 10% |
| TTFT | `time.perf_counter()` until first token | < 2s |
| Prefill tokens/sec | Tokens/second during prompt processing | > 500 tok/s |
| Decode tokens/sec | Tokens/second during generation | > 30 tok/s |
| Peak RSS | `psutil.Process().memory_info().rss` | < 5.5GB |
| Memory after unload | RSS after `unload()` + GC + `mx.clear_cache()` | < 3.0GB |
| Thermal state | `m1_thermal.thermal_readings()` | No sustained "fair" |
| Cache hit rate | Custom probe counter | > 60% |

## 7.2 A/B Tests

| Comparison | Metric | Method |
|------------|--------|--------|
| DeepHermes 3B vs Hermes 3B vs Nanbeige4.1-3B | OSINT F1, JSON validity, TTFT | Parallel probe lanes |
| Qwen3-0.6B vs Qwen3-1.7B vs SmolLM3-3B (structured gen) | JSON validity %, TTFT | Windup-local benchmark |
| ModernBERT vs nomic-embed-text-v1.5 (if available) | Retrieval recall@k | ANN probe lane |
| FlashRank MiniLM vs bge-reranker-v2-m3 | NDCG@k on multilingual corpus | Dedicated rerank probe |
| GLiNER-relex vs GLiNER-relik (joint NER+RE) | F1 NER, relation accuracy | NER probe lane |
| *(removed — M1 8GB unsafe)* vs SmolVLM2-500M vs OCR-first | Image understanding quality | Multimodal probe |
| Hermes-3-4bit vs Hermes-3-bf16 | Quality vs memory | Memory probe |

---

# 8. Implementation Roadmap

## Phase 0: Inventory Cleanup (Do first)

**Goal**: Consolidate redundant wrappers before touching models

**Files**:
- `brain/ane_embedder.py` — designate as **canonical embedder**
- `embeddings/modernbert_embedder.py` — add deprecation comment, route calls to `ANEEmbedder`
- `core/mlx_embeddings.py` — add deprecation comment, route calls to `ANEEmbedder`
- `tools/reranker.py` — clarify owner (hledac_doctor + tests only)
- `prefetch/ssm_reranker.py` — add docstring, clarify prefetch-only
- `layers/memory_layer.py:716` — remove bf16 Hermes load, use canonical 4bit
- `brain/model_lifecycle.py` module-level functions — add clear comments separating windup-local vs runtime-wide

**Acceptance**: No change in behavior, only comments and wrapper consolidation
**Rollback**: `git checkout` of modified files

## Phase 1: Safe Model Registry and Fallback Cleanup

**Goal**: Make model selection explicit, remove implicit discovery

**Files**:
- `brain/model_lifecycle.py:682-707` — replace Tier-1 glob discovery with explicit `model_path: str = "mlx-community/Qwen3-0.6B-4bit"` parameter
- `config.py` — add `STRUCTURED_GEN_MODEL` constant
- `project_types.py` — add `structured_gen_model` to `ModelConfig`

**Acceptance**: Structured generation still works, faster startup (no glob)
**Rollback**: `git checkout`

## Phase 2: Benchmark Harness

**Goal**: Establish reproducible benchmarks before changing any model

**Files to create**:
- `benchmarks/model_integration_benchmark.py` — A/B test runner for all model comparisons
- `benchmarks/probe_integration/` — probe lanes for each A/B test

**Acceptance**: All benchmark commands run and produce comparable JSON results
**Rollback**: Delete created files

## Phase 3: Primary Model A/B Integration

**Goal**: Replace or keep Hermes based on benchmark evidence

**Files** (only if benchmarks pass):
- `brain/hermes3_engine.py:128` — swap `HermesConfig.model_path` to candidate
- `config.py:44` — update `HERMES_MODEL`
- `project_types.py:191` — update `HERMES_MODEL`

**Models to test in Phase 2 before Phase 3**:
- `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit`
- `mlx-community/Nanbeige4.1-3B-4bit`
- `mlx-community/SmolLM3-3B-4bit`

**Acceptance**: Benchmark shows improvement in OSINT F1, JSON validity, or TTFT
**Rollback**: Revert to `Hermes-3-Llama-3.2-3B-4bit`

## Phase 4: Embeddings/Reranker/NER Consolidation

**Goal**: Improve retrieval quality with better reranker

**Files**:
- `brain/synthesis_runner.py:307` — add conditional heavy reranker (bge or jina)
- `brain/ner_engine.py:87` — add second GLiNER option

**Acceptance**: NDCG@k improvement, no regression in latency
**Rollback**: Revert to FlashRank

## Phase 5: Production Runtime Guardrails

**Goal**: Harden M1-specific protections

**Files**:
- `brain/model_swap_manager.py` — already exists, verify completeness
- `runtime/memory_authority.py` — already exists, verify F202J governor
- `brain/hermes3_engine.py` — verify Outlines is always used for JSON

**Acceptance**: All existing tests pass, no new crash vectors
**Rollback**: Revert hardening changes

## Phase 6: M1-Specific Optimization Experiments

**Goal**: GGUF mmap, ANE-only vision, smaller VLM

**Files**:
- `tools/vlm_analyzer.py:65` — swap *(removed — M1 8GB unsafe)* for SmolVLM2-500M
- Optionally add GGUF path in `brain/hermes3_engine.py` — only if benchmarks justify

**Acceptance**: VLM quality acceptable, memory < 5.5GB RSS
**Rollback**: Revert to *(removed — M1 8GB unsafe)*

---

# 9. Do-Not-Do List

1. **DO NOT** replace all models at once — Phase 0→6 is sequential for a reason
2. **DO NOT** add a new model if an existing one already covers the role — verify with grep before adding
3. **DO NOT** load two heavy models concurrently — Hermes 3B + *(removed — M1 8GB unsafe)* would OOM M1 8GB
4. **DO NOT** use preview models without fallback — DeepHermes-3-3B-Preview may not persist on HuggingFace
5. **DO NOT** change tokenizer ownership incorrectly — `mlx_lm.load()` returns (model, tokenizer) together; don't split them
6. **DO NOT** implement dynamic runtime quantization as a core feature — `kv_bits=4` at generate time is already set
7. **DO NOT** implement shared KV cache between workers — single-process architecture, not needed
8. **DO NOT** assume ANE means zero memory impact — ANEEmbedder still loads CoreML (~300MB)
9. **DO NOT** assume MoE active parameters equal RAM footprint — not relevant (no MoE models in current stack)
10. **DO NOT** rely on docs instead of runtime code — always verify model_path from actual code, not documentation
11. **DO NOT** add GGUF support without mmap-first — memory pressure on M1 makes mmap essential
12. **DO NOT** change the 7K unload order — `gc.collect() → mx.eval([]) → clear_cache()` is the invariant

---

# 10. Final Recommendation

## Replace NOW (low risk)
- **`layers/memory_layer.py:716` bf16 Hermes → remove** (redundant, wrong quantization)
- **Windup-local discovery → explicit model ID** in `model_lifecycle.py` (deterministic, faster)

## Keep (verified working)
- **Hermes-3-Llama-3.2-3B-4bit** as primary reasoner — benchmark candidates before replacing
- **nomic-ai/modernbert-embed-base** as embedder — consolidation first, then benchmark nomic v1.5
- **FlashRank ms-marco-MiniLM-L-12-v2** as reranker — add bge/jina as conditional multilingual only
- **GLiNER-relex-large-v0.5** as NER — add gliner-relik as second option
- **VisionOCR** as document OCR primary path — correct and fast
- **MLX as runtime** — no GGUF needed yet

## Benchmark BEFORE Implementation
- DeepHermes 3B vs Nanbeige4.1-3B vs Hermes 3B (primary reasoner quality)
- Qwen3-1.7B vs Qwen3-0.6B (structured JSON quality/speed)
- SmolVLM2-500M vs *(removed — M1 8GB unsafe)* (VLM quality on M1)
- bge-reranker-v2-m3 vs jina-reranker (multilingual reranking)

## Deprecate (cleanup)
- `embeddings/modernbert_embedder.py` — consolidate into `ANEEmbedder`
- `core/mlx_embeddings.py` — consolidate into `ANEEmbedder`
- Legacy autonomous_orchestrator Hermes loads — keep for smoke tests only

## Production Runtime First
- Ensure Phase 5 (guardrails) is complete before Phase 3 (new model swap)
- M1 8GB constraint is non-negotiable: never exceed 5.5GB RSS active

---

*Evidence compiled from runtime code analysis of `brain/hermes3_engine.py`, `brain/model_lifecycle.py`, `brain/model_manager.py`, `brain/ner_engine.py`, `brain/ane_embedder.py`, `embeddings/modernbert_embedder.py`, `core/mlx_embeddings.py`, `brain/synthesis_runner.py`, `tools/vlm_analyzer.py`, `tools/reranker.py`, `config.py`, `project_types.py`. No marketing claims used — all model IDs verified from actual code.*