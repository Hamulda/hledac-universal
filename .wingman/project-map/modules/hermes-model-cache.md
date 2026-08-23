# Hermes Model Cache

## Metadata

- **Entry Path:** modules/hermes-model-cache
- **Status:** current
- **Source:** brain/_hermes_cache.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Unified model cache facade for MLX models with storage + monitoring split-brain architecture.

## Source Paths

- `brain/_hermes_cache.py`
- `brain/deephermes3_engine.py`

## Architecture

```
HermesModelCache (Facade)
├── HermesModelLoader (Storage)
│   ├── Model storage (Hermes-3-Llama-3.2-3B-4bit)
│   ├── LoRA adapter storage
│   └── Eviction callbacks
└── HermesModelMonitor (Pressure Response)
    ├── Soft warning handler
    ├── Critical warning handler
    └── Memory pressure response
```

## Key Classes

| Class | Responsibility |
|-------|----------------|
| `HermesModelCache` | Facade orchestrating loader + monitor |
| `HermesModelLoader` | Storage with LRU eviction |
| `HermesModelMonitor` | psutil-based memory pressure listener |
| `PromptCacheStats` | Tokenized prompt cache metrics |

## Singleton Pattern

```python
from brain._hermes_cache import hermes_cache

cache = hermes_cache()  # Global singleton
model, tokenizer = cache.get_model("hermes-3b")
```

## Related Entries

- modules/mlx-inference
- modules/performance-coordinator
- domains/m1-memory-management
