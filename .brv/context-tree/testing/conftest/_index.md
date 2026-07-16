---
children_hash: c76a12d4358c9d991eb6d601bba9fdf2ca3f3d53e12d75751f14819f8db15766
compression_ratio: 0.8333333333333334
condensation_order: 1
covers: [pytest_infrastructure_and_fixtures.md]
covers_token_total: 804
summary_level: d1
token_count: 670
type: summary
---
<think>
The user wants me to create a more concise structural summary of the pytest infrastructure and fixtures knowledge entry. They've indicated a previous attempt was not short enough, so I need to aggressively compress while keeping the most critical facts.

Let me identify what's truly essential:
- 33 conftest.py fixtures
- Session-scoped asyncio loop (critical for M1 8GB)
- Memory profiling with RSS+tracemalloc
- MLX/Hermes/GraphService cleanup guards
- Lazy load optimization for 27 modules
- Centralized cleanup replaces 40+ scattered gc.collect() calls
- Key conventions: asyncio_mode=auto, 30s timeout, 10% benchmark threshold
- Mock factories save ~500KB RAM

I need to keep the key facts, relationships, and architectural decisions but drop the verbose examples and secondary details.
</think>

## Pytest Infrastructure and Fixtures

### Overview
33 conftest.py fixtures provide session-scoped event loop, memory profiling, MLX/Hermes/GraphService cleanup guards, lazy load optimization, and centralized cleanup for the test suite.

### Core Architecture
**Session Fixtures:** `event_loop` (session-scoped, critical for M1 8GB), `duckdb_store`, `otel_tracer`

**Memory Profiling:** `_session_tracer`, `memory_snapshot`, `memory_tracker`, `assert_memory_leak` — tracks RSS and tracemalloc

**Cleanup Guards (autouse):**
- `_memory_profiler_gc_sync` — forces sync before memory snapshots
- `_hermes_cache_cleanup` — Hermes singleton cleanup
- `_mlx_model_pool_cleanup` — MLX model pool cleanup
- `_asyncio_task_leak_guard` — validates no leaked tasks
- `_graph_service_session_cleanup` — GraphService session cleanup
- `_gc_and_close_loops` — closes event loops
- `_cleanup` — centralized 2-pass GC (mlx/duckdb/lmdb/heavy markers), replaces 40+ scattered `gc.collect()` calls

**Mock Factories:** `scheduler_mocks`, `lifecycle_mock`, base mock factories — spec-limited saves ~500KB session RAM

**Lazy Load:** `_LazyForceLoadFinder` prepended to `sys.meta_path` — tracks 27 hledac.universal subpackages for on-demand import

### Conventions
- `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope=session`
- pytest-timeout: 30s default
- pytest-benchmark: 10% regression threshold for hot-path
- pytest-mock for MagicMock/AsyncMock

### Key Facts
- 1238 test files, 33 fixtures
- Session-scoped loop eliminates loop recreation overhead on M1 8GB
- Lazy load eliminates 27-module eager load at collection time
- Mock factories spec-limited save ~30-50MB RAM

### Related
- `testing/memory_leaks/f350m_r_memory_leak_fixes` — memory leak fixes informed by this infrastructure
- `testing/exit_codes/exit_code_testing` — exit code testing with 30s timeout convention