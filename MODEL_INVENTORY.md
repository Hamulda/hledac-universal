# Model Inventory — Hledac Universal

> **Generated:** 2026-05-13
> **Scope:** All AI/ML/NLP/VLM/embedding/reranking/model-like components
> **Project root:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`

---

## Canonical Model Table

| # | Model / Component | Type | Backend | Files (lines) | Connected To | Purpose | Lifecycle | Memory | Tokenizer | Status |
|---|-------------------|------|---------|---------------|--------------|---------|-----------|--------|-----------|--------|
| 1 | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | LLM | mlx-lm | `brain/hermes3_engine.py:128` (config), `brain/hermes3_engine.py:756` (load), `config.py:44`, `project_types.py:191` | Hermes3Engine, ModelManager (PLAN/DECIDE/SYNTHESIZE phases) | Decision making, structured JSON generation, ChatML format | Lazy-loaded via Hermes3Engine.load(), 7K unload sequence | ~2.0GB, KV: max_kv_size=8192, kv_bits=4 at generate time | Own tokenizer via `mlx_lm.load()` | **RUNTIME ACTIVE** |
| 2 | `nomic-ai/modernbert-embed-base` | Embedding | mlx-embeddings | `embeddings/modernbert_embedder.py:62` (default), `core/mlx_embeddings.py:86` (DEFAULT_MODEL), `utils/semantic.py:110` (DEFAULT_MODEL) | ModernBERTEmbedder, MLXEmbeddingManager, SemanticFilter | Text embeddings (768d) for dedup, routing, search | Lazy-loaded (lazy_load=True default) | ~0.5GB | Own via mlx-embeddings processor | **RUNTIME ACTIVE** |
| 3 | `knowledgator/gliner-x-base` | NER+RE | GLiNER (CPU/PyTorch) | `config.py:46` (config), `brain/model_manager.py:299` (DEFAULT_MODEL for relex) | ModelManager (NER/ENTITY phase) | Entity extraction, relation extraction | Loaded by ModelManager acquire_model_ctx | ~0.3GB | GLiNER owns tokenizer | **RUNTIME ACTIVE** |
| 4 | `ms-marco-MiniLM-L-12-v2` | Reranker | FlashRank/ONNX | `tools/reranker.py:65` (default), `brain/synthesis_runner.py:309` (singleton) | LightweightReranker, SynthesisRunner._rerank_pass() | Cross-encoder reranking before LLM synthesis | Singleton, loaded once per process | ~4MB | FlashRank internal | **RUNTIME ACTIVE** |
| 5 | `mlx-community/Qwen2.5-0.5B-Instruct-4bit` | LLM (windup) | mlx-lm | `brain/synthesis_runner.py:1075` (Tier 3 primary) | SynthesisRunner via windup_engine lifecycle | Windup-local structured generation (secondary plane) | Windup-local, 3-tier discovery (cache→filesystem→download) | ~400MB | Own via mlx_lm | **RUNTIME ACTIVE (windup)** |
| 6 | `mlx-community/SmolLM2-135M-Instruct-4bit` | LLM (windup fallback) | mlx-lm | `brain/synthesis_runner.py:1076` (Tier 3 fallback) | SynthesisRunner via windup_engine lifecycle | Fallback if Qwen download fails | Windup-local | ~70MB | Own | **FALLBACK ONLY** |
| 7 | `*(removed — M1 8GB unsafe)*` | VLM | mlx-vlm | `tools/vlm_analyzer.py:64` | VLMAnalyzer singleton | Image understanding via mlx-vlm | Singleton, lazy-loaded on first analyze_image_vlm() | uncertain | Own processor | **RUNTIME ACTIVE** |
| 8 | VisionEncoder (CoreML artifact, path via model_path param) | VLM image encoder | CoreML/coremltools | `multimodal/vision_encoder.py:31-64` | MultimodalEnricher | Image→embedding for multimodal enrichment | Lazy-loaded via run_in_executor | ~300MB CoreML (if compiled) | None | **RUNTIME ACTIVE (optional CoreML)** |
| 9 | MambaFusion (MLX nn.Mamba or MLP fallback) | Fusion model | MLX nn | `multimodal/fusion.py:23-80` | MultimodalEnricher._fusion_model | Fuses (vision, text, graph) embeddings | Optional, loaded with vision path | uncertain | None (MLP/Mamba) | **RUNTIME ACTIVE (optional)** |
| 10 | `mobileclip_s0` | CLIP | mobileclip | `multimodal/fusion.py:125`, `multimodal/analyzer.py:463` | MultimodalEnricher (optional) | Text↔image similarity scoring | Optional, loaded on demand | uncertain | Own tokenizer | **OPTIONAL** |
| 11 | `BAAI/bge-small-en-v1.5` | Embedding (fallback) | fastembed | `knowledge/rag_engine.py:1021` | RAGEngine._fastembed_embedder | Fallback text embedder for RAG | Initialized on RAGEngine init | uncertain (384d) | fastembed internal | **FALLBACK ONLY** |
| 12 | Draft model (unnamed, via mlx_lm draft_model param) | Speculative decoding | mlx-lm | `brain/hermes3_engine.py:193-194` (_draft_model_obj), `brain/hermes3_engine.py:764-802` (_init_draft_model) | Hermes3Engine | Speculative decoding draft (Sprint 75) | Optional, loaded post-main-model | uncertain | uncertain | **DRAFT/UNFINISHED** |
| 13 | `microsoft/trocr-base-handwritten` | OCR | trocr (HuggingFace) | `project_types.py:344` (ocr_model field) | uncertain (referenced in types, not in runtime) | OCR for handwritten CAPTCHA/text | uncertain | uncertain | trocr tokenizer | **DOC-ONLY/UNVERIFIED** |

---

## A. Canonical Runtime Model Map

The **canonical 3-model runtime plane** is governed by `brain/model_manager.py` and phase routing in `brain/model_phase_facts.py`.

### Model Plane (Layer 1 — Workflow-level)

| Phase | Model Key | Model Type | Canonical File |
|-------|----------|-----------|---------------|
| PLAN | `hermes` | LLM (Hermes-3) | `brain/hermes3_engine.py` |
| DECIDE | `hermes` | LLM (Hermes-3) | `brain/hermes3_engine.py` |
| SYNTHESIZE | `hermes` | LLM (Hermes-3) | `brain/hermes3_engine.py` |
| EMBED | `modernbert` | Embedding (ModernBERT) | `embeddings/modernbert_embedder.py` + `core/mlx_embeddings.py` |
| DEDUP | `modernbert` | Embedding (ModernBERT) | `embeddings/modernbert_embedder.py` + `utils/deduplication.py` |
| ROUTING | `modernbert` | Embedding (ModernBERT) | `embeddings/modernbert_embedder.py` |
| NER | `gliner` | NER+RE (GLiNER) | `brain/model_manager.py:_create_gliner_engine()` |
| ENTITY | `gliner` | NER+RE (GLiNER) | `brain/model_manager.py:_create_gliner_engine()` |

### Ownership Truth

| Truth Source | Location | What It Defines |
|-------------|----------|----------------|
| Canonical 3-model registry | `brain/model_manager.py:126-133` (ModelName, ModelType) | hermes/modernbert/gliner keys |
| Phase→model map | `brain/model_manager.py:253-254` (PHASE_MODEL_MAP) | NER→gliner, EMBED→modernbert |
| Model sizes | `brain/model_manager.py:35-40` (_MODEL_SIZES_GB) | hermes=2.0GB, modernbert=0.5GB, gliner=0.3GB |
| Config defaults | `config.py:44-46` (M1Presets) | HERMES/MODERNBERT/GLINER_MODEL |
| Type aliases | `project_types.py:188-200` (ModelConfig) | HERMES/MODERNBERT/GLINER_MODEL constants |

### Model Loading/Unloading Authority

- **Load authority:** `ModelManager.acquire_model_ctx()` — single entry point for runtime model acquisition
- **Unload authority (7K SSOT):** `engine.unload()` via `Hermes3Engine.unload()` — NOT model_lifecycle.py module-level helpers
- **Lazy init:** mlx-lm loaded only when first needed (Hermes3Engine.load())
- **Tokenizers:** owned by each model engine instance, not shared globally

---

## B. Sidecar / Secondary Model Planes

### Windup-Local Plane (Layer 3)

**Isolated from runtime-wide plane** per `brain/model_lifecycle.py:35` and `brain/model_phase_facts.py:43`.

| Model | Discovery | Files |
|-------|-----------|-------|
| Qwen2.5-0.5B-Instruct-4bit (primary) | Tier 1: `self._cached_model_path`, Tier 2: `~/.cache/huggingface/hub/**/Qwen*0.5B*/config.json`, Tier 3: download | `brain/synthesis_runner.py:1040-1103` |
| SmolLM2-135M-Instruct-4bit (fallback) | Same discovery, lower priority (0.2 vs 1.0) | `brain/synthesis_runner.py:1076` |

**Owner:** `SynthesisRunner._ensure_model()` + `ModelLifecycle._ensure_loaded()`

### FlashRank Reranker Singleton

| Model | File | Line | Usage |
|-------|------|------|-------|
| ms-marco-MiniLM-L-12-v2 | `brain/synthesis_runner.py` | 309 | `_get_flashrank_ranker()` singleton |
| ms-marco-MiniLM-L-12-v2 | `tools/reranker.py` | 65 | `LightweightReranker.__init__()` |

**Owner:** `tools/reranker.py` (factory) and `brain/synthesis_runner.py` (synthesis reranking pass)

### VLM Analyzer

| Model | File | Line |
|-------|------|------|
| *(removed — M1 8GB unsafe)* | `tools/vlm_analyzer.py` | 64 |

**Owner:** `VLMAnalyzer` singleton class

### ANE/CoreML Embedder

| Component | File | Line |
|-----------|------|------|
| CoreML path | `brain/ane_embedder.py` | 224 |
| ModernBERT fallback (MLX) | `brain/ane_embedder.py` | 255 |

**Owner:** `ANEEmbedder` in `brain/ane_embedder.py`

### Multimodal Models

| Model | File | Purpose |
|-------|------|---------|
| VisionEncoder (CoreML) | `multimodal/vision_encoder.py:31-64` | Image→embedding |
| MambaFusion (MLX) | `multimodal/fusion.py:23-80` | (vision,text,graph) fusion |
| mobileclip_s0 | `multimodal/fusion.py:125` | Text↔image similarity |

**Owner:** `MultimodalEnricher` in `multimodal/analyzer.py`

### Draft Model (Speculative Decoding)

| Component | File | Lines |
|-----------|------|-------|
| _draft_model_obj | `brain/hermes3_engine.py` | 193-194 |
| _init_draft_model() | `brain/hermes3_engine.py` | 764-802 |

**Status:** Sprint 75 feature, implementation appears incomplete (checks for stream_generate draft_model param, but no explicit model path)

---

## C. Duplicates, Conflicts, and Drift

### C1. GLiNER Model Name Conflict (CRITICAL)

| Location | Model | File | Line |
|----------|-------|------|------|
| config.py | `knowledgator/gliner-x-base` | `config.py` | 46 |
| project_types.py | `knowledgator/gliner-x-base` | `project_types.py` | 200 |
| model_manager.py | `knowledgator/gliner-relex-large-v0.5` | `brain/model_manager.py` | 299 |

**Analysis:** config.py and project_types.py both say `gliner-x-base` (base NER only), but `model_manager.py:_create_gliner_engine()` loads `gliner-relex-large-v0.5` which includes relation extraction. The runtime actually uses the relex-large model (line 299), making the config.py value a **doc-only placeholder** that is not used at runtime.

**Likely winner:** `knowledgator/gliner-relex-large-v0.5` (model_manager.py:299 — active code path)

**Recommendation:** Update config.py and project_types.py to `knowledgator/gliner-relex-large-v0.5` or add a `GLINER_RELEX_MODEL` separate from `GLINER_MODEL`.

---

### C2. ModernBERT Model Name Drift (HIGH)

| Location | Model | Dim | File | Line |
|----------|-------|-----|------|------|
| config.py | `mlx-community/answerdotai-ModernBERT-base-6bit` | 6bit | `config.py` | 45 |
| project_types.py | `mlx-community/answerdotai-ModernBERT-base-6bit` | 6bit | `project_types.py` | 196 |
| embeddings/modernbert_embedder.py | `nomic-ai/modernbert-embed-base` | full precision | `embeddings/modernbert_embedder.py` | 62 |
| core/mlx_embeddings.py | `nomic-ai/modernbert-embed-base` | full precision | `core/mlx_embeddings.py` | 86 |
| utils/semantic.py | `mlx-community/answerdotai-ModernBERT-base-6bit` | 6bit | `utils/semantic.py` | 110 |
| brain/ane_embedder.py (MLX fallback) | `nomic-ai/modernbert-embed-base` | full precision | `brain/ane_embedder.py` | 255 |
| utils/deduplication.py | `nomic-ai/nomic-embed-text-v1.5` | different model | `utils/deduplication.py` | 73 |

**Analysis:** Four distinct model IDs across 7 locations:
1. `mlx-community/answerdotai-ModernBERT-base-6bit` (6bit quantized) — config + semantic
2. `nomic-ai/modernbert-embed-base` (full precision) — embedder + ane_embedder fallback
3. `nomic-ai/nomic-embed-text-v1.5` — dedup-specific embedding (different model!)

**Likely winner for embedding pipeline:** `nomic-ai/modernbert-embed-base` (used by MLXEmbeddingManager which is canonical for the EMBED phase)

**Recommendation:** 
1. Consolidate on `mlx-community/answerdotai-ModernBERT-base-6bit` everywhere OR `nomic-ai/modernbert-embed-base` everywhere — pick one
2. Clarify whether dedup's `nomic-embed-text-v1.5` is intentional or should use the same model as the main embedding pipeline

---

### C3. RAG Fallback Embedding (MEDIUM)

| Location | Model | Dim | File | Line |
|----------|-------|-----|------|------|
| knowledge/rag_engine.py | `BAAI/bge-small-en-v1.5` | 384d | `knowledge/rag_engine.py` | 1021 |

**Analysis:** Different embedder (fastembed/bge-small) used as RAG fallback vs the 768d ModernBERT used everywhere else. This is intentional (fastembed is lighter) but undocumented as the fallback hierarchy.

**Recommendation:** Document the fallback hierarchy: MLX ModernBERT → fastembed/bge-small → hash.

---

### C4. FlashRank Model Consistency (LOW)

Both `tools/reranker.py:65` and `brain/synthesis_runner.py:309` use `ms-marco-MiniLM-L-12-v2`. **No conflict** — these are the same model, just instantiated differently (factory vs singleton).

---

### C5. Captcha Solver YOLO Model (UNVERIFIED)

`captcha_solver.py:90` mentions "YOLO CoreML model for grid CAPTCHAs" but no model path is specified in code. The file only references `VNCoreMLModel` from Apple Vision framework. **No verified model ID.**

---

### C6. OCR Model (DOC-ONLY)

`project_types.py:344` defines `ocr_model: str = "microsoft/trocr-base-handwritten"` but grep shows no runtime usage in the current codebase. **Status: uncertain.**

---

### C7. config.py qwen3-1.7b Summarization (UNUSED)

`config.py:221` defines `summarization_model: str = "qwen3-1.7b"` but grep shows no active code using this. **Doc-only/reference.**

---

## D. Dead / Archived / Doc-Only Models

### D1. Archived Code

| File | Status | Notes |
|------|--------|-------|
| `archive/federated_osint_v1/model_store.py` | **ARCHIVED** | Federated OSINT v1, not runtime active |
| `archive/federated_osint_v1/secure_aggregator.py` | **ARCHIVED** | Federated learning, not runtime active |
| `brain/hermes3_engine.py.bak_F219B_HERMES_METAL_FINALIZER` | **ARCHIVED** | Old Hermes backup, contains draft model code |
| `intelligence/decision_engine.py` | **DELETED** (shown in git status as D) | Was in git index, now deleted |
| `model_lifecycle.py` | **DELETED** (shown in git status as D) | Was in git index, now deleted |
| `utils/worker_pool.py` | **DELETED** (shown in git status as D) | Was in git index, now deleted |
| `tests/test_phase1a_orchestration/` | **DELETED** (6 test files shown as D in git status) | Phase 1a orchestration tests removed |

### D2. Doc-Only / Plan References

| Reference | File | Status |
|-----------|------|--------|
| GLiNER in `.qoder/repowiki/en/content/Knowledge Management/` | `.qoder/repowiki/` | **DOC-ONLY** — wiki content, not runtime verified |
| Model Management wiki | `.qoder/repowiki/` | **DOC-ONLY** |
| `qwen3-1.7b` | `config.py:221` | **UNUSED** — no runtime reference found |
| `microsoft/trocr-base-handwritten` | `project_types.py:344` | **UNVERIFIED** — no runtime reference found |

### D3. Deprecated Compatibility Wrappers

| Class | File | Status | Notes |
|-------|------|--------|-------|
| `Model2VecEmbedding` | `utils/semantic.py:637` | **DEPRECATED** | Comment says "Use ModernBERTEmbedding from hledac.embeddings.modernbert_embedder directly" |
| `SentenceTransformerEmbedding` | `utils/semantic.py:655` | **DEPRECATED** | Comment says "Use ModernBERTEmbedding directly" |

---

## E. Memory Architecture Summary

### M1 8GB UMA Budget (from `M1_8GB_MEMORY_BUDGET.md`)

```
Total:     8GB
macOS:    -2.5GB
Available: 5.5GB

Peak allocation:
  LLM weights:     2.0GB  ← Hermes-3
  KV cache:         0.5GB  ← quantized (kv_bits=4 at generate time)
  Coordinators:     0.2GB
  NER/GNN/Embedder:  0.2GB
  Python heap:      0.3GB
  Findings:         0.01GB
               -------
  Peak:            ~3.2GB
```

### Hard Bounds Protecting Memory

| Bound | Value | Location |
|-------|-------|----------|
| MAX_HYPOTHESES | 500 | `hypothesis_engine.py:428` |
| MAX_PIVOTS | 20 | `hypothesis_engine.py` |
| MAX_CLAIMS | 5000 | `atomic_storage.py` |
| max_kv_size | 8192 | `hermes3_engine.py:1056` |
| kv_bits | 4 | `hermes3_engine.py:1057` |
| ANE RAM guard (>85%) | blocks vision | `multimodal/analyzer.py` |

### 7K Unload Sequence (Canonical SSOT)

`model_lifecycle.py:587-604` (via `engine.unload()` → Hermes3Engine):
1. `gc.collect()` — Python heap
2. `mx.eval([])` — GPU queue drain (F179C invariant)
3. `mx.metal.clear_cache()` — Metal memory
4. Second `gc.collect()` after clear_cache

**Aggressive mode:** `mx.metal.set_cache_limit(64MB)` → `clear_cache()` → restore to 2.5GB

---

## F. Tokenizer Ownership

| Model | Tokenizer Owner | Usage |
|-------|---------------|-------|
| Hermes-3-Llama-3.2-3B-4bit | Hermes3Engine (via `mlx_lm.load()`) | ChatML formatting, generation, prefix cache |
| ModernBERT (nomic-ai/modernbert-embed-base) | MLXEmbeddingManager / ModernBERTEmbedder (via mlx-embeddings processor) | Task prefixes (SEARCH_QUERY/SEARCH_DOCUMENT), embedding tokenization |
| GLiNER | GLiNER library (internal) | NER tokenization |
| Qwen2.5-0.5B-Instruct-4bit | ModelLifecycle via `_ensure_loaded()` | Windup synthesis |
| SmolLM2-135M-Instruct-4bit | Same as Qwen | Windup fallback |
| *(removed — M1 8GB unsafe)* | VLMAnalyzer (via mlx-vlm processor) | Image understanding |
| FlashRank ms-marco-MiniLM | FlashRank internal | Reranking |

**Shared-tokenizer risk:** None identified — each model owns its tokenizer independently. No shared tokenizer instances found across model boundaries.

---

## G. Top 10 Cleanup Recommendations

### 1. **[CRITICAL] Fix GLiNER model name conflict**
- config.py and project_types.py say `gliner-x-base` but model_manager.py loads `gliner-relex-large-v0.5`
- **Action:** Update `config.py:46` and `project_types.py:200` to `knowledgator/gliner-relex-large-v0.5`, or add `GLINER_RELEX_MODEL` as the runtime model with `GLINER_MODEL` kept as base NER-only

### 2. **[HIGH] Consolidate ModernBERT model names**
- 4 different model IDs across 7 locations
- **Action:** Pick one canonical model ID and use it everywhere, OR document why different models are intentional (e.g., 6bit vs full precision for memory reasons)

### 3. **[HIGH] Clarify deduplication embedding model**
- `utils/deduplication.py:73` uses `nomic-ai/nomic-embed-text-v1.5` which is different from the main embedding pipeline
- **Action:** Verify this is intentional, or align with the main ModernBERT model

### 4. **[MEDIUM] Document ANE/CoreML conversion path**
- `brain/ane_embedder.py` references offline CoreML conversion but no conversion script is in the inventory
- **Action:** Add `tools/ane_converter.py` or document the conversion procedure

### 5. **[MEDIUM] Verify captcha YOLO model path**
- `captcha_solver.py` references YOLO CoreML but no model path is specified
- **Action:** Add explicit model path or remove YOLO reference if VNCoreMLModel is the only actual implementation

### 6. **[MEDIUM] Trace microsoft/trocr usage or remove from project_types.py**
- `project_types.py:344` defines OCR model but no runtime usage found
- **Action:** Find the actual usage or remove the dead reference

### 7. **[MEDIUM] Investigate draft model completeness**
- `Hermes3Engine._draft_model_obj` is set up but no explicit model ID is configured
- **Action:** Either complete the Sprint 75 implementation or remove the draft model scaffolding

### 8. **[LOW] Remove qwen3-1.7b from config.py**
- `summarization_model: str = "qwen3-1.7b"` is unused
- **Action:** Remove or document its intended purpose

### 9. **[LOW] Add tokenizer boundary documentation**
- No shared tokenizers found, but the boundary between tokenizer ownership per model should be explicitly documented
- **Action:** Add a "Tokenizer Ownership" section to the model inventory

### 10. **[LOW] Add model lifecycle diagram**
- The 3-layer phase model (workflow-level → coarse-grained → windup-local) is documented in model_phase_facts.py but has no visual representation
- **Action:** Add architecture diagram to REAL_ARCHITECTURE.md showing the 3 model planes and their isolation boundaries

---

## H. Methodology Notes

- **Source of truth:** Code reading via grep/ctx_batch_execute, NOT doc assumptions
- **Verified files:** All seed files plus cross-references
- **Unverified items:** Labeled `uncertain` or `unverified` where code confirmation was not obtained
- **Archive scope:** Only `archive/federated_osint_v1/` found — no active archived models
- **Test coverage:** Tests reference models but do not load them (no-network/test isolation policy)