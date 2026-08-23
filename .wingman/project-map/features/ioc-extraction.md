# IOC Extraction

## Metadata

| Field | Value |
| --- | --- |
| Kind | feature |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `features/ioc-extraction.md` |
| Source Paths | `knowledge/ioc_processor.py`, `knowledge/duckdb_store.py` |

## Summary

Dual-path IOC extraction: hot path bypasses Python via batch_ioc_extract_unified from hledac_rust_extensions into duckdb_store. Cold path uses knowledge.ioc_processor facade. fast_ioc_extract for forensic analysis.

## Evidence

- Hot path: duckdb_store → hledac_rust_extensions.batch_ioc_extract_unified (Rust)
- Cold path: forensics.ioc_extractor → knowledge.ioc_processor.fast_ioc_extract
- Exports: IOCProcessor, fast_ioc_extract, url_normalize, batch_dedup_urls
- M1 8GB: bounded, fail-safe, no recursion
- DEPRECATED: forensics/ioc_extractor.py (use knowledge.ioc_processor)

## Use When

- Batch IOC extraction in duckdb_store
- Cold-path forensic IOC extraction

## Do Not Use When

- Hot-path performance-critical extraction (use duckdb_store directly)
