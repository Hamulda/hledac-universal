# Semantic Deduplication

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/semantic-dedup.md` |
| Source Path | `semantic_deduplicator.py` |

## Summary

Embedding-based duplicate detection (Sprint F195). Secondary dedup layer after URL/content hash dedup. Detects semantically similar findings. LMDB-persisted embeddings (xxh3-64 key, 256d float32). LRU cache bounded by MAX_CACHE_ITEMS=512 and MAX_CACHE_MEMORY_MB=256.

## Evidence

- Called from DuckDBShadowStore._assess_finding_quality()
- find_semantic_duplicates(texts) → list[set[int]]
- check_single(text) → bool
- LMDB: LMDB_ROOT/semantic_dedup.lmdb, idempotent upsert via put_many
- low_memory mode: fail-soft disable
- _check_memory_guard() before embedding generation

## Use When

- Detecting near-duplicate text findings
- Post-hash-dedup semantic cleanup

## Do Not Use When

- Primary dedup (use URL/content hash dedup)
- Unlimited cache (bounded by MAX_CACHE_ITEMS=512)
