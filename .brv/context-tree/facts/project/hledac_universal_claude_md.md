---
title: Hledac Universal CLAUDE.md
summary: 'Hledac Universal project docs: conventions, invariants, architecture, feature flags, wired components, pre-flight guards, exit codes, anti-patterns, hardware constraints'
tags: []
related: [facts/project/parallel_async_helper.md, facts/project/issue_g2_pep_734_isolation_infrastructure.md, facts/project/coding_conventions_status.md]
keywords: []
createdAt: '2026-07-11T15:07:16.916Z'
updatedAt: '2026-07-11T15:07:16.916Z'
---
## Reason
Curating comprehensive project documentation from CLAUDE.md

## Raw Concept
**Task:**
Document Hledac Universal project from CLAUDE.md

**Files:**
- .claude/CLAUDE.md

**Flow:**
SprintScheduler.run() -> run_prelude -> run_acquisition_lanes -> run_advisory_runner -> _accumulate_findings_to_graph -> run_winddown -> DuckDB async_ingest_findings_batch

**Timestamp:** 2026-07-11

## Narrative
### Structure
Storage Trinity: DuckDB (SQL), LMDB (key-value), LanceDB (ANN embeddings). Brain Layer: MLX inference, DSPy optimizer, hypothesis engine.

### Dependencies
Requires uv sync --extra mlx-embed for MLX embeddings, --extra http3 for aioquic QUIC support

### Highlights
10 Critical invariants for M1 stability. 50+ feature flags. Pre-flight guard F221-ABORT with --force override. HTTP/3 dual strategy: curl_cffi_opportunistic (default) + aioquic real-QUIC lane.

### Rules
Rule 1: No bare except - always except Exception:
Rule 2: asyncio.gather always with return_exceptions=True
Rule 3: mx.eval([]) before mx.metal.clear_cache()
Rule 4: DuckDB writes ONLY through async_ingest_findings_batch()
Rule 5: LMDB bulk via cursor.putmulti() - never per-item
Rule 6: Fail-safe everywhere - sidecary return [] on errors
Rule 7: No time.sleep() in async - use asyncio.sleep()
Rule 8: No asyncio.run() in ThreadPoolExecutor - use loop.run_until_complete()
Rule 9: Use RotatingBloomFilter not ScalableBloomFilter
Rule 10: sys.exit() propagates verbatim - envelope has except SystemExit: raise

### Examples
Entry: python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]
Pre-flight: --duration 60 passes MIN_ACTIVE_WINDOW_S=30s guard
Tests: pytest tests/ -x --timeout=30 -q && smoke_runner.py --smoke

## Facts
- **project_name**: Project is Hledac Universal OSINT orchestrator [project]
- **hardware**: Runs on MacBook Air M1 8GB UMA [project]
- **llm_model**: Uses MLX framework with Hermes-3-Llama-3.2-3B-4bit model [project]
- **sprint_model**: Sprint cycles: each sprint processes a search query and returns IoC data [convention]
- **async_pattern**: Uses asyncio.gather with return_exceptions=True [convention]
- **duckdb_write**: DuckDB via async_ingest_findings_batch() - only canonical write path [convention]
- **lmdb_write**: LMDB bulk via cursor.putmulti() [convention]
- **bloom_filter**: Uses RotatingBloomFilter for URL dedup [convention]
- **mlx_cache**: mx.eval([]) before mx.metal.clear_cache() [convention]
- **no_sleep**: No time.sleep() in async code - use asyncio.sleep() [convention]
- **no_asyncio_run**: No asyncio.run() in ThreadPoolExecutor - use loop.run_until_complete() [convention]
- **exception_handling**: No bare except: - always except Exception: [convention]
- **exit_codes**: Exit codes: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT [convention]
- **test_suite**: Test baseline: test_sprint_scheduler.py ~89, test_hledac_rust_extensions.py ~64, F206 probe 200+ [project]
