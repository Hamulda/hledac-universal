- **Root Cause**: F350M-R test suite had memory leaks causing M1 8GB crashes after ~100 tests
- **Critical Fix #1**: Fixed asyncio fixture scope conflict by changing asyncio_default_fixture_loop_scope from 'module' to 'session'
- **Critical Fix #2**: Added MLX Metal cache cleanup fixture with mx.eval([]) synchronization barrier before clear_cache()
- **High Priority Fixes**: Added HermesModelCache and MLXModelPool singleton cleanup fixtures with try/except for missing reset_instance()
- **Performance Impact**: Reduced pytest-xdist workers from 4 to 2; bounded memory from 5-16GB potential to ~1-2GB
- **Pattern**: 4 autouse fixtures in tests/conftest.py for automatic cleanup after each test
- **Files Modified**: brain/_hermes_cache.py, brain/mlx_model_pool.py

**Sections**:
- Header (title, summary, related docs, timestamps)
- Reason (document purpose)
- Raw Concept (task, changes, files, flow diagram, author)
- Narrative (structure, dependencies, highlights, rules, examples)
- Facts (key findings with project/convention tags)

**Notable Entities**:
- MLX, mx.eval([]), mx.metal.clear_cache()
- HermesModelCache, MLXModelPool (singletons)
- pytest-xdist, asyncio_default_fixture_loop_scope
- _MLX_AVAILABLE flag, _mlx_core module

**Key Rules**:
1. MLX cache cleanup MUST call mx.eval([]) before clear_cache()
2. Singleton cleanup uses try/except for missing reset_instance()
3. asyncio fixture scope must match session_event_loop fixture scope