# Bloom Filter

## Metadata

- **Entry Path:** modules/bloom-filter
- **Status:** current
- **Source:** utils/bloom_filter.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Memory-efficient Bloom Filter with host-tier LRU rotation for URL deduplication.

## Source Paths

- `utils/bloom_filter.py`
- `rust_extensions/src/bloom.rs` (optional Rust backend)

## Architecture

```
RotatingBloomFilter
├── _global_filter: Global dedup
└── _host_tiers: dict[host, BloomFilter]
    └── LRU eviction via OrderedDict
```

## Key Methods

| Method | Purpose |
|--------|---------|
| `add()` | Add URL with host attribution |
| `add_batch()` | Bulk add |
| `contains()` | Check existence |
| `contains_batch()` | Batch check |

## Bounds

| Parameter | Value |
|-----------|-------|
| Max tiers | 256 hosts |
| Per-host capacity | 100,000 |
| Max fill ratio | 0.7 (rotation trigger) |

## Why RotatingBloomFilter

ScalableBloomFilter grows without limit. RotatingBloomFilter uses LRU host tiers with rotation.

## Anti-Pattern

Never use `Set[str]` or `ScalableBloomFilter` for URL dedup.

## Related Entries

- modules/memory-coordinator
- features/semantic-dedup
