# rust-ioc-dedup-wiring

## Kind

`module`

## Status

`Preferred`

## Last Verified

- Date: 2026-08-20
- Evidence:
  - `rust_extensions/wiring/ioc_dedup_wiring.py`: Source verification complete

## Evidence Level

`Source-Verified`

## Tags

- rust-ffi
- ioc-extraction
- performance
- zero-copy

## Summary

Rust-accelerated IOC deduplication via Aho-Corasick + SIMD pattern matching. Zero-copy bridge between Python and Rust for high-throughput IOC dedup at the feed pipeline stage.

## Entry Points

- `rust_extensions.wiring.ioc_dedup_wiring.init_ioc_dedup()`: Initialize engine
- `rust_extensions.wiring.ioc_dedup_wiring.dedup_iocs_flat()`: Batch dedup

## Key Files

- `rust_extensions/wiring/ioc_dedup_wiring.py`: FFI bridge implementation
- `rust_extensions/hledac_rust_extensions/`: PyO3 bindings

## Related Entries

- `modules/ioc-processor.md`: Python IOC processor
- `modules/rust-bloom-filter-wiring.md`: Related dedup wiring

## Owns Responsibility

IOC deduplication for feed pipeline

## Inputs

- JSON bytes with IOC list

## Outputs

- Deduplicated IOC list with scores

## Side Effects

- Rust automaton state modified on dedup

## Use When

- Feed pipeline IOC dedup at high throughput
- Bulk IOC processing

## Do Not Use When

- Single IOC check (use `check_dedup_set()` instead)
- Low-volume processing where Python overhead is acceptable

## Notes For Agents

- Always use Rust FFI for bulk operations, never pure Python
- JSON serialization via msgspec for zero-copy bridge
- Aho-Corasick automaton pre-built at init (not per-request)
