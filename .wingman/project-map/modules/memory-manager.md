# Memory Manager

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/memory-manager.md` |
| Source Path | `memory/memory_manager.py` |

## Summary

Dual-layer LMDB-backed memory. Layer 1: per-session ephemeral storage (put/get/delete). Layer 2: memory_layer.py wraps with SharedBlock and EntropyMaskingManager for cross-session state. NOT thread-safe — async-only.

## Evidence

- LMDB-backed with zero-copy reads via buffers=True
- MAX_KEYS_PER_SESSION bounded
- Lazy session cleanup on put/get
- Separated from DuckDB (different lifetime: micro-session vs sprint-facts)
- Uses orjson zero-copy deserialization

## Use When

- Storing per-session working memory
- Understanding session lifecycle
- Debugging memory state issues

## Do Not Use When

- Persisting sprint facts (use DuckDBShadowStore)
- Cross-thread access (not thread-safe by design)
