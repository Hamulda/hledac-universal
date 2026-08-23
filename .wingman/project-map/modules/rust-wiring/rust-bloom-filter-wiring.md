# rust-bloom-filter-wiring

## Kind

`module`

## Status

`Preferred`

## Last Verified

- Date: 2026-08-20
- Evidence:
  - `rust_extensions/wiring/dedup_bloom_wiring.py`: Source verification complete

## Evidence Level

`Source-Verified`

## Tags

- rust-ffi
- bloom-filter
- dedup
- bounded-memory

## Summary

Rust-native rotating Bloom filter for URL deduplication. Replaces Python `pybloom_live.ScalableBloomFilter` with bounded memory footprint.

## Entry Points

- `RotatingBloomFilterRust`: Main class wrapper
- `add(url)`: Add URL to filter
- `might_contain(url)`: Check membership
- `rotate()`: Rotate to new segment

## Key Files

- `rust_extensions/wiring/dedup_bloom_wiring.py`: Rust implementation

## Related Entries

- `modules/bloom-filter.md`: Python BloomFilter (RotatingBloomFilter)

## Owns Responsibility

URL deduplication with bounded memory

## Inputs

- URL string

## Outputs

- Boolean membership check result

## Side Effects

- Filter state grows within bounded limits

## Use When

- URL dedup in feed pipeline
- High-volume URL filtering

## Do Not Use When

- Exact dedup required (Bloom filters have false positives)
- Low volume where overhead is unnecessary

## Known Constraints

- Max 8 segments × 10M entries = 80M max entries
- False positive rate ~1% per segment

## Notes For Agents

- Replaces `pybloom_live.ScalableBloomFilter` (deprecated)
- Never use Python `Set[str]` for URL dedup
- Memory bounded at ~120MB for full filter
