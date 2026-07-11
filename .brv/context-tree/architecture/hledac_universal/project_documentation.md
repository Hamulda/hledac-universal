---
title: Project Documentation
summary: Hledac Universal OSINT orchestrator on M1 8GB with MLX/Hermes3, sprint cycles, storage trinity (DuckDB/LMDB/LanceDB)
tags: []
related: [architecture/hledac_universal/context.md, data/duckdb_store/context.md, facts/project/technology_stack.md, testing/exit_codes/context.md]
keywords: []
createdAt: '2026-07-11T15:06:23.586Z'
updatedAt: '2026-07-11T15:06:23.586Z'
---
## Reason
Comprehensive project documentation from CLAUDE.md

## Raw Concept
**Task:**
Document Hledac Universal OSINT orchestrator architecture and conventions

**Changes:**
- Comprehensive CLAUDE.md documentation curated
- Added critical invariants
- Added feature flags table
- Added wired components list
- Added pre-flight guards (F221-ABORT)

**Files:**
- .claude/CLAUDE.md
- runtime/sprint_scheduler.py
- knowledge/duckdb_store.py
- knowledge/graph_service.py
- fetching/public_fetcher.py
- brain/inference_engine.py
- transport/http3_lane.py
- core/__main__.py

**Flow:**
CLI -> run_sprint() -> SprintScheduler.run() -> run_prelude/acquisition/advisory/winddown -> DuckDBShadowStore.async_ingest_findings_batch()

**Timestamp:** 2026-07-11

**Author:** Hledac Team

## Narrative
### Structure
Key modules: runtime/sprint_scheduler.py (sprint lifecycle), knowledge/duckdb_store.py (DuckDB shadow store), knowledge/graph_service.py (DuckPGQGraph), fetching/public_fetcher.py (curl_cffi HTTP), brain/ (MLX inference, DSPy, hypothesis), transport/ (Tor/I2P/stealth), coordinators/ (FetchCoordinator, SidecarOrchestrator)

### Dependencies
MLX (Metal backend, lazy evaluation), DuckDB, LMDB, LanceDB, curl_cffi, aioquic (optional http3 extra)

### Highlights
Entry: python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]. Storage trinity: DuckDB (SQL/canonical), LMDB (key-value/entity), LanceDB (ANN/RAG). Brain layer: Hermes3 MLX inference, DSPy optimizer, hypothesis engine for dark surface queries.

### Rules
Rule 1: snake_case Python naming
Rule 2: No bare except: — use except Exception:
Rule 3: asyncio.gather always with return_exceptions=True
Rule 4: mx.eval([]) before mx.metal.clear_cache()
Rule 5: No time.sleep() in async — use asyncio.sleep()
Rule 6: No asyncio.run() in ThreadPoolExecutor — use loop.run_until_complete()
Rule 7: DuckDB writes via async_ingest_findings_batch() only
Rule 8: LMDB bulk writes via cursor.putmulti()
Rule 9: Use RotatingBloomFilter for URL dedup
Rule 10: Sidecars return [] on errors, never throw exceptions

### Examples
Example sprint: python -m hledac.universal --sprint "threat actor APT29" --duration 300

## Facts
- **hardware**: Project runs on MacBook Air M1 with 8GB unified memory [project]
- **llm_model**: MLX framework with Hermes-3-Llama-3.2-3B-4bit model [project]
- **workflow**: Sprint cycles process search queries and return structured IoC data [project]
- **duckdb_mode**: DuckDB in-process mode saves ~200MB RAM [preference]
- **duckdb_threads**: DuckDB thread count optimal at 2 for thread-local connection bottleneck [convention]
- **metal_cache_limit**: Metal cache hard limit 1.5 GiB (1_610_612_736 bytes) [project]
- **kv_cache_config**: KV cache uses kv_bits=4, max_kv_size=8192 in mlx_lm.generate() [convention]
- **min_active_window**: Pre-flight guard aborts if active-window budget below 30s [convention]
- **min_sprint_duration**: Minimum sprint duration is windup_lead_effective + 30s [convention]
