---
children_hash: dd263e84c22eb1eb7668a4bf4ede9e5fd42dad7215382ffab74417c5fe8034f7
compression_ratio: 0.1290478492025133
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 6207
summary_level: d3
token_count: 801
type: summary
---
<think>
The user wants me to create a structural summary at level d2 (topic level) for the conftest domain. I need to consolidate the child entries while preserving key facts, architectural decisions, and relationships. The summary should reference entry names for drill-down and target ~20-30% compression.

Let me analyze what I have:

1. The domain is `testing/conftest/` with an abstract.md, overview.md, and context.md
2. Key topics covered:
   - pytest_infrastructure_and_fixtures (the main entry)
   - Plus references to memory_leaks and exit_codes

From the provided context, I can see this covers:
- 33 conftest.py fixtures
- Session-scoped event loop (critical for M1 8GB)
- Memory profiling with RSS+tracemalloc
- MLX/Hermes/GraphService cleanup guards
- Lazy load optimization for 27 modules
- Centralized cleanup replaces 40+ scattered gc.collect()
- Key conventions: asyncio_mode=auto, 30s timeout, 10% benchmark threshold
- Mock factories save ~500KB RAM
- 1238 test files

I need to create a concise but complete summary that:
- Preserves key facts and architectural decisions
- References entry names for drill-down
- Uses clean markdown format
- Achieves ~20-30% compression
</think>

## Pytest Infrastructure and Fixtures

### Overview
33 conftest.py fixtures provide session-scoped event loop, memory profiling, MLX/Hermes/GraphService cleanup guards, lazy load optimization, and centralized cleanup for the test suite. Session-scoped event loop is critical for M1 8GB memory stability.

### Core Architecture

**Session Fixtures:** `event_loop` (session-scoped), `duckdb_store`, `otel_tracer`

**Memory Profiling:** `_session_tracer`, `memory_snapshot`, `memory_tracker`, `assert_memory_leak` — RSS + tracemalloc dual tracking

**Cleanup Guards (autouse, 8 total):**
- `_memory_profiler_gc_sync` — sync before memory snapshots
- `_hermes_cache_cleanup` — Hermes singleton cleanup
- `_mlx_model_pool_cleanup` — MLX model pool cleanup
- `_asyncio_task_leak_guard` — validates no leaked tasks
- `_graph_service_session_cleanup` — GraphService session cleanup
- `_gc_and_close_loops` — closes event loops
- `_cleanup` — centralized 2-pass GC (mlx/duckdb/lmdb/heavy markers), replaces 40+ scattered `gc.collect()` calls

**Mock Factories:** `scheduler_mocks`, `lifecycle_mock`, base mock factories — spec-limited saves ~500KB session RAM

**Lazy Load:** `_LazyForceLoadFinder` prepended to `sys.meta_path` — tracks 27 hledac.universal subpackages for on-demand import

### Conventions
| Setting | Value |
|---------|-------|
| asyncio_mode | auto |
| asyncio_default_fixture_loop_scope | session |
| pytest-timeout | 30s |
| benchmark threshold | 10% regression |
| Mock library | pytest-mock |

### Key Facts
- 1238 test files, 33 fixtures
- Session-scoped loop eliminates loop recreation overhead
- Lazy load eliminates 27-module eager load at collection time
- Mock factories spec-limited save ~30-50MB RAM
- Centralized cleanup replaces 40+ scattered gc.collect() calls

### Related Entries
- `testing/memory_leaks/f350m_r_memory_leak_fixes` — memory leak fixes informed by this infrastructure
- `testing/exit_codes/exit_code_testing` — exit code testing with 30s timeout convention