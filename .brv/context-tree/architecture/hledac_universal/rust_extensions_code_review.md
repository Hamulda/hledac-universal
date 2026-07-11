---
title: Rust Extensions Code Review
summary: 'Rust extensions review: OnceLock compile error fixed, SQL injection patched, GIL false positive identified, 87 pre-existing errors noted'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T19:30:09.606Z'
updatedAt: '2026-07-11T19:30:09.606Z'
---
## Reason
Documenting Rust extensions code review with 4 issues analyzed and fixes verified

## Raw Concept
**Task:**
Rust Extensions Code Review - documenting 4 issues found during code review

**Changes:**
- Fixed OnceLock compile error by storing module name as OnceLock<&str> instead of OnceLock<Option<Bound<PyModule>>>
- Fixed SQL injection risk by replacing manual string escaping with DuckDB native parameterized queries
- Identified pool_run.rs GIL safety as false positive - no fix needed
- Documented 87 pre-existing compile errors as unrelated PyClass Frozen=False mismatches

**Files:**
- rust_extensions/src/adaptive_scheduler.rs
- rust_extensions/src/async_query.rs
- rust_extensions/src/pool_run.rs
- rust_extensions/src/bloom.rs
- rust_extensions/src/dedup_bloom.rs
- rust_extensions/src/graph_cache.rs
- rust_extensions/src/lancedb_bridge.rs

**Flow:**
Code review -> Issue identification -> Analysis -> Fix implementation -> Verification with cargo check

**Timestamp:** 2026-07-11

## Narrative
### Structure
Review covered 3 Rust extension files. Issue 1 (OnceLock compile error) fixed by storing module name string and re-importing on each call. Issue 2 (SQL injection) fixed using DuckDB native parameterized queries. Issue 3 was a false positive - GIL already held by pyfunction macro. Issue 4 are pre-existing errors not introduced by this review.

### Highlights
OnceLock now stores &str module name, re-imports via py.import() on each call. SQL queries use stmt.query(params) with &dyn ToSql params. GIL safety confirmed safe. cargo check shows only deprecation warnings on edited files.

### Rules
OnceLock<T> requires T: Sync + Send. Bound<PyModule> contains *mut Python which is not Sync/Send. Python module import is idempotent - safe to re-import. Manual string escaping for SQL is never sufficient - always use parameterized queries.

## Facts
- **rust_extension_sync_requirement**: OnceLock<Bound<'static, PyModule>> requires Sync+Send which PyModule doesn't have [project]
- **sql_injection_risk**: Manual string escaping for SQL is error-prone and can lead to injection [project]
- **gil_held_by_macro**: #[pyfunction] macro ensures GIL is held on function entry [project]
- **pre_existing_errors**: 87 pre-existing errors are PyClass Frozen=False mismatches [project]
