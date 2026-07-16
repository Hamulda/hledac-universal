---
title: Pytest Infrastructure and Fixtures
summary: 33 conftest.py fixtures with session-scoped asyncio loop, memory profiling (RSS+tracemalloc), MLX/Hermes/GraphService cleanup guards, lazy load optimization reducing 27-module eager load
tags: []
related: []
keywords: []
createdAt: '2026-07-16T11:01:33.730Z'
updatedAt: '2026-07-16T11:01:33.730Z'
---
## Reason
Document conftest.py fixtures: session event loop, memory profiling, MLX/Hermes cleanup, lazy load optimization, mock factories

## Raw Concept
**Task:**
Document pytest conftest.py fixture infrastructure and testing conventions

**Files:**
- tests/conftest.py
- pyproject.toml

**Flow:**
pytest collection -> lazy load 27 modules -> session fixture setup -> per-test autouse guards -> memory profiling -> cleanup with 2-pass GC

**Timestamp:** 2026-07-16

## Narrative
### Structure
33 conftest.py fixtures organized: session-scoped (event_loop, duckdb_store, otel_tracer), memory profiling (_session_tracer, memory_snapshot, memory_tracker, assert_memory_leak), async loop guard (_gc_and_close_loops), MLX/Hermes cleanup (_memory_profiler_gc_sync, _hermes_cache_cleanup, _mlx_model_pool_cleanup, _asyncio_task_leak_guard, _graph_service_session_cleanup), centralized cleanup (_cleanup), mock factories (scheduler_mocks, lifecycle_mock, base mocks), lazy load (_LazyForceLoadFinder)

### Dependencies
Requires pytest-asyncio with asyncio_mode=auto and asyncio_default_fixture_loop_scope=session; pytest-timeout 30s; pytest-benchmark 10% threshold; pytest-mock for MagicMock/AsyncMock

### Highlights
Session-scoped event loop critical for M1 8GB to avoid loop recreation overhead; lazy load eliminates 27-module eager load at collection time; centralized _cleanup replaces 40+ scattered gc.collect() calls; MLX/Hermes/GraphService singletons cleaned after each test; base mock factories spec-limited save ~500KB session RAM

### Rules
asyncio_default_fixture_loop_scope MUST be session for M1 8GB; _LazyForceLoadFinder MUST be prepended to sys.meta_path before collection; _cleanup uses 2-pass GC for mlx/duckdb/lmdb/heavy markers, 1-pass otherwise; assert_memory_leak is standalone helper, not fixture

## Facts
- **test_count**: 1238 test files with 33 conftest.py fixtures [project]
- **event_loop_scope**: asyncio_default_fixture_loop_scope = session critical for M1 8GB [convention]
- **timeout_default**: pytest-timeout default is 30 seconds [convention]
- **benchmark_threshold**: pytest-benchmark regression threshold is 10% for hot-path benchmarks [convention]
- **mock_ram_savings**: MagicMock/AsyncMock lifecycle management saves ~30-50MB RAM [project]
- **lazy_load_modules**: Lazy load tracks 27 hledac.universal subpackages for on-demand import [project]
- **gc_cleanup_consolidation**: Centralized _cleanup replaces 40+ scattered gc.collect() calls across 7+ test files [project]
- **mock_factory_ram_savings**: Base mock factories save ~500KB session RAM via spec-limited mocks [project]
- **mlx_cleanup_guards**: MLX/Hermes cleanup guards: _memory_profiler_gc_sync, _hermes_cache_cleanup, _mlx_model_pool_cleanup [project]
- **lazy_load_mechanism**: _LazyForceLoadFinder meta_path finder enables on-demand module loading [project]
