# core-embeddings-pool

**Type:** Core Infrastructure  
**Path:** `_core/embeddings/pool.py`  
**Status:** current

## Purpose

Embedding computation pool for batch vectorization. Manages MLX/device embedding workers.

## Key Functions

| Function | Purpose |
|----------|---------|
| `EmbeddingPool` | Main class |
| `embed_batch(texts)` | Batch embed texts |
| `embed_async(text)` | Async single embed |
| `warm_cache(texts)` | Pre-compute embeddings |

## Invariants

- [CEP-1] Pool size: CPU count (default) or configured
- [CEP-2] Batch size: 32 (MLX optimal)
- [CEP-3] Embedding dim: model-dependent (384-1536)

## M1 Memory Notes

Pooled workers share MLX model. ~2GB for model + 500MB KV cache.

## Dependencies

- `mlx_lm` for inference
- `mlx-embed` for embeddings
