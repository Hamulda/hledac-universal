---
children_hash: 18d646182f9f3bf98c725d88329c4b53ae55b9a821129929f2a74d609c0afc6b
compression_ratio: 0.36103467253714916
condensation_order: 2
covers: [conftest/_index.md, exit_codes/_index.md, memory_leaks/_index.md]
covers_token_total: 1817
summary_level: d2
token_count: 656
type: summary
---
<think>
The user wants me to create a more concise structural summary. Looking at the input, it's a fairly verbose explanation of pytest infrastructure and fixtures. I need to compress this significantly while preserving the essential architecture and key facts.

Key things to preserve:
- 33 conftest.py fixtures
- Session-scoped event loop (critical for M1 8GB)
- Memory profiling with RSS+tracemalloc
- MLX/Hermes/GraphService cleanup guards
- Lazy load optimization for 27 modules
- Centralized cleanup replaces 40+ scattered gc.collect()
- Key conventions: asyncio_mode=auto, 30s timeout, 10% benchmark threshold
- Mock factories save ~500KB RAM
- 1238 test files

I need to be much more concise - this is a SUMMARY, not a full documentation.
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