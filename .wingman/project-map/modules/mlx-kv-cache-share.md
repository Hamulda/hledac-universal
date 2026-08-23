# MLX KV Cache Share

## Metadata

- **Entry Path:** modules/mlx-kv-cache-share
- **Status:** current
- **Source:** brain/mlx_kv_cache_share.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Tokenized prompt cache for pre-tokenized prefix reuse, eliminating redundant tokenization.

## Source Paths

- `brain/mlx_kv_cache_share.py`

## Problem Solved

Every `mlx_lm.generate()` call re-tokenizes identical system_msg prefix (~5-20ms per prompt).

## Solution

Pre-tokenize and cache token arrays for fixed prompt templates.

## Constraints

- M1 8GB safe: bounded to `_MAX_CACHED_PROMPTS` (8) entries
- Tokens stored as `list[int]` (~2KB per 500-token prompt)
- Async-safe with `asyncio.Lock`

## Key Classes

| Class | Purpose |
|-------|---------|
| `TokenizedPromptEntry` | Cached token array with metadata |
| `PromptCacheStats` | Cache hit/miss/timing metrics |
| `TokenizedPromptCache` | Main cache with LRU eviction |

## Note

MLX KV cache cannot be reused (mlx_lm doesn't expose internal state). Only tokenized prefixes are cached.

## Related Entries

- modules/hermes-model-cache
- modules/mlx-inference
