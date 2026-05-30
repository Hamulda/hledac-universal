# RERANKER OWNERSHIP PLAN — Sprint F218B

## Overview

Canonical reranker owner: **tools/reranker.py** (`LightweightReranker`, `create_reranker()`)
Default backend: **FlashRank / ms-marco-MiniLM-L-12-v2** (~4MB ONNX, very lightweight)

---

## Reranker Components

| File | Class/Function | Model | Status | Loads Model | Duplicates | Input→Output | Callers |
|------|---------------|-------|--------|-------------|------------|--------------|---------|
| `tools/reranker.py` | `LightweightReranker` | ms-marco-MiniLM-L-12-v2 | **ACTIVE** | YES | NO | query + docs → reranked docs | legacy/autonomous_orchestrator |
| `tools/reranker.py` | `create_reranker()` | ms-marco-MiniLM-L-12-v2 | **ACTIVE** | YES | NO | config → LightweightReranker | tools/__init__.py |
| `tools/reranker.py` | `RerankerFactory` | — | factory | NO | NO | — | internal |
| `brain/synthesis_runner.py` | `_get_flashrank_ranker()` | ms-marco-MiniLM-L-12-v2 | **ACTIVE** | YES | **YES** (own instance) | — (singleton) | synthesis_runner rerank path |
| `brain/synthesis_runner.py` | `rerank_passages()` | ms-marco-MiniLM-L-12-v2 | **ACTIVE** | YES | NO | query + passages → reranked | internal to synthesis |
| `knowledge/lancedb_store.py` | `_get_flashrank_ranker()` | ms-marco-MiniLM-L-12-v2 | **ACTIVE** | YES | **YES** (own instance) | — (lazy) | lancedb_store retrieval |
| `knowledge/lancedb_store.py` | `_colbert_reranker` | ColBERT | **FUTURE** | YES | NO | candidates → reranked | lancedb_store retrieval |
| `knowledge/lancedb_store.py` | `_mlx_rerank()` | MLX cosine | **FALLBACK** | YES | NO | emb + candidates → reranked | lancedb_store fallback path |
| `prefetch/ssm_reranker.py` | `SSMReranker` | SSM (custom) | **EXPERIMENTAL** | YES (MLX) | NO | candidates → scored | NOT actively used |
| `knowledge/semantic_store.py` | ANN cosine score | L2 distance | **ACTIVE** | NO | NO | emb → score 0-1 | semantic_pivot() |
| `knowledge/graph_rag.py` | `score_path()` | heuristic | **ACTIVE** | NO | NO | path + hypothesis → score | graph traversal |
| `knowledge/graph_rag.py` | `_rank_facts()` | novelty+similarity | **ACTIVE** | NO | NO | facts → ranked facts | graph rag |

---

## Duplicate Analysis

### ISSUE 1: Multiple FlashRank instances
- `tools/reranker.py` → `LightweightReranker` (legacy orchestrator path)
- `brain/synthesis_runner.py` → `_get_flashrank_ranker()` (synthesis path)
- `knowledge/lancedb_store.py` → `_get_flashrank_ranker()` (retrieval path)

**All three use `ms-marco-MiniLM-L-12-v2` model via FlashRank.**

**Recommendation**: Mark `brain/synthesis_runner.py` and `knowledge/lancedb_store.py` instances as **compatibility wrappers** — they exist for historical reasons and are NOT duplicates of `tools/reranker.py` since they serve different call sites (synthesis vs retrieval vs legacy orchestration).

### ISSUE 2: SSMReranker not actively used
- `prefetch/ssm_reranker.py` defines `SSMReranker` but is NOT called from any active code path
- Marked as **experimental, test-only** — do not activate without benchmark evidence

### ISSUE 3: No bge/jina/MemReranker loaded
- All three are **future candidates only** per model_integration_matrix.json
- None are loaded in current runtime
- Policy: **benchmark_only** before activation

---

## Canonical Reranker Entry Point

**PRIMARY**: `tools/reranker.py`
- `LightweightReranker(model_name="ms-marco-MiniLM-L-12-v2")`
- `create_reranker(config=None)` → returns LightweightReranker
- Exported via `tools/__init__.py`

**SECONDARY** (historical compatibility):
- `brain/synthesis_runner.py:_get_flashrank_ranker()` — synthesis rerank path
- `knowledge/lancedb_store.py:_get_flashrank_ranker()` — retrieval rerank path

---

## Current Default Behavior

1. **Lightweight reranker** (`ms-marco-MiniLM-L-12-v2`) is the **ONLY active reranker**
2. **No bge/jina/MemReranker models are loaded**
3. **FlashRank is loaded lazily** — first use triggers model load
4. **MLX rerank** in LanceDB is **fallback only** (no FlashRank available)

---

## Future Candidates (DO NOT ACTIVATE YET)

| Model | Purpose | Activation Criteria |
|-------|---------|---------------------|
| `BAAI/bge-reranker-v2-m3` | Multilingual semantic reranking | Real benchmark evidence, not matrix speculation |
| `jinaai/jina-reranker-v2-base` | Multilingual, jina ecosystem | Same — benchmark evidence required |
| `MemGPT/MemReranker-0.6B` | Temporal/causal/agent-memory hard retrieval | Explicit benchmark + memory budget proof |

**Policy**: Do not load heavy reranker together with heavy LLM unless explicitly gated.
**Policy**: Do not run multiple rerankers by default.

---

## Ownership Decision

```
Canonical reranker owner: tools/reranker.py

Default backend: FlashRank / ms-marco-MiniLM-L-12-v2

Fallback: MLX cosine similarity (in LanceDB only when FlashRank unavailable)

Future: bge-reranker-v2-m3, jina-reranker-v2-base, MemReranker-0.6B
        → ONLY after real benchmark evidence, not matrix speculation
```

---

## Safe Cleanup Actions (This Sprint)

1. ✅ Mark `brain/synthesis_runner.py:_get_flashrank_ranker()` as **compatibility wrapper** (docstring)
2. ✅ Mark `knowledge/lancedb_store.py:_get_flashrank_ranker()` as **compatibility wrapper** (docstring)
3. ✅ Add diagnostic helpers: `get_reranker_backend()`, `get_reranker_status()` if useful
4. ✅ Mark `prefetch/ssm_reranker.py` as **experimental** (docstring)
5. ✅ Document bge/jina/MemReranker as **future candidates only** in plan
6. ✅ Verify no new reranker model IDs are activated

---

## What NOT to Do

- ❌ Do not add bge-reranker implementation
- ❌ Do not add jina-reranker implementation  
- ❌ Do not add MemReranker implementation
- ❌ Do not rewrite RAG retrieval
- ❌ Do not create benchmark suite
- ❌ Do not add ONNX/CoreML conversion
- ❌ Do not change ranking behavior (no bug fix here)
- ❌ Do not download models

---

## Verification

- Canonical reranker owner documented ✅
- Duplicate FlashRank instances acknowledged (historical compatibility, not true duplicates) ✅
- bge/jina/MemReranker deferred ✅
- No runtime behavior changed ✅
- No new dependencies ✅