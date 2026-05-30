# Sprint F218A: Embedding Ownership Plan

## Canonical Embedding Owner

**File**: `embedding_pipeline.py`
**Class/Module**: `EmbeddingRouter` + module-level functions (`generate_embeddings`, `embed_query`, `embed_document`)
**Role**: Primary embedding dispatch layer with ANE→MLX ModernBERT→CPU priority routing.

**Canonical entrypoints** (in priority order):
1. `generate_embeddings(texts, batch_size, keep_loaded)` — batch document embedding
2. `generate_embeddings_async(texts, batch_size, keep_loaded)` — async batch
3. `embed_query(text)` — single query embedding (sync)
4. `embed_query_async(text)` — single query embedding (async)
5. `embed_document(text)` — single document embedding for indexing

**Backend priority** (defined in `EmbeddingRouter`):
1. ANEEmbedder (CoreML MiniLM-L6-v2) — if ANE available and MLX not loaded
2. ModernBERTEmbedder (MLX-accelerated nomic-ai/modernbert-embed-base) — primary path
3. MLXEmbeddingManager (same model, different wrapper) — fallback
4. Hash fallback (deterministic, zero-RAM) — when nothing else available

**Why embedding_pipeline.py is canonical**:
- Has the `EmbeddingRouter` class with explicit ANE→MLX priority logic
- Has memory guard (`_check_memory_guard`, `_uma_guard_before_batch`)
- Has MRL truncation (256d output via `truncate_dim`)
- Has xxhash dedup before embedding (AREA J)
- Has `embedding_session` refcounting context manager
- Is referenced as "ROLE: Primary embedder" in its own docstring

---

## Embedding Components

### 1. `embedding_pipeline.py` — EmbeddingRouter (CANONICAL OWNER)
- **Model**: nomic-ai/modernbert-embed-base via mlx-embeddings
- **Output dim**: 256 (MRL truncation) / 768 (full)
- **Loads model**: Yes — via `ModernBERTEmbedder` or `MLXEmbeddingManager` delegation
- **Active path**: Yes — `generate_embeddings()` is the main batch entrypoint
- **Duplication risk**: None for canonical callers (semantic_store, rag_engine, lancedb_store, graph_rag)

### 2. `brain/ane_embedder.py` — ANEEmbedder + semantic_dedup_findings
- **Model**: CoreML MiniLM-L6-v2 (minilm_ane) via ANE, or MLX ModernBERT fallback
- **Output dim**: 384 (MiniLM) / 768 (ModernBERT fallback)
- **Loads model**: Yes — CoreML model file + optional MLX fallback
- **Active path**: Yes — `semantic_dedup_findings()` is used by distillation_engine, windup_engine, sprint_exporter, sprint_diff_engine
- **Duplication risk**: Medium — `semantic_dedup_findings()` is a standalone async function with its own ANE→MLX→hash fallback chain. It does NOT route through `EmbeddingRouter`.
- **Telemetry**: `ane_embed_attempted`, `ane_embed_fallback_used`, `ane_warmup_executed`, `ane_warmup_error`, `get_ane_telemetry()`, `get_ane_status()`, `ANEStatusResult`

### 3. `embeddings/modernbert_embedder.py` — ModernBERTEmbedder
- **Model**: nomic-ai/modernbert-embed-base via mlx-embeddings
- **Output dim**: 768 (full)
- **Loads model**: Yes — via `_ModernBERTMLXLoader.load()` (singleton)
- **Active path**: Indirect — used BY `EmbeddingRouter` internally
- **Direct callers**: None in production code (only in tests and `EmbeddingRouter._load_modernbert()`)
- **Duplication risk**: Low — all production calls go through `EmbeddingRouter` which calls `ModernBERTEmbedder.embed_batch()` or `MLXEmbeddingManager.encode()`

### 4. `core/mlx_embeddings.py` — MLXEmbeddingManager
- **Model**: nomic-ai/modernbert-embed-base via mlx-embeddings
- **Output dim**: 768 (full) / 256 (MRL via `truncate_dim`)
- **Loads model**: Yes — via `mlx_embeddings_load()`
- **Active path**: Indirect — used BY `EmbeddingRouter` as final fallback AND by context_optimization modules directly
- **Direct callers**:
  - `context_optimization/dynamic_context_manager.py` — uses `MLXEmbeddingManager` directly (not through `EmbeddingRouter`)
  - `context_optimization/context_cache.py` — uses `MLXEmbeddingManager` directly
  - `context_optimization/context_compressor.py` — uses `MLXEmbeddingManager` directly
  - `utils/deduplication.py` — `SemanticDeduplicator` uses `get_embedding_manager()` directly
- **Duplication risk**: Medium — context_optimization modules create their own `MLXEmbeddingManager` instance (separate from EmbeddingRouter's ModernBERTEmbedder). However both use the same `mlx_embeddings_load()` path.

### 5. `utils/deduplication.py` — SemanticDeduplicator
- **Model**: Same as MLXEmbeddingManager
- **Active path**: Indirect — used by context optimization
- **Note**: Uses `get_embedding_manager()` singleton, so shares model with `EmbeddingRouter`

---

## Duplicate Wrapper Classification

| Wrapper | Loads Model? | Independent Loading? | Route to Canonical? | Status |
|---------|-------------|---------------------|---------------------|--------|
| `ModernBERTEmbedder` | Yes | Via `_ModernBERTMLXLoader` singleton | Yes — called by `EmbeddingRouter` | ACTIVE wrapper (not standalone) |
| `MLXEmbeddingManager` | Yes | Via `mlx_embeddings_load` | Partially — context modules bypass `EmbeddingRouter` | ACTIVE wrapper (partially standalone) |
| `ANEEmbedder` | Yes | Yes — CoreML + MLX fallback | No — has separate ANE→MLX→hash chain | ACTIVE standalone (semantic dedup path) |

---

## Memory Isolation (M1 8GB)

- **ANE + MLX ModernBERT never loaded simultaneously** — enforced by `EmbeddingRouter._check_mlx_loaded()` + M1ResourceGovernor
- ANE uses ~300MB CoreML; MLX ModernBERT uses ~500MB; loading both would exceed 5.5GB limit
- `embedding_pipeline.py` has `_uma_guard_before_batch()` that checks combined memory before loading

---

## Decisions

1. **Canonical owner is `embedding_pipeline.py`** — keep as-is, do not add new abstraction
2. **`ModernBERTEmbedder`** — keep as active wrapper called by `EmbeddingRouter`, don't refactor
3. **`MLXEmbeddingManager`** — keep as fallback in `EmbeddingRouter`, context modules using it directly is acceptable (they are separate use cases)
4. **`ANEEmbedder` + `semantic_dedup_findings`** — keep as standalone semantic dedup path (different use case than document/query embedding)
5. **No new model loading paths** — all additions must route through existing wrappers

---

## What Was NOT Changed (per sprint constraints)

- Did NOT replace ModernBERT
- Did NOT add nomic/F2LLM/mxbai/SFR embeddings
- Did NOT change DeepHermes/Hermes LLM config
- Did NOT download new models
- Did NOT rewrite vector store or RAG engine
- Did NOT delete compatibility wrappers
- Did NOT add new required dependencies