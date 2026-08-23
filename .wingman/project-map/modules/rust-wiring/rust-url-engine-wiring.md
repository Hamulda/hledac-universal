# rust-url-engine-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/url_engine_wiring.py`  
**Status:** current

## Purpose

Rust-native URL parsing, validation, and normalization. Fast URL processing for high-throughput fetching.

## Key Functions

| Function | Purpose |
|----------|---------|
| `parse_url(url)` | Parse URL into components |
| `normalize_url(url)` | Normalize URL |
| `validate_url(url)` | Validate URL structure |
| `extract_domain(url)` | Extract domain |

## Invariants

- [RUE-1] Uses `url` Rust crate (same as curl_easy)
- [RUE-2] IDN domains normalized to punycode
- [RUE-3] Relative URLs rejected (no base resolution)
