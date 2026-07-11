---
children_hash: 705f1253d34de935c62324ec3aed7acb0794807c78f40633ae0a8a6a47f0d314
compression_ratio: 0.5487804878048781
condensation_order: 1
covers: [context.md, f350m_r_memory_leak_fixes.md]
covers_token_total: 738
summary_level: d1
token_count: 405
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries about memory leaks in the F350M-R test suite. I need to preserve key facts, architectural decisions, and relationships while compressing to ~20-30% of input tokens.

Key information to preserve:
- Memory leak fixes for F350M-R test suite
- Affects M1 8GB systems
- 4 autouse fixtures in tests/conftest.py
- Specific fixes: asyncio scope conflict, MLX Metal cache, HermesModelCache, MLXModelPool
- Files: brain/_hermes_cache.py, brain/mlx_model_pool.py
- mx.eval([]) synchronization barrier pattern
- pytest-xdist workers reduced from 4 to 2
- Memory reduction from 5-16GB to ~1-2GB bounded
- Related topics: testing/exit_codes, memory/resource_governor

Let me create a concise summary.
</think>

# Domain: memory_leaks

## Overview
Memory leak fixes for F350M-R test suite targeting M1 8GB systems, reducing memory from 5-16GB potential to ~1-2GB bounded.

## Key Findings

**Root Causes:**
- asyncio fixture scope conflict (session_event_loop vs module-scoped default)
- MLX Metal cache accumulation across tests
- Singleton leaks in HermesModelCache and MLXModelPool

**Fixes Applied:**
- Changed asyncio_default_fixture_loop_scope from 'module' to 'session'
- Reduced pytest-xdist workers from 4 to 2
- Added 4 autouse cleanup fixtures to tests/conftest.py

**Critical Pattern:**
Fixture must call `mx.eval([])` as synchronization barrier before `clear_cache()`.

## Related Topics
- `testing/exit_codes` — related F350M-R test files
- `memory/resource_governor` — UMA memory management context