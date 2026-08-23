# Context Compressor

## Metadata

- **Entry Path:** modules/context-compressor
- **Status:** current
- **Source:** context_optimization/context_compressor.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

LLM context compression using llmlingua for prompt size reduction.

## Source Paths

- `context_optimization/context_compressor.py`

## Use When

- Context window overflow prevention
- Token budget optimization
- Long document processing

## Optional Dependency

```bash
uv sync --extra llmlingua
```

## Key Classes

| Class | Purpose |
|-------|---------|
| `CompressedContext` | Compression result dataclass |

## M1 Consideration

Compression is CPU-bound — runs in ThreadPoolExecutor to avoid blocking event loop.

## Related Entries

- modules/memory-coordinator
- features/rag-search
