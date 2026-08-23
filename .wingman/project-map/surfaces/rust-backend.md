# Rust Backend Surface

## Metadata

- **Entry Path:** surfaces/rust-backend
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** surface

## Summary

Rust extension modules providing high-performance implementations.

## Source Paths

- rust_extensions/src/
- _core/rust_backend/

## Domains

| Domain | Purpose | Fallback |
|--------|---------|----------|
| url | URL parsing/normalization | Python stdlib |
| bloom | Bloom filter | Python utils |
| ioc | IoC consistency | Python rust_ext |
| ioc_dedup | IOC deduplication | Python rust_ext |
| ip | IP utilities | Python ipaddress |
| aho | Aho-Corasick | Python re |
| evidence | Evidence handling | Python handlers |
| madvise | Memory advice | Python ctypes |

## Architecture

```python
class _RustBackend:
    def _get_domain(self, name, python_fallback):
        if self._rust_available:
            return getattr(self._rust_mod, name)
        return python_fallback()
```

## Probe Detection

Use `RustProbe.simdjson_extract` for SIMD JSON parsing.
