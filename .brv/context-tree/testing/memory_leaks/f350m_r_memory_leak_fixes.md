---
title: F350M-R Memory Leak Fixes
summary: 'F350M-R test suite memory leak fixes: asyncio scope conflict, MLX Metal cache, HermesModelCache, MLXModelPool cleanup via autouse fixtures'
tags: []
related: [testing/exit_codes/sprint_f350m_r_exit_code_tests.md, memory/resource_governor/uma_memory_management.md]
keywords: []
createdAt: '2026-07-11T20:07:27.077Z'
updatedAt: '2026-07-11T20:07:27.077Z'
---
## Reason
Document F350M-R test suite memory leak fixes and cleanup fixtures

## Raw Concept
**Task:**
Fix F350M-R test suite memory leaks causing M1 8GB crashes after ~100 tests

**Changes:**
- Fixed asyncio fixture scope conflict (CRITICAL)
- Added MLX Metal cache cleanup fixture (CRITICAL)
- Added HermesModelCache singleton cleanup fixture (HIGH)
- Added MLXModelPool singleton cleanup fixture (HIGH)
- Reduced pytest-xdist workers from 4 to 2
- Changed asyncio_default_fixture_loop_scope from module to session

**Files:**
- brain/_hermes_cache.py
- brain/mlx_model_pool.py

**Flow:**
Test -> cleanup fixture -> mx.eval([]) barrier -> clear_cache() -> next test

**Timestamp:** 2026-07-11

**Author:** F350M-R investigation

## Narrative
### Structure
4 autouse fixtures added to tests/conftest.py for automatic cleanup after each test

### Dependencies
Requires MLX availability check (_MLX_AVAILABLE), exception handling for missing methods

### Highlights
Memory reduction from 5-16GB potential to ~1-2GB bounded. M1 8GB crashes resolved.

### Rules
Rule 1: MLX cache cleanup MUST call mx.eval([]) before clear_cache() as synchronization barrier
Rule 2: Singleton cleanup uses try/except to handle missing reset_instance() method
Rule 3: asyncio_default_fixture_loop_scope must match session_event_loop fixture scope

### Examples
Example fixture pattern:
@pytest.fixture(autouse=True)
def _mlx_cache_cleanup() -> None:
    yield
    if _MLX_AVAILABLE:
        try:
            _mlx_core.eval([])
            _mlx_core.metal.clear_cache()
        except Exception:
            pass

## Facts
- **asyncio_fixture_scope**: asyncio_default_fixture_loop_scope was 'module' causing scope conflict with session_event_loop fixture [project]
- **asyncio_fixture_scope_fix**: Fixed asyncio_default_fixture_loop_scope to 'session' and reduced pytest-xdist workers from 4 to 2 [project]
- **mlx_cache_cleanup_pattern**: MLX Metal cache cleanup uses mx.eval([]) as barrier before clear_cache() [convention]
- **pytest_xdist_workers**: pytest-xdist parallel workers reduced from 4 to 2 to reduce memory pressure [convention]
