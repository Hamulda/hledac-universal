# Lazy Composition Pattern

## Metadata

- **Entry Path:** patterns/lazy-composition
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** pattern

## Summary

Heavy components initialized on first access rather than at module import.

## Examples

| Component | Lazy Pattern |
|-----------|--------------|
| DuckDB store | cached_property |
| MLX model | hermes_cache singleton |
| Vector store | _ensure_vector_store |
| Graph attachment | _ensure_graph_attachment |

## Anti-Pattern

```python
# WRONG: 7us cold-start penalty
try:
    from otel import instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented

# CORRECT: zero-cost until first use
from hledac.universal.utils.optional_imports import optional
_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))
```

## Benefits

- Faster module import
- Memory savings for unused features
- Conditional availability
