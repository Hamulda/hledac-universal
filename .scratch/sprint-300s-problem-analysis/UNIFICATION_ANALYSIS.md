# Graph RAG + LanceDB + SemanticStore Unification Analysis

## Current Architecture (2026-06-15)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           RAGEngine                                        │
│  hybrid_retrieve() — grounding authority                                  │
│  Own FastEmbed instance (_fastembed_embedder) — 384d                      │
│  Falls back to MLXEmbeddingManager (256d)                                 │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                    LanceDBIdentityStore                                     │
│  get_identity_store() singleton                                            │
│  Own MLX embedder (_embedder) — 256d via MLXEmbeddingManager              │
│  Table: "entities"                                                        │
│  add_entity(), search_similar()                                           │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│                       SemanticStore                                         │
│  Own FastEmbed TextEmbedding — 384d (BAAI/bge-small-en-v1.5)             │
│  Own CoreML embedder (ANE path)                                           │
│  Table: "semantic_ioc_v1"                                                │
│  semantic_pivot(), embed_query()                                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Problem: 3 Separate Embedding Systems

| Component | Embedder | Dimension | Model |
|-----------|----------|-----------|-------|
| RAGEngine | `_fastembed_embedder` | 384d | BAAI/bge-small-en-v1.5 |
| LanceDBIdentityStore | `MLXEmbeddingManager` | 256d | Hermes-3 |
| SemanticStore | FastEmbed TextEmbedding + CoreML | 384d | BAAI/bge-small-en-v1.5 |

**RAM Impact on M1 8GB:**
- FastEmbed model (BAAI/bge-small-en-v1.5): ~33MB
- FastEmbed loaded twice (RAGEngine + SemanticStore): ~66MB wasted
- CoreML embedder: additional resident memory
- Total: **~100MB+ redundant model weight in RAM**

## Why They Can't Simply Be Merged

1. **Dimension mismatch**: RAGEngine/SemanticStore use 384d, LanceDBIdentityStore uses 256d
2. **Different use cases**: RAGEngine = context grounding, LanceDBIdentityStore = entity identity, SemanticStore = IOC findings ANN
3. **Existing data**: LanceDB tables have 256d embeddings, can't easily re-embed all historical data
4. **Architecture assertions**: `test_retrieval_boundaries.py` enforces separation of concerns

## Proposed Solution: Unified Embedding Manager

### Step 1: Create UnifiedEmbeddingManager singleton

```python
# brain/unified_embedding_manager.py
class UnifiedEmbeddingManager:
    """Single source for ALL embeddings in the system.
    
    Responsibilities:
    - Single MLX/FastEmbed model instance
    - Unified dimension (384d for compatibility)
    - Shared across RAGEngine, LanceDBIdentityStore, SemanticStore
    """
    _instance = None
    
    def __init__(self):
        # Try MLX first (fastest on M1)
        self._backend = "mlx"  # or "fastembed" or "coreml"
        self._dim = 384
        self._model = None
        
    async def embed(self, text: str) -> list[float]:
        """Single embed() for all use cases."""
        ...
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batched embed() for all use cases."""
        ...
```

### Step 2: Migrate LanceDBIdentityStore to 384d

**Problem**: Existing `entities` table has 256d embeddings.

**Solution**: Add new `entities_v2` table with 384d, migrate gradually.

### Step 3: Retire SemanticStore in favor of LanceDBIdentityStore

SemanticStore's `semantic_ioc_v1` table can be migrated to LanceDBIdentityStore's schema.

### Step 4: Update RAGEngine to use UnifiedEmbeddingManager

Replace `_fastembed_embedder` with shared singleton.

## Implementation Plan

| Phase | Task | Effort |
|-------|------|--------|
| P0 | Create `UnifiedEmbeddingManager` singleton in `brain/` | 2h |
| P0 | Wire `LanceDBIdentityStore` to use `UnifiedEmbeddingManager` | 1h |
| P0 | Wire `RAGEngine._generate_embeddings()` to use `UnifiedEmbeddingManager` | 1h |
| P1 | Create `entities_v2` table schema with 384d | 2h |
| P1 | Migrate new entities to v2, keep v1 for backward compat | 4h |
| P2 | Deprecate SemanticStore, move to LanceDBIdentityStore | 4h |
| P2 | Remove FastEmbed from SemanticStore, remove from dependencies | 1h |

## FastEmbed Removal

To remove FastEmbed completely:

1. **Remove from dependencies** in `pyproject.toml`:
```toml
# Remove or comment out
# fastembed = { version = "...", extras = [...] }
```

2. **Remove SemanticStore's FastEmbed path**:
```python
# In semantic_store.py, remove:
# from fastembed import TextEmbedding
# self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
```

3. **Remove `_fastembed_embedder` from RAGEngine**:
```python
# In rag_engine.py, replace _generate_embeddings() 
# to use only MLXEmbeddingManager
```

4. **Verify no other imports**:
```bash
rtk grep -r "from fastembed\|import fastembed" .
```

## Dimension Decision

| Option | Pros | Cons |
|--------|------|------|
| 256d | Lower RAM, faster compute, LanceDBIdentityStore already uses | Loses some semantic precision |
| 384d | Better precision, RAGEngine/SemanticStore compatible | Higher RAM, must re-embed |

**Recommendation**: **384d** — semantic precision matters more than RAM savings on modern hardware.

## Backward Compatibility

Existing data in LanceDB:
- `entities` table (256d): Keep for reading, migrate new writes to 384d
- `semantic_ioc_v1` table (384d): Migrate to unified store

## Risks

1. **Migration complexity**: Re-embedding all historical data is expensive
2. **Breaking changes**: External tools relying on 256d embeddings would break
3. **Test updates**: `test_retrieval_boundaries.py` assertions may need updating

## Quick Wins (No Migration Required)

1. **Share FastEmbed instance** between RAGEngine and SemanticStore (don't load 2x)
2. **Cache MLXEmbeddingManager** so LanceDBIdentityStore reuses it
3. **Remove CoreML duplication** — already have MLX path

## Conclusion

Full unification requires:
1. Single embedding manager singleton (UnifiedEmbeddingManager)
2. Migrate LanceDBIdentityStore from 256d → 384d
3. Retire SemanticStore (merge into LanceDBIdentityStore)
4. Remove FastEmbed from dependencies

This is a **2-3 sprint effort** given migration complexity and testing requirements.
