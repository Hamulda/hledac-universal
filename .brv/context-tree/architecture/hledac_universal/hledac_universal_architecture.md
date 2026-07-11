---
title: Hledac Universal Architecture
summary: Sprint-based async orchestrator with DuckDB shadow store, tiered sources, and M1 8GB optimizations
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:51:16.696Z'
updatedAt: '2026-07-11T14:51:16.696Z'
---
## Reason
Document Hledac Universal sprint-based async orchestrator architecture

## Raw Concept
**Task:**
Document Hledac Universal sprint-based async orchestrator architecture

**Changes:**
- DuckDB Shadow Store (canonical facts)
- Sprint Lifecycle Manager
- ResourceRegistry (no weakref)
- SprintRunContext (contextvars)
- ParquetHistoryReader (lazy pagination)
- DeepSecurityConfig

**Files:**
- runtime/sprint_scheduler.py
- knowledge/duckdb_store.py

**Flow:**
run_sprint → run_acquisition_lanes → run_advisory_runner → _accumulate_findings_to_graph → async_ingest_findings_batch

## Narrative
### Structure
Sprint pipeline: owner dispatches cycles via SprintScheduler. Scheduler manages lifecycle, dedup, and export. DuckDBShadowStore is canonical facts authority for analytics subsystem.

### Dependencies
DuckDB for sprint facts, DuckPGQGraph for IOC storage, LMDB for payload WAL, Arrow for zero-copy ingest, Rust extensions for batch operations

### Highlights
M1 8GB optimizations: 600MB DuckDB limit, 4 threads, Arrow zero-copy, LRU(16) dedup, msgspec.Struct hot-path DTOs, orjson JSON fallback

## Facts
- **entry_point**: Entry point: python -m hledac.universal --sprint QUERY [project]
- **core_layers**: Core layers: runtime/, brain/, fetching/, knowledge/, transport/ [project]
- **duckdb_memory_limit**: DuckDB memory limit: 600MB on M1 Air 8GB [project]
- **duckdb_threads**: DuckDB threads: 4 (P+E cores on M1 8GB) [project]
- **duckdb_chunk_config**: Chunk size: 500, concurrency: 2 for DuckDB inserts [project]
- **hot_path_dto_pattern**: msgspec.Struct with frozen=True, gc=False for hot-path DTOs [project]
- **tier_priority**: Tier priority (high→low): surface → structured_ti → deep → archive → other [project]
- **nonfeed_fallback_lanes**: Nonfeed fallback: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH [project]
- **advisory_dedup_pattern**: Advisory dedup uses LRU(16) with FIFO no-promote semantics [project]
- **sprint_invariants**: Sprint invariants: Winddown, Dedup, Lifecycle authority, Export on teardown, TaskGroup concurrency [project]
