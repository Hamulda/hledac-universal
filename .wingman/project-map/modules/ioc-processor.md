# IOC Processor

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/ioc-processor.md` |
| Source Path | `knowledge/ioc_processor.py` |

## Summary

Single unified facade for IOC extraction and URL normalization (F350M-R). Canonical cold path for forensics. Hot path bypasses Python via `batch_ioc_extract_unified` from `hledac_rust_extensions`.

## Evidence

- Exports: IOCProcessor, fast_ioc_extract, url_normalize, batch_dedup_urls
- Hot path: knowledge/duckdb_store.py → hledac_rust_extensions directly
- Cold path: forensics/ioc_extractor.py → this module

## Use When

- Cold-path IOC extraction
- URL normalization in forensic analysis
- Understanding the dual-path IOC architecture

## Do Not Use When

- Hot-path performance-critical IOC extraction (use duckdb_store directly)
