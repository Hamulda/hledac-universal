---
children_hash: 73583ebc4f126b0b973523b172853543ca32da8ddbaa5909a2b993a6b527c885
compression_ratio: 0.14391377852916315
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 4732
summary_level: d3
token_count: 681
type: summary
---
<think>
The user is asking me to create a structural summary for the `testing` domain at the d2 level. Looking at the context, I need to condense the child entries (conftest, exit_codes, memory_leaks) into a cohesive domain summary.

Let me analyze the child entries and create a concise summary:

1. **conftest/pytest_infrastructure_and_fixtures**: 33 fixtures, session-scoped event loop, memory profiling, MLX/Hermes/GraphService cleanup, lazy load optimization, centralized cleanup
2. **exit_codes/exit_code_testing**: Exit code testing with smoke_runner.py, 4 exit codes (0/1/2/3), CI integration
3. **memory_leaks/f350m_r_memory_leak_fixes**: Memory leak fixes from sprint F350m_r

I need to:
- Create a domain-level overview
- Cover the testing infrastructure
- Mention the 3 subdomains
- Preserve key facts and patterns
- Keep it concise (~20-30% of original)

Let me write this as a proper _index.md for the testing domain.
</think>

# testing — Test Infrastructure & Quality Assurance

## Overview

The `testing` domain covers pytest infrastructure, exit code testing, and memory leak detection. Core principle: centralized cleanup via conftest fixtures replaces scattered `gc.collect()` calls.

## Subdomains

### conftest — Pytest Infrastructure (33 Fixtures)

**Session Fixtures:** `event_loop` (session-scoped, critical for M1 8GB), `duckdb_store`, `otel_tracer`

**Memory Profiling:** `_session_tracer`, `memory_snapshot`, `memory_tracker`, `assert_memory_leak` — tracks RSS and tracemalloc

**Cleanup Guards (autouse):** `_mlx_model_pool_cleanup`, `_hermes_cache_cleanup`, `_graph_service_session_cleanup`, `_asyncio_task_leak_guard`, `_gc_and_close_loops`, `_cleanup`

**Lazy Load:** `_LazyForceLoadFinder` prepended to `sys.meta_path` — on-demand import of 27 hledac.universal subpackages

**Mock Factories:** Spec-limited mock factories save ~30-50MB session RAM

### exit_codes — Exit Code Testing

4 exit codes: 0=success, 1=runtime, 2=config, 3=programmer. CI integration via `smoke_runner.py` with 30s timeout.

### memory_leaks — Memory Leak Detection

RSS delta threshold: 1 MiB (session), 512 KiB (module). Tracemalloc delta: 100 KiB. 4 pass pattern for thorough collection.

## Key Patterns

- Session-scoped event loop eliminates loop recreation overhead on M1 8GB
- Lazy load eliminates 27-module eager load at collection time
- Memory profiling in CI catches leaks before merge
- Centralized cleanup replaces 40+ scattered `gc.collect()` calls
- `asyncio_mode=auto`, `asyncio_default_fixture_loop_scope=session`

## Related

- `memory/resource_governor/m1_8gb_ram_priority_optimizations` — P0 memory optimizations
- `facts/project/hledac_universal_claude_md` — testing conventions from CLAUDE.md