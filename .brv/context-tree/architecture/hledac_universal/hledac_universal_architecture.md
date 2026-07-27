---
consolidated_at: '2026-07-27T13:43:23.696Z'
consolidated_from: [{date: '2026-07-27T13:43:23.699Z', path: architecture/hledac_universal/phase_2_architecture_and_directory_structure.md, reason: 'Both files cover the same core architecture: entry point (python -m hledac.universal), 8-lane SprintScheduler flow (CLI → run_sprint → 8 lanes → advisory → DuckDB), and directory structure (core/runtime/knowledge/fetching/transport/brain). phase_2 is more recent (2026-07-27) with expanded directory listing; hledac_universal_architecture has richer M1 8GB facts and DuckDB config details. Merged output should preserve M1 facts from hledac_universal_architecture while incorporating directory structure from phase_2.'}]
---
## Raw Concept
**Task:** Document Hledac Universal sprint-based async orchestrator architecture

**Changes:**
- DuckDB Shadow Store (canonical facts)
- Sprint Lifecycle Manager
- ResourceRegistry (no weakref)
- SprintRunContext (contextvars)
- Phase 2 directory layout: core/ (governance, locks, capabilities, optional_imports), runtime/ (sprint_scheduler, sprint_entrypoint), knowledge/ (duckdb_store, graph_service/DuckPGQGraph, lancedb_store), fetching/ (public_fetcher/curl_cffi, fetch_coordinator), transport/ (http3_lane, prewarm_pool, conditional_cache, tor/i2p/nym transports), brain/ (inference_engine, dspy_optimizer, hypothesis_engine, ner_engine, mlx_batched_executor), coordinators/, sidecar/, tests/, rust_extensions/

**Files:**
- runtime/sprint_scheduler.py
- runtime/sprint_entrypoint.py
- knowledge/duckdb_store.py
- core/__main__.py (deprecated)

**Flow:**
CLI → run_sprint() → SprintScheduler.run() → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write

**Timestamp:** 2026-07-27

## Narrative
### Structure
Sprint pipeline: owner dispatches cycles via SprintScheduler. Scheduler manages lifecycle, dedup, and export. DuckDBShadowStore is canonical facts authority for analytics subsystem. Directory layout organized by concern: core/ (governance), runtime/ (orchestration), knowledge/ (storage), fetching/ (data acquisition), transport/ (network), brain/ (inference).

### Dependencies
DuckDB for sprint facts, DuckPGQGraph for IOC storage, LMDB for payload WAL, Arrow for zero-copy ingest, Rust extensions for batch operations, curl_cffi/httpx for fetching, aioquic for HTTP/3 (optional)

### Highlights
Entry: python -m hledac.universal --sprint QUERY [--duration SECS] [--aggressive]; runtime/sprint_entrypoint.py is current entry (deprecated: core/__main__.py). M1 8GB optimizations: 600MB DuckDB limit, 4 threads, Arrow zero-copy, LRU(16) dedup, msgspec.Struct hot-path DTOs. SprintScheduler.run() orchestrates 8 acquisition lanes. DuckDB is canonical write target. Rust extensions via PyO3 bridge (feed_pipeline, ioc_extractor, url_ops, content_hasher, batch_counters).

### Rules
Rule 1: Advisory Log LRU _ADVISORY_LOG_LRU_MAX = 16, FIFO eviction, no promotion on hit
Rule 2: SprintScheduler tier priority High→Low: surface → structured_ti → deep → archive → other
Rule 3: Fail-safe: sidecary return [] on errors

## Facts
- **entry_point**: Entry point: python -m hledac.universal --sprint QUERY [project]
- **core_layers**: Core layers: core/, runtime/, brain/, fetching/, knowledge/, transport/, coordinators/, sidecar/, tests/, rust_extensions/ [project]
- **duckdb_memory_limit**: DuckDB memory limit: 600MB on M1 Air 8GB [project]
- **duckdb_threads**: DuckDB threads: 4 (P+E cores on M1 8GB) [project]
- **duckdb_chunk_config**: Chunk size: 500, concurrency: 2 for DuckDB inserts [project]
- **hot_path_dto_pattern**: msgspec.Struct with frozen=True, gc=False for hot-path DTOs [project]
- **tier_priority**: Tier priority (high→low): surface → structured_ti → deep → archive → other [project]
- **nonfeed_fallback_lanes**: Nonfeed fallback: CT, WAYBACK, PASSIVE_DNS, PIVOT_EXECUTOR, DOH [project]
- **advisory_dedup_pattern**: Advisory dedup uses LRU(16) with FIFO no-promote semantics [project]
- **sprint_invariants**: Sprint invariants: Winddown, Dedup, Lifecycle authority, Export on teardown, TaskGroup concurrency [project]
- **sprint_pipeline_flow**: Sprint pipeline: CLI → run_sprint → SprintScheduler.run → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write [project]
- **rust_extensions_modules**: PyO3 Rust extensions: feed_pipeline, ioc_extractor, url_ops, content_hasher, batch_counters [project]