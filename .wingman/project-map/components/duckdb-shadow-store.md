# DuckDB Shadow Store

## Metadata

| Field | Value |
| --- | --- |
| Kind | component |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `components/duckdb-shadow-store.md` |
| Source Path | `knowledge/duckdb_store.py` |

## Summary

Canonical sprint facts store. Handles all finding storage, dedup, and IOC extraction. Hot-path IOC extraction via batch_ioc_extract_unified in Rust.

## Evidence

- async_ingest_findings_batch() — canonical write
- _assess_finding_quality() → semantic_deduplicator check
- DuckDBGraphAttachment for 15 deprecated graph methods
- 3-tier facts hierarchy: Sprint Facts / Shadow Findings / Cross-Sprint

## Use When

- Storing or querying sprint findings
- Adding new finding types

## Do Not Use When

- Cache/KV storage (use LMDB)
- Graph storage (use GraphManager or DuckDBGraphAttachment)
