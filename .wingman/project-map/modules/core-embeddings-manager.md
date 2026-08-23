# core-embeddings-manager

**Type:** Core Infrastructure  
**Path:** `_core/embeddings/manager.py`  
**Status:** current

## Purpose

Central embedding management with caching, fallback, and model selection.

## Key Functions

| Function | Purpose |
|----------|---------|
| `EmbeddingManager` | Main class |
| `get_embedding(text)` | Get cached or compute |
| `select_model(task)` | Select best model |
| `clear_cache()` | Clear embedding cache |

## Models

| Model | Dim | Use Case |
|-------|-----|---------|
| all-MiniLM-L6-v2 | 384 | Fast, low memory |
| e5-base-v2 | 768 | General purpose |
| bge-large | 1024 | High quality |

## Invariants

- [CEM-1] Cache: LMDB with 24h TTL
- [CEM-2] Fallback: OpenAI if local fails
- [CEM-3] Budget: 500MB cache max

## M1 Memory Notes

Cache stored in LMDB at `~/.hledac/embeddings.lmdb`
