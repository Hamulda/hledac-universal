# Topic: memory_leaks

## Overview
Memory leak fixes for F350M-R test suite affecting M1 8GB systems

## Key Concepts
- asyncio fixture scope conflicts
- MLX Metal cache accumulation
- Singleton memory leaks (HermesModelCache, MLXModelPool)
- pytest-xdist worker reduction
- autouse cleanup fixtures

## Related Topics
- testing/exit_codes - related F350M-R test files
- memory/resource_governor - UMA memory management context
