# Rust FFI Wiring Modules

## Entry Count

9 modules

## Modules

| Name | Path | Status | Evidence Level |
| --- | --- | --- | --- |
| rust-ioc-dedup-wiring | rust-wiring/rust-ioc-dedup-wiring.md | Preferred | Source-Verified |
| rust-bloom-filter-wiring | rust-wiring/rust-bloom-filter-wiring.md | Preferred | Source-Verified |
| rust-simd-similarity-wiring | rust-wiring/rust-simd-similarity-wiring.md | Preferred | Source-Verified |
| rust-circuit-breaker-wiring | rust-wiring/rust-circuit-breaker-wiring.md | Preferred | Source-Verified |
| rust-graph-analytics-wiring | rust-wiring/rust-graph-analytics-wiring.md | Preferred | Source-Verified |
| rust-aimd-wiring | rust-wiring/rust-aimd-wiring.md | Preferred | Source-Verified |
| rust-claims-extraction-wiring | rust-wiring/rust-claims-extraction-wiring.md | Preferred | Source-Verified |
| rust-text-norm-wiring | rust-wiring/rust-text-norm-wiring.md | Preferred | Source-Verified |
| rust-url-engine-wiring | rust-wiring/rust-url-engine-wiring.md | Preferred | Source-Verified |

## Summary

Rust FFI wiring modules providing high-performance implementations of core functionality via PyO3 bindings. All modules use zero-copy serialization via msgspec.

## Pattern

All wiring modules follow the same pattern:
1. Initialize Rust engine at import
2. Serialize data via msgspec
3. Call Rust FFI
4. Deserialize result via msgspec

## M1 Constraints

- Rust extensions pre-compiled for ARM64
- Memory bounded per module
- Import-time allocation for automaton/tables

## Related Entries

- `modules/rust-backend.md`: Core Rust backend
- `contracts/rust-ffi-contract.md`: FFI contract
